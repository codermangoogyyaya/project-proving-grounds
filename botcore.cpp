// botcore.dll: runs inside MCC (anti-cheat off only).
// Stage 1: hooks Halo CE's game tick (game thread) and runs commands from the brain there.
// ADD_BOT calls the game's own player_new for a fake machine.
// Stage 2: hooks the host's action dequeue (halo1+0xB66AF8). It fills one 0x30-byte action per
// player, in players-array order, from the "update client queues" array (local players only),
// so a bot's slot is empty. After it runs we write each bot's action into that bot's slot.
// Caller: players_update_before_game (host path, halo1+0xAD0720).
// Addresses are for MCC 1.3528 halo1.dll (docs/CE-03-live-game-state.md).
#include <windows.h>
#include <tlhelp32.h>
#include <stdio.h>
#include <share.h>
#include <string.h>
#include <wchar.h>
#include <initializer_list>
#include "shared.h"
#include "bots_panel.h"
#include "third_party/minhook/include/MinHook.h"

void bot_service_tag(const wchar_t* name, wchar_t tag[4]);   // report.cpp
void report_note_bot(int index, const wchar_t* name, const wchar_t* tag, int team, const wchar_t* clan,
                     const uint16_t* emblem);                 // report.cpp

hb_shared* g_shm;            // shared with overlay.cpp
static FILE* g_log;
static uint8_t* g_h1;          // halo1.dll base

// halo1.dll RVAs (1.3528)
static const uintptr_t RVA_GAME_TICK = 0xB6667C;     // increments the tick counter each game tick
static const uintptr_t RVA_PLAYER_NEW = 0xACFCA0;    // player_new variant without the join loop
static const uintptr_t RVA_PLAYERS_PTR = 0x1C40480;  // -> "players" data array header
static const uintptr_t RVA_DEQUEUE = 0xB66AF8;       // host: (actions[16] x 0x30, acks[16] x 0x10) -> bool
// unit -> local player index (0-3) or -1. The weapon update (FUN_180b78de8) asks it before making
// bullets for client-predicted triggers: a non-local player's bullets come from its own machine as
// a network message, so a bot's pistol / rifle spent ammo and made nothing. At these two calls
// (host and client branch; returns +0xB79689 / +0xB79707) a bot counts as local.
static const uintptr_t RVA_UNIT_LOCAL_PLAYER = 0xAE48C8;
static const uintptr_t RET_FIRE_HOST = 0xB79689, RET_FIRE_CLIENT = 0xB79707;

typedef void(__fastcall* tick_fn)(void*, void*, void*, void*);
typedef uint64_t(__fastcall* player_new_fn)(int machine, int player_index, short local_index, wchar_t* np);
typedef uint64_t(__fastcall* dequeue_fn)(uint8_t* actions, uint8_t* acks);
static tick_fn g_tick_orig;
static dequeue_fn g_dequeue_orig;

// Start of MCC's 0x30-byte player action (matches Xbox Halo CE's player_action). We only write
// these fields; weapon/grenade/zoom (+0x19..) keep what the game put there.
#pragma pack(push, 1)
struct mcc_action_head {
    uint32_t control_flags;              // unit control bits (units.h)
    float yaw, pitch;                    // desired facing, radians
    float throttle_forward, throttle_left;
    float primary_trigger;
    uint8_t pad18;
    int8_t weapon;                       // desired weapon slot, -1 = keep (an unset slot here kept
    int16_t grenade, zoom, pad1e;        // forcing slot 0: a bot could never hold the flag)
};
#pragma pack(pop)
static_assert(offsetof(mcc_action_head, zoom) == 0x1C, "shared.h hb_set_zoom writes CE's zoom at +0x1C");
static const size_t ACTION_STRIDE = 0x30;
enum { UC_CROUCH = 1 << 0, UC_JUMP = 1 << 1, UC_ACTION = 1 << 6, UC_MELEE = 1 << 7,
       UC_RELOAD = 1 << 10, UC_FIRE = 1 << 11, UC_GRENADE = 1 << 13, UC_SWAP = 1 << 14,
       UC_PRESS_ONLY = 0x4D0 };   // the game fires these on the press, not while held

void logf(const char* fmt, ...) {
    va_list a; va_start(a, fmt);
    if (g_log) { vfprintf(g_log, fmt, a); fputc('\n', g_log); fflush(g_log); }
    if (g_shm) { va_list b; va_start(b, fmt); vsnprintf(g_shm->status, sizeof g_shm->status, fmt, b); va_end(b); }
    va_end(a);
}

static bool anticheat_running() {
    HANDLE snap = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
    PROCESSENTRY32W e{ sizeof e };
    bool found = false;
    for (BOOL ok = Process32FirstW(snap, &e); ok; ok = Process32NextW(snap, &e))
        if (!_wcsnicmp(e.szExeFile, L"EasyAntiCheat", 13)) found = true;
    CloseHandle(snap);
    return found;
}

static uint8_t* player_entry(int index) {
    uint8_t* hdr = *(uint8_t**)(g_h1 + RVA_PLAYERS_PTR);
    if (!hdr) return nullptr;
    int32_t rel = *(int32_t*)(hdr + 0x34);
    uint16_t size = *(uint16_t*)(hdr + 0x22);
    return hdr + rel + (uintptr_t)index * size;
}

// a Halo CE player's numbers for MCC's post-game rows (report.cpp): u16 +0x106 kills, +0x6F4 assists, +0x6FE deaths,
// +0x700 suicides (decoded live, Battle Creek Slayer, 160 s: deaths matched every player's observed deaths, every
// kill counted the moment another player died, the one suicide at +0x700; assists sit as on Halo PC, deaths - 0xA)
// and the variant's engine (halo1 +0x2E9F770 + 0x30: 2 Slayer). False without the player
bool ce_player_numbers(int index, int out[5]) {
    __try {
        uint8_t* p = g_h1 ? player_entry(index) : nullptr;
        if (!p || !*(uint16_t*)p) return false;
        out[0] = *(uint16_t*)(p + 0x106);
        out[1] = *(uint16_t*)(p + 0x6F4);
        out[2] = *(uint16_t*)(p + 0x6FE);
        out[3] = *(uint16_t*)(p + 0x700);
        out[4] = *(int32_t*)(g_h1 + 0x2E9F770 + 0x30);
        return true;
    } __except (EXCEPTION_EXECUTE_HANDLER) { return false; }
}

static bool player_live(int index) {
    uint8_t* p = (index >= 0 && index < HB_MAX_PLAYERS) ? player_entry(index) : nullptr;
    return p && *(uint16_t*)p != 0;     // salt 0 = free slot
}

static int free_bot_slot() {
    for (int i = 0; i < HB_MAX_PLAYERS; i++) if (g_shm->bot_player_index[i] < 0) return i;
    return -1;
}

static int64_t adopt(int index) {
    int slot = free_bot_slot();
    if (slot < 0 || !player_live(index)) return -1;
    g_shm->actions[slot].seq = 0;
    g_shm->bot_player_index[slot] = index;
    return slot;
}

static uint32_t g_prev_flags[HB_MAX_PLAYERS];

// Write each driven bot's action into its slot. Slots follow live players in array order.
static void fill_bot_actions(uint8_t* actions) {
    int seq = 0;
    for (int index = 0; index < HB_MAX_PLAYERS && seq < HB_MAX_PLAYERS; index++) {
        if (!player_live(index)) continue;
        int slot = -1;
        for (int i = 0; i < HB_MAX_PLAYERS; i++) if (g_shm->bot_player_index[i] == index) slot = i;
        const hb_action* a = slot >= 0 ? &g_shm->actions[slot] : nullptr;
        if (a && a->seq) {
            mcc_action_head m{};
            m.yaw = a->yaw; m.pitch = a->pitch;
            m.throttle_forward = a->forward; m.throttle_left = a->strafe;    // +1 = left, as the game and brain
            uint32_t b = a->buttons, f = 0;
            if (b & HB_BTN_FIRE) { f |= UC_FIRE; m.primary_trigger = 1.0f; }
            if (b & HB_BTN_JUMP) f |= UC_JUMP;
            if (b & HB_BTN_CROUCH) f |= UC_CROUCH;
            if (b & HB_BTN_GRENADE) f |= UC_GRENADE;
            if (b & HB_BTN_MELEE) f |= UC_MELEE;
            if (b & HB_BTN_ACTION) f |= UC_ACTION | UC_SWAP;   // held action: press + "swap weapons" (hold E)
            if (b & HB_BTN_RELOAD) f |= UC_RELOAD;
            m.control_flags = f & ~(g_prev_flags[slot] & UC_PRESS_ONLY);
            m.weapon = (int8_t)(((b >> HB_WEAPON_SHIFT) & 7) - 1);
            m.grenade = -1;
            hb_set_zoom(1, (uint8_t*)&m, sizeof m, b);         // the scope level it wants (Action.zoom; -1 none)
            g_prev_flags[slot] = f;
            memcpy(actions + seq * ACTION_STRIDE, &m, sizeof m);
        }
        seq++;
    }
}

static uint64_t __fastcall dequeue_hook(uint8_t* actions, uint8_t* acks) {
    uint64_t ok = g_dequeue_orig(actions, acks);
    if ((ok & 0xFF) && g_shm) {
        __try { fill_bot_actions(actions); } __except (EXCEPTION_EXECUTE_HANDLER) { g_shm->cmd_fault = 2; }
    }
    return ok;
}

// Live bots' unit handles (0 = none), refreshed every tick for the stub below.
static volatile int32_t g_bot_units[HB_MAX_PLAYERS];
static void* g_unit_local_orig;
static uint64_t* g_stub_orig_slot;

// Detour for unit -> local player index: at the two bullet calls a bot's unit answers 0 (local).
// Machine code, not C++: the caller was compiled knowing the original only changes rax, rdx and
// r8 (it keeps r9 = 0 across the call and passes it on), so the detour may change nothing else.
// ponytail: a bot's biped only; a bot gunning a vehicle turret still makes no turret bullets
static void* make_unit_local_stub() {
    uint8_t* s = (uint8_t*)VirtualAlloc(nullptr, 128, MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE);
    if (!s) return nullptr;
    uint8_t* p = s;
    auto b = [&](std::initializer_list<uint8_t> v) { for (uint8_t x : v) *p++ = x; };
    auto q = [&](uint64_t v) { memcpy(p, &v, 8); p += 8; };
    b({ 0x48, 0x8B, 0x04, 0x24 });                              // mov rax, [rsp]   (return address)
    b({ 0x48, 0xBA }); q((uint64_t)(g_h1 + RET_FIRE_HOST));      // mov rdx, imm64
    b({ 0x48, 0x39, 0xD0, 0x74 }); uint8_t* to_check = p++;     // cmp rax, rdx; je check
    b({ 0x48, 0xBA }); q((uint64_t)(g_h1 + RET_FIRE_CLIENT));
    b({ 0x48, 0x39, 0xD0, 0x75 }); uint8_t* to_orig = p++;      // cmp rax, rdx; jne orig
    *to_check = (uint8_t)(p - to_check - 1);
    b({ 0x48, 0xB8 }); q((uint64_t)g_bot_units);                // check: mov rax, g_bot_units
    b({ 0x31, 0xD2 });                                          // xor edx, edx
    uint8_t* loop = p;
    b({ 0x39, 0x0C, 0x90, 0x74 }); uint8_t* to_bot = p++;       // cmp [rax+rdx*4], ecx; je bot
    b({ 0xFF, 0xC2, 0x83, 0xFA, HB_MAX_PLAYERS, 0x7C });        // inc edx; cmp edx, N; jl loop
    *p = (uint8_t)(loop - (p + 1)); p++;
    *to_orig = (uint8_t)(p - to_orig - 1);
    b({ 0x48, 0xB8 }); g_stub_orig_slot = (uint64_t*)p; q(0);   // orig: mov rax, trampoline
    b({ 0xFF, 0xE0 });                                          // jmp rax
    *to_bot = (uint8_t)(p - to_bot - 1);
    b({ 0x31, 0xC0, 0xC3 });                                    // bot: xor eax, eax; ret
    return s;
}

static void update_bot_units() {
    for (int i = 0; i < HB_MAX_PLAYERS; i++) {
        int index = g_shm->bot_player_index[i];
        int32_t u = (index >= 0 && player_live(index)) ? *(int32_t*)(player_entry(index) + 0x64) : 0;
        g_bot_units[i] = u == -1 ? 0 : u;
    }
}

static const uintptr_t P_TEAM = 0x4C;     // player_new writes 1; the lobby sets the host's

static int64_t set_team(int index, int team) {
    if (!player_live(index) || team < 0 || team > 15) return -1;
    *(int32_t*)(player_entry(index) + P_TEAM) = team;
    return team;
}

static int64_t add_bot(int arg) {
    int machine = arg & 0xFF, team = (arg >> 8) & 0xFF;
    uint8_t* me = player_entry(0);
    if (!me) return -2;
    wchar_t np[0x26];                       // 0x4C bytes: clone the local player's network record
    memcpy(np, me + 0x84, sizeof np);
    wmemset(np, 0, 16);
    int slot = free_bot_slot();
    if (slot < 0) return -3;
    if (g_shm->cmd_name[0]) { wcsncpy_s(np, 16, g_shm->cmd_name, 15); g_shm->cmd_name[0] = 0; }
    else swprintf_s(np, 16, L"Bot %d", slot + 1);
    wchar_t name[16];
    wcsncpy_s(name, 16, np, 15);
    // its own service tag (record +0x20, 4 characters; before: every bot showed the host's)
    wchar_t tag[4];
    bot_service_tag(name, tag);
    memcpy((uint8_t*)np + 0x20, tag, 8);
    hb_apply_look(g_shm, 1, (uint8_t*)np, sizeof np);   // its own colour (brain/looks.py)
    auto pn = (player_new_fn)(g_h1 + RVA_PLAYER_NEW);
    uint64_t h = pn(machine, -1, -1, np);
    if ((uint32_t)h != 0xFFFFFFFFu) {
        int index = (int)(h & 0xFFFF);
        // what MCC shows for it (name, tags, emblem: report.cpp); CE makes the player first, so the id follows at once
        report_note_bot(index, name, tag, team != 0xFF ? team : -1, g_shm->cmd_clan, g_shm->cmd_emblem);
        if (uint8_t* p = player_entry(index)) *(uint64_t*)(p + 0xC0) = 0xB07B07B000000000ull | (uint64_t)index;
        adopt(index);
        if (team != 0xFF) set_team(index, team);
    }
    return (int64_t)h;
}

static void run_command() {
    if (!g_shm || g_shm->cmd_seq == g_shm->cmd_done_seq) return;
    g_shm->cmd_fault = 0;
    __try {
        if (g_shm->cmd == HB_CMD_ADD_BOT) g_shm->cmd_result = add_bot(g_shm->cmd_arg);
        else if (g_shm->cmd == HB_CMD_ADOPT) g_shm->cmd_result = adopt(g_shm->cmd_arg);
        else if (g_shm->cmd == HB_CMD_SET_TEAM) g_shm->cmd_result = set_team(g_shm->cmd_arg & 0xFF, (g_shm->cmd_arg >> 8) & 0xFF);
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        g_shm->cmd_fault = 1;
        g_shm->cmd_result = -1;
    }
    g_shm->cmd_done_seq = g_shm->cmd_seq;
}

// a CE bot's own player id at player +0xC0, where the game copies the player's: MCC names a player by it
static void ce_own_ids() {
    for (int i = 0; i < HB_MAX_PLAYERS; i++) {
        int index = g_shm->bot_player_index[i];
        uint8_t* p = index > 0 ? player_entry(index) : nullptr;
        uint64_t id = 0xB07B07B000000000ull | (uint64_t)index;
        if (p && *(uint16_t*)p && *(uint64_t*)(p + 0xC0) != id) *(uint64_t*)(p + 0xC0) = id;
    }
}

static void __fastcall tick_hook(void* a, void* b, void* c, void* d) {
    g_tick_orig(a, b, c, d);
    if (!g_shm) return;
    g_shm->game_tick++;
    g_shm->game = 1;                          // Halo CE is the game being simulated
    run_command();
    update_bot_units();
    ce_own_ids();
}

static uint8_t g_patch[5];   // the jump our hook wrote: another loaded botcore's jump is not ours

static void hook(uint8_t* h1) {
    if (g_h1) {                               // the old copy is gone: drop its hooks and bots
        MH_RemoveHook(g_h1 + RVA_GAME_TICK);
        MH_RemoveHook(g_h1 + RVA_DEQUEUE);
        MH_RemoveHook(g_h1 + RVA_UNIT_LOCAL_PLAYER);
        for (int i = 0; i < HB_MAX_PLAYERS; i++) g_shm->bot_player_index[i] = -1;
    }
    g_h1 = h1;
    void* stub = make_unit_local_stub();
    if (!stub ||
        MH_CreateHook(g_h1 + RVA_GAME_TICK, (void*)&tick_hook, (void**)&g_tick_orig) != MH_OK ||
        MH_CreateHook(g_h1 + RVA_DEQUEUE, (void*)&dequeue_hook, (void**)&g_dequeue_orig) != MH_OK ||
        MH_CreateHook(g_h1 + RVA_UNIT_LOCAL_PLAYER, stub, &g_unit_local_orig) != MH_OK ||
        !(*g_stub_orig_slot = (uint64_t)g_unit_local_orig) ||
        MH_EnableHook(MH_ALL_HOOKS) != MH_OK) {
        logf("hooks failed at halo1 %p", g_h1);
    } else {
        memcpy(g_patch, g_h1 + RVA_GAME_TICK, sizeof g_patch);
        logf("hooks on at halo1 %p: tick +0x%llx, dequeue +0x%llx", g_h1,
             (unsigned long long)RVA_GAME_TICK, (unsigned long long)RVA_DEQUEUE);
    }
}

// --- Bots tab settings (botcore.ini next to the DLL) and the brain process
static wchar_t g_dir[MAX_PATH];          // folder of this DLL (native\bin)
static HANDLE g_brain;
bool overlay_start();
void h2_watch();
void h3_watch();
void reach_watch();
void report_hooks();
void odst_watch();
void h4f_watch();
void spawnhook_watch();
int mcc_menu_state();
static int g_last_menu = -1;
static DWORD g_brain_due;            // match started: start the brain when the game has bots

static void ini_path(wchar_t* out) { swprintf_s(out, MAX_PATH, L"%sbotcore.ini", g_dir); }

hb_roster g_roster;                  // the BOTS panel's list: exactly the bots of the next match (bots_panel.h)
hb_roster g_ff_roster;               // Firefight's list (botcore.ini ffbot=): Reach, up to 7 bots on red and blue as in a custom
                                     // game; ODST takes its first 3 (play.py), all on your team
static const int FF_ROSTER_MAX = 7;  // (8 players with you, Reach's Firefight)
uint32_t g_prejoin = 1;              // H2A, Halo 4: a match's first tick waits for the bots (native/h4f.cpp f_prejoin; botcore.ini prejoin=0: off)
uint32_t g_collector = 0;            // the brain records the player for the Observer (collector=; tools/collector.py)
uint32_t g_scale_games = 5;         // Scaling difficulty: the player's last games averaged (scale_games=)
uint32_t g_sounds = 60;              // the BOTS panel's sound volume, 0-100, 0 off (sounds=; overlay.cpp play_sound)
uint32_t g_playback = 0;             // a Theater film is running (set by the games' hooks): no brain, no bots (film.cpp)
void film_old_dir(wchar_t* out) {    // where this copy kept them before: <project>\logs\films (film.cpp still reads it)
    swprintf_s(out, MAX_PATH, L"%s..\\..\\logs\\films", g_dir);
}
// where film.cpp keeps each match's bots: %LOCALAPPDATA%\ProjectProvingGrounds\films (made if missing). One folder for
// every copy of the mod, like MCC's own films (per user): a film recorded with one copy (the working repo) plays with
// the bots in another (the install). Each copy's own logs\films had a Reach film play with no bots.
void film_dir(wchar_t* out) {
    wchar_t base[MAX_PATH];
    DWORD n = GetEnvironmentVariableW(L"LOCALAPPDATA", base, MAX_PATH);
    if (!n || n >= MAX_PATH) { film_old_dir(out); CreateDirectoryW(out, nullptr); return; }
    swprintf_s(out, MAX_PATH, L"%s\\ProjectProvingGrounds", base);
    CreateDirectoryW(out, nullptr);
    wcscat_s(out, MAX_PATH, L"\\films");
    CreateDirectoryW(out, nullptr);
}
void bug_reports_dir(wchar_t* out) { // the player's bug reports (overlay.cpp): <project>\logs\bug_reports
    swprintf_s(out, MAX_PATH, L"%s..\\..\\logs\\bug_reports", g_dir);
    CreateDirectoryW(out, nullptr);
}

void save_settings() {
    g_shm->red_players = roster_on_team(g_roster, 0);      // (older readers: botctl.settings())
    g_shm->blue_players = roster_on_team(g_roster, 1);
    wchar_t p[MAX_PATH]; ini_path(p);
    FILE* f = _wfopen(p, L"w");
    if (!f) return;
    fprintf(f, "enabled=%u\nred=%u\nblue=%u\ndifficulty=%u\nlidar=%u\nlidar_players=%u\ncollector=%u\n"
            "sounds=%u\nprejoin=%u\nscale_games=%u\n", g_shm->bots_enabled, g_shm->red_players, g_shm->blue_players,
            g_shm->difficulty, g_shm->lidar_mode, g_shm->lidar_players, g_collector, g_sounds, g_prejoin,
            g_scale_games);                                                                           // (prejoin kept: a save
                                                                                                   // dropped it)
    roster_write(f, g_roster);
    roster_write(f, g_ff_roster, "ff");
    fclose(f);
}

// Firefight's own list, first made from the list's first 7 (Reach: 8 players with you, red and blue; ODST: 4 players, the
// first 3 and all on your team, play.py): the 16-player list is never cut to fit Firefight
static void ff_seed() {
    g_ff_roster.n = 0; g_ff_roster.teams = 2;
    for (int i = 0; i < g_roster.n && g_ff_roster.n < FF_ROSTER_MAX; i++) { g_ff_roster.bot[g_ff_roster.n] = g_roster.bot[i]; g_ff_roster.bot[g_ff_roster.n++].team &= 1; }
}

static void load_settings() {
    g_shm->bots_enabled = 1; g_shm->red_players = 4; g_shm->blue_players = 4; g_shm->difficulty = 4;
    g_shm->lidar_mode = 0; g_shm->lidar_players = HB_MAX_PLAYERS;
    g_roster.n = 0; g_roster.teams = 2;
    g_ff_roster.n = 0;
    bool listed = false, ff_listed = false;                 // written by a build with the bot list (Firefight's)
    wchar_t p[MAX_PATH]; ini_path(p);
    FILE* f = _wfopen(p, L"r");
    if (f) {
        char line[128];
        while (fgets(line, sizeof line, f)) {
            unsigned v;
            if (!strncmp(line, "roster=", 7)) listed = true;
            if (!strncmp(line, "ffroster=", 9)) { ff_listed = true; continue; }
            if (!strncmp(line, "ffbot=", 6)) { if (g_ff_roster.n < FF_ROSTER_MAX) roster_parse_line(line + 2, g_ff_roster); continue; }
            if (roster_parse_line(line, g_roster)) continue;
            if (sscanf_s(line, "enabled=%u", &v) == 1) g_shm->bots_enabled = v != 0;
            else if (sscanf_s(line, "red=%u", &v) == 1) g_shm->red_players = v > 8 ? 8 : v;
            else if (sscanf_s(line, "blue=%u", &v) == 1) g_shm->blue_players = v > 8 ? 8 : v;
            else if (sscanf_s(line, "difficulty=%u", &v) == 1) g_shm->difficulty = v % 6;
            else if (sscanf_s(line, "lidar_players=%u", &v) == 1) g_shm->lidar_players = v < 2 ? 2 : v > HB_MAX_PLAYERS ? HB_MAX_PLAYERS : v;
            else if (sscanf_s(line, "lidar=%u", &v) == 1) g_shm->lidar_mode = v != 0;
            else if (sscanf_s(line, "prejoin=%u", &v) == 1) g_prejoin = v != 0;
            else if (sscanf_s(line, "collector=%u", &v) == 1) g_collector = v != 0;
            else if (sscanf_s(line, "sounds=%u", &v) == 1) g_sounds = v > 100 ? 100 : v;
            else if (sscanf_s(line, "scale_games=%u", &v) == 1) g_scale_games = v < 1 ? 1 : v > 20 ? 20 : v;
        }
        fclose(f);
    }
    if (!listed) {
        // settings from before the bot list: team sizes counted people too, so the bigger side keeps a
        // spot for you (8v8 = 7 red + 8 blue: 15 bots and you, the game's 16), at the saved difficulty
        int red = g_shm->red_players, blue = g_shm->blue_players;
        while (red + blue > HB_ROSTER_MAX) { if (red >= blue) red--; else blue--; }
        uint32_t s = GetTickCount() | 1;
        for (int k = 0; k < (red > blue ? red : blue); k++)
            for (int t = 0; t < 2; t++)
                if (k < (t ? blue : red)) {
                    int tier = g_shm->difficulty < 4 ? (int)g_shm->difficulty : g_shm->difficulty == 5 ? 4
                             : roster_mixed_tier(g_roster.n);
                    roster_add(g_roster, t, tier, HB_STYLE_ANY, s += 7919);
                }
        ff_seed(); ff_listed = true;
        save_settings();
        logf("bot list made from the old team sizes: %d red, %d blue", red, blue);
    }
    if (!ff_listed) ff_seed();
    for (int i = 0; i < g_ff_roster.n; i++) g_ff_roster.bot[i].team &= 1;      // (Firefight: red or blue, no more teams)
    g_ff_roster.teams = 2;
    // a list saved on Scaling before it was a bot's own difficulty (s149 and before kept Mixed's on the bots): Scaling
    // for every bot, once, so the list shows what plays
    bool scaled = false;
    for (int i = 0; i < g_roster.n; i++) scaled |= g_roster.bot[i].tier == 4;
    if (listed && g_shm->difficulty == 5 && !scaled && g_roster.n) {
        for (int i = 0; i < g_roster.n; i++) g_roster.bot[i].tier = 4;
        save_settings();
        logf("bot list: Scaling for all %d bots (saved before Scaling was a bot's own)", g_roster.n);
    }
}

static void stop_brain() {
    if (g_brain) { TerminateProcess(g_brain, 0); CloseHandle(g_brain); g_brain = nullptr; logf("brain stopped"); }
}

// A match started: run the brain, tools/play.py, which adds the bots.
static void start_brain() {
    stop_brain();
    for (int i = 0; i < HB_MAX_PLAYERS; i++) {   // bots belong to a match: forget the last match's
        g_shm->bot_player_index[i] = -1;
        g_shm->actions[i].seq = 0;
    }
    if (!g_shm->bots_enabled) return;
    // 5 Scaling: one skill percentage with no limit, set by the player's K/D after each game (brain/scaling.py)
    static const char* TIERS[6] = { "novice", "average", "strong", "top", "strong,average,top,novice", "scaling" };
    wchar_t root[MAX_PATH], cmd[2048], out[MAX_PATH];
    swprintf_s(root, L"%s..\\..", g_dir);
    wchar_t ini[MAX_PATH]; ini_path(ini);                    // the BOTS panel's list: these bots join
    wchar_t py[MAX_PATH + 2]; hb_python(root, py, MAX_PATH + 2);     // (an install's own Python, else PATH's)
    swprintf_s(cmd, L"%s -u -I \"%s\\tools\\play.py\" --red %u --blue %u --tier %S --minutes 600 --wait 120 --lidar %u --roster \"%s\"",
               py, root, g_shm->red_players, g_shm->blue_players, TIERS[g_shm->difficulty % 6],
               g_shm->lidar_mode ? g_shm->lidar_players : 0u, ini);
    swprintf_s(out, L"%s\\logs\\brain.txt", root);
    SECURITY_ATTRIBUTES sa{ sizeof sa, nullptr, TRUE };
    HANDLE log = CreateFileW(out, GENERIC_WRITE, FILE_SHARE_READ, &sa, CREATE_ALWAYS, 0, nullptr);
    STARTUPINFOW si{ sizeof si };
    si.dwFlags = STARTF_USESTDHANDLES;
    si.hStdOutput = si.hStdError = log;
    PROCESS_INFORMATION pi{};
    if (CreateProcessW(nullptr, cmd, nullptr, nullptr, TRUE, CREATE_NO_WINDOW, nullptr, root, &si, &pi)) {
        g_brain = pi.hProcess;
        CloseHandle(pi.hThread);
        logf("brain started (pid %lu)", pi.dwProcessId);
    } else {
        logf("brain failed to start (%lu): no python (an install has python\\python.exe; a developer copy needs python on PATH)", GetLastError());
    }
    if (log != INVALID_HANDLE_VALUE) CloseHandle(log);
}

static DWORD WINAPI main_thread(LPVOID self) {
    wchar_t path[MAX_PATH];
    GetModuleFileNameW((HMODULE)self, path, MAX_PATH);
    wcscpy_s(g_dir, path);
    *(wcsrchr(g_dir, L'\\') + 1) = 0;
    {   // this install's 15 bots for the BOTS panel's new names (tools/launch.py makes the file before injecting)
        wchar_t prof[MAX_PATH];
        swprintf_s(prof, L"%sbot_profiles.ini", g_dir);
        bots_panel_profiles(prof);
    }
    wcscpy_s(wcsrchr(path, L'.'), 8, L".log");   // botcore_<tag>.log next to the DLL
    g_log = _wfsopen(path, L"w", _SH_DENYNO);   // readable while the game runs

    if (anticheat_running()) {
        logf("anti-cheat is running: botcore stays off");
        FreeLibraryAndExitThread((HMODULE)self, 0);
    }
    HANDLE map = CreateFileMappingW(INVALID_HANDLE_VALUE, nullptr, PAGE_READWRITE, 0, sizeof(hb_shared), HB_SHM_NAME);
    g_shm = (hb_shared*)MapViewOfFile(map, FILE_MAP_ALL_ACCESS, 0, 0, sizeof(hb_shared));
    if (!g_shm) { logf("shared memory failed (%lu)", GetLastError()); return 1; }
    g_shm->magic = HB_MAGIC; g_shm->version = HB_VERSION;
    for (int i = 0; i < HB_MAX_PLAYERS; i++) g_shm->bot_player_index[i] = -1;
    g_shm->cmd_done_seq = g_shm->cmd_seq;

    // MCC unloads and reloads halo1.dll between matches (same address, fresh code): watch for
    // that and hook the new copy. Bots belong to a match, so the bot list is cleared too.
    load_settings();
    MH_Initialize();
    overlay_start();
    report_hooks();                           // what MCC shows for the bots: names, tags, emblems (native/report.cpp)
    for (;;) {
        g_shm->dll_alive++;
        uint8_t* h1 = (uint8_t*)GetModuleHandleW(L"halo1.dll");
        bool hooked = false;
        if (h1) {
            __try { hooked = h1 == g_h1 && !memcmp(h1 + RVA_GAME_TICK, g_patch, sizeof g_patch); }
            __except (EXCEPTION_EXECUTE_HANDLER) { hooked = false; }
        }
        if (h1 && !hooked) {
            Sleep(500);                       // let the loader finish
            if ((uint8_t*)GetModuleHandleW(L"halo1.dll") == h1) hook(h1);
        }
        h2_watch();                           // Halo 2 Classic (native/h2.cpp)
        h3_watch();                           // Halo 3 (native/h3.cpp)
        reach_watch();                        // Halo: Reach (native/reach.cpp)
        odst_watch();                         // Halo 3: ODST (native/odst.cpp)
        h4f_watch();                          // Halo 2 Anniversary, Halo 4 (native/h4f.cpp)
        spawnhook_watch();                    // Reach: bots respawn in Invasion (native/spawnhook.cpp)
        // the brain runs while a match is on: start it when the menus give way to a game (MCC
        // does not always reload halo1.dll for a new match), stop it back in the menus
        int menu = mcc_menu_state();
        if (menu != g_last_menu && menu >= 0) {
            if (menu == 1) { g_brain_due = GetTickCount(); g_shm->game = 0; g_playback = 0; }
            else if (menu == 0) { stop_brain(); g_brain_due = 0; }
            g_last_menu = menu;
        }
        // start the brain once a game tick with bot support is seen (a few seconds into the
        // match): 1 Halo CE, 2 Halo 2 Classic, 3 Halo 3, 4 Halo: Reach (play.py reads which one from the same field)
        if (g_brain_due && g_shm->game >= 1 && g_shm->game <= 7 && !g_playback) { start_brain(); g_brain_due = 0; }
        else if (g_brain_due && g_playback) g_brain_due = 0;     // a Theater film: its bots come from film.cpp
        if (g_playback && g_brain) stop_brain();
        else if (g_brain_due && GetTickCount() - g_brain_due > 60000) g_brain_due = 0;
        Sleep(h1 ? 33 : 100);
    }
}

BOOL APIENTRY DllMain(HMODULE mod, DWORD reason, LPVOID) {
    if (reason == DLL_PROCESS_ATTACH) {
        DisableThreadLibraryCalls(mod);
        CloseHandle(CreateThread(nullptr, 0, main_thread, mod, 0, nullptr));
    }
    return TRUE;
}
