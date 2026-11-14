"""Halo 4 Dominion: exclusive, stationary base capture (the "Dom_base" system).

Replaces Bot.dom_act's terminal handling (walk the last steps, aim, Use, retry). One shared claim per base, so two
bots never fight over one terminal. Pure logic, no game imports: Bot.plan() calls step() once per bot per tick.

Rules (the handover's table):
  object label    dom_base netgame flags (modes.layout, any case: "Dom_base"): snap.dom_labels, matched to snap.obj_zones by
                  position. No such flags on the map (modes falls back to dom_base_zone areas): every live zone counts
  radius          DOM_BASE_RADIUS (1.0 u, flat) and DOM_BASE_DZ up/down
  duration        a real capture takes about 2.4 s of E held (the owner's input log, Nov 10: 2.35 / 2.37 / 2.4 s from the press
                  to the target changing). The bot settles DOM_BASE_SETTLE_S on the terminal, then holds Use DOM_BASE_HOLD_S
                  (5 s, a margin), then the claim is let go. The owner estimate flipping to ours does NOT end the hold
  input           Use (Action.action) held, with the two hold-level bits the game itself shows during a real hold (+0x40 bit 2
                  one second after the press, bit 3 two seconds after it): Action.cycle and Action.fire2, mapped to those
                  bits in maps/_inputs/h4.json (a person's block: 0x400000, then 0xC00000 at 0.3 s, 0xC00004 at 1 s, 0xC0000C at 2 s)
  movement        none: every movement, combat and button field of the Action is cleared, the view stays on the terminal
  multiple bots   the first bot inside takes the claim; the others do nothing here until it is let go
  death / leaving cancel the claim at once (a bot in a vehicle counts as gone)
  friendly base   nothing happens
  humans          never touched: only the Action of a Bot is ever passed in, and a human never holds a claim
"""
import json
import math

DOM_BASE_LABEL = "Dom_base"
DOM_BASE_RADIUS = 1.0      # u, flat distance to the terminal (a person stands about 0.8-1.3 u back, looking level)
DOM_BASE_DZ = 2.0          # u, height difference allowed (the owner belief in modes.dominion_update uses the same)
DOM_BASE_SETTLE_S = 0.4    # s on the terminal, view set, before the press (a person's target flag was up before his E)
DOM_BASE_CAPTURE_S = 2.8   # s from claiming to the capture being done: the settle + the real 2.4 s hold (modes.py: the bot-side
                           # owner estimate flips after this)
DOM_BASE_HOLD_S = 5.0      # s of Use held per claim: twice the real capture, so a late one still finishes
DOM_BASE_LEVEL1_S = 1.0    # s into the hold: +0x40 bit 2 (Action.cycle), as in a person's block
DOM_BASE_LEVEL2_S = 2.0    # s into the hold: +0x40 bit 3 (Action.fire2)
MATCH_XY = 3.0             # u: a labelled object is the live zone within this (flat) and MATCH_DZ (height)
MATCH_DZ = 3.0
DOM_BASE_PITCH = -0.15     # rad: the view while capturing, as a person holds it (the match of Nov 9: 0.8-1.3 u back, pitch -0.1 to -0.23)
RETRY_COOLDOWN_S = 2.0     # s: after a full hold that did not flip the base, this bot waits before claiming it again
STALE_S = 6.0              # s: a claim whose holder has not stepped for this long is let go (bot removed / not planned). The
                           # match trace had 12 whole-loop stalls of 0.9-4.1 s in 419 s: at 1 s a stall dropped the claim and another
                           # bot could take it from under the one that was holding

USE_TARGETS = []           # [(x, y, z, handle)]: each terminal's object handle, by the place of its Dom_base label (load_use_targets)
USE_TARGET_MATCH = 3.0     # u: a claim's base is the table's entry within this (flat) of its label
_labelled = None           # [(x, y, z)] of the Dom_base objects, or None: not read from the map
CLAIMS = {}                # base key -> {"bot", "t0", "seen", "pos"}
ENDED = {}                 # bot id -> (time, held s, why): the last capture this bot let go (a log note in Bot.plan)
COOLDOWN = {}              # (bot id, base key) -> time it may claim again


def reset():
    """A new match / map: no claims, no cooldowns."""
    CLAIMS.clear()
    COOLDOWN.clear()
    ENDED.clear()


def load_use_targets(path, map_name):
    """The terminals' object handles for this map (maps/_inputs/h4_use_targets.json, read off the owner's input block while
    he aimed at each terminal: they are the same in every match of a map). Returns how many; 0 when the map has none."""
    global USE_TARGETS
    try:
        with open(path, encoding="utf-8") as f:
            ents = json.load(f).get(map_name, {}).get("bases", [])
    except (OSError, ValueError):
        ents = []
    USE_TARGETS = [(float(e["pos"][0]), float(e["pos"][1]), float(e["pos"][2]), int(str(e["handle"]), 16)) for e in ents]
    return len(USE_TARGETS)


def use_target(bot_id):
    """The object handle of the terminal this bot holds the claim of (0: none, or the map's table has no such terminal):
    what the brain sends the DLL as the bot's use target (botctl.set_use_target), from the claim on, so it stands in the
    bot's action before the press as a person's does."""
    c = next((c for c in CLAIMS.values() if c["bot"] == bot_id), None)
    if c is None:
        return 0
    best = min(((_flat(c["pos"], (x, y, z)), h) for x, y, z, h in USE_TARGETS), default=None)
    return best[1] if best and best[0] <= USE_TARGET_MATCH else 0


def set_labelled_positions(points):
    """The world positions of the map's Dom_base labelled objects (x, y, z each), read from the map loader. None
    (the default) means the labels are not available: every live objective zone then counts as a Dom_base."""
    global _labelled
    _labelled = None if points is None else [(float(p[0]), float(p[1]), float(p[2])) for p in points]


def _xyz(p):
    return (p.x, p.y, p.z) if hasattr(p, "x") else (p[0], p[1], p[2])


def _flat(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _key(zone_pos):
    x, y, z = zone_pos
    return (round(x, 1), round(y, 1), round(z, 1))


def bases(snap, team):
    """The Dom_base bases of this tick: [{"key", "pos", "friendly"}], matched to the live objective zones by POSITION
    (snap.obj_zones rows are (pos, radius, owner team); their order says nothing about the labels' order). With
    labels: pos is the labelled object's place, the zone only gives the owner; a label with no zone within
    MATCH_XY / MATCH_DZ is not a live objective and is skipped. Without labels: the zones themselves."""
    zones = [(_xyz(z[0]), z[2]) for z in (getattr(snap, "obj_zones", None) or [])]
    labels = getattr(snap, "dom_labels", None) or _labelled      # modes.dominion_update's dom_base flags, else injected
    labels = None if not labels else [_xyz(p) for p in labels]
    out = []
    if labels is None:
        for zp, owner in zones:
            out.append({"key": _key(zp), "pos": zp, "friendly": owner == team})
        return out
    seen = {}
    for lp in labels:
        best = min(((_flat(lp, zp), zp, owner) for zp, owner in zones
                    if _flat(lp, zp) <= MATCH_XY and abs(lp[2] - zp[2]) <= MATCH_DZ), default=None, key=lambda t: t[0])
        if best is None:
            continue
        key = _key(best[1])
        if key not in seen or best[0] < seen[key][0]:        # two labels on one zone: the nearer stays
            seen[key] = (best[0], {"key": key, "pos": lp, "friendly": best[2] == team})
    return [v[1] for v in seen.values()]


def _inside(me_pos, base_pos):
    p = _xyz(me_pos)
    return _flat(p, base_pos) <= DOM_BASE_RADIUS and abs(p[2] - base_pos[2]) <= DOM_BASE_DZ


def holds(bot_id):
    """Does this bot hold a claim (as of its last step)?"""
    return any(c["bot"] == bot_id for c in CLAIMS.values())


def release(bot_id, now=None, why=""):
    for k in [k for k, c in CLAIMS.items() if c["bot"] == bot_id]:
        if now is not None:
            ENDED[bot_id] = (now, now - CLAIMS[k]["t0"], why)
        del CLAIMS[k]


def _prune(now, snap):
    for k, c in list(CLAIMS.items()):
        p = snap.player(c["bot"])
        if p is None or not p.alive or now - c["seen"] > STALE_S:
            del CLAIMS[k]


def _aim_point(me, base_pos):
    """Where the view rests: toward the terminal's place, at DOM_BASE_PITCH from the eye. The label's height is not the
    terminal's (it lay 0.35-1.0 u under a bot's feet: aimed at, the bot looked at its boots, 74 deg down). Standing
    on top of it (under 0.3 u) the view keeps the way the bot already faces."""
    p = _xyz(me.pos)
    eye = p[2] + (0.35 if getattr(me, "crouching", False) else 0.62)
    flat = _flat(p, base_pos)
    if flat >= 0.3:
        ux, uy, d = (base_pos[0] - p[0]) / flat, (base_pos[1] - p[1]) / flat, flat
    else:
        yaw = getattr(me, "yaw", 0.0)
        ux, uy, d = math.cos(yaw), math.sin(yaw), 1.0
    x, y, z = p[0] + ux * d, p[1] + uy * d, eye + math.tan(DOM_BASE_PITCH) * d
    return type(me.pos)(x, y, z) if hasattr(me.pos, "x") else (x, y, z)


def _lock(act, take, aim, press):
    """Nothing but Use and the view on the terminal. Every field buttons() reads is cleared, not only the ones the
    old dom_act cleared (weapon swap, zoom, ability, vertical, lunge, seat were left to the planner)."""
    for f in ("fire", "fire2", "grenade", "melee", "reload", "equipment", "ability", "crouch", "sprint", "cycle",
              "turret", "jump"):
        if hasattr(act, f):
            setattr(act, f, False)
    for f, v in (("forward", 0.0), ("strafe", 0.0), ("vertical", 0.0), ("zoom", 0), ("weapon", -1), ("seat", -1),
                 ("lunge_at", -1)):
        if hasattr(act, f):
            setattr(act, f, v)
    take(act, aim)                                  # view on the terminal, action = True
    act.forward = act.strafe = 0.0                  # (take keeps the walk under the turned view: none)
    act.action = press >= 0.0                       # the settle first: the view on the terminal, no press yet
    if hasattr(act, "cycle"):
        act.cycle = press >= DOM_BASE_LEVEL1_S      # the hold-level bits of a real hold (h4.json cycle / fire2 rows)
    if hasattr(act, "fire2"):
        act.fire2 = press >= DOM_BASE_LEVEL2_S


def step(bot_id, team, now, me, snap, act, take, terminal_z=1.0):
    """One bot, one tick, after the planner. Returns the seconds this bot has held the claim (act is now the locked,
    stationary Use action), or None (act untouched: the bot does what its plan says)."""
    _prune(now, snap)
    if not me.alive or getattr(me, "vehicle", None) not in (None, -1):
        release(bot_id, now, "died or seated")      # death (or a seat) cancels
        return None
    bs = {b["key"]: b for b in bases(snap, team)}
    mine = next(((k, c) for k, c in CLAIMS.items() if c["bot"] == bot_id), None)
    if mine is not None:
        key, c = mine
        b = bs.get(key)
        if b is None:                               # the base is gone
            release(bot_id, now, "base is gone")
            return None
        if not _inside(me.pos, b["pos"]):           # left the radius: cancel
            release(bot_id, now, "left the radius")
            return None
        if now - c["t0"] - DOM_BASE_SETTLE_S >= DOM_BASE_HOLD_S:   # the full hold, and it is still not ours: let go, wait, again
            release(bot_id, now, "hold time done" + (", base is ours" if b["friendly"] else ", base still not ours"))
            if not b["friendly"]:
                COOLDOWN[(bot_id, key)] = now + RETRY_COOLDOWN_S
            return None
    else:
        key = b = c = None
        for k, cand in sorted(bs.items(), key=lambda kv: _flat(_xyz(me.pos), kv[1]["pos"])):
            if cand["friendly"] or not _inside(me.pos, cand["pos"]):
                continue
            if k in CLAIMS or COOLDOWN.get((bot_id, k), 0.0) > now:
                continue                            # claimed by another bot / waiting out the last try
            key, b = k, cand
            c = CLAIMS[k] = {"bot": bot_id, "t0": now, "seen": now, "pos": cand["pos"]}
            break
        if key is None:
            return None
    c["seen"] = now
    _lock(act, take, _aim_point(me, b["pos"]), now - c["t0"] - DOM_BASE_SETTLE_S)
    return now - c["t0"]
