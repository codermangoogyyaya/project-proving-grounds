"""The objective game types beyond Slayer, CTF, King, Oddball and Invasion.

Halo: Reach first (every game type runs on its Megalo engine; the variant's name says which, its engine icon only
for the ones read live: CTF 0, Oddball 2, King 3, Invasion 9). Each mode's places come from the map's scenario
(maps/<map>/objects.json netgame flags with their boundaries, tools/reach_export.py):
  territories   terr_object zones: take the ones not ours, hold the ones we have
  assault       as_bomb (the bomb's stand: one neutral, or one per team), as_goal (a team's goal: the enemy plants
                there): carry the bomb to their goal and stand on it; defend ours
  stockpile     stp_flag (neutral flags), stp_goal (a team's goal): carry flags home, keep them there
  headhunter    hh_drop_point zones: bank the skulls picked up from kills
  infection     inf_haven zones: survivors hold a haven together, the infected hunt the nearest survivor
  juggernaut, vip  played as Slayer (who the juggernaut or VIP is is not read yet)
Objects (the bomb, stockpile flags) are found the way CTF flags are (brain/live.py flags_by_stand: the weapon object
first seen on its stand). Zone ownership in Territories is not read from the game: a team that held a zone alone for
the game's CAPTURE_S owns it until the other team does the same (ponytail: the game's own capture state, once decoded).
"""
import math

from .core import Vec, dist
from .dom_base import DOM_BASE_RADIUS, DOM_BASE_DZ, DOM_BASE_CAPTURE_S

# the objective modes past CTF, King and Oddball: each has its own team plan in the director (mode_slots,
# invasion_slots) and its own objective points (Director.objective_points)
NEW_MODES = ("invasion", "territories", "assault", "stockpile", "headhunter", "infection")

NAMES = (("territories", ("TERRITOR", "LAND GRAB", "PLOTS")),("assault", ("ASSAULT", "BOMB", "NEUTRAL BOMB")),
         ("stockpile", ("STOCKPILE",)), ("headhunter", ("HEADHUNTER",)),
         ("infection", ("INFECTION", "SAFE HAVEN", "ALPHA ZOMBIE", "ZOMBIE", "FLOOD")),   # (Flood: Halo 4's)
         ("juggernaut", ("JUGGERNAUT",)), ("vip", ("VIP",)),
         # Halo 4 and H2A: played as the mode they are like (ALIAS), on their own places (KINDS)
         ("ricochet", ("RICOCHET",)), ("grifball", ("GRIFBALL",)), ("dominion", ("DOMINION",)),
         # Reach's Rocket Race ("$hr_gvar_Rocket_Hog_Race" in MCC's file: before Race), Race, Halo 4's Regicide
         ("rally", ("ROCKET", "RALLY")), ("race", ("RACE",)), ("regicide", ("REGICIDE",)))
# a mode played as another, on its own netgame flags: Ricochet carries the ball to their endzone (Assault's carry to the
# goal; the ball's thrown score not played), Grifball the bomb to their goal, Dominion holds bases as territories
# (their fortifying not played)
ALIAS = {"ricochet": "assault", "grifball": "assault", "dominion": "territories", "rally": "race"}
KINDS = {"ricochet": {"rico_ballspawn": "bomb", "rico_endzone": "bomb_goal"},
         "grifball": {"grif_spawn": "bomb", "grif_goal": "bomb_goal"},
         "dominion": {"dom_base": "dom_base", "dom_base_zone": "territory", "dom_shield": "dom_shield",
                      "dom_turret": "dom_turret", "dom_fortify": "dom_fortify"}}
# every game's names for the places the modes use (Reach's, and Halo 2's: its Assault plates are each team's "bomb
# return" by its own base, its bombs' stands "bomb spawn" per team and one neutral; maps/h2_*/objects.json)
PLACES = {"terr_object": "territory", "as_bomb": "bomb", "as_goal": "bomb_goal", "stp_flag": "stockpile_flag",
          "stp_goal": "stockpile_goal", "hh_drop_point": "drop_point", "inf_haven": "haven",
          "Territories flag": "territory", "Assault bomb spawn": "bomb", "Assault bomb return": "bomb_goal"}
# Halo 3 names no variant in memory read so far: its game engine number says the mode (each read live on Construct:
# 1 CTF, 2 Slayer, 3 Oddball, 4 King, 5 Juggernaut, 6 Territories, 7 Assault, 8 Infection, 9 VIP)
H3_ENGINES = {5: "juggernaut", 6: "territories", 7: "assault", 8: "infection", 9: "vip"}
# H2A by its variant's engine icon where the name says nothing (live, Lockdown: Territories' built-in "3 Plots", icon
# 8, read as Slayer by its name)
H2A_ICONS = {8: "territories"}
# Reach by its engine icon where the name says nothing (a localized or custom name): MCC's own race_054.bin is icon 13
# ("RACE"), rocket_race_hog_054.bin 14 (brain/mvar.py read_game_variant); ReachVariantTool's icon list names them race,
# rocket_race (Invasion's 9 there matched the live read). In memory both built-in Race and Rally read 12 (live Boneyard,
# Nov 4): 12 is played as Race; Rally is told by its name
REACH_ICONS = {12: "race"}
# held alone this long, a zone counts as that team's (Territories): the game's capture time (guide 19 6.1 and
# TERR-SYM-02: Halo 2 contest 5 s then control 5 s, Halo 3 and Reach 30 s), so its takers stay in it until it is theirs
CAPTURE_S = {"h2": 10.0, "h3": 30.0, "reach": 30.0}
OBJECT_MODES = ("territories", "assault", "stockpile", "headhunter", "infection", "juggernaut", "vip",
                "ricochet", "grifball", "dominion", "race", "rally", "regicide")   # (juggernaut and vip:
# Watch.mark_special; without them here Halo 3 Juggernaut played as Slayer, live)


def mode_of(game, live):
    """The objective mode a running game is in for tools/live_bots.py, or None (Slayer, CTF, King, Oddball, Invasion are
    read elsewhere): Halo 3 by its engine number, Halo 2 by its variant's own name (its Territories, Assault and
    Juggernaut played as Slayer before), the others by the variant's name, H2A by its engine icon where the name says
    nothing. A failed read is None (played as Slayer) and never stops the brain's tick."""
    try:
        if game == "h3":
            return H3_ENGINES.get(live.engine())
        if game == "ce":                   # CE's engine index: 5 is Race (halo-64 game_engine.h; MCC's 32_race.bin +0x30)
            return "race" if live.engine() == 5 else None
        mode = detect(live.variant()["name"] if game == "h2" else live.variant_name())
        if mode is None and game in ("h2a", "reach"):
            mode = (H2A_ICONS if game == "h2a" else REACH_ICONS).get(live.engine_icon())
        if mode is None and getattr(live, "zombie_seen", False):
            mode = "infection"       # an infected-only gun in a hand (live.ZOMBIE_TAGS): Halo 4 Flood "read off as
        return mode                  # Slayer" (owner, issue #3), whatever its variant is called
    except Exception:
        return None


def detect(variant_name):
    """The mode a Reach variant's name says, or None (Slayer, CTF, King, Oddball, Invasion are read elsewhere)."""
    name = (variant_name or "").upper()
    if "INVASION" in name:
        return None
    return next((mode for mode, words in NAMES if any(w in name for w in words)), None)


def _v(p):
    return Vec(p[0], p[1], p[2])


def radius(f, default=3.0):
    """A netgame flag's zone size from its boundary: a cylinder's radius, half a box's short side."""
    b = f.get("boundary") or {}
    r = b.get("w", 0) if b.get("shape") == "cylinder" else min(b.get("w", 0), b.get("l", 0) or b.get("w", 0)) / 2
    return max(0.6, min(8.0, r)) if r else default


def layout(objs, mode=None):
    """Every mode's places on this map: {kind: [(Vec, radius, team)]}; a mode in KINDS: its own places only."""
    out = {}
    places = KINDS.get(mode, PLACES)
    for f in objs.get("netgame_flags", []):
        kind = places.get(f["type"]) or places.get(str(f["type"]).strip().lower())
        if kind:
            out.setdefault(kind, []).append((_v(f["pos"]), radius(f), f.get("team", 8)))
    if "bomb_goal" not in out:          # Halo 3: the bomb is armed on the CTF flag return areas (Construct: no
        out["bomb_goal"] = [(_v(f["pos"]), radius(f), f.get("team", 8))   # other goal object on the map)
                            for f in objs.get("netgame_flags", []) if f["type"] == "CTF flag return"]
        out["ctf_stand"] = [(_v(f["pos"]), 1.0, f.get("team", 8))
                            for f in objs.get("netgame_flags", []) if f["type"] == "ctf - flag"]
    return out


def race_gates(objs, kind="race"):
    """A race's gates in the order they are driven, [(Vec, radius, up, down)] (radius: the touch's on foot, a vehicle's
    is RIDE_R). CE's "race - track" flags by their index (the flag's team index: the game's lap bits, halo-64
    game_engine_race.c); a Reach race_flag by its spawn order (seq: maps/<map>/source/scenario.xml, 1..N, an even loop on
    all 7 maps) and its cylinder; rally (Rocket Race): its rally_flags, in no order (the game picks one at a time)."""
    fl = objs.get("netgame_flags", [])
    if kind == "rally":
        return [(_v(f["pos"]), radius(f, 2.0), 1.0, 1.0) for f in fl if f["type"] == "rally_flag"]
    ce = sorted((f for f in fl if f["type"] == "race - track"), key=lambda f: f["id"])
    if ce:                                 # (find_netgame_flag on foot: 1.5 u across, 0.6 up)
        return [(_v(f["pos"]), CE_FOOT_R, CE_FOOT_DZ, CE_FOOT_DZ) for f in ce]
    reach = sorted((f for f in fl if f["type"] == "race_flag"), key=lambda f: f.get("seq", 0))
    return [(_v(f["pos"]), radius(f, 2.0), (f.get("boundary") or {}).get("up", 1.0),
             (f.get("boundary") or {}).get("down", 1.0)) for f in reach]


# CE's touch of a track flag (halo-64 game_engine_race.c race_engine_player_update): within 1.5 u (0.6 up or down) on
# foot, 2.5 u of the vehicle's centre riding. The brain counts a gate only well inside the game's (TOUCH_K): a gate it
# counts that the game did not would send it on to the next one, which the game then never takes (Race in order)
CE_FOOT_R, CE_FOOT_DZ, CE_RIDE_R, TOUCH_K = 1.5, 0.6, 2.5, 0.7


class Race:
    """Every player's progress round a race's gates, by CE's own rules (halo-64 game_engine_race.c can_touch_team /
    race_touch_flag): kind "normal" in index order, a lap on touching the first gate again once all are touched; "any"
    any untouched gate (the first one touched is the lap's end; RACE-02's tour: the nearest one next); "sweep" (the
    game's one live gate is not read: CE's Flag Rally, Reach's Rocket Race before its destination is read) every gate
    nearest first, all over again once all are seen; "dest" one gate the game named (dest). Reach's Race is played as
    "normal" over its race_flags in spawn order (INFERRED: its script's "next gate" is not decoded)."""

    def __init__(s, gates, kind="normal", ce=False, ride_only=False):
        # ride_only: a gate counts only from a vehicle (Reach: live Boneyard Race, the two bots whose first gate the
        # brain counted on foot scored 0 while the rest scored about one a gate, and its script warns "On foot for too
        # long!")
        s.gates, s.kind, s.ce, s.ride_only = gates, kind, ce, ride_only
        s.got, s.first, s.laps, s.dest = {}, {}, {}, None

    def inside(s, k, pos, riding):
        g, r, up, down = s.gates[k]
        if riding and s.ce:
            # CE riding: the vehicle's centre within 2.5 u in 3D (find_netgame_flag: a sphere; no height test). A flat
            # ring with 2 u of height counted a Ghost the game did not, 1.7 u across and 1.9 u under a gate on a ledge
            return dist(pos, g) <= TOUCH_K * CE_RIDE_R
        return (pos - g).flat() <= TOUCH_K * r and -down <= pos.z - g.z <= up

    def can_touch(s, pid, k):
        got, n = s.got.get(pid, set()), len(s.gates)
        if s.kind == "dest":
            return k == s.dest
        if s.kind == "sweep":
            return True
        if len(got) == n:
            return k == s.first.get(pid, 0)
        if k in got:
            return False
        return k == min(set(range(n)) - got) if s.kind == "normal" else True

    def touch(s, pid, k):
        if not s.can_touch(pid, k):
            return False
        got = s.got.setdefault(pid, set())
        if s.kind == "sweep":
            got.add(k)
            if len(got) == len(s.gates):
                got.clear()
            return True
        s.first.setdefault(pid, k)
        if s.kind == "dest" or len(got) == len(s.gates):
            s.laps[pid] = s.laps.get(pid, 0) + 1
            got.clear()
        else:
            got.add(k)
        return True

    def step(s, players):
        """players: [(id, Vec, alive, riding)]: each one's touches this tick."""
        for pid, pos, alive, riding in players:
            if alive and (riding or not s.ride_only):
                for k in range(len(s.gates)):
                    if s.inside(k, pos, riding) and s.touch(pid, k):
                        break

    def next_gate(s, pid, at=None):
        """The gate this player goes for next (its index)."""
        if not s.gates:
            return None
        if s.kind == "dest" and s.dest is not None:
            return s.dest
        got, n = s.got.get(pid, set()), len(s.gates)
        if s.kind not in ("sweep", "dest") and len(got) == n:
            return s.first.get(pid, 0)
        todo = [k for k in range(n) if k not in got] or list(range(n))
        if s.kind == "normal" or at is None:
            return todo[0]
        return min(todo, key=lambda k: dist(s.gates[k][0], at))


def kill_credit(victim, was, players, reach=60.0, cone=0.15):
    """Who killed victim (its id), dead since the last tick at was (Vec), by what everyone else was doing: the living man
    within reach whose aim (yaw, pitch) points at him (within cone rad), or one within 2.5 u (a melee), the nearest
    such; None when nobody (a grenade, a fall). ponytail: a guess from aim, the game's own kill feed is not read."""
    best = None
    for p in players:
        if p.id == victim or not p.alive:
            continue
        d = dist(p.pos, was)
        if d > reach:
            continue
        yaw, pitch = math.atan2(was.y - p.pos.y, was.x - p.pos.x), math.atan2(was.z - p.pos.z, max(1e-6, (was - p.pos).flat()))
        off = math.hypot(math.atan2(math.sin(yaw - p.yaw), math.cos(yaw - p.yaw)), pitch - p.pitch)
        if (off <= cone or d < 2.5) and (best is None or d < best[0]):
            best = (d, p.id)
    return best[1] if best else None


# ---- Dominion (Halo 4): its places are read by LABEL only (the netgame flag's type in objects.json), so a custom map
# works the same as a built-in one. dom_base: the terminal a man captures by standing at it and holding interact;
# dom_base_zone (the capture area; the terminals' stand-in when a map has no dom_base); dom_turret, dom_shield: what a
# fortified base spawns (dom_fortify marks where); a team's own are left alone, the other team's shot down first.
DOM_USE_R = 1.0        # u from the terminal: close enough to use it (the bot stands right next to it, still)
DOM_HOLD_S = DOM_BASE_CAPTURE_S   # seconds a base takes to capture (brain/dom_base.py: 5 s; the owner estimate below flips
                                  # after it; the bot's own hold, dom_base.DOM_BASE_HOLD_S, is longer)
DOM_BASE_R = DOM_BASE_RADIUS   # u: a man this close to the terminal (DOM_BASE_DZ up or down) is capturing it (dom_base.py)
DOM_FORT_R = 14.0      # a turret or shield this close to a base belongs to that base (its owner's)
DOM_DEF_SHARE = 0.35   # of a team's bots, at most this many defend (the rest attack: more attack than defend)
DOM_DEF_PER_BASE = 2   # and no more than this many to a base
FORT_NAMES = (("dom_turret", ("dom_turret",)), ("dom_shield", ("dom_shield", "dom_damage_team_shield")))


def _fort_hostile(forts, team, base, r=DOM_FORT_R):
    """The other team's turrets and shields within r of a base, [(kind, Vec)]: turrets first (they shoot), nearest first."""
    out = [(k, p) for k, p, t in forts if t in (0, 1) and t != team and (p - base).flat() <= r]
    return sorted(out, key=lambda kp: (kp[0] != "dom_turret", (kp[1] - base).flat()))


def dominion_orders(zones, players, team, prev=None, forts=()):
    """What each of a team's bots does in Dominion: {bot id: {kind, zone, pos, hold, clear}}.
    zones: [(Vec, radius, owner team or -1)] (snap.obj_zones); forts: [(kind, Vec, team)] turrets and shields.
      capture: go right next to the base (pos), hold interact `hold` seconds; `clear`: the other team's turrets and
               shields at that base, to shoot down before going in (empty: go in)
      defend:  stay at one of our bases (pos), turn on anyone who comes to it
    Some defend and more attack. We hold every base: all defend; they hold every base: all attack. A bot keeps its job
    while it is still valid (prev), so the split does not shuffle each tick."""
    bots = [p for p in players if p.alive and p.team == team and getattr(p, "is_bot", True)]
    if not bots or not zones:
        return {}
    prev = prev or {}
    mine = [k for k, z in enumerate(zones) if z[2] == team]
    theirs = [k for k in range(len(zones)) if k not in mine]
    n = len(bots)
    n_def = n if not theirs else 0 if not mine else min(DOM_DEF_PER_BASE * len(mine), int(n * DOM_DEF_SHARE), (n - 1) // 2)
    out, left = {}, list(bots)

    def order(p, kind, k):
        pos = zones[k][0]
        out[p.id] = dict(kind=kind, zone=k, pos=pos, hold=DOM_HOLD_S if kind == "capture" else 0.0,
                         clear=_fort_hostile(forts, team, pos) if kind == "capture" else [])
        left.remove(p)

    # defenders: who defended and still can, then the ones nearest our bases; each to the base with fewest of ours
    if n_def:
        keep = [p for p in bots if prev.get(p.id, {}).get("kind") == "defend" and prev[p.id]["zone"] in mine][:n_def]
        rest = sorted((p for p in bots if p not in keep),
                      key=lambda p: min((p.pos - zones[k][0]).flat() for k in mine))
        count = {k: 0 for k in mine}
        for p in keep + rest[:n_def - len(keep)]:
            def threat(k):
                return sum(1 for q in players if q.alive and q.team != team and (q.pos - zones[k][0]).flat() < 10.0)
            k = prev[p.id]["zone"] if p in keep else min(mine, key=lambda k: (count[k], -threat(k), (p.pos - zones[k][0]).flat()))
            count[k] += 1
            order(p, "defend", k)
    # attackers: the rest, spread over the bases that are not ours, nearest first, no base over its share
    if left and theirs:
        cap = -(-len(left) // len(theirs))
        load = {k: 0 for k in theirs}
        for p in [p for p in left if prev.get(p.id, {}).get("kind") == "capture" and prev[p.id]["zone"] in load]:
            k = prev[p.id]["zone"]
            if load[k] < cap:
                load[k] += 1
                order(p, "capture", k)
        for p in sorted(left, key=lambda p: min((p.pos - zones[k][0]).flat() for k in theirs)):
            open_ = [k for k in theirs if load[k] < cap] or theirs
            k = min(open_, key=lambda k: (p.pos - zones[k][0]).flat())
            load[k] += 1
            order(p, "capture", k)
    for p in list(left):                  # nothing to attack and nobody needed to defend: hold the nearest base of ours
        if mine:
            order(p, "defend", min(mine, key=lambda k: (p.pos - zones[k][0]).flat()))
    return out


class Watch:
    """The live state of one of OBJECT_MODES for tools/live_bots.py: fills the snapshot's obj_ fields every tick."""

    def __init__(s, mode, objs, game="reach"):
        s.kind, s.mode, s.lay = mode, ALIAS.get(mode, mode), layout(objs, mode)   # (kind: the mode read, mode: as played)
        s.capture = CAPTURE_S.get(game, 30.0)     # (tools/live_bots.py names none: Reach's, whose territories it reads)
        if mode == "dominion":
            s.capture = DOM_HOLD_S                # held interact 3 s takes a base
            s.dom_prev, s.forts, s.forts_t = {}, [], -9.0
        s.owner, s.held = {}, {}          # territories: zone index -> owner team; -> (team, since) while held alone
        s.items_t, s.items = -9.0, []
        s.stands, s.stands_t = [], -9.0
        s.zombie_tag, s.infected_ids = None, {}    # Halo 3 Infection (split_infected)
        s.objs, s.game, s.race, s.race_score = objs, game, None, None   # Race (race_update)

    def rally_dest(s, live, gates):
        """Rally's / Rocket Race's destination (the game moves one live rally flag), the gate LiveReach.rally_dest names
        (live Boneyard Rally: the HUD's flag; the King hill handle beside it held the last destination), or None when
        that read names no rally flag."""
        try:
            h = live.rally_dest() if hasattr(live, "rally_dest") else None
        except Exception:
            h = None
        if not h or not gates:
            return None
        q = Vec(*h)
        k = min(range(len(gates)), key=lambda k: (gates[k][0] - q).flat())
        return k if (gates[k][0] - q).flat() < 1.0 else None

    def race_update(s, live, snap):
        """Race and Rocket Race: each player's next gate (snap.race_next) by the game's rules (Race), the gates as
        zones (snap.obj_zones). CE by its variant's race type (Live.race_type: normal, any order, flag rally)."""
        if s.race is None:
            kind = "sweep" if s.kind == "rally" else "normal"
            if s.game == "ce" and hasattr(live, "race_type"):
                try:
                    kind = {0: "normal", 1: "any", 2: "sweep"}.get(live.race_type(), "normal")
                except Exception:
                    pass
            s.race = Race(race_gates(s.objs, s.kind), kind, ce=s.game == "ce", ride_only=s.game == "reach")
        r = s.race
        if s.kind == "rally":
            dest = s.rally_dest(live, r.gates)
            r.kind, r.dest = ("dest", dest) if dest is not None else ("sweep", None)
            score = tuple(sorted((snap.score or {}).items()))
            if score != s.race_score:     # somebody scored: the destination moved ("Destination Moved."), look again
                s.race_score = score
                for got in r.got.values():
                    got.clear()
        r.step([(p.id, p.pos, p.alive, p.vehicle not in (None, -1)) for p in snap.players])
        snap.obj_zones = [(g, rad, -1) for g, rad, _, _ in r.gates]
        snap.race_next = {p.id: r.gates[k][0] for p in snap.players for k in (r.next_gate(p.id, p.pos),) if k is not None}

    def mark_king(s, snap):
        """Regicide (Halo 4 free for all, research 19 SLAY-REG-01: the leader is the King, with a bounty): the King in
        snap.vips, as a VIP (every bot's +40 target, the King holds back from far fights, his director gives him his own
        play): the most points the brain saw, each death credited by kill_credit (ponytail: the game's per-player
        points are not read in Halo 4, not found in the players' records or the TLS blocks live; a reader replaces it)."""
        # the points as the game gives them (live Adrift, Nov 4, against the scoreboard): a kill 10, the King's killer
        # his bounty besides, 20 and 5 more for each kill the King made as King (HUD "BOUNTY +20", then +35 for a King
        # 3 kills in; Owl864 2 kills then the King: 50). No Overshield was seen on any King (0 rows over 1.05 in 3 min)
        prev, kills = s.__dict__.setdefault("alive_at", {}), s.__dict__.setdefault("kills", {})
        king = getattr(s, "king", None)
        for p in snap.players:
            if not p.alive and p.id in prev:
                k = kill_credit(p.id, prev[p.id], snap.players)
                if k is None:
                    continue
                kills[k] = kills.get(k, 0) + 10
                if p.id == king and k != king:
                    kills[k] += 20 + 5 * getattr(s, "reign_kills", 0)
                elif k == king:
                    s.reign_kills = getattr(s, "reign_kills", 0) + 1
        s.alive_at = {p.id: p.pos for p in snap.players if p.alive}
        if kills:
            top = max(kills.values())
            new = king if kills.get(king) == top else min(k for k, v in kills.items() if v == top)
            if new != king:
                s.reign_kills = 0
            king = new
        s.king = king if any(p.id == king for p in snap.players) else None
        snap.vips = (s.king,) if s.king is not None else ()

    def live_stands(s, live, now):
        """Halo 3 Assault: the map export lists no bomb spawn, so the bombs' stands are read from the game once the bombs
        lie on them (live.item_stands); a stand on a CTF flag spot is that team's, else neutral."""
        if not s.stands and now - s.stands_t > 5.0:
            s.stands_t = now
            try:
                for p in live.item_stands():
                    p = _v(p)
                    team = next((t for q, _, t in s.lay.get("ctf_stand", []) if dist(p, q) < 1.0), 8)
                    s.stands.append((p, team))
            except Exception:
                s.stands = []
        return s.stands

    def split_infected(s, snap, raw):
        """Halo 3 Infection is a free for all to the game (each player his own team): the infected are told by their
        gun. The zombies all hold one kind nobody else holds, the kind the fewest hold at the start (Save One Bullet,
        Construct: one zombie's apart from nine survivors' shotguns); a survivor who dies comes back infected. The
        snapshot's teams become 0 survivors, 1 infected."""
        if len({p.team for p in snap.players}) < len(snap.players):
            return                          # the game's own teams (Reach): zombie_team() reads the swords
        tags = {d["index"]: d.get("wtag") for d in raw if d.get("pos") is not None and d.get("wtag")}
        named = {p.id: p.weapon for p in snap.players if p.alive and p.weapon not in (None, "", "none", "unknown")}
        if "sword" in named.values():
            # a sword in a hand by name (every game names it now; Halo 3 by the map's tag names): the name decides from
            # here on, not the raw tag. Live Rat's Nest, the owner's game: round 2's three zombies held a sword whose
            # tag was not round 1's zombie tag, so all 16 read survivors and the zombies stood in the holds with them
            # (his "confused on their teams", "something is broken here")
            s.by_name = True
        if getattr(s, "by_name", False):
            tags = {}
        if not tags:
            # (Halo 4 Flood, H2A, Reach in a free for all: their readers carry no raw gun tag; the gun's name tells the
            # same, the infected's prongs or sword read "sword". Before, nothing split: every survivor fought every
            # other, owner, issue #3)
            tags = {p.id: p.weapon for p in snap.players if p.alive and p.weapon not in (None, "", "none", "unknown")}
            # by name the zombies' kind is their sword: the fewest-held rule took a survivor's sidearm (live Reach
            # Boardwalk: one sword, two shotguns, one magnum -> the magnum; Halo 4 Flood on Adrift, its opening DMRs
            # and a magnum -> the magnum: sides swapped all match)
            s.zombie_tag = "sword"
        if s.zombie_tag is None:
            count = {}
            for t in tags.values():
                count[t] = count.get(t, 0) + 1
            tag, n = min(count.items(), key=lambda kv: kv[1], default=(None, 0))
            if len(count) < 2 or len(tags) < 3 or n > len(tags) // 3:
                return
            s.zombie_tag = tag
        for p in snap.players:
            if p.id in tags:
                s.infected_ids[p.id] = tags[p.id] == s.zombie_tag
            elif not p.alive and p.id in s.infected_ids:
                s.infected_ids[p.id] = True
            p.team = 1 if s.infected_ids.get(p.id) else 0
            if p.team == 1 and p.weapon == "unknown":
                # a zombie's gun still unnamed (Halo 3: no placement, no tag name read): the energy sword, Halo 3's
                # zombie weapon unless the variant changes it (INFERRED for custom variants)
                p.weapon = "sword"
                p.weapons = ["sword" if w == "unknown" else w for w in p.weapons]
        snap.infected = 1

    def mark_special(s, live, snap, raw):
        """Halo 3 Juggernaut and VIP: who the game marks (VIP: the HUD waypoint over his body, LiveH3.marked_players;
        the Juggernaut: his overshield).
        Juggernaut is a free for all with one marked man: the snapshot's teams become 0 everyone else (allies against
        him: guide 07, "team up with the nearest player on the Juggernaut"), 1 the Juggernaut; nobody marked yet (the
        first kill makes one): free for all as it is. VIP: each team's VIP in snap.vips."""
        if s.mode == "vip":
            try:
                snap.vips = tuple(sorted(live.marked_players(raw)))
            except Exception:
                pass
            return
        # the Juggernaut: his overshield (Construct, live: the juggernaut's shield read over 1.0 in 870 of 909 world
        # rows, nobody else's ever; the HUD waypoint block holds no marker on a body in this mode), kept while he
        # lives: damage takes it under 1.0 (the split held only 35% of a match on the shield alone)
        over = {p.id for p in snap.players if p.alive and p.shield > 1.05}
        cur = getattr(s, "jugg", None)
        if over:
            s.jugg = cur if cur in over else min(over)
        elif cur is not None and not any(p.id == cur and p.alive for p in snap.players):
            s.jugg = None
        marked = {s.jugg} if getattr(s, "jugg", None) is not None else set()
        if not marked or len({p.team for p in snap.players}) < len(snap.players):
            return                          # (a team Juggernaut variant: the game's own teams)
        for p in snap.players:
            p.team = 1 if p.id in marked else 0
        snap.infected = 1                   # (live_bots: free-for-all directors follow their bot's side)

    def one_bomb(s, now, items):
        """One Bomb (guide 19 ASS-ONE-01: one team attacks with the only bomb, the other defends, sides swap each round):
        the team whose stand the game's one bomb belongs to, else None. Two-bomb Assault shows two bombs, Neutral Bomb's
        one is nobody's (team 8): both goals stay. Judged on 10-20 s of reads that found a bomb (a failed read finds
        none; a swap of sides shows both teams in the window and waits for it to pass). Before, One Bomb's attackers
        posted two defenders on their own goal, where nobody plants (director.mode_slots)."""
        hist = s.__dict__.setdefault("bomb_hist", [])
        if items and (not hist or hist[-1][0] != now):
            hist.append((now, len(items), items[0].get("team", 8)))
        while hist and now - hist[0][0] > 20.0:
            hist.pop(0)
        if not hist or now - hist[0][0] < 10.0 or any(n != 1 for _, n, _ in hist):
            return None
        teams = {t for _, _, t in hist}
        return next(iter(teams)) if len(teams) == 1 and teams <= {0, 1} else None

    def dom_bases(s):
        """Dominion's capture points [(Vec, radius, team)]: the dom_base terminals, else the dom_base_zone areas."""
        return s.lay.get("dom_base") or s.lay.get("territory", [])

    def dominion_update(s, live, snap, now, alive):
        """Dominion: snap.obj_zones (a base's terminal, the use radius, its owner), snap.deployed (the turrets and shields
        a fortified base has spawned, with the team of the base they stand at) and snap.dom_orders ({bot id: order},
        dominion_orders). A base is a team's once it stood alone at the terminal DOM_HOLD_S (the game's owner is not read:
        like Territories, until it is decoded); a man from the other team there too holds it contested."""
        out = []
        for k, (pos, _, _) in enumerate(s.dom_bases()):
            teams = {p.team for p in alive if (p.pos - pos).flat() <= DOM_BASE_R and abs(p.pos.z - pos.z) <= DOM_BASE_DZ}
            if len(teams) == 1:
                t = next(iter(teams))
                team, since = s.held.get(k, (t, now))
                if team != t:
                    since = now
                s.held[k] = (t, since)
                if now - since >= s.capture:
                    s.owner[k] = t
            else:
                s.held.pop(k, None)
            out.append((pos, DOM_USE_R, s.owner.get(k, -1)))
        snap.obj_zones = out
        snap.dom_labels = [pos for pos, _, _ in s.lay.get("dom_base", [])]   # the dom_base-labelled terminals (dom_base.bases)
        if now - s.forts_t > 0.5:               # what stands at the bases: the reader's dom_objects() [(name, Vec, team or
            s.forts_t, found = now, []          # None)] when it has one, else the objects it can find by name (loose_named)
            for kind, names in FORT_NAMES:
                try:
                    if hasattr(live, "dom_objects"):
                        found += [(kind, Vec(*q), t) for nm, q, t in live.dom_objects() if any(w in nm for w in names)]
                    else:
                        found += [(kind, Vec(*q), None) for w in names for q in live.loose_named(w)]
                except Exception:
                    pass
            s.forts, seen = [], set()
            for kind, q, t in found:            # a team of its own, else the owner of the base it stands at
                if (kind, round(q.x, 1), round(q.y, 1), round(q.z, 1)) in seen:
                    continue                    # (one object found under two of its names)
                seen.add((kind, round(q.x, 1), round(q.y, 1), round(q.z, 1)))
                if t not in (0, 1):
                    near = [(( q - z[0]).flat(), z[2]) for z in out if (q - z[0]).flat() <= DOM_FORT_R]
                    t = min(near)[1] if near else -1
                s.forts.append((kind, q, t))
        snap.deployed = list(getattr(snap, "deployed", [])) + [dict(kind=k, pos=q, team=t, t=now) for k, q, t in s.forts]
        orders = {}
        for team in (0, 1):
            s.dom_prev[team] = dominion_orders(out, snap.players, team, s.dom_prev.get(team), s.forts)
            orders.update(s.dom_prev[team])
        snap.dom_orders = orders

    def update(s, live, snap, now, raw=None):
        snap.mode = s.mode
        if s.mode == "headhunter" and raw is not None and hasattr(live, "loose_named") and now - s.items_t > 0.25:
            # the skulls lying where men died (guide 19 HH-01: walk over them to take them; director.mode_slots sends
            # runners to the nearest): Reach's skull weapon objects with no parent (the map's tag names: Headhunter and
            # Oddball share objects\weapons\multiplayer\skull\skull). Never filled before: no runner went for one live
            s.items_t = now
            try:
                s.skulls = [Vec(*q) for q in live.loose_named("skull")]
            except Exception:
                s.skulls = []
        if s.mode == "headhunter":
            snap.obj_items = [dict(pos=q, carrier=None, kind="skull", team=8) for q in getattr(s, "skulls", [])]
        if s.mode == "infection" and raw is not None:
            s.split_infected(snap, raw)
        if s.mode == "race":
            s.race_update(live, snap)
        if s.mode == "regicide":
            s.mark_king(snap)
        if s.mode in ("juggernaut", "vip") and raw is not None and (s.mode == "juggernaut" or hasattr(live, "marked_players")):
            # (the Juggernaut is told by his overshield alone: Halo 2's reader has no HUD marks)
            s.mark_special(live, snap, raw)
        alive = [p for p in snap.players if p.alive]
        if s.kind == "dominion":
            s.dominion_update(live, snap, now, alive)
        elif s.mode in ("territories", "headhunter", "infection"):
            kind = {"territories": "territory", "headhunter": "drop_point", "infection": "haven"}[s.mode]
            zones = s.lay.get(kind, [])
            out = []
            for k, (pos, r, _) in enumerate(zones):
                teams = {p.team for p in alive if dist(p.pos, pos) <= r + 0.3 and abs(p.pos.z - pos.z) < 2.5}
                if s.mode == "territories":
                    if len(teams) == 1:
                        t = next(iter(teams))
                        team, since = s.held.get(k, (t, now))
                        if team != t:
                            since = now
                        s.held[k] = (t, since)
                        if now - since >= s.capture:
                            s.owner[k] = t
                    else:
                        s.held.pop(k, None)
                out.append((pos, r, s.owner.get(k, -1)))
            snap.obj_zones = out
        if s.mode in ("assault", "stockpile"):
            stand_kind, goal_kind = ("bomb", "bomb_goal") if s.mode == "assault" else ("stockpile_flag", "stockpile_goal")
            # two teams: stands and goals of team 0, 1 or neutral (8); 2 and 3 are the four-team game's
            placed = [(pos, t) for pos, r, t in s.lay.get(stand_kind, []) if t in (0, 1, 8)]
            if not placed and s.mode == "assault" and hasattr(live, "item_stands"):
                placed = s.live_stands(live, now)
            stands = {k: pos for k, (pos, t) in enumerate(placed)}
            if raw is not None and stands and now - s.items_t > 0.25:
                s.items_t = now
                try:
                    s.items = live.objective_items(raw, stands, s.mode)
                except Exception:
                    s.items = []
            snap.obj_items = [dict(pos=Vec(*it["pos"]), carrier=it["carrier"], kind=stand_kind,
                                   team=placed[it["stand"]][1] if it.get("stand") in stands else 8) for it in s.items]
            # Assault: a bomb with nobody carrying it in the other team's goal, off its own stand, is armed there (as
            # brain/invasion.py's inv_armed: the carrier stood on the goal and the game took it from his hands): "armed"
            # is that goal's team, the one to disarm it (guide 19 ASS-MULTI-03/09). A team's stand lies in its own goal
            # on most maps (Settlement, Halo 2's, Halo 3's): a bomb at home is never armed
            for it, got in zip(snap.obj_items, s.items):
                home = stands.get(got.get("stand"))
                it["armed"] = None if s.mode != "assault" or it["carrier"] is not None or (
                    home is not None and (it["pos"] - home).flat() < 1.0) else next(
                    (t for g, r, t in s.lay.get(goal_kind, []) if t in (0, 1) and t != it["team"]
                     and (it["pos"] - g).flat() <= r + 0.5), None)
            goals = {}
            for pos, r, t in s.lay.get(goal_kind, []):
                for team in ((0, 1) if t == 8 else (t,) if t in (0, 1) else ()):
                    goals.setdefault(team, []).append(pos)
            one = s.one_bomb(now, snap.obj_items) if s.mode == "assault" else None
            if one is not None:                   # One Bomb: the attackers' own goal is nobody's target
                goals.pop(one, None)
            snap.obj_goals = goals
            carriers = {it["carrier"] for it in s.items if it["carrier"] is not None}
            for p in snap.players:
                if p.id in carriers:
                    p.has_flag = True             # carries the objective: runs, no stopping to fight


def carry_goal(mode, snap, team, pos):
    """Where a carrier of this team takes its object: Assault the enemy's goal, Stockpile its own."""
    goals = snap.obj_goals.get(1 - team if mode == "assault" else team) or []
    return min(goals, key=lambda g: dist(g, pos)) if goals else None


# Infection's survivors "cower into buildings with as minimal entrances as possible" (owner, issue #3): holds are rooms
# found on the nav. From each node of a HOLD_LATTICE-cell lattice the floor within HOLD_R by walk is its room; the nodes
# at the edge of that walk are the ways out, each joined group of them an entrance (its middle a door). Kept: rooms of
# HOLD_AREA or more, roofed first, then the fewest entrances, then the narrowest (exposure: the ways out's width).
HOLD_R, HOLD_LATTICE, HOLD_AREA, HOLD_APART = 5.0, 12, 6.0, 12.0
_HOLDS = {}                 # map name -> [hold], None while being found (holds)


def find_holds(T, R=HOLD_R, lattice=HOLD_LATTICE):
    """Every hold on T's nav, best first: dict(pos, entrances, exposure, area, roof, doors). 0.2-4 s a map (Rat's Nest
    0.4 s, Reach's Breakpoint 3.8 s)."""
    import heapq
    import numpy as np
    from scipy import ndimage
    from .terrain import NAV_CELL
    g, N, out = T.graph, T.nh * T.nw, []
    ip, ix, wt = g.indptr, g.indices, g.data
    reach, oob = getattr(T, "reach", None), getattr(T, "oob", None)
    for l, j, i in np.argwhere(T.lwalk):
        if j % lattice or i % lattice or T.degree[l, j, i] < 6:
            continue
        if (reach is not None and not reach[l, j, i]) or (oob is not None and oob[l, j, i]):
            continue                    # (none from a spawn walks there; a kill zone)
        s0 = int(l * N + j * T.nw + i)
        best, h = {s0: 0.0}, [(0.0, s0)]
        while h:
            c, u = heapq.heappop(h)
            if c > best[u]:
                continue
            for k in range(ip[u], ip[u + 1]):
                v, nc = int(ix[k]), c + wt[k]
                if nc <= R and nc < best.get(v, 1e9):
                    best[v] = nc
                    heapq.heappush(h, (nc, v))
        area = len(best) * NAV_CELL * NAV_CELL
        if area < HOLD_AREA:
            continue
        rim = np.fromiter((v for v, c in best.items() if c >= R - 0.5), np.int64)
        doors = []
        for lv in set((rim // N).tolist()):
            sel = rim[rim // N == lv]
            mask = np.zeros((T.nh, T.nw), bool)
            mask[(sel % N) // T.nw, sel % T.nw] = True
            lab, n = ndimage.label(mask, structure=np.ones((3, 3)))
            for c in ndimage.center_of_mass(mask, lab, range(1, n + 1)):
                doors.append(T._center(lv, int(round(c[0])), int(round(c[1]))))
        if not doors:
            continue                    # (a sealed pocket: nobody walks in, nobody out)
        z = float(T.lz[l, j, i])
        out.append(dict(pos=T._center(int(l), int(j), int(i)), entrances=len(doors), exposure=len(rim) * NAV_CELL,
                        area=area, roof=bool(T.top[j * T.nstep, i * T.nstep] - z > 1.8), doors=doors))
    out.sort(key=lambda o: (not o["roof"], o["entrances"], o["exposure"]))
    return out


def holds(T, name, wait=False):
    """The map's holds (find_holds), found once in the background (a big map's 4 s would stall every bot's tick): []
    until then. wait: found now (tests, the sim)."""
    if wait and not _HOLDS.get(name):
        _HOLDS[name] = find_holds(T)
    if name not in _HOLDS:
        import threading
        _HOLDS[name] = None
        threading.Thread(target=lambda: _HOLDS.__setitem__(name, find_holds(T)), daemon=True).start()
    return _HOLDS[name] or []


def pick_holds(hs, k, apart=HOLD_APART):
    """k holds for k survivor groups (the owner: "split into separate groups in their own areas of the map"): the best,
    then each next best at least `apart` from those taken (closer ones if the map has too few)."""
    out = []
    for gap in (apart, apart / 2, 0.0):
        for h in hs:
            if len(out) >= k:
                return out
            if h not in out and all(dist(h["pos"], o["pos"]) >= gap for o in out):
                out.append(h)
    return out


def survivor_clusters(players, r=6.0):
    """Survivors standing together: [(centre, [player])] of 2 or more within r of the first of them, biggest first."""
    left, out = [p for p in players if p.alive], []
    while left:
        p = left.pop(0)
        grp = [p] + [q for q in left if dist(q.pos, p.pos) < r]
        left = [q for q in left if q not in grp]
        if len(grp) >= 2:
            c = Vec(sum(q.pos.x for q in grp) / len(grp), sum(q.pos.y for q in grp) / len(grp),
                    sum(q.pos.z for q in grp) / len(grp))
            out.append((c, grp))
    return sorted(out, key=lambda cg: -len(cg[1]))


def zombie_team(snap):
    """Infection: the infected team (Reach's infected carry swords): the team with the most swords in hand."""
    if snap.infected != -1:
        return snap.infected
    swords = {}
    for p in snap.players:
        if p.alive and "sword" in (p.weapon or ""):
            swords[p.team] = swords.get(p.team, 0) + 1
    return max(swords, key=swords.get) if swords else None


def stands_by_spawn(spots, players):
    """A Forge map variant's CTF stands, which the scenario export lacks: {team: Vec} from where its flag objects lie at
    the start (spots) and the teams' players then (players: [(team, Vec)], at spawn by their own base). Each spot goes
    to the team whose players stand nearest, the best pairing overall (two flags, two teams); {} until both are read."""
    import itertools
    cent = {}
    for t, p in players:
        cent.setdefault(t, []).append(p)
    cent = {t: Vec(sum(p.x for p in ps) / len(ps), sum(p.y for p in ps) / len(ps), sum(p.z for p in ps) / len(ps))
            for t, ps in cent.items()}
    if not spots or not cent:
        return {}
    teams = sorted(cent)
    best = min(itertools.permutations(teams, min(len(spots), len(teams))),
               key=lambda ts: sum((spots[k] - cent[t]).flat() for k, t in enumerate(ts)))
    return {t: spots[k] for k, t in enumerate(best)}
