// reach.cpp: Halo: Reach (haloreach.dll, MCC 1.3528) player bots, the Halo 3 plan (h3.cpp) with
// Reach's offsets (docs/REACH-01). Differences from H3: array header element size +0x20, max
// +0x2C, data pointer +0x50; TLS block +0x10 objects, +0x18 players, +0x48 game globals (+0x2D8:
// 16 player configurations of 0xA0 bytes); players update FUN_180047050 (actions +0x10 +
// index * 0xA8); player_new_at_index FUN_180043dc8; config +8 controller (byte), +0xC user,
// +0x10 machine identifier, +0x16 player identifier, +0x20 configuration (name first), +0x98 team
// (byte); player (0x490) +0x28 unit, +0xB0 name.
// Original H3 notes follow.
// - Engine globals live in thread-local storage: game thread's TLS block for halo3.dll
//   (ThreadLocalStoragePointer[_tls_index]) +0x10 players array header, +0x38 object header
//   array, +0x48 game globals (+0x200: 16 player configurations of 0xB8 bytes, the match's
//   players). Array header: +0x20 max, +0x24 element size, +0x48 data pointer.
// - players update FUN_1800e444c(actions): actions +8 = bitmask of players with an action,
//   +0x10 + index * 0x88 = each player's action (FUN_1800e8dd8 turns it into unit control).
//   We hook it (game thread, every tick): run the brain's commands, copy player 0's action for
//   the debug view, write each driven bot's action, call the original.
// - player_new_at_index FUN_1800e20dc(index, config, 0): config +0 valid, +1 no machine, +2
//   controller, +4 user, +8 machine (6 bytes), +0x10 player identifier, +0x18 configuration
//   (0xA0 bytes, copied to player +0x58 and +0xF8: name at +0x18 and +0x90, team int at +0xB0).
#include <windows.h>
#include <math.h>
#include <stdio.h>
#include <string.h>
#include <wchar.h>
#include <intrin.h>
#include "shared.h"
#include "film.h"
#include "third_party/minhook/include/MinHook.h"

extern hb_shared* g_shm;
void logf(const char* fmt, ...);
void report_note_bot(int index, const wchar_t* name, const wchar_t* tag, int team, const wchar_t* clan,
                     const uint16_t* emblem);   // report.cpp
void bot_service_tag(const wchar_t* name, wchar_t tag[4]);                                                // report.cpp

static uint8_t* g_hr;
static uint8_t g_hr_patch[5];
static DWORD g_hr_tls_index;
static const uintptr_t HR_PLAYERS_UPDATE = 0x47050, HR_PLAYER_NEW_AT = 0x43DC8;
static const size_t HR_PLAYER_SIZE = 0x490, HR_ACTION = 0xA8, HR_CONFIG = 0xA0;
typedef void(__fastcall* hr_update_fn)(uint8_t* actions);
typedef int(__fastcall* hr_new_at_fn)(int index, uint8_t* config, char flag);
static hr_update_fn o_hr_update;

static uint8_t* hr_tls() {                    // game thread only
    void** arr = (void**)__readgsqword(0x58);
    return arr ? (uint8_t*)arr[g_hr_tls_index] : nullptr;
}

static uint8_t* hr_player(uint8_t* tls, int index) {
    uint8_t* hdr = *(uint8_t**)(tls + 0x18);
    if (!hdr || index < 0 || index >= *(int32_t*)(hdr + 0x2C)) return nullptr;
    uint8_t* data = *(uint8_t**)(hdr + 0x50);
    return data ? data + (size_t)index * HR_PLAYER_SIZE : nullptr;
}
static bool hr_live(uint8_t* tls, int index) { uint8_t* p = hr_player(tls, index); return p && *(uint16_t*)p != 0; }

// objects: [tls+0x10] header, data +0x50: 0x18-byte entries (+0 salt, +4 type, +0x10 object
// address). Object +0x14 parent, +0x44 position; unit +0x34A weapon in hand (index), +0x34C weapon set
// and wanted primary / secondary index (the action's +0x44..+0x47), +0x350 4 weapon handles.
static uint8_t* hr_object(uint8_t* tls, uint32_t handle) {
    uint8_t* hdr = *(uint8_t**)(tls + 0x10);
    if (!hdr || handle == 0xFFFFFFFFu || (int)(handle & 0xFFFF) >= *(int32_t*)(hdr + 0x2C)) return nullptr;
    uint8_t* e = *(uint8_t**)(hdr + 0x50) + (size_t)(handle & 0xFFFF) * 0x18;
    return *(uint16_t*)e == handle >> 16 ? *(uint8_t**)(e + 0x10) : nullptr;
}

static int hr_free_slot() {
    for (int i = 0; i < HB_MAX_PLAYERS; i++) if (g_shm->bot_player_index[i] < 0) return i;
    return -1;
}

// a weapon lying at a bot's feet: object entry +4 type (2 = weapon, FUN_18005030c), object +0x1B4 = -1 (the weapon
// pickup's own test, FUN_180050620: ghidra/hr_interactions.c), +0x44 position. The nearest within 0.6 units across
// (the bot stands on its spot; the game's own pickup reach was not measured, so not more than that). Not +0x14 = -1
// as before (s71 and older): on a CTF flag it names the last player killed for 5 s after every death (Boardwalk
// CTF), so no bot could take a flag for 5 s after any death a vehicle by a bot (object entry +4 type 1): the nearest
// within 3 units across and 2 up or down, top level (parent NONE: a Warthog, a Ghost, a standalone turret), or with
// turret, a vehicle riding on one (the Warthog's chaingun, the Falcon's side guns: their own vehicles, at the
// parent's place). For the enter-seat interaction (the player: bots never got into vehicles; a bot's interaction
// slots are empty, so its Use press entered nothing)
static uint32_t hr_near_vehicle(uint8_t* tls, uint8_t* unit, bool turret) {
    uint8_t* hdr = *(uint8_t**)(tls + 0x10);
    uint8_t* ents = hdr ? *(uint8_t**)(hdr + 0x50) : nullptr;
    if (!ents || !unit) return 0xFFFFFFFFu;
    const float* me = (const float*)(unit + 0x44);
    uint32_t best = 0xFFFFFFFFu;
    float bd = 3.0f * 3.0f;
    for (int i = 0, mx = *(int32_t*)(hdr + 0x2C); i < mx; i++) {
        uint8_t* e = ents + (size_t)i * 0x18;
        uint8_t* o = *(uint8_t**)(e + 0x10);
        if (!*(uint16_t*)e || e[4] != 1 || !o) continue;
        uint32_t parent = *(uint32_t*)(o + 0x14);
        if ((parent != 0xFFFFFFFFu) != turret) continue;
        if (o[0x138] & 4) continue;      // destroyed (damage flags bit 2: object_get_health reads 0, ghidra/hr_object_get_health.c)
        const float* p = (const float*)(o + 0x44);
        float px = p[0], py = p[1], pz = p[2];
        if (turret) {
            uint8_t* po = hr_object(tls, parent);
            if (!po || (po[0x138] & 4)) continue;   // (the gun on a wreck)
            const float* b = (const float*)(po + 0x44);
            if (fabsf(px) < 3.0f && fabsf(py) < 3.0f) { px += b[0]; py += b[1]; pz += b[2]; }   // (at its marker)
        }
        float dx = px - me[0], dy = py - me[1], dz = pz - me[2], d2 = dx * dx + dy * dy;
        if (d2 < bd && dz > -2.0f && dz < 2.0f) { bd = d2; best = (uint32_t)*(uint16_t*)e << 16 | (uint32_t)i; }
    }
    return best;
}

// the weapon lying in reach that a bot's Use takes (shared.h hb_pick_loose: the one its view is on, else the nearest
// within 0.6 u), for the view it sends this tick
static uint32_t hr_loose_weapon(uint8_t* tls, uint8_t* unit, float yaw, float pitch) {
    uint8_t* hdr = *(uint8_t**)(tls + 0x10);
    uint8_t* ents = hdr ? *(uint8_t**)(hdr + 0x50) : nullptr;
    if (!ents || !unit) return 0xFFFFFFFFu;
    const float* me = (const float*)(unit + 0x44);
    float pos[32][3];
    uint32_t handle[32];
    int n = 0;
    for (int i = 0, mx = *(int32_t*)(hdr + 0x2C); i < mx; i++) {
        uint8_t* e = ents + (size_t)i * 0x18;
        uint8_t* o = *(uint8_t**)(e + 0x10);
        if (!*(uint16_t*)e || e[4] != 2 || !o || *(int32_t*)(o + 0x1B4) != -1) continue;
        const float* p = (const float*)(o + 0x44);
        float px = p[0], py = p[1], pz = p[2];
        uint32_t parent = *(uint32_t*)(o + 0x14);
        if (parent != 0xFFFFFFFFu && ((int)(parent & 0xFFFF) >= mx ||
                                      !hb_loose_parent_ok(ents[(size_t)(parent & 0xFFFF) * 0x18 + 4]))) continue;
        uint8_t* po = parent != 0xFFFFFFFFu && fabsf(px) < 2.0f && fabsf(py) < 2.0f ? hr_object(tls, parent) : nullptr;
        if (po) {   // riding a marker (Oddball's ball on its spawn reads 0, 0, 0.2 under it): the marker's place plus that
            const float* b = (const float*)(po + 0x44);
            px += b[0]; py += b[1]; pz += b[2];
        }
        float dx = px - me[0], dy = py - me[1], dz = pz - me[2];
        if (n < 32 && dx * dx + dy * dy <= 1.0f && dz > -1.0f && dz < 1.0f) {
            pos[n][0] = px; pos[n][1] = py; pos[n][2] = pz;
            handle[n++] = (uint32_t)*(uint16_t*)e << 16 | (uint32_t)i;
        }
    }
    int k = hb_pick_loose(pos, n, me, yaw, pitch);
    return k >= 0 ? handle[k] : 0xFFFFFFFFu;
}

// the game's own Armory (ghidra/hr_armory_catalog.c, hr_appearance_set.c, hr_armor_regions.c; prologues checked live):
// per slot an item count (FUN_180242ebc), the n-th item's id (FUN_180242f28 -> slot, item), the item's record
// (FUN_180242f9c: +0x1C its variant count) and "put this item on" (FUN_180243738: writes the item's region
// permutations into an appearance block, as the Armory screen does). A bot's armor = a random catalog pick per slot:
// legal by construction, no guessed byte values. The appearance block is the configuration's client part (+0x20)
static const uintptr_t HR_ARMORY_COUNT = 0x242EBC, HR_ARMORY_ITEM_ID = 0x242F28, HR_ARMORY_ITEM = 0x242F9C,
                       HR_ARMORY_APPLY = 0x243738;
typedef int(__fastcall* hr_armory_count_fn)(char slot);
typedef char*(__fastcall* hr_armory_item_id_fn)(char* out, char slot, char index);
typedef uint8_t*(__fastcall* hr_armory_item_fn)(uint32_t id);
typedef void(__fastcall* hr_armory_apply_fn)(uint32_t id, uint8_t* appearance);

// the Armory catalog as the game has it, once a session in the log: per slot each item's raw index, variant count
// and first word (likely its name's string id). For telling the original items from MCC's additions (Original
// Experience): the variant counts are matched against MCC's own item list (its exe names each item: MarkVB Base, UA,
// UAHUL = one item with 3 variants). Not matched yet: needs this log from a live session
static void hr_log_catalog() {
    static bool done = false;
    if (done) return;
    done = true;
    for (int slot = 0; slot < 13; slot++) {
        char line[2048];
        int n = ((hr_armory_count_fn)(g_hr + HR_ARMORY_COUNT))((char)slot), at = 0;
        at += sprintf_s(line + at, sizeof line - at, "reach armory slot %d: %d items:", slot, n);
        for (int i = 0; i < n && i < 64 && at < (int)sizeof line - 32; i++) {
            char id2[4] = {-1, -1, 0, 0};
            ((hr_armory_item_id_fn)(g_hr + HR_ARMORY_ITEM_ID))(id2, (char)slot, (char)i);
            uint8_t* item = ((hr_armory_item_fn)(g_hr + HR_ARMORY_ITEM))((uint8_t)id2[0] | (uint32_t)(uint8_t)id2[1] << 8);
            at += sprintf_s(line + at, sizeof line - at, " %d:%d:%08x", (uint8_t)id2[1], item ? *(int32_t*)(item + 0x1C) : -1,
                            item ? *(uint32_t*)item : 0);
        }
        logf("%s", line);
    }
    // what each item's variants write into an appearance block, by the game's own equip on a scratch block (bytes that
    // differ from the fill): the profile view's 3D Reach bots wear the pieces those values name (tools/armory_tables.py)
    for (int slot = 0; slot < 13; slot++) {
        int n = ((hr_armory_count_fn)(g_hr + HR_ARMORY_COUNT))((char)slot);
        for (int i = 0; i < n && i < 64; i++) {
            char id2[4] = {-1, -1, 0, 0};
            ((hr_armory_item_id_fn)(g_hr + HR_ARMORY_ITEM_ID))(id2, (char)slot, (char)i);
            if (id2[0] == -1 || id2[1] == -1) continue;
            uint32_t id = (uint8_t)id2[0] | (uint32_t)(uint8_t)id2[1] << 8;
            uint8_t* item = ((hr_armory_item_fn)(g_hr + HR_ARMORY_ITEM))(id);
            int variants = item ? *(int32_t*)(item + 0x1C) : 0;
            char line[4096];
            int at = sprintf_s(line, "reach armory map slot %d item %d raw %d:", slot, i, (uint8_t)id2[1]);
            for (int v = 0; v < variants && v < 64 && at < (int)sizeof line - 160; v++) {
                uint8_t scratch[0x80];
                memset(scratch, 0xEE, sizeof scratch);
                __try { ((hr_armory_apply_fn)(g_hr + HR_ARMORY_APPLY))(id | (uint32_t)v << 16, scratch); }
                __except (EXCEPTION_EXECUTE_HANDLER) { at += sprintf_s(line + at, sizeof line - at, " v%d[fault]", v); continue; }
                at += sprintf_s(line + at, sizeof line - at, " v%d[", v);
                for (int b = 0; b < 0x80 && at < (int)sizeof line - 16; b++)
                    if (scratch[b] != 0xEE) at += sprintf_s(line + at, sizeof line - at, "%x=%d ", b, scratch[b]);
                at += sprintf_s(line + at, sizeof line - at, "]");
            }
            logf("%s", line);
        }
    }
}

static void hr_roll_armor(uint8_t* cfg, uint32_t seed, uint32_t slots, uint32_t none) {
    hr_log_catalog();
    // slot 12 is the Firefight voice (HREK ui/supply_depot.cookie_purchase_globals: 12 voices), not armor: its equip
    // faulted on every roll (s104 log, "reach armor roll faulted" x30), so never from an older mask either. Slot 12 is
    // the last one rolled: the picks of slots 0-11 do not change
    slots &= ~(1u << 12);
    none &= ~(1u << 12);
    uint32_t x = seed ? seed : 0x9E3779B9u;
    for (int slot = 0; slot < 13; slot++)          // "none" first: the catalog's first item, raw (no hidden-item skip)
        if (none & (1u << slot)) ((hr_armory_apply_fn)(g_hr + HR_ARMORY_APPLY))((uint32_t)slot, cfg + 0x20);
    for (int slot = 0; slot < 13; slot++) {
        if (!(slots & (1u << slot))) continue;
        int n = ((hr_armory_count_fn)(g_hr + HR_ARMORY_COUNT))((char)slot);
        x ^= x << 13; x ^= x >> 17; x ^= x << 5;
        if (n < 1) continue;
        char id2[4] = {-1, -1, 0, 0};
        ((hr_armory_item_id_fn)(g_hr + HR_ARMORY_ITEM_ID))(id2, (char)slot, (char)(x % (uint32_t)n));
        if (id2[0] == -1 || id2[1] == -1) continue;
        uint32_t id = (uint8_t)id2[0] | (uint32_t)(uint8_t)id2[1] << 8;
        uint8_t* item = ((hr_armory_item_fn)(g_hr + HR_ARMORY_ITEM))(id);
        int variants = item ? *(int32_t*)(item + 0x1C) : 0;
        x ^= x << 13; x ^= x >> 17; x ^= x << 5;
        uint32_t v = variants > 0 ? x % (uint32_t)variants : 0;
        ((hr_armory_apply_fn)(g_hr + HR_ARMORY_APPLY))(id | v << 16, cfg + 0x20);
    }
}

static int64_t hr_add_bot(uint8_t* tls, int arg) {
    uint8_t* globals = *(uint8_t**)(tls + 0x48);
    uint8_t* me = hr_player(tls, 0);
    int slot = hr_free_slot();
    if (!globals || !me || slot < 0) return -2;
    int index = -1;
    for (int i = 1; i < HB_MAX_PLAYERS; i++) if (hr_player(tls, i) && !hr_live(tls, i)) { index = i; break; }
    if (index < 0) return -3;
    uint8_t cfg[HR_CONFIG];
    memcpy(cfg, globals + 0x2D8, HR_CONFIG);         // player 0's configuration as it joined
    cfg[0] = 1;
    // the machine: a player on the host's machine (arg bit 17) becomes a local user and H3
    // splits the screen for it; a machine-less one (bit 16) shows as gone and its team loses
    // (both verified). Default: an unknown machine identifier, so the machine index is -1 and
    // FUN_1800e2928 maps no local user, but the player is not flagged machine-less.
    cfg[1] = (arg & (1 << 16)) ? 1 : 0;
    if (!(arg & ((1 << 16) | (1 << 17)))) {
        *(uint32_t*)(cfg + 0x10) = 0xB07B0000u | (uint32_t)index;
        *(uint16_t*)(cfg + 0x14) = 0xB07B;
    }
    cfg[8] = 0xFF;                                    // no controller
    *(int32_t*)(cfg + 0xC) = -1;                      // no user
    *(uint64_t*)(cfg + 0x16) = 0xB07B07B000000000ull | (uint64_t)index;   // a player identifier (unaligned)
    int team = (arg >> 8) & 0xFF;                     // 0xFF = keep player 0's
    // team: int at configuration +0x98 (config +0xB0, player +0xF0; FUN_1800e2370). Player 0's
    // team on a bot leaves one team in free for all and the match ends at once (as in H2)
    if (team != 0xFF) cfg[0x98] = (uint8_t)team;     // Reach: team byte (FUN_1800440a4)
    wchar_t name[16] = {};
    if (g_shm->cmd_name[0]) { wcsncpy_s(name, 16, g_shm->cmd_name, 15); g_shm->cmd_name[0] = 0; }
    else swprintf_s(name, 16, L"Bot %d", slot + 1);
    memcpy(cfg + 0x20, name, 32);
    // the rest of the identity was the player's (logs/reach_names.txt: every bot showed the host's name and service
    // tag on the scoreboard and the post-game screen): the second name +0x78, the service tag +0x64 (4 characters)
    // and the Xbox user id +0x40 (the same made-up id as +0x16)
    memcpy(cfg + 0x78, name, 32);
    wchar_t tag[4] = {};
    bot_service_tag(name, tag);
    memcpy(cfg + 0x64, tag, 8);
    *(uint64_t*)(cfg + 0x40) = 0xB07B07B000000000ull | (uint64_t)index;
    if (g_shm->cmd_look_game == (4u | HB_LOOK_ROLL)) {   // its own armor: random picks from the game's Armory
        uint32_t seed = 0;
        uint16_t slots = 0, none = 0;
        memcpy(&seed, g_shm->cmd_look, 4);
        memcpy(&slots, g_shm->cmd_look + 4, 2);
        memcpy(&none, g_shm->cmd_look + 6, 2);          // v10: slots set to the game's none (armor effects off)
        g_shm->cmd_look_game = 0;
        __try { hr_roll_armor(cfg, seed, slots, none); }
        __except (EXCEPTION_EXECUTE_HANDLER) { logf("reach armor roll faulted (seed %08x slots %04x none %04x)", seed, slots, none); }
        int n = g_shm->cmd_look[9];                      // then n (u16 offset, u8 value) over the configuration
        for (int k = 0; k < n && 12 + 3 * k < 64; ++k) {  // (colours: primary +0x49, secondary +0x4A)
            uint16_t off = 0;
            memcpy(&off, g_shm->cmd_look + 10 + 3 * k, 2);
            if (off < HR_CONFIG) cfg[off] = g_shm->cmd_look[12 + 3 * k];
        }
    } else {
        hb_apply_look(g_shm, 4, cfg, HR_CONFIG);     // its own armor and colours (brain/looks.py)
    }
    // what MCC shows for it (name, tags, emblem: report.cpp), before MCC first looks the new player up
    report_note_bot(index, name, tag, cfg[0x98], g_shm->cmd_clan, g_shm->cmd_emblem);
    int h = ((hr_new_at_fn)(g_hr + HR_PLAYER_NEW_AT))(index, cfg, 0);
    if (h != -1) {
        // flags bit 0: what the session sets for a joined player (H2's "present" bit); a player of
        // an unknown machine never gets it and never spawns. Set, the engine spawns the bot and
        // clears the bits (verified on Construct)
        *(uint32_t*)(hr_player(tls, index) + 4) |= 1;
        // the host's machine identifier (player +0x14, 6 bytes), set after creation (at creation
        // it maps a local user and splits the screen): the server applies damage of a player's
        // shots only when his own machine is this one, else it waits for that machine's hit
        // report, which a bot never sends (verified on Reach: bot DMR hits did 0 damage, then 0.25 each)
        memcpy(hr_player(tls, index) + 0x14, hr_player(tls, 0) + 0x14, 6);
        g_shm->actions[slot].seq = 0;
        g_shm->bot_player_index[slot] = index;
    }
    logf("reach add_bot index %d team %d -> %08x", index, team, h);
    return (int64_t)h;
}

// Invasion: co-op (fireteam) spawning. Megalo action 0x5A (per player, FUN_1800f2dac; per team FUN_1801f2c4c)
// writes a byte at game globals (TLS +0x20) +0x38C + player * 0xF0; multiplayer engines 1 and 2 return it from
// vtable +0x118 (FUN_180149874, via FUN_1800810ac), and the spawn check (FUN_1800ffabc, ghidra/hr_spawnchk.c) then
// waits for the player's spawn choice (a teammate to spawn on, or a base spot) from the spawn screen. A bot has no
// spawn screen and never respawned. Called from the spawn-check hook (spawnhook.cpp) just before the check, for
// bots only: the byte cleared, a bot respawns on the normal timer at the phase's spawn points (the base spots a
// person may pick too). ghidra/hr_spawn_choice.c, hr_coop_spawn.c. (s139 kept the byte and made the choice for the
// bot, +0x1D8 = 0: 136 of 303 spawns still waited the 6 s it gave before clearing the byte 211159. Now where a bot
// spawns is the brain's pick, hr_spawn_moves.)
void hr_bot_solo_spawn(uint32_t player) {
    int index = player & 0xFFFF;
    if (!g_shm || index >= HB_MAX_PLAYERS) return;
    bool bot = false;
    for (int i = 0; i < HB_MAX_PLAYERS; i++) bot |= g_shm->bot_player_index[i] == index;
    if (!bot) return;
    __try {
        uint8_t* tls = hr_tls();
        uint8_t* gg = tls ? *(uint8_t**)(tls + 0x20) : nullptr;
        if (gg) gg[0x38C + (size_t)index * 0xF0] = 0;
    } __except (EXCEPTION_EXECUTE_HANDLER) {}
}

// Firefight respawns (GitHub #12: a bot that died stayed dead). The players update's per-player respawn handler
// FUN_18004633c (ghidra/hr_respawn_tick.c) counts a dead player's respawn time (+0x1B4, +0x1B8 = 1 pending), then
// spawns him with FUN_18004a9c8 and, in a game whose teams have lives (Firefight: game globals +0x16C + team * 4, the
// team at player +0xAC, -1 = no limit; ghidra/hr_team_lives.c), takes one of his team's lives (FUN_18010e184); none
// left, none spawn. Its why-not byte +0x408: 1 waiting, 13 the spawn failed.
// Where a Firefight bot respawns. Firefight spawns everyone as co-op (FUN_1800810ac true): a dead player comes back at
// the location stored in his player (+0x3EC position, +0x3F8 forward, +0x410 set; FUN_180049ce8,
// ghidra/hr_spawn_where.c), which the person's death screen fills (its zone, "Pavilion"). Nothing fills a bot's, so
// its spawn failed every time (live s186: why 13, no spawn point, a good biped). Before the respawn handler: a dead
// bot of a team with lives (Firefight) gets the person's own last spawn point, or else a spot 1.5 u behind him;
// the game then spawns it on time and takes the life from the team as for him (live s187 Beachhead: bots back
// after 10.0 s like the person, the team's lives 6 -> 0 one per respawn, then they waited)
// ticks (60 a second) a bot of the person's opposing team waits for the brain's pick before it falls back (10 s)
static const uint32_t HR_PICK_WAIT_TICKS = 600;
static const uint32_t HR_PICK_WAIT_SIDE_TICKS = 600;   // his side: as the other side (10 s at 60 Hz): live_bots.py waits for a free point
static uint32_t g_pick_wait_since[HB_MAX_PLAYERS];   // tick a bot began waiting for a pick (0 = not waiting)
void hr_bot_spawn_point(uint32_t player) {
    static bool logged[HB_MAX_PLAYERS];
    int index = player & 0xFFFF;
    if (!g_shm || !g_hr || index >= HB_MAX_PLAYERS) return;
    bool bot = false;
    for (int i = 0; i < HB_MAX_PLAYERS; i++) bot |= g_shm->bot_player_index[i] == index;
    if (!bot) return;
    __try {
        uint8_t* tls = hr_tls();
        uint8_t* p = tls ? hr_player(tls, index) : nullptr;
        uint8_t* gg = tls ? *(uint8_t**)(tls + 0x20) : nullptr;
        if (!p || !gg) return;
        if (*(int32_t*)(p + 0x28) != -1) { logged[index] = false; g_pick_wait_since[index] = 0; return; }   // (alive: the wait restarts at its next death)
        int team = (int8_t)p[0xAC];
        if (team < 0 || team >= 8 || p[0x410]) return;
        // Firefight: a team has lives (+0x16C + team * 4, -1 = no limit). Was asked of the BOT'S team, so a bot on a team
        // with no limit (the Covenant's, blue, against the person's Spartans) never got a spawn point and never came
        // back (the owner, Nov 10: "blue side bots aren't respawning", red ones did). Now any team's lives say it is
        // Firefight, and every bot gets a spawn point.
        bool firefight = false;
        for (int t = 0; t < 8; t++) firefight |= *(int32_t*)(gg + 0x16C + t * 4) >= 0;
        if (!firefight) return;
        uint8_t* me = nullptr;                      // the person: the first player no bot is
        for (int i = 0; i < HB_MAX_PLAYERS && !me; i++) {
            bool b = false;
            for (int k = 0; k < HB_MAX_PLAYERS; k++) b |= g_shm->bot_player_index[k] == i;
            if (!b && hr_live(tls, i)) me = hr_player(tls, i);
        }
        if (!me) return;
        float* at = (float*)(p + 0x3EC);
        const float* his = (const float*)(me + 0x3EC);
        const char* from = nullptr;
        const float* pick = g_shm->spawn_at[index];     // the brain's pick for this death (live_bots.py ff_spawn_check): its
        bool picked = g_shm->spawn_seq[index] != g_shm->spawn_done[index]      // team's own side, not the person's start
                      && isfinite(pick[0]) && isfinite(pick[1]) && isfinite(pick[2]) && isfinite(pick[3]);
        // Which side the bot is on. The person's own spawn is only right for HIS side: a bot of the other team (the
        // Spartans when he plays the Elites, or the reverse) used to come back there too, the owner, Nov 10: "the
        // Spartans spawn at my spawn". Order for a bot of the other team: (1) the brain's pick; (2) wait for it
        // (HR_PICK_WAIT_TICKS: the brain is a separate process, and at match start it may not be running yet);
        // (3) where its own team last spawned or was put (g_team_spot), or the last spawn point of a teammate who is not
        // standing at his; (4) behind a living teammate; (5) only then the person's spawn, so no bot stays dead.
        static float g_team_spot[8][6];
        static bool g_team_spot_set[8];
        const int mine = (int8_t)me[0xAC];
        const bool his_side = team == mine;
        if (picked) {                               // it spawns there itself (hr_spawn_moves then moves it to the same spot)
            at[0] = pick[0]; at[1] = pick[1]; at[2] = pick[2] + 0.1f;
            at[3] = cosf(pick[3]); at[4] = sinf(pick[3]); at[5] = 0.0f;
            memcpy(g_team_spot[team], at, 24);
            g_team_spot_set[team] = true;
            from = "the brain's pick for its team";
        } else if (his_side && (his[0] || his[1] || his[2])) {    // his side: his last spawn point and facing (the game's own pick)
            // after a short wait for the brain's pick (a free real spawn point: live_bots.py ff_spawn_check), so a bot never
            // takes his one point on top of another bot
            uint32_t now_tick = g_shm->game_tick;
            if (!g_pick_wait_since[index]) g_pick_wait_since[index] = now_tick ? now_tick : 1;
            if (now_tick - g_pick_wait_since[index] < HR_PICK_WAIT_SIDE_TICKS) return;
            memcpy(p + 0x3EC, me + 0x3EC, 24);
            from = "his last spawn point (his side)";
        } else if (!his_side) {
            uint32_t now_tick = g_shm->game_tick;
            if (!g_pick_wait_since[index]) g_pick_wait_since[index] = now_tick ? now_tick : 1;
            if (now_tick - g_pick_wait_since[index] < HR_PICK_WAIT_TICKS) return;   // (the brain's pick may still come: p[0x410] stays 0)
            if (g_team_spot_set[team]) {
                memcpy(at, g_team_spot[team], 24);
                from = "where its team last spawned";
            } else {
                // a teammate's own last spawn point, when it is not the person's (a Spartan who spawned at the Spartans' start)
                for (int i = 0; i < HB_MAX_PLAYERS && !from; i++) {
                    uint8_t* q = i == index ? nullptr : hr_player(tls, i);
                    if (!q || !hr_live(tls, i) || (int8_t)q[0xAC] != team || !q[0x410]) continue;
                    const float* qa = (const float*)(q + 0x3EC);
                    if (!(qa[0] || qa[1] || qa[2])) continue;
                    float dx = qa[0] - his[0], dy = qa[1] - his[1];
                    if (dx * dx + dy * dy < 15.0f * 15.0f) continue;       // (spawned where he did: the wrong side)
                    memcpy(at, qa, 24);
                    from = "a teammate's last spawn point";
                }
                // (no "behind a teammate": a spot behind one lies in walls and under stairs; his real spawn point is used next)
            }
            if (!from && (his[0] || his[1] || his[2])) {            // nothing of its own side known: never stay dead
                memcpy(p + 0x3EC, me + 0x3EC, 24);
                from = "his last spawn point (no spot of its own side known)";
            }
        }
        if (!from) {
            uint8_t* u = hr_object(tls, *(uint32_t*)(me + 0x28));
            if (!u) return;                         // (he is down too, and never spawned at a point: wait)
            const float* pos = (const float*)(u + 0x44);
            const float* fwd = (const float*)(u + 0x50);
            at[0] = pos[0] - 1.5f * fwd[0]; at[1] = pos[1] - 1.5f * fwd[1]; at[2] = pos[2] + 0.1f;
            at[3] = fwd[0]; at[4] = fwd[1]; at[5] = 0.0f;
            from = "behind him";
        }
        g_pick_wait_since[index] = 0;
        p[0x410] = 1;
        if (!logged[index]) {
            logged[index] = true;
            logf("reach respawn: bot %d spawns at %s (%.1f %.1f %.1f)", index, from, at[0], at[1], at[2]);
        }
    } __except (EXCEPTION_EXECUTE_HANDLER) {}
}

// Invasion spawns by the player's rules ("if tier 1 > Spawn river unlocked, Spawn Spire locked, if tier 2 > Spawn spire
// unlocked, Elites prefer spire spawn, Spartans prefer River and surrounding area spawn and ally"): while a bot is dead
// the brain picks one of the spawns a person is offered (brain/invasion.py honor_spawn: a named spawn of its side and
// phase, or behind a teammate) into spawn_at; the bot spawns on time (hr_bot_solo_spawn) and its new unit is moved
// there on its first tick by object_teleport's inner call, FUN_1801ec468(object, position, forward, up, 1)
// (ghidra/hr_teleport_inner.c: out of any vehicle, the player's own teleport FUN_180049278, then the facing): what
// Reach's scripts do to a player. Its first bytes are checked (cmp ecx, -1; je) before every call. Each move goes in
// the match's film (film.cpp R_SPAWN), so Theater moves the bot at the same tick
static const uintptr_t HR_TELEPORT = 0x1EC468;
static const uint8_t HR_TELEPORT_HEAD[9] = {0x83, 0xF9, 0xFF, 0x0F, 0x84, 0x0E, 0x02, 0x00, 0x00};
typedef void(__fastcall* hr_teleport_fn)(uint32_t object, const float* pos, const float* fwd, const float* up, char flag);

static void hr_spawn_moves(uint8_t* tls, const film_frame* fr, uint32_t tick) {
    static uint32_t last_unit[HB_MAX_PLAYERS] = {~0u, ~0u, ~0u, ~0u, ~0u, ~0u, ~0u, ~0u, ~0u, ~0u, ~0u, ~0u, ~0u, ~0u, ~0u, ~0u};
    static bool head_logged = false;
    for (int i = 0; i < HB_MAX_PLAYERS; i++) {
        int index = fr->index[i];
        if (index < 0 || index >= HB_MAX_PLAYERS || !hr_live(tls, index)) continue;
        uint32_t unit = *(uint32_t*)(hr_player(tls, index) + 0x28);
        if (!hb_spawn_take(unit, &last_unit[index], g_shm->spawn_seq[index], &g_shm->spawn_done[index])) continue;
        const float* at = g_shm->spawn_at[index];
        if (!isfinite(at[0]) || !isfinite(at[1]) || !isfinite(at[2]) || !isfinite(at[3])) continue;   // called off (NaN)
        if (memcmp(g_hr + HR_TELEPORT, HR_TELEPORT_HEAD, sizeof HR_TELEPORT_HEAD)) {
            if (!head_logged) logf("reach spawn: the teleport's first bytes differ (another game build): bots spawn where the game puts them");
            head_logged = true;
            return;
        }
        float pos[3] = {at[0], at[1], at[2]}, fwd[3] = {cosf(at[3]), sinf(at[3]), 0.0f}, up[3] = {0.0f, 0.0f, 1.0f};
        ((hr_teleport_fn)(g_hr + HR_TELEPORT))(unit, pos, fwd, up, 1);
        film_spawn(tick, index, at);
        logf("reach spawn: bot %d moved to (%.2f, %.2f, %.2f)", index, pos[0], pos[1], pos[2]);
    }
}

// a bot's loadout from its next spawn: what the host's player_loadout_request handler does
// (FUN_180458238, ghidra/hr_loadout_perform.c): player +0x3E4 and +0x3E8 = the slot. Bots only.
static int64_t hr_set_loadout(uint8_t* tls, int index, int slot) {
    bool bot = false;
    for (int i = 0; i < HB_MAX_PLAYERS; i++) bot |= g_shm->bot_player_index[i] == index;
    if (!bot || !hr_live(tls, index)) return -1;
    uint8_t* p = hr_player(tls, index);
    *(int32_t*)(p + 0x3E4) = slot;
    *(int32_t*)(p + 0x3E8) = slot;
    return slot;
}

static void hr_run_command(uint8_t* tls) {
    // only commands marked for Halo 3 (cmd_arg bit 26), like H2's bit 24 / H3's 25
    if (g_shm->cmd_seq == g_shm->cmd_done_seq || !(g_shm->cmd_arg & (1 << 26))) return;
    g_shm->cmd_fault = 0;
    __try {
        if (g_shm->cmd == HB_CMD_ADD_BOT) g_shm->cmd_result = hr_add_bot(tls, g_shm->cmd_arg);
        else if (g_shm->cmd == HB_CMD_SET_LOADOUT)
            g_shm->cmd_result = hr_set_loadout(tls, g_shm->cmd_arg & 0xFF, (g_shm->cmd_arg >> 8) & 7);
        else g_shm->cmd_result = -1;
    } __except (EXCEPTION_EXECUTE_HANDLER) { g_shm->cmd_fault = 1; g_shm->cmd_result = -1; }
    g_shm->cmd_done_seq = g_shm->cmd_seq;
}

extern uint32_t g_playback;                     // botcore.cpp: a Theater film is running (no brain)
// game globals (TLS +0x48): +0x1DA is 1 while a Theater film plays (0 in a match, whose +0x1DB is 1: recording); the
// options before it are the same in a match and in its film (dumps, tools/globals_dump.py): the film key
static const size_t HR_GG_PLAYBACK = 0x1DA, HR_GG_OPTIONS = 0x1D0, HR_GG_TICKS = 0x1F0EC;

static void __fastcall h_hr_update(uint8_t* actions) {
    if (g_shm) {
        g_shm->game_tick++;
        __try {
            uint8_t* tls = hr_tls();
            uint8_t* gg = tls ? *(uint8_t**)(tls + 0x48) : nullptr;
            const film_frame* fr = nullptr;
            if (gg) {
                g_playback = gg[HR_GG_PLAYBACK] != 0;
                fr = film_tick(film_hash(gg, HR_GG_OPTIONS), *(uint32_t*)(gg + HR_GG_TICKS), g_playback != 0, 1u << 26);
            }
            g_shm->game = 4;
            if (tls && fr) {
                hr_run_command(tls);
                hr_spawn_moves(tls, fr, *(uint32_t*)(gg + HR_GG_TICKS));
                if (*(uint32_t*)(actions + 8) & 1) {        // player 0's action, for decoding
                    memcpy(g_shm->dbg_action, actions + 0x10, HR_ACTION);
                    g_shm->dbg_seq++;
                }
                for (int i = 0; i < HB_MAX_PLAYERS; i++) {
                    int index = fr->index[i];               // this tick's input, frozen (film.cpp: a film replays it)
                    // flags bit 0 every tick: the players update spawns (and respawns) a player
                    // without a unit only while it is set (FUN_1800e444c's spawn loop), and the
                    // session clears it for a player of no known machine between ticks
                    if (index >= 0 && hr_live(tls, index)) *(uint32_t*)(hr_player(tls, index) + 4) |= 1;
                    const hb_action& a = fr->act[i];
                    if (index < 0 || !a.seq || !hr_live(tls, index)) continue;
                    uint8_t* act = actions + 0x10 + (size_t)index * HR_ACTION;
                    *(uint32_t*)(actions + 8) |= 1u << index;
                    if (g_shm->h2_raw_valid == 2) { memcpy(act, g_shm->h2_raw_action, HR_ACTION); continue; }   // decoding
                    if (*(uint32_t*)(actions + 8) & 1) memcpy(act, actions + 0x10, HR_ACTION);
                    // decoded live (tools/h2_action_record.py): +0x08 control flags (jump 0x2, fire
                    // 0x40000000), +0x10 yaw, +0x14 pitch, +0x1C throttle forward, +0x20 throttle
                    // left, +0x38 trigger, +0x40 action flags (E held 0x100000), +0x44 weapon set /
                    // +0x46 primary / +0x47 secondary index, +0xA0 input history (template)
                    uint8_t* unit = hr_object(tls, *(uint32_t*)(hr_player(tls, index) + 0x28));
                    // unit +0x34C = the action's +0x44..+0x47 (weapon set, wanted primary / secondary index):
                    // the unit takes +0x46 only when +0x44 matches its +0x34C (FUN_180483468), and the game's
                    // own input copies +0x34C there (FUN_18004bc0c). +0x348 (s65 and before) never matched:
                    // 338 switch requests in the player's Reach games, no switch. The game drops an action
                    // whose primary equals its secondary (FUN_180046ac8)
                    if (unit) memcpy(act + 0x44, unit + 0x34C, 4);
                    // +0x54: three interaction slots (u16 type, object at +8; type 0 = none), the player's
                    // in the template: his pickup prompt, his melee target (type 0xE, the melee's fallback
                    // target in FUN_18004bc0c). A bot has none of its own
                    for (int k = 0; k < 3; k++) {
                        memset(act + 0x54 + 12 * k, 0, 12);
                        *(int32_t*)(act + 0x54 + 12 * k + 8) = -1;
                    }
                    // +0x7C: the aim assist block (weights +0x8C / +0x90; FUN_18010e8f8). the player's passed the
                    // action check whole, but pulled the bot's shots toward his target: no weights, no assist
                    memset(act + 0x8C, 0, 8);
                    // the red reticle's target (shared.h hb_target), as the player's own action carries it (live,
                    // Boardwalk Infection, logs/tmp/aa_watch.py, his reticle red on a zombie): +0x78 1, +0x7C node,
                    // +0x80 the man's unit, +0x88 weight 1.0, +0x8C 0, +0x9C flags 4; within melee reach his input
                    // also puts the man in interaction slot 1 as the melee target (+0x60 type 0xE, +0x68 the unit).
                    // Cleared for bots before: no sword press lunged (issue #3)
                    int tgt = hb_target(a.buttons);
                    if (tgt >= 0 && tgt < HB_MAX_PLAYERS && hr_live(tls, tgt)) {
                        uint32_t th = *(uint32_t*)(hr_player(tls, tgt) + 0x28);
                        if (th != 0xFFFFFFFFu && hr_object(tls, th)) {
                            *(uint32_t*)(act + 0x78) = 1;
                            *(uint32_t*)(act + 0x7C) = 0;
                            *(uint32_t*)(act + 0x80) = th;
                            *(float*)(act + 0x88) = 1.0f;
                            memset(act + 0x8C, 0, 0x10);
                            *(uint32_t*)(act + 0x9C) = 4;
                            *(uint16_t*)(act + 0x60) = 0xE;
                            *(uint32_t*)(act + 0x68) = th;
                        }
                    }
                    // applied only when the int at +0 is 1 and +4 equals the unit's life counter
                    // (unit +0x288; FUN_180047050), as in Halo 3: player 0's template carries his
                    *(int32_t*)act = 1;
                    if (unit) act[4] = unit[0x288];
                    int want = (int)((a.buttons >> HB_WEAPON_SHIFT) & 7) - 1;
                    if (want >= 0) { act[0x46] = (uint8_t)want; if (act[0x47] == act[0x46]) act[0x47] = 0xFF; }
                    uint32_t b = a.buttons, flags = 0;
                    if (b & HB_BTN_JUMP) flags |= 0x2;
                    if (b & HB_BTN_FIRE) flags |= 0x40000000;
                    *(uint32_t*)(act + 0x08) = flags;
                    *(uint32_t*)(act + 0x0C) = 0;
                    *(float*)(act + 0x10) = a.yaw;
                    *(float*)(act + 0x14) = a.pitch;
                    *(float*)(act + 0x18) = 0.0f;
                    *(float*)(act + 0x1C) = a.forward;
                    *(float*)(act + 0x20) = a.strafe;
                    *(float*)(act + 0x38) = (b & HB_BTN_FIRE) ? 1.0f : 0.0f;
                    // +0x3C: the vertical input (FUN_18004d7d0 -> the unit control; a Falcon climbs on +1), the
                    // player's template's until s107: a bot pilot never left the ground (0% moving in 3 Falcon seats)
                    *(float*)(act + 0x3C) = hb_vertical(b);
                    uint32_t use = (b & HB_BTN_ACTION) ? 0x100000u : 0u;   // E held (decoded live)
                    // a bot asking for a seat picks nothing up (shared.h hb_use_picks_up; match 221404 at 219.7 s)
                    int seat = hb_seat(b);
                    bool on_foot = unit && *(uint32_t*)(unit + 0x14) == 0xFFFFFFFFu;   // (+0x14: its vehicle)
                    uint32_t loose = hb_use_picks_up(b) && on_foot ? hr_loose_weapon(tls, unit, a.yaw, a.pitch) : 0xFFFFFFFFu;
                    if (loose != 0xFFFFFFFFu) {
                        // a weapon at its feet: interaction slot 0 = {type 2, the weapon} and Use pressed
                        // (+0x40 bits 1 / 21): FUN_18004bc0c -> FUN_180050620 picks it up (unit action 0x2E)
                        *(uint16_t*)(act + 0x54) = 2;
                        *(uint32_t*)(act + 0x54 + 8) = loose;
                        use |= 0x200002u;
                    }
                    if (seat >= 0 && (b & HB_BTN_ACTION) && on_foot) {
                        // on foot by a vehicle: interaction slot 0 = {type 5, seat at +4, the vehicle at +8} and Use
                        // (FUN_180051278: types 5/6/9 -> FUN_18048668c, the unit enters that seat)
                        uint32_t veh = hr_near_vehicle(tls, unit, (b & HB_BTN_TURRET) != 0);
                        if (veh != 0xFFFFFFFFu) {
                            *(uint16_t*)(act + 0x54) = 5;
                            *(int16_t*)(act + 0x54 + 4) = (int16_t)seat;
                            *(uint32_t*)(act + 0x54 + 8) = veh;
                            use |= 0x200002u;
                        }
                    }
                    if (seat < 0 && (b & HB_BTN_ACTION) && unit && *(uint32_t*)(unit + 0x14) != 0xFFFFFFFFu) {
                        // in a seat, getting out: Use pressed (+0x40 bits 1 / 21). FUN_18004bc0c: a unit in a vehicle
                        // (+0x14) and its seat (+0x336) with those bits starts unit action 0xC when the vehicle's seat
                        // state (+8 bit 1) allows; held alone (0x100000) never got out (9 of 12 bot seats). Not while
                        // an enter request (seat >= 0) is still on: in on one tick, out the next
                        use |= 0x200002u;
                    }
                    *(uint32_t*)(act + 0x40) = use;
                    static uint32_t map_prev[HB_MAX_PLAYERS];  // v8 decoded buttons (shared.h)
                    hb_apply_buttons(g_shm, 4, act, HR_ACTION, b, map_prev[i]);
                    map_prev[i] = b;
                }
            }
        } __except (EXCEPTION_EXECUTE_HANDLER) { g_shm->cmd_fault = 2; }
    }
    o_hr_update(actions);
}

static DWORD tls_index_of(uint8_t* mod) {
    IMAGE_NT_HEADERS64* nt = (IMAGE_NT_HEADERS64*)(mod + ((IMAGE_DOS_HEADER*)mod)->e_lfanew);
    DWORD rva = nt->OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_TLS].VirtualAddress;
    if (!rva) return 0;
    IMAGE_TLS_DIRECTORY64* t = (IMAGE_TLS_DIRECTORY64*)(mod + rva);
    return *(DWORD*)t->AddressOfIndex;
}

// called from botcore's main loop: hook halo3.dll when it is loaded (again)
void reach_watch() {
    uint8_t* h3 = (uint8_t*)GetModuleHandleW(L"haloreach.dll");
    if (!h3) return;
    bool hooked = false;
    __try { hooked = h3 == g_hr && !memcmp(h3 + HR_PLAYERS_UPDATE, g_hr_patch, 5); }
    __except (EXCEPTION_EXECUTE_HANDLER) { hooked = false; }
    if (hooked) return;
    Sleep(500);
    if ((uint8_t*)GetModuleHandleW(L"haloreach.dll") != h3) return;
    if (g_hr) MH_RemoveHook(g_hr + HR_PLAYERS_UPDATE);
    g_hr = h3;
    g_hr_tls_index = tls_index_of(h3);
    for (int i = 0; i < HB_MAX_PLAYERS; i++) g_shm->bot_player_index[i] = -1;
    if (MH_CreateHook(h3 + HR_PLAYERS_UPDATE, (void*)&h_hr_update, (void**)&o_hr_update) != MH_OK ||
        MH_EnableHook(h3 + HR_PLAYERS_UPDATE) != MH_OK) {
        logf("h3 hook failed at %p", h3);
        return;
    }
    memcpy(g_hr_patch, h3 + HR_PLAYERS_UPDATE, 5);
    logf("reach hooks on at haloreach %p: players update +0x%llx, tls index %lu", h3,
         (unsigned long long)HR_PLAYERS_UPDATE, g_hr_tls_index);
}
