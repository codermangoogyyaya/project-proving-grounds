// overlay.cpp: the "BOTS" tile and settings panel, drawn by botcore.dll over MCC's Halo CE custom
// game lobby (the lobby is a Scaleform movie, so we cannot add a real tile; docs/research/08).
// D3D11 Present hook + Dear ImGui (MIT). The icon is our own vector drawing (a Spartan helmet
// with an active-camo shimmer), no game art is used or shipped.
#include <windows.h>
#include <mmsystem.h>
#include <xinput.h>
#include <d3d11.h>
#include <dxgi.h>
#include <math.h>
#include <stdio.h>
#include <time.h>
#include "shared.h"
#include "model_view.h"
#include "bots_panel.h"
#include "ui_layout.h"
#include "third_party/minhook/include/MinHook.h"
#include "third_party/imgui/imgui.h"
#include "third_party/imgui/imgui_impl_win32.h"
#include "third_party/imgui/imgui_impl_dx11.h"

extern hb_shared* g_shm;
extern hb_roster g_roster;           // botcore.cpp: the BOTS panel's list
extern hb_roster g_ff_roster;        // botcore.cpp: Firefight's list (Reach: up to 7 bots on red and blue; ODST: 3, your team)
extern uint32_t g_collector;         // botcore.cpp: the brain records the player (Data collector)
extern uint32_t g_scale_games;       // botcore.cpp: Scaling difficulty's games averaged
extern uint32_t g_sounds;            // botcore.cpp: the BOTS panel's sound volume, 0-100 (0 off)
void logf(const char* fmt, ...);
void save_settings();
extern IMGUI_IMPL_API LRESULT ImGui_ImplWin32_WndProcHandler(HWND, UINT, WPARAM, LPARAM);

typedef HRESULT(__stdcall* present_fn)(IDXGISwapChain*, UINT, UINT);
typedef HRESULT(__stdcall* resize_fn)(IDXGISwapChain*, UINT, UINT, UINT, DXGI_FORMAT, UINT);
static present_fn o_present;
static resize_fn o_resize;
static ID3D11Device* g_dev;
static ID3D11DeviceContext* g_ctx;
static ID3D11RenderTargetView* g_rtv;
static HWND g_hwnd;
static WNDPROC o_wndproc;
static bool g_ready, g_panel, g_show_tile, g_hover_tile;
static volatile bool g_pad_held;         // the pad kept from MCC (h_xinput_get)
static bool g_pad_last;                  // the last input was the pad (the prompts say its buttons)
static bool g_popup_before;              // a list or dialog was open in the panel the frame before
static uint8_t* g_exe;

// Which screen is open, from the files MCC opens (tools: native/filemon.cpp). Opening a custom
// game lobby loads Data\UI\Screens\MultiplayerCustomGame.gfx and, within ~60 ms, that title's
// variants (halo1\game_variants\... for CE). Leaving loads every title's variants (halo2\...) or
// another screen movie. MCC 1.3528: byte [exe+0x4000B8C+0x11] is 0 in menus, 1 in a game.
typedef HANDLE(WINAPI* createfile_fn)(LPCWSTR, DWORD, DWORD, LPSECURITY_ATTRIBUTES, DWORD, DWORD, HANDLE);
// The files tell which title's lobby was opened last; whether it is still on screen (not left,
// not covered by the carnage report or a menu) is checked on the frame itself (probe_lobby).
static createfile_fn o_createfile;
static volatile LONG g_session;      // title of the last custom game lobby opened: 1 CE, 2 Halo 2
                                     // Classic, 3 Halo 3, 5 Reach, 6 H2A, 7 H4, 8 ODST (the titles with bots), 4 any other, 0 none
static volatile DWORD g_lobby_gfx_at;
static volatile LONG g_ff_lobby;     // the lobby last opened is Firefight's (FirefightCustomGame.gfx): it holds 4 players
static bool bots_title(LONG t) { return t >= 1 && t <= 8 && t != 4; }
extern int hb_panel_firefight;   // (bots_panel.cpp: the roster's cap, 3 bots in Firefight)
static const wchar_t* wcsistr(const wchar_t* s, const wchar_t* f) {   // (MCC opens some screens in lower case)
    for (; *s; s++) {
        const wchar_t *a = s, *b = f;
        while (*a && *b && towlower(*a) == towlower(*b)) { a++; b++; }
        if (!*b) return s;
    }
    return nullptr;
}

static LONG title_of(LPCWSTR name) {
    if (wcsstr(name, L"halo1\\")) return 1;
    if (wcsstr(name, L"halo2\\")) return 2;
    if (wcsstr(name, L"halo3\\")) return 3;
    if (wcsstr(name, L"haloreach\\")) return 5;
    if (wcsstr(name, L"groundhog\\")) return 6;
    if (wcsstr(name, L"halo4\\")) return 7;
    if (wcsstr(name, L"halo3odst\\")) return 8;
    return 4;
}

static HANDLE WINAPI h_createfile(LPCWSTR name, DWORD a, DWORD s, LPSECURITY_ATTRIBUTES sa, DWORD c, DWORD f, HANDLE t) {
    if (name) {
        static int logged;                       // the files a lobby opens (evidence for new titles)
        if (g_lobby_gfx_at && GetTickCount() - g_lobby_gfx_at < 3000 && logged < 60 && !wcsstr(name, L"\\Screens\\")) {
            logged++;
            logf("lobby file %ls", name);
        }
        if (wcsstr(name, L"\\Screens\\")) {
            if (wcsistr(name, L"MultiplayerCustomGame.gfx")) { g_lobby_gfx_at = GetTickCount(); g_ff_lobby = 0; hb_panel_firefight = 0; }
            else if (wcsistr(name, L"FirefightCustomGame.gfx")) { g_lobby_gfx_at = GetTickCount(); g_ff_lobby = 1; hb_panel_firefight = 1; }
        } else if (wcsstr(name, L"game_variants\\") || wcsstr(name, L"\\maps\\info\\")) {
            LONG title = title_of(name);
            if (g_lobby_gfx_at && GetTickCount() - g_lobby_gfx_at < 1500) {   // the lobby's own title
                g_session = title;
                g_lobby_gfx_at = 0;
                logf("lobby title %ld", title);
            } else if (title != g_session) {
                g_session = 0;                                                  // other titles: we left
            }
        }
    }
    return o_createfile(name, a, s, sa, c, f, t);
}

int mcc_menu_state() {               // 0 menus, 1 in a game, -1 unknown (used by botcore.cpp)
    __try { return g_exe ? g_exe[0x4000B8C + 0x11] : -1; }
    __except (EXCEPTION_EXECUTE_HANDLER) { return -1; }
}

// Is the lobby's tile row on screen? Every few frames, copy two thin vertical strips of the back
// buffer where the stock OPTIONS and START tiles have their left frame line, and look for that
// light-blue line down most of the tile height. Anything covering the lobby (carnage report,
// another menu after Back) breaks it, which the file signals above cannot see.
static ID3D11Texture2D* g_probe;
static UINT g_probe_h;
static DXGI_FORMAT g_probe_fmt;
static bool g_lobby_visible;

static bool frame_pixel(const uint8_t* p, DXGI_FORMAT f) {
    int r, g, b;
    if (f == DXGI_FORMAT_B8G8R8A8_UNORM || f == DXGI_FORMAT_B8G8R8A8_UNORM_SRGB) { b = p[0]; g = p[1]; r = p[2]; }
    else if (f == DXGI_FORMAT_R10G10B10A2_UNORM) {
        uint32_t v = *(const uint32_t*)p; r = (v & 1023) >> 2; g = ((v >> 10) & 1023) >> 2; b = ((v >> 20) & 1023) >> 2;
    } else { r = p[0]; g = p[1]; b = p[2]; }
    // stock frame (78,132,168) .. focused (110,200,250); the Halo 3 lobby draws its frames dark,
    // about (34,57,69): bluish is enough, the probe still wants a line down both tile edges
    return b > 55 && b > r + 25 && g > r + 15;
}

static bool white_pixel(const uint8_t* p, DXGI_FORMAT f) {   // (MCC's title letters)
    int r, g, b;
    if (f == DXGI_FORMAT_B8G8R8A8_UNORM || f == DXGI_FORMAT_B8G8R8A8_UNORM_SRGB) { b = p[0]; g = p[1]; r = p[2]; }
    else if (f == DXGI_FORMAT_R10G10B10A2_UNORM) {
        uint32_t v = *(const uint32_t*)p; r = (v & 1023) >> 2; g = ((v >> 10) & 1023) >> 2; b = ((v >> 20) & 1023) >> 2;
    } else { r = p[0]; g = p[1]; b = p[2]; }
    return r > 200 && g > 200 && b > 200;
}

static UINT g_probe_rows0, g_probe_rows1, g_probe_solid0, g_probe_solid1;
static ID3D11Texture2D* g_title;
static UINT g_title_w, g_title_h;
static DXGI_FORMAT g_title_fmt;
static LONG g_probe_ff_logged = -1;
static void probe_lobby(IDXGISwapChain* sc) {
    ID3D11Texture2D* back = nullptr;
    if (FAILED(sc->GetBuffer(0, __uuidof(ID3D11Texture2D), (void**)&back)) || !back) return;
    D3D11_TEXTURE2D_DESC bd;
    back->GetDesc(&bd);
    float W = (float)bd.Width, H = (float)bd.Height;                     // (ui_layout.h: MCC's fitted 16:9 page)
    ui_rect rows = mcc_probe_rows(W, H);
    UINT top = (UINT)rows.y0, bot = (UINT)rows.y1, h = bot - top;
    if (!g_probe || g_probe_h != h || g_probe_fmt != bd.Format) {
        if (g_probe) g_probe->Release();
        D3D11_TEXTURE2D_DESC sd{};
        sd.Width = 12; sd.Height = h; sd.MipLevels = 1; sd.ArraySize = 1; sd.Format = bd.Format;
        sd.SampleDesc.Count = 1; sd.Usage = D3D11_USAGE_STAGING; sd.CPUAccessFlags = D3D11_CPU_ACCESS_READ;
        g_probe = nullptr;
        g_dev->CreateTexture2D(&sd, nullptr, &g_probe);
        g_probe_h = h; g_probe_fmt = bd.Format;
    }
    bool ok = g_probe && bd.SampleDesc.Count == 1;
    for (int e = 0; e < 2 && ok; e++) {                   // OPTIONS' and START's left frame lines
        UINT x = (UINT)mcc_probe_x(W, H, e, g_ff_lobby != 0), left = x > 6 ? x - 6 : 0;   // (Firefight: its own row)
        if (left + 12 > bd.Width || bot > bd.Height) { ok = false; break; }
        D3D11_BOX box;                                     // field order is left, top, FRONT, right, bottom, back
        box.left = left; box.right = left + 12;
        box.top = top; box.bottom = bot;
        box.front = 0; box.back = 1;
        g_ctx->CopySubresourceRegion(g_probe, 0, 0, 0, 0, back, 0, &box);
        D3D11_MAPPED_SUBRESOURCE m;
        if (FAILED(g_ctx->Map(g_probe, 0, D3D11_MAP_READ, 0, &m))) { ok = false; break; }
        UINT rows = 0, solid = 0;
        for (UINT y = 0; y < h; y++) {
            const uint8_t* row = (const uint8_t*)m.pData + y * m.RowPitch;
            int on = 0;
            for (int xx = 0; xx < 12; xx++) on += frame_pixel(row + xx * 4, bd.Format);
            rows += on > 0;
            solid += on == 12;
        }
        g_ctx->Unmap(g_probe, 0);
        if (e == 0) g_probe_rows0 = rows; else g_probe_rows1 = rows;
        if (e == 0) g_probe_solid0 = solid; else g_probe_solid1 = solid;
        // a line down the tile height, not a blue band: the carnage report's blue team rows are blue straight across
        // (the tile showed over the scoreboard, live H2A Nov 6); in the lobby the strip is solid in 37% of rows at most
        // (its video behind the tiles; "a line only" made the tile blink there, s175-s176)
        ok = rows > h * 7 / 10 && solid < h * 6 / 10;
    }
    // Firefight's post-game report has the lobby's tile row (map, time, waves, difficulty, skull combo, and a 6th, TIME
    // MULTIPLIER, under the BOTS tile till s179) and opens no file on Back: its title tells them apart (ui_layout.h)
    UINT title = 0;
    if (ok && g_ff_lobby) {
        ui_rect tb = mcc_ff_title_probe(W, H);
        UINT tl = (UINT)tb.x0, tt = (UINT)tb.y0, tw = (UINT)(tb.x1 - tb.x0), th = (UINT)(tb.y1 - tb.y0);
        if (!g_title || g_title_w != tw || g_title_h != th || g_title_fmt != bd.Format) {
            if (g_title) g_title->Release();
            D3D11_TEXTURE2D_DESC sd{};
            sd.Width = tw; sd.Height = th; sd.MipLevels = 1; sd.ArraySize = 1; sd.Format = bd.Format;
            sd.SampleDesc.Count = 1; sd.Usage = D3D11_USAGE_STAGING; sd.CPUAccessFlags = D3D11_CPU_ACCESS_READ;
            g_title = nullptr;
            g_dev->CreateTexture2D(&sd, nullptr, &g_title);
            g_title_w = tw; g_title_h = th; g_title_fmt = bd.Format;
        }
        D3D11_MAPPED_SUBRESOURCE m;
        D3D11_BOX box{tl, tt, 0, tl + tw, tt + th, 1};
        if (g_title && tl + tw <= bd.Width && tt + th <= bd.Height) {
            g_ctx->CopySubresourceRegion(g_title, 0, 0, 0, 0, back, 0, &box);
            if (SUCCEEDED(g_ctx->Map(g_title, 0, D3D11_MAP_READ, 0, &m))) {
                for (UINT y = 0; y < th; y++)
                    for (UINT x = 0; x < tw; x++) title += white_pixel((const uint8_t*)m.pData + y * m.RowPitch + x * 4, bd.Format);
                g_ctx->Unmap(g_title, 0);
            }
        }
        ok = title * 100 > tw * th * 3;                   // (letters: 9.6% of the box in the lobby, 0 after a match)
    }
    back->Release();
    if (ok != g_lobby_visible || g_ff_lobby != g_probe_ff_logged) {      // (evidence: which row was read, what it found)
        logf("lobby probe: %s lobby, %ux%u, OPTIONS %u (%u solid) START %u (%u solid) of %u rows, title %u -> %s",
             g_ff_lobby ? "Firefight" : "custom game", bd.Width, bd.Height, g_probe_rows0, g_probe_solid0, g_probe_rows1,
             g_probe_solid1, h, title, ok ? "on screen" : "not seen");
        g_probe_ff_logged = g_ff_lobby;
    }
    g_lobby_visible = ok;
}

static bool in_ce_menus() {
    __try {
        return bots_title(g_session) && g_lobby_visible && g_exe[0x4000B8C + 0x11] == 0;
    } __except (EXCEPTION_EXECUTE_HANDLER) { return false; }
}

static const char* DIFF_NAME[6] = { "Easy", "Normal", "Heroic", "Legendary", "Scaling", "Mixed" };   // (bots_panel.cpp tiers, then Mixed)

// MCC lays its menus out on the back buffer; the window shows that buffer stretched to its client area. The two differ
// when the window was moved or resized without MCC resizing its buffer (a borderless window put on another monitor):
// MCC's menus are placed by the buffer's size, so ours are too, then mapped into the window ImGui draws and clicks in
// (io.DisplayFramebufferScale makes ImGui render window units onto the whole buffer).
static float g_bb_w, g_bb_h;              // the back buffer, every frame (h_present)
static ImVec2 bb_size() {
    ImVec2 d = ImGui::GetIO().DisplaySize;
    return g_bb_w > 0 && g_bb_h > 0 ? ImVec2(g_bb_w, g_bb_h) : d;
}
static ImVec4 to_ui(ui_rect r) {          // back buffer px -> window px
    ImVec2 d = ImGui::GetIO().DisplaySize, b = bb_size();
    float kx = d.x / b.x, ky = d.y / b.y;
    return ImVec4(r.x0 * kx, r.y0 * ky, r.x1 * kx, r.y1 * ky);
}
// our windows' scale: MCC's menu page fitted into the window (ui_layout.h mcc_content), in window px
static float ui_scale() { ImVec2 d = ImGui::GetIO().DisplaySize; return mcc_content(d.x, d.y).s; }

// --- the tile: same size as the lobby's tiles, the next slot right of START (ui_layout.h bots_tile_rect)
static ImVec4 tile_rect() {
    ImVec2 b = bb_size();
    return to_ui(bots_tile_rect(b.x, b.y, g_ff_lobby != 0));
}

static ImFont* g_font_label;   // Bahnschrift (Windows), close to MCC's condensed UI font
static ImFont* g_font_small;

static void draw_helmet(ImDrawList* dl, ImVec2 c, float r, float t, ImU32 line) {
    // Spartan helmet in the stock tiles' style: thin light-blue line art, no fill. Dome, cheek
    // guards, wide visor with a glint; an active-camo shimmer drifts through it.
    float w = r * 0.075f;
    dl->PathArcTo(ImVec2(c.x, c.y - r * 0.05f), r * 0.80f, 3.25f, 6.17f, 36);
    dl->PathStroke(line, 0, w);
    ImVec2 shell[7] = { {c.x - r * 0.79f, c.y + r * 0.02f}, {c.x - r * 0.74f, c.y + r * 0.55f}, {c.x - r * 0.32f, c.y + r * 0.86f},
                        {c.x, c.y + r * 0.92f}, {c.x + r * 0.32f, c.y + r * 0.86f}, {c.x + r * 0.74f, c.y + r * 0.55f},
                        {c.x + r * 0.79f, c.y + r * 0.02f} };
    dl->AddPolyline(shell, 7, line, 0, w);
    ImVec2 visor[6] = { {c.x - r * 0.64f, c.y - r * 0.16f}, {c.x + r * 0.64f, c.y - r * 0.16f}, {c.x + r * 0.56f, c.y + r * 0.22f},
                        {c.x + r * 0.16f, c.y + r * 0.38f}, {c.x - r * 0.16f, c.y + r * 0.38f}, {c.x - r * 0.56f, c.y + r * 0.22f} };
    dl->AddConvexPolyFilled(visor, 6, IM_COL32(110, 185, 235, 40));
    dl->AddPolyline(visor, 6, line, ImDrawFlags_Closed, w);
    dl->AddLine(ImVec2(c.x - r * 0.48f, c.y - r * 0.04f), ImVec2(c.x - r * 0.12f, c.y - r * 0.04f), line, w * 0.8f);
    dl->AddLine(ImVec2(c.x - r * 0.42f, c.y + r * 0.62f), ImVec2(c.x - r * 0.18f, c.y + r * 0.74f), line, w * 0.7f);
    dl->AddLine(ImVec2(c.x + r * 0.42f, c.y + r * 0.62f), ImVec2(c.x + r * 0.18f, c.y + r * 0.74f), line, w * 0.7f);
    // active camo: faint refractive ripples sweeping down through the helmet
    for (int k = 0; k < 4; k++) {
        float ph = fmodf(t * 0.45f + k * 0.25f, 1.0f), y = c.y - r * 0.8f + ph * r * 1.7f;
        float a = sinf(ph * 3.14159f) * 0.45f, wob = sinf(t * 3.0f + k) * r * 0.10f;
        dl->AddBezierCubic(ImVec2(c.x - r * 0.78f, y), ImVec2(c.x - r * 0.26f, y - wob), ImVec2(c.x + r * 0.26f, y + wob),
                           ImVec2(c.x + r * 0.78f, y), IM_COL32(190, 235, 255, (int)(a * 255)), w * 0.6f);
    }
}

static void draw_tile(float t) {
    ImGuiIO& io = ImGui::GetIO();
    ImVec4 r = tile_rect();
    ImDrawList* dl = ImGui::GetForegroundDrawList();
    ImVec2 a(r.x, r.y), b(r.z, r.w);
    ImVec2 m = io.MousePos;
    g_hover_tile = m.x >= a.x && m.x <= b.x && m.y >= a.y && m.y <= b.y;
    float h = b.y - a.y, w = b.x - a.x;
    // colours sampled from the stock tiles (START): frame, label, icon line, highlight bar
    const ImU32 frame = g_hover_tile ? IM_COL32(150, 215, 250, 255) : IM_COL32(78, 132, 168, 230);
    const ImU32 label = IM_COL32(112, 178, 222, 255), icon = g_hover_tile ? IM_COL32(150, 215, 250, 255) : IM_COL32(96, 168, 220, 255);
    dl->AddRectFilled(a, b, g_hover_tile ? IM_COL32(24, 44, 62, 225) : IM_COL32(14, 26, 38, 200));
    dl->AddRect(a, b, frame, 0, 0, g_hover_tile ? 2.5f : 1.2f);
    dl->AddRectFilled(ImVec2(a.x, b.y + h * 0.035f), ImVec2(b.x, b.y + h * 0.065f),
                      g_hover_tile ? IM_COL32(120, 205, 250, 255) : IM_COL32(70, 120, 150, 200));   // highlight bar
    draw_helmet(dl, ImVec2(a.x + w * 0.5f, a.y + h * 0.40f), h * 0.27f, t, icon);
    float fs = h * 0.115f;
    dl->AddRectFilled(ImVec2(a.x + 1, b.y - fs * 1.45f), ImVec2(b.x - 1, b.y - 1), IM_COL32(4, 8, 12, 200));
    dl->AddText(g_font_label, fs, ImVec2(a.x + fs * 0.35f, b.y - fs * 1.25f), label, "BOTS");
    char sum[64];
    if (g_shm->bots_enabled && g_shm->lidar_mode)
        sprintf_s(sum, "LiDAR %u", g_shm->lidar_players);
    else if (g_shm->bots_enabled)
    {
        // the list in a few words: 7v8 Legendary, 12 bots Mixed (more than two teams), No bots
        const hb_roster& R = g_ff_lobby ? g_ff_roster : g_roster;   // (Firefight: its own list)
        int teams = 0, tier = -1, n = R.n;
        for (int t = 0; t < HB_TEAMS_MAX; t++) teams += roster_on_team(R, t) > 0;
        for (int i = 0; i < n; i++) tier = tier < 0 || tier == R.bot[i].tier ? R.bot[i].tier : 5;
        const char* d = tier < 0 ? "" : DIFF_NAME[tier < 6 ? tier : 5];
        if (!n) sprintf_s(sum, "No bots");
        else if (g_ff_lobby && g_session != 5) sprintf_s(sum, "%d %s %s", n, n == 1 ? "bot" : "bots", d);   // (ODST: one team; Reach's reads 3v4 like a custom game)
        else if (teams <= 2) sprintf_s(sum, "%dv%d %s", roster_on_team(R, 0), roster_on_team(R, 1), d);
        else sprintf_s(sum, "%d bots %s", R.n, d);
    }
    else
        sprintf_s(sum, "OFF");
    float ss = fs * 0.78f;
    ImVec2 ts = g_font_label->CalcTextSizeA(ss, 1e9f, 0, sum);
    dl->AddText(g_font_label, ss, ImVec2(b.x - ts.x - fs * 0.35f, b.y - fs * 1.12f), IM_COL32(150, 195, 222, 230), sum);
    // key prompt under the tile, like MCC's button prompts: [B] Bots (hidden under the open panel)
    float ks = fs * 0.85f, ky = b.y + h * 0.10f;
    if (!g_panel) {                                          // (the device used last: its buttons, both sticks on a pad)
        const char* key = g_pad_last ? "LS+RS" : "B";
        float kw = g_font_label->CalcTextSizeA(ks, 1e9f, 0, key).x + ks * 0.66f;
        dl->AddRect(ImVec2(a.x, ky), ImVec2(a.x + kw, ky + ks * 1.2f), IM_COL32(170, 200, 215, 220), 2.0f, 0, 1.2f);
        dl->AddText(g_font_label, ks, ImVec2(a.x + ks * 0.33f, ky + ks * 0.1f), IM_COL32(200, 220, 230, 240), key);
        dl->AddText(g_font_label, ks, ImVec2(a.x + kw + ks * 0.4f, ky + ks * 0.1f), IM_COL32(170, 200, 215, 230), "Bots");
    }
    if (g_hover_tile && ImGui::IsMouseClicked(0)) g_panel = true;
}

// a texture from RGBA pixels for the profile view's pictures (game logos, bot looks), on MCC's own device
static unsigned long long make_texture(int w, int h, const unsigned char* rgba) {
    if (!g_dev) return 0;
    D3D11_TEXTURE2D_DESC d{};
    d.Width = w; d.Height = h; d.MipLevels = 1; d.ArraySize = 1; d.Format = DXGI_FORMAT_R8G8B8A8_UNORM;
    d.SampleDesc.Count = 1; d.Usage = D3D11_USAGE_DEFAULT; d.BindFlags = D3D11_BIND_SHADER_RESOURCE;
    D3D11_SUBRESOURCE_DATA sd{ rgba, (UINT)w * 4, 0 };
    ID3D11Texture2D* t = nullptr;
    ID3D11ShaderResourceView* v = nullptr;
    if (FAILED(g_dev->CreateTexture2D(&d, &sd, &t))) return 0;
    g_dev->CreateShaderResourceView(t, nullptr, &v);
    t->Release();
    return (unsigned long long)(intptr_t)v;
}

// the profile view's 3D bot (model_view.cpp) on MCC's device, the game's state kept around it
static unsigned long long render_model(const wchar_t* path, const mv_pick* picks, int n, const mv_colors& c, float yaw,
                                       int w, int h) {
    return mv_render(g_dev, g_ctx, path, picks, n, c, yaw, w, h);
}
static bool model_has(const wchar_t* path, const char* region, const char* perm) { return mv_has(g_dev, path, region, perm); }

// The BOTS panel's sounds (bots_panel.cpp panel_sound_wav makes them): played from memory (PlaySound, async: one at
// a time, the next cuts the last short), made again when the volume changes (playback stopped first: it reads them)
static void play_sound(int kind, uint32_t volume) {
    static char wav[5][12000];
    static uint32_t made[5] = { 999, 999, 999, 999, 999 };
    if (kind < PS_MOVE || kind > PS_ERROR || !volume) return;
    if (made[kind] != volume) {
        PlaySoundW(nullptr, nullptr, 0);
        if (!panel_sound_wav(kind, volume, wav[kind], (int)sizeof wav[kind])) return;
        made[kind] = volume;
    }
    PlaySoundW((LPCWSTR)wav[kind], nullptr, SND_MEMORY | SND_ASYNC | SND_NODEFAULT);
}

static void draw_panel() {
    // the BOTS panel (bots_panel.cpp): the exact bots of the next match, saved in botcore.ini
    bots_panel_env e{};
    e.make_texture = make_texture;
    e.render_model = render_model; e.model_has = model_has; e.model_zoom = mv_set_zoom; e.model_head = mv_head; e.model_headroom = mv_set_headroom;
    e.roster = g_ff_lobby ? &g_ff_roster : &g_roster;     // (Firefight: its own list; the 16-player list is kept)
    e.enabled = &g_shm->bots_enabled; e.difficulty = &g_shm->difficulty;
    e.lidar_mode = &g_shm->lidar_mode; e.lidar_players = &g_shm->lidar_players;
    e.collector = &g_collector;
    e.sounds = &g_sounds; e.play_sound = play_sound; e.scale_games = &g_scale_games;
    e.pad = g_pad_last;
    e.title = (int)g_session; e.scale = ui_scale();
    e.f_title = e.f_head = e.f_body = e.f_small = g_font_small;
    e.open = &g_panel;
    if (bots_panel(e)) { g_shm->settings_seq++; save_settings(); }
}

// --- the bug report over a match. MCC shows the mouse cursor over a match only for its own menus, so cursor up = the
// pause menu: the "Report bot bug" button shows; F9 opens the window any time in a match. Save writes
// logs\bug_reports\<time>\report.json; the brain (tools/live_bots.py) adds the match recording and the moment to it,
// tools/bug_reports.py lists them for review.
extern uint32_t g_playback;          // botcore.cpp: a Theater film is running
int mcc_menu_state();
void bug_reports_dir(wchar_t* out);  // botcore.cpp
static bool g_bug_open, g_bug_was_open, g_bug_hover, g_bug_from_menu;
static bool g_bug_ui;                // last frame: the report UI is up over the match (the pause menu's button, the window)
static char g_bug_text[1001];

static bool in_match() {
    return bots_title(g_session) && mcc_menu_state() == 1 && !g_playback;
}

static bool cursor_up() {
    CURSORINFO ci{ sizeof ci };
    return GetCursorInfo(&ci) && (ci.flags & CURSOR_SHOWING) && GetForegroundWindow() == g_hwnd;
}

static void save_bug_report() {
    SYSTEMTIME st;
    GetLocalTime(&st);
    char stamp[32];
    sprintf_s(stamp, "%04d%02d%02d-%02d%02d%02d", st.wYear, st.wMonth, st.wDay, st.wHour, st.wMinute, st.wSecond);
    wchar_t dir[MAX_PATH], file[MAX_PATH];
    bug_reports_dir(dir);
    swprintf_s(file, L"%s\\%S", dir, stamp);
    CreateDirectoryW(file, nullptr);
    swprintf_s(file, L"%s\\%S\\report.json", dir, stamp);
    FILE* f = _wfopen(file, L"wb");
    if (!f) { logf("bug report: cannot write %S", stamp); return; }
    fprintf(f, "{\"time\": \"%04d-%02d-%02d %02d:%02d:%02d\", \"unix\": %lld, \"title\": %ld, \"how\": \"%s\", \"description\": \"",
            st.wYear, st.wMonth, st.wDay, st.wHour, st.wMinute, st.wSecond, (long long)_time64(nullptr), (long)g_session,
            g_bug_from_menu ? "pause menu" : "F9");
    for (const unsigned char* p = (const unsigned char*)g_bug_text; *p; p++) {   // JSON string (UTF-8 kept as is)
        if (*p == '"' || *p == '\\') fprintf(f, "\\%c", *p);
        else if (*p == '\n') fputs("\\n", f);
        else if (*p == '\t') fputs("\\t", f);
        else if (*p < 0x20) { if (*p != '\r') fprintf(f, "\\u%04x", *p); }
        else fputc(*p, f);
    }
    fputs("\"}\n", f);
    fclose(f);
    strcpy_s(g_shm->notice, "BUG REPORT SAVED|Saved with this match's recording.");
    g_shm->notice_seq++;
    g_bug_text[0] = 0;
    logf("bug report %s saved", stamp);
}

static void make_rtv(IDXGISwapChain* sc) {
    ID3D11Texture2D* back = nullptr;
    if (SUCCEEDED(sc->GetBuffer(0, __uuidof(ID3D11Texture2D), (void**)&back)) && back) {
        g_dev->CreateRenderTargetView(back, nullptr, &g_rtv);
        back->Release();
    }
}

// While the panel is open the lobby must not see the cursor. Its mouse messages were kept from it, but MCC reads the
// cursor's place itself: moving right of the panel slid its roster ("game session") pane open, and the lobby probe, no
// longer seeing the lobby's tiles, closed the panel. Every caller but our own frame (t_ours) gets the cursor parked
// where it was when the panel opened (on the BOTS tile, where the lobby has nothing of its own); its raw mouse input is
// kept from it too (wndproc).
typedef BOOL(WINAPI* cursorpos_fn)(LPPOINT);
static cursorpos_fn o_getcursorpos, o_getphyscursorpos;
static volatile bool g_parked;
static POINT g_park_at;
static thread_local bool t_ours;
static BOOL WINAPI h_getcursorpos(LPPOINT p) {
    if (g_parked && !t_ours && p) { *p = g_park_at; return TRUE; }
    return o_getcursorpos(p);
}
static BOOL WINAPI h_getphyscursorpos(LPPOINT p) {
    if (g_parked && !t_ours && p) { *p = g_park_at; return TRUE; }
    return o_getphyscursorpos(p);
}

// The pad. MCC reads it itself (its lobby: XInput 1.3, XInputGetState), so the panel's A would also be the lobby's A:
// MCC's reads answer "at rest" while the panel is open, and until every button is let go after it closes (the B that
// closed it must not reach the lobby). Our own frame's reads (t_ours; ImGui's backend reads XInput 1.4 anyway) get the
// real pad
typedef DWORD(WINAPI* xinput_get_fn)(DWORD, XINPUT_STATE*);
static xinput_get_fn o_xinput_get;
static DWORD WINAPI h_xinput_get(DWORD user, XINPUT_STATE* st) {
    DWORD r = o_xinput_get(user, st);
    if (r == ERROR_SUCCESS && st && g_pad_held && !t_ours) memset(&st->Gamepad, 0, sizeof st->Gamepad);
    return r;
}
// each frame over the lobby: both sticks pressed together open or close the panel (MCC's lobby has no use for that);
// B at its top level backs out of it (in a list or a dialog, B is theirs: ImGui and the panel close those); which
// device was used last
static void pad_frame() {
    bool any = false;
    for (int k = ImGuiKey_GamepadStart; k <= ImGuiKey_GamepadRStickDown; k++) any |= ImGui::IsKeyDown((ImGuiKey)k);
    if (any) g_pad_last = true;
    if (g_show_tile) {
        bool l3 = ImGui::IsKeyDown(ImGuiKey_GamepadL3), r3 = ImGui::IsKeyDown(ImGuiKey_GamepadR3);
        if ((l3 && ImGui::IsKeyPressed(ImGuiKey_GamepadR3, false)) || (r3 && ImGui::IsKeyPressed(ImGuiKey_GamepadL3, false))) {
            if (g_panel) play_sound(PS_BACK, g_sounds);
            g_panel = !g_panel;
        } else if (g_panel && !g_popup_before && ImGui::IsKeyPressed(ImGuiKey_GamepadFaceRight, false)) {
            g_panel = false;
            play_sound(PS_BACK, g_sounds);
        }
    }
    g_pad_held = (g_show_tile && g_panel) || (g_pad_held && any);
}

static DWORD g_esc_eat;              // Esc closed the panel at this tick: its release (and repeats) stay ours
static LRESULT CALLBACK wndproc(HWND h, UINT msg, WPARAM wp, LPARAM lp) {
    if (msg == WM_KEYDOWN || msg == WM_LBUTTONDOWN || msg == WM_RBUTTONDOWN || msg == WM_MOUSEWHEEL) g_pad_last = false;
    // MCC backs out of the lobby on Esc's release: closing the panel with Esc left the lobby too (live Nov 6), as the
    // pad's B would without g_pad_held
    if (g_esc_eat && (msg == WM_KEYUP || msg == WM_KEYDOWN) && wp == VK_ESCAPE && GetTickCount() - g_esc_eat < 3000) {
        if (msg == WM_KEYUP) g_esc_eat = 0;
        return 0;
    }
    if (g_ready && g_show_tile) {
        bool typing = g_panel && ImGui::GetIO().WantTextInput;   // a bot's name being typed: B and Esc are its
        if (!typing && msg == WM_KEYDOWN && wp == 'B' && !(lp & (1 << 30))) { g_panel = !g_panel; return 0; }
        if (!typing && msg == WM_KEYDOWN && wp == VK_ESCAPE && g_panel && !ImGui::IsPopupOpen("", ImGuiPopupFlags_AnyPopupId)) {
            g_panel = false; play_sound(PS_BACK, g_sounds);   // (a confirmation open: Esc closes just that)
            g_esc_eat = GetTickCount() | 1; return 0;
        }
        ImGui_ImplWin32_WndProcHandler(h, msg, wp, lp);
        bool mouse = msg >= WM_MOUSEFIRST && msg <= WM_MOUSELAST;
        bool keys = msg >= WM_KEYFIRST && msg <= WM_KEYLAST;
        if (g_panel && msg == WM_INPUT) return DefWindowProcW(h, msg, wp, lp);   // (raw input: cleaned up, not passed on)
        if ((g_panel && (mouse || keys)) || (g_hover_tile && mouse && msg != WM_MOUSEMOVE)) return 0;   // ours
    } else if (g_ready && in_match()) {                         // the bug report over a match
        bool typing = g_bug_open && ImGui::GetIO().WantTextInput;
        if (!typing && msg == WM_KEYDOWN && wp == VK_F9 && !(lp & (1 << 30))) { g_bug_open = !g_bug_open; return 0; }
        // ImGui hears the game's input only while the report UI is up: fed in play it queued every key and letter
        // typed, with no frame to take them, and F9's window took the backlog as typing; its WM_SETCURSOR answer put
        // the arrow over the game ("windows mouse cursor flickering over gameplay")
        if (g_bug_open || g_bug_ui) ImGui_ImplWin32_WndProcHandler(h, msg, wp, lp);
        bool mouse = msg >= WM_MOUSEFIRST && msg <= WM_MOUSELAST;
        bool keys = msg >= WM_KEYFIRST && msg <= WM_KEYLAST;
        if (g_bug_open && (mouse || keys || msg == WM_INPUT)) return 0;   // typing never reaches the game
        if (g_bug_hover && mouse && msg != WM_MOUSEMOVE) return 0;
    }
    return CallWindowProcW(o_wndproc, h, msg, wp, lp);
}

// A notice from the brain (LiDAR: "JUMP LEARNED|Every bot will use it ..."; the AI director's calls),
// drawn over the game whenever notice_seq changes: 3.5 s, longer for a long line (time to read it, up
// to 7 s), top centre, MCC's dark panel and blue frame, a gold title. No window, so it never takes the
// mouse or keys from the game.
static uint32_t g_notice_seq;
static DWORD g_notice_at, g_notice_ms = 3500;

static bool notice_active() {
    if (g_shm->notice_seq != g_notice_seq) {
        g_notice_seq = g_shm->notice_seq;
        g_notice_at = GetTickCount();
        size_t n = strnlen(g_shm->notice, 128);
        g_notice_ms = 3500 + (n > 50 ? (DWORD)(n - 50) * 45 : 0);
        if (g_notice_ms > 7000) g_notice_ms = 7000;
    }
    return g_notice_at && GetTickCount() - g_notice_at < g_notice_ms;
}

static void draw_notice() {
    char text[129];
    memcpy(text, g_shm->notice, 128);
    text[128] = 0;
    char* line = strchr(text, '|');
    if (line) *line++ = 0;
    float t = (GetTickCount() - g_notice_at) / 1000.0f, end = g_notice_ms / 1000.0f;
    float a = t < 0.15f ? t / 0.15f : (t > end - 0.6f ? (end - t) / 0.6f : 1.0f);   // fade in, hold, fade out
    if (a <= 0) return;
    ImGuiIO& io = ImGui::GetIO();
    ImDrawList* dl = ImGui::GetForegroundDrawList();
    ui_page pg = mcc_content(io.DisplaySize.x, io.DisplaySize.y);    // (MCC's page: 16:10 and 32:9 alike)
    float h = pg.h, ts = h * 0.036f, ls = h * 0.022f;
    ImVec2 tsz = g_font_label->CalcTextSizeA(ts, 1e9f, 0, text);
    ImVec2 lsz = line ? g_font_small->CalcTextSizeA(ls, 1e9f, 0, line) : ImVec2(0, 0);
    float w = (tsz.x > lsz.x ? tsz.x : lsz.x) + h * 0.06f, bh = tsz.y + lsz.y + h * 0.035f;
    ImVec2 p0(io.DisplaySize.x * 0.5f - w * 0.5f, pg.y0 + h * 0.17f), p1(p0.x + w, p0.y + bh);
    int A = (int)(a * 255);
    dl->AddRectFilled(p0, p1, IM_COL32(6, 18, 28, (int)(a * 215)));
    dl->AddRect(p0, p1, IM_COL32(110, 200, 255, (int)(a * 230)), 0, 0, 2.0f);
    dl->AddRectFilled(p0, ImVec2(p0.x + h * 0.006f, p1.y), IM_COL32(255, 201, 77, A));          // gold edge
    dl->AddText(g_font_label, ts, ImVec2(p0.x + (w - tsz.x) * 0.5f, p0.y + h * 0.012f), IM_COL32(255, 210, 100, A), text);
    if (line)
        dl->AddText(g_font_small, ls, ImVec2(p0.x + (w - lsz.x) * 0.5f, p0.y + h * 0.016f + tsz.y),
                    IM_COL32(225, 236, 245, A), line);
}

static HRESULT __stdcall h_present(IDXGISwapChain* sc, UINT sync, UINT flags) {
    __try {
        if (!g_ready && g_shm) {
            if (SUCCEEDED(sc->GetDevice(__uuidof(ID3D11Device), (void**)&g_dev))) {
                g_dev->GetImmediateContext(&g_ctx);
                DXGI_SWAP_CHAIN_DESC d;
                sc->GetDesc(&d);
                g_hwnd = d.OutputWindow;
                ImGui::CreateContext();
                ImGuiIO& io = ImGui::GetIO();
                io.IniFilename = nullptr;
                float px = (float)d.BufferDesc.Height / 1080.0f;
                ImFontConfig fc; fc.OversampleH = 2;
                g_font_small = io.Fonts->AddFontFromFileTTF("C:\\Windows\\Fonts\\bahnschrift.ttf", 22.0f * px, &fc);
                g_font_label = io.Fonts->AddFontFromFileTTF("C:\\Windows\\Fonts\\bahnschrift.ttf", 40.0f * px, &fc);
                if (!g_font_small) g_font_small = g_font_label = io.Fonts->AddFontDefault();   // fallback
                io.FontDefault = g_font_small;
                ImGui::StyleColorsDark();
                ImGuiStyle& st = ImGui::GetStyle();
                st.WindowRounding = 0; st.FrameRounding = 0; st.WindowBorderSize = 2;
                st.Colors[ImGuiCol_WindowBg] = ImVec4(0.03f, 0.08f, 0.12f, 0.94f);
                st.Colors[ImGuiCol_Border] = ImVec4(0.43f, 0.78f, 1.0f, 0.9f);
                st.Colors[ImGuiCol_TitleBgActive] = ImVec4(0.06f, 0.2f, 0.3f, 1);
                st.Colors[ImGuiCol_FrameBg] = ImVec4(0.08f, 0.18f, 0.26f, 1);
                st.Colors[ImGuiCol_SliderGrab] = ImVec4(0.45f, 0.8f, 1.0f, 1);
                st.Colors[ImGuiCol_CheckMark] = ImVec4(0.45f, 0.8f, 1.0f, 1);
                st.Colors[ImGuiCol_Button] = ImVec4(0.08f, 0.2f, 0.3f, 1);
                st.Colors[ImGuiCol_ButtonHovered] = ImVec4(0.15f, 0.38f, 0.55f, 1);
                ImGui_ImplWin32_Init(g_hwnd);
                ImGui_ImplDX11_Init(g_dev, g_ctx);
                make_rtv(sc);
                o_wndproc = (WNDPROC)SetWindowLongPtrW(g_hwnd, GWLP_WNDPROC, (LONG_PTR)wndproc);
                g_ready = true;
                logf("overlay ready (window %p)", g_hwnd);
            }
        }
        if (g_ready) {
            DXGI_SWAP_CHAIN_DESC scd;                             // the back buffer's size (MCC lays out on it)
            if (SUCCEEDED(sc->GetDesc(&scd))) {
                RECT cr{};
                GetClientRect(g_hwnd, &cr);
                if (scd.BufferDesc.Width != (UINT)g_bb_w || scd.BufferDesc.Height != (UINT)g_bb_h)
                    logf("overlay: back buffer %ux%u, window client %ldx%ld", scd.BufferDesc.Width, scd.BufferDesc.Height,
                         cr.right - cr.left, cr.bottom - cr.top);
                g_bb_w = (float)scd.BufferDesc.Width; g_bb_h = (float)scd.BufferDesc.Height;
            }
            static unsigned frame;
            if (bots_title(g_session) && frame++ % 8 == 0) probe_lobby(sc);   // the lobby must be on screen
            g_show_tile = in_ce_menus();
            if (!g_show_tile) g_panel = false;
            if (g_show_tile && g_panel && !g_parked && o_getcursorpos) { o_getcursorpos(&g_park_at); g_parked = true; }
            else if (!(g_show_tile && g_panel)) g_parked = false;    // (closed: the lobby sees the cursor again)
            bool notice = notice_active();
            bool match = !g_show_tile && in_match();
            if (!match) g_bug_open = false;
            bool pause = match && cursor_up();
            bool bug = match && (pause || g_bug_open);
            g_bug_ui = bug;
            if (!bug) g_bug_hover = false;
            if (g_show_tile || notice || bug) {
                ImGuiIO& gio = ImGui::GetIO();
                if (bug && g_bug_open && !g_bug_was_open) {          // F9 opened the window: nothing typed before is its text
                    gio.ClearEventsQueue();
                    gio.ClearInputKeys();
                }
                // the OS cursor is ours to change only under our own UI (the lobby's panel, the report window, the pause
                // menu's button): over play a notice's frame set the arrow on the game's hidden cursor
                if (g_show_tile || bug) gio.ConfigFlags &= ~ImGuiConfigFlags_NoMouseCursorChange;
                else gio.ConfigFlags |= ImGuiConfigFlags_NoMouseCursorChange;
                ImGui_ImplDX11_NewFrame();
                t_ours = true;                                       // (our frame reads the real cursor)
                ImGui_ImplWin32_NewFrame();
                t_ours = false;
                {                                                    // window units onto the whole back buffer
                    ImGuiIO& fio = ImGui::GetIO();
                    if (fio.DisplaySize.x > 0 && fio.DisplaySize.y > 0 && g_bb_w > 0 && g_bb_h > 0)
                        fio.DisplayFramebufferScale = ImVec2(g_bb_w / fio.DisplaySize.x, g_bb_h / fio.DisplaySize.y);
                }
                ImGui::GetIO().MouseDrawCursor = bug && g_bug_open && !pause;   // the game hides the cursor in play
                // keyboard navigation (Tab, arrows, Space / Enter, Esc) while the panel is open: every control by keys
                // (the keyboard pass: without it Tab reached only the two text boxes); off otherwise, so the lobby's
                // own Enter can never press the BOTS tile
                const int NAV = ImGuiConfigFlags_NavEnableKeyboard | ImGuiConfigFlags_NavEnableGamepad;   // (and the pad's)
                if (g_show_tile && g_panel) ImGui::GetIO().ConfigFlags |= NAV;
                else ImGui::GetIO().ConfigFlags &= ~NAV;
                ImGui::NewFrame();
                pad_frame();
                if (bug) {
                    ImVec2 bb = bb_size();
                    bug_report_env be{ ui_scale(), g_font_small, pause, &g_bug_open,
                                       g_bug_text, (int)sizeof g_bug_text, false, bb.x, bb.y };
                    bool saved = bug_report_ui(be);
                    if (g_bug_open && !g_bug_was_open) g_bug_from_menu = pause;   // how it was opened, for the report
                    if (saved) save_bug_report();
                    g_bug_hover = be.hovered;
                }
                g_bug_was_open = g_bug_open;
                // the tile draws on the foreground layer, over every window: with the panel open it is
                // left out (it drew over the panel's header), and takes no clicks
                if (g_show_tile && !g_panel) draw_tile((float)ImGui::GetTime());
                else g_hover_tile = false;
                if (g_show_tile && g_panel) draw_panel();
                g_popup_before = ImGui::IsPopupOpen("", ImGuiPopupFlags_AnyPopupId | ImGuiPopupFlags_AnyPopupLevel);
                if (notice) draw_notice();
                ImGui::Render();
                g_ctx->OMSetRenderTargets(1, &g_rtv, nullptr);
                ImGui_ImplDX11_RenderDrawData(ImGui::GetDrawData());
            }
        }
    } __except (EXCEPTION_EXECUTE_HANDLER) { g_ready = false; }
    return o_present(sc, sync, flags);
}

static HRESULT __stdcall h_resize(IDXGISwapChain* sc, UINT n, UINT w, UINT h, DXGI_FORMAT f, UINT fl) {
    if (g_rtv) { g_rtv->Release(); g_rtv = nullptr; }
    HRESULT r = o_resize(sc, n, w, h, f, fl);
    if (g_ready) make_rtv(sc);
    return r;
}

// Present/ResizeBuffers addresses from a throwaway swap chain's vtable (they are shared by all
// swap chains in the process).
bool overlay_start() {
    g_exe = (uint8_t*)GetModuleHandleW(nullptr);
    WNDCLASSEXW wc{ sizeof wc, CS_CLASSDC, DefWindowProcW, 0, 0, GetModuleHandleW(nullptr), 0, 0, 0, 0, L"botcore_dummy", 0 };
    RegisterClassExW(&wc);
    HWND hw = CreateWindowW(L"botcore_dummy", L"", WS_OVERLAPPEDWINDOW, 0, 0, 100, 100, 0, 0, wc.hInstance, 0);
    DXGI_SWAP_CHAIN_DESC d{};
    d.BufferCount = 1; d.BufferDesc.Format = DXGI_FORMAT_R8G8B8A8_UNORM; d.BufferUsage = DXGI_USAGE_RENDER_TARGET_OUTPUT;
    d.OutputWindow = hw; d.SampleDesc.Count = 1; d.Windowed = TRUE;
    IDXGISwapChain* sc = nullptr; ID3D11Device* dev = nullptr; ID3D11DeviceContext* ctx = nullptr;
    HRESULT hr = D3D11CreateDeviceAndSwapChain(nullptr, D3D_DRIVER_TYPE_HARDWARE, nullptr, 0, nullptr, 0,
                                               D3D11_SDK_VERSION, &d, &sc, &dev, nullptr, &ctx);
    if (FAILED(hr)) { logf("overlay: dummy swap chain failed %08lx", hr); DestroyWindow(hw); return false; }
    void** vt = *(void***)sc;
    void* present = vt[8], *resize = vt[13];
    sc->Release(); dev->Release(); ctx->Release();
    DestroyWindow(hw);
    UnregisterClassW(L"botcore_dummy", wc.hInstance);
    void* createfile = GetProcAddress(GetModuleHandleW(L"kernelbase.dll"), "CreateFileW");
    bool ok = MH_CreateHook(present, (void*)&h_present, (void**)&o_present) == MH_OK &&
              MH_CreateHook(resize, (void*)&h_resize, (void**)&o_resize) == MH_OK &&
              MH_CreateHook(createfile, (void*)&h_createfile, (void**)&o_createfile) == MH_OK &&
              MH_EnableHook(present) == MH_OK && MH_EnableHook(resize) == MH_OK && MH_EnableHook(createfile) == MH_OK;
    logf("overlay hooks %s", ok ? "on" : "FAILED");
    HMODULE u32 = GetModuleHandleW(L"user32.dll");                   // (the cursor kept from the lobby: h_getcursorpos)
    void* gcp = u32 ? (void*)GetProcAddress(u32, "GetCursorPos") : nullptr;
    void* gpcp = u32 ? (void*)GetProcAddress(u32, "GetPhysicalCursorPos") : nullptr;
    bool cur = gcp && MH_CreateHook(gcp, (void*)&h_getcursorpos, (void**)&o_getcursorpos) == MH_OK && MH_EnableHook(gcp) == MH_OK;
    if (gpcp && MH_CreateHook(gpcp, (void*)&h_getphyscursorpos, (void**)&o_getphyscursorpos) == MH_OK) MH_EnableHook(gpcp);
    logf("cursor hooks %s", cur ? "on" : "FAILED (the lobby may still follow the cursor under the panel)");
    HMODULE xi = GetModuleHandleW(L"xinput1_3.dll");                 // (the pad kept from the lobby: h_xinput_get)
    void* xg = xi ? (void*)GetProcAddress(xi, "XInputGetState") : nullptr;
    bool pad = xg && MH_CreateHook(xg, (void*)&h_xinput_get, (void**)&o_xinput_get) == MH_OK && MH_EnableHook(xg) == MH_OK;
    logf("pad hook %s", pad ? "on" : "FAILED (MCC's lobby may also act on the pad under the panel)");
    return ok;
}
