// h4f.cpp: Halo 2 Anniversary (groundhog.dll) and Halo 4 (halo4.dll) player bots, MCC 1.3528:
// one engine family (Reach's successor), one code path, per-game constants (docs/NEXT-01).
// - TLS block: +0x8 players (header +0x20 element size, +0x28 '@t@d', +0x2C max, +0x50 data),
//   +0x18 objects (0x18-byte entries: +0 salt, +4 type, +0x10 object address), configurations in
//   the block at TLS+0x90 (+0x358: the player's, header 0x20 + body).
// - players update(actions): +8 a 64-bit mask of players with an action, +0x10 + index * 0x100
//   each player's action; an action is used only when its int at +0 is 1 and the byte at +4
//   equals the unit's life counter (unit +0x578).
// - player_new_at_index(index, config, 0): config +0 valid, +1 no machine, +8 controller, +0xC
//   user, +0x10 machine (6 bytes), +0x16 player identifier, +0x20 body (name at +0 and +0x100).
//   Player +0x1C machine identifier (6 bytes), +0x2C unit.
#include <windows.h>
#include <math.h>
#include <stdio.h>
#include <string.h>
#include <wchar.h>
#include <intrin.h>
#include "shared.h"
#include "third_party/minhook/include/MinHook.h"

extern hb_shared* g_shm;
extern uint32_t g_prejoin;                     // botcore.cpp (botcore.ini prejoin=)
void logf(const char* fmt, ...);
void report_note_bot(int index, const wchar_t* name, const wchar_t* tag, int team, const wchar_t* clan,
                     const uint16_t* emblem);   // report.cpp
void bot_service_tag(const wchar_t* name, wchar_t tag[4]);                                                // report.cpp

struct h4f_game {
    const wchar_t* dll;
    const char* tag;
    int id, cmd_bit;
    uintptr_t players_update, player_new_at;
    // unit: the weapons asked for (set / primary / secondary), the action's +0x48: +0x63C, as the game fills a player's
    // action from it (halo4 FUN_1800895a8, FUN_1800ac690); +0x638 is the weapons in hand. From +0x638 a bot asked for
    // its old gun every tick while H2A switched to a flag just taken, so the flag fell again after 0.2 s (Zenith CTF,
    // 20 of 21 runs); at rest the two are the same
    size_t weapon_index;
    size_t loadout;               // player: the loadout slot its player_loadout_request handler writes
    // the game's own Armory catalog, the same shape as Reach's (ghidra/h4_armory_cluster.c, h2a_armory.c): per slot
    // (20) the visible item count, the n-th visible item's id (slot, raw index), the item record (+0 name string id,
    // +0x1C variant count). Found by Reach's code pattern ("hidden item" function, slot bound 0x14)
    uintptr_t arm_count, arm_item_id, arm_item;
    // equipping a variant, as the game's own equip (H4 FUN_1802cc444 / its check FUN_1802ccb88): the variant's entry
    // (kind-0 slots: the armor pieces), its (region, permutation) pairs into the appearance block's +0x38 + region,
    // its second set into +0x40 + region, its special value (0 = none for this game) into +0x2B
    uintptr_t arm_entry, arm_pairs, arm_pairs2, arm_special;
    uint8_t* mod;
    uint8_t patch[5];
    DWORD tls_index;
    void (__fastcall* orig)(uint8_t*);
    uint32_t map_prev[HB_MAX_PLAYERS];   // each bot's buttons last tick (press-only decoded buttons)
};
static void __fastcall h2a_update(uint8_t* actions);
static void __fastcall h4_update(uint8_t* actions);
// loadout: H2A FUN_1801c5be0 (ghidra/h2a_loadout_set.c), H4 FUN_1801c67a0 (ghidra/h4_loadout_set.c)
static h4f_game g_h2a = { L"groundhog.dll", "h2a", 6, 28, 0x80F88, 0x7C3F8, 0x63C, 0x94C, 0x2AE9A4, 0x2AEA1C, 0x2AEC04,
                          0x2AE680, 0x2CC6B4, 0x2CC734, 0 };
static h4f_game g_h4 = { L"halo4.dll", "h4", 7, 29, 0x82330, 0x7D948, 0x63C, 0x944, 0x2AFA94, 0x2AFB0C, 0x2AFCF4,
                         0x2AF770, 0x2CD738, 0x2CD7B8, 0x2CD9AC };
static const size_t F_ACTION = 0x100, F_CONFIG = 0x200;
typedef int(__fastcall* f_new_at_fn)(int index, uint8_t* config, char flag);
typedef int(__fastcall* f_count_fn)(char slot);
typedef char*(__fastcall* f_item_id_fn)(char* out, char slot, char index);
typedef uint8_t*(__fastcall* f_item_fn)(uint32_t id);
typedef uint8_t*(__fastcall* f_entry_fn)(uint32_t id);
typedef char(__fastcall* f_pairs_fn)(uint8_t* entry, int i, int* region, int* perm);
typedef uint32_t(__fastcall* f_special_fn)(uint8_t* entry);
static const uint8_t F_COUNT_HEAD[] = { 0x48, 0x89, 0x5C, 0x24, 0x08, 0x48, 0x89, 0x6C, 0x24, 0x10, 0x48, 0x89 };
static const uint8_t F_ITEMID_HEAD[] = { 0x40, 0x53, 0x48, 0x83, 0xEC, 0x20, 0x66, 0x81, 0x09, 0xFF, 0xFF, 0x8D };
static const uint8_t F_ITEM_HEAD[] = { 0x48, 0x89, 0x5C, 0x24, 0x10, 0x89, 0x4C, 0x24, 0x08, 0x57, 0x48, 0x83 };
static const uint8_t F_ENTRY_HEAD[] = { 0x48, 0x89, 0x5C, 0x24, 0x18, 0x89, 0x4C, 0x24, 0x08, 0x56, 0x57, 0x41 };
static const uint8_t F_PAIRS_HEAD[] = { 0x4C, 0x8B, 0xDC, 0x49, 0x89, 0x5B, 0x10, 0x49, 0x89, 0x6B, 0x18, 0x49 };
static const uint8_t F_SPECIAL_HEAD[] = { 0x48, 0x89, 0x5C, 0x24, 0x08, 0x41, 0x83, 0xC8, 0xFF, 0x33, 0xD2, 0x48 };
// the appearance block is the configuration's +0x20 (as Reach's), so the region permutations sit at +0x58 (live,
// Halo 4: +0x58 the helmet, +0x60 the first of the second set, the armor effect: 0 = none); the configuration holds
// a second copy 0x128 on. Regions 0-7, second set 0-3 (+0x64 is the service tag)
static const size_t F_APPEARANCE = 0x20, F_APPEARANCE_COPY = 0x20 + 0x128;
static const int F_SLOTS = 16;                            // catalog slots the 16-bit roll masks reach

static bool f_armory_ok(h4f_game& G) {
    static int ok[8];                                     // per game: 0 not checked, 1 the catalog code is there
    int& s = ok[G.id & 7];
    if (!s) {
        __try {
            s = (!memcmp(G.mod + G.arm_count, F_COUNT_HEAD, sizeof F_COUNT_HEAD) &&
                 !memcmp(G.mod + G.arm_item_id, F_ITEMID_HEAD, sizeof F_ITEMID_HEAD) &&
                 !memcmp(G.mod + G.arm_item, F_ITEM_HEAD, sizeof F_ITEM_HEAD) &&
                 !memcmp(G.mod + G.arm_entry, F_ENTRY_HEAD, sizeof F_ENTRY_HEAD) &&
                 !memcmp(G.mod + G.arm_pairs, F_PAIRS_HEAD, sizeof F_PAIRS_HEAD) &&
                 !memcmp(G.mod + G.arm_pairs2, F_PAIRS_HEAD, sizeof F_PAIRS_HEAD) &&
                 (!G.arm_special || !memcmp(G.mod + G.arm_special, F_SPECIAL_HEAD, sizeof F_SPECIAL_HEAD))) ? 1 : -1;
        } __except (EXCEPTION_EXECUTE_HANDLER) { s = -1; }
        logf("%s armory catalog %s", G.tag, s > 0 ? "found" : "not the decoded build: bots keep the player's armor");
    }
    return s > 0;
}

static bool f_apply_variant(h4f_game& G, uint8_t* app, uint32_t id, char* log, size_t logn);

// the catalog once a session in the log, as Reach's: per slot its item count and each item's raw index, variant
// count and first word (likely its name's string id)
static void f_log_catalog(h4f_game& G) {
    static bool done[8];
    if (done[G.id & 7]) return;
    done[G.id & 7] = true;
    for (int slot = 0; slot < 20; slot++) {
        char line[4096];
        int n = ((f_count_fn)(G.mod + G.arm_count))((char)slot), at = 0;
        at += sprintf_s(line + at, sizeof line - at, "%s armory slot %d: %d items:", G.tag, slot, n);
        for (int i = 0; i < n && i < 128 && at < (int)sizeof line - 32; i++) {
            char id2[4] = { -1, -1, 0, 0 };
            ((f_item_id_fn)(G.mod + G.arm_item_id))(id2, (char)slot, (char)i);
            uint8_t* item = ((f_item_fn)(G.mod + G.arm_item))((uint8_t)id2[0] | (uint32_t)(uint8_t)id2[1] << 8);
            at += sprintf_s(line + at, sizeof line - at, " %d:%d:%08x", (uint8_t)id2[1], item ? *(int32_t*)(item + 0x1C) : -1,
                            item ? *(uint32_t*)item : 0);
            // what each variant puts on (an armor piece's regions; nothing for other kinds): the profile view's 3D bots
            // wear the pieces these name (tools/armory_tables.py)
            for (uint32_t v = 0; item && (int)v < *(int32_t*)(item + 0x1C) && at < (int)sizeof line - 200; v++) {
                uint8_t scratch[0x80] = {};
                char what[160] = "";
                if (f_apply_variant(G, scratch, ((uint8_t)id2[0] | (uint32_t)(uint8_t)id2[1] << 8) | v << 16, what, sizeof what))
                    at += sprintf_s(line + at, sizeof line - at, " [v%u%s]", v, what);
            }
        }
        logf("%s", line);
    }
}

// one catalog variant on an appearance block, as the game's equip does it; false if the slot is not an armor piece
// (kind 0) or the id is no entry. Which regions it wrote go to `log` when given (the catalog log)
static bool f_apply_variant(h4f_game& G, uint8_t* app, uint32_t id, char* log, size_t logn) {
    uint8_t* entry = ((f_entry_fn)(G.mod + G.arm_entry))(id);
    if (!entry) return false;
    int at = 0, n1 = *(int32_t*)(entry + 0x104), n2 = *(int32_t*)(entry + 0x110);
    for (int i = 0; i < n1 && i < 16; i++) {
        int r = -1, p = -1;
        if (((f_pairs_fn)(G.mod + G.arm_pairs))(entry, i, &r, &p) && r >= 0 && r < 8) {
            app[0x38 + r] = (uint8_t)p;
            if (log) at += sprintf_s(log + at, logn - at, " r%d=%d", r, p);
        }
    }
    for (int i = 0; i < n2 && i < 16; i++) {
        int r = -1, p = -1;
        if (((f_pairs_fn)(G.mod + G.arm_pairs2))(entry, i, &r, &p) && r >= 0 && r < 4) {
            app[0x40 + r] = (uint8_t)p;
            if (log) at += sprintf_s(log + at, logn - at, " s%d=%d", r, p);
        }
    }
    if (G.arm_special) {
        uint32_t s = ((f_special_fn)(G.mod + G.arm_special))(entry);
        if (s != 0xFFFFFFFFu) {
            app[0x2B] = (uint8_t)s;
            if (log) at += sprintf_s(log + at, logn - at, " x=%u", s);
        }
    }
    return true;
}

// a bot's armor from the game's own Armory: per armor-piece slot in the mask a random visible item and variant,
// equipped on both copies of the configuration's appearance block; a slot in the none mask gets its first variant
// (the armor effect's none). Legal by construction: only what the catalog hands out
static void f_roll_armor(h4f_game& G, uint8_t* cfg, uint32_t seed, uint32_t slots, uint32_t none) {
    f_log_catalog(G);
    uint32_t x = seed ? seed : 0x9E3779B9u;
    for (int slot = 0; slot < F_SLOTS; slot++) {
        bool off = (none >> slot) & 1;
        if (!off && !((slots >> slot) & 1)) continue;
        int n = ((f_count_fn)(G.mod + G.arm_count))((char)slot);
        x ^= x << 13; x ^= x >> 17; x ^= x << 5;
        if (n < 1) continue;
        char id2[4] = { -1, -1, 0, 0 };
        ((f_item_id_fn)(G.mod + G.arm_item_id))(id2, (char)slot, (char)(off ? 0 : x % (uint32_t)n));
        if (id2[0] == -1 || id2[1] == -1) continue;
        uint32_t id = (uint8_t)id2[0] | (uint32_t)(uint8_t)id2[1] << 8;
        uint8_t* item = ((f_item_fn)(G.mod + G.arm_item))(id);
        int variants = item ? *(int32_t*)(item + 0x1C) : 0;
        x ^= x << 13; x ^= x >> 17; x ^= x << 5;
        uint32_t v = off || variants < 1 ? 0 : x % (uint32_t)variants;
        f_apply_variant(G, cfg + F_APPEARANCE, id | v << 16, nullptr, 0);
        f_apply_variant(G, cfg + F_APPEARANCE_COPY, id | v << 16, nullptr, 0);
    }
}

static uint8_t* f_tls(h4f_game& G) {
    void** arr = (void**)__readgsqword(0x58);
    return arr ? (uint8_t*)arr[G.tls_index] : nullptr;
}
static uint8_t* f_player(uint8_t* tls, int index) {
    uint8_t* hdr = *(uint8_t**)(tls + 0x8);
    if (!hdr || index < 0 || index >= *(int32_t*)(hdr + 0x2C)) return nullptr;
    uint8_t* data = *(uint8_t**)(hdr + 0x50);
    return data ? data + (size_t)index * *(int32_t*)(hdr + 0x20) : nullptr;
}
static bool f_live(uint8_t* tls, int index) { uint8_t* p = f_player(tls, index); return p && *(uint16_t*)p != 0; }
static uint8_t* f_object(uint8_t* tls, uint32_t handle) {
    uint8_t* hdr = *(uint8_t**)(tls + 0x18);
    if (!hdr || handle == 0xFFFFFFFFu || (int)(handle & 0xFFFF) >= *(int32_t*)(hdr + 0x2C)) return nullptr;
    uint8_t* e = *(uint8_t**)(hdr + 0x50) + (size_t)(handle & 0xFFFF) * 0x18;
    return *(uint16_t*)e == handle >> 16 ? *(uint8_t**)(e + 0x10) : nullptr;
}

// boarding (supervisor: "find how the game handles Use for vehicles and fix the press"). A bot's Use beside a vehicle
// entered nothing (Bloodline: 6016 presses, no rider; its action, player and unit hold no interaction). The game's
// own vehicle_load_magic (its script command; evaluator H4 FUN_180699574, H2A FUN_18069a2a4, found by the name's
// registration) lists a vehicle's seats and its guns' ({object, seat}: H4 FUN_1806063bc, H2A FUN_1806081d4), takes an
// empty one (no child of that object in that seat: H4 FUN_1805f4d68, H2A FUN_1805f69fc) the unit may take (H4
// FUN_1805f323c, H2A FUN_1805f4e44), and seats the unit through the unit's own virtual +0x38 (unit, object, seat, 0,
// 7). botcore does the same for a bot on foot holding Use with a seat asked for (shared.h HB_SEAT_SHIFT) by the
// nearest vehicle within 3 u: a seat on its gun with HB_BTN_TURRET, else the seat asked for on the vehicle itself,
// else its first free one there. Prologues checked first (any other build: no boarding)
struct f_board_fns { uintptr_t seats, occupant, can; };
static const f_board_fns F_BOARD_H2A = { 0x6081D4, 0x5F69FC, 0x5F4E44 }, F_BOARD_H4 = { 0x6063BC, 0x5F4D68, 0x5F323C };
static const uint8_t F_SEATS_HEAD[] = { 0x48, 0x89, 0x5C, 0x24, 0x08, 0x48, 0x89, 0x6C, 0x24, 0x10, 0x48, 0x89 };
static const uint8_t F_CAN_HEAD[] = { 0x48, 0x89, 0x5C, 0x24, 0x08, 0x48, 0x89, 0x74, 0x24, 0x10, 0x57, 0x48 };
static const uint8_t F_OCC_HEAD[] = { 0x44, 0x8B, 0x05 }, F_OCC_TAIL[] = { 0x44, 0x0F, 0xB7, 0xD2, 0x65 };   // (rel32 between)
struct f_seat { uint32_t obj; int16_t seat; int16_t pad; uint64_t def; };
typedef void(__fastcall* f_seats_fn)(uint32_t vehicle, f_seat* out, int16_t* count, int16_t max);
typedef uint32_t(__fastcall* f_occupant_fn)(uint16_t obj, int16_t seat);
typedef char(__fastcall* f_can_fn)(uint32_t unit, uint16_t obj, int16_t seat);
typedef char(__fastcall* f_enter_fn)(uint8_t* unit, uint32_t obj, int seat, int zero, uint8_t how);

static const f_board_fns* f_board_ok(h4f_game& G) {
    static int ok[8];
    const f_board_fns* B = G.id == 7 ? &F_BOARD_H4 : &F_BOARD_H2A;
    int& s = ok[G.id & 7];
    if (!s) {
        __try {
            s = (!memcmp(G.mod + B->seats, F_SEATS_HEAD, sizeof F_SEATS_HEAD) &&
                 !memcmp(G.mod + B->can, F_CAN_HEAD, sizeof F_CAN_HEAD) &&
                 !memcmp(G.mod + B->occupant, F_OCC_HEAD, sizeof F_OCC_HEAD) &&
                 !memcmp(G.mod + B->occupant + 7, F_OCC_TAIL, sizeof F_OCC_TAIL)) ? 1 : -1;
        } __except (EXCEPTION_EXECUTE_HANDLER) { s = -1; }
        logf("%s vehicle seats %s", G.tag, s > 0 ? "found: bots board by Use" : "not the decoded build: bots stay on foot");
    }
    return s > 0 ? B : nullptr;
}

// the vehicle a bot on foot boards: the nearest within 3 u (and 2.5 u of its height), top level (not a gun riding on
// one), or NONE. Halo 4: one on its side or roof is passed over (its up vector at object +0x7C, the forward one at
// +0x70: found live on Exile; H2A's not checked yet). Taken in, a bot was put out a second later, again and again: the
// Warthog at Exile's double spawn lay on its roof beside the standing one a bot went for, and was the nearer
static uint32_t f_near_vehicle(h4f_game& G, uint8_t* tls, uint8_t* unit) {
    uint8_t* hdr = *(uint8_t**)(tls + 0x18);
    uint8_t* ents = hdr ? *(uint8_t**)(hdr + 0x50) : nullptr;
    if (!ents) return 0xFFFFFFFFu;
    const float* me = (const float*)(unit + 0x40);
    uint32_t veh = 0xFFFFFFFFu;
    float best = 3.0f * 3.0f;
    for (int i = 0, mx = *(int32_t*)(hdr + 0x2C); i < mx; i++) {
        uint8_t* e = ents + (size_t)i * 0x18;
        uint8_t* o = *(uint16_t*)e && e[4] == 1 ? *(uint8_t**)(e + 0x10) : nullptr;   // type 1: a vehicle
        if (!o || *(uint32_t*)(o + 0x24) != 0xFFFFFFFFu) continue;                      // top level (not a gun on one)
        if (G.id == 7 && ((const float*)(o + 0x7C))[2] < 0.5f) continue;                // on its side or roof
        if (o[0x16C] & 4) continue;   // destroyed (damage flags bit 2: both games' object_get_health reads 0 then)
        const float* q = (const float*)(o + 0x40);
        float d = (q[0] - me[0]) * (q[0] - me[0]) + (q[1] - me[1]) * (q[1] - me[1]);
        if (d < best && fabsf(q[2] - me[2]) < 2.5f) { best = d; veh = ((uint32_t)*(uint16_t*)e << 16) | (uint32_t)i; }
    }
    return veh;
}

static void f_board(h4f_game& G, uint8_t* tls, int index, uint32_t buttons) {
    static uint32_t next_try[HB_MAX_PLAYERS * 2];
    int want = (int)((buttons >> HB_SEAT_SHIFT) & 15) - 1;
    if (!(buttons & HB_BTN_ACTION) || want < 0 || index < 0 || index >= HB_MAX_PLAYERS * 2) return;
    if (g_shm->game_tick < next_try[index]) return;
    next_try[index] = g_shm->game_tick + 15;                    // a try every half second while it holds Use
    const f_board_fns* B = f_board_ok(G);
    uint8_t* p = f_player(tls, index);
    uint32_t uh = p ? *(uint32_t*)(p + 0x2C) : 0xFFFFFFFFu;
    uint8_t* unit = f_object(tls, uh);
    if (!B || !unit || *(uint32_t*)(unit + 0x24) != 0xFFFFFFFFu) return;   // on foot only (+0x24: what it rides)
    uint32_t veh = f_near_vehicle(G, tls, unit);
    if (veh == 0xFFFFFFFFu) return;
    static f_seat seats[64];
    int16_t n = 0;
    ((f_seats_fn)(G.mod + B->seats))(veh, seats, &n, 64);
    static uint32_t logged[32];                                 // each vehicle kind's seats once: which is the driver's
    uint32_t vtag = *(uint32_t*)(f_object(tls, veh) + 8);
    bool seen = false;
    for (int k = 0; k < 32 && !seen; k++) seen = logged[k] == vtag;
    for (int k = 0; k < 32 && !seen; k++) if (!logged[k]) { logged[k] = vtag; break; }
    if (!seen) {
        char line[1024];
        int at = sprintf_s(line, sizeof line, "%s vehicle tag %08x seats:", G.tag, vtag);
        for (int k = 0; k < n && at < (int)sizeof line - 48; k++)
            at += sprintf_s(line + at, sizeof line - at, " [%s%d flags %08x]", seats[k].obj != veh ? "gun " : "", seats[k].seat,
                            seats[k].def ? *(uint32_t*)seats[k].def : 0);
        logf("%s", line);
    }
    // the seat by its role, from its flags (the seat definition's first dword, as Halo 3's: bit 2 driver, bit 3 gunner;
    // bit 27 marks the boarding / hijack spots; live Bloodline: Warthog 0, Ghost 0, Hornet 1, Gungoose 0 carry bit 2,
    // the Warthog's gun bit 3). The brain asks seat 0 for the driver's, the gun with HB_BTN_TURRET, any other number
    // for a passenger's (vehicles.reach_seat); seat 0 was a Hornet's side seat
    int pick = -1;
    bool turret = (buttons & HB_BTN_TURRET) != 0;
    uint32_t role = turret ? 0x8u : want == 0 ? 0x4u : 0u;
    for (int k = 0; k < n && pick < 0; k++) {
        uint32_t fl = seats[k].def ? *(uint32_t*)seats[k].def : 0;
        if (fl & 0x08000000u) continue;                               // a boarding / hijack spot, not a seat
        if (role ? !(fl & role) : (fl & 0xCu) != 0) continue;
        if (((f_occupant_fn)(G.mod + B->occupant))((uint16_t)seats[k].obj, seats[k].seat) != 0xFFFFFFFFu) continue;
        if (!((f_can_fn)(G.mod + B->can))(uh, (uint16_t)seats[k].obj, seats[k].seat)) continue;
        pick = k;
    }
    if (pick < 0) return;
    char in = ((f_enter_fn)(*(void***)unit)[0x38 / 8])(unit, seats[pick].obj, seats[pick].seat, 0, 7);
    logf("%s bot %d boards %08x seat %d%s: %s", G.tag, index, seats[pick].obj, seats[pick].seat, turret ? " (gun)" : "",
         in ? "in" : "refused");
}

// leaving (supervisor: "bot vehicle exits in H2A and Halo 4 (botcore exit call)"). A bot's Use in a seat got it out of
// nothing (H2A Bloodline: drivers held Use at the end of trips and stayed in). The game's own unit_exit_vehicle (its
// script command, found by the name's registration: H4 FUN_180699220, H2A FUN_180699f50; the one-argument form is the
// thunk 8 bytes before it, type 0) returns unless the unit rides something (+0x24) in a seat (+0x2C), and queues the
// exit as unit action 0xC (H4 FUN_18062c5dc, H2A FUN_18062e0e4). botcore calls it for a bot in a seat holding Use with
// no seat asked for, a try every 15 ticks as boarding's. Both checked first (any other build: no exits)
static const uintptr_t F_EXIT_H4 = 0x699220, F_EXIT_H2A = 0x699F50;
static const uint8_t F_EXIT_THUNK[] = { 0x33, 0xD2, 0xE9, 0x01, 0x00, 0x00, 0x00, 0xCC };   // xor edx, edx; jmp: type 0
static const uint8_t F_EXIT_HEAD[] = { 0x48, 0x89, 0x5C, 0x24, 0x08, 0x55, 0x48, 0x8B, 0xEC, 0x48, 0x83, 0xEC, 0x70 };
typedef void(__fastcall* f_exit_fn)(uint32_t unit, int16_t type);

static f_exit_fn f_exit_ok(h4f_game& G) {
    static int ok[8];
    uintptr_t at = G.id == 7 ? F_EXIT_H4 : F_EXIT_H2A;
    int& s = ok[G.id & 7];
    if (!s) {
        __try {
            s = (!memcmp(G.mod + at - sizeof F_EXIT_THUNK, F_EXIT_THUNK, sizeof F_EXIT_THUNK) &&
                 !memcmp(G.mod + at, F_EXIT_HEAD, sizeof F_EXIT_HEAD)) ? 1 : -1;
        } __except (EXCEPTION_EXECUTE_HANDLER) { s = -1; }
        logf("%s vehicle exit %s", G.tag, s > 0 ? "found: bots get out by Use" : "not the decoded build: bots stay in");
    }
    return s > 0 ? (f_exit_fn)(G.mod + at) : nullptr;
}

static void f_exit(h4f_game& G, uint8_t* tls, int index, uint32_t buttons) {
    static uint32_t next_try[HB_MAX_PLAYERS * 2];
    if (!(buttons & HB_BTN_ACTION) || ((buttons >> HB_SEAT_SHIFT) & 15) != 0 || index < 0 || index >= HB_MAX_PLAYERS * 2)
        return;
    if (g_shm->game_tick < next_try[index]) return;
    uint8_t* p = f_player(tls, index);
    uint32_t uh = p ? *(uint32_t*)(p + 0x2C) : 0xFFFFFFFFu;
    uint8_t* unit = f_object(tls, uh);
    if (!unit || *(uint32_t*)(unit + 0x24) == 0xFFFFFFFFu || *(int16_t*)(unit + 0x2C) == -1) return;   // in a seat only
    f_exit_fn out = f_exit_ok(G);
    if (!out) return;
    next_try[index] = g_shm->game_tick + 15;
    logf("%s bot %d gets out of %08x seat %d", G.tag, index, *(uint32_t*)(unit + 0x24), *(int16_t*)(unit + 0x2C));
    out(uh, 0);
}

// a bot's loadout from its next spawn: what the host's player_loadout_request handler does (two
// player fields = the slot, G.loadout and the next int). Bots only.
static int64_t f_set_loadout(h4f_game& G, uint8_t* tls, int index, int slot) {
    bool bot = false;
    for (int i = 0; i < HB_MAX_PLAYERS; i++) bot |= g_shm->bot_player_index[i] == index;
    if (!G.loadout || !bot || !f_live(tls, index)) return -1;
    uint8_t* p = f_player(tls, index);
    *(int32_t*)(p + G.loadout) = slot;
    *(int32_t*)(p + G.loadout + 4) = slot;
    return slot;
}

static int64_t f_add_bot(h4f_game& G, uint8_t* tls, int arg) {
    uint8_t* cfgs = *(uint8_t**)(tls + 0x90);
    int slot = -1;
    for (int i = 0; i < HB_MAX_PLAYERS; i++) if (g_shm->bot_player_index[i] < 0) { slot = i; break; }
    if (!cfgs || !f_player(tls, 0) || slot < 0) return -2;
    int index = -1;
    for (int i = 1; i < HB_MAX_PLAYERS; i++) if (f_player(tls, i) && !f_live(tls, i)) { index = i; break; }
    if (index < 0) return -3;
    static uint8_t cfg[F_CONFIG];
    memcpy(cfg, cfgs + 0x358, F_CONFIG);              // the player's configuration (keeps his team)
    cfg[0] = 1;
    cfg[1] = 0;
    cfg[8] = 0xFF;                                    // no controller
    *(int32_t*)(cfg + 0xC) = -1;                      // no user
    *(uint32_t*)(cfg + 0x10) = 0xB07B0000u | (uint32_t)index;   // an unknown machine
    *(uint16_t*)(cfg + 0x14) = 0xB07B;
    uint64_t bot_id = 0xB07B07B000000000ull | (uint64_t)index;      // in every place the player's id was (H3 has a
    hb_replace_id(cfg, F_CONFIG, *(uint64_t*)(cfg + 0x16), bot_id);  // second copy MCC's report goes by)
    *(uint64_t*)(cfg + 0x16) = bot_id;
    // team: byte at configuration +0x140 (after the second name, as Reach's +0x98; the player
    // init reads it). the player's team on a bot in free for all leaves one team: the match
    // ends
    int team = (arg >> 8) & 0xFF;                     // 0xFF = keep the player's (Firefight, Spartan Ops)
    if (team != 0xFF) cfg[0x140] = (uint8_t)team;
    wchar_t name[16] = {};
    if (g_shm->cmd_name[0]) { wcsncpy_s(name, 16, g_shm->cmd_name, 15); g_shm->cmd_name[0] = 0; }
    else swprintf_s(name, 16, L"Bot %d", slot + 1);
    memcpy(cfg + 0x20, name, 32);
    memcpy(cfg + 0x120, name, 32);
    wchar_t tag[4];                                   // its service tag, both copies (+0x64, +0x18C)
    bot_service_tag(name, tag);
    memcpy(cfg + 0x64, tag, 8);
    memcpy(cfg + 0x18C, tag, 8);
    if (g_shm->cmd_look_game == ((uint32_t)G.id | HB_LOOK_ROLL)) {   // its own armor: picks from the game's Armory
        uint32_t seed = 0;
        uint16_t slots = 0, none = 0;
        memcpy(&seed, g_shm->cmd_look, 4);
        memcpy(&slots, g_shm->cmd_look + 4, 2);
        memcpy(&none, g_shm->cmd_look + 6, 2);
        g_shm->cmd_look_game = 0;
        uint8_t effect = g_shm->cmd_look[8];             // the armor effect (appearance +0x40 = configuration +0x60:
        if (f_armory_ok(G)) {                             // 0 none, 2 the purple flaming head, seen live); 0xFF keep
            __try { f_roll_armor(G, cfg, seed, slots, none); }
            __except (EXCEPTION_EXECUTE_HANDLER) { logf("%s armor roll faulted (seed %08x slots %04x)", G.tag, seed, slots); }
        }
        if (effect != 0xFF) {
            cfg[F_APPEARANCE + 0x40] = effect;
            cfg[F_APPEARANCE_COPY + 0x40] = effect;
        }
        int n = g_shm->cmd_look[9];                      // then n (u16 offset, u8 value) over the configuration, in the
        for (int k = 0; k < n && 12 + 3 * k < 64; ++k) {  // appearance block both copies (Halo 4 colours and visor,
            uint16_t off = 0;                             // primary +0x49, secondary +0x4A, visor +0x4B)
            memcpy(&off, g_shm->cmd_look + 10 + 3 * k, 2);
            uint8_t v = g_shm->cmd_look[12 + 3 * k];
            if (off < F_CONFIG) cfg[off] = v;
            if (off >= F_APPEARANCE && off < F_APPEARANCE_COPY && off + 0x128 < F_CONFIG) cfg[off + 0x128] = v;
        }
    } else {
        hb_apply_look(g_shm, (uint32_t)G.id, cfg, sizeof cfg);   // its own armor and colours (brain/looks.py)
    }
    // what MCC shows for it (name, tags, emblem: report.cpp), before MCC first looks the new player up
    report_note_bot(index, name, tag, team != 0xFF ? team : -1, g_shm->cmd_clan, g_shm->cmd_emblem);
    int h = ((f_new_at_fn)(G.mod + G.player_new_at))(index, cfg, 0);
    if (h != -1) {
        *(uint32_t*)(f_player(tls, index) + 4) |= 1;
        memcpy(f_player(tls, index) + 0x1C, f_player(tls, 0) + 0x1C, 6);     // the host's machine
        g_shm->actions[slot].seq = 0;
        g_shm->bot_player_index[slot] = index;
    }
    logf("%s add_bot index %d -> %08x", G.tag, index, h);
    return (int64_t)h;
}

// Halo 4's sprint is the hero_assist ability every loadout carries (unit +0xD40, its state slot +0x534; the armor
// ability is +0x664 / +0x52C). FUN_180089118 turns a press (action +0x40 bit 34; bit 33 the armor ability's) into
// "wanted on" (+0x08 bit 9; bit 8) against the ability's state, so a press TOGGLES it, and only with the slot at +0x60
// (+0x5C); the body sprints while +0x08 bit 17 is held as well (live, Adrift: pressed and held, a bot ran 3.32 u/s, the
// player's sprint 3.34, walking 2.40; the press alone switched it on and left the bot at 2.37). So: press while the
// game's state differs from the wish, hold bit 17 while wanted.
static void f_sprint(uint8_t* tls, uint8_t* act, uint8_t* unit, bool want) {
    int32_t slot = *(int32_t*)(unit + 0x534);
    uint8_t* ha = f_object(tls, *(uint32_t*)(unit + 0xD40));
    if (!ha || slot < 0) return;
    *(int32_t*)(act + 0x60) = slot;
    bool on = *(int32_t*)(ha + 0x494 + (size_t)slot * 0x1C) != -1 && !(*(uint32_t*)(ha + 0x16C) & 4);
    if (want != on) *(uint64_t*)(act + 0x40) |= 1ull << 34;
    if (want) *(uint64_t*)(act + 0x08) |= 0x20000;
}

// H2A takes a weapon (the CTF flag is one) through the action's interaction slots (+0x64, +0x70, +0x7C: u16 type, the
// object at +8; empty: type 0, object NONE): with Use (+0x40 bit 1 or 23) FUN_180088248 hands slot 0 to FUN_18008e14c,
// which takes a weapon object in a type 5, 8 or 13 slot (mask 0x2120; FUN_18008d924 mask 4 = object type 2) as unit
// action 0x32 after its own checks (type 6 is equipment's, FUN_18008dbc0: a type 3 object only). Bots had only the
// player's template slots (his own pickup prompt), so Use on the flag took nothing (Lockdown and Zenith CTF: a runner
// held Use 474 ticks on it). A bot gets its own: the nearest loose weapon within 1 u while it holds Use on foot with no
// seat asked. (Calling the host's pickup directly, FUN_18008f6e4, crashed MCC: the equipment path)
static uint32_t g_offer_at[HB_MAX_PLAYERS * 2];   // H2A: the tick a bot took a pickup (the quiet after it, f_update)
static uint32_t g_hold_obj[HB_MAX_PLAYERS * 2];   // H2A: the object a bot is holding Use on (0: none), and until when
static uint32_t g_hold_until[HB_MAX_PLAYERS * 2], g_hold_from[HB_MAX_PLAYERS * 2];
static const uint32_t HOLD_TICKS = 90;            // 3 s: the longest a held pickup is offered (Assault's bomb takes ~1)
static const uint64_t H2A_USE = 0xC00000ull;      // +0x40 Use, as maps/_inputs/h2a.json decodes the brain's action

static uint8_t* f_entity(uint8_t* tls, uint32_t handle) {   // a weapon object by its handle, nullptr if gone
    uint8_t* hdr = *(uint8_t**)(tls + 0x18);
    uint8_t* ents = hdr ? *(uint8_t**)(hdr + 0x50) : nullptr;
    uint32_t i = handle & 0xFFFF;
    if (!ents || !handle || (int32_t)i >= *(int32_t*)(hdr + 0x2C)) return nullptr;
    uint8_t* e = ents + (size_t)i * 0x18;
    if (*(uint16_t*)e != (uint16_t)(handle >> 16) || e[4] != 2) return nullptr;
    return *(uint8_t**)(e + 0x10);
}

// who holds a weapon object: its parent (+0x24) when that is a unit (entry type 0 biped, 1 vehicle), else NONE. A gun on
// a weapon pad and Assault's bomb on its stand have the pad or the stand as parent (brain/live.py PARENT_AT): counted
// as held, the bomb was never offered (live Lockdown Multi-Bomb s175: "BOMB AT BASE" for 4 min, bots on it)
static uint32_t f_holder(uint8_t* tls, uint8_t* o) {
    uint32_t p = *(uint32_t*)(o + 0x24);
    uint8_t* hdr = p != 0xFFFFFFFFu ? *(uint8_t**)(tls + 0x18) : nullptr;
    uint8_t* ents = hdr ? *(uint8_t**)(hdr + 0x50) : nullptr;
    if (!ents || (int32_t)(p & 0xFFFF) >= *(int32_t*)(hdr + 0x2C)) return p;
    return ents[(size_t)(p & 0xFFFF) * 0x18 + 4] <= 1 ? p : 0xFFFFFFFFu;
}

static void f_interaction(uint8_t* tls, uint8_t* act, uint8_t* unit, uint32_t b, int index) {
    static uint32_t next_offer[HB_MAX_PLAYERS * 2];
    for (int k = 0; k < 3; k++) {
        memset(act + 0x64 + 12 * k, 0, 8);
        *(uint32_t*)(act + 0x64 + 12 * k + 8) = 0xFFFFFFFFu;
    }
    if (index < 0 || index >= HB_MAX_PLAYERS * 2) return;
    int want = (int)((b >> HB_SEAT_SHIFT) & 15) - 1;
    uint32_t now = g_shm->game_tick;
    if (g_hold_obj[index]) {
        // a pickup the game takes only after Use is held a while (GitHub #7, H2A Assault's bomb: "requires a hold press
        // not a tap"): the same object offered every tick while Use stays down, never another; the quiet (no Use, f_update)
        // starts once it is in this bot's hands. It used to start at the offer and let go of the hold the next tick
        // The hold is botcore's own (f_update keeps Use down): the brain pulses its action (live Lockdown Multi-Bomb s173,
        // trace 040313: a bot stood on its bomb 4 min, Use in some ticks and not the next, and every gap ended the hold)
        uint8_t* o = f_entity(tls, g_hold_obj[index]);
        uint32_t holder = o ? f_holder(tls, o) : 0xFFFFFFFFu;
        uint32_t mine = unit ? *(uint32_t*)(f_player(tls, index) + 0x2C) : 0xFFFFFFFFu;
        const float* q = o ? (const float*)(o + 0x40) : nullptr;
        const float* me = unit ? (const float*)(unit + 0x40) : nullptr;
        bool reach = q && me && (q[0] - me[0]) * (q[0] - me[0]) + (q[1] - me[1]) * (q[1] - me[1]) < 1.44f &&
                     fabsf(q[2] - me[2]) < 1.5f;                   // (walked off it: let go, Use near a seat boards)
        if (o && holder == 0xFFFFFFFFu && reach && want < 0 && now < g_hold_until[index]) {
            *(uint16_t*)(act + 0x64) = 13;
            *(uint32_t*)(act + 0x6C) = g_hold_obj[index];
            return;
        }
        bool took = o && holder != 0xFFFFFFFFu && holder == mine;
        if (took) g_offer_at[index] = now - 1;                    // taken: the quiet from now
        logf("h2a pickup: bot %d %s %08x (tag %08x) after %u ticks%s", index, took ? "took" : "let go of",
             g_hold_obj[index], o ? *(uint32_t*)(o + 8) : 0u,
             now - g_hold_from[index], took ? "" : !o ? " (gone)" : holder != 0xFFFFFFFFu ? " (someone else's)" :
             !reach ? " (walked off)" : want >= 0 ? " (a seat asked)" : " (3 s up)");
        g_hold_obj[index] = 0;
        next_offer[index] = now + 45;
        return;
    }
    if (!(b & HB_BTN_ACTION) || want >= 0 || !unit || *(uint32_t*)(unit + 0x24) != 0xFFFFFFFFu) return;
    if (now < next_offer[index]) return;
    uint8_t* hdr = *(uint8_t**)(tls + 0x18);
    uint8_t* ents = hdr ? *(uint8_t**)(hdr + 0x50) : nullptr;
    if (!ents) return;
    const float* me = (const float*)(unit + 0x40);
    uint32_t pick = 0xFFFFFFFFu;
    float best = 1.0f;                                           // (squared: within 1 u)
    for (int i = 0, mx = *(int32_t*)(hdr + 0x2C); i < mx; i++) {
        uint8_t* e = ents + (size_t)i * 0x18;
        uint8_t* o = *(uint16_t*)e && e[4] == 2 ? *(uint8_t**)(e + 0x10) : nullptr;   // type 2: a weapon
        if (!o || f_holder(tls, o) != 0xFFFFFFFFu) continue;                            // loose: no unit holds it
        const float* q = (const float*)(o + 0x40);
        float d = (q[0] - me[0]) * (q[0] - me[0]) + (q[1] - me[1]) * (q[1] - me[1]);
        if (d < best && fabsf(q[2] - me[2]) < 1.5f) { best = d; pick = ((uint32_t)*(uint16_t*)e << 16) | (uint32_t)i; }
    }
    if (pick == 0xFFFFFFFFu) return;
    // only this object while the hold lasts, and 1.5 s before the next offer: the held Use took the next loose gun the
    // tick after a flag pickup (its own, dropped by the swap; the brain hears of the carry a few ticks late), so the flag
    // fell again after 0.2 s (Zenith CTF s126, 4 of 5 carries)
    g_hold_obj[index] = pick;                          // (offered on until taken: above)
    g_hold_until[index] = now + HOLD_TICKS; g_hold_from[index] = now;
    *(uint16_t*)(act + 0x64) = 13;   // (one of the three accepted; 5 worked too, the 0.2 s drops were the weapon request's)
    *(uint32_t*)(act + 0x6C) = pick;
}

// Halo 4's use target (Dominion's terminals). A person's action carries what his view rests on in the interaction slots
// (+0x64, +0x70, +0x7C: u16 type, the object at +8), set by the game's own targeting before his Use. Aimed at a terminal
// (the owner's input logs, Nov 10, three terminals in two matches): slots 0 and 2 hold type 7 and the terminal's handle
// (+0x64 7, +0x6C handle, +0x7C 7, +0x84 handle), slot 1 none (+0x78 NONE); idle they are 0 and NONE. A bot's action is
// the player's as the template and nothing set its own target, so its Use (down 5 s, 0.5 u from a terminal) took
// nothing (MCC's report: Captured 0 for every bot, 3 for the person). The brain sends the terminal's handle in
// hb_shared.use_target[slot]; it stands in the slots from then on, like a person's target before his press. A handle
// that is not a live object is ignored (the template's slots stay)
static void f_use_target(uint8_t* tls, uint8_t* act, uint32_t handle) {
    if (!handle || handle == 0xFFFFFFFFu || !f_object(tls, handle)) return;
    *(uint32_t*)(act + 0x64) = 7;
    *(uint32_t*)(act + 0x68) = 0;
    *(uint32_t*)(act + 0x6C) = handle;
    *(uint32_t*)(act + 0x70) = 0;
    *(uint32_t*)(act + 0x74) = 0;
    *(uint32_t*)(act + 0x78) = 0xFFFFFFFFu;
    *(uint32_t*)(act + 0x7C) = 7;
    *(uint32_t*)(act + 0x80) = 0;
    *(uint32_t*)(act + 0x84) = handle;
}

// decoded live in H2A with tools/h2_action_record.py: +0 1, +4 life counter, +0x08 control
// flags (jump 0x2), +0x0C fire (2 while the trigger is down), +0x10 yaw, +0x14 pitch, +0x1C
// throttle forward, +0x20 throttle left, +0x38 trigger, +0x40 action (E 0x400000), +0x48
// weapon set / +0x4A primary / +0x4B secondary index (unit +0x638), +0xE0 input history
static void f_fill(h4f_game& G, uint8_t* tls, uint8_t* act, int index, const hb_action& a) {
    uint8_t* unit = f_object(tls, *(uint32_t*)(f_player(tls, index) + 0x2C));
    *(int32_t*)act = 1;
    if (unit) act[4] = unit[0x578];
    if (unit) memcpy(act + 0x48, unit + G.weapon_index, 4);
    int want = (int)((a.buttons >> HB_WEAPON_SHIFT) & 7) - 1;
    if (want >= 0) { act[0x4A] = (uint8_t)want; if (act[0x4B] == act[0x4A]) act[0x4B] = 0xFF; }
    uint32_t b = a.buttons;
    // +0x08: bit 0 crouch (held; decoded from the player's Ctrl), bit 1 jump
    *(uint32_t*)(act + 0x08) = ((b & HB_BTN_JUMP) ? 0x2u : 0u) | ((b & HB_BTN_CROUCH) ? 0x1u : 0u);
    *(uint32_t*)(act + 0x0C) = (b & HB_BTN_FIRE) ? 0x2u : 0u;
    *(float*)(act + 0x10) = a.yaw;
    *(float*)(act + 0x14) = a.pitch;
    *(float*)(act + 0x18) = 0.0f;
    *(float*)(act + 0x1C) = a.forward;
    *(float*)(act + 0x20) = a.strafe;
    *(float*)(act + 0x38) = (b & HB_BTN_FIRE) ? 1.0f : 0.0f;
    // the whole word: the player's action is the template, and its high half holds his presses (+0x40 bit 34, Halo
    // 4's sprint press, toggled every bot's sprint whenever he pressed Shift)
    *(uint64_t*)(act + 0x40) = (b & HB_BTN_ACTION) ? 0x400000u : 0u;
    if (G.id == 7 && unit) f_sprint(tls, act, unit, (b & HB_BTN_SPRINT) != 0);
    if (G.id == 6) f_interaction(tls, act, unit, b, index);
    // the red reticle's target (shared.h hb_target), as the player's own action carries it: Halo 4 (live, Adrift Flood,
    // logs/tmp/aa_watch.py, his reticle red on a man) +0x88 1, +0x8C node, +0x90 the man's unit, +0x98 weight (to 1.0),
    // +0xAC flags 4: Reach's block 0x10 on. And the melee target in interaction slot 1 (+0x70 type 0xE, +0x78 the unit),
    // as Reach's (INFERRED here, and the whole block for H2A). Bots had the player's template's or none (issue #3)
    int tgt = hb_target(a.buttons);
    if (tgt >= 0 && tgt < HB_MAX_PLAYERS && f_live(tls, tgt)) {
        uint32_t th = *(uint32_t*)(f_player(tls, tgt) + 0x2C);
        if (th != 0xFFFFFFFFu && f_object(tls, th)) {
            *(uint32_t*)(act + 0x88) = 1;
            *(uint32_t*)(act + 0x8C) = 0;
            *(uint32_t*)(act + 0x90) = th;
            *(float*)(act + 0x98) = 1.0f;
            memset(act + 0x9C, 0, 0x10);
            *(uint32_t*)(act + 0xAC) = 4;
            *(uint16_t*)(act + 0x70) = 0xE;
            *(uint32_t*)(act + 0x78) = th;
        }
    }
}

// the bot's own player id in both of its bodies (+0xC0 the host's, +0x1E8 the client's: the id at +0x20 of each). The
// game writes the player's there (the bot plays from his machine) and MCC names a player by it: his name and emblem
// on every bot's post-game row (live Adrift: bots patched so showed their own names, clan tags and emblems in the
// post-game report, the one left with his id came out as him)
static void f_own_ids(uint8_t* p, int index) {
    uint64_t id = 0xB07B07B000000000ull | (uint64_t)index;
    if (*(uint64_t*)(p + 0xE0) != id) *(uint64_t*)(p + 0xE0) = id;
    if (*(uint64_t*)(p + 0x208) != id) *(uint64_t*)(p + 0x208) = id;
}

// one command from the brain (shared.h cmd): add a bot, set a loadout
static void f_command(h4f_game& G, uint8_t* tls) {
    if (g_shm->cmd_seq == g_shm->cmd_done_seq || !(g_shm->cmd_arg & (1 << G.cmd_bit))) return;
    g_shm->cmd_fault = 0;
    __try {
        g_shm->cmd_result = g_shm->cmd == HB_CMD_ADD_BOT ? f_add_bot(G, tls, g_shm->cmd_arg)
                          : g_shm->cmd == HB_CMD_SET_LOADOUT ? f_set_loadout(G, tls, g_shm->cmd_arg & 0xFF, (g_shm->cmd_arg >> 8) & 7)
                          : -1;
    } __except (EXCEPTION_EXECUTE_HANDLER) { g_shm->cmd_fault = 1; g_shm->cmd_result = -1; }
    g_shm->cmd_done_seq = g_shm->cmd_seq;
}

static bool f_any_bot(uint8_t* tls) {         // a bot in this match's players (its id: f_own_ids)
    for (int i = 0; i < HB_MAX_PLAYERS; i++)
        if (f_live(tls, i) && (*(uint64_t*)(f_player(tls, i) + 0xE0) >> 32) == 0xB07B07B0u) return true;
    return false;
}

// H2A makes each team's CTF flag, stand and goal on a match's first tick, only for the teams that have players by then
// (Zenith Multi Flag: red's goal on the first tick, the bots 11-20 ticks later, so never a blue flag or goal and no
// capture possible). The brain starts on that tick (botcore.cpp start_brain), so the tick waits here for it, taking its
// commands (tools/play.py adds the bots), until none has come for 1 s after one did, 8 s at most. botcore.ini
// prejoin=0: off
static void f_prejoin(h4f_game& G, uint8_t* tls) {
    DWORD t0 = GetTickCount(), last = 0;
    int n = 0;
    // (no command in 4 s: no bots are coming, as on a map the brain does not know; the match goes on)
    while (GetTickCount() - t0 < 8000 && !(last && GetTickCount() - last > 1000) && (last || GetTickCount() - t0 < 4000)) {
        uint32_t done = g_shm->cmd_done_seq;
        f_command(G, tls);
        if (g_shm->cmd_done_seq != done) { n++; last = GetTickCount(); }
        Sleep(5);
    }
    logf("%s first tick held %lu ms for the bots (%d commands)", G.tag, GetTickCount() - t0, n);
}

static void f_update(h4f_game& G, uint8_t* actions) {
    if (!g_shm) return;
    g_shm->game_tick++;
    g_shm->game = (uint32_t)G.id;
    __try {
        uint8_t* tls = f_tls(G);
        if (!tls) return;
        static DWORD last_call[8];                    // ticks come 60 a second while a match runs: a gap is a new match
        bool first = GetTickCount() - last_call[G.id & 7] > 3000;
        if (first && (G.id == 6 || G.id == 7)) {
            // and the match's own clock near zero (player +0xA94 H2A, +0xA8C Halo 4, 60 a second: brain/live.py LiveH4F
            // MATCH), so a pause or a stall later in a match never holds a tick. Halo 4 too: Flood picks its first round's
            // Flood from the players there at the start; the bots joined 0.3-8 s later, so the first round had none and
            // the game ended it at once (the owner's three Flood games, Nov 4: all 16 removed at full health ~14.7 s,
            // no Flood all round 1, two in round 2)
            int clock = f_player(tls, 0) ? *(int32_t*)(f_player(tls, 0) + (G.id == 6 ? 0xA94 : 0xA8C)) : -1;
            bool hold = g_prejoin && g_shm->bots_enabled && clock >= 0 && clock < 60 && !f_any_bot(tls);
            logf("%s first tick: match clock %d, %s", G.tag, clock, hold ? "holding for the bots" : "not held");
            if (hold) f_prejoin(G, tls);
        }
        last_call[G.id & 7] = GetTickCount();
        f_command(G, tls);
        uint64_t& mask = *(uint64_t*)(actions + 8);
        if (mask & 1) {                                   // the player's action, for decoding
            memcpy(g_shm->dbg_action, actions + 0x10, F_ACTION);
            g_shm->dbg_seq++;
        }
        for (int i = 0; i < HB_MAX_PLAYERS; i++) {
            int index = g_shm->bot_player_index[i];
            if (index >= 0 && f_live(tls, index)) {
                *(uint32_t*)(f_player(tls, index) + 4) |= 1;
                f_own_ids(f_player(tls, index), index);
            }
            const hb_action& a = g_shm->actions[i];
            if (index < 0 || !a.seq || !f_live(tls, index)) continue;
            uint8_t* act = actions + 0x10 + (size_t)index * F_ACTION;
            mask |= 1ull << index;
            if (g_shm->h2_raw_valid == 2) { memcpy(act, g_shm->h2_raw_action, F_ACTION); continue; }
            if (mask & 1) memcpy(act, actions + 0x10, F_ACTION);    // the player's as the template
            f_fill(G, tls, act, index, a);
            if (G.id == 7) f_use_target(tls, act, g_shm->use_target[i]);     // v14: a terminal's target (above)
            hb_apply_buttons(g_shm, (uint32_t)G.id, act, F_ACTION, a.buttons, G.map_prev[i]);   // v8 (shared.h)
            if (G.id == 6 && index < HB_MAX_PLAYERS * 2 && g_hold_obj[index])
                *(uint64_t*)(act + 0x40) |= H2A_USE;      // a held pickup (f_interaction): Use stays down till it is in hand
            if (G.id == 6 && index < HB_MAX_PLAYERS * 2 && g_offer_at[index] &&
                g_shm->game_tick > g_offer_at[index] && g_shm->game_tick < g_offer_at[index] + 45) {
                // a pickup just offered: no Use and no weapon switch while it happens. The flag is in no weapon slot, so
                // the brain hears of the carry a few ticks late, and its held Use dropped the flag again after 0.2 s
                // (Zenith CTF s126 / s127: 8 of 9 carries)
                *(uint64_t*)(act + 0x40) &= ~0xC00002ull;
                uint8_t* u = f_object(tls, *(uint32_t*)(f_player(tls, index) + 0x2C));
                if (u) memcpy(act + 0x48, u + G.weapon_index, 4);
            }
            G.map_prev[i] = a.buttons;
            __try { f_board(G, tls, index, a.buttons); }
            __except (EXCEPTION_EXECUTE_HANDLER) { logf("%s boarding faulted (bot %d)", G.tag, index); }
            __try { f_exit(G, tls, index, a.buttons); }
            __except (EXCEPTION_EXECUTE_HANDLER) { logf("%s exit faulted (bot %d)", G.tag, index); }
        }
    } __except (EXCEPTION_EXECUTE_HANDLER) { g_shm->cmd_fault = 2; }
}
static void __fastcall h2a_update(uint8_t* actions) { f_update(g_h2a, actions); g_h2a.orig(actions); }
static void __fastcall h4_update(uint8_t* actions) { f_update(g_h4, actions); g_h4.orig(actions); }

static DWORD tls_index_of(uint8_t* mod) {
    IMAGE_NT_HEADERS64* nt = (IMAGE_NT_HEADERS64*)(mod + ((IMAGE_DOS_HEADER*)mod)->e_lfanew);
    DWORD rva = nt->OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_TLS].VirtualAddress;
    if (!rva) return 0;
    return *(DWORD*)((IMAGE_TLS_DIRECTORY64*)(mod + rva))->AddressOfIndex;
}

static void f_watch(h4f_game& G, void* detour) {
    uint8_t* m = (uint8_t*)GetModuleHandleW(G.dll);
    if (!m) return;
    bool hooked = false;
    __try { hooked = m == G.mod && !memcmp(m + G.players_update, G.patch, 5); }
    __except (EXCEPTION_EXECUTE_HANDLER) { hooked = false; }
    if (hooked) return;
    Sleep(500);
    if ((uint8_t*)GetModuleHandleW(G.dll) != m) return;
    if (G.mod) MH_RemoveHook(G.mod + G.players_update);
    G.mod = m;
    G.tls_index = tls_index_of(m);
    for (int i = 0; i < HB_MAX_PLAYERS; i++) g_shm->bot_player_index[i] = -1;
    if (MH_CreateHook(m + G.players_update, detour, (void**)&G.orig) != MH_OK ||
        MH_EnableHook(m + G.players_update) != MH_OK) {
        logf("%s hook failed at %p", G.tag, m);
        return;
    }
    memcpy(G.patch, m + G.players_update, 5);
    logf("%s hooks on at %p: players update +0x%llx, tls index %lu", G.tag, m,
         (unsigned long long)G.players_update, G.tls_index);
}

void h4f_watch() {
    f_watch(g_h2a, (void*)&h2a_update);
    f_watch(g_h4, (void*)&h4_update);
}
