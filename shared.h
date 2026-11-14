// Shared memory between the in-game DLL (botcore.dll) and the Python brain.
// The brain writes one action per bot each tick; the DLL copies them into the game.
#pragma once
#include <math.h>
#include <stdint.h>
#include <string.h>

#define HB_SHM_NAME L"Local\\HaloMCCBots31"   // renamed per build while old DLLs stay loaded
#define HB_MAGIC 0x54424248u  // "HBBT"
#define HB_VERSION 13
#define HB_MAX_PLAYERS 16
#define HB_BTN_MAP 32         // v8: decoded buttons, all games together (hb_btn_entry.game)

#pragma pack(push, 1)
struct hb_action {          // mirrors Halo CE player_action
    float forward, strafe;  // -1..1, strafe +1 = left
    float yaw, pitch;       // radians
    uint32_t buttons;       // HB_BTN_*
    uint32_t seq;           // 0 = brain has not driven this bot yet (DLL leaves its input alone)
};

enum : uint32_t {
    HB_BTN_FIRE = 1 << 0, HB_BTN_JUMP = 1 << 1, HB_BTN_CROUCH = 1 << 2, HB_BTN_GRENADE = 1 << 3,
    HB_BTN_MELEE = 1 << 4, HB_BTN_ACTION = 1 << 5, HB_BTN_RELOAD = 1 << 6,
    HB_BTN_EQUIPMENT = 1 << 7,  // v8: use equipment / armor ability
    HB_WEAPON_SHIFT = 8,    // bits 8-10: desired weapon slot + 1 (0 = keep the current one)
    HB_BTN_SPRINT = 1 << 11,    // v8: Halo 4's sprint
    HB_ZOOM_SHIFT = 12,         // bits 12-13: scope level wanted, 0 none, 1 first, 2 second (brain Action.zoom)
    HB_SEAT_SHIFT = 14,         // bits 14-17: vehicle seat wanted + 1 with ACTION held (Reach: the enter-seat interaction)
    HB_BTN_TURRET = 1 << 18,    // ... in the gun riding on the vehicle (a Warthog's turret is its own vehicle)
    HB_VERT_SHIFT = 19,         // bits 19-20: a hovering aircraft's vertical input (Reach's Falcon): 0 none, 1 up (+1),
                                // 2 down (-1), 3 hold (+0.5); hb_vertical()
    HB_BTN_FIRE2 = 1 << 21,     // a vehicle's second weapon (a Banshee's bomb, Reach's fuel rod): reaches a game only
                                // through its decoded button table (maps/_inputs/<game>.json "fire2", hb_apply_buttons)
    HB_BTN_CYCLE = 1 << 22,     // a press of the weapon cycle (Reach: the Banshee's fuel rod is cycled to); decoded table only
    HB_BTN_ABILITY = 1 << 23,   // with EQUIPMENT: a hold-mode armor ability held (Reach action +0x08 bit 8, maps/_inputs/
                                // reach.json "ability"; the brain sends it only for kinds whose tag is in hold mode).
                                // Decoded table only (hb_apply_buttons): no build needed for it to reach the game
    HB_TARGET_SHIFT = 24,       // bits 24-28: the player index + 1 a sword / hammer / melee press locks onto (0 none): the
                                // red reticle's target the game's lunge needs in the action (hb_target; issue #3)
};
static inline int hb_target(uint32_t buttons) { return (int)((buttons >> HB_TARGET_SHIFT) & 31) - 1; }

// Which loose weapon a bot's Use picks up (Reach: botcore fills the pickup interaction itself). Of the weapons lying
// within reach (1.0 u across, 1 u up or down from the feet), the one nearest the view line when one lies within 0.6 rad
// of it (a person's Use takes what he looks at: the brain's take puts the view on the core, a flag, a bomb), else the
// nearest within 0.6 u (walking over guns). -1: none. The nearest-within-0.6-u rule alone swapped a Spire runner's
// rifle for a gun lying by the core while it looked at the core 0.85 u off, then left it 0.62 u from the core with Use
// doing nothing (live; this morning's 45 s at 0.62 u was the same radius).
static inline int hb_pick_loose(const float (*pos)[3], int n, const float feet[3], float yaw, float pitch) {
    const float vx = cosf(pitch) * cosf(yaw), vy = cosf(pitch) * sinf(yaw), vz = sinf(pitch);
    int view = -1, nearest = -1;
    float best_view = 0.6f, best_d2 = 0.6f * 0.6f;
    for (int i = 0; i < n; i++) {
        float dx = pos[i][0] - feet[0], dy = pos[i][1] - feet[1], dz = pos[i][2] - feet[2];
        float d2 = dx * dx + dy * dy;
        if (d2 > 1.0f || dz <= -1.0f || dz >= 1.0f) continue;
        if (d2 < best_d2) { best_d2 = d2; nearest = i; }
        float ez = dz - 0.62f, len = sqrtf(d2 + ez * ez);           // from the eye
        if (len < 1e-3f) continue;
        float c = (dx * vx + dy * vy + ez * vz) / len;
        float ang = acosf(c > 1.0f ? 1.0f : c < -1.0f ? -1.0f : c);
        if (ang < best_view) { best_view = ang; view = i; }
    }
    return view >= 0 ? view : nearest;
}

// The seat a bot asks for (HB_SEAT_SHIFT bits, -1 none), and whether its Use picks up a loose weapon (Reach): never
// while it asks for a seat
static inline int hb_seat(uint32_t buttons) { return (int)((buttons >> HB_SEAT_SHIFT) & 15) - 1; }
static inline bool hb_use_picks_up(uint32_t buttons) { return (buttons & HB_BTN_ACTION) && hb_seat(buttons) < 0; }

// A weapon a bot's Use may take by what it rides on (its parent's object type, -1 none): never one in someone's hands
// or on a vehicle (0 biped, 1 vehicle), as Halo 3's h3_pickup_target; a marker's or a stand's yes (Oddball's ball, a
// flag). Reach took a vehicle's own weapon. Every game's Use picks up on foot only: seated, Use is the way out
static inline bool hb_loose_parent_ok(int parent_type) { return parent_type != 0 && parent_type != 1; }

// The vertical input a level stands for (Reach action +0x3C, decoded live with tools/falcon_probe.py: +1 climbed a
// Falcon 6.8 u in 6 s after a 2 s spool-up, 0 sinks it about 1.5 u/s, +0.5 held its height within 0.8 u flying forward)
static inline float hb_vertical(uint32_t buttons) {
    static const float level[4] = {0.0f, 1.0f, -1.0f, 0.5f};
    return level[(buttons >> HB_VERT_SHIFT) & 3];
}

// v8: a button decoded for one game (tools/decode_buttons.py -> maps/_inputs/<game>.json, written here
// by the brain at start): while a bot holds btn (or on the press tick only, press_only), these mask
// bits are set in the u64 at action + off; they are cleared first, so a skilled player's presses in
// the template action never reach a bot
struct hb_btn_entry { uint32_t btn; uint16_t off; uint8_t press_only; uint8_t game; uint64_t mask; };

// ADD_BOT arg: machine | team << 8 (team 0 red, 1 blue, 0xFF = game default); result: player handle.
// ADOPT arg: player index of an existing bot. SET_TEAM arg: player index | team << 8 (takes
// effect on the next spawn). SET_LOADOUT arg (Reach, Halo 4, H2A): bot player index | slot << 8, the
// slot a player_loadout_request carries (used at the next spawn); result: the slot, -1 refused.
enum : uint32_t { HB_CMD_NONE = 0, HB_CMD_ADD_BOT = 1, HB_CMD_ADOPT = 2, HB_CMD_SET_TEAM = 3,
                  HB_CMD_SET_LOADOUT = 4 };

struct hb_shared {
    uint32_t magic, version;
    uint32_t dll_alive;         // DLL heartbeat (its own thread)
    uint32_t brain_alive;       // brain heartbeat
    uint32_t game_tick;         // counted in the game-tick hook (game thread)
    int32_t  bot_player_index[HB_MAX_PLAYERS];  // bot slot -> player index, -1 = slot unused
    hb_action actions[HB_MAX_PLAYERS];
    char     status[256];       // last DLL status line, for the brain's log
    // v2: commands, run on the game thread at the next tick
    uint32_t cmd, cmd_seq, cmd_done_seq;
    int32_t  cmd_arg;           // e.g. machine index for ADD_BOT
    int64_t  cmd_result;        // e.g. new player handle, or -1
    uint32_t cmd_fault;         // 1 if the call raised an exception (caught)
    // v4: the Bots tab (overlay in the CE custom game lobby) settings, also saved in botcore.ini
    uint32_t bots_enabled;      // 1 = fill teams with bots when a match starts
    uint32_t red_players, blue_players;   // wanted team sizes, people included
    uint32_t difficulty;        // 0 easy (novice) .. 3 legendary (top), 4 mixed, 5 scaling
    uint32_t settings_seq;      // bumped on every change
    // v5: ADD_BOT name (empty = "Bot N")
    wchar_t  cmd_name[16];
    // v6: which game the hooks are in (1 CE, 2 H2, 3 H3), action decoding aids
    uint32_t game;
    uint32_t dbg_seq;           // bumped when dbg_action is refreshed
    uint8_t  dbg_action[0x100]; // H2 / H3: player 0's raw action this tick (0x60 / 0x88 bytes)
    uint32_t h2_raw_valid;      // 1 = use h2_raw_action as the base of every bot action (H2);
                                // 2 = send it as is, no fields from hb_action (H3 decoding)
    uint8_t  h2_raw_action[0x100];   // v7: 0x100 (was 0x60)
    uint8_t  dbg_big[0x400];         // player 0's whole action for games with bigger ones (ODST 0x240)
    // LiDAR mode (Bots tab): fill the match with mapping bots (tools/lidar.py) instead of fighters
    uint32_t lidar_mode;
    uint32_t lidar_players;          // wanted players in all, people included (2..16)
    // a notice drawn over the game for a few seconds (LiDAR: "JUMP LEARNED|..."): title|line
    uint32_t notice_seq;             // bumped after notice is written
    char     notice[128];
    // v8: decoded buttons (hb_btn_entry); btn 0 = an empty entry
    uint32_t btn_map_seq;
    hb_btn_entry btn_map[HB_BTN_MAP];
    // v9: the next ADD_BOT's look (its own armor and colours, brain/looks.py): for game cmd_look_game (0 = none,
    // the copy of the player's), byte k of the bot's configuration from cmd_look_off on is cmd_look[k] where
    // cmd_look_mask[k] is set. The add clears cmd_look_game. cmd_look_game | HB_LOOK_ROLL (Reach, s74): roll the
    // bot's armor from the game's own Armory instead, seed = cmd_look[0..3], slot mask = cmd_look[4..5]; v10: the
    // Halo 4 / H2A (s84): cmd_look[8] = the armor effect value for the appearance block's +0x40 (0xFF = keep);
    // slots in cmd_look[6..7] get the catalog's first item, the game's "none" (armor effects off: a bot is made
    // from the player's configuration and kept his flames); cmd_look[9] = n, then n (u16 configuration offset, u8
    // value) at cmd_look[10 + 3k] laid over the rolled configuration (Halo 4 colours and visor)
    uint32_t cmd_look_game;
    uint32_t cmd_look_off;
    uint8_t  cmd_look[64];
    uint8_t  cmd_look_mask[64];
    // v10: the next ADD_BOT's clan tag (bot_profiles.ini), shown in MCC's post-game report (native/report.cpp)
    wchar_t  cmd_clan[8];
    // v11: its MCC emblem (brain/emblems.py): foreground, background, primary, secondary and background colour ids,
    // what MCC shows for it in every game's scoreboard and the post-game report; foreground 0 = none sent
    uint16_t cmd_emblem[5];
    // v12: where a bot respawns, by player index (Reach Invasion: the spawns a person is offered, brain/invasion.py
    // honor_spawn): x, y, z, yaw, written by the brain while the bot is dead, then spawn_seq bumped; botcore moves
    // the bot's next unit there on its first tick (reach.cpp hr_spawn_moves) and copies spawn_seq to spawn_done. A NaN
    // x calls a sent target off (the bot stays where the game spawns it)
    float    spawn_at[HB_MAX_PLAYERS][4];
    uint32_t spawn_seq[HB_MAX_PLAYERS];
    uint32_t spawn_done[HB_MAX_PLAYERS];
    // v13: reserved (once a typed chat line; kept so the offsets after it stay put)
    uint32_t chat_seq;
    // v14: Halo 4's use target, by bot slot (the old chat line's 64 bytes, unused: the layout is the same size): the
    // handle of the object a bot's view rests on for a Use (a Dominion terminal), 0 = none. botcore writes it into the
    // action's interaction slots (h4f.cpp f_use_target), where a person's own action has it from the game's targeting
    union {
        char     chat[64];                       // (nothing reads it)
        uint32_t use_target[HB_MAX_PLAYERS];
    };
};
#pragma pack(pop)
static_assert(sizeof(hb_shared) == 3622, "shared memory layout changed: update tools/botctl.py (SIZE, OFF_*)");

// v12: is a bot's unit moved to its spawn target this tick (reach.cpp hr_spawn_moves; tests/test_spawn_take.cpp)? A new
// unit (not none, not last tick's) with a target sent since the last one taken. While the bot is dead the target waits;
// while it lives whatever is sent is dropped (done = seq), so only a target sent while it was dead moves it
static inline bool hb_spawn_take(uint32_t unit, uint32_t* last_unit, uint32_t seq, uint32_t* done) {
    bool fresh = unit != 0xFFFFFFFFu && unit != *last_unit;
    *last_unit = unit;
    if (unit == 0xFFFFFFFFu) return false;
    bool take = fresh && seq != *done;
    *done = seq;
    return take;
}


static const uint32_t HB_LOOK_ROLL = 0x100;

// apply the decoded buttons of this game to a bot's action (size bytes); prev = its buttons last tick v9: the
// next ADD_BOT's own look over the configuration (or network record) it is made from, when the brain sent one
// for this game; consumed by the add (the next bot has its own) every copy of the player's player identifier in
// a bot's configuration becomes the bot's own, at any alignment (live: Halo 3 keeps a second copy at +0x78, and
// MCC's post-game report named every bot after the player)
static inline void hb_replace_id(uint8_t* cfg, size_t size, uint64_t person, uint64_t bot) {
    if (!person || person == bot) return;
    for (size_t k = 0; k + 8 <= size; k++)
        if (!memcmp(cfg + k, &person, 8)) { memcpy(cfg + k, &bot, 8); k += 7; }
}

static inline void hb_apply_look(hb_shared* m, uint32_t game, uint8_t* cfg, size_t size) {
    if (m->cmd_look_game == game)
        for (uint32_t k = 0; k < 64; k++)
            if (m->cmd_look_mask[k] && m->cmd_look_off + k < size) cfg[m->cmd_look_off + k] = m->cmd_look[k];
    m->cmd_look_game = 0;
}

static inline void hb_apply_buttons(const hb_shared* m, uint32_t game, uint8_t* act, size_t size,
                                    uint32_t now, uint32_t prev) {
    for (int k = 0; k < HB_BTN_MAP; k++) {
        const hb_btn_entry& e = m->btn_map[k];
        if (!e.btn || e.game != game || (size_t)e.off + 8 > size) continue;
        uint64_t* w = (uint64_t*)(act + e.off);
        *w &= ~e.mask;
        if ((now & e.btn) && !(e.press_only && (prev & e.btn))) *w |= e.mask;
    }
}

// The scope level a bot wants as the action's zoom field holds it: -1 none, 0 first level, 1 second. An
// absolute level in CE, Halo 2 and Halo 3: CE's unit control writes its +8 into the unit's +0x301 every tick
// (ghidra/ce_uc.c), Halo 2's network decoder reads "zoom-level" - 1 into +0x2A (ghidra/h2_actdec.c), Halo 3's
// input copies +0x4A into the unit control, which writes it to the unit's +0x299 (ghidra/h3_input.c).
static inline int16_t hb_zoom_index(uint32_t buttons) {
    uint32_t z = (buttons >> HB_ZOOM_SHIFT) & 3;
    return (int16_t)(z == 0 ? -1 : z == 1 ? 0 : 1);      // 3 is never sent: the second level at most
}

// Writes it at the game's zoom field (CE's 0x30-byte action +0x1C, Halo 2 +0x2A, Halo 3 +0x4A). False, nothing
// written, for a game whose field is not decoded yet (Reach, ODST, H2A, Halo 4) or a buffer too short.
static inline bool hb_set_zoom(uint32_t game, uint8_t* act, size_t size, uint32_t buttons) {
    size_t off = game == 1 ? 0x1C : game == 2 ? 0x2A : game == 3 ? 0x4A : 0;
    if (!off || off + 2 > size) return false;
    *(int16_t*)(act + off) = hb_zoom_index(buttons);
    return true;
}
