"""One command for a bot match: in a Halo CE game (MCC, anti-cheat off), load botcore if needed,
fill both teams with bots, and run the brain.

usage: python -I play.py [--red N] [--blue N] [--tier T[,T..]] [--minutes M]
  --red/--blue  total players wanted per team, people included (default 4 and 4)
  --tier        novice | average | strong | top, or a list dealt out in order (default mixed)
Stop with Ctrl+C. Bots stay in the game, standing still, until the match ends.
"""
import argparse
import glob
import os
import struct
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
import botctl  # noqa: E402
import recording  # noqa: E402
from brain.live import Live, LiveH2, LiveH3, LiveReach, LiveODST, LiveH4F  # noqa: E402
from brain.names import make_names  # noqa: E402
from brain import looks  # noqa: E402

HB_MAGIC = 0x54424248
VARIANT = 0x2E9F770     # halo1 global game variant (MCC 1.3528)
H4F_DLL = {"h2a": "groundhog.dll", "h4": "halo4.dll"}
GAME_ID = {1: "ce", 2: "h2", 3: "h3", 4: "reach", 5: "odst", 6: "h2a", 7: "h4"}     # botcore's game ids
GAME_DLL = (("haloreach.dll", "reach"), ("halo3odst.dll", "odst"), ("groundhog.dll", "h2a"), ("halo4.dll", "h4"),
            ("halo3.dll", "h3"), ("halo2.dll", "h2"))


CARNAGE = os.path.join(os.environ.get("USERPROFILE", ""), "AppData", "LocalLow", "MCC", "Temporary",
                       "mpcarnagereport1_3528_0_0.xml")


def keep_carnage(traces):
    """MCC's post-game report (kills, deaths, score per player) beside the last match's trace as <trace>.carnage.xml:
    MCC overwrites its one file every match. Done as the NEXT match starts: botcore stops this script the moment a
    match ends (a copy after the match never ran). The report belongs to the newest trace when MCC wrote it within 10
    minutes after that trace's last line (a quit match has none)."""
    if not traces or not os.path.exists(CARNAGE):
        return None
    last = max(traces, key=os.path.getmtime)
    dest = last[:-len(".jsonl")] + ".carnage.xml"
    gap = os.path.getmtime(CARNAGE) - os.path.getmtime(last)
    if os.path.exists(dest) or not -30 <= gap <= 600:
        return None
    with open(CARNAGE, "rb") as src, open(dest, "wb") as dst:
        dst.write(src.read())
    return dest


def match_trace(map_name, logs=os.path.join(ROOT, "logs"), games=None, after=None):
    """This match's recording for the Observer: logs/trace_<map>_<date>-<time>.jsonl, one per match
    (one file per map lost a match whenever the next was on the same map). The oldest unsaved matches go, each with its
    paired files, so that with this one there are `games` (the Observer's Recording settings, tools/recording.py);
    saved matches and traces named any other way are never touched."""
    old = sorted(glob.glob(os.path.join(logs, "trace_*_????????-??????.jsonl")), key=os.path.getmtime)
    keep_carnage(old)
    recording.rotate(logs, games, after)
    return os.path.join(logs, f"trace_{map_name}_{time.strftime('%Y%m%d-%H%M%S')}.jsonl")


def roster_plan(roster, players, teams, game):
    """The BOTS panel's bots for this match, [(team, name)] in the panel's order: Halo: CE team games have
    two teams (the panel's third and later teams play as red or blue); free for all, every bot its own team;
    never more than 16 players with the people already in (the panel's last bots stay out)."""
    out, used = [], {p["team"] for p in players}
    taken = {p["name"].lower() for p in players}
    team_ffa = 1
    for b in roster[:max(0, 16 - len(players))]:
        if teams:
            team = b["team"] % 2 if game == "ce" else b["team"]
        else:
            while team_ffa in used:
                team_ffa += 1
            team = team_ffa
            used.add(team)
        name = b["name"]
        while name.lower() in taken:                     # someone in the lobby has that name already
            name = name[:14] + str(len(taken) % 10)
        taken.add(name.lower())
        out.append((team, name))
    return out


def module_loaded(name):
    import ctypes as C
    from memread import find_pid, PROCESS
    pid = find_pid(PROCESS)
    if not pid:
        return False
    snap = C.windll.kernel32.CreateToolhelp32Snapshot(0x18, pid)      # TH32CS_SNAPMODULE | 32
    class ME(C.Structure):
        _fields_ = [("size", C.c_uint32), ("mid", C.c_uint32), ("pid", C.c_uint32), ("g", C.c_uint32), ("p", C.c_uint32),
                    ("base", C.c_void_p), ("bsize", C.c_uint32), ("h", C.c_void_p), ("name", C.c_wchar * 256), ("path", C.c_wchar * 260)]
    e = ME(); e.size = C.sizeof(ME)
    ok = C.windll.kernel32.Module32FirstW(C.c_void_p(snap), C.byref(e))
    found = False
    while ok:
        found = found or e.name.lower() == name
        ok = C.windll.kernel32.Module32NextW(C.c_void_p(snap), C.byref(e))
    C.windll.kernel32.CloseHandle(C.c_void_p(snap))
    return found


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--red", type=int, default=4)
    ap.add_argument("--blue", type=int, default=4)
    ap.add_argument("--tier", default="strong,average,top,novice")
    ap.add_argument("--minutes", type=float, default=30)
    ap.add_argument("--wait", type=float, default=0, help="seconds to wait for the match to load")
    ap.add_argument("--game", default="auto", choices=("auto", "ce", "h2", "h3", "reach", "odst", "h2a", "h4"))
    ap.add_argument("--teams", action="store_true", help="Halo 2/3: force a team game (normally read from the running variant)")
    ap.add_argument("--lidar", type=int, default=0, help="LiDAR mode: fill the match to this many players with "
                    "mapping bots (tools/lidar.py; your team in team games), no fighting")
    ap.add_argument("--ffa", action="store_true", help="Halo 3: force free for all (its variant is not read yet; default teams)")
    ap.add_argument("--roster", help="botcore.ini with the BOTS panel's list: exactly those bots join (names, "
                    "teams, difficulty, play style); --red / --blue fill teams only when there is no list")
    o = ap.parse_args()
    override = os.path.join(ROOT, "logs", "test_override.json")
    if os.path.exists(override):     # Claude's test games: these settings instead of the BOTS panel's,
        import json                  # so the player's botcore.ini and panel are never touched (logs\ is not in git)
        for k, v in json.load(open(override)).items():
            setattr(o, k, v)
        print("test override", override, vars(o))
    if o.game == "auto":        # botcore's hooks say which game ticks (1 CE, 2 H2, 3 H3 ...); else whichever DLL is loaded
        t0 = time.time()
        while True:             # none ticks while the match loads (a loading Reach match was read as CE)
            g = struct.unpack_from("<I", botctl.shm(), botctl.OFF_GAME)[0]
            if g or time.time() - t0 >= o.wait:
                break
            time.sleep(1)
        o.game = GAME_ID.get(g) or next((k for dll, k in GAME_DLL if module_loaded(dll)), "ce")
    print("game", o.game)
    h2 = o.game in ("h2", "h3", "reach", "odst", "h2a", "h4")   # the Halo 2 code path (H3, Reach are its successors; differences via the Live class)
    h3 = o.game == "h3"
    LiveCls = (lambda: LiveH4F(H4F_DLL[o.game])) if o.game in H4F_DLL else LiveODST if o.game == "odst" else LiveReach if o.game == "reach" else LiveH3 if h3 else LiveH2 if h2 else Live
    t0 = time.time()
    while True:                                      # Live() exits if MCC/halo1 is missing or anti-cheat is on
        try:
            live = LiveCls()
            if live.players():
                break
        except (SystemExit, Exception):
            if time.time() - t0 > o.wait:
                raise
        if time.time() - t0 > o.wait:
            sys.exit("no players in the game")
        time.sleep(1)
    while True:
        if h2:                                       # the H2 tag names table fills in a moment after spawn
            map_name = o.game + "_" + (live.map_name() or "")
        else:                                        # tag 0 is the scenario: levels\test\bloodgulch\bloodgulch
            map_name = live.tag_path(0).replace("/", "\\").rsplit("\\", 1)[-1]
        if not map_name.endswith("_") or time.time() - t0 > o.wait:
            break
        time.sleep(1)
    have_map = os.path.exists(os.path.join(ROOT, "maps", map_name, "heightmap.npz"))
    # Firefight without map data (ODST's kit exports none; Reach's DLC Firefight maps, Unearthed and Installation 04,
    # are not exported: bots never joined there, GitHub #12): the map-free escort brain
    escort = not have_map and (o.game == "odst" or (o.game == "reach" and getattr(live, "firefight", lambda: False)()))
    if not escort and not have_map and not o.lidar:     # LiDAR maps blind too
        sys.exit(f"bots do not know the map '{map_name}' yet (supported: "
                 f"{', '.join(d for d in os.listdir(os.path.join(ROOT, 'maps')) if os.path.isdir(os.path.join(ROOT, 'maps', d)))})")
    print("map", map_name)
    m = botctl.shm()
    if botctl.status(m)["magic"] != hex(HB_MAGIC):
        dll = max(glob.glob(os.path.join(ROOT, "native", "bin", "botcore*.dll")), key=os.path.getmtime)
        print("loading", os.path.basename(dll))
        exe = botctl.injector()                      # (put back if Windows Defender deleted it)
        if exe is None:
            sys.exit(1)
        subprocess.run([exe, os.path.abspath(dll)], check=True)
        for _ in range(50):
            if botctl.status(m)["magic"] == hex(HB_MAGIC):
                break
            time.sleep(0.1)
        else:
            sys.exit("botcore did not start (see native/bin/*.log)")
    players = live.players()
    driven = set(botctl.status(m)["bots"].values())
    for p in players:                                # bots made by an earlier session: drive them
        if p["name"].startswith("Bot ") and p["index"] not in driven:
            botctl.command(m, botctl.CMD_ADOPT, p["index"])
    # the running game variant (halo1 global, same layout as halo1\game_variants\*.bin):
    # +0x30 game engine (1 ctf, 2 slayer, ...), +0x34 teams (0 = free for all)
    if h2:
        v = live.variant()
        for _ in range(20):                          # engine 0: the match is still loading (read too
            if v["engine"] > 0:                      # early, a Team Slayer game got free for all bots)
                break
            time.sleep(0.5)
            v = live.variant()
        print("variant", v)
        teams = o.teams or (v["teams"] if v["teams"] is not None else not o.ffa)
    else:
        teams = struct.unpack("<I", live.g.read(live.g.base + VARIANT + 0x34, 4))[0] != 0
    print("teams" if teams else "free for all")
    machine = 1 + max([0] + [p["index"] for p in players])
    if o.game == "reach":     # a respawn pick left by the last match (a bot dead at its end) moves this match's first
        for i in range(botctl.MAX_PLAYERS):   # spawn to the old map's spot (live: Boneyard bots sent to Spire's zones,
            botctl.spawn_at(m, i, float("nan"), float("nan"), float("nan"), 0.0)   # out of bounds): called off
    # this install's 15 bots first (brain/looks.py: their names and looks are kept from match to match), then
    # made-up gamertags for any more
    taken = {p["name"].lower() for p in players}
    names = [n for n, _ in looks.profiles() if n.lower() not in taken][::-1]
    names = make_names(16, taken=[p["name"] for p in players] + names) + names   # (popped from the end)
    firefight = getattr(live, "firefight", lambda: False)()
    ff = firefight or o.game == "odst"
    reach_ff = firefight and o.game == "reach"      # Reach Firefight plays as a multiplayer custom game: its own BOTS list
    if reach_ff and not teams:                      # (ffbot=: a team and a style per bot, up to 7), red and blue teams only
        teams = True
        print("Firefight: red / blue teams (the Covenant AI are blue)")
    roster = botctl.roster(o.roster) if o.roster and not o.lidar else None
    ff_own = False                                   # the bots come from Firefight's own list (ffbot=)
    if roster is not None and ff:                    # Firefight's own list (botcore.cpp ff_seed: the first 7 till saved)
        ff_list = botctl.roster(o.roster, firefight=True)
        if reach_ff and ff_list is not None and not (len(ff_list) > 3 or any(b["team"] == 1 for b in ff_list)):
            # a Firefight list of up to 3 bots, all red, is the old panel's (it cannot make a blue bot or a fourth): the
            # multiplayer list (the custom game lobby's red / blue panel) is used, 7 bots at most, the teams evenly
            print("Firefight: the panel here has no blue team; playing the custom game list (red and blue)")
            ff_list = None
        ff_own = ff_list is not None
        if ff_list is not None:
            roster = ff_list
        elif reach_ff:
            sides = [[b for b in roster if b["team"] % 2 == k] for k in (0, 1)]
            roster = [b for pair in zip(*sides) for b in pair]                       # red, blue, red, blue ...
            roster += sides[0][len(sides[1]):] + sides[1][len(sides[0]):]            # (the longer team's rest)
            roster = roster[:botctl.REACH_FIREFIGHT_BOTS]
        else:
            roster = roster[:botctl.player_cap(o.game, firefight) - 1]
    # one cap on every path below, LiDAR included: ODST's Firefight holds 4 players in all (it crashed with more),
    # Reach's 8
    room = botctl.player_cap(o.game, firefight) - len(players)
    if reach_ff:                                     # never more than 7 bots
        room = min(room, botctl.REACH_FIREFIGHT_BOTS)
    if getattr(o, "max_bots", None) is not None:     # test override: leave room for Claude's probe bots
        room = min(room, int(o.max_bots))
    if room < botctl.MAX_PLAYERS - len(players):
        print(f"Firefight: {room} bots at most ({botctl.player_cap(o.game, firefight)} players in all)")
    if roster is not None:                           # the BOTS panel's list (ODST Firefight: its first 3, all your teammates;
                                                     # Reach Firefight: up to 8 in all, each bot on the red or blue team its row names)
        print(f"bot list: {len(roster)} bots from {o.roster}" + (f", {max(0, room)} join (Firefight)" if ff else ""))
        for team, name in roster_plan(roster, players, teams, o.game)[:max(0, room)]:
            if firefight and o.game == "reach":      # red (0) or blue (1) by its row; blue is the Campaign AI's team
                team = team % 2
            elif ff:
                team = players[0]["team"]
            r = botctl.add_bot(m, machine, team, name, o.game, look=looks.look(name, o.game), clan=looks.clan_of(name),
                               emblem=looks.emblem_of(name))
            print(f"bot {name} on team {team}:", "ok" if not r.get("fault") and r.get("result", -1) & 0xFFFFFFFF != 0xFFFFFFFF and r.get("result", -1) > -100 or r.get("result", -1) < -100 else r)
            machine += 1
    elif teams:
        count = {0: 0, 1: 0}
        for p in players:
            count[p["team"]] = count.get(p["team"], 0) + 1
        sides = ((0, o.red), (1, o.blue))
        if (firefight and not reach_ff) or o.game == "odst":   # ODST: teammates only (Reach Firefight, no list: --red / --blue)
            sides = ((players[0]["team"], botctl.player_cap(o.game, firefight)),)
        if o.lidar:                                  # LiDAR: everyone mapping on the player's side
            sides = ((players[0]["team"], o.lidar),)
        for team, want in sides:
            while count[team] < want and machine < 16 and room > 0:
                room -= 1
                name = names.pop()
                r = botctl.add_bot(m, machine, team, name, o.game, look=looks.look(name, o.game), clan=looks.clan_of(name),
                                   emblem=looks.emblem_of(name))
                print(f"bot {name} on {'red' if team == 0 else 'blue'}:", "ok" if not r.get("fault") and r.get("result", -1) & 0xFFFFFFFF != 0xFFFFFFFF and r.get("result", -1) > -100 or r.get("result", -1) < -100 else r)
                count[team] += 1
                machine += 1
    else:                                            # every bot on its own team, up to red + blue players
        used = {p["team"] for p in players}
        team, total = 1, len(players)
        while total < (o.lidar or o.red + o.blue) and machine < 16 and room > 0:
            total += 1
            room -= 1
            while team in used:
                team += 1
            name = names.pop()
            r = botctl.add_bot(m, machine, team, name, o.game, look=looks.look(name, o.game), clan=looks.clan_of(name),
                               emblem=looks.emblem_of(name))
            print(f"bot {name} on team {team}:", "ok" if not r.get("fault") and r.get("result", -1) & 0xFFFFFFFF != 0xFFFFFFFF and r.get("result", -1) > -100 or r.get("result", -1) < -100 else r)
            used.add(team)
            machine += 1
    if getattr(o, "brain", "") == "none":            # test override only: bots added, Claude's probe drives them
        print("no brain (test override): bots wait for tools/device_probe.py")
        return
    if o.lidar:                                      # the mapping Roombas (tools/lidar.py)
        subprocess.run([sys.executable, "-u", "-I", os.path.join(HERE, "lidar.py"), map_name, str(o.minutes * 60),
                        "--game", o.game])
        return
    if escort:                                       # the map-free escort brain (tools/escort_bots.py)
        tier = o.tier.split(",")[0] if "," not in o.tier else "average"
        if tier == "scaling":                        # (this brain takes a named tier: Scaling plays Legendary's game)
            tier = "top"
        subprocess.run([sys.executable, "-u", "-I", os.path.join(HERE, "escort_bots.py"), str(o.minutes * 60),
                        "--game", o.game, "--tier", tier])
        return
    # (Scaling difficulty reads each match's trace back with its post-game report, brain/scaling.py: always kept then)
    scaled = "scaling" in o.tier.split(",") + [b["tier"] for b in roster or []]   # (a bot on Scaling: either team)
    trace = match_trace(map_name, after=recording.since()) if (os.path.exists(os.path.join(ROOT, "logs", "trace_on")) or scaled) else None
    subprocess.run([sys.executable, "-u", "-I", os.path.join(HERE, "live_bots.py"), str(o.minutes * 60),
                    "--tier", o.tier, "--personality", "rusher,flanker,camper,objector,spammer,grudger,sniper",
                    "--map", map_name, "--game", o.game, "--quiet"] + ([] if teams else ["--ffa"]) +
                   (["--roster", o.roster] if roster is not None else []) +   # each bot's own settings
                   (["--ff-list"] if roster is not None and ff_own else []) +      # (Firefight's list: its ffbot= lines)
                   (["--collect", "on"] if getattr(o, "collector", "off") == "on" or
                    (o.roster and botctl.setting(o.roster, "collector", "0") == "1") else []) +   # Data collector (tools/collector.py)
                   (["--ride-only", o.ride_only] if getattr(o, "ride_only", None) else []) +   # test override: one kind
                   (["--aim-loose", o.aim_loose] if getattr(o, "aim_loose", None) else []) +   # test override: A/B
                   (["--forge-invasion"] if getattr(o, "forge_invasion", None) else []) +   # test override: no setup
                   (["--trace", trace] if trace else []))   # the Observer's recordings
    if trace and o.game == "reach" and os.path.exists(trace):   # the post-game report: the bots' names and teams
        subprocess.run([sys.executable, "-I", os.path.join(HERE, "carnage_names.py"), trace, "--wait", "20"])


if __name__ == "__main__":
    main()
