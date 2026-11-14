"""Run the brain on a live MCC game: every bot botcore.dll drives gets a Bot + its team's Director.

usage: python -I live_bots.py [seconds] [--tier T[,T..]] [--personality P[,P..]] [--quiet] [--trace FILE]
Tiers: novice average strong top. Lists are dealt out to bots in order (cycling).
Needs botcore.dll injected and bots made with `botctl.py add_bot` (or adopted). Ctrl+C or the time
limit stops it; bots are left standing still.
"""
import argparse
import json
import math
import os
import random
import re
import struct
import sys
import threading
import time

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from brain.core import Vec, PlayerState, Snapshot, FlagState  # noqa: E402
from brain.tracefmt import (Everyone, blitz_rows, call_lines, crosshair, meta_line, seat_of,  # noqa: E402
                            think_line)
from brain.maps import get_map, terrain as map_terrain, save_learned, grow as map_grow, swap as map_swap  # noqa: E402
from brain.bot import Bot  # noqa: E402
from brain import vehicles as V  # noqa: E402
from brain.director import Director  # noqa: E402
from brain.loadouts import Palette, liked_guns, record_death, want_slot  # noqa: E402
from brain.profiles import make_profile  # noqa: E402
from brain import scaling  # noqa: E402
from brain.live import Live, LiveH2, LiveH3, LiveReach, LiveODST, LiveH4F, WEAPON_ALIAS  # noqa: E402
from brain.invasion import layout as invasion_layout, Watch, phase_from_spawns, clear_barriers, honor_spawn, honor_spawns, \
    ally_spot as invasion_ally_spot, mate_clear as invasion_mate_clear, res_zone_spawn as invasion_res_zone_spawn, \
    SPAWN_CLEAR, res_summary as invasion_res_summary, res_zone_pick as invasion_res_zone_pick, \
    free_vehicles as invasion_free_vehicles  # noqa: E402
from brain.modes import Watch as ModeWatch, OBJECT_MODES, mode_of, stands_by_spawn  # noqa: E402
from brain import dom_base  # noqa: E402
import botctl  # noqa: E402
from brain.firefight_spawns import RoundWatch as FirefightRoundWatch  # Firefight-only round boundary
from brain.firefight_spawns import spawn_check as ff_spawn_check  # noqa: E402

HZ = 30
MAX_DT = 4 / HZ      # the longest step the bots think with: a longer gap (a hitch) is not made up in one step
PAUSE_TICKS = 9      # loop passes (0.3 s) with botcore's game tick standing still: the game is paused


MAP = None   # set in main() from --map


def place_ok(p):
    """A place some map has: three finite numbers, none 100,000 u out or more."""
    return p is not None and len(p) == 3 and all(isinstance(c, (int, float)) and math.isfinite(c) and abs(c) < 1e5 for c in p)


def think_order(bots, n):
    """The bots in the order they think and send their sticks on pass n: the teams interleaved, the first team swapping
    every pass. In index order team 1 (players 7-12) always acted last on the pass's snapshot (passes a median 35 ms,
    p90 109 ms), live B21-B28 / S14 it did worse on both maps in both roles (tools/test_team_fair.py)."""
    teams = {}
    for index, b in bots.items():
        teams.setdefault(getattr(b, "game_team", getattr(b, "team", 0)), []).append((index, b))
    keys = sorted(teams, reverse=bool(n % 2))
    out = []
    for k in range(max((len(v) for v in teams.values()), default=0)):
        out += [teams[t][k] for t in keys if k < len(teams[t])]
    return out


def roster_pick(styles, tiers, dealt, team):
    """(style, tier) for a team's next bot: dealt round-robin within its team, so the rosters mirror (over all bots by
    join order team 0 got rusher..grudger and team 1 sniper..spammer, its tiers shifted: tools/test_team_fair.py)."""
    k = dealt.get(team, 0)
    dealt[team] = k + 1
    return styles[k % len(styles)], tiers[k % len(tiers)]


def think_dt(elapsed):
    """The time step the bots think with: the real time since the last pass, so turning, aiming and moving keep their
    real-time speeds on a PC that needs longer than 1/HZ for a pass; never under 1/HZ (a pass that came early or the
    first one), never over MAX_DT."""
    return min(MAX_DT, max(1 / HZ, elapsed))


# the games where a bot's crouch press reaches the game (CE: botcore UC_CROUCH; Halo 3: maps/_inputs/h3.json "crouch";
# Halo 4: h4f.cpp action +0x08 bit 0); in Reach, Halo 2 and H2A it is not decoded, so their bots always stand
BOT_CROUCH = ("ce", "h3", "h4")


def snapshot(live, now, prev, bot_ids, crouch=(), person_crouch=False):
    """crouch: the bots crouching (their last command); person_crouch: the person holds his crouch key (the games read
    no stance: crouched heads were shot over at standing height). ponytail: with more people on the machine's network
    the host's key is read for the first of them; their own stance needs the game's unit read."""
    players = []
    raw = live.players()
    person = min((d["index"] for d in raw if d["index"] not in bot_ids), default=None)
    live.raw = raw                                # this tick's, for the Invasion watcher's core reader
    live.units = {d["index"]: d["unit"] for d in raw if d.get("pos") is not None}   # this tick's, for unit_zoom
    flags = {}
    engine = live.engine() if hasattr(live, "engine") else 0
    # CTF only; none in Slayer (H3 finds them by the stands: a gun dropped on a stand in Slayer became a "flag" the
    # player carried and the team ran flag plays, Cyberdyne); never in Oddball / King (the carried ball read as a flag).
    # Only CTF's engine (1): Halo 3 Territories (engine 6) read a gun on a CTF stand as a flag and played CTF
    # (Construct)
    on = engine == 1
    if on and "forge" in MAP["base"] and not MAP.get("forge_stands") and hasattr(live, "loose_flags"):
        # a Forge canvas's variant (Forge World; Halo 4's, H2A's) has stands of its own, not the blank canvas's in the
        # export (Paradiso CTF played as Slayer): where its flags lie at the start, each to the team that spawned
        # nearest
        try:
            stands = stands_by_spawn([Vec(*q) for q in live.loose_flags()],
                                     [(d["team"], Vec(*d["pos"])) for d in raw if d.get("pos") is not None])
        except Exception:
            stands = {}
        if len(stands) >= 2 or (stands and now > 10.0):
            MAP["flags"], MAP["forge_stands"] = stands, True
            print(f"Forge CTF stands: {stands}", flush=True)
    found = live.flags(raw, stands=MAP["flags"] if on else None)
    if getattr(live, "live_stands", None):
        # stands riding vehicles (Halo 3 Sandtrap: on the Elephants): every home and capture point where they are now;
        # the map's own flag returns lie at its static stands, so a team on a vehicle captures at the one riding with it
        MAP["flags"].update(live.live_stands)
        rets = MAP.setdefault("returns", {})
        for t in live.live_stands:
            rets.pop(t, None)
        rets.update(getattr(live, "live_returns", {}))
    for f in found:
        home = MAP["flags"][f.get("stand", f["team"])]      # its own stand (One Flag: not its the player's side)
        pos = Vec(*f["pos"])
        state = "carried" if f["carrier"] is not None else ("home" if (pos - home).flat() < 1.0 else "dropped")
        flags[f["team"]] = FlagState(f["team"], home, pos, state, f["carrier"] if f["carrier"] is not None else -1)
    carriers = {f.carrier for f in flags.values()}
    for d in raw:
        alive = d["pos"] is not None
        if alive and not place_ok(d["pos"]):
            # a place no map has (NaN, or millions of units out: a tick read mid-change, Forge) is his last good one:
            # the 2 million unit belief it made built an 11 million sample sight line and the brain died of memory
            q = prev.get(d["index"])
            alive = q is not None
            d["pos"] = (q.x, q.y, q.z) if alive else None
        pos = Vec(*d["pos"]) if alive else prev.get(d["index"], Vec())
        vel = Vec()
        if alive and d["index"] in prev:
            q = prev[d["index"]]
            vel = Vec((pos.x - q.x) * HZ, (pos.y - q.y) * HZ, (pos.z - q.z) * HZ)
        fx, fy, fz = d["aim"] or (1.0, 0.0, 0.0)
        on = d.get("ability_on", False)
        players.append(PlayerState(d["index"], d["team"], d["name"], pos, vel,
                                   math.atan2(fy, fx), math.atan2(fz, math.hypot(fx, fy)), alive,
                                   d["shield"], d["health"], d["weapon"], d.get("weapons", []), d.get("ammo", []), d.get("mag", []), d.get("vehicle", -1),
                                   has_flag=d["index"] in carriers,
                                   is_bot=d["index"] in bot_ids,
                                   crouching=alive and (d["index"] in crouch if d["index"] in bot_ids else
                                                        person_crouch and d["index"] == person),
                                   # armor ability / equipment (brain/live.py ability()); a running camo
                                   # ability hides him like the camo powerup does
                                   equipment=d.get("equipment", ""), ability_on=on,
                                   # the match's motion tracker setting for him (off in SWAT)
                                   tracker=d.get("tracker", True),
                                   camo_until=now + 0.3 if on and d.get("equipment") == "active_camo" else 0.0))
    bad = ability_misread(players, now)
    for p in players:
        if p.equipment in bad:
            p.ability_on, p.camo_until = False, 0.0      # (that kind's on-state only: its name and the rest stay)
    # a vehicle riding on another (Reach: a Warthog's gun, a Falcon's side guns are their own vehicles, positioned
    # relative to it): its riders count as the carrier's, and it is not a vehicle of its own in the list (its gunner
    # read as riding a separate "unknown" vehicle, so every manned Warthog looked gunner-less)
    objs = live.objects((1,))
    parent_of = {}
    if hasattr(live, "object") and hasattr(live, "g"):
        for h, typ, path, pos in objs:
            try:
                # (each game's parent field: Halo 3's is +0x10, LiveH3.PARENT_AT; this read +0x14 in every game)
                par = struct.unpack("<I", live.g.read(live.object(h) + getattr(live, "PARENT_AT", 0x14), 4))[0]
            except Exception:
                continue
            if par != 0xFFFFFFFF:
                parent_of[h] = par
    # what rides on each vehicle names its variant and what its gunners shoot (a Reach Warthog's gauss gun, a Falcon's
    # side guns and which side is manned, the Forge Falcon's chin gun: brain/vehicles.TURRETS)
    name = {h: path.rsplit("\\", 1)[-1] for h, typ, path, pos in objs}
    guns = {}
    for h, par in parent_of.items():
        guns.setdefault(par, []).append(name.get(h, ""))
    for p in players:
        if p.vehicle in parent_of:
            p.turret = name.get(p.vehicle, "")
            p.vehicle, p.gunner_seat = parent_of[p.vehicle], True
    ridden = {p.vehicle for p in players if p.vehicle != -1}
    riders = {}
    for p in players:
        if p.vehicle != -1 and p.alive:
            riders[p.vehicle] = riders.get(p.vehicle, 0) + 1
    # riders / team: who is in it (a Warthog with only its driver wants a gunner: brain/vehicles.py)
    vehicles = [dict(handle=h, kind=V.variant(name[h], guns.get(h, ())), pos=pos, occupied=h in ridden,
                     riders=riders.get(h, 0), team=next((p.team for p in players if p.vehicle == h), -1),
                     fwd=vehicle_fwd(live, h), up=vehicle_fwd(live, h, 1),
                     gunner=any(getattr(p, "gunner_seat", False) and p.vehicle == h for p in players),
                     turrets=guns.get(h, []), manned=[p.turret for p in players if p.gunner_seat and p.vehicle == h and p.alive],
                     dead=vehicle_dead(live, h), left=vehicle_left(live, h))
                for h, typ, path, pos in objs if h not in parent_of and place_ok(pos)]   # (a NaN place, read as Forge spawns
    # or deletes one, crashed a tick walking a bot to it)
    if RIDE_ONLY:                              # a test match about one kind (a Falcon check): no other empty vehicles
        vehicles = [v for v in vehicles if v["occupied"] or any(k in v["kind"] for k in RIDE_ONLY)]
    # one on its side or roof is no ride (brain/vehicles.py upright), nor a wreck (usable): nobody is sent to it; its
    # riders keep it
    vehicles = [v for v in vehicles if v["occupied"] or V.usable(v)]
    vpos = {v["handle"]: v["pos"] for v in vehicles}
    for p in players:                          # a rider's biped position does not follow the vehicle
        if p.vehicle in vpos:
            p.pos = Vec(*vpos[p.vehicle])
            # its speed from the vehicle's places too: the raw one is relative to the vehicle, so the diff above read
            # about -30x its map position, and every gunner aimed along one line through the map's origin and bots
            # led a driver to the far side of it; none on the boarding tick
            q = prev.get(p.id)
            p.vel = (Vec((p.pos.x - q.x) * HZ, (p.pos.y - q.y) * HZ, (p.pos.z - q.z) * HZ)
                     if q is not None and (p.pos - q).length() < 1.0 else Vec())
    if now - GROUND["t"] > 0.5 and hasattr(live, "ground_weapons"):
        GROUND["t"], GROUND["list"] = now, [(n, Vec(*q)) for n, q in live.ground_weapons()]
    elif now - GROUND["t"] > 0.5 and hasattr(live, "_learn"):
        # Halo 3 / Reach keep no tag names: scanning the weapons names each kind by the map
        # placement it lies on (LiveH3._learn); without it every gun read "unknown"
        GROUND["t"] = now
        GROUND["list"] = [(n, Vec(*q)) for h, typ, n, q in live.objects((2,)) if n != "unknown"]
    # a match without shields (Team SWAT, Snipers ...): every living player's shield has read 0 for 3 s; over as soon
    # as anyone shows one
    if not hasattr(live, "shields_at") or any(p.shield > 0.05 for p in players if p.alive):
        live.shields_at = now
    # honks (director.honk_orders): a person at the wheel who stopped within 8 u of a teammate bot on foot for 1.5 s.
    # ponytail: the horn itself is the crouch key in a vehicle, and Reach's crouch is not decoded yet (tools/
    # decode_buttons.py with the player); pulling up beside a bot stands in for it
    honks = []
    for p in players:
        v = next((x for x in vehicles if x["handle"] == p.vehicle), None)
        if p.is_bot or not p.alive or v is None or p.gunner_seat:
            HONK.pop(p.id, None)
            continue
        h = HONK.get(p.id)
        if h is None or h[0] != v["handle"] or math.dist(h[1], v["pos"]) > 0.3:
            HONK[p.id] = (v["handle"], v["pos"], now)
        elif now - h[2] >= 1.5 and any(q.is_bot and q.alive and q.team == p.team and q.vehicle == -1
                                       and math.dist((q.pos.x, q.pos.y, q.pos.z), v["pos"]) < 8.0
                                       for q in players):
            honks.append(v["handle"])
    snap = Snapshot(now, players, flags, mode="ctf" if flags else "slayer", vehicles=vehicles, honks=honks,
                    ground_weapons=GROUND["list"], shieldless=now - live.shields_at > 3.0)
    if type(live).__name__ == "LiveH3":          # Halo 3's equipment out in the world (#9; Reach / H4 readers inherit
        try:                                     # LiveH3 but their object tables differ)
            snap.deployed = live.deployed(raw, now)
        except Exception:
            snap.deployed = []
    if hasattr(live, "match_state") and now - MATCH["t"] > 0.5:      # score, the variant's limit, time left
        MATCH["t"] = now
        try:
            v = live.match_state(raw)
            sane = v and all(-50 <= x <= max(500, 4 * (v["limit"] or 0)) for x in v["score"].values()) \
                and 0 <= (v["limit"] or 0) <= 10000
            MATCH["v"] = v if sane else None       # at the match end the memory is reused: garbage scores
        except Exception:
            MATCH["v"] = None
    for d in raw:                                # every death is a point for the other team (Slayer): the
        if d["index"] in prev and d["pos"] is None:   # tally for games whose team scores are not decoded (H2)
            TALLY[1 - d["team"] % 2] = TALLY.get(1 - d["team"] % 2, 0) + 1
    if MATCH.get("v"):
        snap.score = MATCH["v"]["score"] or (dict(TALLY) if TEAMS and snap.mode == "slayer" else snap.score)
        snap.score_limit, snap.time_left = MATCH["v"]["limit"], MATCH["v"]["time_left"]
    if FIREFIGHT:                                # the Covenant are the enemy: they join as players
        # Reach: the Covenant AI are on the BLUE team (CAMPAIGN_AI_TEAM), whatever team number the game gives them: the
        # bots read "same team" for them, so a bot on blue (the Covenant's side) never aims at, hunts or throws at them,
        # and a bot or person on red fights them like any other enemy. The human-side AI (the Spartans' allies, the
        # unit's side 1 or 2: live.ai_units "human") stay RED (HUMAN_AI_TEAM), with the Spartan players. ODST keeps its
        # -1 (nobody's friend: its players may sit on any team number)
        reach = isinstance(live, LiveReach)
        for d in live.ai_units(raw):
            fx, fy, fz = d["aim"]
            ai_team = (HUMAN_AI_TEAM if d.get("human") else CAMPAIGN_AI_TEAM) if reach else d["team"]
            players.append(PlayerState(d["index"], ai_team, "ai", Vec(*d["pos"]), Vec(),
                                       math.atan2(fy, fx), math.atan2(fz, math.hypot(fx, fy)), True,
                                       d["shield"], d["health"], d["weapon"]))
        snap.mode = "firefight"
    if engine == 4:                              # King of the Hill
        h = live.king_hill() or HILL.get("last")
        # (a King game always has a hill: a read that misses keeps the last one. Halo 3's comes from the host's HUD
        # waypoint, which the game hides while he stands in the hill: live Shrine 20261104-154714, 383 of 389 ticks
        # with him in it read no hill, so the match played as Slayer, 23.8-119.2 s)
        if h:
            HILL["last"] = h
            snap.mode, snap.hill, snap.hill_r = "king", Vec(*h[0]), h[1]
    elif engine == 3:                            # Oddball (the mode once a ball is found: a game without a ball
        b = next(iter(live.balls(raw)), None)    # reader plays Slayer, not runners to mid)
        if b:
            snap.mode = "oddball"
            snap.ball, snap.ball_carrier = Vec(*b["pos"]), b["carrier"]
            for p in players:
                p.has_flag = p.has_flag or p.id == b["carrier"]   # carries the objective: no shooting
    return snap


def vehicle_fwd(live, handle, k=0):
    """A vehicle's forward vector, k=1 its up vector (the next one), or None where the read fails or the reader does not
    know where it is: bots then walk to its middle (brain/bot.py seat_point) and take it for standing. Where the
    reader knows the object's orientation (ORIENT_AT: Halo 4) from there; else the facing right after its position, as
    for units, and the up vector after that, taken only as a unit vector square to the facing (the engines' object
    layout; unread until the up was None in Reach, every vehicle "standing": bots got into the driver's seat of
    upturned ones)."""
    at = getattr(live, "ORIENT_AT", None)
    if at is None and getattr(live, "POS_AT", None) is None:
        return None
    try:
        o = live.object(handle)
        if not o:
            return None
        if at is not None:
            return struct.unpack("<3f", live.g.read(o + at + 12 * k, 12))
        f = struct.unpack("<3f", live.g.read(o + live.POS_AT + 12, 12))
        if not k:
            return f
        u = struct.unpack("<3f", live.g.read(o + live.POS_AT + 24, 12))
        n, dot = math.sqrt(sum(c * c for c in u)), sum(a * b for a, b in zip(f, u))
        return u if 0.95 < n < 1.05 and abs(dot) < 0.15 else None
    except Exception:
        return None


def vehicle_dead(live, handle):
    """A wreck (brain/live.py Live.vehicle_dead); False where the reader has none or the read fails."""
    try:
        return bool(live.vehicle_dead(handle)) if hasattr(live, "vehicle_dead") else False
    except Exception:
        return False




def ride_health(snap, p):
    """For brain/oob.py: False on foot, the health left of the vehicle he rides (0-1), True riding one unread."""
    if p.vehicle in (None, -1):
        return False
    v = next((x for x in snap.vehicles if x["handle"] == p.vehicle), None)
    left = v.get("left") if v else None
    return left if isinstance(left, (int, float)) and not isinstance(left, bool) else True


def vehicle_left(live, handle):
    """Its body field's health left (brain/live.py Live.vehicle_left), rounded for the trace; None where unread."""
    try:
        v = live.vehicle_left(handle) if hasattr(live, "vehicle_left") else None
        return round(v, 3) if v is not None and math.isfinite(v) else None
    except Exception:
        return None


GROUND = {"t": -1.0, "list": []}     # loose weapons, re-read twice a second
MATCH = {"t": -1.0, "v": None}       # live.match_state(): score, limit, time left, twice a second
HILL = {}                            # "last": the last King hill read (centre, radius), kept through missed reads
HONK = {}                            # person id -> (vehicle, where it stopped, since when): the honk stand-in
RIDE_ONLY = None                     # test override (--ride-only): the empty vehicles bots may take, by kind
TALLY = {0: 0, 1: 0}                 # deaths counted as the other team's points (no score reader)


def unit_zoom(live, index):
    """The unit's desired zoom byte this tick (0xFF none, 0 first level, 1 second), where the game's offset is known
    (live.UNIT_ZOOM: CE, Halo 2, Halo 3), else None. In the trace as "uz" next to the zoom the brain asked (cmd bits
    12-13): the game takes a request on the next tick. Units: the reader's own, from this tick's snapshot."""
    off, units = getattr(live, "UNIT_ZOOM", None), getattr(live, "units", {})
    o = live.object(units[index]) if off is not None and index in units else None
    return live.g.read(o + off, 1)[0] if o else None
TEAMS = True                         # set in main(): False in free for all
FIREFIGHT = False                    # set in main: Reach Firefight (bots play for red or blue, as in a multiplayer custom game)
CAMPAIGN_AI_TEAM = 1                 # Reach Firefight: the Covenant AI are on this team (1 = blue)
HUMAN_AI_TEAM = 0                    # ... and the human-side AI (the Spartans' allies) on this one (0 = red)


def flag_events(directors, before, snap, now):
    """The announcer: a team hears at once when its flag is taken or dropped."""
    for team, f in snap.flags.items():
        was = before.get(team)
        d = directors.get(team)
        if not d or not was or was == f.state:
            continue
        if f.state == "carried":
            d.report(now, -1, "flag_taken", f.home)
        elif f.state == "dropped" and was == "carried":
            d.report(now, -1, "flag_dropped", f.pos)
        elif f.state == "home" and was == "carried":          # carried straight home: captured
            d.report(now, -1, "enemy_scored", f.home)
            if directors.get(1 - team):
                directors[1 - team].report(now, -1, "scored", f.home)
        elif f.state == "home" and was == "dropped":
            d.report(now, -1, "flag_returned", f.home)


def events(bots, directors, before, snap, now):
    """Game events the sim reports itself: who cracked whom, who died and where the killer is.
    Live we do not see the shooter, so we credit the bot that was fighting that player."""
    for index, b in bots.items():
        me, was = snap.player(index), before.get(index)
        if not me or not was:
            continue
        d = next((d for d in directors.values() if index in d.bots), None)   # (team, side or its own: director_key)
        if was.alive and not me.alive:              # this bot just died
            kind, arg = b.goal
            killer = arg if kind == "fight" else None
            if killer is None and b.per.beliefs:
                killer = min(b.per.beliefs, key=lambda k: (b.per.beliefs[k].pos - was.pos).length())
            k = snap.player(killer) if isinstance(killer, int) else None
            if d and k:
                d.report(now, index, "died", k.pos, enemy=killer, killer_weak=k.shield <= 0, died_at=was.pos)
            b.on_death(killer)
        elif me.alive and b.goal[0] == "fight" and d:
            e, e_was = snap.player(b.goal[1]), before.get(b.goal[1])
            if e and e_was and e.alive and e_was.shield > 0 and e.shield <= 0:
                d.report(now, index, "one_shot" if e.health < 0.5 else "cracked", e.pos, enemy=e.id, busy=True)


EQUIPMENT_ALIAS = {"over_shield": "overshield", "active_camouflage": "camo"}


def item_name(path):
    """Tag path -> the brain's item name (maps.POWER names: rockets, sniper, overshield, ...)."""
    n = path.rsplit("\\", 1)[-1].replace(" ", "_")
    return EQUIPMENT_ALIAS.get(n, WEAPON_ALIAS.get(n, n))


def item_check(live, directors, bots, snap, now):
    """Power items: a team learns one is gone (taken, or not in this game type) when one of its
    bots is near the spawn and nothing of that kind lies there; its timer starts then, as for a
    player who walks up to an empty spawn."""
    loose = [(item_name(path), pos) for h, t, path, pos in live.objects((2, 3))]
    for i, it in enumerate(MAP["items"]):
        p = it["pos"]
        there = any(n == it["name"] and abs(q[0] - p.x) < 1.2 and abs(q[1] - p.y) < 1.2 and abs(q[2] - p.z) < 1.5
                    for n, q in loose)
        if there:
            continue
        for d in directors.values():
            if d.item_up.get(i, 0.0) > now:
                continue                                  # already counting down
            if any(snap.player(b.id) and snap.player(b.id).alive and      # (d.bots: its team)
                   (snap.player(b.id).pos - p).flat() < 25 for b in d.bots.values()):
                d.item_taken(i, now, it["respawn"])


def loadout_check(live, ask, bots, palette, state, snap, now, trace=None):
    """Loadouts (Reach, Halo 4, H2A; brain/loadouts.py): learn each slot's guns from the bots' spawns,
    record how each bot's life ended and, while it waits to respawn, ask for the slot that answers
    the problem it keeps meeting. ask(index, slot) -> True if botcore took the request."""
    for index, b in bots.items():
        me = snap.player(index)
        if not me:
            continue
        st = state.setdefault(index, dict(alive=False, born=None, asked=None))
        # (Invasion: each side its own slots, Palette.side; the round change swaps a team's side)
        pal = palette.side(("attack" if me.team == snap.inv_attackers else "defend") if snap.mode == "invasion" else None)
        if me.alive:
            if not st["alive"]:
                st["born"] = now
            elif st["born"] is not None and now - st["born"] > 1.0:   # a moment after the spawn
                b.slot, st["born"] = live.loadout(index), None
                pal.learn(b.slot, me.weapons, me.equipment)
        else:
            if st["alive"]:
                record_death(b, now, snap)
            team = [p.equipment for p in snap.players if p.team == me.team and p.id != index and p.alive and p.equipment]
            want = want_slot(b, pal, st["asked"] if st["asked"] is not None else getattr(b, "slot", None), now, team)
            if want:
                slot, why, n = want
                if ask(index, slot):
                    st["asked"] = slot
                    if trace:
                        trace.write(json.dumps(dict(t=round(now, 3), id=index, loadout=slot, why=why, n=n,
                                                    guns=pal.seen.get(slot))) + chr(10))
                else:
                    palette.off = pal.off = True
                    print(f"{now:6.1f}s loadouts off: botcore refused SET_LOADOUT (restart MCC to load s59 or newer)",
                          flush=True)
        st["alive"] = me.alive


def spawn_check(send, bots, snap, now, state, spawns, watch, rng, T, trace=None):
    """Invasion respawns by the player's honor system (brain/invasion.py honor_spawn): while a bot is dead, where it will
    spawn, sent to botcore (send(index, x, y, z, yaw): botctl.spawn_at), which moves its new unit there. Picked once a
    death and again in a new phase; a spot behind a teammate is redone every half second (he moves), the pick redone if
    he is no longer out of the fight. Only with the sides known and half the players or more alive: a round's change
    leaves everyone dead for ~10 s with the old sides still read (match 211159, 457-467 s), and the round's opening
    spawn is the game's (it tells the new sides, invasion.Watch); a pick sent before that is called off (NaN: where the
    game puts it). watch.placed: the bots with a pick out (their spawns say nothing of the phase). A pick at a spawn
    kept for its vehicles (River in phases 2-3) gives the bot that vehicle as its errand (b.errand: handle, until)."""
    alive = sum(1 for p in snap.players if p.alive)
    respawn_phase = watch.phase  # the real Invasion phase; snap.inv_phase may be 3 for an Assault objective
    # (not while a round's change is pending: invasion.Watch.round_wait_until, the half alive came while the rest were
    # still respawning and the picks by the old phase hid the new round, live Spire)
    ok = watch.sides_set and alive * 2 >= len(snap.players) and now >= getattr(watch, "round_wait_until", -1.0)
    zones = snap.inv_zones or []
    goal = Vec(sum(z.x for z in zones) / len(zones), sum(z.y for z in zones) / len(zones), sum(z.z for z in zones) / len(zones)) \
        if zones else None
    taken = [st["pick"]["pos"] for st in state.values() if st.get("pick")]
    for index, b in bots.items():
        me = snap.player(index)
        if not me:
            continue
        st = state.setdefault(index, {})
        if me.alive or not ok:
            if st.get("pick") and not me.alive:
                send(index, math.nan, math.nan, math.nan, 0.0)
            st.clear()
            continue
        side = "att" if me.team == snap.inv_attackers else "def"
        pick = st.get("pick")
        on_mate = pick is not None and pick["mate"] is not None
        if pick and st["phase"] == respawn_phase and not (on_mate and now - st["t"] >= 0.5):
            continue
        others = [q for q in taken if pick is None or q is not pick["pos"]]
        mate = snap.player(pick["mate"]) if on_mate and st["phase"] == respawn_phase else None
        spot = invasion_ally_spot(mate, snap.players, T, others) \
            if mate is not None and invasion_mate_clear(mate, snap.players, T) else None
        errands = {e[0] for k, o in bots.items() if k != index and (e := getattr(o, "errand", None)) and now < e[1]}
        new = dict(pick, pos=spot, yaw=mate.yaw) if spot is not None else \
            honor_spawn(spawns, respawn_phase, side, me, snap.players, rng, T, others, goal, snap.vehicles, errands)
        if new is None:
            continue
        # spawned at River in phases 2-3 for the free Warthog or Falcon by it: sent to it (Bot.decide errand)
        b.errand = (new["ride"], now + 30.0) if new.get("ride") is not None else None
        send(index, new["pos"].x, new["pos"].y, new["pos"].z, new["yaw"])
        if trace and (pick is None or new["name"] != pick["name"]):
            trace.write(json.dumps(dict(t=round(now, 3), id=index, spawn=new["name"], side=side, phase=respawn_phase, objective_phase=snap.inv_phase,
                                        at=[round(new["pos"].x, 2), round(new["pos"].y, 2), round(new["pos"].z, 2)])) + chr(10))
        if pick is not None:
            taken = [q for q in taken if q is not pick["pos"]]
        taken.append(new["pos"])
        st.update(pick=new, phase=respawn_phase, t=now)
    watch.placed = {index for index, st in state.items() if st.get("pick")}


def res_spawn_check(send, bots, snap, now, state, inv, watch, rng, T, trace=None):
    """Invasion respawns straight off the map's own Inv_res_zone hills (brain/invasion.py res_zone_spawn), for a
    custom map with an Invasion setup of its own (inv_layout) but no inv_spawns.json of named spawns - Tempest:
    "I only use Inv_res_zone on the hills because Inv_Res_ph1 to 3 don't work in customs". While a bot is dead,
    where it will spawn, sent to botcore (send(index, x, y, z, yaw): botctl.spawn_at); its team's hill(s) for the
    active phase, random among more than one (res_zone_spawn), facing the phase's objective. Picked once a death
    and again in a new phase. Only with the sides known and half the players or more alive (as spawn_check)."""
    alive = sum(1 for p in snap.players if p.alive)
    # the real Invasion phase (watch.phase), as spawn_check uses: snap.inv_phase is the objective's phase and reads 3
    # during an Assault/bomb run, and can lag, so p2/p3 hills were never picked
    respawn_phase = watch.phase if watch.phase in (1, 2, 3) else (snap.inv_phase if snap.inv_phase in (1, 2, 3) else 1)
    ok = watch.sides_set and alive * 2 >= len(snap.players) and now >= getattr(watch, "round_wait_until", -1.0)
    zones = snap.inv_zones or []
    goal = Vec(sum(z.x for z in zones) / len(zones), sum(z.y for z in zones) / len(zones),
              sum(z.z for z in zones) / len(zones)) if zones else None
    taken = [st["pos"] for st in state.values() if st.get("pos")]
    claimed = set()                                    # vehicles sent for by a pick this tick
    for index, b in bots.items():
        me = snap.player(index)
        if not me:
            continue
        st = state.setdefault(index, {})
        if me.alive or not ok:
            if st.get("pos") and not me.alive:
                send(index, math.nan, math.nan, math.nan, 0.0)
            st.clear()
            continue
        # The scenario's hill labels describe attacking/defending roles, not the
        # players' persistent game-team IDs. Those roles swap between Invasion rounds.
        side = "att" if me.team == snap.inv_attackers else "def"
        # the map's Inv_res_p team for this player: read off where the game itself respawned its team at the round's
        # start (watch.res_label); only without that, the attackers/defenders bookkeeping
        read = getattr(watch, "res_label", {}).get(me.team)
        layout_team = read if read is not None else (inv["attackers"] if side == "att" else inv["defenders"])
        if (st.get("pos") is not None and st.get("phase") == respawn_phase
                and st.get("side") == side and st.get("team") == layout_team):
            continue
        others = [q for q in taken if q is not st.get("pos")]
        # the objective steers the pick (attackers: later spawns more often; defenders: the nearest to what they guard);
        # going back a spawn is for a free vehicle only (brain/invasion.py res_zone_pick)
        errands = {e[0] for k, o in bots.items() if k != index and (e := getattr(o, "errand", None)) and now < e[1]} | claimed
        rides = invasion_free_vehicles(getattr(snap, "vehicles", None) or (), errands)
        picked = invasion_res_zone_pick(inv, layout_team, respawn_phase, rng, others, side == "att", rides)
        pos, ride = picked if picked is not None else (None, None)
        if pos is None:
            key = (layout_team, respawn_phase)
            if key not in res_spawn_check.warned:
                res_spawn_check.warned.add(key)
                print(f"{now:6.1f}s res spawn: no Inv_res_p{respawn_phase} marker or usable Inv_res_zone hill for respawn team {layout_team}; "
                      f"those bots spawn where the game puts them", flush=True)
            continue
        yaw = math.atan2(goal.y - pos.y, goal.x - pos.x) if goal is not None else 0.0
        send(index, pos.x, pos.y, pos.z, yaw)
        if ride is not None:                            # a free vehicle by this spawn: the bot goes for it (Bot.decide errand)
            b.errand = (ride, now + 30.0)
            claimed.add(ride)
        if st.get("pos") is None or (pos - st["pos"]).flat() > 0.01:
            print(f"{now:6.1f}s res spawn: bot {index} (player team {me.team}, {side}, respawn team {layout_team}"
                  f"{'' if read is not None else ' [guessed]'}) -> "
                  f"{'Inv_res_p' + str(respawn_phase) if any(pos is q for q in inv.get('res_named', {}).get(layout_team, {}).get(respawn_phase, [])) else 'Inv_res_zone hill'}"
                  f" (phase {respawn_phase}) at ({pos.x:.2f}, {pos.y:.2f}, {pos.z:.2f})"
                  f"{'' if ride is None else f', free vehicle {ride} by it'}", flush=True)
        if trace and (st.get("pos") is None or (pos - st["pos"]).flat() > 0.01):
            trace.write(json.dumps(dict(t=round(now, 3), id=index, spawn="res_zone",
                                        side="att" if me.team == snap.inv_attackers else "def",
                                        phase=respawn_phase, objective_phase=snap.inv_phase,
                                        at=[round(pos.x, 2), round(pos.y, 2), round(pos.z, 2)])) + chr(10))
        if st.get("pos") is not None:
            taken = [q for q in taken if q is not st["pos"]]
        taken.append(pos)
        st.update(pos=pos, phase=respawn_phase, side=side, team=layout_team)
    watch.placed = {index for index, st in state.items() if st.get("pos")}


res_spawn_check.warned = set()


def zone_spawn_check(send, bots, snap, zone_of, state, rng, T, now, trace=None):
    """Invasion on a map with no Invasion setup of its own (a Forge map variant): a dead bot respawns in the zone the
    game gives it (zone_of(index): LiveReach.respawn_zone, the script's zone of its side and phase), within 2 u of the
    zone's centre on floor and clear of everyone; sent once a death and again when its zone moves (a new phase)."""
    taken = [st["at"] for st in state.values() if st.get("at")]
    for index in bots:
        me = snap.player(index)
        if not me:
            continue
        st = state.setdefault(index, {})
        if me.alive:
            st.clear()
            continue
        z = zone_of(index)
        if z is None or st.get("zone") == z[:3]:
            continue
        c = Vec(z[0], z[1], z[2])
        spot = c
        for k in range(16):
            r, a = (0.0, 0.0) if k == 0 else (rng.uniform(0.5, 2.0), rng.uniform(0.0, 2 * math.pi))
            p = Vec(c.x + r * math.cos(a), c.y + r * math.sin(a), c.z)
            if (T.ground_ok(p.x, p.y, p.z + 0.4, drop=1.0) and T.los(c, p)
                    and all((q.pos - p).flat() >= SPAWN_CLEAR for q in snap.players if q.alive)
                    and all((q - p).flat() >= SPAWN_CLEAR for q in taken if q is not st.get("at"))):
                spot = p
                break
        send(index, spot.x, spot.y, spot.z, z[3])
        if trace:
            trace.write(json.dumps(dict(t=round(now, 3), id=index, spawn="zone", side=None, phase=None,
                                        at=[round(spot.x, 2), round(spot.y, 2), round(spot.z, 2)])) + chr(10))
        taken = [q for q in taken if q is not st.get("at")] + [spot]
        st.update(zone=z[:3], at=spot)


def named(text, snap, director):
    """A commander's line with bot ids as the bots' names and people as "a player" (the report the model
    reads has no names, guide 10 section 6.6)."""
    def name(mt):
        k = mt.group(0)
        p = snap.player(int(k[1:])) if k.startswith("b") else None
        return p.name if p else ("a player" if k.startswith("human") else k)
    return re.sub(r"\b(?:b\d+|human\d+)\b", name, text)


def director_key(b, snap, ffa):
    """Whose director a bot plays under: its game team's; in free for all its own, except where brain/modes.py splits a
    free for all into two sides (Infection's survivors 0 and infected 1, Juggernaut's hunters 0 and the Juggernaut 1:
    snap.infected set, the sides' teams 0 / 1): that side's, so each side has a director and a commander (the player:
    the director in every mode; Infection lobbies have teams off, so its plays never ran)."""
    if not ffa:
        return b.game_team                      # the game's team: Green is not Red
    p = snap.player(b.id)
    return ("side", p.team) if snap.infected != -1 and p is not None else ("bot", b.id)


def regroup(directors, bots, snap, ffa, make):
    """This tick's directors by director_key: a key's director (re)made by make(key, members) when its members
    changed (a new bot, a survivor infected, a new Juggernaut), dropped when no bot has its key any more."""
    groups = {}
    for i, b in bots.items():
        groups.setdefault(director_key(b, snap, ffa), {})[i] = b
    for k in [k for k in directors if k not in groups]:
        del directors[k]
    for k, members in groups.items():
        if k not in directors or set(directors[k].bots) != set(members):
            directors[k] = make(k, members)
    return directors


def reach_name_dump(live, path):
    """Once a match (Reach): every UTF-16 text in each player's data (0x490 bytes) and in the match's 16 player
    configurations (game globals +0x2D8, 0xA0 each), with offsets, then the raw bytes: where the player's name
    and service tag sit and which copies a bot carries (the player: Reach bots showed their name and clan tag).
    Read only."""
    def texts(b):
        out, i = [], 0
        while i + 4 <= len(b):
            j = i
            while j + 1 < len(b) and 0x20 <= b[j] < 0x7F and b[j + 1] == 0:
                j += 2
            if j - i >= 4:                          # two characters or more
                out.append((i, b[i:j].decode("utf-16-le")))
                i = j
            else:
                i += 2
        return out
    base, mx, size = live._array(0x18)
    gg = live.g.u64(live._blk + 0x48)
    rows = [(f"player {i}", live.g.read(base + i * size, size)) for i in range(mx)]
    rows = [r for r in rows if r[1][0:2] != b"\0\0"]           # salt 0: a free slot
    rows += [(f"config {i}", live.g.read(gg + 0x2D8 + i * 0xA0, 0xA0)) for i in range(16)]
    with open(path, "w", encoding="utf-8") as f:
        f.writelines(f"{k}: " + "; ".join(f"+0x{o:X} {t!r}" for o, t in texts(b)) + "\n" for k, b in rows)
        f.writelines(f"{k} bytes {b.hex()}\n" for k, b in rows)


def buttons(a):
    B = botctl.BTN
    return int((a.fire and B["fire"]) | (a.jump and B["jump"]) | (a.crouch and B["crouch"]) |
            (a.grenade and B["grenade"]) | (a.melee and B["melee"]) | (a.action and B["action"]) |
            # a hold-mode ability sends its own button alone (maps/_inputs "ability": the game's input gives it bit 8 and
            # the key, not the toggle-mode press, brain/equipment.py hold_bit)
            (a.reload and B["reload"]) | (a.equipment and not a.ability and B["equipment"]) | (a.sprint and B["sprint"]) |
            (a.fire2 and B["fire2"]) | (a.cycle and B["cycle"]) | (a.ability and B["ability"])) | (
            ((a.weapon + 1) & 7) << 8 if a.weapon >= 0 else 0) | ((a.zoom & 3) << 12) | (
            ((a.seat + 1) & 15) << 14 if a.seat >= 0 else 0) | (1 << 18 if a.turret else 0) | (   # shared.h HB_SEAT_SHIFT
            (1 if a.vertical > 0.75 else 3 if a.vertical > 0.25 else 2 if a.vertical < -0.25 else 0) << 19) | (  # HB_VERT_SHIFT
            ((a.lunge_at + 1) & 31) << 24 if 0 <= a.lunge_at < 16 else 0)   # HB_TARGET_SHIFT: the lunge's target


# the ability readers (brain/live.py ability) are decoded statically, not yet checked live: a reading the game cannot
# produce turns that kind's on-state off for the match, so a misread never stops bots shooting (an "armor lock" held
# longer than its energy lasts: the tag drains it in 5 s; jetpack 4 s, hardlight 5 s). Each limit has 3 s more: the
# reader stays on 2-3 s after release (Spire trace). Per kind, names kept: one guard for all blanked every player's
# equipment, the bots' own too, so no bot used an ability for the rest of the match (logs/ brain.txt, Spire: "477.5s
# ability readers off: player 0 shows evade on for 2 s", a skilled player's evade)
ABILITY_MAX_ON = {"armor_lock": 9.0, "jetpack": 8.0, "hardlight_shield": 9.0, "promethean_vision": 9.0,
                  "sprint": 9.0, "autosentry": 13.0, "regen_field": 15.0, "drop_shield": 9.0, "hologram": 5.0,
                  "evade": 5.0, "thruster": 5.0,
                  "active_camo": 63.0}   # (camo drains only with movement: a still camper keeps it long)
ABILITY_READ = dict(bad=set(), since={})


def ability_misread(players, now):
    """The ability kinds whose on-state is not trusted (from then on for this match): any player showed one running
    longer than it can."""
    bad, since = ABILITY_READ["bad"], ABILITY_READ["since"]
    for p in players:
        key = (p.id, p.equipment)
        if p.ability_on and p.equipment in ABILITY_MAX_ON and p.equipment not in bad:
            since.setdefault(key, now)
            if now - since[key] > ABILITY_MAX_ON[p.equipment]:
                bad.add(p.equipment)
                print(f"{now:6.1f}s {p.equipment} reader off: player {p.id} shows it on for "
                      f"{now - since[key]:.0f} s (cannot happen: a misread)", flush=True)
        else:
            since.pop(key, None)
    return bad


INPUTS = os.path.join(ROOT, "maps", "_inputs")


def decoded_buttons(game):
    """The buttons decoded for this game (maps/_inputs/<game>.json, tools/decode_buttons.py; Halo 3's
    equipment bit from its own input code): [(game, button, offset, mask, press_only)] for
    botctl.set_button_map. Buttons nobody decoded stay unpressed: no guessed input reaches the game."""
    path = os.path.join(INPUTS, game + ".json")
    if not os.path.exists(path):
        return []
    out = []
    for name, rows in json.load(open(path)).items():
        if name in botctl.BTN:
            out += [(game, name, r["off"], r["mask"], r.get("press", False)) for r in rows]
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("seconds", type=float, nargs="?", default=60)
    ap.add_argument("--tier", default="strong")
    ap.add_argument("--personality", default="rusher")
    ap.add_argument("--roster", help="botcore.ini with the BOTS panel's list: each bot's difficulty and play "
                    "style by its name (bots not on it get --tier / --personality)")
    ap.add_argument("--ff-list", action="store_true", help="Firefight: --roster's Firefight list (ffbot= lines)")
    ap.add_argument("--map", default="bloodgulch", help="map folder in maps/ (play.py passes the running map)")
    ap.add_argument("--ffa", action="store_true", help="free for all: every player is an enemy")
    ap.add_argument("--game", default="ce", choices=("ce", "h2", "h3", "reach", "odst", "h2a", "h4"), help="ce = Halo CE (halo1.dll), h2 = Halo 2, h3 = Halo 3, reach = Halo: Reach, h2a = Halo 2 Anniversary, h4 = Halo 4")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--callouts", action="store_true", help="print callouts even with --quiet")
    ap.add_argument("--trace", help="write one JSON line per bot per tick (pos, goal, waypoint, action)")
    ap.add_argument("--collect", choices=("on", "off"), default="off",
                    help="the Data collector: record the people to logs/collect_<map>_<stamp>.jsonl (tools/collector.py)")
    ap.add_argument("--ride-only", help="test: the only empty vehicles bots see, kinds by name (comma list, e.g. falcon)")
    ap.add_argument("--forge-invasion", action="store_true", help="test: the map's own Invasion setup left out, as on "
                    "a Forge map variant's Invasion (respawns in the game's zones only)")
    ap.add_argument("--aim-loose", choices=("on", "off"), help="test: the looser trigger below Legendary on or off "
                    "for this match (brain/profiles.py LOOSE_AIM, the default)")
    o = ap.parse_args()
    # Scaling difficulty (brain/scaling.py): one skill rating with no limit, set from the person's last Scaling games'
    # K/D; every bot on Scaling (its row on the BOTS list, else --tier) is made from it, both teams
    SCALE, SCALE_CFG = None, scaling.settings()
    panel = (botctl.roster(o.roster) or []) if o.roster else []
    if o.roster and o.ff_list:   # Firefight's own list (play.py), else the list's first 3 till it is saved (ff_seed)
        ff_list = botctl.roster(o.roster, firefight=True)
        panel = ff_list if ff_list is not None else panel[:botctl.player_cap(o.game, True) - 1]
    on_list = [b["tier"] for b in panel]
    if "scaling" in o.tier.split(",") + on_list:
        SCALE = scaling.update(n=scaling.games_window(o.roster) if o.roster else scaling.GAMES, cfg=SCALE_CFG)["play"]
        print("Scaling difficulty:", scaling.summary(SCALE, SCALE_CFG["weights"]),
              "(locked)" if SCALE_CFG["lock"] else "", SCALE_CFG["name"], flush=True)
    if o.aim_loose:
        import brain.profiles
        brain.profiles.LOOSE_AIM = o.aim_loose == "on"
    global RIDE_ONLY
    RIDE_ONLY = set(o.ride_only.split(",")) if o.ride_only else None
    global MAP, FIREFIGHT, TEAMS
    import brain.maps
    brain.maps.PVP = True                       # simulator-only jumps wait for a real landing (maps.usable_links)
    MAP = get_map(o.map)
    TEAMS = not o.ffa
    live, m = ({"h2": LiveH2, "h3": LiveH3, "reach": LiveReach, "odst": LiveODST, "h2a": lambda: LiveH4F("groundhog.dll"),
      "h4": LiveH4F}.get(o.game, Live))(), botctl.shm()
    FIREFIGHT = hasattr(live, "firefight") and live.firefight()
    T = None
    # Halo 4 / H2A: on their Forge canvases only (exported bare: tools/reach_export.py --h4 / --h2a); a built-in map's
    # game-type crates are its own placements there (tools/placed_refresh.py), not a layout
    canvas = o.game in ("h4", "h2a") and "forge" in MAP["name"]
    if (o.game == "reach" or canvas) and not FIREFIGHT:   # a Forge map variant: filed under its base map with a navmesh
        try:                                  # of its own, its pieces read live and named by tag id (brain/forge.py,
            from brain.forge import live_pieces   # brain/variants.py)
            from brain.variants import base_area, base_placed, identify
            pieces = live_pieces(live)
            name = identify(MAP["name"], pieces, base_placed(MAP["objects"]), game=o.game,
                            area=base_area(MAP["objects"]))
            if name != MAP["name"] and o.game == "reach":
                try:                                # its own .mvar file (brain/mvar.py): Forge labels, Invasion phases,
                    from brain import mvar          # kill boundaries, which the game's memory does not give
                    v, share = mvar.find(MAP["name"], pieces)
                    if v is not None:
                        nf, ns, nk = mvar.write(name, v, share)
                        print(f"map variant file {os.path.basename(v['path'])} ({v['title']!r}, {share:.0%} of its "
                              f"pieces live): {nf} labelled objects, {ns} spawns, {nk} kill / safe zones", flush=True)
                    else:
                        print(f"map variant {name}: no .mvar file on this PC places its pieces (best {share:.0%}): "
                              f"objectives from the live objects only", flush=True)
                except Exception as e:
                    print("map variant file not read:", e, flush=True)
            if name != MAP["name"]:                 # its nav from its own pieces and shields (variant.json), its window
                import variant_strategy             # its own callouts and power positions, not the canvas's (the player,
                # "the bots [were not] using the building structures"); a built-in map's variant keeps the base's
                # (brain/maps.py load_map)
                if not base_area(MAP["objects"]) and variant_strategy.stale(name):
                    print(f"map variant {name}: making its callouts and power positions (once per layout) ...", flush=True)
                    print(f"  done in {variant_strategy.build(name):.0f} s", flush=True)
                MAP = get_map(name)
                T = map_terrain(MAP)
                from brain import variants as _variants
                if o.game == "reach" and not os.path.exists(os.path.join(_variants.ROOT, name, "mvar_objects.json")):
                    # no .mvar file: its items are the canvas's placements, not what it has (#11): what lies in it
                    from brain.forge import live_items
                    found = live_items(live)
                    print(f"map variant {name}: {len(found)} power items lying in it (was {len(MAP['items'])} of the "
                          f"canvas's): {sorted({it['name'] for it in found})}", flush=True)
                    MAP["items"] = found
                print(f"map variant {name}: {MAP['variant']['count']} objects of its own, {len(MAP['live_blocks'])} "
                      f"pieces in the nav ({getattr(T, 'face_kinds', {})}), {len(MAP['shields'])} shields, nav window "
                      f"{MAP['window']}", flush=True)
        except Exception as e:
            print("could not read the game's Forge pieces:", e, flush=True)
    if T is None:
        T = map_terrain(MAP)
    palette = Palette() if getattr(live, "LOADOUT", 0) else None   # Reach, Halo 4, H2A
    loadout_state = {}
    pressable = decoded_buttons(o.game)          # melee, grenades, equipment ... where decoded
    botctl.set_button_map(m, pressable)
    print(f"decoded buttons for {o.game}: {sorted({b for _, b, *_ in pressable}) or 'none'}", flush=True)
    if o.game in ("h3", "reach", "odst", "h2a", "h4", "h2"):
        live.set_map(MAP["objects"])            # Halo 3 weapon names come from the map's placements (Halo 2: its hills)
    rng = random.Random(7)
    bots, slots, directors, prev, last_yaw, before = {}, {}, {}, {}, {}, {}
    comms, said = [], 0           # director log: every callout spoken
    said_calls = set()            # callouts already in the trace
    everyone = Everyone()         # the world line rows: still time, teammates formed up, who is in sights
    listed = {b["name"].lower(): b for b in panel}
    dealt = {}                         # team -> bots dealt a style / tier so far (roster_pick)
    flag_before = {}
    t0 = time.time()
    paused = [0.0]               # seconds the game sat paused, off the brain's clock (the loop below)
    still = [None, 0, None]      # botcore's last tick count, loop passes it held, when the pause began
    last_print = 0.0
    import recording                         # the longest recording (the Observer's Recording settings): its last
    rec_s = recording.settings()["minutes"] * 60.0   # minutes kept, trimmed on disk as it is written
    trace = recording.RollingFile(o.trace, rec_s) if o.trace else None
    if trace and SCALE is not None:          # the match ran on Scaling: brain/scaling.py counts it after (and its settings)
        trace.write(json.dumps(dict(t=0.0, scaling=round(SCALE, 5))) + chr(10))
    last_wp = {}

    def nav(a, b, **kw):                      # remember each bot's waypoint for the trace
        w = T.nav(a, b, **kw)
        last_wp["w"] = w
        return w
    cur = [None]                                 # this tick's snapshot, for key()

    def key(b):                                  # one director per team or side, or per bot in free for all
        return director_key(b, cur[0], o.ffa)

    from lidar import HumanWatch, RideWatch
    people = HumanWatch(MAP["name"], T=T)    # people's floor, deaths and landed jumps -> the map's (variant's) LiDAR data
    # everyone's drives and flights -> the map's drive mesh and airspace (rides.json), applied to this match as saved
    rides, rides_new = RideWatch(MAP["name"], T=T), [False]

    def save_lidar():
        """Both LiDAR saves, each on its own: one failing is logged and the other still saves (a failing people's save
        took the whole match's data with it, Lockout)."""
        for what, save in (("rides", rides.save), ("people's LiDAR", people.save)):
            try:
                if save() is True and what == "rides":
                    rides_new[0] = True
            except Exception as e:
                print(f"{what} not saved: {type(e).__name__}: {e}", flush=True)
    collector = None
    if o.game == "h4":                 # each terminal's object handle, the use target a bot's Use needs (botctl.set_use_target)
        n_targets = dom_base.load_use_targets(os.path.join(INPUTS, "h4_use_targets.json"), o.map)
        print(f"use targets for {o.map}: {n_targets} terminal(s)" + ("" if n_targets else " (none: a bot's Use at a terminal has no target)"), flush=True)
    owner_input = None                 # the person's own input block, for decoding a real E at a terminal (tools/input_log.py)
    if o.game == "h4":
        try:
            from input_log import OwnerInput
            owner_input = OwnerInput(os.path.join(ROOT, "logs", f"owner_input_{o.map}_{time.strftime('%Y%m%d-%H%M%S')}.jsonl"),
                                     m, botctl)
            print("owner input log on:", owner_input.path, flush=True)
        except Exception as e:                       # never stops the bots
            print(f"owner input log could not start: {type(e).__name__}: {e}", flush=True)
    from crouch_key import crouch_held
    try:
        crouch_now = crouch_held(live.g.pid)           # the person's crouch key held now (never saved)
    except Exception as e:                           # (no keyboard read: he reads as standing)
        print(f"crouch key not read: {type(e).__name__}: {e}", flush=True)
        crouch_now = lambda: False                   # noqa: E731
    crouch_cmd = {}                                  # bot index -> its last command crouched
    if o.collect == "on":      # the BOTS panel's Data collector: the people, for the Observer (tools/player_stats.py)
        from collector import Collector
        same = re.search(r"_(\d{8}-\d{6})\.jsonl$", o.trace or "")     # the match's trace stamp: the pair is found by it
        stamp = same.group(1) if same else time.strftime("%Y%m%d-%H%M%S")
        try:
            collector = Collector(os.path.join(ROOT, "logs", f"collect_{o.map}_{stamp}.jsonl"), o.map, o.game,
                                  lambda: (still[2] if still[2] is not None else time.time()) - t0 - paused[0], seconds=rec_s)
            print("data collector on:", collector.path, flush=True)
        except Exception as e:                       # never stops the bots
            print(f"data collector could not start: {type(e).__name__}: {e}", flush=True)
    stations, st_check = None, [-9.0, -9.0]     # every gameplay object (brain/stations.py), for LiDAR
    if o.game in ("h3", "reach", "h4", "h2a"):
        from brain.stations import Stations, scan as station_scan
        stations = Stations(MAP["name"])
    from brain.oob import Oob                    # out of bounds, learned in every game (brain/oob.py)
    oob = Oob(MAP["name"])
    oob_saved = [oob.version, 0.0]
    if oob.spots or oob.volumes:                 # (and the map's own kill zones: maps/<map>/killzones.json)
        T.set_oob(oob.spots, oob.version, oob.volumes)
        print(f"out of bounds: {len(oob.spots)} spots, {len(oob.volumes)} kill / safe zones known here", flush=True)
    fails = 0
    seen_errors = set()
    last_items = 0.0
    last_think = None                  # the time of the last pass the bots thought on (think_dt)
    passes = 0                         # think passes so far (think_order: which team goes first)
    names_due = None if o.game == "reach" else float("inf")   # Reach: logs/reach_names.txt once, 20 s after bots join (after any refresh)
    # Reach Invasion (brain/invasion.py): the map's setup from its scenario; the watcher runs while the variant is it
    inv_styles = None                             # a map variant read from its file: its game variant's phases
    if o.game == "reach" and not FIREFIGHT and MAP["objects"].get("mvar"):     # (brain/mvar.py: its file by its title)
        try:
            from brain import mvar
            gv = mvar.find_game_variant(live.variant_name())
            inv_styles = mvar.phase_styles(gv) or None
            if gv:
                opts = ", ".join(f"{x['name']} = {x['value']}" for x in gv["options"])
                print(f"game variant file {os.path.basename(gv['path'])} ({gv['title']!r}): {opts}", flush=True)
        except Exception as e:
            print("game variant file not read:", e, flush=True)
    inv_layout = invasion_layout(MAP["objects"], styles=inv_styles) if (o.game == "reach" and not FIREFIGHT
                                                                        and not o.forge_invasion) else None
    if o.game == "reach" and not FIREFIGHT and not inv_layout and getattr(invasion_layout, "reject", None):
        print(f"Invasion setup NOT used (so no Inv_res_p respawns): {invasion_layout.reject}", flush=True)
    inv_watch, inv_check, inv_unread = [None], [-9.0], [False]
    inv_trace_t = [-9.0]
    zone_check, zone_state = [-9.0], {}           # Invasion with no setup of its own: the game's zones (zone_spawn_check)
    # a map variant's pieces that come and go (brain/forge.py PieceWatch): walls while they stand
    piece_watch, piece_check = None, [-9.0, 0]
    if MAP.get("variant") and not FIREFIGHT:
        from brain.forge import PieceWatch, live_pieces
        from brain.variants import pieces_of, info as variant_info, save as variant_save
        piece_watch = PieceWatch(pieces_of(MAP["variant"]), MAP["variant"].get("dynamic", ()),
                                 MAP["variant"].get("game") or o.game)
    inv_spawns, spawn_state = honor_spawns(MAP.get("base") or MAP["name"]) if inv_layout else None, {}
    ff_spawn_pools, ff_spawn_state = None, {}
    ff_round_watch = FirefightRoundWatch()
    if FIREFIGHT and o.game == "reach":
        print("Firefight: recording bots' initial team spawn positions", flush=True)
    mode_watch, mode_check = [None], [-9.0]      # Territories, Assault, Stockpile, Headhunter, Infection (brain/modes.py)
    bug_check, grow_check = [-9.0, None], [-9.0]     # bug_check[1]: the last tick's mode once read (see below)
    import bug_reports
    try:
        while time.time() - t0 < o.seconds:
            try:
                tick = time.time()
                now = (still[2] if still[2] is not None else tick) - t0 - paused[0]
                st = botctl.status(m)
                oc = botctl.owner_crouch(m)             # H2A / Halo 4: his own action's crouch bit; elsewhere his key
                snap = snapshot(live, now, prev, set(st["bots"].values()), {i for i, c in crouch_cmd.items() if c},
                                oc if oc is not None else crouch_now())
                # the game paused (the pause menu: the player's bug notes held matches still 15-35 s): botcore's tick
                # count (its per-tick game hook) stands still. The brain's clock stops with it, so nothing reads as
                # stuck or waited out (the critic flagged bots "pushed into something" all through his pauses)
                gt = st["game_tick"]
                still[0], still[1] = gt, (still[1] + 1 if gt == still[0] else 0)
                if still[1] >= PAUSE_TICKS and still[2] is None:
                    still[2] = tick
                elif still[1] < PAUSE_TICKS and still[2] is not None:
                    paused[0] += tick - still[2]
                    still[2] = None
                    now = tick - t0 - paused[0]
                if now - bug_check[0] > 1.0:            # the player's bug reports (tools/bug_reports.py): the moment
                    bug_check[0] = now                   # and this match's recording go with each one saved now
                    try:
                        # (the mode the last tick read: this snapshot's is the raw one, before the Invasion and mode
                        # readers below, so)
                        for stamp in bug_reports.attach_new(t0, now, snap, MAP["name"], trace, o.trace, bots,
                                                               None, mode=bug_check[1],
                                                               stations=stations, oob=oob):
                            print(f"{now:6.1f}s bug report {stamp}: recording attached", flush=True)
                    except Exception as e:
                        print(f"{now:6.1f}s bug report attach failed: {type(e).__name__}: {e}", flush=True)
                if trace is not None:             # the recording's oldest minutes off (tools/recording.py)
                    trace.tick(now)
                if still[2] is not None:          # paused: nothing moves, nothing to do
                    time.sleep(1 / HZ)
                    continue
                try:                            # out of bounds, learned (brain/oob.py): a soft wall for every bot
                    for x, y, z, counted in oob.watch(now, [(p.id, p.team, (p.pos.x, p.pos.y, p.pos.z), p.alive,
                                                             p.health, p.shield, ride_health(snap, p)) for p in snap.players],
                                                      lambda a, b: T.los(Vec(*a), Vec(*b))):
                        print(f"{now:6.1f}s out of bounds: a death nobody caused at ({x:.1f}, {y:.1f}, {z:.1f})"
                              + ("" if counted else ", once (a second one there counts)"), flush=True)
                    if oob.version != oob_saved[0]:
                        T.set_oob(oob.spots, oob.version)
                        if now - oob_saved[1] > 30.0:
                            oob_saved[:] = [oob.version, now]
                            oob.save()
                except Exception as e:                   # never stops the bots
                    print(f"{now:6.1f}s out of bounds watch failed: {type(e).__name__}: {e}", flush=True)
                if stations is not None:        # every gameplay object, teleports taken (brain/stations.py)
                    try:
                        stations.watch([(p.id, (p.pos.x, p.pos.y, p.pos.z), p.alive) for p in snap.players])
                        if now - st_check[0] > 2.0:
                            st_check[0] = now
                            stations.update(station_scan(live, o.game), {p.vehicle for p in snap.players if p.vehicle != -1},
                                            [(p.pos.x, p.pos.y, p.pos.z) for p in snap.players if p.alive])
                        if now - st_check[1] > 30.0:
                            st_check[1] = now
                            stations.save()
                    except Exception as e:      # never stops the bots: off for the rest of the match
                        print(f"{now:6.1f}s stations stopped: {type(e).__name__}: {e}", flush=True)
                        stations = None
                # Reach Invasion: is the variant it? Read on every map: a Forge map variant's Invasion runs on maps whose
                # scenario has none (Tempest), and was read as Slayer with no word of it
                if o.game == "reach" and not FIREFIGHT and now - inv_check[0] > 5.0:
                    inv_check[0] = now
                    try:
                        # (on a map with no setup of its own also by Invasion's engine icon, 9: live, both built-in
                        # variants; a custom variant's name may not say it)
                        on = live.invasion() or (not inv_layout and live.engine_icon() == 9
                                                 and "SLAYER" not in live.variant_name().upper())
                    except Exception:
                        on = False
                    if on and not inv_layout and not inv_unread[0]:
                        # its objectives and gates are the map variant's labelled Forge objects, whose labels are not
                        # read from the game yet (tools/forge_label_probe.py): played as Slayer, respawns in the zones
                        # the game gives each bot (zone_spawn_check)
                        inv_unread[0] = True
                        print(f"{now:6.1f}s Invasion on {MAP['name']}: its objectives are a Forge map variant's (labels "
                              f"not read yet): bots play it as Slayer and respawn in the game's zones", flush=True)
                        if trace:
                            trace.write(json.dumps(dict(t=round(now, 3), invasion_unread=MAP["name"])) + chr(10))
                    if on and inv_layout and inv_watch[0] is None:
                        inv_watch[0] = Watch(inv_layout, T)
                        print(f"Invasion: attackers team {inv_layout['attackers']}, {len(inv_layout['barriers'])} "
                              f"barriers on the nav", flush=True)
                        for line in invasion_res_summary(inv_layout):
                            print(line, flush=True)
                    elif not on and inv_watch[0] is not None:
                        clear_barriers(T, inv_layout)
                        inv_watch[0] = None
                        oob.set_phase(T, None)
                # a map variant whose file gave no labelled Invasion setup at all (inv_layout None): only the game's
                # own live-read zones are there to use (the engine's own Invasion respawn read, unreliable in
                # customs - the res-zone fallback below needs the map's hills, which this map has none parsed)
                if inv_unread[0] and now - zone_check[0] > 0.5:
                    zone_check[0] = now
                    try:
                        zone_spawn_check(lambda i, *a: botctl.spawn_at(m, i, *a), bots, snap, live.respawn_zone,
                                         zone_state, rng, T, now, trace)
                    except Exception as e:           # (never the whole tick: bots then spawn where the game puts them)
                        inv_unread[0], zone_check[0] = False, float("inf")
                        print(f"{now:6.1f}s zone spawns off ({type(e).__name__}: {e})", flush=True)
                if inv_watch[0] is not None:
                    inv_watch[0].update(live, snap, now, getattr(live, "raw", None))
                    # Diagnostic only: record the watcher's inputs so a wrong hill can be traced to either
                    # phase detection or role/team mapping. This does not alter bot decisions.
                    if trace and now - inv_trace_t[0] >= 1.0:
                        inv_trace_t[0] = now
                        w = inv_watch[0]
                        recent = [(team, [round(p.x, 2), round(p.y, 2), round(p.z, 2)])
                                  for _, team, p in w.spawns[-8:]]
                        attacker_votes = [phase_from_spawns(w.inv, [(team, pos)])
                                          for _, team, pos in w.spawns[-8:] if team == w.attackers]
                        trace.write(json.dumps(dict(t=round(now, 3), invasion_watch=True,
                                                    phase=w.phase, attackers=w.attackers,
                                                    map_attackers=w.inv.get("attackers"),
                                                    sides_set=w.sides_set, round_wait=round(w.round_wait_until, 2),
                                                    phase_votes=attacker_votes, spawns=recent,
                                                    score=getattr(snap, "score", None),
                                                    score_base=getattr(w, "score_base", None),
                                                    score_cap=getattr(w, "score_cap", None))) + chr(10))
                    oob.set_phase(T, inv_watch[0].phase)   # (its gates' kill volumes stand until their phase is taken)
                    # Explicit Inv_res_p1..p3 map labels describe the respawn phase and take priority over
                    # inv_spawns.json named offers. Otherwise retain the existing honor-spawn system unchanged.
                    use_map_phase_res = bool(inv_layout.get("res_labelled"))
                    if inv_spawns and not use_map_phase_res:
                        try:
                            spawn_check(lambda i, *a: botctl.spawn_at(m, i, *a), bots, snap, now, spawn_state, inv_spawns,
                                        inv_watch[0], rng, T, trace)
                        except Exception as e:           # (never the whole tick: bots then spawn where the game puts them)
                            inv_spawns = None
                            import traceback
                            print(f"{now:6.1f}s spawn picks off ({type(e).__name__}: {e}):", traceback.format_exc(limit=4),
                                  flush=True)
                    else:
                        # A map with its own Invasion setup and phase-labelled respawn flags (or no named-spawn
                        # configuration) uses its map-authored team hills directly. Keep res_spawn_check's existing
                        # attacker/defender-to-map-team mapping so round-two side swaps remain correct.
                        try:
                            res_spawn_check(lambda i, *a: botctl.spawn_at(m, i, *a), bots, snap, now, spawn_state,
                                            inv_layout, inv_watch[0], rng, T, trace)
                        except Exception as e:           # (never the whole tick: bots then spawn where the game puts them)
                            import traceback
                            print(f"{now:6.1f}s res-zone spawn picks off ({type(e).__name__}: {e}):",
                                  traceback.format_exc(limit=4), flush=True)
                elif o.game in ("reach", "h3", "h4", "h2a", "h2", "ce") and not FIREFIGHT and now - mode_check[0] > 5.0:   # the other objective modes (CE: Race)
                    mode_check[0] = now
                    mode = mode_of(o.game, live)
                    if mode in OBJECT_MODES and (mode_watch[0] is None or mode_watch[0].kind != mode):
                        mode_watch[0] = ModeWatch(mode, MAP["objects"], o.game)
                        dom_base.reset()           # a new match / mode: no stale claims or cooldowns
                        print(f"game type: {mode}", flush=True)
                    elif mode not in OBJECT_MODES:
                        mode_watch[0] = None
                if FIREFIGHT and o.game == "reach":
                    try:
                        if ff_round_watch.update(snap, now, bots):
                            ff_spawn_state.clear()
                            ff_spawn_pools = None
                            print(f"{now:6.1f}s Firefight: round {ff_round_watch.round_number} detected; cleared old spawn pools", flush=True)
                        ff_spawn_check(lambda i, *a: botctl.spawn_at(m, i, *a),
                                       bots, snap, ff_spawn_state, ff_spawn_pools, rng, now, trace,
                                       round_pending=ff_round_watch.pending)
                    except Exception as e:
                        print(f"{now:6.1f}s Firefight spawn picks failed ({type(e).__name__}: {e})", flush=True)
                if mode_watch[0] is not None and inv_watch[0] is None:
                    mode_watch[0].update(live, snap, now, getattr(live, "raw", None))
                if piece_watch is not None and now - piece_check[0] > 2.0:
                    piece_check[0] = now
                    try:
                        gone = piece_watch.update(live_pieces(live), T)
                        if gone:                 # filed: the next match's nav leaves them out (brain/maps.py)
                            data = variant_info(MAP["name"]) or {}
                            data["dynamic"] = sorted(set(data.get("dynamic", ())) | gone)
                            variant_save(MAP["name"], data)
                            print(f"{now:6.1f}s map variant: {len(gone)} pieces went (dynamic from the next match)",
                                  flush=True)
                        if len(piece_watch.walls) != piece_check[1]:
                            piece_check[1] = len(piece_watch.walls)
                            print(f"{now:6.1f}s map variant: {piece_check[1]} pieces stood up after the nav was built "
                                  f"(walls while they stand)", flush=True)
                    except Exception as e:       # never stops the bots: off for the rest of the match
                        print(f"{now:6.1f}s piece watch stopped: {type(e).__name__}: {e}", flush=True)
                        piece_watch = None
                bug_check[1] = snap.mode
                crouching = botctl.owner_crouch(m)  # player 0 = the player (None: not decoded for this game)
                steering = botctl.owner_jumping(m)
                people.update(now, [(p.id, (p.pos.x, p.pos.y, p.pos.z) if p.alive else None, p.alive, bool(crouching) and p.id == 0,
                                     steering if p.id == 0 else None, p.vehicle != -1)
                                    for p in snap.players if p.id not in st["bots"].values() and p.name != "ai"])
                rides.update(now, snap.vehicles, snap.players, set(st["bots"].values()))
                if rides_new[0]:                 # saved new rides: on this match's terrain now (main thread)
                    rides_new[0] = False
                    from brain.maps import data_names, rides_layers
                    T.add_rides(*rides_layers(data_names(MAP), T))
                prev = {p.id: p.pos for p in snap.players if p.alive}
                if MAP.get("window") is not None:   # a map variant's nav grows as players near its edges (brain/variants.py)
                    if now - grow_check[0] > 1.0:
                        grow_check[0] = now
                        w = map_grow(MAP, [p.pos for p in snap.players if p.alive])
                        if w:
                            print(f"{now:6.1f}s nav window growing to {w}", flush=True)
                    if map_swap(MAP):
                        print(f"{now:6.1f}s nav grown: window {MAP['window']}", flush=True)
                # each person's crosshair and ride: for the collector every tick, for the trace's world lines
                aims = {p.id: (crosshair(p, snap.players, snap.vehicles, T), seat_of(p, snap.vehicles))
                        for p in snap.players if not p.is_bot} if collector or trace else None
                if collector:
                    try:
                        collector.tick(now, snap.players, lambda i: unit_zoom(live, i), aims)
                    except Exception as e:           # never stops the bots: off for the rest of the match
                        print(f"data collector stopped: {type(e).__name__}: {e}", flush=True)
                        collector.close()
                        collector = None
                events(bots, directors, before, snap, now)
                flag_events(directors, flag_before, snap, now)
                flag_before = {t: f.state for t, f in snap.flags.items()}
                before = {p.id: p for p in snap.players}
                for slot, index in st["bots"].items():      # new bots: brain + director membership
                    me = snap.player(index)
                    if index not in bots and me:
                        tiers, pers = o.tier.split(","), o.personality.split(",")
                        mine = listed.get(me.name.lower(), {})        # its row on the BOTS panel's list
                        style = mine.get("style", "any")
                        p_style, p_tier = roster_pick(pers, tiers, dealt, me.team)   # (per team: the rosters mirror)
                        style = style if style != "any" else p_style
                        tier = mine.get("tier") or p_tier
                        prof = (scaling.profile(SCALE, style, rng, SCALE_CFG["weights"]) if tier == "scaling" else
                                make_profile(tier, style, rng))
                        side = me.team % 2                 # map knowledge (holds, home) is red/blue
                        bots[index] = Bot(index, side, prof, MAP, random.Random(index))
                        bots[index].game_team = me.team     # its director: the game's team
                        bots[index].liked = liked_guns(me.name)   # its favourite loadout guns (brain/loadouts.py)
                        # Reach: botcore s67 fills the pickup itself (native/reach.cpp hr_loose_weapon); Halo 3 the
                        # same way as Halo 2. H2A / H4 pickups not decoded yet
                        bots[index].can_pickup = o.game in ("ce", "h2", "h3", "reach")
                        # what reaches the game: botcore sends melee natively only in CE; the rest
                        # only where decoded (maps/_inputs); a bot plans with what it can press
                        names = {n for _, n, *_ in pressable}
                        bots[index].can_melee = o.game == "ce" or "melee" in names
                        bots[index].kit.pressable = "equipment" in names
                        # (Halo 4's sprint is botcore's own, s123: native/h4f.cpp f_sprint)
                        bots[index].kit.sprint_ok = "sprint" in names or o.game == "h4"
                        bots[index].kit.decoys_listed = False   # holograms are not players: never listed
                        bots[index].kit.unread = ABILITY_READ["bad"]   # (the same set: a reader turned off reads off)
                        slots[index] = slot
                        if trace:                          # who is who (tools/observer.py)
                            trace.write(meta_line(now, o.map, o.game, index, me.name, me.team, prof))
                cur[0] = snap

                def make(k, members):                   # (a director's members changed: rebuilt with them)
                    team = k[1] if isinstance(k, tuple) and k[0] == "side" else next(iter(members.values())).game_team
                    return Director(team, MAP, members, random.Random(hash(k) & 0xFFFF), log=comms,
                                    side=team % 2, home_rule=o.game in ("ce", "reach"))
                regroup(directors, bots, snap, o.ffa, make)
                for d in directors.values():
                    if snap.infected != -1 and not TEAMS:   # Halo 3 Infection: one side each, survivors or infected
                        p = snap.player(next(iter(d.bots)))
                        d.team = p.team if p else d.team
                    d.update(now, snap)
                    if trace:                           # every callout, once (tools/observer.py)
                        trace.writelines(call_lines(now, d, said_calls))
                if names_due is None and bots:
                    names_due = now + 20.0
                if names_due is not None and now >= names_due:
                    names_due = float("inf")
                    try:
                        reach_name_dump(live, os.path.join(ROOT, "logs", "reach_names.txt"))
                    except Exception as e:                     # (diagnostic only: never stops the bots)
                        print("reach name dump failed:", e, flush=True)
                if trace and now - getattr(main, "world_at", -1.0) >= 0.25:   # everyone, for tools/observer.py
                    main.world_at = now
                    bot_set = set(st["bots"].values())
                    trace.write(json.dumps(dict(
                        t=round(now, 3), mode=snap.mode, score=snap.score, limit=snap.score_limit, time_left=snap.time_left,
                        gt=st["game_tick"], fault=st["fault"],     # botcore's tick count and caught faults (input stalls)
                        posture={str(d.team): getattr(d, "posture_state", "?") for t, d in directors.items()
                                 if not (isinstance(t, tuple) and t[0] == "bot")},
                        blitz=blitz_rows(directors),
                        crouch=[p.id for p in snap.players if p.crouching],   # (who reads crouched: aim heights)
                        world=everyone.rows(now, snap.players, bot_set, T.los, aims),   # + still, formed up, in sights,
                        # people's crosshairs and rides
                        deployed=[[d["kind"], round(d["pos"].x, 2), round(d["pos"].y, 2), round(d["pos"].z, 2), d["team"]]
                                  for d in snap.deployed],
                        # brain/modes.py: zones [x, y, z, r, the player], items [x, y, z, carrier, team], goals {team:
                        # [[x, y, z]]}
                        zones=[[round(z.x, 1), round(z.y, 1), round(z.z, 1), round(r, 1), o] for z, r, o in snap.obj_zones],
                        items=[[round(it["pos"].x, 1), round(it["pos"].y, 1), round(it["pos"].z, 1), it["carrier"],
                                it.get("team", 8)] for it in snap.obj_items],
                        # the objective, for tools/objective_report.py: flags {team: [state, x, y, z, carrier]}, hill, ball
                        flags={str(t): [f.state, round(f.pos.x, 2), round(f.pos.y, 2), round(f.pos.z, 2), f.carrier]
                               for t, f in snap.flags.items()},
                        hill=[round(snap.hill.x, 2), round(snap.hill.y, 2), round(snap.hill.z, 2), round(snap.hill_r, 2)]
                        if snap.hill is not None else None,
                        ball=[round(snap.ball.x, 2), round(snap.ball.y, 2), round(snap.ball.z, 2), snap.ball_carrier]
                        if snap.ball is not None else None,
                        # Invasion: [phase, attacking team, core x, y, z or None, its carrier] (tools/invasion_report.py)
                        invasion=[snap.inv_phase, snap.inv_attackers] + ([round(snap.inv_core.x, 2), round(snap.inv_core.y, 2),
                             round(snap.inv_core.z, 2)] if snap.inv_core is not None else [None] * 3) +
                            [snap.inv_core_carrier] if snap.mode == "invasion" else None,
                        # vehicles (tools/observer.py): [handle, kind, x, y, z, riders, team, up z (None: not read),
                        # health left (None: not read), wreck 0/1 (vehicles.usable)]; who rides: bot rows' veh
                        vehicles=[[v["handle"], v["kind"], round(v["pos"][0], 2), round(v["pos"][1], 2),
                                   round(v["pos"][2], 2), v["riders"], v["team"],
                                   round(v["up"][2], 2) if v.get("up") else None, v.get("left"), int(bool(v.get("dead")))]
                                  for v in snap.vehicles])) + chr(10))
                if now - getattr(main, "saved_at", 0.0) > 60:   # learned sight lines, once a minute
                    main.saved_at = now
                    save_learned(MAP)
                    # (on a thread: the merge and write of a big map's lidar.json froze every bot for a few ticks)
                    threading.Thread(target=save_lidar, daemon=True).start()
                if now - last_items > 0.5:
                    last_items = now
                    item_check(live, directors, bots, snap, now)
                # (no loadout asks in Infection / Flood: Halo 4 Flood took them, slots 3-4 handing survivors a DMR and a
                # magnum where everyone else spawned with the shotgun, the owner's Ragnarok game: "why did this guy have a
                # DMR?"; the game's own loadout, as a person who picks none)
                if palette and snap.mode == "infection":
                    for i, st in loadout_state.items():     # (asked before the mode was read: back to the game's own)
                        if st.get("asked") not in (None, 0) and botctl.set_loadout(m, i, 0, o.game):
                            st["asked"] = 0
                if palette and snap.mode != "infection":
                    loadout_check(live, lambda i, sl: botctl.set_loadout(m, i, sl, o.game), bots, palette,
                                  loadout_state, snap, now, trace)
                dt = think_dt(now - last_think if last_think is not None else 0.0)
                last_think = now
                passes += 1
                if owner_input:
                    try:
                        owner_input.tick(now)
                    except Exception as e:
                        print(f"owner input log stopped: {type(e).__name__}: {e}", flush=True)
                        owner_input = None
                locked = {}                                   # bots holding a Dominion base this pass (brain/dom_base.py)
                for index, b in think_order(bots, passes):    # (the teams interleaved, the first swapping)
                    me = snap.player(index)
                    if not me or not me.alive:
                        continue
                    d = directors[key(b)]
                    last_wp["w"] = None
                    decide_was = b.next_decide
                    a = b.think(now, dt, snap, T.sees, nav, d.callouts, d.item_up)   # (sight: windows and fences pass)
                    if trace:
                        g, w = b.goal, last_wp["w"]
                        line = dict(t=round(now, 3), id=index, pos=[round(v, 2) for v in (me.pos.x, me.pos.y, me.pos.z)],
                                    yaw=round(me.yaw, 3), pitch=round(me.pitch, 3), goal=g[0], arg=str(g[1])[:60],
                                    veh=me.vehicle, wpn=me.weapon, wp=[round(w.x, 2), round(w.y, 2)] if w else None,
                                    foes=[[p.id, round(p.shield, 2), round(p.health, 2)] for p in snap.players
                                          if p.team != me.team],
                                    cmd=[round(a.forward, 2), round(a.strafe, 2), round(a.yaw, 3), buttons(a), round(a.pitch, 3)])
                        if me.vehicle not in (None, -1):
                            line["seat"] = getattr(b, "seat", None)   # (tools/live_qc.py: who drove, who took a gun)
                            if me.turret:
                                line["turret"] = me.turret
                        if b.fx:
                            line["fx"] = b.fx
                        if b.att:
                            line["att"] = b.att               # what it looks at and why
                        if b.notes:
                            line["notes"] = b.notes
                        if now < getattr(b, "jump_until", 0.0) and getattr(b, "jump_land", None) is not None:
                            jl = b.jump_land          # a planned jump's window: seconds left, its landing (edge_guard)
                            line["jw"] = [round(b.jump_until - now, 2), round(jl.x, 2), round(jl.y, 2), round(jl.z, 2)]
                        if me.equipment:
                            line["eq"] = [me.equipment, me.ability_on]
                        if len(me.weapons) > 1:
                            line["inv"] = me.weapons          # (tools/engagement.py: what it could switch to)
                        line["hp"] = [round(me.shield, 2), round(me.health, 2)]
                        uz = unit_zoom(live, index)
                        if uz is not None:
                            line["uz"] = uz              # the game's own zoom state (cmd bits 12-13: the ask)
                        if any(x >= 0 for x in me.ammo):
                            line["ammo"], line["mag"] = me.ammo, me.mag
                    if trace:
                        trace.write(json.dumps(line) + chr(10))
                        if b.next_decide != decide_was:      # it decided this tick: what it weighed and why
                            trace.write(think_line(now, index, b))
                    for kind, eid, pos, t, busy in b.reports:
                        d.report(t, index, kind, pos, enemy=eid, busy=busy)
                    b.reports.clear()
                    botctl.act(m, slots[index], a.forward, a.strafe, a.yaw, a.pitch, buttons(a))
                    if dom_base.holds(index):
                        locked[index] = (slots[index], a)
                    if o.game == "h4":                 # the terminal this bot holds, its use target (0 without a claim)
                        botctl.set_use_target(m, slots[index], dom_base.use_target(index))
                    last_yaw[index] = a.yaw
                    crouch_cmd[index] = a.crouch and o.game in BOT_CROUCH
                for c in comms[said:]:
                    if not o.quiet or o.callouts:
                        print(f"{now:6.1f}s {'RED ' if c[1] == 0 else 'BLUE'} says: {c[2]}", flush=True)
                said = len(comms)
                if not o.quiet and now - last_print > 2.0:
                    last_print = now
                    for index, b in bots.items():
                        me = snap.player(index)
                        print(f"{now:6.1f}s bot {index} {b.p.tier[:3]} t{b.team} {me.pos.x:6.1f},{me.pos.y:7.1f} goal {b.goal[0]:<8} "
                              f"sh {me.shield:.2f} hp {me.health:.2f}" if me and me.alive else f"{now:6.1f}s bot {index} dead",
                              flush=True)
                # a bot holding a base has its command re-sent a few times inside the pass, so the game's tick never
                # reads a stale or empty word between two passes (only those bots; everyone else as before)
                end = tick + 1 / HZ
                while locked and time.time() < end - 0.01:
                    time.sleep(0.008)
                    for slot, a in locked.values():
                        botctl.act(m, slot, a.forward, a.strafe, a.yaw, a.pitch, buttons(a))
                time.sleep(max(0.0, end - time.time()))
            except Exception as e:      # a read raced the game (object gone) or the match ended
                fails += 1
                err = f"{type(e).__name__}: {e}"      # (named `key` before: that replaced the key
                if err not in seen_errors:           # function, so after one read error every tick failed
                    seen_errors.add(err)             # and the brain stopped 3 s later)
                    import traceback
                    print(f"{now:6.1f}s tick failed ({err}):", traceback.format_exc(limit=4), flush=True)
                if fails > 90:
                    print('game gone, brain stopping:', e, flush=True)
                    break
                time.sleep(1 / HZ)
                continue
            fails = 0
    finally:
        save_learned(MAP)
        save_lidar()
        try:                                     # nodrive cells people drove over this match come out (tools/wedges.py)
            import wedges
            n = wedges.prune(MAP.get("base") or MAP["name"].partition("/")[0])
            if n:
                print(f"wedges: {n} nodrive cells people drove over taken out", flush=True)
        except Exception as e:
            print("wedges not pruned:", e, flush=True)
        if stations is not None:
            try:
                print("stations:", stations.save(), flush=True)
            except Exception as e:
                print("stations not saved:", e, flush=True)
        try:
            if oob.version != oob_saved[0]:
                print("out of bounds:", oob.save(), flush=True)
        except Exception as e:
            print("out of bounds not saved:", e, flush=True)
        if trace is not None:
            try:                                         # the whole match into its bug reports
                trace.close()                            # (a trim under way finished first)
                bug_reports.finish(o.trace, None)
            except Exception as e:
                print("bug report recordings:", e, flush=True)
        if collector:
            collector.close()
        for index, slot in slots.items():
            botctl.act(m, slot, 0, 0, last_yaw.get(index, 0.0), 0, 0)


if __name__ == "__main__":
    main()

