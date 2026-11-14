// bots_panel.cpp: the BOTS panel over MCC's custom game lobby (see bots_panel.h).
//
// Design (docs/HANDOFF.md; the Master of UI/UX guide's loop):
//   tasks   set bots up fast (Quick setup: teams x bots per team x difficulty, confirmed with the count);
//           move bots between teams (drag a row onto a team, the inspector's team chips, Ctrl+Left/Right,
//           right-click > Move to); tune one bot (difficulty, play style, name); turn bots off; LiDAR.
//   layout  header (title, game, on/off) > toolbar (summary, roster actions) > team cards in a grid (up to
//           four a row) beside the inspector > footer (difficulty for everyone, limits, Done).
//   states  bots off, LiDAR on, empty, full (15 bots + you = the game's 16), a bot selected, dragging,
//           Halo: CE (two teams), eight teams, long names, confirmations before replacing or clearing.
//   style   MCC's dark blue panels, one accent, team colours only where they mean a team, Bahnschrift at
//           a fixed type scale (34 / 21 / 18 px at 1080p, nothing under 18), a 4-8-12-16-24 spacing scale.
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#undef small                                             // (rpcndr.h's: a ui::button parameter)
#include "bots_panel.h"
#include "model_view.h"
#include "ui_layout.h"
#include <ctype.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "third_party/imgui/imgui.h"
#include "third_party/imgui/imgui_internal.h"             // (SetKeyOwner: the 3D view keeps its arrow keys)

int hb_panel_firefight = 0;      // overlay.cpp: the lobby open is Firefight's (ODST: 4 players, you and 3 bots; Reach: 8)
static int g_panel_title = 0;    // the game open (bots_panel_env.title: 5 Reach, 8 ODST), set each frame by bots_panel
// Reach's Firefight is set up like a custom game: red and blue teams, up to 7 bots (8 players with you), and the Covenant
// AI are blue. ODST's keeps its one team and 3 bots.
static bool ff_one_team() { return hb_panel_firefight && g_panel_title != 5; }
static bool ff_two_teams() { return hb_panel_firefight && g_panel_title == 5; }
static int roster_cap() { return ff_one_team() ? 3 : hb_panel_firefight ? 7 : HB_ROSTER_MAX; }

// ------------------------------------------------------------------ tables
static const char* TEAM_NAME[HB_TEAMS_MAX] = { "Red", "Blue", "Green", "Orange", "Purple", "Gold", "Brown", "Pink" };
static const ImU32 TEAM_COL[HB_TEAMS_MAX] = {
    IM_COL32(229, 72, 60, 255), IM_COL32(61, 134, 230, 255), IM_COL32(72, 184, 92, 255), IM_COL32(240, 138, 44, 255),
    IM_COL32(154, 90, 214, 255), IM_COL32(229, 191, 60, 255), IM_COL32(154, 106, 60, 255), IM_COL32(229, 106, 174, 255) };
// 4 Scaling: a difficulty of its own, no rank
static const char* TIER_NAME[5] = { "Easy", "Normal", "Heroic", "Legendary", "Scaling" };
static const ImU32 SCALE_COL = IM_COL32(196, 206, 216, 255);   // neutral: no rank colour
static const ImU32 TIER_COL[5] = { IM_COL32(95, 224, 138, 255), IM_COL32(110, 200, 255, 255), IM_COL32(255, 154, 61, 255),
                                   IM_COL32(255, 91, 75, 255), SCALE_COL };
// brain/profiles.py TIERS, CRAFT and REACTION: what each level knows and does (Scaling: brain/scaling.py)
static const char* SCALE_ABOUT = "A difficulty of its own. Legendary's game sense, with reaction, aim and turn speed set "
                                 "by your K/D: after every game they rise or fall, without limit, until your games even "
                                 "out.";
static const char* TIER_ABOUT[5] = {
    "Plays for itself. Reacts a beat slower than a good player, never jumps, walks straight at you and fights in "
    "the open. No motion tracker, no teamwork. You should win most fights.",
    "Reacts like a good player. Strafes, jumps now and then, uses side routes and backs off from a losing fight. "
    "Watches the motion tracker and follows the team plan about half the time.",
    "Reacts faster than most players. Jump shots, jukes when hit, recharges behind cover, shoots with its team and "
    "knows when power weapons come back.",
    "Reacts about a third faster than a good player and uses everything: focus fire with the team, sneaky routes, "
    "constant awareness and every power weapon and respawn timed.",
    SCALE_ABOUT };
// brain/profiles.py PERSONALITIES order (rusher, camper, flanker, objector, spammer, grudger, sniper)
static const char* STYLE_KEY[8] = { "rusher", "camper", "flanker", "objector", "spammer", "grudger", "sniper", "any" };
static const char* STYLE_NAME[8] = { "Rusher", "Camper", "Flanker", "Objective", "Grenadier", "Grudge", "Sniper", "Any style" };
static const char* STYLE_ABOUT[8] = {
    "Pushes in and fights up close.", "Holds a spot and waits for you.", "Comes at you from the side.",
    "Plays the flag, ball or hill first.", "Throws a lot of grenades.", "Hunts whoever killed it last.",
    "Stays back at long range.", "The brain picks one each match." };
static const char* TITLE_NAME[9] = { "", "HALO: CE", "HALO 2", "HALO 3", "", "HALO: REACH", "HALO 2: ANNIVERSARY", "HALO 4",
                                     "HALO 3: ODST" };   // (8: ODST, Firefight only: overlay.cpp title_of)

static const char* TIER_KEY[5] = { "novice", "average", "strong", "top", "scaling" };
const char* team_name(int t) { return t >= 0 && t < HB_TEAMS_MAX ? TEAM_NAME[t] : "?"; }
const char* tier_name(int t) { return t >= 0 && t < 5 ? TIER_NAME[t] : "?"; }
const char* tier_key(int t) { return t >= 0 && t < 5 ? TIER_KEY[t] : "top"; }
const char* style_name(int s) { return s >= 0 && s < 8 ? STYLE_KEY[s] : "any"; }
int roster_max_teams(int title) { return ff_one_team() ? 1 : ff_two_teams() ? 2 : title == 1 ? 2 : HB_TEAMS_MAX; }   // (ODST Firefight: yours; Reach's: red and blue)

static int key_index(const char* s, const char* const* keys, int n, int fallback) {
    for (int i = 0; i < n; i++)
        if (_stricmp(s, keys[i]) == 0) return i;
    return fallback;
}

bool roster_parse_line(const char* line, hb_roster& r) {
    unsigned v;
    if (sscanf_s(line, "roster=%u", &v) == 1) return true;
    if (sscanf_s(line, "teams=%u", &v) == 1) { r.teams = v < 2 ? 2 : v > HB_TEAMS_MAX ? HB_TEAMS_MAX : (int)v; return true; }
    if (strncmp(line, "bot=", 4) != 0) return false;
    if (r.n >= HB_ROSTER_MAX) return true;
    char buf[96]; strcpy_s(buf, line + 4);
    char* ctx = nullptr;
    char* team = strtok_s(buf, ",", &ctx);
    char* tier = strtok_s(nullptr, ",", &ctx);
    char* style = strtok_s(nullptr, ",", &ctx);
    char* name = ctx;                                         // the rest of the line (a name may hold commas)
    if (!team || !tier || !style || !name) return true;
    for (char* c = name; *c; c++) if (*c == '\r' || *c == '\n') *c = 0;
    if (!*name) return true;
    hb_bot& b = r.bot[r.n++];
    int t = atoi(team);
    b.team = (uint8_t)(t < 0 ? 0 : t >= HB_TEAMS_MAX ? HB_TEAMS_MAX - 1 : t);
    b.tier = (uint8_t)key_index(tier, TIER_KEY, 5, 3);
    b.style = (uint8_t)key_index(style, STYLE_KEY, 8, HB_STYLE_ANY);
    strncpy_s(b.name, name, 15);
    if (b.team + 1 > r.teams) r.teams = b.team + 1;
    return true;
}

void roster_write(FILE* f, const hb_roster& r, const char* prefix) {
    fprintf(f, "%sroster=1\n", prefix);
    if (!*prefix) fprintf(f, "teams=%d\n", r.teams);
    for (int i = 0; i < r.n; i++)
        fprintf(f, "%sbot=%u,%s,%s,%s\n", prefix, r.bot[i].team, tier_key(r.bot[i].tier), style_name(r.bot[i].style), r.bot[i].name);
}

// ------------------------------------------------------------------ names (brain/names.py, same lists)
static const char* FIRST[] = { "Shadow", "Ghost", "Spartan", "Reaper", "Covert", "Silent", "Rogue", "Frost", "Iron", "Static", "Crimson",
    "Lone", "Rapid", "Hollow", "Grim", "Lucky", "Toxic", "Vapor", "Noble", "Echo", "Night", "Storm", "Pixel",
    "Nova", "Cosmic", "Lazy", "Salty", "Turbo", "Quiet", "Feral", "Dark", "Blood", "Lethal", "Insane", "Epic",
    "Mad", "Fatal", "Sick", "Legit", "Mythic", "Heroic", "Rampant", "Atomic", "Cyber", "Hyper", "Mega", "Ultra",
    "Phantom", "Spectre", "Brutal", "Savage", "Swift", "Zero", "Omega", "Alpha", "Delta", "Sierra", "Steel",
    "Chrome", "Venom", "Fury", "Havoc", "Rebel", "Outlaw", "Wicked", "Chief", "Major", "Agent", "Covie",
    "Plasma", "Sticky", "Frag", "Gravity", "Stealth", "Sneaky", "Bacon", "Elder", "Scarlet" };
static const char* SECOND[] = { "Wolf", "Sniper", "Hunter", "Falcon", "Viper", "Rider", "Blade", "Fox", "Owl", "Jackal", "Grunt", "Elite",
    "Brute", "Hawk", "Raven", "Tiger", "Panda", "Moose", "Badger", "Lynx", "Pilot", "Medic", "Ranger", "Bandit",
    "Nomad", "Drifter", "Warden", "Sentry", "Rocket", "Pistol", "Ninja", "Slayer", "Killa", "Gamer", "Assassin",
    "Dragon", "Demon", "Wraith", "Scorpion", "Mongoose", "Hornet", "Pelican", "Chopper", "Prowler", "Arbiter",
    "Prophet", "Marine", "Trooper", "Recon", "Scout", "Striker", "Gunner", "Sapper", "Warlord", "Legend", "Lord",
    "King", "Boss", "Beast", "Monkey", "Duck", "Taco", "Noob", "Fragger", "Camper", "Goat", "Bear", "Shark",
    "Cobra", "Skull", "Zealot", "Bomber", "Runner", "Carrier", "Sword", "Hammer", "Needler",
    "Magnum" };
static const char* WORDS[] = { "Plasmatic", "Overshield", "Needlerz", "Warthog", "Banshee", "Gulch", "Sidewinder", "Cortana", "Monitor",
    "Sentinel", "Flood", "Teabag", "Noscope", "Sticky", "Flagrunner", "Arbiter", "Pelican", "Scarab", "Mongoose",
    "Elephant", "Wraith", "Snipes", "Headshot", "Killtacular", "Overkill", "Killtrocity", "Killionaire",
    "Perfection", "Hogrider", "Rocketrider", "Swordlunge", "Doubleshot", "Quickscope", "Ragequit", "Partychat",
    "Gamerscore", "Redring", "Lagswitch", "Superjump", "Bridgecamp", "Lockout", "Guardian", "Valhalla",
    "Sandtrap", "Highground", "Zanzibar", "Ascension", "Headlong", "Chillout", "Midship", "Foundry", "Narrows",
    "Countdown", "Powerhouse", "Boardwalk", "Spartacus", "Grifball", "Juggernaut", "Oddball", "Bloodline",
    "Killjoy", "Pwnage", "Fragtastic", "Bubbleshield", "Spikegrenade" };
#define COUNT(a) (int)(sizeof(a) / sizeof((a)[0]))

static uint32_t rnd(uint32_t& s) { s ^= s << 13; s ^= s >> 17; s ^= s << 5; return s; }
static int rint(uint32_t& s, int n) { return (int)(rnd(s) % (uint32_t)n); }

static bool name_taken(const hb_roster& r, const char* n, int skip) {
    for (int i = 0; i < r.n; i++)
        if (i != skip && _stricmp(r.bot[i].name, n) == 0) return true;
    return false;
}

// this install's 15 bots (native\bin\bot_profiles.ini: brain/looks.py writes it on the first launch, the Settings
// tab's re-roll writes it again): new names come from them while any is free, so a bot keeps its name and look from
// match to match. Lines: experience=original|modern, effects=none|rare|common|frequent, bot=<name>,<seed>[,<clan tag>]
// A 4th field holds what the player set by hand in the bot editor: <game>.<field>=<value> pairs split by ';'
// (brain/looks.py overrides_of).
static char g_prof[32][16];
static char g_prof_clan[32][6];
static uint32_t g_prof_seed[32];
static char g_prof_over[32][256];
static int g_nprof = 0;
static int g_experience = 0;                             // 0 Original, 1 Modern
static int g_effects = 0;                                // how many bots wear an armor effect: EFFECTS_LEVEL
static wchar_t g_prof_path[260];                         // the file bots_panel_profiles read, where a re-roll writes
static const char* EFFECTS_LEVEL[4] = { "none", "rare", "common", "frequent" };   // brain/looks.py EFFECT_CHANCE
static int g_species = 1;                                // the bots' species: 0 Spartans only, 1 mixed, 2 Elites only
static int g_elite_share = -1;                           // mixed: the percent rolled Elite (-1: each game's own mix)
static const char* SPECIES_LEVEL[3] = { "spartans", "mixed", "elites" };   // brain/looks.py elite_share

int profiles_count() { return g_nprof; }
const char* profile_name(int i) { return i >= 0 && i < g_nprof ? g_prof[i] : ""; }
const char* profile_clan(int i) { return i >= 0 && i < g_nprof ? g_prof_clan[i] : ""; }
int profiles_experience() { return g_experience; }
int profiles_effects() { return g_effects; }
int profiles_species() { return g_species; }
int profiles_elite_share() { return g_elite_share; }

static int effects_level(const char* v) {                // a level name; the first files said 0 (off) or 1 (on)
    for (int k = 0; k < 4; k++) if (_stricmp(v, EFFECTS_LEVEL[k]) == 0) return k;
    return atoi(v) != 0 ? 2 : 0;
}

void bots_panel_profiles(const wchar_t* path) {
    FILE* f = nullptr;
    if (path != g_prof_path) wcsncpy_s(g_prof_path, path, _TRUNCATE);
    g_nprof = 0;
    g_experience = 0;
    g_effects = 0;
    g_species = 1;
    g_elite_share = -1;
    if (_wfopen_s(&f, path, L"r") != 0 || !f) return;
    char line[512];
    while (g_nprof < 32 && fgets(line, sizeof line, f)) {
        for (char* c = line; *c; c++) if (*c == '\r' || *c == '\n') *c = 0;
        if (strncmp(line, "experience=", 11) == 0) { g_experience = _stricmp(line + 11, "modern") == 0; continue; }
        if (strncmp(line, "effects=", 8) == 0) { g_effects = effects_level(line + 8); continue; }
        if (strncmp(line, "species=", 8) == 0) {
            for (int k = 0; k < 3; k++) if (_stricmp(line + 8, SPECIES_LEVEL[k]) == 0) g_species = k;
            continue;
        }
        if (strncmp(line, "elites=", 7) == 0) { g_elite_share = atoi(line + 7) > 100 ? 100 : atoi(line + 7); continue; }
        if (strncmp(line, "bot=", 4) != 0) continue;
        char* name = line + 4;
        char* comma = strchr(name, ',');                     // <name>,<seed>[,<clan>[,<overrides>]]: no commas in names
        if (!comma || comma - name < 1 || comma - name > 15) continue;
        int k = g_nprof;
        memcpy(g_prof[k], name, comma - name);
        g_prof[k][comma - name] = 0;
        g_prof_seed[k] = (uint32_t)strtoul(comma + 1, nullptr, 10);
        g_prof_clan[k][0] = 0;
        g_prof_over[k][0] = 0;
        if (char* clan = strchr(comma + 1, ',')) {
            char* over = strchr(clan + 1, ',');
            size_t n = over ? (size_t)(over - clan - 1) : strlen(clan + 1);
            strncpy_s(g_prof_clan[k], clan + 1, n < 4 ? n : 4);
            if (over) strncpy_s(g_prof_over[k], over + 1, _TRUNCATE);
        }
        g_nprof++;
    }
    fclose(f);
}

const char* profile_clan_of(const char* name) {
    for (int i = 0; i < g_nprof; i++) if (_stricmp(g_prof[i], name) == 0) return g_prof_clan[i];
    return "";
}

// the whole file again, as the panel holds it (the editor's changes); false if it could not be written
bool profiles_save() {
    FILE* f = nullptr;
    if (!g_prof_path[0] || _wfopen_s(&f, g_prof_path, L"w") != 0 || !f) return false;
    fprintf(f, "; this install's bots (the Settings tab, brain/looks.py): name, look seed, clan tag, looks set by hand\n");
    fprintf(f, "experience=%s\neffects=%s\n", g_experience ? "modern" : "original", EFFECTS_LEVEL[g_effects & 3]);
    fprintf(f, "species=%s\n", SPECIES_LEVEL[g_species >= 0 && g_species < 3 ? g_species : 1]);
    if (g_elite_share >= 0) fprintf(f, "elites=%d\n", g_elite_share);
    for (int i = 0; i < g_nprof; i++) {
        if (g_prof_over[i][0]) fprintf(f, "bot=%s,%u,%s,%s\n", g_prof[i], g_prof_seed[i], g_prof_clan[i], g_prof_over[i]);
        else fprintf(f, "bot=%s,%u,%s\n", g_prof[i], g_prof_seed[i], g_prof_clan[i]);
    }
    fclose(f);
    return true;
}

// one hand-set look value ("h3.primary"), or -1
int profile_over_get(int i, const char* key) {
    if (i < 0 || i >= g_nprof) return -1;
    size_t kl = strlen(key);
    for (const char* p = g_prof_over[i]; *p;) {
        const char* end = strchr(p, ';');
        if (!strncmp(p, key, kl) && p[kl] == '=') return atoi(p + kl + 1);
        if (!end) break;
        p = end + 1;
    }
    return -1;
}

// set (v >= 0) or clear (v < 0) one hand-set look value; every other pair kept in its order
void profile_over_set(int i, const char* key, int v) {
    if (i < 0 || i >= g_nprof) return;
    char out[256] = "";
    size_t kl = strlen(key);
    const char* p = g_prof_over[i];
    while (*p) {
        const char* end = strchr(p, ';');
        size_t n = end ? (size_t)(end - p) : strlen(p);
        if (n && !(n > kl && !strncmp(p, key, kl) && p[kl] == '=')) {
            if (out[0]) strcat_s(out, ";");
            strncat_s(out, p, n);
        }
        if (!end) break;
        p = end + 1;
    }
    if (v >= 0) {
        char kv[48]; sprintf_s(kv, "%s=%d", key, v);
        if (strlen(out) + strlen(kv) + 2 < sizeof out) { if (out[0]) strcat_s(out, ";"); strcat_s(out, kv); }
    }
    strcpy_s(g_prof_over[i], out);
}

// ------------------------------------------------------------------ the profile view's data each bot's look as
// rolled (native/bin/bot_looks.ini, brain/looks.py export_view, written by tools/looks_export.py): the view shows the
// result of the roll, never "Random". Lines: <name>|<game>|own|other|k=v... ("own": its preferred species, "other":
// the other)
struct look_line { char name[16]; char game[6]; bool other; char kv[640]; };   // (Reach / Halo 4 piece lists: ~500)
static look_line g_look[192];
static int g_nlook = 0;
static ULONGLONG g_look_mtime = 0, g_look_check = 0, g_look_asked = 0;
static unsigned long long (*g_make_texture)(int w, int h, const unsigned char* rgba) = nullptr;   // bots_panel_env

static ULONGLONG file_mtime(const wchar_t* p) {
    WIN32_FILE_ATTRIBUTE_DATA a;
    if (!GetFileAttributesExW(p, GetFileExInfoStandard, &a)) return 0;
    return (ULONGLONG)a.ftLastWriteTime.dwHighDateTime << 32 | a.ftLastWriteTime.dwLowDateTime;
}
// a file beside bot_profiles.ini (native\bin\...)
static void bin_file(wchar_t* out, size_t cap, const wchar_t* name) {
    wcsncpy_s(out, cap, g_prof_path, _TRUNCATE);
    wchar_t* s = wcsrchr(out, L'\\');
    if (s) s[1] = 0; else out[0] = 0;
    wcscat_s(out, cap, name);
}
// runs tools/looks_export.py for these profiles, in the background (the preview waits for it). The tools folder: two up
// from native\bin in an install; the preview's profiles live in temp, so there the preview exe's own repo
static void looks_refresh(bool wait) {
    if (!g_prof_path[0]) return;
    wchar_t root[260], view[260], cmd[1024];
    bin_file(root, 260, L"");
    bin_file(view, 260, L"bot_looks.ini");
    wchar_t* s = wcsstr(root, L"\\native\\bin\\");
    if (!s) {
        GetModuleFileNameW(nullptr, root, 260);
        s = wcsstr(root, L"\\native\\bin\\");
        if (!s) return;
    }
    *s = 0;
    wchar_t py[MAX_PATH + 2]; hb_python(root, py, MAX_PATH + 2);
    swprintf_s(cmd, L"%s -I \"%s\\tools\\looks_export.py\" \"%s\" \"%s\"", py, root, g_prof_path, view);
    STARTUPINFOW si{}; si.cb = sizeof si;
    PROCESS_INFORMATION pi{};
    if (CreateProcessW(nullptr, cmd, nullptr, nullptr, FALSE, CREATE_NO_WINDOW, nullptr, nullptr, &si, &pi)) {
        if (wait) WaitForSingleObject(pi.hProcess, 15000);
        CloseHandle(pi.hThread);
        CloseHandle(pi.hProcess);
    }
    g_look_asked = GetTickCount64();
}
// re-reads bot_looks.ini when it changed (twice a second at most; every frame for 3 s after asking for one, so a pick
// shows on the bot in the export's 0.35 s, not up to 0.85); asks for a fresh one when it is older than the profiles
// (made before an edit or a re-roll) or missing
static void looks_load() {
    ULONGLONG now = GetTickCount64();
    if (now - g_look_check < 500 && now - g_look_asked > 3000) return;
    g_look_check = now;
    wchar_t view[260];
    bin_file(view, 260, L"bot_looks.ini");
    ULONGLONG mt = file_mtime(view), pt = file_mtime(g_prof_path);
    if ((mt == 0 || mt < pt) && now - g_look_asked > 5000) looks_refresh(false);
    if (mt == 0 || mt == g_look_mtime) return;
    g_look_mtime = mt;
    FILE* f = nullptr;
    if (_wfopen_s(&f, view, L"r") != 0 || !f) return;
    g_nlook = 0;
    char line[800];
    while (g_nlook < COUNT(g_look) && fgets(line, sizeof line, f)) {
        if (line[0] == ';') continue;
        for (char* c = line; *c; c++) if (*c == '\r' || *c == '\n') *c = 0;
        char* a = strchr(line, '|'); if (!a) continue; *a++ = 0;
        char* b = strchr(a, '|'); if (!b) continue; *b++ = 0;
        char* c = strchr(b, '|'); if (!c) continue; *c++ = 0;
        look_line& L = g_look[g_nlook];
        strncpy_s(L.name, line, _TRUNCATE);
        strncpy_s(L.game, a, _TRUNCATE);
        L.other = strcmp(b, "other") == 0;
        strncpy_s(L.kv, c, _TRUNCATE);
        g_nlook++;
    }
    fclose(f);
}
// where a field's value starts in a bot's rolled look in a game (own or the other species), nullptr when not known
static const char* look_field(const char* name, const char* game, bool other, const char* field) {
    size_t fl = strlen(field);
    for (int i = 0; i < g_nlook; i++) {
        const look_line& L = g_look[i];
        if (L.other != other || strcmp(L.game, game) || _stricmp(L.name, name)) continue;
        for (const char* p = L.kv; *p;) {
            if (!strncmp(p, field, fl) && p[fl] == '=') return p + fl + 1;
            const char* e = strchr(p, ';');
            if (!e) break;
            p = e + 1;
        }
        return nullptr;
    }
    return nullptr;
}
// a field of a bot's rolled look in a game (own or the other species), -1 when not known
static int look_get(const char* name, const char* game, bool other, const char* field) {
    const char* v = look_field(name, game, other, field);
    return v ? atoi(v) : -1;
}
// a text field (Reach's "pieces": region:permutation,...), "" when not known
static int g_snd_frame = -10, g_snd_quiet = 0;          // (panel_sounds; also tells the panel just opened)
// Scaling difficulty's hover box (brain/scaling.py write_view: logs\scaling_view.txt, a block of numbers for each
// "games averaged" choice; tools/scaling_view.py brings it up to date, run as the panel opens)
static wchar_t g_scale_file[260];
static char g_scale_text[32768];
static ULONGLONG g_scale_mt, g_scale_check;
void hb_python(const wchar_t* root, wchar_t* out, size_t cap) {
    wchar_t exe[MAX_PATH];
    swprintf_s(exe, L"%s\\python\\python.exe", root);
    if (GetFileAttributesW(exe) != INVALID_FILE_ATTRIBUTES) swprintf_s(out, cap, L"\"%s\"", exe);
    else swprintf_s(out, cap, L"python");
}
static bool repo_root(wchar_t* root, size_t cap) {         // the repo: two up from native\bin (the preview: its exe's)
    bin_file(root, cap, L"");
    wchar_t* s = wcsstr(root, L"\\native\\bin\\");
    if (!s) {
        GetModuleFileNameW(nullptr, root, (DWORD)cap);
        s = wcsstr(root, L"\\native\\bin\\");
        if (!s) return false;
    }
    *s = 0;
    return true;
}
static void scale_refresh() {
    wchar_t root[260], cmd[1024];
    if (!repo_root(root, 260)) return;
    wchar_t sp[260]; bin_file(sp, 260, L"scaling.ini");     // (the Advanced tab's; the preview's in temp)
    wchar_t py[MAX_PATH + 2]; hb_python(root, py, MAX_PATH + 2);
    swprintf_s(cmd, L"%s -I \"%s\\tools\\scaling_view.py\" \"%s\\native\\bin\\botcore.ini\" \"%s\"", py, root, root, sp);
    STARTUPINFOW si{}; si.cb = sizeof si;
    PROCESS_INFORMATION pi{};
    if (CreateProcessW(nullptr, cmd, nullptr, nullptr, FALSE, CREATE_NO_WINDOW, nullptr, nullptr, &si, &pi)) {
        CloseHandle(pi.hThread);
        CloseHandle(pi.hProcess);
    }
}
static void scale_load() {                                 // re-read when the file changed, twice a second at most
    ULONGLONG now = GetTickCount64();
    if (now - g_scale_check < 500) return;
    g_scale_check = now;
    if (!g_scale_file[0]) {
        wchar_t root[260];
        if (!repo_root(root, 260)) return;
        swprintf_s(g_scale_file, L"%s\\logs\\scaling_view.txt", root);
    }
    ULONGLONG mt = file_mtime(g_scale_file);
    if (!mt || mt == g_scale_mt) return;
    FILE* f = nullptr;
    if (_wfopen_s(&f, g_scale_file, L"rb") != 0 || !f) return;
    size_t n = fread(g_scale_text, 1, sizeof g_scale_text - 1, f);
    fclose(f);
    g_scale_text[n] = 0;
    g_scale_mt = mt;
}
static void look_text(const char* name, const char* game, bool other, const char* field, char* out, size_t cap) {
    const char* v = look_field(name, game, other, field);
    size_t n = v ? strcspn(v, ";") : 0;
    if (n >= cap) n = cap - 1;
    memcpy(out, v ? v : "", n);
    out[n] = 0;
}
// the species a bot rolled in a game, before any hand-set one (Halo 2 / Reach "species", Halo 3 "model")
static int rolled_species(int game_index);

// pictures: native\bin\<file>.rgba (uint32 width, height, then RGBA), made a texture once
struct tex_entry { char key[64]; ImTextureID id; int w, h; };
static tex_entry g_tex[64];
static int g_ntex = 0;
static const tex_entry* texture(const char* key, const wchar_t* file) {
    for (int i = 0; i < g_ntex; i++) if (!strcmp(g_tex[i].key, key)) return g_tex[i].id ? &g_tex[i] : nullptr;
    if (g_ntex >= COUNT(g_tex)) return nullptr;
    tex_entry& t = g_tex[g_ntex++];                          // tried once: a missing file is not looked for every frame
    strncpy_s(t.key, key, _TRUNCATE);
    t.id = ImTextureID_Invalid; t.w = t.h = 0;
    wchar_t path[300];
    bin_file(path, 300, file);
    FILE* f = nullptr;
    if (_wfopen_s(&f, path, L"rb") != 0 || !f) {             // beside the program (the preview: its profiles are in temp)
        GetModuleFileNameW(nullptr, path, 300);
        if (wchar_t* sl = wcsrchr(path, L'\\')) sl[1] = 0;
        wcscat_s(path, file);
        if (_wfopen_s(&f, path, L"rb") != 0) f = nullptr;
    }
    if (!g_make_texture || !f) { if (f) fclose(f); return nullptr; }
    uint32_t wh[2] = { 0, 0 };
    if (fread(wh, 4, 2, f) == 2 && wh[0] && wh[1] && wh[0] <= 4096 && wh[1] <= 4096) {
        size_t n = (size_t)wh[0] * wh[1] * 4;
        unsigned char* px_ = (unsigned char*)malloc(n);
        if (px_ && fread(px_, 1, n, f) == n) { t.id = g_make_texture((int)wh[0], (int)wh[1], px_); t.w = wh[0]; t.h = wh[1]; }
        free(px_);
    }
    fclose(f);
    return t.id ? &t : nullptr;
}
// a picture taken again (native/bin/looks: a new look): drop the cached one so the next frame loads it
static void texture_forget(const char* prefix) {
    for (int i = 0; i < g_ntex; i++)
        if (!strncmp(g_tex[i].key, prefix, strlen(prefix))) { g_tex[i] = g_tex[--g_ntex]; i--; }
}

// a made-up gamertag (brain/names.py styles): first + second word, a word and a number, xXwordXx, a Halo word
static void made_name(char* n, size_t cap, uint32_t& s) {
    int style = rint(s, 100);
    if (style < 35) sprintf_s(n, cap, "%s%s", FIRST[rint(s, COUNT(FIRST))], SECOND[rint(s, COUNT(SECOND))]);
    else if (style < 60) {
        const int pick[5] = { 1 + rint(s, 99), 100 + rint(s, 900), 7, 117, 343 };
        int k = rint(s, COUNT(FIRST) + COUNT(SECOND));
        sprintf_s(n, cap, "%s%d", k < COUNT(FIRST) ? FIRST[k] : SECOND[k - COUNT(FIRST)], pick[rint(s, 5)]);
    } else if (style < 75) {
        int k = rint(s, COUNT(SECOND) + COUNT(WORDS));
        sprintf_s(n, cap, "xX%.9sXx", k < COUNT(SECOND) ? SECOND[k] : WORDS[k - COUNT(SECOND)]);
    } else if (style < 90) {
        static const char* SUF[6] = { "", "", "x", "TV", "HD", "_" };
        const char* suf = SUF[rint(s, 6)];
        if (suf[0] == '_') sprintf_s(n, cap, "%s_%d", WORDS[rint(s, COUNT(WORDS))], 1 + rint(s, 9));
        else sprintf_s(n, cap, "%s%s", WORDS[rint(s, COUNT(WORDS))], suf);
    } else {
        static const char* PRE[5] = { "The", "Mr", "Lil", "Big", "Sir" };
        sprintf_s(n, cap, "%s%s", PRE[rint(s, 5)], SECOND[rint(s, COUNT(SECOND))]);
    }
    n[15] = 0;
}

// clan tags, Halo and Xbox 360 era (4 characters at most): brain/looks.py CLAN_TAGS, the same list in the same order
static const char* CLAN_TAGS[] = {
    "ODST", "TBK", "EVO", "NV", "GG", "HAVK", "SPRT", "R3D", "UNSC", "OGs", "FFA", "PRO", "MLG", "ELTE", "GRNT",
    "SKL", "X7", "VX", "117", "BRT", "ONI", "NOBL", "SIX", "MJLR", "CORT", "ARBY", "JKAL", "HNTR", "FLOD", "SNTL",
    "BNSH", "WRTH", "HOG", "GHST", "PELI", "SCRB", "SPNK", "MNGS", "HORN", "ELPH", "MNTR", "343", "BNGE", "HCS", "RROD",
    "XBL", "LIVE", "GOLD", "360", "LAN", "SPLT", "BNET", "GS", "ACHV", "PWND", "N00B", "LAG", "TBAG", "NADE", "STKY",
    "SNPR", "SWAT", "CTF", "KOTH", "ODDB", "JUGG", "INFX", "VIP", "BTB", "REKT", "FRAG", "SWRD", "BR55", "DMR", "MA5",
    "M6D", "NDLR", "CARB", "EMP", "OVR", "CAMO", "BUBL", "LZR", "RKT", "SPKR", "PPST", "FUEL", "GRAV", "HLJM", "S3",
    "SPII", "POA", "INF", "NMPD", "BUCK", "DTCH", "RMEO", "ROOK", "MCKY", "SGT", "JHNS", "KAT", "EMIL", "JUN", "JRGE",
    "CRTR", "KEYS", "RTAS", "SANG", "KIG", "YANM", "COVN", "BRUT", "GRNY", "PROP", "FORR", "HALO", "ARK", "REQ", "BGLC",
    "LCKT", "GRDN", "PIT", "NRWS", "VLHL", "SNWB", "HMRG", "ZBAR", "IVRY", "MDTR", "LSTN", "HGND", "CNST", "SWMP", "OBRN",
    "WARL", "AFTR", "SWRM", "TOP", "ACE", "KOS", "DOA", "AIM", "HDSH", "NSCP", "QSCP", "WAVE", "DUAL", "BXR", "DBLK",
    "TRPL", "OVKL", "KLTX", "KLPC", "RAMP", "LEGD", "HERO", "RECN", "EOD", "CQB", "EVA", "SCUT", "MKV", "MKVI", "HYBS",
    "KTNA", "RGUE", "SCTY", "JFO", "GUNG", "PLSM", "SHDE", "WRTN", "PWN", "SKLZ", "LGND", "MYTH", "IRON", "KILL", "BOOM",
    "PEW", "LOL", "OMG", "LEET", "1337", "H4X", "UBER", "EPIC", "FAIL", "BRB", "AFK", "OP", "DLC", "RVB", "CHF",
    "ARB", "SPRK", "GLTY", "MJ", "BLUE", "RED", "ASLT", "TERR", "HDHT", "GRFB", "RICO", "DOMN", "SPAR", "FIRE", "ICE",
    "VOID", "APEX", "ZERO", "NOVA", "OMEN", "WOLF", "VPR", "HAWK", "RAVN", "GRIM", "REAP", "SHDW", "STLH", "RAID", "WAR",
    "SOS", "TEAM", "CLAN", "SQD", "ARMY", "NAVY", "MRNE", "HELO", "PLT", "RKTS", "SNPZ", "NDLZ", "BRS", "DMRS", "ARS",
    "SMG", "SPEC", "SPTN", "IV", "III", "II", "REB", "OUT", "XB1", "XBX", "PAD", "SKRT", "TKO", "KO", "HAX",
    "MOD", "GLHF", "GGWP", "EZ", "NOPE", "YOLO", "SWAG", "BRUH", "MLG4", "PROZ", "OGZ", "KNG", "QUEN", "ROYL", "DYN",
    "VNGD", "AXIS", "ALFA", "BRVO", "CHRL", "ECHO", "FXTR", "GOLF", "HTL", "INDA", "JLT", "KILO", "LIMA", "MIKE", "OSCR",
    "PAPA", "QBC", "ROMO", "SRRA", "TNGO", "UNI", "VCTR", "WSKY", "XRAY", "YNKE", "ZULU", "ODS", "SPRC", "CRTX", "FRGE",
    "THTR", "FILM", "SHOT", "CLIP", "MNTG", "TBG", "NADZ", "PLZ", "SRSL", "COOP", "LGDY", "SKUL", "IWHB", "GRUN", "TILT",
    "MYTC", "CTCH", "FAMN", "TOUG", "BLND", "BLCK", "COWB", "BLPF",
};

// a clan tag: a known tag 2 times in 3, else 2-4 capitals and digits, the way real tags look (X7, GG4)
static void made_clan(char* t, uint32_t& s) {
    if (rint(s, 3) < 2) { strcpy_s(t, 6, CLAN_TAGS[rint(s, COUNT(CLAN_TAGS))]); return; }
    static const char AZ[] = "ABCDEFGHJKLMNPQRSTUVWXYZ0123456789";
    int n = 2 + rint(s, 3);
    for (int i = 0; i < n; i++) t[i] = AZ[rint(s, (int)sizeof AZ - 1)];
    t[n] = 0;
}

bool profiles_reroll(const wchar_t* path, int experience, int effects, uint32_t seed, int species, int elite_share) {
    uint32_t s = seed | 1;
    char names[15][16];
    for (int i = 0; i < 15; i++) {
        for (int tries = 0; tries < 200; tries++) {
            made_name(names[i], sizeof names[i], s);
            bool dup = false;
            for (int k = 0; k < i; k++) dup |= _stricmp(names[k], names[i]) == 0;
            if (!dup) break;
        }
    }
    // clans as friends play: two or three tags, each shared by a few of the 15; about half the bots in none
    char clans[3][6];
    int nclans = 2 + rint(s, 2);
    for (int c = 0; c < nclans; c++) made_clan(clans[c], s);
    FILE* f = nullptr;
    if (_wfopen_s(&f, path, L"w") != 0 || !f) return false;
    fprintf(f, "; this install's bots (the Settings tab's re-roll, brain/looks.py): name, look seed, clan tag\n");
    fprintf(f, "experience=%s\neffects=%s\n", experience ? "modern" : "original", EFFECTS_LEVEL[effects >= 0 && effects < 4 ? effects : 0]);
    fprintf(f, "species=%s\n", SPECIES_LEVEL[species >= 0 && species < 3 ? species : 1]);
    if (species == 1 && elite_share >= 0) fprintf(f, "elites=%d\n", elite_share > 100 ? 100 : elite_share);
    for (int i = 0; i < 15; i++) {
        uint32_t look = rnd(s) & 0x7FFFFFFF;
        const char* clan = rint(s, 100) < 55 ? clans[rint(s, nclans)] : "";
        fprintf(f, "bot=%s,%u,%s\n", names[i], look ? look : 1, clan);
    }
    fclose(f);
    bots_panel_profiles(path);
    return true;
}

void roster_name(hb_roster& r, int i, uint32_t seed) {
    uint32_t s = seed | 1;
    char n[64];
    int free_prof[32], nfree = 0;
    for (int k = 0; k < g_nprof; k++)
        if (!name_taken(r, g_prof[k], i) && _stricmp(r.bot[i].name, g_prof[k]) != 0) free_prof[nfree++] = k;
    if (nfree) { strcpy_s(r.bot[i].name, g_prof[free_prof[rint(s, nfree)]]); return; }
    for (int tries = 0; tries < 200; tries++) {
        made_name(n, sizeof n, s);
        if (!name_taken(r, n, i)) break;
    }
    strcpy_s(r.bot[i].name, n);
}

// ------------------------------------------------------------------ roster operations
int roster_on_team(const hb_roster& r, int team) {
    int c = 0;
    for (int i = 0; i < r.n; i++) c += r.bot[i].team == team;
    return c;
}

bool roster_add(hb_roster& r, int team, int tier, int style, uint32_t seed) {
    if (r.n >= roster_cap()) return false;
    hb_bot& b = r.bot[r.n];
    b.team = (uint8_t)team; b.tier = (uint8_t)tier; b.style = (uint8_t)style;
    r.n++;
    roster_name(r, r.n - 1, seed);
    if (team + 1 > r.teams) r.teams = team + 1;
    return true;
}

void roster_remove(hb_roster& r, int i) {
    if (i < 0 || i >= r.n) return;
    for (int k = i; k < r.n - 1; k++) r.bot[k] = r.bot[k + 1];
    r.n--;
}

// Mixed difficulty deals Heroic, Normal, Legendary, Easy in turn (tools/play.py's old default)
int roster_mixed_tier(int k) { static const int M[4] = { 2, 1, 3, 0 }; return M[k % 4]; }
static int mixed_tier(int k) { return roster_mixed_tier(k); }

void roster_fill(hb_roster& r, int teams, int per_team, int tier, int style, uint32_t seed) {
    uint32_t s = seed | 1;
    r.n = 0;
    r.teams = ff_one_team() ? 1 : ff_two_teams() ? 2 : teams < 2 ? 2 : teams > HB_TEAMS_MAX ? HB_TEAMS_MAX : teams;
    for (int k = 0; k < per_team && r.n < roster_cap(); k++)          // team by team, a row at a time:
        for (int t = 0; t < r.teams && r.n < roster_cap(); t++)       // the cap cuts every team evenly
            roster_add(r, t, tier == 4 ? mixed_tier(r.n) : tier == 5 ? 4 : tier, style, rnd(s));
}

void roster_balance(hb_roster& r) {
    // teams with the most bots give their last bots to the ones with the fewest, until within one
    for (int guard = 0; guard < 64; guard++) {
        int hi = 0, lo = 0;
        for (int t = 0; t < r.teams; t++) {
            if (roster_on_team(r, t) > roster_on_team(r, hi)) hi = t;
            if (roster_on_team(r, t) < roster_on_team(r, lo)) lo = t;
        }
        if (roster_on_team(r, hi) - roster_on_team(r, lo) <= 1) return;
        for (int i = r.n - 1; i >= 0; i--)
            if (r.bot[i].team == hi) { r.bot[i].team = (uint8_t)lo; break; }
    }
}

void roster_shuffle(hb_roster& r, uint32_t seed) {
    // new teams for everyone, the team sizes kept
    uint32_t s = seed | 1;
    for (int i = r.n - 1; i > 0; i--) {
        int j = rint(s, i + 1);
        uint8_t t = r.bot[i].team; r.bot[i].team = r.bot[j].team; r.bot[j].team = t;
    }
}

void roster_rename_all(hb_roster& r, uint32_t seed) {
    uint32_t s = seed | 1;
    for (int i = 0; i < r.n; i++) r.bot[i].name[0] = 0;
    for (int i = 0; i < r.n; i++) roster_name(r, i, rnd(s));
}

// ------------------------------------------------------------------ look
namespace ui {
const ImU32 BG = IM_COL32(7, 15, 23, 246), SURF = IM_COL32(12, 25, 37, 255), SURF2 = IM_COL32(18, 37, 54, 255),
            HOVER = IM_COL32(22, 46, 66, 255), LINE = IM_COL32(38, 70, 95, 255), ACCENT = IM_COL32(110, 200, 255, 255),
            TEXT = IM_COL32(226, 236, 245, 255), TEXT2 = IM_COL32(152, 177, 197, 255), TEXT3 = IM_COL32(112, 138, 156, 255),
            GOLD = IM_COL32(255, 201, 77, 255), DANGER = IM_COL32(255, 107, 94, 255),
            FOCUS = TEXT;     // the keyboard's place (and the pointer's: a swatch's hover): gold is changed / current
                              // and the accent selected / on, so neither is focus (one meaning a colour)
static float s = 1.0f;                                   // px per 1080p px
static ImFont* font;
inline float px(float v) { return v * s; }
inline ImVec4 v4(ImU32 c) { return ImGui::ColorConvertU32ToFloat4(c); }
inline ImU32 alpha(ImU32 c, float a) { return (c & 0x00FFFFFF) | ((ImU32)(((c >> 24) & 0xFF) * a) << 24); }
enum Size { TITLE, BODY, SMALL };
inline float size(Size z) { return z == TITLE ? px(34) : z == BODY ? px(21) : px(18); }
inline void push(Size z) { ImGui::PushFont(font, size(z)); }
inline void pop() { ImGui::PopFont(); }

// a label in capitals, small and quiet: what a group of controls is
void caption(const char* t) {
    push(SMALL);
    char up[96]; size_t k = 0;
    for (; t[k] && k < sizeof up - 1; k++) up[k] = (char)toupper((unsigned char)t[k]);
    up[k] = 0;
    ImGui::PushStyleColor(ImGuiCol_Text, v4(TEXT2));
    ImGui::TextUnformatted(up);
    ImGui::PopStyleColor();
    pop();
}

void muted(const char* t, bool wrap = true) {
    push(SMALL);
    ImGui::PushStyleColor(ImGuiCol_Text, v4(TEXT2));
    if (wrap) ImGui::TextWrapped("%s", t);                   // at an outer PushTextWrapPos if one is set
    else ImGui::TextUnformatted(t);
    ImGui::PopStyleColor();
    pop();
}

enum Kind { PRIMARY, OUTLINE, SECONDARY, GHOST, DANGER_K };
// flat buttons: one filled primary per view, an accent outline for the main tool, quiet secondary
// ones, text-only ghosts, red text for destructive ones
bool button(const char* label, Kind k = SECONDARY, ImVec2 size_ = ImVec2(0, 0), bool small = false) {
    ImVec4 bg = k == PRIMARY ? v4(ACCENT) : k == SECONDARY ? v4(SURF2) : k == OUTLINE ? v4(alpha(ACCENT, 0.08f)) : ImVec4(0, 0, 0, 0);
    ImVec4 hv = k == PRIMARY ? v4(IM_COL32(150, 218, 255, 255)) : k == DANGER_K ? v4(alpha(DANGER, 0.14f))
              : k == OUTLINE ? v4(alpha(ACCENT, 0.2f)) : v4(HOVER);
    ImVec4 tx = k == PRIMARY ? v4(IM_COL32(6, 18, 28, 255)) : k == DANGER_K ? v4(DANGER) : k == GHOST || k == OUTLINE ? v4(ACCENT) : v4(TEXT);
    ImGui::PushStyleColor(ImGuiCol_Button, bg);
    ImGui::PushStyleColor(ImGuiCol_ButtonHovered, hv);
    ImGui::PushStyleColor(ImGuiCol_ButtonActive, hv);
    ImGui::PushStyleColor(ImGuiCol_Text, tx);
    ImGui::PushStyleColor(ImGuiCol_Border, k == OUTLINE ? v4(alpha(ACCENT, 0.8f)) : k == SECONDARY ? v4(LINE) : ImVec4(0, 0, 0, 0));
    ImGui::PushStyleVar(ImGuiStyleVar_FrameBorderSize, k == OUTLINE || k == SECONDARY ? 1.0f : 0.0f);
    if (small) ImGui::PushStyleVar(ImGuiStyleVar_FramePadding, ImVec2(px(10), px(5)));
    push(small ? SMALL : BODY);
    bool r = ImGui::Button(label, size_);
    pop();
    if (small) ImGui::PopStyleVar();
    ImGui::PopStyleVar();
    ImGui::PopStyleColor(5);
    return r;
}

// a row of joined options; the chosen one filled with `col`
bool segmented(const char* id, const char* const* labels, int n, int* v, float width, const ImU32* cols = nullptr, int disabled_from = 99) {
    bool changed = false;
    float w = (width - (n - 1) * px(2)) / n;
    ImGui::PushID(id);
    ImGui::PushStyleVar(ImGuiStyleVar_ItemSpacing, ImVec2(px(2), px(2)));
    for (int i = 0; i < n; i++) {
        if (i) ImGui::SameLine();
        bool on = *v == i;
        ImU32 c = cols ? cols[i] : ACCENT;
        ImGui::PushStyleColor(ImGuiCol_Button, on ? v4(alpha(c, 0.28f)) : v4(SURF2));
        ImGui::PushStyleColor(ImGuiCol_ButtonHovered, on ? v4(alpha(c, 0.36f)) : v4(HOVER));
        ImGui::PushStyleColor(ImGuiCol_ButtonActive, v4(alpha(c, 0.45f)));
        ImGui::PushStyleColor(ImGuiCol_Text, on ? v4(TEXT) : v4(TEXT2));
        ImGui::PushStyleColor(ImGuiCol_Border, on ? v4(c) : v4(LINE));
        ImGui::PushStyleVar(ImGuiStyleVar_FrameBorderSize, 1.0f);
        ImGui::PushStyleVar(ImGuiStyleVar_FramePadding, ImVec2(px(4), px(6)));   // the label gets the width
        ImGui::BeginDisabled(i >= disabled_from);
        push(SMALL);
        ImGui::PushID(i);
        if (ImGui::Button(labels[i], ImVec2(w, px(36)))) { *v = i; changed = true; }
        ImGui::PopID();
        pop();
        ImGui::EndDisabled();
        ImGui::PopStyleVar(2);
        ImGui::PopStyleColor(5);
    }
    ImGui::PopStyleVar();
    ImGui::PopID();
    return changed;
}

// [-] value [+]
bool stepper(const char* id, int* v, int lo, int hi, const char* unit, int step = 1, float bh = 36, float bw = 150) {
    bool c = false;                                      // (bh, bw: its height and the value's width, 1080p px)
    ImGui::PushID(id);
    ImGui::BeginDisabled(*v <= lo);
    if (button("-", SECONDARY, ImVec2(px(bh + 4), px(bh)))) { *v = *v - step < lo ? lo : *v - step; c = true; }
    ImGui::EndDisabled();
    ImGui::SameLine();
    char t[48]; sprintf_s(t, unit[0] == '%' ? "%d%s" : "%d %s", *v, unit);
    push(bh < 36 ? SMALL : BODY);
    float tw = ImGui::CalcTextSize(t).x, box = px(bw);
    ImVec2 p = ImGui::GetCursorScreenPos();
    ImGui::GetWindowDrawList()->AddText(ImVec2(p.x + (box - tw) * 0.5f, p.y + (px(bh) - ImGui::GetFontSize()) * 0.5f), TEXT, t);
    pop();
    ImGui::Dummy(ImVec2(box, px(bh)));
    ImGui::SameLine();
    ImGui::BeginDisabled(*v >= hi);
    if (button("+", SECONDARY, ImVec2(px(bh + 4), px(bh)))) { *v = *v + step > hi ? hi : *v + step; c = true; }
    ImGui::EndDisabled();
    ImGui::PopID();
    return c;
}

// the last item has the keyboard's focus and a key put it there (not a click): its focus ring shows
static bool focus_shown() { return ImGui::IsItemFocused() && ImGui::GetCurrentContext()->NavCursorVisible; }

// a control drawn by hand: an invisible button keyboard navigation reaches (InvisibleButton leaves it out unless told),
// with ImGui's focus frame once a key moved the focus there (the keyboard pass)
static bool nav_button(const char* id, ImVec2 size) {
    bool r = ImGui::InvisibleButton(id, size, ImGuiButtonFlags_EnableNav);
    ImGui::RenderNavCursor(ImRect(ImGui::GetItemRectMin(), ImGui::GetItemRectMax()), ImGui::GetItemID());
    return r;
}

// Esc for a dialog, but not the press that shut an open list (keyboard navigation closes the list on it; the dialog
// under it stays: the keyboard pass, Esc in the Backpack list closed the whole editor). g_list_frame: the last frame a
// list (combo) was open
static int g_list_frame = -10;
static int g_game_to = -1;               // the editor's game list: the one Up / Down moves the focus to
static bool esc_pressed() {                              // (Esc, or the pad's B: back one level)
    return (ImGui::IsKeyPressed(ImGuiKey_Escape) || ImGui::IsKeyPressed(ImGuiKey_GamepadFaceRight, false))
           && ImGui::GetFrameCount() - g_list_frame > 1;
}

// an on/off switch with its label left of it
bool toggle(const char* label, uint32_t* v) {
    ImGui::PushID(label);
    push(BODY);
    ImVec2 ts = ImGui::CalcTextSize(label);
    ImVec2 p = ImGui::GetCursorScreenPos();
    float w = px(46), h = px(26), gap = px(12);
    ImVec2 size_(ts.x + gap + w, h > ts.y ? h : ts.y);
    bool clicked = ImGui::InvisibleButton("##t", size_, ImGuiButtonFlags_EnableNav);   // (its own focus ring)
    bool hov = ImGui::IsItemHovered();
    if (clicked) *v = !*v;
    ImDrawList* dl = ImGui::GetWindowDrawList();
    auto C = [](ImU32 c) { return ImGui::GetColorU32(c); };          // dims with BeginDisabled (style alpha)
    dl->AddText(ImVec2(p.x, p.y + (size_.y - ts.y) * 0.5f), C(*v ? TEXT : TEXT2), label);
    ImVec2 a(p.x + ts.x + gap, p.y + (size_.y - h) * 0.5f), b(a.x + w, a.y + h);
    dl->AddRectFilled(a, b, C(*v ? alpha(ACCENT, hov ? 1.0f : 0.85f) : (hov ? HOVER : SURF2)), h * 0.5f);
    dl->AddRect(a, b, C(*v ? ACCENT : LINE), h * 0.5f);
    float kx = *v ? b.x - h * 0.5f : a.x + h * 0.5f;
    dl->AddCircleFilled(ImVec2(kx, a.y + h * 0.5f), h * 0.36f, C(*v ? IM_COL32(6, 18, 28, 255) : TEXT2));
    if (focus_shown()) dl->AddRect(ImVec2(a.x - 3, a.y - 3), ImVec2(b.x + 3, b.y + 3), FOCUS, h * 0.5f + 3, 0, 2.0f);
    pop();
    ImGui::PopID();
    return clicked;
}

// a close / remove cross drawn as lines (a letter x reads as text, not as a control)
bool icon_x(const char* id, float size_, const char* tip) {
    ImVec2 p = ImGui::GetCursorScreenPos();
    bool r = nav_button(id, ImVec2(size_, size_));
    bool hov = ImGui::IsItemHovered();
    ImDrawList* dl = ImGui::GetWindowDrawList();
    if (hov) dl->AddRectFilled(p, ImVec2(p.x + size_, p.y + size_), HOVER, px(2));
    float m = size_ * 0.32f;
    ImU32 c = hov ? TEXT : TEXT2;
    dl->AddLine(ImVec2(p.x + m, p.y + m), ImVec2(p.x + size_ - m, p.y + size_ - m), c, px(2));
    dl->AddLine(ImVec2(p.x + size_ - m, p.y + m), ImVec2(p.x + m, p.y + size_ - m), c, px(2));
    if (hov && tip) ImGui::SetTooltip("%s", tip);
    return r;
}

// difficulty as rank chevrons: one (Easy) to four (Legendary); Scaling, no rank: a gauge (its needle moves both ways)
void chevrons(ImDrawList* dl, ImVec2 c, int tier, float scale_ = 1.0f) {
    float w = px(9) * scale_, h = px(5) * scale_, gap = px(5) * scale_;
    if (tier == 4) {
        ImVec2 o(c.x, c.y + px(4) * scale_);
        dl->PathArcTo(o, w, 3.14159f, 6.28318f, 16);
        dl->PathStroke(TIER_COL[4], 0, px(2.2f) * scale_);
        dl->AddLine(o, ImVec2(o.x + w * 0.45f, o.y - w * 0.75f), TIER_COL[4], px(2.2f) * scale_);
        dl->AddCircleFilled(o, px(2.2f) * scale_, TIER_COL[4]);
        return;
    }
    int n = tier + 1;
    float top = c.y - (n - 1) * gap * 0.5f;
    for (int k = 0; k < n; k++) {
        float y = top + k * gap;
        ImVec2 pts[3] = { ImVec2(c.x - w, y - h * 0.5f), ImVec2(c.x, y + h * 0.5f), ImVec2(c.x + w, y - h * 0.5f) };
        dl->AddPolyline(pts, 3, TIER_COL[tier], 0, px(2.2f) * scale_);
    }
}

void line(float pad = 12) {
    ImVec2 p = ImGui::GetCursorScreenPos();
    ImGui::GetWindowDrawList()->AddLine(ImVec2(p.x, p.y + px(pad) * 0.5f), ImVec2(p.x + ImGui::GetContentRegionAvail().x, p.y + px(pad) * 0.5f), LINE);
    ImGui::Dummy(ImVec2(0, px(pad)));
}
}  // namespace ui

// ------------------------------------------------------------------ the panel
static int g_sel = -1;                 // the selected bot
static uint32_t g_seed = 0x9E3779B9;
static int qs_teams = 2, qs_per = 4, qs_tier = 3, qs_style = HB_STYLE_ANY;

static uint32_t seed() { g_seed = g_seed * 1664525u + 1013904223u + (uint32_t)(ImGui::GetTime() * 1000.0); return g_seed | 1; }
static const char* g_open_popup;       // native/panel_preview.cpp: a state to draw (a popup open, a bot picked)

void bots_panel_debug(int select, const char* popup) { g_sel = select; g_open_popup = popup; }

static void move_bot(hb_roster& r, int i, int team) {
    if (i < 0 || i >= r.n || team < 0 || team >= HB_TEAMS_MAX) return;
    r.bot[i].team = (uint8_t)team;
    if (team + 1 > r.teams) r.teams = team + 1;
}

static void bot_row(bots_panel_env& e, int i, float w, bool& changed) {
    using namespace ui;
    hb_roster& r = *e.roster;
    hb_bot& b = r.bot[i];
    ImGui::PushID(i);
    ImVec2 p = ImGui::GetCursorScreenPos();
    float h = px(40);
    bool sel = g_sel == i;
    ImGui::PushStyleColor(ImGuiCol_Header, v4(SURF2));
    ImGui::PushStyleColor(ImGuiCol_HeaderHovered, v4(HOVER));
    ImGui::PushStyleColor(ImGuiCol_HeaderActive, v4(HOVER));
    if (ImGui::Selectable("##row", sel, ImGuiSelectableFlags_AllowOverlap, ImVec2(w, h))) g_sel = i;
    ImGui::PopStyleColor(3);
    if (ImGui::IsItemHovered(ImGuiHoveredFlags_ForTooltip) && !ImGui::IsMouseDragging(0))
        ImGui::SetTooltip("%s, %s. Drag to another team to move.", TIER_NAME[b.tier < 5 ? b.tier : 3],
                          b.style < 8 ? STYLE_NAME[b.style] : "");
    if (ImGui::BeginDragDropSource()) {                       // drag onto another team to move it
        ImGui::SetDragDropPayload("HB_BOT", &i, sizeof i);
        push(BODY);
        ImGui::TextUnformatted(b.name);
        pop();
        muted("Drop on a team", false);
        ImGui::EndDragDropSource();
    }
    if (ImGui::BeginPopupContextItem("row")) {               // right-click: the same actions by menu
        g_sel = i;
        push(BODY);
        if (!ff_one_team() && ImGui::BeginMenu("Move to")) {   // (ODST Firefight: one team)
            for (int t = 0; t < roster_max_teams(e.title); t++) {
                ImGui::PushStyleColor(ImGuiCol_Text, v4(TEAM_COL[t]));
                if (ImGui::MenuItem(TEAM_NAME[t], nullptr, b.team == t)) { move_bot(r, i, t); changed = true; }
                ImGui::PopStyleColor();
            }
            ImGui::EndMenu();
        }
        if (ImGui::MenuItem("Duplicate", "Ctrl+D", false, r.n < roster_cap())) {
            roster_add(r, b.team, b.tier, b.style, seed()); changed = true;
        }
        ImGui::Separator();
        ImGui::PushStyleColor(ImGuiCol_Text, v4(DANGER));
        if (ImGui::MenuItem("Remove", "Del")) { roster_remove(r, i); g_sel = -1; changed = true; }
        ImGui::PopStyleColor();
        pop();
        ImGui::EndPopup();
    }
    if (i < r.n) {                                           // (still there after the menu)
        ImDrawList* dl = ImGui::GetWindowDrawList();
        if (sel) dl->AddRectFilled(p, ImVec2(p.x + px(3), p.y + h), GOLD);
        chevrons(dl, ImVec2(p.x + px(22), p.y + h * 0.5f), b.tier < 5 ? b.tier : 3);
        // the name, then its play style on the right when both fit; a long name is cut with "..."
        // and the style moves to the tooltip (narrow cards at eight teams, 15-character names)
        const char* st = b.style < HB_STYLE_ANY ? STYLE_NAME[b.style] : "";
        push(SMALL);
        float sw = *st ? ImGui::CalcTextSize(st).x : 0.0f;
        pop();
        push(BODY);
        float name_x = p.x + px(42), right = p.x + w - px(12);
        float nw = ImGui::CalcTextSize(b.name).x;
        bool show_style = *st && name_x + nw + px(14) + sw <= right;
        float room = right - name_x;
        char shown[24];
        strcpy_s(shown, b.name);
        if (nw > room) {
            size_t k = strlen(shown);
            while (k > 1) {
                shown[--k] = 0;
                char t[24]; sprintf_s(t, "%s...", shown);
                if (ImGui::CalcTextSize(t).x <= room) { strcpy_s(shown, t); break; }
            }
        }
        dl->AddText(ImVec2(name_x, p.y + (h - ImGui::GetFontSize()) * 0.5f), sel ? TEXT : IM_COL32(205, 220, 232, 255), shown);
        pop();
        if (show_style) {
            push(SMALL);
            dl->AddText(ImVec2(right - sw, p.y + (h - ImGui::GetFontSize()) * 0.5f), TEXT3, st);
            pop();
        }
    }
    ImGui::PopID();
}

static void team_card(bots_panel_env& e, int t, ImVec2 size, bool& changed) {
    using namespace ui;
    hb_roster& r = *e.roster;
    ImGui::PushID(t);
    ImVec2 p = ImGui::GetCursorScreenPos();
    ImDrawList* dl = ImGui::GetWindowDrawList();
    dl->AddRectFilled(p, ImVec2(p.x + size.x, p.y + size.y), SURF);
    dl->AddRect(p, ImVec2(p.x + size.x, p.y + size.y), LINE);
    dl->AddRectFilled(p, ImVec2(p.x + size.x, p.y + px(4)), ff_one_team() ? ACCENT : TEAM_COL[t]);   // (ODST Firefight: your side, not Red)
    bool folded = t >= roster_max_teams(e.title);            // Halo: CE: green and up play as red / blue
    // header: name, count, remove (the last team, when empty)
    push(BODY);
    char head[48]; sprintf_s(head, "%s", ff_one_team() ? "Your team" : TEAM_NAME[t]);
    for (char* c = head; *c; c++) *c = (char)toupper((unsigned char)*c);
    dl->AddText(ImVec2(p.x + px(14), p.y + px(14)), ff_one_team() ? ACCENT : TEAM_COL[t], head);
    pop();
    int n = roster_on_team(r, t);
    push(SMALL);
    char cnt[32]; sprintf_s(cnt, n == 1 ? "1 bot" : "%d bots", n);
    float cw = ImGui::CalcTextSize(cnt).x;
    bool removable = t == r.teams - 1 && t >= 2 && n == 0;
    dl->AddText(ImVec2(p.x + size.x - cw - px(removable ? 46.0f : 14.0f), p.y + px(16)), TEXT2, cnt);
    if (folded) {
        char f[48]; sprintf_s(f, "plays as %s in Halo: CE", TEAM_NAME[t % 2]);
        dl->AddText(ImVec2(p.x + px(14), p.y + px(40)), TEXT3, f);
    }
    pop();
    if (removable) {
        ImGui::SetCursorScreenPos(ImVec2(p.x + size.x - px(40), p.y + px(10)));
        if (icon_x("rm", px(30), "Remove this team")) { r.teams--; changed = true; }
    }
    // the bots, scrolling inside the card if they do not fit
    float top = px(folded ? 64.0f : 48.0f), add_h = px(40);
    ImGui::SetCursorScreenPos(ImVec2(p.x + px(1), p.y + top));
    ImGui::PushStyleColor(ImGuiCol_ChildBg, ImVec4(0, 0, 0, 0));
    ImGui::BeginChild("bots", ImVec2(size.x - px(2), size.y - top - add_h - px(8)), ImGuiChildFlags_NavFlattened);   // (Tab goes on into it)
    float w = ImGui::GetContentRegionAvail().x;
    for (int i = 0; i < r.n; i++)
        if (r.bot[i].team == t) bot_row(e, i, w, changed);
    if (n == 0) {
        ImGui::Dummy(ImVec2(0, px(6)));
        ImGui::Indent(px(14));
        muted("No bots. Drag one here or add one.");
        ImGui::Unindent(px(14));
    }
    ImGui::EndChild();
    ImGui::PopStyleColor();
    // the whole card takes a dropped bot
    ImGui::SetCursorScreenPos(p);
    ImGui::InvisibleButton("drop", ImVec2(size.x, size.y - add_h - px(8)));
    if (ImGui::BeginDragDropTarget()) {
        if (const ImGuiPayload* pl = ImGui::AcceptDragDropPayload("HB_BOT")) { move_bot(r, *(const int*)pl->Data, t); changed = true; }
        ImGui::EndDragDropTarget();
    }
    ImGui::SetCursorScreenPos(ImVec2(p.x + px(8), p.y + size.y - add_h - px(4)));
    ImGui::BeginDisabled(r.n >= roster_cap());
    char add[32]; sprintf_s(add, "+  Add bot##add%d", t);
    if (button(add, GHOST, ImVec2(size.x - px(16), add_h), true)) {
        int tier = *e.difficulty < 4 ? (int)*e.difficulty : *e.difficulty == 5 ? 4 : mixed_tier(r.n);
        if (roster_add(r, t, tier, HB_STYLE_ANY, seed())) { g_sel = r.n - 1; changed = true; }
    }
    ImGui::EndDisabled();
    ImGui::SetCursorScreenPos(ImVec2(p.x, p.y + size.y));
    ImGui::PopID();
}

static void add_team_card(bots_panel_env& e, ImVec2 size, bool& changed) {
    using namespace ui;
    ImVec2 p = ImGui::GetCursorScreenPos();
    bool hov = ImGui::IsMouseHoveringRect(p, ImVec2(p.x + size.x, p.y + size.y));
    ImDrawList* dl = ImGui::GetWindowDrawList();
    for (float x = p.x; x < p.x + size.x; x += px(10)) {     // dashed outline: a slot, not a team
        dl->AddLine(ImVec2(x, p.y), ImVec2(x + px(5) < p.x + size.x ? x + px(5) : p.x + size.x, p.y), hov ? ACCENT : LINE);
        dl->AddLine(ImVec2(x, p.y + size.y), ImVec2(x + px(5) < p.x + size.x ? x + px(5) : p.x + size.x, p.y + size.y), hov ? ACCENT : LINE);
    }
    for (float y = p.y; y < p.y + size.y; y += px(10)) {
        dl->AddLine(ImVec2(p.x, y), ImVec2(p.x, y + px(5) < p.y + size.y ? y + px(5) : p.y + size.y), hov ? ACCENT : LINE);
        dl->AddLine(ImVec2(p.x + size.x, y), ImVec2(p.x + size.x, y + px(5) < p.y + size.y ? y + px(5) : p.y + size.y), hov ? ACCENT : LINE);
    }
    hb_roster& r = *e.roster;
    push(BODY);
    char t[48]; sprintf_s(t, "+  Add %s team", TEAM_NAME[r.teams]);
    ImVec2 ts = ImGui::CalcTextSize(t);
    dl->AddText(ImVec2(p.x + (size.x - ts.x) * 0.5f, p.y + (size.y - ts.y) * 0.5f), hov ? ACCENT : TEXT2, t);
    pop();
    if (nav_button("addteam", size)) { r.teams++; changed = true; }
}

// one "[n]" block of logs\scaling_view.txt (brain/scaling.py view_lines): his K/D over his last n Scaling games, the
// skill rating from them, what the bots play at and each hand setting's row (brain/scaling.py PARAMS order)
struct scale_view {
    bool ok;
    char used[16], kd[16], skill[16];
    int n;
    char label[16][32], pct[16][16], val[16][24];
};
static void scale_header(const char* key, char* out, size_t cap) {   // a line above the blocks: "lock|1", "profile|x"
    out[0] = 0;
    size_t kl = strlen(key);
    for (const char* p = g_scale_text; *p && *p != '[';) {
        const char* e = strchr(p, '\n');
        size_t len = e ? (size_t)(e - p) : strlen(p);
        if (len > kl && !strncmp(p, key, kl) && p[kl] == '|') { sprintf_s(out, cap, "%.*s", (int)(len - kl - 1), p + kl + 1); return; }
        if (!e) return;
        p = e + 1;
    }
}
static scale_view scale_read(int games) {
    scale_load();
    scale_view v{};
    strcpy_s(v.used, "0"); strcpy_s(v.kd, "-"); strcpy_s(v.skill, "100%");
    char tag[16]; sprintf_s(tag, "[%d]\n", games);
    const char* p = strstr(g_scale_text, tag);
    if (!p) return v;
    v.ok = true;
    for (p += strlen(tag); *p && *p != '[';) {
        const char* e = strchr(p, '\n');
        int len = (int)(e ? e - p : strlen(p));
        char ln[160]; sprintf_s(ln, "%.*s", len < 159 ? len : 159, p);
        char* ctx = nullptr;
        const char* k = strtok_s(ln, "|", &ctx);
        const char* a = strtok_s(nullptr, "|", &ctx);
        const char* b = strtok_s(nullptr, "|", &ctx);
        const char* c = strtok_s(nullptr, "|", &ctx);
        if (k && a) {
            if (!strcmp(k, "used")) strncpy_s(v.used, a, _TRUNCATE);
            else if (!strcmp(k, "kd")) strncpy_s(v.kd, a, _TRUNCATE);
            else if (!strcmp(k, "skill")) strncpy_s(v.skill, a, _TRUNCATE);
            else if (!strcmp(k, "row") && b && c && v.n < 16) {
                strncpy_s(v.label[v.n], a, _TRUNCATE);
                strncpy_s(v.pct[v.n], b, _TRUNCATE);
                strncpy_s(v.val[v.n], c, _TRUNCATE);
                v.n++;
            }
        }
        if (!e) break;
        p = e + 1;
    }
    return v;
}
// the rating's lines, in the hover box and the Advanced tab
static void scale_summary(const scale_view& v, int games, bool rating = true) {   // (rating: its own line)
    using namespace ui;
    char lock[8], prof[32];
    scale_header("lock", lock, sizeof lock);
    scale_header("profile", prof, sizeof prof);
    if (!strcmp(v.used, "0")) ImGui::TextUnformatted("No Scaling games played yet.");
    else ImGui::Text("Your K/D over your last %s of %d games: %s", v.used, games, v.kd);
    if (rating) ImGui::Text("Skill rating: %s%s", v.skill, !strcmp(lock, "1") ? " (locked)" : "");
    if (prof[0] && rating) ImGui::Text("Custom profile: %s", prof);
}
static void scale_tooltip(int n) {
    using namespace ui;
    scale_view v = scale_read(n);
    ImGui::SetNextWindowSize(ImVec2(px(500), 0));
    ImGui::BeginTooltip();
    push(BODY);
    ImGui::TextUnformatted("Scaling");
    pop();
    push(SMALL);
    scale_summary(v, n);
    ImGui::PushStyleColor(ImGuiCol_Text, v4(TEXT2));
    ImGui::TextWrapped("100%% is a skilled human player's baseline; Legendary's hands are about 150%%. Game sense is "
                       "Legendary's up to 150%% and grows past it above.");
    ImGui::PopStyleColor();
    if (v.n && ImGui::BeginTable("scale", 3, ImGuiTableFlags_SizingFixedFit | ImGuiTableFlags_PadOuterX)) {
        for (int i = 0; i < v.n; i++) {
            ImGui::TableNextRow();
            ImGui::TableNextColumn(); ImGui::TextUnformatted(v.label[i]);
            ImGui::TableNextColumn(); ImGui::TextColored(v4(TEXT), "%s", v.pct[i]);
            ImGui::TableNextColumn(); ImGui::TextColored(v4(TEXT2), "%s", v.val[i]);
        }
        ImGui::EndTable();
    }
    pop();
    ImGui::EndTooltip();
}
static void scale_games_setting(bots_panel_env& e, bool& changed) {   // how many of the player's games Scaling averages
    if (!e.scale_games) return;
    ui::caption("Games averaged");
    int g = (int)(*e.scale_games < 1 ? 5 : *e.scale_games > 20 ? 20 : *e.scale_games);
    if (ui::stepper("scalegames", &g, 1, 20, g == 1 ? "game" : "games")) { *e.scale_games = (uint32_t)g; changed = true; }
}

// the footer's difficulty follows the list: one difficulty on every bot shows it, several show Mixed
static void sync_difficulty(bots_panel_env& e) {
    const hb_roster& r = *e.roster;
    if (!r.n) return;
    for (int i = 1; i < r.n; i++)
        if (r.bot[i].tier != r.bot[0].tier) { *e.difficulty = 4; return; }
    *e.difficulty = r.bot[0].tier == 4 ? 5u : (uint32_t)r.bot[0].tier;
}

static void inspector(bots_panel_env& e, bool& changed) {
    using namespace ui;
    hb_roster& r = *e.roster;
    if (g_sel < 0 || g_sel >= r.n) {
        caption("Selected bot");
        ImGui::Dummy(ImVec2(0, px(8)));
        if (ff_one_team()) {                                 // (ODST Firefight: one team, yours)
            muted("Pick a bot to change its difficulty or play style. Names, clan tags and armor are in Settings.");
            ImGui::Dummy(ImVec2(0, px(12)));
            muted("Firefight holds 4 players: you and up to 3 bots, all on your team. "
                  "Right-click a bot for more; Del removes the picked one.");
            return;
        }
        muted("Pick a bot to change its team, difficulty or play style. Names, clan tags and armor are in Settings.");
        ImGui::Dummy(ImVec2(0, px(12)));
        muted("Drag a bot onto another team to move it, or pick it and press Ctrl+Left / Right. "
              "Right-click a bot for more; Del removes the picked one.");
        return;
    }
    hb_bot& b = r.bot[g_sel];
    float W = ImGui::GetContentRegionAvail().x;
    caption("Selected bot");                                 // names and looks belong to the profiles (Settings)
    push(BODY);
    const char* clan = profile_clan_of(b.name);
    if (clan[0]) {
        ImGui::PushStyleColor(ImGuiCol_Text, v4(ACCENT));
        ImGui::Text("[%s]", clan);
        ImGui::PopStyleColor();
        ImGui::SameLine(0, px(8));
    }
    ImGui::TextUnformatted(b.name);
    pop();
    ImGui::Dummy(ImVec2(0, px(6)));
    if (!ff_one_team()) {                                    // (ODST Firefight: every bot on your team, no picker)
    caption("Team");
    int maxt = roster_max_teams(e.title);
    float chip = px(30), gap = px(6);
    for (int t = 0; t < HB_TEAMS_MAX; t++) {
        if (t) ImGui::SameLine(0, gap);
        ImGui::PushID(t);
        ImVec2 p = ImGui::GetCursorScreenPos();
        ImGui::BeginDisabled(t >= maxt);
        if (nav_button("chip", ImVec2(chip, chip)) && b.team != t) { move_bot(r, g_sel, t); changed = true; }
        bool hov = ImGui::IsItemHovered(ImGuiHoveredFlags_AllowWhenDisabled);
        ImGui::EndDisabled();
        ImDrawList* dl = ImGui::GetWindowDrawList();
        dl->AddRectFilled(p, ImVec2(p.x + chip, p.y + chip), t >= maxt ? alpha(TEAM_COL[t], 0.25f) : TEAM_COL[t], px(2));
        if (b.team == t) dl->AddRect(ImVec2(p.x - 3, p.y - 3), ImVec2(p.x + chip + 3, p.y + chip + 3), GOLD, px(3), 0, 2.0f);
        if (hov) ImGui::SetTooltip(t >= maxt ? "%s: Halo: CE has two teams" : "%s", TEAM_NAME[t]);
        ImGui::PopID();
    }
    ImGui::SameLine(0, px(12));                              // the team's name beside its chips
    push(SMALL);
    ImGui::SetCursorPosY(ImGui::GetCursorPosY() + (chip - ImGui::GetFontSize()) * 0.5f);
    ImGui::PushStyleColor(ImGuiCol_Text, v4(TEAM_COL[b.team < HB_TEAMS_MAX ? b.team : 0]));
    ImGui::TextUnformatted(TEAM_NAME[b.team < HB_TEAMS_MAX ? b.team : 0]);
    ImGui::PopStyleColor();
    pop();
    }
    ImGui::Dummy(ImVec2(0, px(2)));
    caption("Difficulty");
    int tier = b.tier < 5 ? b.tier : 3, rank = tier < 4 ? tier : -1, own = tier == 4 ? 0 : -1;
    if (segmented("tier", TIER_NAME, 4, &rank, W, TIER_COL)) { b.tier = (uint8_t)rank; sync_difficulty(e); changed = true; }
    if (segmented("tier_s", &TIER_NAME[4], 1, &own, W, &TIER_COL[4])) { b.tier = 4; sync_difficulty(e); changed = true; }
    if (ImGui::IsItemHovered(ImGuiHoveredFlags_ForTooltip)) scale_tooltip(e.scale_games ? (int)*e.scale_games : 5);
    tier = b.tier < 5 ? b.tier : 3;                          // (Scaling on a row of its own: no rank among the four)
    muted(TIER_ABOUT[tier]);
    if (tier == 4) {
        ImGui::Dummy(ImVec2(0, px(4)));
        scale_games_setting(e, changed);
    }
    ImGui::Dummy(ImVec2(0, px(2)));
    caption("Play style");
    float cw = (W - px(6)) * 0.5f;                           // two columns, the chosen one's line under them
    for (int k = 0; k < 8; k++) {
        int st = k == 0 ? HB_STYLE_ANY : k - 1;             // "Any style" first
        if (k % 2) ImGui::SameLine(0, px(6));
        bool on = b.style == st;
        ImGui::PushID(k);
        ImGui::PushStyleColor(ImGuiCol_Button, on ? v4(alpha(ACCENT, 0.16f)) : v4(SURF2));
        ImGui::PushStyleColor(ImGuiCol_ButtonHovered, on ? v4(alpha(ACCENT, 0.24f)) : v4(HOVER));
        ImGui::PushStyleColor(ImGuiCol_ButtonActive, v4(alpha(ACCENT, 0.3f)));
        ImGui::PushStyleColor(ImGuiCol_Text, on ? v4(TEXT) : v4(TEXT2));
        ImGui::PushStyleColor(ImGuiCol_Border, on ? v4(ACCENT) : v4(LINE));
        ImGui::PushStyleVar(ImGuiStyleVar_FrameBorderSize, 1.0f);
        push(SMALL);
        if (ImGui::Button(STYLE_NAME[st], ImVec2(cw, px(32)))) { b.style = (uint8_t)st; changed = true; }
        pop();
        ImGui::PopStyleVar();
        ImGui::PopStyleColor(5);
        ImGui::PopID();
    }
    muted(STYLE_ABOUT[b.style < 8 ? b.style : HB_STYLE_ANY]);
    ImGui::Dummy(ImVec2(0, px(4)));
    ImGui::BeginDisabled(r.n >= roster_cap());
    if (button("Duplicate", SECONDARY, ImVec2((W - px(8)) * 0.5f, 0), true)) {
        roster_add(r, b.team, b.tier, b.style, seed()); g_sel = r.n - 1; changed = true;
    }
    ImGui::EndDisabled();
    ImGui::SameLine(0, px(8));
    if (button("Remove", DANGER_K, ImVec2((W - px(8)) * 0.5f, 0), true)) { roster_remove(r, g_sel); g_sel = -1; changed = true; }
    if (ImGui::IsItemHovered(ImGuiHoveredFlags_ForTooltip)) ImGui::SetTooltip("Del");
}

static void quick_setup(bots_panel_env& e, bool& changed) {
    using namespace ui;
    hb_roster& r = *e.roster;
    ImGui::SetNextWindowSize(ImVec2(px(620), 0));
    if (!ImGui::BeginPopupModal("Quick setup", nullptr, ImGuiWindowFlags_NoTitleBar | ImGuiWindowFlags_NoResize | ImGuiWindowFlags_NoMove))
        return;
    float W = ImGui::GetContentRegionAvail().x;
    push(TITLE);
    ImGui::TextUnformatted("Quick setup");
    pop();
    muted(ff_one_team() ? "Makes a new list of bots for Firefight. They all fight on your team."
          : ff_two_teams() ? "Makes a new list of bots for Firefight, on the red and blue teams. The Covenant AI are blue."
                           : "Makes a new list of bots. You pick your own team in the lobby.");
    line(16);
    int maxt = roster_max_teams(e.title);
    if (ff_one_team()) qs_teams = 1;
    else if (ff_two_teams()) qs_teams = 2;
    else {
        caption("Teams");
        static const char* TN[7] = { "2", "3", "4", "5", "6", "7", "8" };
        int ti = qs_teams - 2;
        if (segmented("teams", TN, 7, &ti, W, nullptr, maxt - 1)) qs_teams = ti + 2;
        if (maxt == 2) muted("Halo: CE team games have two teams.");
        ImGui::Dummy(ImVec2(0, px(4)));
    }
    caption(ff_one_team() ? "Bots" : "Bots on each team");
    int most = ff_one_team() ? roster_cap() : (roster_cap() + qs_teams - 1) / qs_teams;   // (ODST Firefight: one team, yours; Reach's 7 split 4 + 3)
    if (qs_per > most) qs_per = most;
    stepper("per", &qs_per, 1, most, qs_per == 1 ? "bot" : "bots");
    ImGui::Dummy(ImVec2(0, px(4)));
    caption("Difficulty");
    static const char* DN[6] = { "Easy", "Normal", "Heroic", "Legendary", "Mixed", "Scaling" };
    static const ImU32 DC[6] = { TIER_COL[0], TIER_COL[1], TIER_COL[2], TIER_COL[3], ACCENT, TIER_COL[4] };
    segmented("qtier", DN, 6, &qs_tier, W, DC);
    if (ImGui::IsItemHovered(ImGuiHoveredFlags_ForTooltip)) scale_tooltip(e.scale_games ? (int)*e.scale_games : 5);
    muted(qs_tier < 4 ? TIER_ABOUT[qs_tier] : qs_tier == 4 ? "A mix of all four, like a real lobby." : SCALE_ABOUT);
    if (qs_tier == 5) scale_games_setting(e, changed);
    ImGui::Dummy(ImVec2(0, px(4)));
    caption("Play style");
    push(BODY);
    ImGui::SetNextItemWidth(W);
    ImGui::PushStyleColor(ImGuiCol_FrameBg, v4(SURF2));
    ImGui::PushStyleColor(ImGuiCol_FrameBgHovered, v4(HOVER));
    ImGui::PushStyleColor(ImGuiCol_Button, v4(SURF2));            // the arrow box in the panel's colours
    ImGui::PushStyleColor(ImGuiCol_ButtonHovered, v4(HOVER));
    ImGui::PushStyleColor(ImGuiCol_Border, v4(LINE));
    ImGui::PushStyleColor(ImGuiCol_Header, v4(alpha(ACCENT, 0.16f)));
    ImGui::PushStyleColor(ImGuiCol_HeaderHovered, v4(HOVER));
    ImGui::PushStyleVar(ImGuiStyleVar_FrameBorderSize, 1.0f);
    if (ImGui::BeginCombo("##qstyle", STYLE_NAME[qs_style])) {
        g_list_frame = ImGui::GetFrameCount();
        for (int k = 0; k < 8; k++) {
            int st = k == 0 ? HB_STYLE_ANY : k - 1;
            if (ImGui::Selectable(STYLE_NAME[st], qs_style == st)) qs_style = st;
            if (qs_style == st) ImGui::SetItemDefaultFocus();   // (the keys start on what it is)
        }
        ImGui::EndCombo();
    }
    ImGui::PopStyleVar();
    ImGui::PopStyleColor(7);
    pop();
    line(18);
    int total = qs_per * qs_teams;
    if (total > roster_cap()) total = roster_cap();             // (Reach's Firefight: 7 bots at most, the cap cuts the teams evenly)
    char what[160];
    if (ff_one_team()) sprintf_s(what, "%d %s on your team.%s", total, total == 1 ? "bot" : "bots",
                                 r.n ? " Replaces the list you have now." : "");
    else sprintf_s(what, "%d bots: %d on each of %d teams.%s", total, qs_per, qs_teams,
                   r.n ? " Replaces the list you have now." : "");
    push(BODY);
    ImGui::PushStyleColor(ImGuiCol_Text, v4(r.n ? GOLD : TEXT));
    ImGui::TextWrapped("%s", what);
    ImGui::PopStyleColor();
    pop();
    if (!hb_panel_firefight) muted("In free-for-all game types every bot plays for itself.");
    ImGui::Dummy(ImVec2(0, px(10)));
    float bw = px(160);
    ImGui::SetCursorPosX(ImGui::GetCursorPosX() + W - 2 * bw - px(10));
    if (button("Cancel", SECONDARY, ImVec2(bw, px(44))) || esc_pressed()) ImGui::CloseCurrentPopup();
    ImGui::SameLine(0, px(10));
    char make[32]; sprintf_s(make, "Make %d bots", total);
    if (button(make, PRIMARY, ImVec2(bw, px(44)))) {
        roster_fill(r, qs_teams, qs_per, qs_tier, qs_style, seed());
        *e.difficulty = (uint32_t)qs_tier;
        g_sel = -1;
        changed = true;
        ImGui::CloseCurrentPopup();
    }
    ImGui::EndPopup();
}

// ------------------------------------------------------------------ the Settings tab. MCC's own Settings screen is a
// UE4 widget inside its packed UI (docs/research/08), so the tab lives in this panel., and no dev notes in the menu
// ("all that shit needs to be back end stuff only the devs can review in the files"): which games wear their own look
// is in docs/research/15-modern-customization.md.
static int g_tab = 0;                                    // 0 the bots list, 1 Settings, 2 Advanced
void bots_panel_debug_tab(int tab) { g_tab = tab; }      // native/panel_preview.cpp
static int rr_experience = 0;                            // the re-roll dialog's choices
static int rr_effects = 0;                               // 0 none, 1 rare, 2 common, 3 frequent
static int rr_species = 1;                               // 0 Spartans only, 1 mixed, 2 Elites only
static int rr_elite_share = 20;                          // mixed: the percent of bots rolled Elite (the games' own: 20-30)
static bool rr_failed = false;                           // the last re-roll could not write the file
void bots_panel_debug_reroll_failed(bool f) { rr_failed = f; }   // native/panel_preview.cpp
static const char* FX_NAME[4] = { "None", "Rare", "Common", "Frequent" };   // EFFECTS_LEVEL, as the menu says it

static bool tab(const char* label, bool on) {
    using namespace ui;
    push(BODY);
    ImVec2 ts = ImGui::CalcTextSize(label);
    ImVec2 p = ImGui::GetCursorScreenPos();
    bool hit = nav_button(label, ImVec2(ts.x + px(28), ts.y + px(16)));
    bool hov = ImGui::IsItemHovered();
    ImDrawList* dl = ImGui::GetWindowDrawList();
    dl->AddText(ImVec2(p.x + px(14), p.y + px(4)), on || hov ? TEXT : TEXT2, label);
    if (on) dl->AddRectFilled(ImVec2(p.x + px(10), p.y + ts.y + px(10)), ImVec2(p.x + ts.x + px(18), p.y + ts.y + px(13)), ACCENT);
    pop();
    return hit;
}

static void reroll_dialog(bots_panel_env& e, bool& changed) {
    using namespace ui;
    hb_roster& r = *e.roster;
    ImGui::SetNextWindowSize(ImVec2(px(660), 0));
    if (!ImGui::BeginPopupModal("Re-roll bot profiles", nullptr, ImGuiWindowFlags_NoTitleBar | ImGuiWindowFlags_NoResize | ImGuiWindowFlags_NoMove))
        return;
    float W = ImGui::GetContentRegionAvail().x;
    push(TITLE);
    ImGui::TextUnformatted("Re-roll bot profiles");
    pop();
    muted("15 new bots: new names, clan tags and armor. Each keeps its look from match to match.");
    line(16);
    caption("Experience");
    static const char* EN[2] = { "Original Experience", "Modern Experience" };
    segmented("exp", EN, 2, &rr_experience, W);
    muted(rr_experience == 0 ? "Bot profiles only spawn with original content from each game."
                             : "Bot profiles can spawn with all armor customizations in MCC.");
    ImGui::Dummy(ImVec2(0, px(6)));
    caption("Armor effects");
    segmented("fx", FX_NAME, 4, &rr_effects, W);
    muted("Flaming helmets, lightning storms, hearts, pestilence and other armor effects from each game.");
    ImGui::Dummy(ImVec2(0, px(6)));
    // the Spartan / Elite mix: brain/looks.py elite_share
    caption("Species");
    static const char* SPN[3] = { "Spartans only", "Mixed", "Elites only" };
    segmented("species", SPN, 3, &rr_species, W);
    if (rr_species == 1) {                               // the mix: Spartans to the left, Elites to the right
        ImGui::Dummy(ImVec2(0, px(2)));
        push(SMALL);
        ImGui::PushStyleColor(ImGuiCol_Text, v4(TEXT2));
        ImGui::TextUnformatted("More Spartans");
        const char* rt = "More Elites";
        ImGui::SameLine(W - ImGui::CalcTextSize(rt).x);
        ImGui::TextUnformatted(rt);
        ImGui::PopStyleColor();
        pop();
        push(BODY);
        ImGui::PushStyleColor(ImGuiCol_FrameBg, v4(SURF2));
        ImGui::PushStyleColor(ImGuiCol_FrameBgHovered, v4(HOVER));
        ImGui::PushStyleColor(ImGuiCol_FrameBgActive, v4(HOVER));
        ImGui::PushStyleColor(ImGuiCol_SliderGrab, v4(ACCENT));
        ImGui::PushStyleColor(ImGuiCol_SliderGrabActive, v4(IM_COL32(150, 218, 255, 255)));
        ImGui::PushStyleColor(ImGuiCol_Border, v4(LINE));
        ImGui::PushStyleVar(ImGuiStyleVar_FrameBorderSize, 1.0f);
        ImGui::PushStyleVar(ImGuiStyleVar_GrabMinSize, px(18));
        ImGui::PushStyleVar(ImGuiStyleVar_FramePadding, ImVec2(px(8), px(8)));
        ImGui::SetNextItemWidth(W);
        char fmt[48];
        sprintf_s(fmt, "%d%%%% Elites, %d%%%% Spartans", rr_elite_share, 100 - rr_elite_share);
        if (ImGui::SliderInt("##elites", &rr_elite_share, 0, 100, fmt, ImGuiSliderFlags_AlwaysClamp))
            rr_elite_share = (rr_elite_share + 2) / 5 * 5;   // (in steps of 5)
        ImGui::PopStyleVar(3);
        ImGui::PopStyleColor(6);
        pop();
    }
    muted(rr_species == 0 ? "Every bot is a Spartan." :
          rr_species == 2 ? "Every bot is an Elite in Halo 2, Halo 3, Halo: Reach and Halo 2: Anniversary. Halo: CE and "
                            "Halo 4 bots are always Spartans."
                          : "Halo: CE and Halo 4 bots are always Spartans.");
    line(18);
    push(BODY);
    ImGui::PushStyleColor(ImGuiCol_Text, v4(GOLD));
    ImGui::TextWrapped("Replaces your 15 bots. The bots in your list take the new names.");
    ImGui::PopStyleColor();
    if (rr_failed) {                                     // the file could not be written: say so, keep the dialog
        ImGui::PushStyleColor(ImGuiCol_Text, v4(DANGER));
        ImGui::TextWrapped("Couldn't save the new bots to native\\bin\\bot_profiles.ini. Check that the file isn't "
                           "read-only or open in another program, then re-roll again.");
        ImGui::PopStyleColor();
    }
    pop();
    ImGui::Dummy(ImVec2(0, px(10)));
    float bw = px(170);
    ImGui::SetCursorPosX(ImGui::GetCursorPosX() + W - 2 * bw - px(10));
    if (button("Cancel", SECONDARY, ImVec2(bw, px(44))) || esc_pressed()) {
        rr_failed = false;
        ImGui::CloseCurrentPopup();
    }
    ImGui::SameLine(0, px(10));
    if (button("Re-roll", PRIMARY, ImVec2(bw, px(44)))) {
        rr_failed = !profiles_reroll(g_prof_path, rr_experience, rr_effects, seed(), rr_species, rr_elite_share);
        if (!rr_failed) {
            roster_rename_all(r, seed());
            changed = true;
            ImGui::CloseCurrentPopup();
        }
    }
    ImGui::EndPopup();
}

// ------------------------------------------------------------------ the bot editor (Settings: click a bot's name)
// the player: click a bot in Settings to change its name, clan tag and look in each game; "a professional and neat
// looking pop up menu that changes the bot in realtime and close simply close again". Values are each game's own
// numbers (maps/_looks/<game>.json); Random leaves a field to the bot's look seed. Every change is saved to
// bot_profiles.ini at once (the 4th field, brain/looks.py overrides_of) and worn from the next match.
struct swatch { const char* name; ImU32 col; };
// Halo: CE's colour order (0 White ... 17 Salmon; live: 3 is blue), maps/_looks/ce.json
static const swatch CE_COL[18] = {
    {"White", IM_COL32(226, 226, 226, 255)}, {"Black", IM_COL32(28, 28, 30, 255)}, {"Red", IM_COL32(180, 28, 28, 255)},
    {"Blue", IM_COL32(32, 60, 176, 255)}, {"Gray", IM_COL32(118, 118, 118, 255)}, {"Yellow", IM_COL32(222, 202, 40, 255)},
    {"Green", IM_COL32(40, 140, 40, 255)}, {"Pink", IM_COL32(236, 128, 178, 255)}, {"Purple", IM_COL32(110, 36, 150, 255)},
    {"Cyan", IM_COL32(40, 190, 210, 255)}, {"Cobalt", IM_COL32(52, 82, 150, 255)}, {"Orange", IM_COL32(228, 120, 24, 255)},
    {"Teal", IM_COL32(30, 140, 130, 255)}, {"Sage", IM_COL32(118, 140, 96, 255)}, {"Brown", IM_COL32(102, 66, 32, 255)},
    {"Tan", IM_COL32(190, 160, 110, 255)}, {"Maroon", IM_COL32(110, 22, 32, 255)}, {"Salmon", IM_COL32(236, 140, 120, 255)} };
// Halo 2's 18 (docs/research/13 order), maps/_looks/h2.json
static const swatch H2_COL[18] = {
    {"White", IM_COL32(222, 222, 222, 255)}, {"Steel", IM_COL32(112, 120, 128, 255)}, {"Red", IM_COL32(178, 30, 30, 255)},
    {"Orange", IM_COL32(226, 116, 28, 255)}, {"Gold", IM_COL32(210, 170, 50, 255)}, {"Olive", IM_COL32(110, 112, 46, 255)},
    {"Green", IM_COL32(44, 136, 44, 255)}, {"Sage", IM_COL32(120, 142, 100, 255)}, {"Cyan", IM_COL32(44, 186, 206, 255)},
    {"Teal", IM_COL32(28, 132, 124, 255)}, {"Cobalt", IM_COL32(50, 80, 152, 255)}, {"Blue", IM_COL32(36, 62, 182, 255)},
    {"Violet", IM_COL32(126, 76, 186, 255)}, {"Purple", IM_COL32(100, 34, 140, 255)}, {"Pink", IM_COL32(232, 124, 176, 255)},
    {"Crimson", IM_COL32(142, 18, 40, 255)}, {"Brown", IM_COL32(100, 64, 32, 255)}, {"Tan", IM_COL32(188, 158, 108, 255)} };
// Halo 3's 30 (0 Steel ... 29 Khaki), maps/_looks/h3.json
static const swatch H3_COL[30] = {
    {"Steel", IM_COL32(104, 112, 120, 255)}, {"Silver", IM_COL32(176, 180, 186, 255)}, {"White", IM_COL32(228, 228, 228, 255)},
    {"Red", IM_COL32(176, 26, 26, 255)}, {"Mauve", IM_COL32(176, 112, 138, 255)}, {"Salmon", IM_COL32(234, 134, 116, 255)},
    {"Orange", IM_COL32(226, 114, 24, 255)}, {"Coral", IM_COL32(240, 120, 90, 255)}, {"Peach", IM_COL32(244, 182, 140, 255)},
    {"Gold", IM_COL32(206, 164, 44, 255)}, {"Yellow", IM_COL32(226, 206, 40, 255)}, {"Pale", IM_COL32(232, 222, 168, 255)},
    {"Sage", IM_COL32(120, 142, 98, 255)}, {"Green", IM_COL32(42, 138, 42, 255)}, {"Olive", IM_COL32(108, 110, 44, 255)},
    {"Teal", IM_COL32(28, 132, 124, 255)}, {"Aqua", IM_COL32(64, 196, 186, 255)}, {"Cyan", IM_COL32(42, 186, 208, 255)},
    {"Blue", IM_COL32(36, 62, 180, 255)}, {"Cobalt", IM_COL32(50, 80, 150, 255)}, {"Sapphire", IM_COL32(26, 46, 120, 255)},
    {"Violet", IM_COL32(124, 76, 184, 255)}, {"Orchid", IM_COL32(186, 112, 196, 255)}, {"Lavender", IM_COL32(176, 158, 220, 255)},
    {"Crimson", IM_COL32(140, 18, 38, 255)}, {"Ruby Wine", IM_COL32(104, 20, 44, 255)}, {"Pink", IM_COL32(232, 126, 178, 255)},
    {"Brown", IM_COL32(100, 64, 32, 255)}, {"Tan", IM_COL32(188, 158, 108, 255)}, {"Khaki", IM_COL32(170, 160, 112, 255)} };
// Halo: Reach's 30 and Halo 4's 30 (the same names and order: 0 Steel ... 29 Pale), MCC's own values
// (Data/UI/playercustomization.xml), maps/_looks/reach.json / h4.json
static const swatch REACH_COL[30] = {
    {"Steel", IM_COL32(77, 77, 77, 255)}, {"Silver", IM_COL32(129, 129, 129, 255)}, {"White", IM_COL32(204, 204, 204, 255)},
    {"Brown", IM_COL32(75, 63, 38, 255)}, {"Tan", IM_COL32(126, 89, 64, 255)}, {"Khaki", IM_COL32(176, 141, 121, 255)},
    {"Sage", IM_COL32(103, 106, 89, 255)}, {"Olive", IM_COL32(108, 119, 74, 255)}, {"Drab", IM_COL32(148, 173, 97, 255)},
    {"Forest", IM_COL32(45, 94, 49, 255)}, {"Green", IM_COL32(71, 141, 87, 255)}, {"Sea Foam", IM_COL32(110, 172, 121, 255)},
    {"Teal", IM_COL32(36, 108, 105, 255)}, {"Aqua", IM_COL32(55, 141, 140, 255)}, {"Cyan", IM_COL32(100, 186, 183, 255)},
    {"Blue", IM_COL32(47, 71, 99, 255)}, {"Cobalt", IM_COL32(81, 99, 137, 255)}, {"Ice", IM_COL32(113, 145, 186, 255)},
    {"Violet", IM_COL32(69, 68, 110, 255)}, {"Orchid", IM_COL32(101, 96, 152, 255)}, {"Lavender", IM_COL32(115, 113, 160, 255)},
    {"Maroon", IM_COL32(92, 31, 30, 255)}, {"Brick", IM_COL32(144, 6, 6, 255)}, {"Rose", IM_COL32(197, 115, 117, 255)},
    {"Rust", IM_COL32(116, 36, 27, 255)}, {"Coral", IM_COL32(209, 114, 70, 255)}, {"Peach", IM_COL32(198, 148, 118, 255)},
    {"Gold", IM_COL32(114, 63, 28, 255)}, {"Yellow", IM_COL32(171, 119, 8, 255)}, {"Pale", IM_COL32(214, 200, 128, 255)} };
static const swatch H4_COL[30] = {
    {"Steel", IM_COL32(100, 100, 100, 255)}, {"Silver", IM_COL32(156, 156, 156, 255)}, {"White", IM_COL32(216, 216, 216, 255)},
    {"Brown", IM_COL32(115, 91, 72, 255)}, {"Tan", IM_COL32(159, 126, 106, 255)}, {"Khaki", IM_COL32(204, 169, 148, 255)},
    {"Sage", IM_COL32(117, 124, 89, 255)}, {"Olive", IM_COL32(151, 166, 110, 255)}, {"Drab", IM_COL32(183, 202, 140, 255)},
    {"Forest", IM_COL32(56, 122, 71, 255)}, {"Green", IM_COL32(90, 173, 112, 255)}, {"Sea Foam", IM_COL32(136, 205, 145, 255)},
    {"Teal", IM_COL32(51, 136, 132, 255)}, {"Aqua", IM_COL32(75, 178, 176, 255)}, {"Cyan", IM_COL32(127, 220, 216, 255)},
    {"Blue", IM_COL32(63, 96, 131, 255)}, {"Cobalt", IM_COL32(101, 129, 165, 255)}, {"Ice", IM_COL32(141, 176, 217, 255)},
    {"Violet", IM_COL32(95, 93, 141, 255)}, {"Orchid", IM_COL32(126, 122, 183, 255)}, {"Lavender", IM_COL32(166, 163, 221, 255)},
    {"Maroon", IM_COL32(162, 67, 67, 255)}, {"Brick", IM_COL32(211, 70, 70, 255)}, {"Rose", IM_COL32(234, 148, 148, 255)},
    {"Rust", IM_COL32(180, 88, 43, 255)}, {"Coral", IM_COL32(236, 125, 66, 255)}, {"Peach", IM_COL32(235, 173, 137, 255)},
    {"Gold", IM_COL32(178, 132, 48, 255)}, {"Yellow", IM_COL32(222, 196, 79, 255)}, {"Pale", IM_COL32(241, 235, 123, 255)} };
// Reach's visor tints by the value its Armory writes (appearance +0x2B, maps/_looks/reach_armory.json): the catalog
// lists Default, Silver, Blue, Black, Gold (MCC's order) and writes 0, 1, 2, 4, 3. The shades are by eye
static const float REACH_VISOR[5][3] = { {0.80f, 0.56f, 0.16f}, {0.62f, 0.66f, 0.70f}, {0.18f, 0.40f, 0.86f},
                                         {0.92f, 0.74f, 0.18f}, {0.07f, 0.07f, 0.08f} };
// Halo 4's visors by the appearance byte (+0x4B, its Armory's slot 13): front and rim colour from the game's own
// globals.globals visor colors block (H4EK tags; names default, visor2-9, wetwork, operator, pioneer, pathfinder,
// engineer, stalker, rogue, tracker), checked live on 9 of them (Haven lineup: 0 blue, 5 red, 6 magenta, 7 green,
// 12 a red rim)
static const float H4_VISOR[17][6] = {
    {0.000f, 0.006f, 0.191f, 0.000f, 0.521f, 1.000f},
    {1.000f, 0.000f, 0.000f, 0.539f, 0.058f, 0.000f},
    {1.000f, 1.000f, 1.000f, 0.451f, 0.451f, 0.451f},
    {0.216f, 0.216f, 0.216f, 0.000f, 0.000f, 0.000f},
    {0.000f, 0.319f, 1.000f, 0.000f, 1.000f, 0.665f},
    {0.000f, 0.030f, 0.120f, 1.000f, 0.072f, 0.000f},
    {0.863f, 0.000f, 0.863f, 0.102f, 0.716f, 0.716f},
    {0.063f, 0.091f, 0.003f, 0.000f, 0.880f, 0.188f},
    {0.730f, 0.361f, 0.120f, 0.392f, 0.178f, 0.084f},
    {0.000f, 0.521f, 1.000f, 1.000f, 0.144f, 0.000f},
    {0.939f, 0.266f, 0.305f, 0.319f, 0.000f, 1.000f},
    {0.468f, 0.947f, 1.000f, 0.301f, 0.546f, 0.939f},
    {0.000f, 1.000f, 0.301f, 1.000f, 0.109f, 0.000f},
    {0.701f, 0.262f, 0.905f, 0.000f, 0.381f, 0.617f},
    {1.000f, 1.000f, 0.048f, 0.202f, 0.202f, 0.202f},
    {1.000f, 0.100f, 0.000f, 0.246f, 0.262f, 0.533f},
    {0.301f, 0.000f, 0.680f, 0.175f, 0.000f, 0.651f} };
struct piece { int v; const char* name; };
// Halo 3 pieces by the map's customization table index (maps/_looks/h3.json "original" / "modern" / "names"): the
// Original values first (the count Original Experience shows), then MCC's (Modern only). tools/test_looks.py checks
static const int H3_HELMET_ORIGINAL = 10, H3_SHOULDER_ORIGINAL = 7, H3_CHEST_ORIGINAL = 7;
static const piece H3_HELMET[] = { {0, "Mark VI"}, {1, "CQB"}, {2, "EOD"}, {3, "EVA"}, {4, "Hayabusa"},
    {5, "Mark V"}, {6, "ODST"}, {7, "Recon"}, {9, "Scout"}, {10, "Security"}, {8, "Rogue"}, {11, "Air Assault"},
    {12, "Base"}, {14, "Chameleon"}, {15, "CQB (MCC)"}, {16, "Demo"}, {17, "Dynamo"}, {18, "Enigma"},
    {19, "EOD (MCC)"}, {20, "EVA (MCC)"}, {22, "Ghoul"}, {23, "Gungnir"}, {24, "Hammerhead"}, {25, "Hayabusa (MCC)"},
    {26, "Lancer"}, {27, "Mark V (MCC)"}, {28, "Mark VI (MCC)"}, {29, "Mongoose"}, {30, "Navigator"}, {31, "NiHard"},
    {32, "ODST (MCC)"}, {33, "Oracle"}, {35, "Poncho"}, {36, "Recon (MCC)"}, {37, "Rhino"}, {38, "Rogue (MCC)"},
    {39, "Scout (MCC)"}, {40, "Security (MCC)"}, {41, "Seeker"}, {45, "Warrior"}, {46, "Raven 1"}, {47, "Raven 2"},
    {48, "Raven 3"}, {49, "Raven 4"}, {50, "Belos Heran"}, {51, "Belos Syntrofos"}, {52, "Belos Kerata"},
    {53, "Belos Leontokardos"}, {54, "Belos Stheno"}, {55, "Belos Cerberus"}, {56, "Blackguard Warfeast"},
    {57, "Blackguard Fallen Equerry"}, {58, "Blackguard Forsaken Dragon"}, {59, "Blackguard Carrion Crow"},
    {60, "Blackguard Sorrowful Visage"}, {61, "Blackguard Ashen Crown"}, {62, "Drengr Skald"},
    {63, "Drengr Valdemar"}, {64, "Drengr Dvalinn"}, {65, "Drengr Nari"}, {66, "Drengr Thyra"}, {67, "Drengr Tyr"},
    {68, "SPI"} };
static const piece H3_SHOULDER[] = { {0, "Mark VI"}, {1, "CQB"}, {2, "EOD"}, {3, "EVA"}, {4, "Hayabusa"},
    {5, "Recon"}, {6, "Scout"}, {7, "Security"}, {8, "Air Assault"}, {9, "Base"}, {11, "Chameleon"},
    {12, "CQB (MCC)"}, {13, "Demo"}, {14, "Dynamo"}, {15, "Enigma"}, {16, "EOD (MCC)"}, {17, "EVA (MCC)"},
    {19, "Ghoul"}, {20, "Gungnir"}, {21, "Hammerhead"}, {22, "Hayabusa (MCC)"}, {24, "Lancer"},
    {25, "Mark VI (MCC)"}, {26, "Mongoose"}, {27, "Navigator"}, {28, "NiHard"}, {29, "Oracle"}, {31, "Poncho"},
    {32, "Recon (MCC)"}, {33, "Rhino"}, {34, "Scout (MCC)"}, {35, "Security (MCC)"}, {36, "Seeker"}, {40, "Warrior"},
    {41, "Raven 1"}, {42, "Raven 2"}, {43, "Raven 3"}, {44, "Raven 4"}, {45, "Belos"}, {46, "Belos Ornate"},
    {47, "Belos Lionmarked"}, {48, "Belos Dartbreaker"}, {49, "Belos Valorous"}, {50, "Belos Lionbreaker"},
    {51, "Blackguard"}, {52, "Blackguard Knight of Verent"}, {53, "Blackguard Knight of Venezia"},
    {54, "Blackguard Knight of Aleria"}, {55, "Blackguard Knight of Madrigal"}, {56, "Drengr Oathbound"},
    {57, "Drengr Thornbound"}, {58, "Drengr Fangbound"}, {59, "Drengr Scalebound"}, {60, "SPI"}, {61, "Xbox"} };
static const piece H3_CHEST[] = { {0, "Mark VI"}, {2, "CQB"}, {3, "EOD"}, {4, "EVA"}, {5, "Hayabusa"}, {6, "Katana"},
    {13, "Flaming Head"}, {1, "Flaming Head (2007)"}, {7, "Recon"}, {8, "Scout"}, {10, "Air Assault"}, {11, "Base"},
    {14, "Chameleon"}, {15, "CQB (MCC)"}, {16, "Demo"}, {17, "Dynamo"}, {18, "Enigma"}, {19, "EOD (MCC)"},
    {20, "EVA (MCC)"}, {22, "Ghoul"}, {23, "Gungnir"}, {24, "Hammerhead"}, {25, "Hayabusa (MCC)"},
    {26, "Katana (MCC)"}, {28, "Lancer"}, {29, "Mark VI (MCC)"}, {30, "Mongoose"}, {31, "Navigator"}, {32, "NiHard"},
    {33, "Oracle"}, {35, "Poncho"}, {36, "Recon (MCC)"}, {37, "Rhino"}, {38, "Scout (MCC)"}, {40, "Seeker"},
    {44, "Warrior"}, {45, "Raven 1"}, {46, "Raven 2"}, {47, "Raven 3"}, {48, "Raven 4"}, {49, "Belos Kardia"},
    {50, "Belos Lochagos"}, {51, "Blackguard Banetouched"}, {52, "Blackguard Margrave"}, {53, "Drengr Geirfugl"},
    {54, "Drengr Vinr"}, {55, "SPI"} };
// Halo 3 Modern's other pieces (MCC's order and names; the ones with no geometry left out: maps/_looks/h3.json)
static const piece H3_ARMS[] = { {0, "Mark VI"}, {1, "Air Assault"}, {2, "Base"}, {4, "Chameleon"}, {5, "Demo"},
    {6, "Dynamo"}, {7, "Enigma"}, {9, "Gungnir"}, {10, "Hammerhead"}, {11, "Lancer"}, {12, "Mark VI (MCC)"},
    {13, "NiHard"}, {14, "Oracle"}, {16, "Poncho"}, {17, "Rhino"}, {21, "Warrior"}, {22, "Raven 1"}, {23, "Raven 2"},
    {24, "Raven 3"}, {25, "Raven 4"}, {26, "Belos Chosen"}, {27, "Belos Lionmarked"}, {28, "Blackguard Bane Marked"},
    {29, "Blackguard Sorrow Marked"}, {30, "Drengr Scandalizer Spines"}, {31, "Drengr Truthteller Spines"},
    {32, "SPI"} };
static const piece H3_LEGS[] = { {0, "Mark VI"}, {1, "Air Assault"}, {2, "Base"}, {4, "Chameleon"}, {5, "Demo"},
    {6, "Dynamo"}, {7, "Enigma"}, {9, "Ghoul"}, {10, "Gungnir"}, {11, "Hammerhead"}, {13, "Lancer"},
    {14, "Mark VI (MCC)"}, {15, "Mongoose"}, {16, "Navigator"}, {17, "NiHard"}, {18, "Oracle"}, {20, "Poncho"},
    {21, "Rhino"}, {22, "Seeker"}, {26, "Warrior"}, {27, "Raven 1"}, {28, "Raven 2"}, {29, "Raven 3"},
    {30, "Raven 4"}, {31, "Belos Chosen"}, {32, "Belos Lionmark"}, {33, "Blackguard Far Isle Keep"},
    {34, "Blackguard Tribute Keep"}, {35, "Drengr Far Striders"}, {36, "Drengr Traveller's Striders"}, {37, "SPI"} };
static const piece H3_SUIT[] = { {0, "Mark VI"}, {1, "Base"}, {2, "Tier 1"}, {3, "Tier 2"}, {4, "Gen2 Azure"},
    {5, "Gen2 Gunmetal"}, {6, "Gen2 Maroon"}, {7, "Gen2 Army Green"}, {8, "Gen2 Burnt Ground"}, {9, "Gen2 Cold Mud"},
    {10, "Gen2 Purplex"}, {11, "Gen2 Slate Grey"}, {12, "Gen2 Tinted Smoke"}, {13, "Gen2 Umber"},
    {14, "Gen2 Chainmail"}, {15, "Gen2 Latticeweave"}, {16, "Gen2 Dragonscale"} };
static const piece H3_BACKPACK[] = { {0, "None"}, {1, "Raven 1"}, {2, "Raven 2"}, {3, "Raven 3"}, {4, "Raven 4"},
    {5, "Pulaski"}, {6, "Sword and Board"}, {7, "Katana"}, {15, "Evalach's Lament"}, {14, "Ironburst"},
    {19, "Last Word"}, {13, "Law's End"}, {8, "Lionheart Shield"}, {18, "Oaklimb"}, {17, "Ravenoak"},
    {16, "Reaver's Tooth"}, {10, "Seafoam Trident"}, {12, "Sorrowmourn"}, {11, "Taygetus Dory"},
    {9, "Thessalian Bow"}, {20, "Grunt Plush"}, {21, "Rucksack"}, {22, "Fronkbox"}, {23, "X-Pack"}, {24, "Xbox"},
    {25, "Chiefguy"} };
// Halo 3 Elite pieces by the map's customization table index, one order for the helmet, shoulders and chest
// (+0x4E-0x51: the 2007 sets, MCC's meshes of them, Series 7's) and one for the arms and legs (+0x52, +0x53), named as
// maps/_looks/h3.json "names" (matched by renders against Halopedia's): the Original values first
static const int H3_ELITE_ORIGINAL = 5, H3_ELITE_LIMB_ORIGINAL = 1;
static const piece H3_ELITE[] = { {0, "Combat"}, {2, "Assault"}, {1, "Flight"}, {3, "Ascetic"}, {4, "Commando"},
    {5, "Combat (MCC)"}, {7, "Assault (MCC)"}, {6, "Flight (MCC)"}, {8, "Ascetic (MCC)"}, {9, "Commando (MCC)"},
    {11, "Accord"}, {14, "Artisan"}, {12, "General"}, {10, "Keepward"}, {15, "Ossoona"}, {13, "Scion"},
    {16, "Seneschal"} };
static const piece H3_ELITE_LIMB[] = { {0, "Combat"}, {1, "Combat (MCC)"}, {3, "Accord"}, {6, "Artisan"},
    {4, "General"}, {2, "Keepward"}, {7, "Ossoona"}, {5, "Scion"}, {8, "Seneschal"} };
// Halo: CE's and Halo 3's visors (MCC's: Modern only; Original the default) by the value botcore sends (CE +0x46: the
// unlockdb order from 0x87; Halo 3 +0x6C: MCC-Win64-Shipping.exe's H3_VISOR_* order from 1), named as MCC's menu (maps/
// _looks ce.json / h3.json "names"); each with a colour for the 3D bot, by eye from its name (the animated ones a tint)
static const int CE_VISOR_ORIGINAL = 1, H3_VISOR_ORIGINAL = 1;
static const piece CE_VISOR[] = { {135, "Default"}, {136, "Bronze"}, {137, "Silver"}, {138, "Platinum"},
    {139, "Onyx"}, {140, "Groovy"}, {141, "Spectrum"}, {142, "Pastel Purple"}, {143, "Pastel Blue"},
    {144, "Pastel Green"}, {145, "Pastel Yellow"}, {146, "Pastel Orange"}, {147, "Pastel Red"}, {148, "Dark Purple"},
    {149, "Dark Blue"}, {150, "Dark Green"}, {151, "Dark Yellow"}, {152, "Dark Orange"}, {153, "Dark Red"},
    {154, "Purple"}, {155, "Blue"}, {156, "Green"}, {157, "Yellow"}, {158, "Orange"}, {159, "Red"},
    {160, "Pearlescent Purple"}, {161, "Pearlescent Blue"}, {162, "Pearlescent Green"}, {163, "Pearlescent Yellow"},
    {164, "Pearlescent Orange"}, {165, "Pearlescent Red"} };
static const float CE_VISOR_RGB[31][3] = { {0.80f, 0.56f, 0.16f}, {0.55f, 0.34f, 0.18f}, {0.70f, 0.72f, 0.75f},
    {0.86f, 0.87f, 0.89f}, {0.06f, 0.06f, 0.07f}, {0.72f, 0.28f, 0.78f}, {0.45f, 0.75f, 0.55f},
    {0.72f, 0.62f, 0.90f}, {0.62f, 0.78f, 0.95f}, {0.66f, 0.90f, 0.66f}, {0.95f, 0.92f, 0.60f},
    {0.98f, 0.76f, 0.55f}, {0.95f, 0.62f, 0.62f}, {0.25f, 0.10f, 0.35f}, {0.08f, 0.14f, 0.40f},
    {0.08f, 0.28f, 0.10f}, {0.45f, 0.38f, 0.05f}, {0.50f, 0.22f, 0.04f}, {0.40f, 0.05f, 0.05f},
    {0.50f, 0.20f, 0.75f}, {0.15f, 0.35f, 0.90f}, {0.20f, 0.70f, 0.20f}, {0.95f, 0.85f, 0.10f},
    {0.95f, 0.50f, 0.10f}, {0.85f, 0.12f, 0.12f}, {0.78f, 0.55f, 0.95f}, {0.55f, 0.80f, 1.00f},
    {0.60f, 0.95f, 0.70f}, {1.00f, 0.95f, 0.65f}, {1.00f, 0.75f, 0.45f}, {1.00f, 0.55f, 0.55f} };
static const piece H3_VISOR[] = { {1, "Default"}, {2, "Bronze"}, {3, "Silver"}, {4, "Platinum"}, {5, "Onyx"},
    {6, "Groovy"}, {7, "Spectrum"}, {8, "Pastel Purple"}, {9, "Pastel Blue"}, {10, "Pastel Green"},
    {11, "Pastel Yellow"}, {12, "Pastel Orange"}, {13, "Pastel Red"}, {14, "Dark Purple"}, {15, "Dark Blue"},
    {16, "Dark Green"}, {17, "Dark Yellow"}, {18, "Dark Orange"}, {19, "Dark Red"}, {20, "Purple"}, {21, "Blue"},
    {22, "Green"}, {23, "Yellow"}, {24, "Orange"}, {25, "Red"}, {26, "Pearlescent Purple"}, {27, "Pearlescent Blue"},
    {28, "Pearlescent Green"}, {29, "Pearlescent Yellow"}, {30, "Pearlescent Orange"}, {31, "Pearlescent Red"},
    {32, "Another Sunrise"}, {33, "Cool Depths"}, {34, "Neon"}, {35, "Burn Radius"}, {36, "Nebula"},
    {37, "Old Salt"}, {38, "Patina"}, {39, "Plated"}, {40, "Red Shift"}, {41, "Shadow"}, {42, "Shoreline Sunset"},
    {43, "Tall Tales"}, {44, "Turbulence"}, {45, "Uncontrolled Growth"}, {46, "Creamsicle"}, {47, "80s"},
    {48, "Auntie Dot"}, {49, "Code"}, {50, "Ice"}, {51, "Lava"}, {52, "Prism Scan"}, {53, "Scan Dots"},
    {54, "Scanner"}, {55, "Scrambler"}, {56, "Sunset"}, {57, "Teals"}, {58, "Tracker"} };
static const float H3_VISOR_RGB[58][3] = { {0.80f, 0.56f, 0.16f}, {0.55f, 0.34f, 0.18f}, {0.70f, 0.72f, 0.75f},
    {0.86f, 0.87f, 0.89f}, {0.06f, 0.06f, 0.07f}, {0.72f, 0.28f, 0.78f}, {0.45f, 0.75f, 0.55f},
    {0.72f, 0.62f, 0.90f}, {0.62f, 0.78f, 0.95f}, {0.66f, 0.90f, 0.66f}, {0.95f, 0.92f, 0.60f},
    {0.98f, 0.76f, 0.55f}, {0.95f, 0.62f, 0.62f}, {0.25f, 0.10f, 0.35f}, {0.08f, 0.14f, 0.40f},
    {0.08f, 0.28f, 0.10f}, {0.45f, 0.38f, 0.05f}, {0.50f, 0.22f, 0.04f}, {0.40f, 0.05f, 0.05f},
    {0.50f, 0.20f, 0.75f}, {0.15f, 0.35f, 0.90f}, {0.20f, 0.70f, 0.20f}, {0.95f, 0.85f, 0.10f},
    {0.95f, 0.50f, 0.10f}, {0.85f, 0.12f, 0.12f}, {0.78f, 0.55f, 0.95f}, {0.55f, 0.80f, 1.00f},
    {0.60f, 0.95f, 0.70f}, {1.00f, 0.95f, 0.65f}, {1.00f, 0.75f, 0.45f}, {1.00f, 0.55f, 0.55f},
    {0.95f, 0.55f, 0.35f}, {0.10f, 0.35f, 0.55f}, {0.95f, 0.20f, 0.80f}, {0.95f, 0.35f, 0.10f},
    {0.45f, 0.25f, 0.75f}, {0.40f, 0.55f, 0.65f}, {0.35f, 0.65f, 0.55f}, {0.75f, 0.70f, 0.55f},
    {0.80f, 0.15f, 0.20f}, {0.10f, 0.08f, 0.14f}, {0.90f, 0.55f, 0.35f}, {0.65f, 0.45f, 0.75f},
    {0.30f, 0.40f, 0.70f}, {0.35f, 0.75f, 0.25f}, {1.00f, 0.70f, 0.45f}, {0.85f, 0.30f, 0.85f},
    {0.30f, 0.70f, 0.95f}, {0.15f, 0.85f, 0.35f}, {0.70f, 0.92f, 1.00f}, {0.95f, 0.30f, 0.05f},
    {0.70f, 0.60f, 0.95f}, {0.25f, 0.85f, 0.90f}, {0.20f, 0.90f, 0.60f}, {0.55f, 0.45f, 0.70f},
    {0.98f, 0.50f, 0.20f}, {0.15f, 0.65f, 0.65f}, {0.90f, 0.35f, 0.20f} };
static const piece H2A_SET[] = { {0, "Mark VI"}, {1, "Centurion"}, {2, "Hoplite"} };
// Modern Experience: each H2A piece from the three sets or MCC's 77 (ca_lockout.map's customization table indices,
// maps/_looks/h2a.json "modern" / "names"); forearms and legs share one list
static const piece H2A_HELMET[] = { {0, "Mark VI"}, {1, "Centurion"}, {2, "Hoplite"}, {7, "Orion"},
    {8, "Bioroid Apollyon"}, {9, "Bioroid Israfil"}, {10, "Bioroid Tithonus"}, {11, "Megaframe"},
    {12, "Trooper Falco"}, {13, "Trooper Buzzkill"}, {14, "Trooper Tusk"}, {15, "Trooper Hog"},
    {16, "Trooper Seawing"}, {17, "Trooper Sandsting"}, {18, "Breach Scout"}, {19, "Breach Link"},
    {20, "Panzerdoll Firefox"}, {21, "Panzerdoll Cindersnake"}, {22, "Panzerdoll Infernowolf"},
    {23, "Panzerdoll Blazeknight"} };
static const piece H2A_CHEST[] = { {0, "Mark VI"}, {1, "Centurion"}, {2, "Hoplite"}, {7, "Orion"},
    {8, "Bioroid Alpha Strain"}, {9, "Bioroid Beta Strain"}, {10, "Megaframe"}, {11, "Trooper Falco"},
    {12, "Trooper Buzzkill"}, {13, "Trooper Tusk"}, {14, "Trooper Hog"}, {15, "Trooper Seawing"},
    {16, "Trooper Sandsting"}, {17, "Breach A"}, {18, "Breach B"}, {19, "Panzerdoll Vectorslash"},
    {20, "Panzerdoll Orbcutter"} };
static const piece H2A_SHOULDER[] = { {0, "Mark VI"}, {1, "Centurion"}, {2, "Hoplite"}, {3, "Orion"},
    {4, "Bioroid Eliminator-Type"}, {5, "Bioroid Guardian-Type"}, {6, "Bioroid Meld-Type"},
    {7, "Bioroid Sensor-Type"}, {8, "Megaframe"}, {9, "Trooper Falco"}, {10, "Trooper Buzzkill"},
    {11, "Trooper Tusk"}, {12, "Trooper Hog"}, {13, "Trooper Seawing"}, {14, "Trooper Sandsting"}, {15, "Breach A"},
    {16, "Breach B"}, {17, "Panzerdoll Y-1G Panel"}, {18, "Panzerdoll ZKU-06 Panel"},
    {19, "Panzerdoll Zerotype Panel"}, {20, "Panzerdoll Impulsecrit Panel"} };
static const piece H2A_LIMB[] = { {0, "Mark VI"}, {1, "Centurion"}, {2, "Hoplite"}, {6, "Orion"}, {7, "Bioroid"},
    {8, "Megaframe"}, {9, "Trooper"}, {10, "Panzerdoll"} };
static const piece SPECIES[] = { {0, "Spartan"}, {1, "Elite"} };
#include "look_names.h"                                  // Reach's and Halo 4's Armory, named (tools/look_names.py)

struct ed_game { const char* key; const char* name; const char* art; bool species; };
// the games down the left: the logo (native/bin/art, tools/panel_art.py) and the title
static const ed_game ED_GAMES[6] = { {"ce", "Halo: Combat Evolved", "ce", false}, {"h2", "Halo 2", "h2", true},
    {"h3", "Halo 3", "h3", true}, {"reach", "Halo: Reach", "reach", true}, {"h2a", "Halo 2: Anniversary", "h2a", true},
    {"h4", "Halo 4", "h4", false} };
static int g_edit = -1;                                  // the profile being viewed
static int g_edit_game = 2;                              // ED_GAMES index
static int g_edit_view = -1;                             // Reach: the species shown (-1 its preferred one)
static bool g_edit_failed = false;                       // the last save could not write the file
static char g_edit_name[16], g_edit_clan[6];

static void ed_set(const char* game, const char* field, int v, bool& dirty) {
    char key[40]; sprintf_s(key, "%s.%s", game, field);
    profile_over_set(g_edit, key, v);
    dirty = true;
}
static int ed_over(const char* game, const char* field) {     // set by hand in this view, or -1
    char key[40]; sprintf_s(key, "%s.%s", game, field);
    return profile_over_get(g_edit, key);
}
static int rolled_species(int G) {
    int r = look_get(g_prof[g_edit], ED_GAMES[G].key, false, "rolled_species");
    return r >= 0 ? r : 0;
}
// the species this bot is shown as in a game: its preferred one (set here, else rolled), or Reach's other one
static int ed_species(int G) {
    const char* g = ED_GAMES[G].key;
    int pref = ed_over(g, "species");
    if (pref < 0) pref = look_get(g_prof[g_edit], g, false, G == 2 ? "model" : "species");
    if (pref < 0) pref = 0;
    return G == 3 && g_edit_view >= 0 ? g_edit_view : pref;
}
// whether the bot is shown as the species it did not roll (bot_looks.ini's "other" line)
static bool ed_other(int G) {
    int own = look_get(g_prof[g_edit], ED_GAMES[G].key, false, G == 2 ? "model" : "species");
    return ED_GAMES[G].species && own >= 0 && ed_species(G) != own;
}
// what the bot wears: set by hand, else the roll (as its own species, or as the other one when shown as that)
static int ed_roll(int G, const char* field) { return look_get(g_prof[g_edit], ED_GAMES[G].key, ed_other(G), field); }
static int ed_value(int G, const char* field) {
    int v = ed_over(ED_GAMES[G].key, field);
    return v >= 0 ? v : ed_roll(G, field);
}
// a text field of what it wears (Reach's pieces), the same way
static void ed_text(int G, const char* field, char* out, size_t cap) {
    look_text(g_prof[g_edit], ED_GAMES[G].key, ed_other(G), field, out, cap);
}

// a picker's cell on the page's grid: columns `cw` wide 12 apart, rows of a caption and a box
static void ed_at(ImVec2 r0, int col, int row, float cw) {
    ImGui::SetCursorPos(ImVec2(r0.x + col * (cw + ui::px(12)), r0.y + row * ui::px(72)));
}

// a caption with "Changed" beside it when the value was set by hand
static void ed_caption(const char* label, bool changed) {
    using namespace ui;
    caption(label);
    if (changed) {
        ImGui::SameLine(0, px(8));
        push(SMALL);
        ImGui::PushStyleColor(ImGuiCol_Text, v4(GOLD));
        ImGui::TextUnformatted("CHANGED");
        ImGui::PopStyleColor();
        pop();
    }
}

// a list to pick from, the current value shown (its roll marked "its roll", a hand-set one framed in gold); the value
// picked, or -2 when none was
static int ed_combo(const char* label, const char* id, bool changed, const piece* p, int n, int cur, int roll, float w) {
    using namespace ui;
    int picked = -2;
    ImGui::BeginGroup();                                     // new lines keep to the cell's x
    ed_caption(label, changed);
    char shown[48] = "";
    strcpy_s(shown, g_nlook ? "Not known" : "Loading...");   // (the look file is being made: about a second)
    for (int i = 0; i < n; i++) if (p[i].v == cur) strcpy_s(shown, p[i].name);
    if (cur >= 0 && (!strcmp(shown, "Not known") || !strcmp(shown, "Loading..."))) sprintf_s(shown, "Item %d", cur);
    push(BODY);
    ImGui::SetNextItemWidth(w);
    ImGui::PushStyleColor(ImGuiCol_FrameBg, v4(SURF2));
    ImGui::PushStyleColor(ImGuiCol_FrameBgHovered, v4(HOVER));
    ImGui::PushStyleColor(ImGuiCol_Button, v4(SURF2));
    ImGui::PushStyleColor(ImGuiCol_ButtonHovered, v4(HOVER));
    ImGui::PushStyleColor(ImGuiCol_Border, v4(changed ? alpha(GOLD, 0.7f) : LINE));
    ImGui::PushStyleColor(ImGuiCol_Header, v4(alpha(ACCENT, 0.16f)));
    ImGui::PushStyleColor(ImGuiCol_HeaderHovered, v4(HOVER));
    ImGui::PushStyleVar(ImGuiStyleVar_FrameBorderSize, 1.0f);
    bool open = ImGui::BeginCombo(id, shown, ImGuiComboFlags_HeightLarge);
    if (!open && ImGui::IsItemHovered() && ImGui::CalcTextSize(shown).x > w - ImGui::GetFrameHeight() - px(12))
        ImGui::SetTooltip("%s", shown);                      // (a long name cut off in the box)
    if (open) {
        g_list_frame = ImGui::GetFrameCount();
        for (int i = 0; i < n; i++) {
            char item[64];
            sprintf_s(item, p[i].v == roll ? "%s  (its roll)" : "%s", p[i].name);
            if (ImGui::Selectable(item, cur == p[i].v)) picked = p[i].v;
            if (cur == p[i].v) ImGui::SetItemDefaultFocus();   // (the keys start on what it wears, not at the top)
        }
        ImGui::EndCombo();
    }
    ImGui::PopStyleVar();
    ImGui::PopStyleColor(7);
    pop();
    ImGui::EndGroup();
    return picked;
}

// a piece: the bot's own (its roll, or what was set), the game's items to pick from; picking its roll again
// takes the hand-set value away
static void ed_pieces(const char* label, int G, const char* field, const piece* p, int n, float w, bool& dirty) {
    const char* g = ED_GAMES[G].key;
    int over = ed_over(g, field), roll = ed_roll(G, field), cur = over >= 0 ? over : roll;
    char id[48]; sprintf_s(id, "##%s.%s", g, field);
    int v = ed_combo(label, id, over >= 0, p, n, cur, roll, w);
    if (v != -2) ed_set(g, field, v == roll ? -1 : v, dirty);
}

// a colour from the game's palette: the bot's own framed in gold, its roll marked with a dot; picking its roll
// again takes the hand-set value away
static void ed_colors(const char* label, int G, const char* field, const swatch* pal, int n, float w, bool& dirty) {
    using namespace ui;
    const char* g = ED_GAMES[G].key;
    int over = ed_over(g, field), roll = ed_roll(G, field), cur = over >= 0 ? over : roll;
    char cap[64]; sprintf_s(cap, "%s: %s", label, cur >= 0 && cur < n ? pal[cur].name : g_nlook ? "not known" : "loading...");
    ed_caption(cap, over >= 0);
    float s = px(26), gap = px(5);
    int per = (int)((w + gap) / (s + gap));
    if (per < 1) per = 1;
    ImDrawList* dl = ImGui::GetWindowDrawList();
    ImGui::PushID(field);
    for (int i = 0; i < n; i++) {
        if (i % per) ImGui::SameLine(0, gap);
        ImGui::PushID(i);
        ImVec2 p = ImGui::GetCursorScreenPos();
        ImGui::PushItemFlag(ImGuiItemFlags_NoTabStop, i != (cur >= 0 && cur < n ? cur : 0));   // one Tab stop a row (the
        if (ImGui::InvisibleButton("sw", ImVec2(s, s), ImGuiButtonFlags_EnableNav)) ed_set(g, field, i == roll ? -1 : i, dirty);
        ImGui::PopItemFlag();                                // colour it wears), arrows along it: not 30 Tabs a row
        bool hov = ImGui::IsItemHovered();
        dl->AddRectFilled(p, ImVec2(p.x + s, p.y + s), pal[i].col, px(3));
        dl->AddRect(p, ImVec2(p.x + s, p.y + s), hov ? TEXT : IM_COL32(0, 0, 0, 120), px(3));
        if (i == roll)                                       // its roll: a small dot in the corner
            dl->AddCircleFilled(ImVec2(p.x + s - px(6), p.y + px(6)), px(3.5f), IM_COL32(255, 255, 255, 230));
        if (hov) ImGui::SetTooltip(i == roll ? "%s (its roll)" : "%s", pal[i].name);
        if (cur == i)
            dl->AddRect(ImVec2(p.x - 3, p.y - 3), ImVec2(p.x + s + 3, p.y + s + 3), GOLD, px(4), 0, 2.0f);
        if (focus_shown())
            dl->AddRect(ImVec2(p.x - 5, p.y - 5), ImVec2(p.x + s + 5, p.y + s + 5), FOCUS, px(5), 0, 2.0f);
        ImGui::PopID();
    }
    ImGui::PopID();
    ImGui::Dummy(ImVec2(0, px(4)));
}

// the 3D bot (native/bin/models/<game>_<spartan|elite>.mdl from the games' own meshes, tools/make_models.py): Halo: CE
// and Halo 2 in their colours, Halo 3 with its pieces too (maps/_looks/h3.json item numbers -> the model's permutation
// names; Original Experience wears the 2007 "legacy_" meshes, Modern MCC's "mp_" ones), H2A by its customization table's
// sets, Reach and Halo 4 with the pieces their own Armory roll puts on the bot (bot_looks.ini "pieces")
static ImVec4 pal_rgb(const swatch* pal, int n, int i) { return ImGui::ColorConvertU32ToFloat4(pal[i >= 0 && i < n ? i : 0].col); }
static ImVec4 h3_rgb(int i) { return pal_rgb(H3_COL, 30, i); }
static unsigned long long ed_model(bots_panel_env* e, int G, int sp, float yaw, int w, int h) {
    if (!e || !e->render_model || !e->model_has) return 0;
    wchar_t path_rel[64];
    swprintf_s(path_rel, L"models\\%S_%S.mdl", ED_GAMES[G].key, sp == 1 ? "elite" : "spartan");
    wchar_t path[300];
    bin_file(path, 300, path_rel);
    if (GetFileAttributesW(path) == INVALID_FILE_ATTRIBUTES) {   // beside the program (the preview)
        GetModuleFileNameW(nullptr, path, 300);
        if (wchar_t* sl = wcsrchr(path, L'\\')) sl[1] = 0;
        wcscat_s(path, path_rel);
    }
    static char buf[24][40];
    mv_pick picks[24];
    int n = 0;
    auto add = [&](const char* region, const char* perm) {
        if (n < 24) { strcpy_s(buf[n], perm); picks[n] = { region, buf[n] }; n++; }
    };
    mv_colors c{};
    ImVec4 pri, sec, det;
    if (G == 0) pri = sec = det = pal_rgb(CE_COL, 18, ed_value(G, "color"));
    else if (G == 1) { pri = pal_rgb(H2_COL, 18, ed_value(G, "primary")); sec = det = pal_rgb(H2_COL, 18, ed_value(G, "secondary")); }
    else if (G >= 3) {                                       // (H2A's 30 are Halo 4's: MCC lists them in linear light)
        const swatch* P = G == 3 ? REACH_COL : H4_COL;
        pri = pal_rgb(P, 30, ed_value(G, "primary")); sec = det = pal_rgb(P, 30, ed_value(G, "secondary"));
    }
    else { pri = h3_rgb(ed_value(G, "primary")); sec = h3_rgb(ed_value(G, "secondary")); det = h3_rgb(ed_value(G, "tertiary")); }
    float under[3] = { 0.07f + 0.10f * sec.x, 0.07f + 0.10f * sec.y, 0.08f + 0.10f * sec.z };
    memcpy(c.armor, &pri, 12); memcpy(c.undersuit, under, 12);
    float visor[3] = { 0.80f, 0.56f, 0.16f }, skin[3] = { 0.24f, 0.21f, 0.19f };
    int tint = G == 3 ? ed_value(G, "visor") : -1;           // Reach: the visor tint its Armory roll gave it
    if (tint >= 0 && tint < 5) memcpy(visor, REACH_VISOR[tint], 12);
    // Halo: CE / Halo 3 Spartan: MCC's visor (Modern; Original wears the default whatever was set by hand)
    int cev = G == 0 ? (g_experience ? ed_value(G, "visor") - 135 : 0) : -1;
    int h3v = G == 2 && sp == 0 ? (g_experience ? ed_value(G, "visor") - 1 : 0) : -1;
    if (cev >= 0 && cev < COUNT(CE_VISOR)) memcpy(visor, CE_VISOR_RGB[cev], 12);
    if (h3v >= 0 && h3v < COUNT(H3_VISOR)) memcpy(visor, H3_VISOR_RGB[h3v], 12);
    memcpy(c.visor, visor, 12); memcpy(c.lights, &det, 12); memcpy(c.skin, skin, 12); memcpy(c.secondary, &sec, 12);
    int h4v = G == 5 ? ed_value(G, "visor") : -1;           // Halo 4: its own visor, front and rim
    if (h4v >= 0 && h4v < 17) {
        memcpy(c.visor, H4_VISOR[h4v], 12); memcpy(c.visor_rim, H4_VISOR[h4v] + 3, 12); c.rim_on = true;
    }
    static char pcs[640];                                    // (Reach, Halo 4: picks point into it until the draw)
    if (G == 0) {                                            // Halo: CE: one cyborg, its colour
        add("cyborg", "__base");
    } else if (G >= 2) {                                     // Halo 3, Reach, H2A, Halo 4: the model's pieces this bot
        // wears, listed as region:permutation by brain/looks.py export_view from the game's customization table (its
        // maps/_looks/<game>_armory.json): Reach's and Halo 4's as botcore rolls them from the game's own Armory
        // (worked out offline, brain/armory.py), Halo 3's and H2A's from their look bytes, the table's indices (both
        // species, MCC's pieces too). Reach's instanced flair (shoulder pads, pouches, helmet attachments) is packed
        // under its piece (tools/model_pack2.py). A piece the map's model lacks (some of MCC's Halo 3 pieces): the
        // region's base piece. None listed (a Halo 4 roll whose pieces botcore has not logged yet): the logo, not a guess
        ed_text(G, "pieces", pcs, sizeof pcs);
        char* next = nullptr;
        for (char* t = strtok_s(pcs, ",", &next); t; t = strtok_s(nullptr, ",", &next)) {
            char* c = strchr(t, ':');
            if (!c) continue;
            *c = 0;
            if (e->model_has(path, t, c + 1)) add(t, c + 1);
            else if (e->model_has(path, t, "base")) add(t, "base");
        }
        if (!n) return 0;
    } else if (G == 1) {                                     // Halo 2: no armor choices, the species and colours
        if (sp == 1) { add("body", "base"); add("head", "base"); add("helmet", "base"); add("lights", "base"); }
        else { add("arms", "base"); add("body", "base"); add("helmet", "base"); add("legs", "base"); }
    }
    return e->render_model(path, picks, n, c, yaw, w, h);
}

// the bot in a game: its 3D model where one is packed (Halo 3), else a picture taken in game
// (native/bin/looks/<name>_<game>_<spartan|elite>.rgba), else the game's logo; drag to turn it. Reach: a button in the
// top right corner shows its other species (Invasion)
static bots_panel_env* g_env;
static float g_yaw = 0.35f;
static ULONGLONG g_turn_after = ~0ULL;   // a backpack picked: the bot turned round to show it once its look comes in (a
                                         // bot_looks.ini newer than this one), not before with the old one on its back
// how close the view frames the bot: 1 the upper torso, Halo 3's armory view, the default, 0 the whole figure
static float g_zoom = 1.0f;
void bots_panel_debug_zoom(float z) { g_zoom = z; }                  // native/panel_preview.cpp
// ------------------------------------------------------------------ the profile view's armor effects. Drawn over the
// 3D bot, on its head (mv_head), with the game's own particle bitmaps (tools/fx_art.py: what Reach's
// objects\characters\spartans\fx\armor_fx effects draw), each particle worked out from the clock alone: Legendary's
// fire (Halo 3's Flaming Head the same), Eternal's (Reach's blue, Halo 4's purple), Heart Attack's winged hearts,
// Pestilence's gas, flies and drips, Inclement Weather's storm cloud and lightning, Birthday Party's confetti (in game
// only on a headshot death; shown bursting)
enum { FX_NONE, FX_FIRE, FX_FIRE_BLUE, FX_FIRE_PURPLE, FX_HEARTS, FX_PESTILENCE, FX_STORM, FX_BIRTHDAY };
static double g_fx_time = -1;                            // the effects' clock (-1: ImGui's); native/panel_preview.cpp
void bots_panel_debug_time(double t) { g_fx_time = t; }  // --time holds it for still renders
static float fx_hash(int i, int k) {                     // 0..1 from two integers
    uint32_t x = (uint32_t)i * 2654435761u ^ (uint32_t)k * 2246822519u;
    x ^= x >> 15; x *= 2654435761u; x ^= x >> 13;
    return (x & 0xFFFFFF) / 16777216.0f;
}
// one sprite: centred at c, w by h, turned by rot (radians), uv0-uv1 of its texture, tinted
static void fx_sprite(ImDrawList* dl, ImTextureID id, ImVec2 c, float w, float h, float rot, ImVec2 uv0, ImVec2 uv1, ImU32 col) {
    float cs = cosf(rot), sn = sinf(rot), hw = w * 0.5f, hh = h * 0.5f;
    float dx[4] = { -hw, hw, hw, -hw }, dy[4] = { -hh, -hh, hh, hh };
    ImVec2 p[4];
    for (int k = 0; k < 4; k++) p[k] = ImVec2(c.x + dx[k] * cs - dy[k] * sn, c.y + dx[k] * sn + dy[k] * cs);
    dl->AddImageQuad(id, p[0], p[1], p[2], p[3], uv0, ImVec2(uv1.x, uv0.y), uv1, ImVec2(uv0.x, uv1.y), col);
}
static ImU32 fx_col(int r, int g, int b, float a) { return IM_COL32(r, g, b, (int)(255 * (a < 0 ? 0 : a > 1 ? 1 : a))); }
// a particle's place in its life: life L seconds, slot i of n spread over it; p 0..1, cyc the life it is in
static float fx_phase(double t, float L, int i, int n, int& cyc) {
    double u = t / L + (double)i / n + fx_hash(i, 77) * 0.37;
    cyc = (int)floor(u);
    return (float)(u - cyc);
}
static void ed_fx(ImDrawList* dl, ImVec2 a, ImVec2 b, int kind) {
    if (kind == FX_NONE || !g_env || !g_env->model_head) return;
    float hd[3];
    g_env->model_head(hd);
    if (hd[2] <= 0) return;
    ImVec2 H(a.x + hd[0], a.y + hd[1]);
    float R = hd[2];
    double t = g_fx_time >= 0 ? g_fx_time : ImGui::GetTime();
    auto tex = [](const char* name) {
        char k[32]; sprintf_s(k, "fx:%s", name);
        wchar_t f[64]; swprintf_s(f, L"art\\fx_%S.rgba", name);
        return texture(k, f);
    };
    const ImVec2 U0(0, 0), U1(1, 1);
    dl->PushClipRect(a, b, true);
    if (kind == FX_FIRE || kind == FX_FIRE_BLUE || kind == FX_FIRE_PURPLE) {   // head_fire: the helmet engulfed
        const tex_entry* fire = tex(kind == FX_FIRE ? "fire" : kind == FX_FIRE_BLUE ? "fire_blue" : "fire_purple");
        const tex_entry* glow = tex("flash");
        if (glow) {
            ImU32 gc = kind == FX_FIRE ? fx_col(255, 130, 40, 0.30f) : kind == FX_FIRE_BLUE ? fx_col(80, 110, 255, 0.30f)
                                                                     : fx_col(190, 60, 255, 0.30f);
            fx_sprite(dl, glow->id, ImVec2(H.x, H.y - R * 0.4f), R * 3.6f, R * 3.6f, 0, U0, U1, gc);
        }
        // tongues licking up off the helmet: born on its surface, stretched tall, rising and narrowing as they burn out;
        // the helmet shows through them
        for (int i = 0; fire && i < 34; i++) {
            int cyc;
            float p = fx_phase(t, 0.8f, i, 34, cyc);
            float th = (fx_hash(i, cyc) * 1.25f - 0.125f) * 3.14159f, rs = fx_hash(i, cyc + 433), rr = fx_hash(i, cyc + 71);
            float bx = H.x + cosf(th) * R * 0.78f, by = H.y - sinf(th) * R * 0.85f + R * 0.15f;   // (the upper shell)
            float x = bx + (H.x - bx) * 0.3f * p + sinf(p * 7.0f + rr * 6.283f) * R * 0.09f;
            float y = by - p * R * 2.4f - p * p * R * 0.8f;
            float s = R * (0.62f + 0.45f * rs) * (1.0f - 0.55f * p);
            float al = (p < 0.1f ? p / 0.1f : 1.0f) * powf(1.0f - p, 1.4f) * 0.8f;
            fx_sprite(dl, fire->id, ImVec2(x, y), s * 0.85f, s * 1.75f, (rr - 0.5f) * 0.5f + sinf(p * 5 + rr * 9) * 0.2f, U0, U1,
                      fx_col(255, 255, 255, al));
        }
    } else if (kind == FX_HEARTS) {                          // cupid_heart: winged hearts rising off the head
        const tex_entry* hearts = tex("hearts");
        for (int i = 0; hearts && i < 7; i++) {
            int cyc;
            float p = fx_phase(t, 2.4f, i, 7, cyc);
            float rx = fx_hash(i, cyc) * 2 - 1, rr = fx_hash(i, cyc + 5);
            float x = H.x + rx * R * 0.85f + sinf(p * 9.0f + rr * 6.283f) * R * 0.25f;
            float y = H.y - R * 0.5f - p * R * 2.8f;
            float pop = p < 0.12f ? 0.4f + 0.6f * p / 0.12f : 1.0f;
            float s = R * 1.0f * pop * (0.8f + 0.35f * rr);
            int cell = (int)(rr * 3) % 3;                    // (three hearts on the sheet)
            ImVec2 u0(cell == 1 ? 0.5f : 0.0f, cell == 2 ? 0.5f : 0.0f);
            fx_sprite(dl, hearts->id, ImVec2(x, y), s, s, sinf(p * 7 + rr * 6.283f) * 0.25f, u0, ImVec2(u0.x + 0.5f, u0.y + 0.5f),
                      fx_col(255, 255, 255, p > 0.7f ? (1 - p) / 0.3f : 1.0f));
        }
    } else if (kind == FX_PESTILENCE) {                      // pestilence_gas, bugs, pestilence_drip from the shoulders
        const tex_entry* smoke = tex("smoke");
        const tex_entry* bugs = tex("bugs");
        for (int i = 0; smoke && i < 10; i++) {
            int cyc;
            float p = fx_phase(t, 3.2f, i, 10, cyc);
            float th = fx_hash(i, cyc) * 6.283f + p * 1.2f, rr = fx_hash(i, cyc + 3);
            float x = H.x + cosf(th) * R * (0.9f + 0.6f * rr), y = H.y + R * 0.6f + sinf(th) * R * 0.5f - p * R * 0.7f;
            fx_sprite(dl, smoke->id, ImVec2(x, y), R * (1.5f + 1.0f * p), R * (1.5f + 1.0f * p), th, U0, U1,
                      fx_col(118, 168, 38, sinf(p * 3.14159f) * 0.5f));
        }
        for (int i = 0; i < 8; i++) {
            int cyc;
            float p = fx_phase(t, 1.3f, i, 8, cyc);
            float side = (i & 1) ? 1.0f : -1.0f, rx = fx_hash(i, cyc) - 0.5f;
            float x = H.x + side * R * (1.15f + rx * 0.5f), y = H.y + R * 1.1f + p * p * R * 2.0f;
            dl->AddCircleFilled(ImVec2(x, y), R * 0.075f * (1 + p * 0.5f), fx_col(150, 205, 50, (p < 0.15f ? p / 0.15f : (1 - p) / 0.85f) * 0.85f), 10);
        }
        for (int i = 0; bugs && i < 14; i++) {
            float sp = 1.6f + fx_hash(i, 1) * 1.8f, tt = (float)t * sp + fx_hash(i, 2) * 6.283f;
            float x = H.x + cosf(tt) * R * (1.0f + 0.6f * fx_hash(i, 3)) + sinf(tt * 2.7f) * R * 0.3f;
            float y = H.y + R * 0.2f + sinf(tt * 1.3f) * R * 0.8f + cosf(tt * 3.1f) * R * 0.18f;
            float fr = (((int)(t * 30) + i) & 1) ? 0.5f : 0.0f;   // (two wing frames)
            fx_sprite(dl, bugs->id, ImVec2(x, y), R * 0.42f, R * 0.42f, tt, ImVec2(fr, 0), ImVec2(fr + 0.5f, 1), IM_COL32(255, 255, 255, 255));
        }
    } else if (kind == FX_STORM) {                           // thunder_cloud above, lightning to the head, crackle
        const tex_entry* smoke = tex("smoke");
        const tex_entry* flash = tex("flash");
        ImVec2 C(H.x, H.y - R * 2.1f);                       // (kept inside the card up close)
        if (C.y < a.y + R * 0.7f) C.y = a.y + R * 0.7f;
        if (C.y > H.y - R * 1.3f) C.y = H.y - R * 1.3f;
        double lu = t / 1.7;                                 // a strike every 1.7 s, some with a second flash
        int lc = (int)floor(lu);
        float lp = (float)(lu - lc);
        bool strike = lp < 0.09f || (fx_hash(lc, 3) > 0.5f && lp > 0.15f && lp < 0.21f);
        // the cloud: a dome of puffs, dark and lit from inside by a strike, a flatter darker belly under it
        for (int i = 0; smoke && i < 16; i++) {
            float u = (fx_hash(i, 11) * 2 - 1), belly = i >= 12 ? 1.0f : 0.0f;
            float ox = u * R * (1.35f - 0.35f * belly), oy = belly * R * 0.3f - (1.0f - u * u) * R * 0.45f * (1 - belly)
                       + (fx_hash(i, 12) - 0.5f) * R * 0.2f;
            float dr = sinf((float)t * (0.45f + 0.4f * fx_hash(i, 13)) + i) * R * 0.08f;
            float sz = R * (belly ? 1.8f : 1.4f + 0.9f * fx_hash(i, 14));
            int lit = strike ? 75 : 0, base = belly ? 30 : 44 + (int)(26 * (1 - fx_hash(i, 16)));
            fx_sprite(dl, smoke->id, ImVec2(C.x + ox + dr, C.y + oy), sz, sz * (belly ? 0.45f : 0.78f), fx_hash(i, 15) * 0.8f - 0.4f,
                      U0, U1, IM_COL32(base + lit, base + 4 + lit, base + 16 + lit, belly ? 215 : 250));
        }
        if (strike) {                                        // the bolt: a jagged line, glowing, with a branch
            ImVec2 pt[9];
            pt[0] = ImVec2(C.x + (fx_hash(lc, 5) - 0.5f) * R * 0.9f, C.y + R * 0.35f);
            pt[8] = ImVec2(H.x + (fx_hash(lc, 6) - 0.5f) * R * 0.6f, H.y - R * 0.9f);
            for (int k = 1; k < 8; k++)
                pt[k] = ImVec2(pt[0].x + (pt[8].x - pt[0].x) * k / 8 + (fx_hash(lc, 7 + k) - 0.5f) * R * 0.42f,
                               pt[0].y + (pt[8].y - pt[0].y) * k / 8);
            ImVec2 br[4] = { pt[3], {}, {}, {} };            // (a fork off the third kink)
            for (int k = 1; k < 4; k++)
                br[k] = ImVec2(br[k - 1].x + (fx_hash(lc, 20 + k) > 0.5f ? 1 : -1) * R * (0.18f + 0.1f * fx_hash(lc, 30 + k)),
                               br[k - 1].y + R * (0.16f + 0.08f * fx_hash(lc, 40 + k)));
            float th = R * 0.05f > 1.5f ? R * 0.05f : 1.5f;
            dl->AddPolyline(pt, 9, IM_COL32(120, 170, 255, 70), 0, th * 6);
            dl->AddPolyline(pt, 9, IM_COL32(170, 205, 255, 150), 0, th * 2.6f);
            dl->AddPolyline(pt, 9, IM_COL32(245, 250, 255, 255), 0, th);
            dl->AddPolyline(br, 4, IM_COL32(150, 190, 255, 110), 0, th * 2.2f);
            dl->AddPolyline(br, 4, IM_COL32(235, 245, 255, 230), 0, th * 0.8f);
            if (flash) {
                fx_sprite(dl, flash->id, C, R * 3.6f, R * 2.4f, 0, U0, U1, fx_col(160, 195, 255, 0.5f));
                fx_sprite(dl, flash->id, pt[8], R * 1.5f, R * 1.5f, 0, U0, U1, fx_col(150, 190, 255, 0.55f));
            }
        }
        for (int i = 0; i < 6; i++) {                        // electric_crackle: short arcs flickering on the helmet
            int fr = (int)(t * 16) + i * 7;
            if (fx_hash(i, fr) < 0.5f) continue;
            float a0 = fx_hash(i, fr + 1) * 6.283f, r0 = R * (0.8f + 0.18f * fx_hash(i, fr + 2));
            ImVec2 arc[5];
            for (int k = 0; k < 5; k++) {
                float an = a0 + k * 0.16f, rk = r0 + ((k & 1) ? R * 0.1f : -R * 0.04f);
                arc[k] = ImVec2(H.x + cosf(an) * rk, H.y + sinf(an) * rk * 0.95f);
            }
            dl->AddPolyline(arc, 5, IM_COL32(120, 170, 255, 90), 0, 4.0f);
            dl->AddPolyline(arc, 5, IM_COL32(200, 230, 255, 240), 0, 1.4f);
        }
    } else if (kind == FX_BIRTHDAY) {                        // confetti: a burst off the head every 2.6 s
        static const ImU32 CONF[8] = { IM_COL32(235, 60, 60, 255), IM_COL32(250, 210, 50, 255), IM_COL32(70, 200, 90, 255),
            IM_COL32(60, 140, 240, 255), IM_COL32(240, 110, 200, 255), IM_COL32(60, 220, 230, 255), IM_COL32(250, 150, 40, 255),
            IM_COL32(240, 240, 240, 255) };
        const float LP = 2.6f;
        int cyc = (int)floor(t / LP);
        float tau = (float)(t - cyc * (double)LP), p = tau / LP;
        const tex_entry* flash = tex("flash");
        if (flash && tau < 0.18f) fx_sprite(dl, flash->id, ImVec2(H.x, H.y - R * 0.5f), R * 2.6f, R * 2.6f, 0, U0, U1, fx_col(255, 240, 200, 0.5f * (1 - tau / 0.18f)));
        for (int i = 0; i < 48; i++) {
            float vx = (fx_hash(i, cyc) * 2 - 1) * R * 2.4f, vy = -(1.2f + 1.6f * fx_hash(i, cyc + 1)) * R * 1.7f;
            float x = H.x + vx * tau * (1 - 0.22f * tau) + sinf(tau * 9 + i) * R * 0.08f * tau;
            float y = H.y - R * 0.6f + vy * tau + 0.5f * R * 4.0f * tau * tau;
            float rot = tau * (4 + 6 * fx_hash(i, cyc + 2)) + i, w = R * 0.16f;
            float h = R * 0.08f * fabsf(cosf(tau * 7 + i)) + R * 0.02f;   // (a strip turning over as it falls)
            float cs = cosf(rot), sn = sinf(rot);
            ImVec2 q[4];
            float dx[4] = { -w, w, w, -w }, dy[4] = { -h, -h, h, h };
            for (int k = 0; k < 4; k++) q[k] = ImVec2(x + (dx[k] * cs - dy[k] * sn) * 0.5f, y + (dx[k] * sn + dy[k] * cs) * 0.5f);
            ImU32 c = CONF[i % 8];
            float al = p < 0.72f ? 1.0f : (1 - p) / 0.28f;
            dl->AddQuadFilled(q[0], q[1], q[2], q[3], (c & 0x00FFFFFF) | ((ImU32)(255 * al) << 24));
        }
    }
    dl->PopClipRect();
}

// the effect the shown bot wears: Reach's Spartan and Halo 4 (Modern: MCC's) by their armor_effect value, Halo 3's
// Spartan by its Flaming Head chest
static int ed_fx_kind(int G, int sp) {
    static const int HR[7] = { FX_NONE, FX_FIRE, FX_FIRE_BLUE, FX_HEARTS, FX_PESTILENCE, FX_STORM, FX_BIRTHDAY };
    static const int H4[7] = { FX_NONE, FX_FIRE, FX_FIRE_PURPLE, FX_HEARTS, FX_PESTILENCE, FX_STORM, FX_BIRTHDAY };
    if (G == 2 && sp == 0) { int ch = ed_value(G, "chest"); return ch == 13 || (ch == 1 && g_experience) ? FX_FIRE : FX_NONE; }
    int v = G == 3 && sp == 0 ? ed_value(G, "armor_effect") : G == 5 && g_experience ? ed_value(G, "armor_effect") : 0;
    return v > 0 && v < 7 ? (G == 3 ? HR[v] : H4[v]) : FX_NONE;
}

static void ed_preview(ImVec2 p0, ImVec2 size, int G, bool& dirty) {
    using namespace ui;
    (void)dirty;
    ImDrawList* dl = ImGui::GetWindowDrawList();
    const char* g = ED_GAMES[G].key;
    // the stage: a lit blue room filling the picture, framed in the bot's primary colour like Halo 3's armory card,
    // its emblem in a ring at the bottom right (brain/emblems.py, emblems/<name>.rgba)
    ImVec2 p1(p0.x + size.x, p0.y + size.y);
    const tex_entry* stage = texture("art:stage", L"art\\stage.rgba");
    if (stage && stage->id) {
        float k = size.x / stage->w > size.y / stage->h ? size.x / stage->w : size.y / stage->h;   // cover, centred
        float uw = size.x / (stage->w * k), uh = size.y / (stage->h * k);
        dl->AddImage(stage->id, p0, p1, ImVec2(0.5f - uw * 0.5f, 0.5f - uh * 0.5f), ImVec2(0.5f + uw * 0.5f, 0.5f + uh * 0.5f));
    } else {
        dl->AddRectFilledMultiColor(p0, p1, IM_COL32(16, 34, 50, 255), IM_COL32(16, 34, 50, 255), IM_COL32(6, 14, 22, 255),
                                    IM_COL32(6, 14, 22, 255));
    }
    ImVec4 frame = G == 0 ? pal_rgb(CE_COL, 18, ed_value(G, "color")) : G == 1 ? pal_rgb(H2_COL, 18, ed_value(G, "primary"))
                 : G == 3 ? pal_rgb(REACH_COL, 30, ed_value(G, "primary")) : G >= 4 ? pal_rgb(H4_COL, 30, ed_value(G, "primary"))
                 : h3_rgb(ed_value(G, "primary"));
    frame.w = 1.0f;
    ImU32 fc = ImGui::ColorConvertFloat4ToU32(frame);
    ImU32 fcd = ImGui::ColorConvertFloat4ToU32(ImVec4(frame.x * 0.55f, frame.y * 0.55f, frame.z * 0.55f, 1.0f));
    float bw = px(7), band = px(64);
    dl->AddRectFilledMultiColor(ImVec2(p0.x, p1.y - band), p1, IM_COL32(0, 0, 0, 0), IM_COL32(0, 0, 0, 0),
                                IM_COL32(0, 0, 0, 200), IM_COL32(0, 0, 0, 200));   // the name plate's shade
    auto bar = [&](float x0, float y0, float x1, float y1, ImU32 col) { dl->AddRectFilled(ImVec2(x0, y0), ImVec2(x1, y1), col); };
    bar(p0.x, p0.y, p1.x, p0.y + bw, fc); bar(p0.x, p1.y - bw, p1.x, p1.y, fc);            // the frame: four bars
    bar(p0.x, p0.y, p0.x + bw, p1.y, fc); bar(p1.x - bw, p0.y, p1.x, p1.y, fc);
    float th = px(1.5f);                                                                    // its inner line, darker
    bar(p0.x + bw, p0.y + bw, p1.x - bw, p0.y + bw + th, fcd); bar(p0.x + bw, p1.y - bw - th, p1.x - bw, p1.y - bw, fcd);
    bar(p0.x + bw, p0.y + bw, p0.x + bw + th, p1.y - bw, fcd); bar(p1.x - bw - th, p0.y + bw, p1.x - bw, p1.y - bw, fcd);
    int sp = ED_GAMES[G].species ? ed_species(G) : 0;
    const char* spn = sp == 1 ? "elite" : "spartan";
    float pad = px(12), top = px(56);
    ImVec2 a(p0.x + pad, p0.y + top), b(p0.x + size.x - pad, p0.y + size.y - px(76));
    // turn it: drag across the picture, or the arrow keys while it has focus; zoom: the mouse wheel over it, or the up
    // and down arrows (scrolling down shows more of it, up comes back to the torso)
    ImGui::SetCursorScreenPos(a);
    nav_button("turn", ImVec2(b.x - a.x, b.y - a.y));
    bool keyed = focus_shown();                              // (its hint then names the keys)
    ImGuiIO& io = ImGui::GetIO();
    if (ImGui::IsItemActive()) g_yaw += io.MouseDelta.x * 0.012f;
    if (ImGui::IsItemHovered() && io.MouseWheel != 0.0f) g_zoom += io.MouseWheel * 0.2f;
    if (ImGui::IsItemFocused()) {                            // (its arrows turn and zoom it: not keyboard navigation's)
        static const ImGuiKey ARROWS[] = { ImGuiKey_LeftArrow, ImGuiKey_RightArrow, ImGuiKey_UpArrow, ImGuiKey_DownArrow };
        for (ImGuiKey k : ARROWS) ImGui::SetKeyOwner(k, ImGui::GetItemID());
        if (ImGui::IsKeyDown(ImGuiKey_LeftArrow)) g_yaw -= 2.0f * io.DeltaTime;
        if (ImGui::IsKeyDown(ImGuiKey_RightArrow)) g_yaw += 2.0f * io.DeltaTime;
        if (ImGui::IsKeyDown(ImGuiKey_UpArrow)) g_zoom += 1.5f * io.DeltaTime;
        if (ImGui::IsKeyDown(ImGuiKey_DownArrow)) g_zoom -= 1.5f * io.DeltaTime;
    }
    g_zoom = g_zoom < 0 ? 0 : g_zoom > 1 ? 1 : g_zoom;
    if (ImGui::IsItemHovered()) ImGui::SetMouseCursor(ImGuiMouseCursor_ResizeEW);
    if (g_env && g_env->model_zoom) g_env->model_zoom(g_zoom);
    if (g_turn_after != ~0ULL && g_look_mtime != g_turn_after) { g_yaw = 2.6f; g_turn_after = ~0ULL; }   // its back;
    if (g_turn_after != ~0ULL && GetTickCount64() - g_look_asked > 3000) g_turn_after = ~0ULL;   // none came: not later
    int fx = ed_fx_kind(G, sp);                              // room above the head for its armor effect
    if (g_env && g_env->model_headroom)
        g_env->model_headroom(fx == FX_STORM ? 0.42f : fx == FX_NONE || fx == FX_PESTILENCE ? 0.0f : 0.22f);
    unsigned long long mv = ed_model(g_env, G, sp, g_yaw, (int)(b.x - a.x), (int)(b.y - a.y));
    wchar_t file[128]; swprintf_s(file, L"looks\\%S_%S_%S.rgba", g_prof[g_edit], g, spn);
    char key[64]; sprintf_s(key, "look:%s:%s:%s", g_prof[g_edit], g, spn);
    const tex_entry* t = mv ? nullptr : texture(key, file);
    if (mv) {                                                // the 3D bot, drawn this frame, its armor effect on it;
        dl->AddImage((ImTextureID)mv, a, b);                 // the hint under its feet
        ed_fx(dl, a, b, fx);
        push(SMALL);
        const char* hint = keyed ? "Arrows to turn and zoom" : "Drag to turn, scroll to zoom";   // (at the left: the
                                                                                              // emblem ring the right)
        dl->AddText(ImVec2(a.x + px(10), b.y + px(2)), TEXT3, hint);
        pop();
    } else if (t && t->id) {                                 // the picture, fitted, centred
        float k = (b.x - a.x) / t->w < (b.y - a.y) / t->h ? (b.x - a.x) / t->w : (b.y - a.y) / t->h;
        float w = t->w * k, h = t->h * k, x = a.x + (b.x - a.x - w) * 0.5f, y = a.y + (b.y - a.y - h) * 0.5f;
        dl->AddImage(t->id, ImVec2(x, y), ImVec2(x + w, y + h));
    } else {                                                 // no picture yet: the logo and the words
        wchar_t lf[64]; swprintf_s(lf, L"art\\%S.rgba", ED_GAMES[G].art);
        char lk[32]; sprintf_s(lk, "art:%s", ED_GAMES[G].art);
        const tex_entry* logo = texture(lk, lf);
        float cy = a.y + (b.y - a.y) * 0.42f;
        if (logo && logo->id) {
            float lw = px(150), lh = lw * logo->h / (float)logo->w;
            dl->AddImage(logo->id, ImVec2((a.x + b.x - lw) * 0.5f, cy - lh * 0.5f), ImVec2((a.x + b.x + lw) * 0.5f, cy + lh * 0.5f),
                         ImVec2(0, 0), ImVec2(1, 1), IM_COL32(255, 255, 255, 70));
        }
        push(SMALL);
        const char* msg = "No picture of this look yet";
        ImVec2 ms = ImGui::CalcTextSize(msg);
        dl->AddText(ImVec2((a.x + b.x - ms.x) * 0.5f, cy + px(70)), TEXT3, msg);
        pop();
    }
    // the corner: which species is shown, and (Reach) the button for the other one
    push(SMALL);
    char head[48];
    int pref = G == 3 ? (ed_over(g, "species") >= 0 ? ed_over(g, "species") : look_get(g_prof[g_edit], g, false, "species")) : sp;
    if (ED_GAMES[G].species) sprintf_s(head, "%s%s", sp == 1 ? "ELITE" : "SPARTAN", G == 3 && sp == pref ? "  -  PREFERRED" : "");
    else strcpy_s(head, "SPARTAN");
    dl->AddText(ImVec2(p0.x + pad, p0.y + px(18)), TEXT2, head);
    pop();
    if (G == 3) {
        const char* lbl = sp == 1 ? "Show Spartan" : "Show Elite";
        push(SMALL);
        float bw = ImGui::CalcTextSize(lbl).x + px(28);
        pop();
        ImGui::SetCursorScreenPos(ImVec2(p0.x + size.x - pad - bw, p0.y + px(10)));
        if (button(lbl, OUTLINE, ImVec2(bw, px(34)), true)) g_edit_view = sp == 1 ? 0 : 1;
        if (ImGui::IsItemHovered()) ImGui::SetTooltip("Invasion puts every player on a side: this is how the bot looks as the other species");
    }
    // the name plate along the bottom (left of the emblem ring)
    push(BODY);
    char plate[32];
    sprintf_s(plate, g_prof_clan[g_edit][0] ? "[%s] %s" : "%s%s", g_prof_clan[g_edit], g_prof[g_edit]);
    ImVec2 ps = ImGui::CalcTextSize(plate);
    float er = px(44);
    float room = size.x - er * 2 - px(40);
    float tx = ps.x < room ? p0.x + px(22) + (room - ps.x) * 0.5f : p0.x + px(22);
    dl->AddText(ImVec2(tx, p0.y + size.y - px(40)), TEXT, plate);
    pop();
    // its emblem in a ring of the frame colour, over the bottom right corner
    wchar_t ef[96]; swprintf_s(ef, L"emblems\\%S.rgba", g_prof[g_edit]);
    char ek[64]; sprintf_s(ek, "emblem:%s", g_prof[g_edit]);
    if (const tex_entry* em = texture(ek, ef)) {
        ImVec2 c(p1.x - er - px(14), p1.y - er - px(14));
        dl->AddCircleFilled(c, er + px(5), fc, 48);
        dl->AddCircleFilled(c, er, IM_COL32(14, 18, 26, 255), 48);
        float r = er * 0.80f;
        dl->AddImage(em->id, ImVec2(c.x - r, c.y - r), ImVec2(c.x + r, c.y + r));
    }
}

static void bot_editor(bots_panel_env& e, bool& changed) {
    using namespace ui;
    looks_load();
    ImGui::SetNextWindowSize(ImVec2(px(1160), px(760)));
    if (!ImGui::BeginPopupModal("Bot editor", nullptr, ImGuiWindowFlags_NoTitleBar | ImGuiWindowFlags_NoResize |
                                ImGuiWindowFlags_NoMove | ImGuiWindowFlags_NoScrollbar | ImGuiWindowFlags_NoScrollWithMouse))
        return;
    if (g_edit < 0 || g_edit >= g_nprof) { ImGui::CloseCurrentPopup(); ImGui::EndPopup(); return; }
    bool dirty = false;
    float W = ImGui::GetContentRegionAvail().x;
    ImVec2 top = ImGui::GetCursorPos();
    push(TITLE);
    ImGui::TextUnformatted("Bot profile");
    pop();
    ImGui::SetCursorPos(ImVec2(top.x + W - px(40), top.y));
    bool close = icon_x("edclose", px(40), g_env && g_env->pad ? "Close (B)" : "Close (Esc)");
    ImGui::SetCursorPos(ImVec2(top.x, top.y + px(52)));
    // name and clan tag, kept unique and plain (no commas or semicolons: the file's separators)
    ImGui::PushStyleColor(ImGuiCol_FrameBg, v4(SURF2));
    ImGui::PushStyleColor(ImGuiCol_Border, v4(LINE));
    ImGui::PushStyleVar(ImGuiStyleVar_FrameBorderSize, 1.0f);
    ImGui::BeginGroup();
    caption("Name");
    push(BODY);
    ImGui::SetNextItemWidth(px(300));
    ImGui::InputText("##edname", g_edit_name, sizeof g_edit_name, ImGuiInputTextFlags_CharsNoBlank);
    if (ImGui::IsItemDeactivatedAfterEdit()) {
        bool ok = g_edit_name[0] && !strpbrk(g_edit_name, ",;=|");
        for (int i = 0; ok && i < g_nprof; i++) ok = i == g_edit || _stricmp(g_prof[i], g_edit_name) != 0;
        if (ok && strcmp(g_prof[g_edit], g_edit_name)) {
            hb_roster& r = *e.roster;                        // the list's bot of that name follows the profile
            for (int i = 0; i < r.n; i++) if (!_stricmp(r.bot[i].name, g_prof[g_edit])) strcpy_s(r.bot[i].name, g_edit_name);
            strcpy_s(g_prof[g_edit], g_edit_name);
            dirty = changed = true;
        } else strcpy_s(g_edit_name, g_prof[g_edit]);
    }
    pop();
    ImGui::EndGroup();
    ImGui::SetCursorPos(ImVec2(top.x + px(316), top.y + px(52)));
    ImGui::BeginGroup();
    caption("Clan tag");
    push(BODY);
    ImGui::SetNextItemWidth(px(120));
    ImGui::InputText("##edclan", g_edit_clan, 5, ImGuiInputTextFlags_CharsNoBlank);
    if (ImGui::IsItemDeactivatedAfterEdit()) {
        bool ok = !strpbrk(g_edit_clan, ",;=[]|");
        if (ok) { strcpy_s(g_prof_clan[g_edit], g_edit_clan); dirty = true; }
        else strcpy_s(g_edit_clan, g_prof_clan[g_edit]);
    }
    pop();
    ImGui::EndGroup();
    ImGui::PopStyleVar();
    ImGui::PopStyleColor(2);
    ImGui::SameLine(0, px(16));
    ImGui::SetCursorPosY(ImGui::GetCursorPosY() + px(30));
    muted("Up to 4 letters or numbers. Leave it empty for no tag.", false);
    ImGui::SetCursorPos(ImVec2(top.x, top.y + px(52) + px(74)));
    line(14);
    // the games down the left (logo and title), the bot's picture in the middle, what it wears on the right
    ImVec2 body = ImGui::GetCursorScreenPos();
    float foot = px(64), bh = ImGui::GetWindowPos().y + ImGui::GetWindowSize().y - body.y - foot - px(16);
    float lw = px(236);
    ImDrawList* dl = ImGui::GetWindowDrawList();
    for (int k = 0; k < 6; k++) {
        ImVec2 p(body.x, body.y + k * (bh / 6));
        ImVec2 sz(lw, bh / 6 - px(6));
        ImGui::SetCursorScreenPos(p);
        ImGui::PushID(k);
        if (g_game_to == k) { ImGui::SetKeyboardFocusHere(); g_game_to = -1; }
        if (ImGui::InvisibleButton("game", sz, ImGuiButtonFlags_EnableNav)) { g_edit_game = k; g_edit_view = -1; }
        if (ImGui::IsItemFocused()) {                        // up and down the list within it (ImGui's own move went to
            static const ImGuiKey UP[] = { ImGuiKey_UpArrow, ImGuiKey_GamepadDpadUp, ImGuiKey_GamepadLStickUp };   // the
            static const ImGuiKey DOWN[] = { ImGuiKey_DownArrow, ImGuiKey_GamepadDpadDown, ImGuiKey_GamepadLStickDown };
            for (int d = 0; d < 3; d++) {                    // picker in line with the chosen game); its ends let go
                if (k > 0) ImGui::SetKeyOwner(UP[d], ImGui::GetItemID());
                if (k < 5) ImGui::SetKeyOwner(DOWN[d], ImGui::GetItemID());
                if (k > 0 && ImGui::IsKeyPressed(UP[d])) g_game_to = k - 1;
                if (k < 5 && ImGui::IsKeyPressed(DOWN[d])) g_game_to = k + 1;
            }
        }
        bool hov = ImGui::IsItemHovered(), on = g_edit_game == k, foc = focus_shown();
        ImGui::PopID();
        dl->AddRectFilled(p, ImVec2(p.x + sz.x, p.y + sz.y), on ? alpha(ACCENT, 0.14f) : hov ? HOVER : SURF, px(4));
        dl->AddRect(p, ImVec2(p.x + sz.x, p.y + sz.y), on ? ACCENT : LINE, px(4));
        if (foc)                                             // (outside its own border: the chosen game shows it too)
            dl->AddRect(ImVec2(p.x - 3, p.y - 3), ImVec2(p.x + sz.x + 3, p.y + sz.y + 3), FOCUS, px(6), 0, 2.0f);
        if (on) dl->AddRectFilled(p, ImVec2(p.x + px(4), p.y + sz.y), ACCENT, px(4));
        wchar_t lf[64]; swprintf_s(lf, L"art\\%S.rgba", ED_GAMES[k].art);
        char lk[32]; sprintf_s(lk, "art:%s", ED_GAMES[k].art);
        const tex_entry* logo = texture(lk, lf);
        float iw = px(54), tx = p.x + px(16);
        if (logo && logo->id) {
            float ih = iw * logo->h / (float)logo->w;
            if (ih > sz.y - px(16)) { ih = sz.y - px(16); iw = ih * logo->w / (float)logo->h; }
            dl->AddImage(logo->id, ImVec2(tx, p.y + (sz.y - ih) * 0.5f), ImVec2(tx + iw, p.y + (sz.y + ih) * 0.5f),
                         ImVec2(0, 0), ImVec2(1, 1), on || hov ? IM_COL32_WHITE : IM_COL32(255, 255, 255, 170));
            tx += px(54) + px(12);
        }
        push(BODY);
        ImGui::PushClipRect(p, ImVec2(p.x + sz.x - px(6), p.y + sz.y), true);
        ImVec2 ts = ImGui::CalcTextSize(ED_GAMES[k].name, nullptr, false, p.x + sz.x - px(10) - tx);
        dl->AddText(ImGui::GetFont(), ImGui::GetFontSize(), ImVec2(tx, p.y + (sz.y - ts.y) * 0.5f), on ? TEXT : TEXT2,
                    ED_GAMES[k].name, nullptr, p.x + sz.x - px(10) - tx);
        ImGui::PopClipRect();
        pop();
    }
    if (ImGui::GetFrameCount() - g_list_frame > 1) {         // the pad's shoulders: the game before / after (no list open)
        if (ImGui::IsKeyPressed(ImGuiKey_GamepadL1, false)) g_edit_game = (g_edit_game + 5) % 6, g_edit_view = -1;
        if (ImGui::IsKeyPressed(ImGuiKey_GamepadR1, false)) g_edit_game = (g_edit_game + 1) % 6, g_edit_view = -1;
    }
    int G = g_edit_game;
    const char* g = ED_GAMES[G].key;
    float cx = body.x + lw + px(16), cw = px(380);
    ed_preview(ImVec2(cx, body.y), ImVec2(cw, bh), G, dirty);
    float ox = cx + cw + px(20), ow = body.x + W - ox;
    ImGui::SetCursorScreenPos(ImVec2(ox, body.y));
    ImGui::BeginChild("edpage", ImVec2(ow, bh), ImGuiChildFlags_NavFlattened);   // (Tab goes on into it)
    float w = ImGui::GetContentRegionAvail().x - px(8);
    if (ED_GAMES[G].species) {                               // the games that let a player pick Spartan or Elite
        int over = ed_over(g, "species");
        int pref = over >= 0 ? over : look_get(g_prof[g_edit], g, false, G == 2 ? "model" : "species");
        ed_caption(G == 3 ? "Preferred species" : "Species", over >= 0);
        static const char* SN[2] = { "Spartan", "Elite" };
        int sv = pref == 1 ? 1 : 0;
        if (segmented("species", SN, 2, &sv, w)) {         // picking its rolled species again takes the change away
            ed_set(g, "species", sv == rolled_species(G) ? -1 : sv, dirty);
            g_edit_view = -1;
        }
        if (G == 3) muted("Custom games that let players choose use it. Invasion and Elite game types pick a side for "
                          "everyone: Show Elite or Show Spartan in the picture's corner shows the other look.");
        ImGui::Dummy(ImVec2(0, px(6)));
    }
    if ((G == 3 && ed_species(G) == 0) || G == 5) {          // Reach's Spartans are male or female (about 1 in 5
        // female, editable in the bot profile), Halo 4's too (male unless set here): maps/_looks/<game>.json gender,
        // picking the roll again takes the change away
        int over = ed_over(g, "gender"), roll = look_get(g_prof[g_edit], g, false, "gender") == 1 ? 1 : 0;
        ed_caption("Gender", over >= 0);
        static const char* GN[2] = { "Male", "Female" };
        int gv = over >= 0 ? (over == 1 ? 1 : 0) : roll;
        if (segmented("gender", GN, 2, &gv, w)) ed_set(g, "gender", gv == roll ? -1 : gv, dirty);
        ImGui::Dummy(ImVec2(0, px(6)));
    }
    if (G == 0) ed_colors("Armor color", G, "color", CE_COL, 18, w, dirty);
    if (G == 0 && g_experience) {                            // MCC's visors (the player: "you can choose a visor in
        ImGui::Dummy(ImVec2(0, px(4)));                      // modern for halo ce that needs an option")
        ed_pieces("Visor", G, "visor", CE_VISOR, COUNT(CE_VISOR), (w - px(12)) * 0.5f, dirty);
    }
    if (G == 1) {
        ed_colors("Primary color", G, "primary", H2_COL, 18, w, dirty);
        ed_colors("Secondary color", G, "secondary", H2_COL, 18, w, dirty);
    }
    if (G == 2) {
        bool elite = ed_species(G) == 1;
        float hw = (w - px(12)) * 0.5f;
        ImVec2 r0 = ImGui::GetCursorPos();
        if (!elite) {
            int nh = g_experience ? COUNT(H3_HELMET) : H3_HELMET_ORIGINAL;   // Original: the original pieces only
            int nc = g_experience ? COUNT(H3_CHEST) : H3_CHEST_ORIGINAL;
            int ns = g_experience ? COUNT(H3_SHOULDER) : H3_SHOULDER_ORIGINAL;
            ed_at(r0, 0, 0, hw); ed_pieces("Helmet", G, "helmet", H3_HELMET, nh, hw, dirty);
            ed_at(r0, 1, 0, hw); ed_pieces("Chest", G, "chest", H3_CHEST, nc, hw, dirty);
            ed_at(r0, 0, 1, hw); ed_pieces("Left shoulder", G, "left_shoulder", H3_SHOULDER, ns, hw, dirty);
            ed_at(r0, 1, 1, hw); ed_pieces("Right shoulder", G, "right_shoulder", H3_SHOULDER, ns, hw, dirty);
            // Halo 3's armor effect is a chest, the Flaming Head (MCC's 2007 one in Modern): picked here, the chest
            // follows; None puts its rolled chest back (Mark VI when that roll was a flaming one)
            static const piece H3_FX[] = { {0, "None"}, {13, "Flaming Head"}, {1, "Flaming Head (2007)"} };
            int ch = ed_value(G, "chest"), chr = ed_roll(G, "chest");
            int fx = ch == 13 || (ch == 1 && g_experience) ? ch : 0, fxr = chr == 13 || chr == 1 ? chr : 0;
            ed_at(r0, 0, 2, hw);
            int v = ed_combo("Armor effect", "##h3.fx", fx != fxr, H3_FX, g_experience ? 3 : 2, fx, fxr, hw);
            if (v > 0) ed_set(g, "chest", v == chr ? -1 : v, dirty);
            else if (v == 0) ed_set(g, "chest", fxr ? 0 : -1, dirty);
            if (g_experience) {                              // MCC's pieces (Original: the 2007 arms, legs, no backpack)
                ed_at(r0, 1, 2, hw); ed_pieces("Visor", G, "visor", H3_VISOR, COUNT(H3_VISOR), hw, dirty);
                ed_at(r0, 0, 3, hw); ed_pieces("Arms", G, "arms", H3_ARMS, COUNT(H3_ARMS), hw, dirty);
                ed_at(r0, 1, 3, hw); ed_pieces("Legs", G, "legs", H3_LEGS, COUNT(H3_LEGS), hw, dirty);
                ed_at(r0, 0, 4, hw); ed_pieces("Tech suit", G, "techsuit", H3_SUIT, COUNT(H3_SUIT), hw, dirty);
                int bp = ed_value(G, "backpack");
                ed_at(r0, 1, 4, hw); ed_pieces("Backpack", G, "backpack", H3_BACKPACK, COUNT(H3_BACKPACK), hw, dirty);
                if (ed_value(G, "backpack") != bp) g_turn_after = g_look_mtime;   // (from the front it hardly shows)
                ed_at(r0, 0, 5, hw);
            } else ed_at(r0, 0, 3, hw);
        } else {                                             // the Elite's own slots (maps/_looks/h3.json)
            int ns = g_experience ? COUNT(H3_ELITE) : H3_ELITE_ORIGINAL;
            ed_at(r0, 0, 0, hw); ed_pieces("Helmet", G, "elite_helmet", H3_ELITE, ns, hw, dirty);
            ed_at(r0, 1, 0, hw); ed_pieces("Chest", G, "elite_piece_51", H3_ELITE, ns, hw, dirty);
            ed_at(r0, 0, 1, hw); ed_pieces("Left shoulder", G, "elite_left_shoulder", H3_ELITE, ns, hw, dirty);
            ed_at(r0, 1, 1, hw); ed_pieces("Right shoulder", G, "elite_right_shoulder", H3_ELITE, ns, hw, dirty);
            if (g_experience) {                              // (Original: the 2007 arms and legs, as the Spartan's)
                ed_at(r0, 0, 2, hw); ed_pieces("Arms", G, "elite_piece_52", H3_ELITE_LIMB, COUNT(H3_ELITE_LIMB), hw, dirty);
                ed_at(r0, 1, 2, hw); ed_pieces("Legs", G, "elite_legs", H3_ELITE_LIMB, COUNT(H3_ELITE_LIMB), hw, dirty);
                ed_at(r0, 0, 3, hw);
            } else ed_at(r0, 0, 2, hw);
        }
        ImGui::Dummy(ImVec2(0, px(6)));
        ed_colors("Primary color", G, "primary", H3_COL, 30, w, dirty);
        ed_colors("Secondary color", G, "secondary", H3_COL, 30, w, dirty);
        ed_colors("Detail color", G, "tertiary", H3_COL, 30, w, dirty);
    }
    if (G == 4 && ed_species(G) == 1) {                      // its Elite: the same, by the Elite's own entries in the
        // customization table (helmet and chest 4-6, arms and legs 3-5; maps/_looks/h2a.json)
        static const piece SET[] = { {4, "Combat"}, {5, "Outrider"}, {6, "Invader"} };
        static const piece LIMB[] = { {3, "Combat"}, {4, "Outrider"}, {5, "Invader"} };
        if (!g_experience) {
            ed_pieces("Armor set", G, "elite_helmet", SET, 3, w, dirty);
            int v = ed_over(g, "elite_helmet");
            if (ed_over(g, "elite_chest") != v) ed_set(g, "elite_chest", v, dirty);
            static const char* LIMBS[2] = { "elite_arms", "elite_legs" };
            for (const char* k : LIMBS) if (ed_over(g, k) != (v < 0 ? -1 : v - 1)) ed_set(g, k, v < 0 ? -1 : v - 1, dirty);
            muted("Original Experience wears a set whole, as Halo 2: Anniversary shipped.");
        } else {
            float hw = (w - px(12)) * 0.5f;
            ImVec2 r0 = ImGui::GetCursorPos();
            ed_at(r0, 0, 0, hw); ed_pieces("Helmet", G, "elite_helmet", SET, 3, hw, dirty);
            ed_at(r0, 1, 0, hw); ed_pieces("Chest", G, "elite_chest", SET, 3, hw, dirty);
            ed_at(r0, 0, 1, hw); ed_pieces("Arms", G, "elite_arms", LIMB, 3, hw, dirty);
            ed_at(r0, 1, 1, hw); ed_pieces("Legs", G, "elite_legs", LIMB, 3, hw, dirty);
            ed_at(r0, 0, 2, hw);
            ImGui::Dummy(ImVec2(0, 0));                     // (an item where the cursor went: ImGui asserts without)
        }
    } else if (G == 4) {                                     // Halo 2: Anniversary: whole sets (Original) or mixed
        static const char* PART[6] = { "helmet", "chest", "shoulder_a", "shoulder_b", "forearms", "legs" };
        static const char* PARTN[6] = { "Helmet", "Chest", "Left shoulder", "Right shoulder", "Forearms", "Legs" };
        if (!g_experience) {
            ed_pieces("Armor set", G, "helmet", H2A_SET, 3, w, dirty);
            int v = ed_over(g, "helmet");
            for (int k = 1; k < 6; k++) if (ed_over(g, PART[k]) != v) ed_set(g, PART[k], v, dirty);
            muted("Original Experience wears a set whole, as Halo 2: Anniversary shipped.");
        } else {
            float hw = (w - px(12)) * 0.5f;
            ImVec2 r0 = ImGui::GetCursorPos();
            static const piece* LIST[6] = { H2A_HELMET, H2A_CHEST, H2A_SHOULDER, H2A_SHOULDER, H2A_LIMB, H2A_LIMB };
            static const int LISTN[6] = { COUNT(H2A_HELMET), COUNT(H2A_CHEST), COUNT(H2A_SHOULDER), COUNT(H2A_SHOULDER),
                                          COUNT(H2A_LIMB), COUNT(H2A_LIMB) };
            for (int k = 0; k < 6; k++) { ed_at(r0, k % 2, k / 2, hw); ed_pieces(PARTN[k], G, PART[k], LIST[k], LISTN[k], hw, dirty); }
            ed_at(r0, 0, 3, hw);
            ImGui::Dummy(ImVec2(0, 0));
        }
    }
    if (G == 3 || G == 5) {                                  // Reach, Halo 4: each piece from the game's own Armory (its
        // roll, or picked here: laid over the roll by its table index), the Original values first (look_names.h)
        float hw = (w - px(12)) * 0.5f;
        ImVec2 r0 = ImGui::GetCursorPos();
        auto n_of = [](int all, int original) { return g_experience ? all : original; };
        if (G == 3 && ed_species(G) == 1) {                  // its Elite: one armor set
            ed_pieces("Armor", G, "elite_armor", ED_REACH_ELITE_ARMOR, COUNT(ED_REACH_ELITE_ARMOR), w, dirty);
        } else if (G == 3) {
            ed_at(r0, 0, 0, hw); ed_pieces("Helmet", G, "helmet", ED_REACH_HELMET, n_of(COUNT(ED_REACH_HELMET), ED_REACH_HELMET_ORIGINAL), hw, dirty);
            ed_at(r0, 1, 0, hw); ed_pieces("Chest", G, "chest", ED_REACH_CHEST, n_of(COUNT(ED_REACH_CHEST), ED_REACH_CHEST_ORIGINAL), hw, dirty);
            ed_at(r0, 0, 1, hw); ed_pieces("Left shoulder", G, "left_shoulder", ED_REACH_LEFT_SHOULDER, COUNT(ED_REACH_LEFT_SHOULDER), hw, dirty);
            ed_at(r0, 1, 1, hw); ed_pieces("Right shoulder", G, "right_shoulder", ED_REACH_RIGHT_SHOULDER, COUNT(ED_REACH_RIGHT_SHOULDER), hw, dirty);
            ed_at(r0, 0, 2, hw); ed_pieces("Wrist", G, "wrist", ED_REACH_WRIST, COUNT(ED_REACH_WRIST), hw, dirty);
            ed_at(r0, 1, 2, hw); ed_pieces("Utility", G, "utility", ED_REACH_UTILITY, n_of(COUNT(ED_REACH_UTILITY), ED_REACH_UTILITY_ORIGINAL), hw, dirty);
            ed_at(r0, 0, 3, hw); ed_pieces("Knee guards", G, "knees", ED_REACH_KNEES, COUNT(ED_REACH_KNEES), hw, dirty);
            ed_at(r0, 1, 3, hw); ed_pieces("Visor", G, "visor", ED_REACH_VISOR, COUNT(ED_REACH_VISOR), hw, dirty);
            ed_at(r0, 0, 4, hw); ed_pieces("Armor effect", G, "armor_effect", ED_REACH_ARMOR_EFFECT, COUNT(ED_REACH_ARMOR_EFFECT), hw, dirty);
            ed_at(r0, 0, 5, hw);
            ImGui::Dummy(ImVec2(0, 0));
        } else {
            ed_at(r0, 0, 0, hw); ed_pieces("Helmet", G, "helmet", ED_H4_HELMET, n_of(COUNT(ED_H4_HELMET), ED_H4_HELMET_ORIGINAL), hw, dirty);
            ed_at(r0, 1, 0, hw); ed_pieces("Chest", G, "chest", ED_H4_CHEST, COUNT(ED_H4_CHEST), hw, dirty);
            ed_at(r0, 0, 1, hw); ed_pieces("Left shoulder", G, "left_shoulder", ED_H4_LEFT_SHOULDER, COUNT(ED_H4_LEFT_SHOULDER), hw, dirty);
            ed_at(r0, 1, 1, hw); ed_pieces("Right shoulder", G, "right_shoulder", ED_H4_RIGHT_SHOULDER, COUNT(ED_H4_RIGHT_SHOULDER), hw, dirty);
            ed_at(r0, 0, 2, hw); ed_pieces("Forearms", G, "arms", ED_H4_ARMS, COUNT(ED_H4_ARMS), hw, dirty);
            ed_at(r0, 1, 2, hw); ed_pieces("Legs", G, "legs", ED_H4_LEGS, COUNT(ED_H4_LEGS), hw, dirty);
            ed_at(r0, 0, 3, hw); ed_pieces("Visor", G, "visor", ED_H4_VISOR, COUNT(ED_H4_VISOR), hw, dirty);
            if (g_experience) {                              // (MCC added Halo 4's armor effects: Modern only)
                ed_at(r0, 1, 3, hw);
                ed_pieces("Armor effect", G, "armor_effect", ED_H4_ARMOR_EFFECT, COUNT(ED_H4_ARMOR_EFFECT), hw, dirty);
            }
            ed_at(r0, 0, 4, hw);
            ImGui::Dummy(ImVec2(0, 0));
        }
        ImGui::Dummy(ImVec2(0, px(6)));
        ed_colors("Primary color", G, "primary", G == 3 ? REACH_COL : H4_COL, 30, w, dirty);
        ed_colors("Secondary color", G, "secondary", G == 3 ? REACH_COL : H4_COL, 30, w, dirty);
        ed_caption("Its roll", ed_over(g, "seed") > 0);
        muted(G == 3 ? "Pieces not changed here come from Halo: Reach's Armory, the same every match."
                     : "Pieces not changed here come from Halo 4's Armory, the same every match.");
        ImGui::Dummy(ImVec2(0, px(6)));
        int salt = ed_over(g, "seed");
        if (button("New look", SECONDARY, ImVec2(px(180), px(40)))) ed_set(g, "seed", salt < 0 ? 1 : salt + 1, dirty);
        ImGui::SameLine(0, px(8));
        ImGui::BeginDisabled(salt <= 0);
        if (button("Its first look", GHOST, ImVec2(px(180), px(40)))) ed_set(g, "seed", -1, dirty);
        ImGui::EndDisabled();
    }
    ImGui::EndChild();
    // footer: this game back to the roll, error, done
    ImGui::SetCursorScreenPos(ImVec2(body.x, body.y + bh + px(16)));
    static const char* KEYS[] = { "species", "color", "primary", "secondary", "tertiary", "helmet", "chest",
        "left_shoulder", "right_shoulder", "elite_piece_51", "elite_piece_52", "elite_piece_54", "elite_helmet",
        "elite_left_shoulder", "elite_right_shoulder", "elite_legs", "shoulder_a",
        "shoulder_b", "forearms", "legs", "seed", "model", "gender", "elite_chest", "elite_arms", "wrist", "utility",
        "knees", "visor", "elite_armor", "arms", "armor_effect", "techsuit", "backpack" };
    bool any = false;
    for (const char* k : KEYS) any |= ed_over(g, k) >= 0;
    ImGui::BeginDisabled(!any);
    if (button("Back to its roll", GHOST, ImVec2(px(220), px(44)))) {
        for (const char* k : KEYS) if (ed_over(g, k) >= 0) ed_set(g, k, -1, dirty);
        g_edit_view = -1;
    }
    ImGui::EndDisabled();
    if (ImGui::IsItemHovered(ImGuiHoveredFlags_AllowWhenDisabled))
        ImGui::SetTooltip(any ? "Undo every change in this game: the bot wears what it was rolled" : "Nothing changed in this game");
    if (g_edit_failed) {
        ImGui::SameLine(0, px(16));
        push(SMALL);
        ImGui::PushStyleColor(ImGuiCol_Text, v4(DANGER));
        ImGui::AlignTextToFramePadding();
        ImGui::TextUnformatted("Couldn't save to native\\bin\\bot_profiles.ini. Check it isn't read-only or open elsewhere.");
        ImGui::PopStyleColor();
        pop();
    }
    ImGui::SetCursorScreenPos(ImVec2(body.x + W - px(170), body.y + bh + px(16)));
    if (button("Done", PRIMARY, ImVec2(px(170), px(44)))) close = true;
    if (dirty) {
        g_edit_failed = !profiles_save();
        if (!g_edit_failed) looks_refresh(false);            // the view's rolled looks follow (brain/looks.py)
    }
    if (close || (esc_pressed() && !ImGui::IsAnyItemActive())) {
        g_edit = -1;
        ImGui::CloseCurrentPopup();
    }
    ImGui::EndPopup();
}

static void open_editor(int i) {
    g_edit = i;
    g_edit_view = -1;
    g_edit_failed = false;
    g_turn_after = ~0ULL;
    strcpy_s(g_edit_name, g_prof[i]);
    strcpy_s(g_edit_clan, g_prof_clan[i]);
    ImGui::OpenPopup("Bot editor");
}
void bots_panel_debug_view(int species) { g_edit_view = species; }                       // native/panel_preview.cpp
void bots_panel_debug_yaw(float yaw) { g_yaw = yaw; }
void bots_panel_debug_looks() { looks_refresh(true); g_look_check = g_look_mtime = 0; looks_load(); }   // (waits)
void bots_panel_debug_edit(int i, int game) { if (i >= 0 && i < g_nprof) { g_edit = i; g_edit_game = game;
    strcpy_s(g_edit_name, g_prof[i]); strcpy_s(g_edit_clan, g_prof_clan[i]); } }   // native/panel_preview.cpp

// the tab's page: the 15 bots, how they suit up, the re-roll ---- the Advanced tab: Scaling difficulty.
// native\bin\scaling.ini, read by brain/scaling.py settings: lock=, rating= (percent, while locked), use= (0 Standard,
// k the k-th profile), then profile=<name>|<key>=<percent>|... A profile weights each hand setting against the rating.
// SP_KEY / SP_LABEL: brain/scaling.py PARAMS, in its order (tools/test_scaling.py holds them to it)
enum { SP_N = 15 };
static const char* SP_KEY[SP_N] = { "reaction", "aim", "aim_speed", "flick", "tracking", "decide", "overshoot", "turn",
                                    "head", "lead", "trigger", "ram", "memory", "hear", "dry" };
static const char* SP_LABEL[SP_N] = { "Reaction time", "Aim accuracy", "Aim speed", "Flick speed", "Tracking",
                                      "Decision speed", "Flick control", "Turn speed", "Headshots", "Leading shots",
                                      "Trigger discipline", "Ramming accuracy", "Memory", "Callout hearing",
                                      "Shots before moving on" };
struct scale_prof { char name[24]; int w[SP_N]; };
static scale_prof g_sp[8];
static int g_sp_n = 0, g_sp_use = 0, g_sp_rating = 100;
static uint32_t g_sp_lock = 0;
static bool g_sp_read = false, g_sp_confirm = false;
static ULONGLONG g_sp_asked = 0;                           // a change waits for its numbers (refreshed 300 ms after the last)
static void sp_load() {
    if (g_sp_read) return;
    g_sp_read = true;
    wchar_t p[260]; bin_file(p, 260, L"scaling.ini");
    FILE* f = nullptr;
    if (_wfopen_s(&f, p, L"r") != 0 || !f) return;
    char line[512];
    while (fgets(line, sizeof line, f)) {
        for (char* c = line; *c; c++) if (*c == '\r' || *c == '\n') *c = 0;
        unsigned v;
        if (sscanf_s(line, "lock=%u", &v) == 1) g_sp_lock = v != 0;
        else if (sscanf_s(line, "rating=%u", &v) == 1) g_sp_rating = v < 1 ? 1 : (int)v;
        else if (sscanf_s(line, "use=%u", &v) == 1) g_sp_use = (int)v;
        else if (!strncmp(line, "profile=", 8) && g_sp_n < 8) {
            scale_prof& sp = g_sp[g_sp_n++];
            for (int k = 0; k < SP_N; k++) sp.w[k] = 100;
            char* ctx = nullptr;
            char* tok = strtok_s(line + 8, "|", &ctx);
            strncpy_s(sp.name, tok ? tok : "Profile", _TRUNCATE);
            while ((tok = strtok_s(nullptr, "|", &ctx)) != nullptr) {
                char* eq = strchr(tok, '=');
                if (!eq) continue;
                *eq = 0;
                int k = key_index(tok, SP_KEY, SP_N, -1), w = atoi(eq + 1);
                if (k >= 0) sp.w[k] = w < 1 ? 1 : w;
            }
        }
    }
    fclose(f);
    if (g_sp_use > g_sp_n) g_sp_use = 0;
}
static void sp_save() {                                    // at once (the panel may close); the numbers a moment later
    wchar_t p[260]; bin_file(p, 260, L"scaling.ini");
    FILE* f = nullptr;
    if (_wfopen_s(&f, p, L"w") != 0 || !f) return;
    fprintf(f, "lock=%u\nrating=%d\nuse=%d\n", g_sp_lock, g_sp_rating, g_sp_use);
    for (int i = 0; i < g_sp_n; i++) {
        for (char* c = g_sp[i].name; *c; c++) if (*c == '|' || *c == '=') *c = '/';
        fprintf(f, "profile=%s", g_sp[i].name[0] ? g_sp[i].name : "Profile");
        for (int k = 0; k < SP_N; k++) fprintf(f, "|%s=%d", SP_KEY[k], g_sp[i].w[k]);
        fprintf(f, "\n");
    }
    fclose(f);
    g_sp_asked = GetTickCount64();
}
static int sp_step(int v) { return v < 100 ? 5 : v < 1000 ? 10 : 100; }   // (a rating from 1% to many thousands)

static void advanced_settings(bots_panel_env& e, bool& changed, ImVec2 body0, float w, float h) {
    using namespace ui;
    sp_load();
    if (g_sp_asked && GetTickCount64() - g_sp_asked > 300) { g_sp_asked = 0; scale_refresh(); }
    int games = e.scale_games ? (int)*e.scale_games : 5;
    scale_view v = scale_read(games);
    ImDrawList* dl = ImGui::GetWindowDrawList();
    float gap = px(16), left_w = px(560), right_w = w - left_w - gap;
    // left: the rating, games averaged, the lock
    dl->AddRectFilled(body0, ImVec2(body0.x + left_w, body0.y + h), SURF);
    dl->AddRect(body0, ImVec2(body0.x + left_w, body0.y + h), LINE);
    ImGui::SetCursorScreenPos(ImVec2(body0.x + px(20), body0.y + px(16)));
    ImGui::BeginGroup();
    ImGui::PushTextWrapPos(body0.x + left_w - px(20) - ImGui::GetWindowPos().x);
    caption("Scaling difficulty: skill rating");
    push(TITLE);
    ImGui::TextUnformatted(v.skill);
    pop();
    if (g_sp_lock) {                                       // (the lock beside the number)
        ImGui::SameLine(0, px(12));
        ImGui::SetCursorPosY(ImGui::GetCursorPosY() + px(10));
        push(SMALL);
        ImGui::TextColored(v4(GOLD), "LOCKED");
        pop();
    }
    push(SMALL);
    scale_summary(v, games, false);
    pop();
    muted("100% is a skilled human player's baseline; Legendary's hands are about 150%. Game sense (memory, callouts) "
          "is Legendary's up to 150% and grows past it above. Turn speed tops out at 2000 degrees a second.");
    ImGui::Dummy(ImVec2(0, px(12)));
    scale_games_setting(e, changed);
    ImGui::Dummy(ImVec2(0, px(16)));
    if (toggle("Lock the rating", &g_sp_lock)) {
        if (g_sp_lock) g_sp_rating = atoi(v.skill) > 0 ? atoi(v.skill) : 100;   // (where it stands now)
        sp_save();
    }
    muted(g_sp_lock ? "The bots stay at this rating. Your games still count for when you unlock it."
                    : "Every game you finish on Scaling moves the rating.");
    if (g_sp_lock) {
        ImGui::Dummy(ImVec2(0, px(4)));
        caption("Locked rating");
        if (stepper("sprating", &g_sp_rating, 1, 100000, "%", sp_step(g_sp_rating))) sp_save();
    }
    ImGui::PopTextWrapPos();
    ImGui::EndGroup();
    // right: custom profiles, each hand setting's weight
    ImVec2 r0(body0.x + left_w + gap, body0.y);
    dl->AddRectFilled(r0, ImVec2(r0.x + right_w, r0.y + h), SURF);
    dl->AddRect(r0, ImVec2(r0.x + right_w, r0.y + h), LINE);
    ImGui::SetCursorScreenPos(ImVec2(r0.x + px(20), r0.y + px(16)));
    ImGui::BeginGroup();
    ImGui::PushTextWrapPos(r0.x + right_w - px(20) - ImGui::GetWindowPos().x);
    caption("Custom profiles");
    muted("Weight each setting against the rating: 150% plays it half as well again, 50% half as well.", false);
    ImGui::Dummy(ImVec2(0, px(6)));
    // the profiles as one row of choices: Standard, then his own
    for (int i = 0; i <= g_sp_n; i++) {
        if (i) ImGui::SameLine(0, px(6));
        bool on = g_sp_use == i;
        ImGui::PushID(i);
        ImGui::PushStyleColor(ImGuiCol_Button, on ? v4(alpha(ACCENT, 0.16f)) : v4(SURF2));
        ImGui::PushStyleColor(ImGuiCol_ButtonHovered, on ? v4(alpha(ACCENT, 0.24f)) : v4(HOVER));
        ImGui::PushStyleColor(ImGuiCol_ButtonActive, v4(alpha(ACCENT, 0.3f)));
        ImGui::PushStyleColor(ImGuiCol_Border, on ? v4(ACCENT) : v4(LINE));
        ImGui::PushStyleColor(ImGuiCol_Text, on ? v4(TEXT) : v4(TEXT2));
        ImGui::PushStyleVar(ImGuiStyleVar_FrameBorderSize, 1.0f);
        ImGui::PushStyleVar(ImGuiStyleVar_FramePadding, ImVec2(px(12), px(7)));
        push(SMALL);
        if (ImGui::Button(i ? g_sp[i - 1].name : "Standard")) { g_sp_use = i; g_sp_confirm = false; sp_save(); }
        pop();
        ImGui::PopStyleVar(2);
        ImGui::PopStyleColor(5);
        ImGui::PopID();
    }
    ImGui::BeginDisabled(g_sp_n >= 8);
    ImGui::SameLine(0, px(12));
    if (button("+  New profile", GHOST, ImVec2(0, 0), true)) {
        scale_prof& sp = g_sp[g_sp_n++];
        sprintf_s(sp.name, "Profile %d", g_sp_n);
        for (int k = 0; k < SP_N; k++) sp.w[k] = g_sp_use ? g_sp[g_sp_use - 1].w[k] : 100;   // (a copy of the one in use)
        g_sp_use = g_sp_n;
        g_sp_confirm = false;
        sp_save();
    }
    ImGui::EndDisabled();
    ImGui::Dummy(ImVec2(0, px(6)));
    scale_prof* sp = g_sp_use ? &g_sp[g_sp_use - 1] : nullptr;
    if (sp) {                                              // its name, and Delete (a second press confirms)
        push(SMALL);
        ImGui::AlignTextToFramePadding();
        ImGui::TextColored(v4(TEXT2), "NAME");
        pop();
        ImGui::SameLine(0, px(12));
        ImGui::SetNextItemWidth(px(300));
        push(BODY);
        if (ImGui::InputText("##spname", sp->name, sizeof sp->name)) sp_save();
        pop();
        ImGui::SameLine(0, px(12));
        if (button(g_sp_confirm ? "Delete it?" : "Delete profile", DANGER_K, ImVec2(0, 0), true)) {
            if (!g_sp_confirm) g_sp_confirm = true;
            else {
                for (int i = g_sp_use - 1; i < g_sp_n - 1; i++) g_sp[i] = g_sp[i + 1];
                g_sp_n--;
                g_sp_use = 0;
                g_sp_confirm = false;
                sp_save();
                sp = nullptr;
            }
        }
    } else {
        muted("Standard: every setting plays at the rating. Make a profile to weight them.", false);
    }
    ImGui::Dummy(ImVec2(0, px(6)));
    // each setting: its weight, what it plays at, its value (the view's rows, brain/scaling.py)
    ImGui::BeginChild("sprows", ImVec2(right_w - px(40), r0.y + h - px(16) - ImGui::GetCursorScreenPos().y),
                      ImGuiChildFlags_NavFlattened);
    push(SMALL);
    ImGui::PushStyleVar(ImGuiStyleVar_FramePadding, ImVec2(px(6), px(2)));   // (rows of 24 px: all 15 in view at 1080p)
    ImGui::PushStyleVar(ImGuiStyleVar_CellPadding, ImVec2(px(4), px(0)));
    if (ImGui::BeginTable("spt", 4, ImGuiTableFlags_SizingFixedFit | ImGuiTableFlags_PadOuterX)) {
        ImGui::TableSetupColumn("Setting", ImGuiTableColumnFlags_WidthFixed, px(200));
        ImGui::TableSetupColumn("Weight", ImGuiTableColumnFlags_WidthFixed, px(190));
        ImGui::TableSetupColumn("Plays at", ImGuiTableColumnFlags_WidthFixed, px(100));
        ImGui::TableSetupColumn("Value", ImGuiTableColumnFlags_WidthStretch);
        ImGui::TableNextRow();
        static const char* HEAD[4] = { "SETTING", "WEIGHT", "PLAYS AT", "VALUE" };
        for (int c = 0; c < 4; c++) { ImGui::TableNextColumn(); ImGui::TextColored(v4(TEXT2), "%s", HEAD[c]); }
        for (int k = 0; k < SP_N; k++) {
            ImGui::TableNextRow();
            ImGui::TableNextColumn();
            ImGui::AlignTextToFramePadding();
            ImGui::TextUnformatted(SP_LABEL[k]);
            ImGui::TableNextColumn();
            if (sp) {
                ImGui::PushID(k);
                if (stepper("w", &sp->w[k], 10, 1000, "%", 10, 22, 90)) sp_save();   // (compact: all 12 in view)
                ImGui::PopID();
            } else {
                ImGui::AlignTextToFramePadding();
                ImGui::TextColored(v4(TEXT2), "100%%");
            }
            bool row = k < v.n && !strcmp(v.label[k], SP_LABEL[k]);   // (the view's rows in the same order)
            ImGui::TableNextColumn();
            ImGui::AlignTextToFramePadding();
            ImGui::TextColored(v4(TEXT), "%s", row ? v.pct[k] : "-");
            ImGui::TableNextColumn();
            ImGui::AlignTextToFramePadding();
            ImGui::TextColored(v4(TEXT2), "%s", row ? v.val[k] : "-");
        }
        ImGui::EndTable();
    }
    ImGui::PopStyleVar(2);
    pop();
    ImGui::EndChild();
    ImGui::PopTextWrapPos();
    ImGui::EndGroup();
}

static void mod_settings(bots_panel_env& e, bool& changed, ImVec2 body0, float w, float h) {
    using namespace ui;
    ImDrawList* dl = ImGui::GetWindowDrawList();
    float gap = px(16), right_w = px(520), left_w = w - right_w - gap;
    // left: the bots
    dl->AddRectFilled(body0, ImVec2(body0.x + left_w, body0.y + h), SURF);
    dl->AddRect(body0, ImVec2(body0.x + left_w, body0.y + h), LINE);
    ImGui::SetCursorScreenPos(ImVec2(body0.x + px(20), body0.y + px(16)));
    ImGui::BeginGroup();
    caption("Bot profiles");
    muted("Your 15 bots. They keep their names, clan tags and looks from match to match.", false);
    ImGui::Dummy(ImVec2(0, px(8)));
    int n = profiles_count();
    float col_w = (left_w - px(40)) / 3.0f;
    ImVec2 g0 = ImGui::GetCursorScreenPos();
    push(BODY);
    for (int i = 0; i < n && i < 15; i++) {                  // each name opens the bot editor
        ImVec2 c(g0.x + (i % 3) * col_w, g0.y + (i / 3) * px(52));
        ImGui::SetCursorScreenPos(ImVec2(c.x - px(8), c.y - px(8)));
        ImGui::PushID(i);
        bool click = nav_button("prof", ImVec2(col_w - px(8), px(40)));
        bool hov = ImGui::IsItemHovered();
        ImGui::PopID();
        if (click) open_editor(i);    // after PopID: OpenPopup inside PushID(i) named another popup
        if (hov) {
            dl->AddRectFilled(ImVec2(c.x - px(8), c.y - px(8)), ImVec2(c.x + col_w - px(16), c.y + px(32)), HOVER, px(3));
            ImGui::SetTooltip("Edit name, clan tag and armor");
        }
        const char* clan = profile_clan(i);
        float x = c.x;
        if (clan[0]) {
            char tagbuf[12]; sprintf_s(tagbuf, "[%s]", clan);
            dl->AddText(ImVec2(x, c.y), ACCENT, tagbuf);
            x += ImGui::CalcTextSize(tagbuf).x + px(8);
        }
        dl->AddText(ImVec2(x, c.y), TEXT, profile_name(i));
    }
    ImGui::SetCursorScreenPos(ImVec2(g0.x, g0.y + ((n + 2) / 3) * px(52)));
    if (n) muted("Pick a bot to change its name, clan tag and armor.", false);
    if (n == 0) dl->AddText(g0, TEXT2, "No profiles yet: Re-roll makes your 15 bots.");
    pop();
    ImGui::EndGroup();
    // right: how they dress, the re-roll
    ImVec2 r0(body0.x + left_w + gap, body0.y);
    dl->AddRectFilled(r0, ImVec2(r0.x + right_w, r0.y + h), SURF);
    dl->AddRect(r0, ImVec2(r0.x + right_w, r0.y + h), LINE);
    ImGui::SetCursorScreenPos(ImVec2(r0.x + px(20), r0.y + px(16)));
    ImGui::BeginGroup();
    ImGui::PushTextWrapPos(r0.x + right_w - px(20) - ImGui::GetWindowPos().x);   // (window-local x)
    caption("How they suit up");
    push(BODY);
    ImGui::Text("%s", profiles_experience() ? "Modern Experience" : "Original Experience");
    pop();
    muted(profiles_experience() ? "Bot profiles can spawn with all armor customizations in MCC."
                                : "Bot profiles only spawn with original content from each game.");
    ImGui::Dummy(ImVec2(0, px(4)));
    push(BODY);
    ImGui::Text("Armor effects: %s", FX_NAME[profiles_effects()]);
    if (profiles_species() != 1) ImGui::Text("Species: %s", profiles_species() == 0 ? "Spartans only" : "Elites only");
    else if (profiles_elite_share() >= 0) ImGui::Text("Species: mixed, %d%% Elites", profiles_elite_share());
    else ImGui::TextUnformatted("Species: mixed");
    pop();
    ImGui::Dummy(ImVec2(0, px(10)));
    if (button("Re-roll profiles...", PRIMARY, ImVec2(px(240), px(46)))) {
        rr_experience = profiles_experience();
        rr_effects = profiles_effects();
        rr_species = profiles_species();
        rr_elite_share = profiles_elite_share() >= 0 ? profiles_elite_share() : 20;
        ImGui::OpenPopup("Re-roll bot profiles");
    }
    if (e.sounds) {                                          // the panel's sounds: their volume, 0 off
        ImGui::Dummy(ImVec2(0, px(18)));
        caption("Panel sounds");
        int v = (int)*e.sounds;
        ImGui::PushStyleColor(ImGuiCol_FrameBg, v4(SURF2));
        ImGui::PushStyleColor(ImGuiCol_FrameBgHovered, v4(HOVER));
        ImGui::PushStyleColor(ImGuiCol_FrameBgActive, v4(HOVER));
        ImGui::PushStyleColor(ImGuiCol_SliderGrab, v4(ACCENT));
        ImGui::PushStyleColor(ImGuiCol_SliderGrabActive, v4(ACCENT));
        ImGui::SetNextItemWidth(px(240));
        if (ImGui::SliderInt("##sounds", &v, 0, 100, v ? "%d%%" : "Off")) { *e.sounds = (uint32_t)v; changed = true; }
        if (ImGui::IsItemDeactivatedAfterEdit() && e.play_sound && v) e.play_sound(PS_SELECT, (uint32_t)v);   // (how loud)
        ImGui::PopStyleColor(5);
    }
    ImGui::PopTextWrapPos();
    ImGui::EndGroup();
    reroll_dialog(e, changed);
    bot_editor(e, changed);
}

static void confirm_clear(bots_panel_env& e, bool& changed) {
    using namespace ui;
    ImGui::SetNextWindowSize(ImVec2(px(480), 0));
    if (!ImGui::BeginPopupModal("Remove all bots", nullptr, ImGuiWindowFlags_NoTitleBar | ImGuiWindowFlags_NoResize | ImGuiWindowFlags_NoMove))
        return;
    float W = ImGui::GetContentRegionAvail().x;
    push(TITLE);
    ImGui::TextUnformatted("Remove all bots?");
    pop();
    char t[96]; sprintf_s(t, "All %d bots leave the list. Quick setup makes a new one.", e.roster->n);
    muted(t);
    ImGui::Dummy(ImVec2(0, px(16)));
    float bw = px(150);
    ImGui::SetCursorPosX(ImGui::GetCursorPosX() + W - 2 * bw - px(10));
    if (button("Keep them", SECONDARY, ImVec2(bw, px(44))) || esc_pressed()) ImGui::CloseCurrentPopup();
    ImGui::SameLine(0, px(10));
    if (button("Remove all", DANGER_K, ImVec2(bw, px(44)))) {
        e.roster->n = 0; g_sel = -1; changed = true; ImGui::CloseCurrentPopup();
    }
    ImGui::EndPopup();
}

// the panel's sounds as WAV files in memory (overlay.cpp plays them, panel_preview --wavs writes them for
// tools/test_panel_sounds.py): short tones made here, no sound files: 16-bit mono 22.05 kHz; the bytes written, 0 when
// kind or cap is wrong
int panel_sound_wav(int kind, uint32_t volume, char* out, int cap) {
    if (kind < PS_MOVE || kind > PS_ERROR || cap < 44 + 2) return 0;
    struct part { float s, f0, f1, a; };                 // seconds, from Hz, to Hz, loudness
    static const part P[5][2] = {
        { { 0.014f, 1900, 1900, 0.10f }, {} },                                // move: a tick
        { { 0.022f, 1300, 1300, 0.20f }, { 0.032f, 1950, 1950, 0.20f } },     // select: up a step
        { { 0.022f, 1600, 1600, 0.18f }, { 0.036f, 1050, 1050, 0.18f } },     // back: down a step
        { { 0.130f, 700, 1500, 0.16f }, {} },                                 // open: a rising swell
        { { 0.080f, 220, 220, 0.22f }, { 0.080f, 196, 196, 0.22f } } };       // error: low, twice
    const int rate = 22050, most = (cap - 44) / 2;
    short* pcm = (short*)(out + 44);
    int n = 0;
    for (const part& p : P[kind]) {
        int m = (int)(p.s * rate);
        for (int i = 0; i < m && n < most; i++, n++) {
            float t = i / (float)rate, u = t / p.s;
            float env = kind == PS_OPEN ? sinf(3.14159f * u) : expf(-5.0f * u) * (1.0f - expf(-t * 4000.0f));
            float ph = 6.28318f * (p.f0 * t + 0.5f * (p.f1 - p.f0) * t * u);      // (a rising tone: its phase)
            float w = sinf(ph) + (kind == PS_ERROR ? 0.35f * sinf(3.0f * ph) : 0.0f);
            pcm[n] = (short)(32767.0f * p.a * (volume / 100.0f) * env * w);
        }
    }
    char* h = out;
    auto put = [h](int at, uint32_t v, int bytes) { memcpy(h + at, &v, bytes); };
    memcpy(h, "RIFF", 4); put(4, 36 + n * 2, 4); memcpy(h + 8, "WAVEfmt ", 8); put(16, 16, 4); put(20, 1, 2);
    put(22, 1, 2); put(24, rate, 4); put(28, rate * 2, 4); put(32, 2, 2); put(34, 16, 2);
    memcpy(h + 36, "data", 4); put(40, n * 2, 4);
    return 44 + n * 2;
}

// ------------------------------------------------------------------ the panel's sounds All of them here, once a frame,
// from ImGui's own state: the keyboard's or the pad's focus moving to another control, a control pressed (a click,
// Enter / Space, the pad's A), back (Esc, the pad's B), the panel opening, a save that failed. The mouse moving over
// things makes none. A click counts when it lets go of the control it pressed (not a drag: turning the 3D view, a
// slider). The focus moving wins over Tab's press of a text box; the tick of it coming back as a list or dialog closes
// is left out. e.play_sound plays it (botcore: at the panel's volume, Settings)

static ImGuiID g_snd_nav = 0;
static bool g_snd_failed = false;
static void panel_sounds(bots_panel_env& e) {
    ImGuiContext& g = *ImGui::GetCurrentContext();
    int f = ImGui::GetFrameCount(), kind = -1;
    bool failed = g_edit_failed || rr_failed;
    bool click = ImGui::IsMouseReleased(0) && g.ActiveIdPreviousFrame != 0 && g.IO.MouseDragMaxDistanceSqr[0] < 36.0f;
    if (f - g_snd_frame > 1) kind = PS_OPEN;                 // (not drawn the frame before: it opened)
    else if (failed && !g_snd_failed) kind = PS_ERROR;
    else if (ImGui::IsKeyPressed(ImGuiKey_Escape, false) || ImGui::IsKeyPressed(ImGuiKey_GamepadFaceRight, false)) kind = PS_BACK;
    else if (g.NavCursorVisible && g.NavId != g_snd_nav && g_snd_nav != 0 && f > g_snd_quiet) kind = PS_MOVE;
    else if ((g.NavActivatePressedId != 0 && g.NavId == g_snd_nav) || click) kind = PS_SELECT;
    if (kind == PS_SELECT || kind == PS_BACK) g_snd_quiet = f + 3;
    g_snd_frame = f, g_snd_nav = g.NavId, g_snd_failed = failed;
    if (kind >= 0 && e.play_sound && e.sounds && *e.sounds) e.play_sound(kind, *e.sounds);
}

bool bots_panel(bots_panel_env& e) {
    using namespace ui;
    s = e.scale; font = e.f_body;
    if (ImGui::GetFrameCount() - g_snd_frame > 1) scale_refresh();   // (it opened: the last game's K/D counted)
    panel_sounds(e);
    g_make_texture = e.make_texture;
    g_env = &e;
    hb_roster& r = *e.roster;
    g_panel_title = e.title;
    if (ff_one_team()) { r.teams = 1; for (int i = 0; i < r.n; i++) r.bot[i].team = 0; }   // (ODST Firefight: yours)
    else if (ff_two_teams()) { r.teams = 2; for (int i = 0; i < r.n; i++) r.bot[i].team &= 1; }   // (Reach Firefight: red or blue)
    else if (r.teams < 2) r.teams = 2;
    if (r.teams > HB_TEAMS_MAX) r.teams = HB_TEAMS_MAX;
    for (int i = 0; i < r.n; i++)
        if (r.bot[i].team + 1 > r.teams) r.teams = r.bot[i].team + 1;
    bool changed = false;
    ImGuiIO& io = ImGui::GetIO();
    float W = io.DisplaySize.x * 0.94f < px(1400) ? io.DisplaySize.x * 0.94f : px(1400);
    float H = io.DisplaySize.y * 0.9f < px(860) ? io.DisplaySize.y * 0.9f : px(860);
    ImGui::SetNextWindowSize(ImVec2(W, H));
    ImGui::SetNextWindowPos(ImVec2(io.DisplaySize.x * 0.5f, io.DisplaySize.y * 0.5f), 0, ImVec2(0.5f, 0.5f));
    ImGui::PushStyleVar(ImGuiStyleVar_WindowPadding, ImVec2(px(28), px(22)));
    ImGui::PushStyleVar(ImGuiStyleVar_ItemSpacing, ImVec2(px(8), px(8)));
    ImGui::PushStyleVar(ImGuiStyleVar_FramePadding, ImVec2(px(14), px(9)));
    ImGui::PushStyleVar(ImGuiStyleVar_WindowBorderSize, 1.0f);
    ImGui::PushStyleVar(ImGuiStyleVar_FrameRounding, px(2));
    ImGui::PushStyleVar(ImGuiStyleVar_PopupRounding, px(2));
    ImGui::PushStyleVar(ImGuiStyleVar_WindowRounding, 0.0f);
    ImGui::PushStyleColor(ImGuiCol_WindowBg, v4(BG));
    ImGui::PushStyleColor(ImGuiCol_PopupBg, v4(IM_COL32(10, 21, 32, 252)));
    ImGui::PushStyleColor(ImGuiCol_Border, v4(LINE));
    ImGui::PushStyleColor(ImGuiCol_Text, v4(TEXT));
    ImGui::PushStyleColor(ImGuiCol_DragDropTarget, v4(GOLD));
    ImGui::PushStyleColor(ImGuiCol_NavCursor, v4(FOCUS));
    ImGui::PushStyleColor(ImGuiCol_ModalWindowDimBg, ImVec4(0.0f, 0.02f, 0.04f, 0.6f));
    ImGui::PushStyleColor(ImGuiCol_ScrollbarBg, ImVec4(0, 0, 0, 0));
    ImGui::PushStyleColor(ImGuiCol_ScrollbarGrab, v4(LINE));
    ImGui::Begin("##bots_panel", e.open, ImGuiWindowFlags_NoTitleBar | ImGuiWindowFlags_NoResize | ImGuiWindowFlags_NoMove |
                 ImGuiWindowFlags_NoCollapse | ImGuiWindowFlags_NoScrollbar | ImGuiWindowFlags_NoSavedSettings);
    ImDrawList* dl = ImGui::GetWindowDrawList();
    ImVec2 wp = ImGui::GetWindowPos();
    dl->AddRectFilled(wp, ImVec2(wp.x + W, wp.y + px(3)), ACCENT);                     // MCC's blue top edge
    float inner = ImGui::GetContentRegionAvail().x;

    // ---- header: title, game, on/off, close
    ImVec2 h0 = ImGui::GetCursorPos();
    push(TITLE);
    ImGui::TextUnformatted("BOTS");
    pop();
    ImGui::SameLine(0, px(16));
    ImGui::SetCursorPosY(h0.y + px(12));
    char sub[64]; sprintf_s(sub, "%s%s%s", e.title > 0 && e.title < 9 ? TITLE_NAME[e.title] : "",
                            e.title > 0 && e.title < 9 && TITLE_NAME[e.title][0] ? "  /  " : "",
                            hb_panel_firefight ? "FIREFIGHT" : "CUSTOM GAME");
    caption(sub);
    float tw = px(46) + px(12);
    push(BODY);
    tw += ImGui::CalcTextSize("Bots Off").x;
    pop();
    ImGui::SetCursorPos(ImVec2(h0.x + inner - tw - px(60), h0.y + px(6)));
    if (toggle(*e.enabled ? "Bots On" : "Bots Off", e.enabled)) changed = true;
    ImGui::SetCursorPos(ImVec2(h0.x + inner - px(40), h0.y + px(2)));
    if (icon_x("close", px(40), e.pad ? "Close (B)" : "Close (Esc)")) *e.open = false;
    ImGui::SetCursorPos(ImVec2(h0.x, h0.y + px(54)));
    line(10);

    // ---- tabs: the bots list | the mod's settings
    if (!ImGui::IsPopupOpen("", ImGuiPopupFlags_AnyPopupId | ImGuiPopupFlags_AnyPopupLevel)) {   // the pad's shoulders: the
        if (ImGui::IsKeyPressed(ImGuiKey_GamepadL1, false)) g_tab = g_tab > 0 ? g_tab - 1 : 0;   // tabs
        if (ImGui::IsKeyPressed(ImGuiKey_GamepadR1, false)) g_tab = g_tab < 2 ? g_tab + 1 : 2;
    }
    if (tab("Bots", g_tab == 0)) g_tab = 0;
    ImGui::SameLine(0, px(6));
    if (tab("Settings", g_tab == 1)) g_tab = 1;
    ImGui::SameLine(0, px(6));
    if (tab("Advanced", g_tab == 2)) g_tab = 2;
    ImGui::Dummy(ImVec2(0, px(6)));
    if (g_tab >= 1) {
        float foot1 = px(78);
        if (g_open_popup) { ImGui::OpenPopup(g_open_popup); g_open_popup = nullptr; }   // (native/panel_preview.cpp)
        ImVec2 b0 = ImGui::GetCursorScreenPos();
        if (g_tab == 1) mod_settings(e, changed, b0, inner, wp.y + H - px(22) - foot1 - b0.y);
        else advanced_settings(e, changed, b0, inner, wp.y + H - px(22) - foot1 - b0.y);
        ImGui::SetCursorScreenPos(ImVec2(wp.x + W - px(28) - px(170), wp.y + H - px(22) - foot1 + px(24)));
        if (button("Done", PRIMARY, ImVec2(px(170), px(46)))) *e.open = false;
        ImGui::End();
        ImGui::PopStyleColor(9);
        ImGui::PopStyleVar(7);
        return changed;
    }

    // ---- toolbar: what you have, roster actions, LiDAR
    {
        int teams_used = 0;
        for (int t = 0; t < r.teams; t++) teams_used += roster_on_team(r, t) > 0;
        char sum[80];
        sprintf_s(sum, r.n ? "%d %s on %d %s" : "No bots yet", r.n, r.n == 1 ? "bot" : "bots", teams_used, teams_used == 1 ? "team" : "teams");
        push(BODY);
        ImGui::AlignTextToFramePadding();
        ImGui::TextUnformatted(sum);
        pop();
        ImGui::SameLine(0, px(18));
        bool off = !*e.enabled || *e.lidar_mode;
        ImGui::BeginDisabled(*e.lidar_mode != 0);
        if (button("Quick setup", OUTLINE, ImVec2(0, 0), true)) {   // (the empty state has the one primary)
            qs_teams = r.teams < roster_max_teams(e.title) ? r.teams : roster_max_teams(e.title);
            int most = 0;
            for (int t = 0; t < r.teams; t++) most = roster_on_team(r, t) > most ? roster_on_team(r, t) : most;
            qs_per = most ? most : 4;
            qs_tier = (int)(*e.difficulty <= 5 ? *e.difficulty : 4);
            ImGui::OpenPopup("Quick setup");
        }
        ImGui::SameLine(0, px(14));
        if (!ff_one_team()) {                               // (ODST Firefight: one team, nothing to balance)
            ImGui::BeginDisabled(r.n < 2);
            if (button("Balance teams", GHOST, ImVec2(0, 0), true)) { roster_balance(r); changed = true; }
            if (ImGui::IsItemHovered(ImGuiHoveredFlags_AllowWhenDisabled | ImGuiHoveredFlags_ForTooltip)) ImGui::SetTooltip("Even out the team sizes");
            ImGui::SameLine(0, px(2));
            if (button("Shuffle teams", GHOST, ImVec2(0, 0), true)) { roster_shuffle(r, seed()); changed = true; }
            if (ImGui::IsItemHovered(ImGuiHoveredFlags_AllowWhenDisabled | ImGuiHoveredFlags_ForTooltip)) ImGui::SetTooltip("New teams for everyone, same team sizes");
            ImGui::EndDisabled();
            ImGui::SameLine(0, px(2));
        }
        ImGui::BeginDisabled(r.n < 1);
        if (button("Remove all", DANGER_K, ImVec2(0, 0), true)) ImGui::OpenPopup("Remove all bots");
        ImGui::EndDisabled();
        ImGui::EndDisabled();
        // the data collector and LiDAR, right-aligned
        push(BODY);
        float lw = px(46) + px(12) + ImGui::CalcTextSize("LiDAR mapping").x;
        // (not in LiDAR mode: no fight to record there, and the players stepper needs the room)
        bool collect = e.collector && !*e.lidar_mode;
        float dw = collect ? px(46) + px(12) + ImGui::CalcTextSize("Data collector").x + px(36) : 0.0f;
        pop();
        ImGui::SameLine(inner - lw - dw + px(28) - (*e.lidar_mode ? px(260) : 0));
        if (collect) {                                      // records you for the Observer (tools/collector.py)
            ImGui::BeginDisabled(!*e.enabled);
            if (toggle("Data collector", e.collector)) changed = true;
            if (ImGui::IsItemHovered(ImGuiHoveredFlags_AllowWhenDisabled | ImGuiHoveredFlags_ForTooltip))
                ImGui::SetTooltip("%s", !*e.enabled ? "Needs bots in the match: the bots' brain does the recording."
                                  : "Records you every match from the game itself: movement, aim, shots,\n"
                                    "guns and equipment (never your keyboard or controller). The Observer\n"
                                    "shows your positioning, reaction time, pace and accuracy. Saved on this\n"
                                    "PC in logs\\collect_*.jsonl.");
            ImGui::EndDisabled();
            ImGui::SameLine(0, px(36));
        }
        if (toggle("LiDAR mapping", e.lidar_mode)) changed = true;
        if (ImGui::IsItemHovered()) ImGui::SetTooltip("Mapping bots instead of fighters: they walk every floor and learn your jumps");
        if (*e.lidar_mode) {
            ImGui::SameLine(0, px(14));
            int lp = (int)*e.lidar_players;
            if (stepper("lidar", &lp, 2, 16, "players")) { *e.lidar_players = (uint32_t)lp; changed = true; }
        }
        (void)off;
    }
    ImGui::Dummy(ImVec2(0, px(6)));

    // ---- body: team cards | inspector
    float foot = px(78), insp_w = px(420), gap = px(16);
    ImVec2 body0 = ImGui::GetCursorScreenPos();
    float body_h = wp.y + H - px(22) - foot - body0.y;
    float grid_w = inner - insp_w - gap;
    // state banners over the grid
    const char* banner = nullptr;
    char btxt[160];
    if (*e.lidar_mode) {
        sprintf_s(btxt, "LiDAR mapping is on: %u mapping bots join your team instead of this list.", *e.lidar_players);
        banner = btxt;
    } else if (!*e.enabled) banner = "Bots are off: nobody on this list joins. Switch Bots on at the top.";
    else if (e.title == 1 && r.teams > 2) banner = "Halo: CE has two teams: bots on Green and later teams play as Red or Blue.";
    else if (e.collector && *e.collector)                   // (not in LiDAR mode: that banner comes first)
        banner = "Data collector is on: your movement, aim, shots and guns are recorded each match (never your keys).";
    if (banner) {
        ImVec2 a = body0, b(body0.x + grid_w, body0.y + px(40));
        dl->AddRectFilled(a, b, alpha(GOLD, 0.12f));
        dl->AddRectFilled(a, ImVec2(a.x + px(3), b.y), GOLD);
        push(SMALL);
        dl->AddText(ImVec2(a.x + px(14), a.y + (px(40) - ImGui::GetFontSize()) * 0.5f), IM_COL32(255, 222, 150, 255), banner);
        pop();
        body0.y += px(48); body_h -= px(48);
    }
    ImGui::SetCursorScreenPos(body0);
    bool dim = *e.lidar_mode || !*e.enabled;
    if (dim) ImGui::PushStyleVar(ImGuiStyleVar_Alpha, 0.55f);
    if (r.n == 0 && !*e.lidar_mode) {
        // empty: one clear next step
        dl->AddRect(body0, ImVec2(body0.x + grid_w, body0.y + body_h), LINE);
        push(TITLE);
        const char* t1 = "No bots yet";
        ImVec2 ts = ImGui::CalcTextSize(t1);
        dl->AddText(ImVec2(body0.x + (grid_w - ts.x) * 0.5f, body0.y + body_h * 0.36f), TEXT, t1);
        pop();
        push(BODY);
        const char* t2 = ff_one_team() ? "Choose up to 3 bots for your team, and how hard they play."
                       : ff_two_teams() ? "Choose up to 7 bots for the red and blue teams, and how hard they play."
                                        : "Choose how many teams and bots, and how hard they play.";
        ImVec2 t2s = ImGui::CalcTextSize(t2);
        dl->AddText(ImVec2(body0.x + (grid_w - t2s.x) * 0.5f, body0.y + body_h * 0.36f + px(48)), TEXT2, t2);
        pop();
        ImGui::SetCursorScreenPos(ImVec2(body0.x + (grid_w - px(220)) * 0.5f, body0.y + body_h * 0.36f + px(92)));
        if (button("Quick setup##empty", PRIMARY, ImVec2(px(220), px(46)))) {
            qs_teams = ff_one_team() ? 1 : 2; qs_per = ff_one_team() ? 3 : ff_two_teams() ? 3 : 4;
            qs_tier = (int)(*e.difficulty <= 5 ? *e.difficulty : 4);
            ImGui::OpenPopup("Quick setup");
        }
    } else {
        int maxt = roster_max_teams(e.title) > r.teams ? roster_max_teams(e.title) : r.teams;
        int cards = r.teams + (r.teams < maxt && r.teams < HB_TEAMS_MAX ? 1 : 0);
        int cols = cards < 4 ? cards : 4, rows = (cards + cols - 1) / cols;
        float cw = (grid_w - (cols - 1) * gap) / cols, ch = (body_h - (rows - 1) * gap) / rows;
        for (int c = 0; c < cards; c++) {
            ImGui::SetCursorScreenPos(ImVec2(body0.x + (c % cols) * (cw + gap), body0.y + (c / cols) * (ch + gap)));
            if (c < r.teams) team_card(e, c, ImVec2(cw, ch), changed);
            else add_team_card(e, ImVec2(cw, ch), changed);
        }
    }
    if (dim) ImGui::PopStyleVar();
    // inspector
    ImVec2 ip(body0.x + grid_w + gap, body0.y - (banner ? px(48) : 0));
    float ih = body_h + (banner ? px(48) : 0);
    dl->AddRectFilled(ip, ImVec2(ip.x + insp_w, ip.y + ih), SURF);
    dl->AddRect(ip, ImVec2(ip.x + insp_w, ip.y + ih), LINE);
    ImGui::SetCursorScreenPos(ImVec2(ip.x + px(18), ip.y + px(16)));
    ImGui::PushStyleColor(ImGuiCol_ChildBg, ImVec4(0, 0, 0, 0));
    ImGui::PushStyleVar(ImGuiStyleVar_ItemSpacing, ImVec2(px(8), px(6)));
    ImGui::BeginChild("inspector", ImVec2(insp_w - px(36), ih - px(32)), ImGuiChildFlags_NavFlattened);   // (Tab goes on into it)
    inspector(e, changed);
    ImGui::EndChild();
    ImGui::PopStyleVar();
    ImGui::PopStyleColor();

    // ---- footer: difficulty for everyone, limits, Done
    ImGui::SetCursorScreenPos(ImVec2(wp.x + px(28), wp.y + H - px(22) - foot + px(14)));
    line(8);
    ImVec2 f0 = ImGui::GetCursorScreenPos();
    float rowh = px(46);                                    // everything centred on the Done button's row
    ImGui::SetCursorScreenPos(ImVec2(f0.x, f0.y + (rowh - px(18)) * 0.5f));
    caption("Difficulty");
    static const char* DN[6] = { "Easy", "Normal", "Heroic", "Legendary", "Mixed", "Scaling" };
    static const ImU32 DC[6] = { TIER_COL[0], TIER_COL[1], TIER_COL[2], TIER_COL[3], ACCENT, TIER_COL[4] };
    int d = (int)(*e.difficulty <= 5 ? *e.difficulty : 4);
    ImGui::SetCursorScreenPos(ImVec2(f0.x + px(140), f0.y + (rowh - px(36)) * 0.5f));
    ImGui::BeginDisabled(r.n == 0 || *e.lidar_mode);
    if (segmented("all", DN, 6, &d, px(600), DC)) {
        *e.difficulty = (uint32_t)d;
        for (int i = 0; i < r.n; i++) r.bot[i].tier = (uint8_t)(d < 4 ? d : d == 5 ? 4 : mixed_tier(i));   // (both teams)
        changed = true;
    }
    if (ImGui::IsItemHovered(ImGuiHoveredFlags_ForTooltip | ImGuiHoveredFlags_AllowWhenDisabled))
        scale_tooltip(e.scale_games ? (int)*e.scale_games : 5);
    ImGui::EndDisabled();
    char lim[96];
    if (ff_one_team()) sprintf_s(lim, "%d of 3 bots. Firefight holds 4 players.", r.n);
    else if (ff_two_teams()) sprintf_s(lim, "%d of 7 bots. Firefight holds 8 players.", r.n);
    else sprintf_s(lim, "%d of %d bots. The game holds 16 players.", r.n, HB_ROSTER_MAX);
    push(SMALL);
    float lw2 = ImGui::CalcTextSize(lim).x;
    pop();
    ImGui::SetCursorScreenPos(ImVec2(wp.x + W - px(28) - px(170) - px(20) - lw2, f0.y + (rowh - px(18)) * 0.5f));
    muted(lim, false);
    ImGui::SetCursorScreenPos(ImVec2(wp.x + W - px(28) - px(170), f0.y));
    if (button("Done", PRIMARY, ImVec2(px(170), rowh))) *e.open = false;

    // ---- keys for the selected bot (not while typing a name)
    if (g_sel >= 0 && g_sel < r.n && !io.WantTextInput && ImGui::IsWindowFocused(ImGuiFocusedFlags_RootAndChildWindows)
        && !ImGui::IsPopupOpen("", ImGuiPopupFlags_AnyPopupId)) {
        int maxt = roster_max_teams(e.title);
        if (ImGui::IsKeyPressed(ImGuiKey_Delete)) { roster_remove(r, g_sel); g_sel = -1; changed = true; }
        else if (io.KeyCtrl && ImGui::IsKeyPressed(ImGuiKey_D) && r.n < roster_cap()) {
            hb_bot b = r.bot[g_sel]; roster_add(r, b.team, b.tier, b.style, seed()); g_sel = r.n - 1; changed = true;
        } else if (io.KeyCtrl && ImGui::IsKeyPressed(ImGuiKey_RightArrow)) {
            move_bot(r, g_sel, (r.bot[g_sel].team + 1) % maxt); changed = true;
        } else if (io.KeyCtrl && ImGui::IsKeyPressed(ImGuiKey_LeftArrow)) {
            move_bot(r, g_sel, (r.bot[g_sel].team + maxt - 1) % maxt); changed = true;
        } else if (ImGui::IsKeyPressed(ImGuiKey_DownArrow) && g_sel + 1 < r.n) g_sel++;
        else if (ImGui::IsKeyPressed(ImGuiKey_UpArrow) && g_sel > 0) g_sel--;
    }
    // the confirmations, once a frame at window level (opened by the toolbar or the empty state)
    if (g_open_popup) { ImGui::OpenPopup(g_open_popup); g_open_popup = nullptr; }
    quick_setup(e, changed);
    confirm_clear(e, changed);
    ImGui::End();
    ImGui::PopStyleColor(9);
    ImGui::PopStyleVar(7);
    return changed;
}

// ---- the bug report over a match (bots_panel.h bug_report_env)
bool bug_report_ui(bug_report_env& e) {
    using namespace ui;
    s = e.scale; font = e.font;
    ImGuiIO& io = ImGui::GetIO();
    ImVec2 D = io.DisplaySize;
    bool save = false;
    e.hovered = false;
    if (e.show_button && !*e.open) {
        // in MCC's footer row (Back's line and height), right-aligned on its frame's margin (ui_layout.h), the stock
        // buttons' dark fill and blue frame; the key that opens it too. Placed on MCC's back buffer, mapped to the window.
        ImDrawList* dl = ImGui::GetForegroundDrawList();
        float bw = e.bb_w > 0 ? e.bb_w : D.x, bh = e.bb_h > 0 ? e.bb_h : D.y, kx = D.x / bw, ky = D.y / bh;
        float fs = size(BODY), ks = size(SMALL);
        float need = font->CalcTextSizeA(fs, 1e9f, 0, "Report bot bug").x + px(20 + 24 + 44 + 14);
        ui_rect r = bug_button_rect(bw, bh, need / kx);
        ImVec2 a(r.x0 * kx, r.y0 * ky), b(r.x1 * kx, r.y1 * ky);
        float h = b.y - a.y;
        if (fs > h * 0.62f) fs = h * 0.62f;
        if (ks > h * 0.5f) ks = h * 0.5f;
        bool hov = io.MousePos.x >= a.x && io.MousePos.x <= b.x && io.MousePos.y >= a.y && io.MousePos.y <= b.y;
        dl->AddRectFilled(a, b, hov ? IM_COL32(24, 52, 74, 235) : IM_COL32(10, 22, 33, 215));
        dl->AddRect(a, b, hov ? IM_COL32(150, 215, 250, 255) : IM_COL32(78, 132, 168, 230), 0, 0, hov ? 2.0f : 1.2f);
        dl->AddText(font, fs, ImVec2(a.x + px(20), a.y + (h - fs) * 0.5f), TEXT, "Report bot bug");
        ImVec2 k0(b.x - px(14) - px(44), a.y + h * 0.2f), k1(b.x - px(14), b.y - h * 0.2f);
        dl->AddRect(k0, k1, IM_COL32(170, 200, 215, 220), px(3), 0, 1.2f);
        ImVec2 kt = font->CalcTextSizeA(ks, 1e9f, 0, "F9");
        dl->AddText(font, ks, ImVec2((k0.x + k1.x - kt.x) * 0.5f, (k0.y + k1.y - kt.y) * 0.5f), TEXT2, "F9");
        if (hov) { e.hovered = true; if (ImGui::IsMouseClicked(0)) *e.open = true; }
    }
    if (!*e.open)
        return false;
    // the window: what went wrong, typed by the player; the match recording goes with it
    static bool was_open;
    bool first = !was_open;
    float W = px(680);
    ImGui::GetBackgroundDrawList()->AddRectFilled(ImVec2(0, 0), D, IM_COL32(0, 4, 8, 120));   // the game, dimmed
    ImGui::SetNextWindowPos(ImVec2(D.x * 0.5f, D.y * 0.45f), ImGuiCond_Always, ImVec2(0.5f, 0.5f));
    ImGui::SetNextWindowSize(ImVec2(W, 0));
    ImGui::SetNextWindowFocus();
    ImGui::PushStyleVar(ImGuiStyleVar_WindowPadding, ImVec2(px(28), px(24)));
    ImGui::PushStyleVar(ImGuiStyleVar_ItemSpacing, ImVec2(px(10), px(8)));
    ImGui::PushStyleVar(ImGuiStyleVar_FrameRounding, px(2));
    ImGui::PushStyleVar(ImGuiStyleVar_WindowRounding, 0.0f);
    ImGui::PushStyleVar(ImGuiStyleVar_WindowBorderSize, 1.0f);
    ImGui::PushStyleColor(ImGuiCol_WindowBg, v4(BG));
    ImGui::PushStyleColor(ImGuiCol_Border, v4(LINE));
    ImGui::PushStyleColor(ImGuiCol_Text, v4(TEXT));
    ImGui::PushStyleColor(ImGuiCol_FrameBg, v4(SURF));
    ImGui::PushStyleColor(ImGuiCol_FrameBgHovered, v4(SURF2));
    ImGui::PushStyleColor(ImGuiCol_FrameBgActive, v4(SURF2));
    ImGui::Begin("##bug_report", nullptr, ImGuiWindowFlags_NoTitleBar | ImGuiWindowFlags_NoResize | ImGuiWindowFlags_NoMove |
                 ImGuiWindowFlags_NoCollapse | ImGuiWindowFlags_NoScrollbar | ImGuiWindowFlags_NoSavedSettings);
    ImVec2 wp = ImGui::GetWindowPos();
    ImGui::GetWindowDrawList()->AddRectFilled(wp, ImVec2(wp.x + W, wp.y + px(3)), ACCENT);   // MCC's blue top edge
    float inner = ImGui::GetContentRegionAvail().x;
    ImGui::PushTextWrapPos(ImGui::GetCursorPosX() + inner);
    push(TITLE);
    ImGui::TextUnformatted("Report a bot problem");
    pop();
    muted("Say what the bots did wrong and where. This match's recording is saved with your report, so the moment "
          "can be found and fixed.");
    ImGui::Dummy(ImVec2(0, px(6)));
    caption("What happened");
    if (first) ImGui::SetKeyboardFocusHere();
    push(BODY);
    ImVec2 box(inner, px(170));
    ImVec2 at = ImGui::GetCursorScreenPos();
    ImGui::InputTextMultiline("##bug_text", e.text, (size_t)e.text_n, box, ImGuiInputTextFlags_WordWrap);
    if (!e.text[0])                                           // a hint while it is empty
        ImGui::GetWindowDrawList()->AddText(ImVec2(at.x + px(10), at.y + px(8)), TEXT3,
                                            "Example: two bots kept running into the rail by red base");
    pop();
    char count[32]; sprintf_s(count, "%d / %d", (int)strlen(e.text), e.text_n - 1);
    push(SMALL);
    float cw = ImGui::CalcTextSize(count).x;
    pop();
    muted("Ctrl+Enter saves, Esc cancels.", false);
    ImGui::SameLine(inner - cw + ImGui::GetStyle().WindowPadding.x);
    muted(count, false);
    ImGui::Dummy(ImVec2(0, px(8)));
    float bw = px(170);
    bool empty = !e.text[0];
    ImGui::SetCursorPosX(ImGui::GetCursorPosX() + inner - 2 * bw - px(10));
    if (button("Cancel", SECONDARY, ImVec2(bw, px(46))) || esc_pressed()) *e.open = false;
    ImGui::SameLine(0, px(10));
    ImGui::BeginDisabled(empty);
    if (button("Save report", PRIMARY, ImVec2(bw, px(46))) ||
        (!empty && io.KeyCtrl && (ImGui::IsKeyPressed(ImGuiKey_Enter) || ImGui::IsKeyPressed(ImGuiKey_KeypadEnter)))) {
        save = true;
        *e.open = false;
    }
    ImGui::EndDisabled();
    if (empty && ImGui::IsItemHovered(ImGuiHoveredFlags_AllowWhenDisabled))
        ImGui::SetTooltip("Type what went wrong first");
    ImGui::PopTextWrapPos();
    e.hovered = true;                                         // the window is up: every click and key is ours
    ImGui::End();
    ImGui::PopStyleColor(6);
    ImGui::PopStyleVar(5);
    was_open = *e.open;
    return save;
}
