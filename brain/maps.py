"""Per-map knowledge, built from the map's real data:
  maps/<map>/objects.json   flags, teleporters, weapon/vehicle spawns, player spawns
                            (tools/scenario_extract.py from the CE kit's scenario tag)
  maps/<map>/callouts.json  named callout zones (docs/research/06 for where names come from)
  maps/<map>/heightmap.npz  level surface heights (tools/heightmap.py from the level mesh)
"""
import math
import os

from .core import Vec
from .callouts import ZoneMap, read_json

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "maps")
# the games whose nav has no drops into seams no body fits (Terrain slit_rule): Reach's 0.25 u cells (Countdown)
SLIT_GAMES = ("reach_",)


def _v(p):
    return Vec(p[0], p[1], p[2] if len(p) > 2 else 0.0)


SPOT_KINDS = {"koth_hill": "hills", "oddball_ball": "balls", "terr_object": "territories", "hh_drop_point": "drop_points",
              "inf_haven": "havens", "as_bomb": "bombs", "as_goal": "bomb_goals", "stp_flag": "stockpile_flags",
              "stp_goal": "stockpile_goals"}
H3_TYPES = {"oddball_ball_spawn_point": "oddball_ball", "koth_hill_static": "koth_hill", "territory_static": "terr_object"}


def load_map(name):
    """A map by name; a map variant ("<base>/variants/<id>", brain/variants.py) has its base map's level and scenario
    with its own navmesh data (its folder: lidar.json, learned spots, paint), its nav window, and its own callouts and
    power positions once made (tools/variant_strategy.py: the base's are a Forge canvas's, not the layout's)."""
    from . import variants
    from .variants import base_of, info
    d = os.path.join(ROOT, base_of(name))
    vd = os.path.join(variants.ROOT, name) if name != base_of(name) else d
    objs = read_json(os.path.join(d, "objects.json"))
    # (a built-in map's variant, Invasion: Spire's layout, a CTF variant: the base map's callouts and power positions,
    # its teams' own holds; the strategy made per layout gave both teams one set of holds: data_names)
    builtin = vd != d and bool(variants.base_area(objs))
    own = lambda fn: (os.path.join(vd, fn) if os.path.exists(os.path.join(vd, fn)) and not builtin
                      else os.path.join(d, fn))
    mv = os.path.join(vd, "mvar_objects.json") if vd != d else None
    if mv and os.path.exists(mv):       # its own .mvar file read (brain/mvar.py): its labelled objects, spawns, weapons
        mo = read_json(mv)              # and vehicles replace the scenario's; the scenario's CTF stands stay where the
        ctf = [f for f in objs["netgame_flags"] if f["type"] in ("ctf - flag", "CTF flag return")]   # file has none
        has = {f["id"] for f in mo["netgame_flags"] if f["type"] == "ctf - flag"} >= {0, 1}
        objs = dict(objs, netgame_flags=mo["netgame_flags"] + ([] if has else ctf), mvar=mo.get("source"),
                    **{k: mo[k] for k in ("spawns", "equipment", "vehicles") if mo.get(k)})
    for f in objs["netgame_flags"]:     # Halo 3's names for Reach's (Construct Team Oddball: no ball spots,
        f["type"] = H3_TYPES.get(f["type"], f["type"])    # so the ball was never found and the match ran as Slayer)
    flags = {f["id"]: _v(f["pos"]) for f in objs["netgame_flags"] if f["type"] == "ctf - flag"}
    spots = {t: [_v(f["pos"]) for f in objs["netgame_flags"] if f["type"] == "ctf - flag" and f["id"] == t] for t in (0, 1)}
    if len(spots[0]) > 1 or len(spots[1]) > 1:      # several setups (Halo 3): the farthest pair
        flags[0], flags[1] = max(((a, b) for a in spots[0] for b in spots[1]), key=lambda ab: (ab[0] - ab[1]).flat())
    # where each team captures (Halo 3's "CTF flag return": on most maps at its stand, on Sandtrap's static setup 0.6 u
    # off it; its Elephants' ride with them: tools/live_bots.py): the team's own return nearest its stand, at any
    # distance (Avalanche's sit 7.8 u off the stands on the floor below: live carriers stood 9 s on their own stand,
    # own flag home, and never scored) if it is nearer its stand than the other's (a team tag the other way round, as
    # on the Lockout remake's, is not trusted), else any return within 3 u of the stand
    rets = [(f.get("team", f.get("id")), _v(f["pos"])) for f in objs["netgame_flags"] if f["type"] == "CTF flag return"]
    returns = {}
    for t in flags:
        far = flags.get(1 - t)
        mine = [r for k, r in rets if k == t and (far is None or (r - flags[t]).flat() < (r - far).flat())]
        mine = mine or [r for k, r in rets if (r - flags[t]).flat() < 3.0]
        if mine:
            returns[t] = min(mine, key=lambda r: (r - flags[t]).flat())
    tele_from = {f["id"]: _v(f["pos"]) for f in objs["netgame_flags"] if f["type"] == "teleport from"}
    tele_to = {f["id"]: _v(f["pos"]) for f in objs["netgame_flags"] if f["type"] == "teleport to"}
    spawns = {"ctf": {0: [], 1: []}, "slayer": {0: [], 1: []}}
    for sp in objs["spawns"]:
        games = sp["games"] or ["all games"]   # no game types listed = every game (Halo 2 Relic, Warlock,
                                               # Sanctuary: all 121 spawns had an empty list, so no spawns at all)
        team = sp["team"] % 2           # extra spawn groups (Boarding Action has teams 2, 3) -> red/blue
        if "ctf" in games:
            spawns["ctf"][team].append(_v(sp["pos"]))
        if any(g.startswith("all") and g != "all except slayer" for g in games) or "slayer" in games:
            spawns["slayer"][team].append(_v(sp["pos"]))
    cfg = read_json(own("callouts.json"))
    teleporters = [(tele_from[k], tele_to[k]) for k in tele_from if k in tele_to]
    features = read_json(os.path.join(d, "features.json")) if os.path.exists(os.path.join(d, "features.json")) else {}
    for a, b in features.get("teleporters", []):   # placed teleporters the flags lack (H2A, H4: tools/map_features.py)
        a, b = _v(a), _v(b)
        if not any((a - x).flat() < 0.5 and (b - y).flat() < 0.5 for x, y in teleporters):
            teleporters.append((a, b))
    m = {
        "name": name,
        "flags": flags,
        "returns": returns,
        "teleporters": teleporters,
        "features": features.get("features", []),   # weapons, objective points, interactables (painted)
        "spawns": spawns,
        "zones": ZoneMap.load(own("callouts.json")),
        "bases": [(flags[t], cfg["base_radius"], cfg["roof_z"]) for t in (0, 1)],
        "heightmap": os.path.join(d, "heightmap.npz"),
        "objects": objs,
    }
    pts = {k: _v(v) for k, v in cfg["points"].items()}
    m["points"] = pts
    m["holds"] = {0: [_v(p) for p in cfg["holds"]["red"]], 1: [_v(p) for p in cfg["holds"]["blue"]]}
    m["lanes"] = [_v(p) for p in cfg["lanes"]]
    m["sneak"] = {0: [[_v(p) for p in r] for r in cfg.get("sneak", {}).get("red", [])],
                  1: [[_v(p) for p in r] for r in cfg.get("sneak", {}).get("blue", [])]}
    # objective spots by game type (Halo: Reach netgame flags, tools/reach_export.py): KotH hills, ball spawns,
    # territories, Headhunter drop points, Infection havens, Assault bombs / goals, Stockpile flags / goals
    m["spots"] = {}
    for f in objs["netgame_flags"]:
        if f["type"] in SPOT_KINDS:
            m["spots"].setdefault(SPOT_KINDS[f["type"]], []).append(_v(f["pos"]))
    m["items"] = power_items(objs, base_of(name).startswith("h3_"))
    pos = own("positions.json")                    # power positions (tools/power_positions.py)
    rows = read_json(pos) if os.path.exists(pos) else []
    m["positions"] = [_v(p["pos"]) for p in rows]
    # and why each one is: height over its surroundings, floor it sees, cover to duck behind (the commander)
    m["position_info"] = [dict(pos=_v(p["pos"]), height=p.get("height", 0.0), sees=p.get("sees", 0),
                               duck=bool(p.get("duck")), score=p.get("score", 0.0)) for p in rows]
    m["base"] = base_of(name)
    v = info(name)
    if v is not None:
        m["variant"], m["window"] = v, tuple(v["window"]) if v.get("window") else None
    return m


# Power items bots with map knowledge go for. Respawn: the scenario's own field where set
# (rockets 90 s, snipers 120 s); 0 means the game type default, assumed 60 s for powerups and
# 45 s for weapons (UNVERIFIED for CE, check in game).
POWER = {"rocket launcher": ("rockets", "weapon", 1.0), "sniper rifle": ("sniper", "weapon", 0.8),
         "powerup super shield": ("overshield", "powerup", 0.9), "shield-invisibility": ("camo", "powerup", 0.7),
         "powerup invisibility": ("camo", "powerup", 0.7),
         "plasma_cannon": ("fuelrod", "weapon", 0.6),
         # Halo 2 (item collection names)
         "rocket_launcher": ("rockets", "weapon", 1.0), "sniper_rifle": ("sniper", "weapon", 0.8),
         "energy_sword": ("sword", "weapon", 0.9), "beam_rifle": ("beam_rifle", "weapon", 0.6),
         "powerup_super_shield": ("overshield", "powerup", 0.9), "powerup_invisibility": ("camo", "powerup", 0.7),
         # Halo 3 (placement palette names)
         "spartan_laser": ("spartan_laser", "weapon", 0.9), "gravity_hammer": ("gravity_hammer", "weapon", 0.8),
         "missile_pod": ("missile_pod", "weapon", 0.5), "energy_blade": ("sword", "weapon", 0.9),
         # Halo: Reach (tools/reach_export.py writes the brain's weapon names)
         "rockets": ("rockets", "weapon", 1.0), "sniper": ("sniper", "weapon", 0.8), "sword": ("sword", "weapon", 0.9),
         "fuelrod": ("fuelrod", "weapon", 0.6), "plasma_launcher": ("plasma_launcher", "weapon", 0.6),
         # Halo 3 equipment pickups (one slot; guide 08 pickup priority: bubble, power drain,
         # regenerator, trip mine, grav lift, cover, flare and jammer), below the power weapons and
         # powerups (valued above the sniper, top bots ran the map for them: sim A/B)
         "bubbleshield_equipment": ("bubble_shield", "equipment", 0.55),
         "powerdrain_equipment": ("power_drain", "equipment", 0.5),
         "regenerator_equipment": ("regenerator", "equipment", 0.45),
         "tripmine_equipment": ("trip_mine", "equipment", 0.4), "gravlift_equipment": ("grav_lift", "equipment", 0.3),
         "superflare_equipment": ("flare", "equipment", 0.25), "jammer_equipment": ("radar_jammer", "equipment", 0.25),
         # H2A and Halo 4 powerups
         "h2a_overshield": ("overshield", "powerup", 0.9), "h2a_activecamo": ("camo", "powerup", 0.7),
         "h2a_speedboost": ("speed_boost", "powerup", 0.6), "ord_equip_overshield": ("overshield", "powerup", 0.9),
         "speedboost": ("speed_boost", "powerup", 0.6)}


def power_items(objs, h3=False):
    """h3: MCC Halo 3, where a weapon's timer starts 2 s after it is disturbed (map brain MAP-12; guide 21 Part 2 from
    the Steam Halo 3 spawn guide): its respawn is 2 s longer from the pickup."""
    out = []
    for e in objs["equipment"]:
        key = e["item"].split("\\")[-1]
        if key in POWER:
            name, kind, value = POWER[key]
            default = 60 if kind == "powerup" else 45
            out.append({"name": name, "kind": kind, "value": value, "pos": _v(e["pos"]),
                        "respawn": (e["spawn_time"] or default) + (2 if h3 and kind == "weapon" else 0)})
    return out


_MAPS = {}
_TERRAIN = {}


PVP = False     # tools/live_bots.py sets it: a fighting match, not a LiDAR or simulator run


def path_rises(zs, h=0.15):
    """How many times a path climbs h or more after a low point: a jump once, a drop none, a lift ride once; a person's
    hops in a row recorded as one jump (the tracker's landing needs 0.1 s on the ground and a bunny hop touches it for
    less) two and more. Spire: link 140, 3.5 s of hops read as a 7.5 u jump, held the core carrier at the plateau's
    lip; the player's match that day added 41 links, 2-9 s in the air and up to 17 u long, many such."""
    if not zs:
        return 0
    n, up, ext = 0, False, zs[0]
    for z in zs[1:]:
        if up:
            if z > ext:
                ext = z
            elif ext - z >= h:
                up, ext = False, z
        elif z < ext:
            ext = z
        elif z - ext >= h:
            up, ext, n = True, z, n + 1
    return n


LAUNCH_SNAP = 5.0    # u: a ride up starting this near a man cannon or gravity lift starts on it
LAUNCH_RISE = 10.0   # u: a link rising this much is a launch (no jump gets a person 10 u up)
PAD_TRIGGER = 1.2    # u: a ride people started this near a launch's marker starts at its trigger


def anchor_launches(links, features):
    """People's rides up (a link rising LAUNCH_RISE+) recorded from near a man cannon or gravity lift (features.json
    "launcher" / "lift") start at its trigger, marked a ride: LiDAR set the take-off where the person left the ground, a
    few units off when he jumped onto the pad (live Spire: one 4.1 u off the middle cannon, where three bots circled
    pressing jump; "Do these stupid fucks think the man cannon is there?"). The trigger is the ride people started
    nearest the marker, within PAD_TRIGGER of it: the marker itself sits up to 1.2 u into the cannon's body ("Right here
    where im standing, if I go forward it will shoot me up", 0.4 u from such a ride's start and 1.2 u from the marker; a
    bot dithered 2 u off the Elites' cannon). With none that near, the marker. Copies; the rest as they are."""
    pads = [f["pos"] for f in features if f.get("kind") in ("launcher", "lift")]
    if not pads:
        return list(links)
    flat = lambda a, b: math.hypot(a[0] - b[0], a[1] - b[1])
    rides = [k for k in links if k.get("take_off") and k.get("land") and k["land"][2] - k["take_off"][2] >= LAUNCH_RISE]
    trigger = {}
    for i, p in enumerate(pads):
        starts = [k["take_off"] for k in rides if flat(k["take_off"], p) < PAD_TRIGGER and abs(k["take_off"][2] - p[2]) < 3.0]
        trigger[i] = list(min(starts, key=lambda q: flat(q, p))[:3]) if starts else list(p[:3])
    out, ride_ids = [], {id(k) for k in rides}
    for k in links:
        to = k.get("take_off")
        if id(k) in ride_ids:
            i = min(range(len(pads)), key=lambda j: flat(pads[j], to))
            if flat(pads[i], to) < LAUNCH_SNAP and abs(pads[i][2] - to[2]) < 3.0:
                k = dict(k, take_off=trigger[i] + list(to[3:]), ride=1)
                k.pop("runup", None)
        out.append(k)
    return out


def usable_links(links, pvp=None):
    """LiDAR links bots may use: people's; jumps a bot called and landed on the call, in a match or in the
    simulator (tools/lidar_sim.py); others bots did twice. Not ones bots keep failing. In PvP matches
    (PVP) a big drop (2.5+ units down) only the simulator has landed waits until a bot has landed it
    once in a real game (LiDAR bots try them): the simulator's physics is close, not exact, and a
    missed drop is a death that gives the enemy a point. Small hops stay: the simulator only calls
    jumps whose small misses it survives, and gating them all cut Halo 3 Construct's levels apart
    (kills 4.9 -> 3.2 a minute)."""
    pvp = PVP if pvp is None else pvp

    def ok(k):
        if k.get("bot_fail", 0) >= 3 and not k.get("bot_ok", 0):
            return False
        if k.get("kind") not in ("crouch", "route") and path_rises([q[2] for q in k.get("path") or []]) >= 2:
            return False          # hops in a row recorded as one jump: not one a bot can make (hop_chain)
        if k.get("sim_fail", 0) >= 3 and k.get("sim_fail", 0) > 2 * k.get("sim_ok", 0) and not k.get("bot_ok", 0):
            return False
        if (pvp and not k.get("human", 0) and not k.get("bot_ok", 0)
                and k.get("take_off") and k.get("land") and k["take_off"][2] - k["land"][2] > 2.5):
            return False
        return bool(k.get("human", 0) or k.get("bot_ok", 0) >= 2
                    or ((k.get("called") or k.get("ride")) and (k.get("bot_ok", 0) or k.get("sim_ok", 0))))
    return [k for k in links if ok(k)]


def reviewed_moves(name):
    """maps/<map>/moves_review.json (tools/review_moves.py): people's moves off the nav, each reviewed:
    [{id, kind: "floor", ok, cells [[x, y, z]], why, by, at}]."""
    path = os.path.join(ROOT, name, "moves_review.json")
    return read_json(path).get("reviews", []) if os.path.exists(path) else []


def air_heights(hist):
    """(strafing, travelling) clearance over the floor from flown time per half-unit height (rides.json "h"): a quarter
    of the flying lower than the strafing height, three quarters lower than the travelling height; None under 30."""
    pts = sorted((float(b), n) for b, n in hist.items())
    tot = sum(n for _, n in pts)
    if tot < 30:
        return None

    def q(f):
        acc = 0
        for b, n in pts:
            acc += n
            if acc >= f * tot:
                return b
        return pts[-1][0]
    low = min(max(q(0.25), 2.0), 8.0)
    return low, min(max(q(0.75), low + 3.0), 30.0)


def rides_layers(names, T, files=None):
    """What people and bots drove and flew on these maps (maps/<name>/rides.json, tools/lidar.py RideWatch), for
    Terrain.add_rides: (drive cells [(x, y, z)], air {"low"|"high": {half-unit cell: floor z}}, heights (strafing,
    travelling) or None, marks {"takeoff"|"landing"|"turnback": [(x, y, z)]}). A drive cell counts once a person drove
    it or bots twice; heights come from people's flying (bots fly the heights they are given), else the bots'.
    Flown cells go to the layer their height is in (low under the middle of the two heights, high over it, both when
    flown at both)."""
    drive, hist, cells, marks = [], {"human": {}, "bot": {}}, {}, {"takeoff": [], "landing": [], "turnback": []}
    if T is not None:
        T.people_drive = []                      # (filled below, file by file: called again on every rides save)
    for f in files or [os.path.join(ROOT, nm, "rides.json") for nm in names]:
        d = read_json(f) if os.path.exists(f) else {}
        drive += [tuple(r[:3]) for r in d.get("drive", []) if r[3] >= 1 or r[4] >= 2]
        if T is not None:                         # people's own driving lines: the vehicles' roads (Terrain._build_vnav)
            T.people_drive = getattr(T, "people_drive", []) + [tuple(r[:3]) for r in d.get("drive", []) if r[3] >= 1]
        for r in d.get("air", []):
            c = cells.get((r[0], r[1]))
            cells[(r[0], r[1])] = list(r[2:]) if c is None else [c[0], c[1] + r[3], c[2] + r[4], min(c[3], r[5]),
                                                                   max(c[4], r[6])]
        for src in ("human", "bot"):
            for b, n in d.get("h", {}).get(src, {}).items():
                hist[src][b] = hist[src].get(b, 0) + n
        for k in marks:
            marks[k] += [tuple(r[:3]) for r in d.get(k, [])]
    heights = air_heights(hist["human"]) or air_heights(hist["bot"])
    mid = (heights[0] + heights[1]) / 2 if heights else 6.0
    air = {"low": {}, "high": {}}
    for c, (gz, sum_h, n, lo, hi) in cells.items():
        mean = sum_h / max(n, 1)
        if mean < mid or lo < mid:
            air["low"][c] = gz
        if mean >= mid or hi >= mid:
            air["high"][c] = gz
    return drive, air, heights, marks


def terrain(m, live_blocks=None):
    """The map's walkable terrain (brain/terrain.py), built once, with reachability from every spawn point so goals no
    player can reach snap to the nearest place one can. Floor people walked that the level data lacks joins it once
    reviewed and approved. live_blocks: solid objects read from the running game (brain/forge.py: a map variant's
    Forge pieces); given, the terrain is built again with them (kept in m, for growing). A map variant's terrain
    covers its window only (brain/variants.py), grown by grow and swap; m["shields"] (brain/forge.py shields) go
    onto every build."""
    if live_blocks is not None:
        _TERRAIN.pop(m["name"], None)
        m["live_blocks"] = live_blocks
    if m["name"] not in _TERRAIN:
        _TERRAIN[m["name"]] = _build(m, m.get("window"))
    return _TERRAIN[m["name"]]


def data_names(m):
    """Whose navmesh data a map's nav is built from: its own; a map variant of a built-in map (one whose scenario places
    things: Invasion: Spire's layout, a CTF variant) the base map's first, then its own. Live: Spire's Invasion layout,
    filed as a variant, had none of the base's LiDAR jumps, approved ramps and cover spots, so no attacker could get up
    to the core (four games) and the cover posts found no cover. A Forge canvas's variants (Forge World) keep their own
    navmesh, as the player asked ("paradiso is a separate navmesh")."""
    from .variants import base_of, base_area
    base = base_of(m["name"])
    if base != m["name"] and base_area(m.get("objects") or {}):
        return [base, m["name"]]
    return [m["name"]]


def people_walked(names, kinds=None):
    """Cells stood on, from the maps' LiDAR "walk" (0.25 u x 0.1 u keys): every record, or only those of the given kinds
    (tools/lidar.py KIND_*: 0 bot, 1 human, 3 crouched human...)."""
    out = set()
    for nm in names:
        lp = os.path.join(ROOT, nm, "lidar.json")
        if os.path.exists(lp):
            d = read_json(lp)
            c, zs = d.get("cell", 0.25), d.get("zstep", 0.1)
            out |= {(round(w[0] * c / 0.25), round(w[1] * c / 0.25), round(w[2] * zs / 0.1)) for w in d.get("walk", ())
                    if kinds is None or (w[3] if len(w) > 4 else 0) in kinds}
    return out


def _build(m, window):
    """A new terrain for map m over window (None: the whole level); terrain() keeps it, grow() makes one bigger."""
    from .terrain import Terrain
    names = data_names(m)
    floor = [c for nm in names for r in reviewed_moves(nm) if r.get("ok") and r.get("kind") == "floor" for c in r["cells"]]
    pdata = {}
    for nm in names:                     # painted by hand on the roofless view (tools/paint.py)
        pf = os.path.join(ROOT, nm, "paint.json")
        d = read_json(pf) if os.path.exists(pf) else {}
        for k, v in d.get("layers", {}).items():
            pdata.setdefault("layers", {}).setdefault(k, []).extend(v)
        pdata.setdefault("jumps", []).extend(d.get("jumps", []))
    paint = pdata.get("layers", {})
    # jumps the player drew on the painter's view (tools/paint.py "jumps"): people's jumps, one way, never
    # filtered
    painted = [dict(take_off=list(j[:3]), land=list(j[3:6]), kind="jump", painted=1, human=1, found_by="owner")
               for j in pdata.get("jumps", []) if len(j) == 6]
    # drops the player showed onto something that breaks the fall (maps/<map>/drops.json; Spire's heat vents): his
    # too, with a run-up (soft: onto a vent)
    for nm in names:
        df = os.path.join(ROOT, nm, "drops.json")
        painted += [dict(k, kind="jump", painted=1, human=1, found_by="owner", soft=1)
                    for k in (read_json(df).get("drops", []) if os.path.exists(df) else [])]
    floor = list(floor) + [tuple(c) for c in paint.get("walk", [])]
    from .placed import static_boxes             # placed crates, walls, rocks the level mesh lacks (brain/placed.py); a
    # map variant's are its own pieces (it may have moved or removed the scenario's), kept in its variant.json
    game = next((g for g in ("reach", "h4", "h2a") if m["name"].startswith(g + "_")), None)
    blocks = static_boxes(m["objects"], game) if game and "variant" not in m else []
    if "variant" in m and m.get("live_blocks") is None:
        from .forge import nav_pieces, piece_key
        from .variants import pieces_of
        # pieces seen to come and go (variant.json "dynamic", forge.PieceWatch): walls only while they stand
        dyn = set(m["variant"].get("dynamic", ()))
        m["live_blocks"], m["shields"] = nav_pieces([p for p in pieces_of(m["variant"]) if piece_key(p[0], p[1]) not in dyn],
                                                    m["variant"].get("game") or "reach")
    blocks = list(blocks) + list(m.get("live_blocks") or [])
    # cells people stood on (LiDAR "walk" of a person, standing or crouched: the bots' own records are no proof a body
    # fits; Spire's variant had bot records under the rock lip its attackers stalled at, S10)
    walked = people_walked(names, kinds=(1, 3))
    T = Terrain(m["heightmap"], m["bases"], extra_floor=floor, blocks=blocks, slit_rule=m["name"].startswith(SLIT_GAMES),
                stair_rule=m["name"].startswith(("h4_", "h2a_")),
                window=window, walked=walked)
    T.add_links(m["teleporters"])
    if not m["name"].startswith(("h2_", "h3_", "reach_", "odst_", "h2a_", "h4_")):  # jump links, for skilled bots (CE jump physics; ponytail:
        T.build_jumps()                  # Halo 2's jump is not measured yet, so no gap jumps there)
    data = {}
    for nm in names:                     # jumps people landed (tools/lidar.py), unless bots keep failing them
        lidar = os.path.join(ROOT, nm, "lidar.json")
        d = read_json(lidar) if os.path.exists(lidar) else {}
        for k in ("walk", "cover", "jumps", "links"):
            data.setdefault(k, []).extend(d.get(k, []))
    links = []
    if any(data.values()):
        from .terrain import crouch_key  # and floor people walked crouched: every bot crouches there
        T.crouch_cells = {crouch_key(x * 0.25, y * 0.25, z * 0.1) for x, y, z, k, n in data.get("walk", []) if k == 3}
        if data.get("cover"):                # floor in front of upright walls LiDAR painted (glass excluded)
            import numpy as np
            T.cover_spots = np.array(data["cover"], dtype=float)
        jumps = sorted(data.get("jumps", []))       # apexes LiDAR measured on this map
        T.jump_apex = jumps[len(jumps) // 2] if len(jumps) >= 20 else 0.8
        links = usable_links(data.get("links", []))
    links = anchor_launches(links + painted, m.get("features", []))
    if links:
        T.add_human_links(links)
    if paint:
        T.apply_paint(paint)             # blocked, drivable and flying cells (Terrain.apply_paint)
    # where bot drivers wedged, live (tools/wedges.py): the floor round them no person drove is left off the vehicles'
    # ground (Terrain._build_vnav; Spire's gap south of (-143, -354))
    wedge_files = [os.path.join(ROOT, nm, "wedges.json") for nm in names]
    T.nodrive = [c for f in wedge_files if os.path.exists(f) for c in read_json(f).get("nodrive", [])]
    drive, air, heights, marks = rides_layers(names, T)   # and what people and bots drove and flew (tools/lidar.py)
    if drive or air["low"] or air["high"] or heights or any(marks.values()):
        T.add_rides(drive, air, heights, marks)
    T.set_spawns(spawn_points(m))
    for it in m["items"]:                # an item on a rock top nobody can reach is not worth a trip
        it["reachable"] = T.inside(it["pos"].x, it["pos"].y) and bool(T.reach[T._node(it["pos"])])

    def snap(p):                         # a point no player can stand on (Halo 2 Lockout's mid at
        if not T.inside(p.x, p.y):       # (outside a variant's window: left as it is)
            return p
        l, j, i = T._node(p)             # z 0, in the air between its levels) -> nearest reachable floor
        return T._center(*T._reachable_node(l, j, i, p.z))
    m["points"] = {k: snap(p) for k, p in m["points"].items()}
    m["lanes"] = [snap(p) for p in m["lanes"]]
    m["holds"] = {t: [snap(p) for p in hs] for t, hs in m["holds"].items()}
    for z in m["zones"].zones:           # zone centres at the real floor height (callout spots),
        if T.inside(z.center.x, z.center.y):
            c = Vec(z.center.x, z.center.y, T.floor_in_band(z.center.x, z.center.y, z.zmin, z.zmax))
            z.center = snap(c)           # and on ground players reach (Sidewinder's Mid sat in rock)
    T.people_walked = people_walked(names)   # cells anyone stood on: never a learned wall (Terrain.learn_stuck)
    for nm in names:                     # (a built-in map's variant: the base's learned spots too)
        learned = os.path.join(ROOT, nm, "los_learned.json")
        if os.path.exists(learned):
            T.blocked = set(getattr(T, "blocked", set())) | {tuple(k) for k in read_json(learned)}
        learned = os.path.join(ROOT, nm, "nav_learned.json")
        if os.path.exists(learned):
            for x, y, z in read_json(learned):
                T.learn_stuck(Vec(x, y, z), sure=True)
        traps = os.path.join(ROOT, nm, "trapped_walls.json")      # walls bots were trapped at (Bot.mark_trap): kept with
        if os.path.exists(traps):                                 # their counts, strikes and expiry (Terrain.load_traps)
            T.load_traps(traps)
    T._traps_loaded = True
    kinds = {}                           # windows and fences (tools/lidar_walls.py): walls you see through, fences you
    for nm in names:                     # shoot through too (Terrain.set_wall_kinds, sees, shot_los)
        wk = os.path.join(ROOT, nm, "wall_kinds.json")
        if os.path.exists(wk):
            kinds = read_json(wk)
            break
    from .terrain import piece_wall_kinds   # and placed pieces' own (a Forge railing, glass, an energy pane)
    pk = piece_wall_kinds(blocks)
    learned = []                         # and spots rounds went through (Terrain.learn_shot_through): fences
    for nm in names:
        lf = os.path.join(ROOT, nm, "walls_learned.json")
        learned += read_json(lf) if os.path.exists(lf) else []
    pk["fence"] += [c[:4] for c in learned if not c[4]]
    pk["fence_floor"] += [c[:4] for c in learned if c[4]]
    if any(pk.values()):
        kinds = dict(kinds, **{k: list(kinds.get(k, [])) + v for k, v in pk.items()})
    if any(kinds.get(k) for k in ("window", "fence", "window_floor", "fence_floor")):
        T.set_wall_kinds(kinds)
    T.walls_learned = [list(c) for c in learned]
    if m.get("shields"):                 # Forge shields (brain/forge.py): sight-line stops and one-way barriers
        from .forge import apply_shields
        apply_shields(T, m["shields"])
    return T


def spawn_points(m):
    """Where players start: a map variant's own spawn objects (named *spawn*; Forge World's scenario spawns are not
    its), else the map's spawns."""
    own = [_v(o[1:4]) for o in (m.get("variant") or {}).get("objects", []) if "spawn" in o[0]]
    return own or [p for mode in m["spawns"].values() for team in mode.values() for p in team]


def grow(m, positions):
    """Grow a map variant's nav window when a player nears an edge the level goes on past: the bigger terrain is built
    in the background, swap puts it in. Returns the new window, or None (no variant, nothing to grow, a build already
    running)."""
    g = m.setdefault("grow", {})
    T = _TERRAIN.get(m["name"])
    if m.get("window") is None or T is None or g.get("job") is not None or g.get("ready"):
        return None
    from .variants import grown
    win = tuple(m["window"])
    for p in positions:
        win = grown(win, p, T.full) or win
    if win == tuple(m["window"]):
        return None
    import threading

    def job():
        try:
            g["ready"] = (win, _build(m, win))
        finally:
            g["job"] = None
    g["job"] = threading.Thread(target=job, daemon=True)
    g["job"].start()
    return win


def swap(m):
    """Put a grown terrain (grow) in the place of the one in use, in the same object, so bots, directors and LiDAR keep
    theirs; what they learned meanwhile comes along; the window is kept for the variant's next match. Call between
    ticks. True when it swapped."""
    ready = (m.get("grow") or {}).pop("ready", None)
    if not ready:
        return False
    win, new = ready
    T = _TERRAIN.get(m["name"])
    if T is not None:
        new.blocked |= T.blocked
        for k, mt in getattr(T, "trapped", {}).items():      # trapped walls keep being walls, with their counts
            new.learn_trap(Vec(*k), meta=mt)
        new.trap_gone = set(getattr(T, "trap_gone", ()))
        for k in T.stuck - new.stuck:
            new.learn_stuck(Vec(*k), sure=True)
        T.__dict__.clear()
        T.__dict__.update(new.__dict__)
    else:
        _TERRAIN[m["name"]] = new
    m["window"] = win
    from .variants import save_window
    save_window(m["name"], win)
    return True


def save_learned(m):
    """Write what bots learned the level data misses to the map folder, so the next match starts
    knowing it: blocked sight lines (Terrain.learn_blocked), spots they could not walk through
    (Terrain.learn_stuck), spots rounds went through (Terrain.learn_shot_through)."""
    T = _TERRAIN.get(m["name"])
    if T is None:
        return
    import json
    # trapped walls live in trapped_walls.json (Bot.save_traps), not here: kept in this file they came back as plain
    # stuck spots, and a spot forgotten (bots ran through it) came back from it
    for name, data in (("los_learned.json", T.blocked), ("nav_learned.json", T.stuck - set(getattr(T, "trapped", {}))),
                       ("walls_learned.json", [tuple(c) for c in getattr(T, "walls_learned", [])])):
        if data or (name == "nav_learned.json" and os.path.exists(os.path.join(ROOT, m["name"], name))):
            tmp = os.path.join(ROOT, m["name"], name + ".tmp")
            json.dump(sorted(data), open(tmp, "w"))
            os.replace(tmp, tmp[:-4])


def get_map(name):
    """Map data by name (the scenario's file name, e.g. bloodgulch), loaded once."""
    if name not in _MAPS:
        _MAPS[name] = load_map(name)
    return _MAPS[name]


BLOODGULCH = get_map("bloodgulch")
