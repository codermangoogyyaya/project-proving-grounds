"""Firefight teammates without map data (ODST: the kit does not export the collision geometry of the
areas Firefight uses). Each bot follows the player by walking a skilled player's footsteps (a
trail of his positions: always walkable), keeps 3-6 units from him, and fights the nearest
Covenant in range: lead-aimed, reaction delay and aim error by tier, burst fire, and a target its
shots do not hurt (behind cover the bot cannot see) is dropped for a few seconds. Logs every tick
to logs/escort_<game>.jsonl and prints a score at the end.

Reach Firefight on the DLC maps (no map data) also gets: team spawns (TeamSpawns: a bot of the team opposing the person
respawns where its own side's AI were seen, not at his spawn) and hijacking (Escort.hijack / Escort.drive: an AI-driven
ground vehicle of the other side within BOARD_R is boarded and driven at the nearest foe).

usage: python -I escort_bots.py [seconds=120] [--game odst] [--tier average]
"""
import argparse
import json
import math
import os
import random
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
import botctl  # noqa: E402

HZ = 30
CHEST = 0.45                          # aim this far above the feet (Elites and Brutes stand tall)
TIERS = {"novice": (0.45, 0.06), "average": (0.30, 0.035), "strong": (0.22, 0.02), "top": (0.15, 0.012)}   # reaction s, aim error rad
RANGE = 28.0
MATCH_GONE = 6                         # failed reads in a row (0.5 s apart) that mean the match is over
BOARD_R, BOARD_GIVE_UP, BOARD_BAN = 22.0, 8.0, 40.0   # u to an enemy-driven vehicle worth hijacking, s trying, s left alone after
SPOT_AGE = 8.0                                         # s an AI is about before its place counts as its side's ground (not where it arrived)
ROAM_DWELL, ROAM_MIN_LEG, ROAM_SEARCH = (5.0, 14.0), 40, 10.0   # s at a goal, points (0.75 u apart) a new goal lies from here, u to find one's footsteps
SPAWN_WAIT, SPAWN_CLEAR, SPAWN_FOE, SPAWN_SPOTS = 25.0, 3.0, 25.0, 40   # TeamSpawns: s a pick stands, clearances (u), spots kept


def wrap(a):
    return (a + math.pi) % (2 * math.pi) - math.pi


class Escort:
    def __init__(s, slot, index, tier, rng):
        s.slot, s.index, s.rng = slot, index, rng
        s.react, s.err = TIERS[tier]
        s.trail_i, s.target, s.seen_at = 0, None, 0.0
        s.shots, s.hp_at_start, s.banned = 0, 1.0, {}
        s.last, s.stuck_t, s.wiggle_until, s.wiggle = None, 0.0, 0.0, (0.0, 0.0)
        s.err_xy, s.err_t, s.strafe, s.strafe_t = (0.0, 0.0), 0.0, 1.0, 0.0
        s.prev_tpos = None
        s.board, s.board_ban = None, {}          # hijack: (vehicle, since), vehicle -> left alone until
        s.drive_last, s.drive_still, s.reverse_until = None, 0.0, 0.0
        s.roam, s.shared = False, {}             # roam: its own free will (set by main); shared: {bot: goal point}, to spread out
        s.hi, s.goal, s.dwell_until, s.hi_t, s.retreat = None, None, 0.0, -9.0, False

    def weak(s, me, foes, pos):
        """True while the bot is better off at the person's side than roaming: hurt, or too many foes about; it stays
        there until it has recovered (shield and health back, the foes gone)."""
        hp, sh = me.get("health", 1.0), me.get("shield", 1.0)
        near = sum(1 for e in foes if math.dist(e["pos"], pos) < 25.0)
        if s.retreat:
            if hp >= 0.7 and sh >= 0.6 and near < 3:
                s.retreat = False
        elif hp < 0.5 or (sh < 0.15 and near) or near >= 5:
            s.retreat = True
        return s.retreat

    def roam_target(s, now, me, person, hist):
        """Free will without a map: walk the person's footsteps (hist: his positions with a segment number that changes at
        every jump: always walkable) to a spot of the bot's own choosing, stay a while, pick the next. Spots favour places
        away from him and from where the other bots are going. Returns the next waypoint (a few footsteps ahead), or
        None: dwelling at a goal, or not on his footsteps (the caller then walks to him, which puts it back on them)."""
        pos = me["pos"]
        if not hist or now < s.dwell_until:
            return None
        if s.hi is not None and (math.hypot(hist[s.hi][0] - pos[0], hist[s.hi][1] - pos[1]) > ROAM_SEARCH
                                 or abs(hist[s.hi][2] - pos[2]) > 3.0):
            s.hi, s.goal = None, None
        if s.hi is None:
            if now - s.hi_t < 0.5:
                return None
            s.hi_t = now
            best = None
            for i, h in enumerate(hist):
                d = math.hypot(h[0] - pos[0], h[1] - pos[1])
                if d < ROAM_SEARCH and abs(h[2] - pos[2]) < 3.0 and (best is None or d < best[0]):
                    best = (d, i)
            if best is None:
                return None
            s.hi = best[1]
        seg = hist[s.hi][3]
        if s.goal is None or s.goal >= len(hist) or hist[s.goal][3] != seg:
            lo = hi = s.hi
            while lo > 0 and hist[lo - 1][3] == seg:
                lo -= 1
            while hi < len(hist) - 1 and hist[hi + 1][3] == seg:
                hi += 1
            cands = [i for i in (s.rng.randint(lo, hi) for _ in range(10)) if abs(i - s.hi) >= ROAM_MIN_LEG]
            if not cands:
                s.dwell_until = now + 3.0
                return None
            others = [g for k, g in s.shared.items() if k != s.index]
            def score(i):
                h = hist[i]
                return min(40.0, math.dist(h[:2], person["pos"][:2])) + min(40.0, min((math.dist(h[:2], g[:2]) for g in others), default=40.0))
            s.goal = max(cands, key=score)
            s.shared[s.index] = hist[s.goal][:3]
        step = 1 if s.goal > s.hi else -1
        while abs(s.hi - s.goal) > 2 and 0 <= s.hi + step < len(hist) and hist[s.hi + step][3] == seg:
            d0 = math.hypot(hist[s.hi][0] - pos[0], hist[s.hi][1] - pos[1])
            d1 = math.hypot(hist[s.hi + step][0] - pos[0], hist[s.hi + step][1] - pos[1])
            if d0 >= 1.2 and d1 > d0:                   # (on to the next footstep once it is nearer than this one)
                break
            s.hi += step
        if abs(s.hi - s.goal) <= 2:                     # there: stay a while, then somewhere else
            s.dwell_until, s.goal = now + s.rng.uniform(*ROAM_DWELL), None
            s.shared.pop(s.index, None)
            return None
        j = max(0, min(len(hist) - 1, s.hi + step * 3))
        return hist[j]

    def hijack(s, now, me, v):
        """On foot, to an enemy-driven vehicle v (vehicle_scan) and Use at its driver's seat (Reach: the boarding seat, as
        bot.py's board does). None once it would not take us in BOARD_GIVE_UP s (that vehicle is then left alone)."""
        pos, vp = me["pos"], v["pos"]
        if s.board is None or s.board[0] != v["handle"]:
            s.board = (v["handle"], now)
        if now - s.board[1] > BOARD_GIVE_UP:
            s.board_ban[v["handle"]] = now + BOARD_BAN
            s.board = None
            return None
        d = math.hypot(vp[0] - pos[0], vp[1] - pos[1])
        yaw = math.atan2(vp[1] - pos[1], vp[0] - pos[0])
        buttons = 0
        if d < 2.6:
            if int(now * 6) % 2 == 0:
                buttons |= botctl.BTN["action"]
            from brain import vehicles as V
            seat = V.reach_seat(v["kind"], "board", int(now - s.board[1]))
            if seat:
                buttons |= ((seat[0] + 1) & 15) << 14 | (1 << 18 if seat[1] else 0)    # shared.h HB_SEAT_SHIFT / turret bit
        return (1.0 if d > 1.6 else 0.0), 0.0, yaw, 0.0, buttons

    def drive(s, now, me, vpos, person, foes):
        """At the wheel of a ground vehicle (a driver steers by facing): at the nearest foe within 60 u, else to the person;
        shoots at one within 45 u; backs off what it is stuck on."""
        near = min((e for e in foes if math.dist(e["pos"], vpos) < 60.0), key=lambda e: math.dist(e["pos"], vpos), default=None)
        goal = near["pos"] if near else (person["pos"] if person and person["pos"] else None)
        if goal is None:
            return 0.0, 0.0, 0.0, 0.0, 0
        d = math.hypot(goal[0] - vpos[0], goal[1] - vpos[1])
        yaw = math.atan2(goal[1] - vpos[1], goal[0] - vpos[0])
        if now < s.reverse_until:
            return -1.0, 0.0, s.drive_yaw, 0.0, 0
        fwd = 1.0 if d > (14.0 if near else 8.0) else 0.0
        if s.drive_last and fwd and math.dist(vpos[:2], s.drive_last[:2]) < 0.02:
            s.drive_still += 1.0 / HZ
        else:
            s.drive_still = 0.0
        s.drive_last = vpos
        if s.drive_still > 2.0:                  # wedged: back off for a moment, turned away
            s.reverse_until, s.drive_still, s.drive_yaw = now + 1.5, 0.0, yaw + s.rng.choice((-1.0, 1.0))
        return fwd, 0.0, yaw, 0.0, botctl.BTN["fire"] if near and d < 45.0 and int(now * 8) % 4 < 2 else 0

    def think(s, now, me, person, trail, enemies, hist=None):
        fwd = strafe = 0.0
        yaw = math.atan2(me["aim"][1], me["aim"][0]) if me["aim"] else 0.0
        pitch, fire = 0.0, False
        pos = me["pos"]
        # --- fight: nearest Covenant in range, not banned (unhittable from here)
        live = [e for e in enemies if math.dist(e["pos"], pos) < RANGE and s.banned.get(e["index"], 0) < now]
        tgt = min(live, key=lambda e: math.dist(e["pos"], pos) - (4 if e["index"] == s.target else 0), default=None)
        if tgt and tgt["index"] != s.target:
            s.target, s.seen_at, s.shots, s.hp_at_start, s.prev_tpos = tgt["index"], now, 0, tgt["health"] + tgt["shield"], None
        if not tgt:
            s.target = None
        # --- move: walk the player's footsteps until 3-6 units from him
        d_person = math.dist(person["pos"][:2], pos[:2]) if person["pos"] else 0.0
        if trail and (s.trail_i >= len(trail) or math.dist(trail[min(s.trail_i, len(trail) - 1)][:2], pos[:2]) > 12):
            s.trail_i = min(range(len(trail)), key=lambda i: math.dist(trail[i][:2], pos[:2]))   # rejoin (respawned)
        goal = None
        roaming = bool(s.roam and hist and not s.weak(me, enemies, pos))
        if roaming:                                 # its own way about the map
            goal = s.roam_target(now, me, person, hist)
            roaming = goal is not None or s.hi is not None          # (dwelling counts; not on his footsteps: go to him)
        if not roaming and d_person > (6.0 if s.target is None else 9.0) and trail:
            while s.trail_i < len(trail) - 1 and math.dist(trail[s.trail_i][:2], pos[:2]) < 1.2:
                s.trail_i += 1
            goal = trail[min(s.trail_i, len(trail) - 1)]
        if goal is not None:
            move = math.atan2(goal[1] - pos[1], goal[0] - pos[0])
        if s.target is not None and now - s.seen_at > s.react:
            tp, tv = tgt["pos"], (0.0, 0.0, 0.0)
            if s.prev_tpos:
                tv = tuple((a - b) * HZ for a, b in zip(tp, s.prev_tpos))
            s.prev_tpos = tp
            eye = (pos[0], pos[1], pos[2] + 0.62)
            d = math.dist(tp, eye)
            lead = d / 300.0 + 0.05
            aim = (tp[0] + tv[0] * lead, tp[1] + tv[1] * lead, tp[2] + CHEST + tv[2] * lead)
            if now > s.err_t:                       # a fresh small aim error every half second
                s.err_xy, s.err_t = (s.rng.gauss(0, s.err), s.rng.gauss(0, s.err)), now + 0.5
            yaw = math.atan2(aim[1] - eye[1], aim[0] - eye[0]) + s.err_xy[0]
            pitch = math.atan2(aim[2] - eye[2], math.hypot(aim[0] - eye[0], aim[1] - eye[1])) + s.err_xy[1]
            fire = int(now * 8) % 4 < 2            # bursts
            if fire:
                s.shots += 1
            hp = tgt["health"] + tgt["shield"]
            if hp < s.hp_at_start - 0.01:
                s.shots, s.hp_at_start = 0, hp     # landing: keep going
            elif s.shots > 45:                     # 45 trigger ticks, nothing landed: out of sight
                s.banned[s.target] = now + 5.0
                s.target = None
            if now > s.strafe_t:                   # strafe while shooting, as people do
                s.strafe, s.strafe_t = s.rng.choice((-1.0, 1.0)), now + s.rng.uniform(0.6, 1.4)
            strafe = 0.6 * s.strafe
        elif person["aim"] and goal is None:
            yaw = math.atan2(person["aim"][1], person["aim"][0])   # idle: look where the player looks
        if goal is not None:
            if s.target is None or now - s.seen_at <= s.react:
                yaw = move                           # not shooting: face where you walk
            rel = wrap(move - yaw)
            fwd, strafe = math.cos(rel), math.sin(rel)
        # --- unstick: pressing to move but not moving for 1 s: jump and sidestep
        if s.last and (abs(fwd) + abs(strafe)) > 0.3 and math.dist(pos[:2], s.last[:2]) < 0.02:
            s.stuck_t += 1.0 / HZ
        else:
            s.stuck_t = 0.0
        s.last = pos
        jump = False
        if s.stuck_t > 1.0:
            a = s.rng.uniform(0, 2 * math.pi)
            s.wiggle, s.wiggle_until, s.stuck_t = (math.cos(a), math.sin(a)), now + 0.6, 0.0
            jump = True
        if now < s.wiggle_until:
            fwd, strafe = s.wiggle
        buttons = (botctl.BTN["fire"] if fire else 0) | (botctl.BTN["jump"] if jump else 0)
        return fwd, strafe, yaw, pitch, buttons


def vehicle_scan(live, players):
    """{handle: {handle, pos, kind, driver}}: the ground vehicles lying about (not a gun riding on one), each with the
    AI that drives it (live.ai_riders, the seat 0 rider) or None."""
    from brain import vehicles as V
    import struct
    roots = {}
    for h, typ, path, pos in live.objects((1,)):
        try:
            par = struct.unpack("<I", live.g.read(live.object(h) + getattr(live, "PARENT_AT", 0x14), 4))[0]
        except Exception:
            continue
        if par != 0xFFFFFFFF:
            continue
        kind = V.variant(path.rsplit("\\", 1)[-1], ())
        if V.kind_of(kind) in V.BOARDABLE:
            roots[h] = dict(handle=h, pos=pos, kind=kind, driver=None)
    for r in live.ai_riders(players):
        v = roots.get(r["vehicle"])
        if v is not None and r["seat"] in (None, 0):
            v["driver"] = r
    return roots


class TeamSpawns:
    """Reach Firefight without map data: a bot of the team OPPOSING the person (the Spartans when he plays the Elites,
    or the Covenant's team when he plays the Spartans) respawns on its own side, not at his spawn (botcore's fallback).
    The sides' ground is learned from where their AI were first seen: team 1 (the Covenant's) by the Covenant AI, team 0
    by the human-side AI. Sent to botcore while the bot is dead (botctl.spawn_at); a pick still out after SPAWN_WAIT s is
    called off (NaN: botcore's own fallback). No spots known for a side: nothing is sent."""

    def __init__(s, m, rng):
        s.m, s.rng = m, rng
        from brain.live import Walked
        s.spots = {0: [], 1: []}
        s.walked = Walked()
        s.seen, s.picks, s.gave_up, s.warned = {}, {}, set(), set()

    def learn(s, now, ai, players):
        """The sides' ground, from AI that have been about SPOT_AGE s (where an AI first shows can be out of bounds: it
        arrives from outside the play area), and the ground anyone has walked (Walked: a pick must be on it)."""
        s.walked.feed(now, [(p["index"], p["pos"]) for p in players if p["pos"] and p.get("vehicle", -1) in (None, -1)]
                      + [(e["index"], e["pos"]) for e in ai])
        for e in ai:
            t0 = s.seen.setdefault(e["index"], now)
            if t0 is None or now - t0 < SPOT_AGE:
                continue
            s.seen[e["index"]] = None
            pool = s.spots[0 if e.get("human") else 1]
            if all(math.dist(e["pos"], q) > SPAWN_CLEAR for q in pool):
                pool.append(e["pos"])
                del pool[:-SPAWN_SPOTS]

    def step(s, now, index, me, players, ai):
        if me["pos"]:                                  # alive: this death's pick is spent
            s.picks.pop(index, None)
            s.gave_up.discard(index)
            return
        if index in s.gave_up:
            return
        if index in s.picks:
            if now - s.picks[index][1] > SPAWN_WAIT:
                botctl.spawn_at(s.m, index, math.nan, math.nan, math.nan, 0.0)
                s.gave_up.add(index)
                print(f"{now:6.1f}s bot {index}: spawn pick called off after {SPAWN_WAIT:.0f}s", flush=True)
            return
        pool = s.spots[0 if me["team"] == 0 else 1]
        if not pool:
            if me["team"] not in s.warned:
                s.warned.add(me["team"])
                print(f"{now:6.1f}s no AI of team {me['team']}'s side seen yet: its bots use botcore's fallback", flush=True)
            return
        alive = [p["pos"] for p in players if p["pos"]] + [e["pos"] for e in ai]
        foes = [p["pos"] for p in players if p["pos"] and p["team"] != me["team"]] + [e["pos"] for e in ai if e["team"] != me["team"]]
        taken = [v[0] for v in s.picks.values()]
        ok = [c for c in pool if all(math.dist(c[:2], q[:2]) >= SPAWN_CLEAR for q in alive + taken)
              and (not s.walked.known() or s.walked.near(c[0], c[1], c[2]))]
        far = [c for c in ok if all(math.dist(c[:2], q[:2]) >= SPAWN_FOE for q in foes)] or ok
        if not far:
            return
        c = s.rng.choice(far)
        near = min(foes, key=lambda q: math.dist(c[:2], q[:2]), default=None)
        yaw = math.atan2(near[1] - c[1], near[0] - c[0]) if near else 0.0
        s.picks[index] = (c, now)
        botctl.spawn_at(s.m, index, c[0], c[1], c[2], yaw)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("seconds", type=float, nargs="?", default=120)
    ap.add_argument("--game", default="odst")
    ap.add_argument("--tier", default="average")
    ap.add_argument("--follow", action="store_true", help="Reach: stay at the person's side (the old behaviour) instead of roaming")
    o = ap.parse_args()
    from brain.live import LiveODST, LiveReach
    live, m = {"odst": LiveODST, "reach": LiveReach}[o.game](), botctl.shm()   # (Reach: its Firefight maps)
    rng = random.Random(5)
    bots = {}
    trail = []
    spawns = TeamSpawns(m, rng) if o.game == "reach" else None
    hist, roam_goals = [], {}                       # the person's footsteps with a segment number; where each bot is heading
    roots, roots_t = {}, -9.0
    log = open(os.path.join(HERE, "..", "logs", f"escort_{o.game}.jsonl"), "w")
    t0 = time.time()
    fails = 0
    while time.time() - t0 < o.seconds:
        tick = time.time()
        now = tick - t0
        try:
            players = live.players()
            enemies = live.ai_units(players)
        except Exception as e:                       # a read raced the game (match over, object gone)
            print("read failed:", e)
            fails += 1
            # the match is over (3 s of failed reads, as live_bots): stop. botcore ends play.py at the next match, not
            # this child: live Nov 6 an Installation 04 escort brain drove the next match's bots beside its brain
            if fails >= MATCH_GONE:
                print("game gone, brain stopping:", e, flush=True)
                break
            time.sleep(0.5)
            continue
        fails = 0
        ps = {p["index"]: p for p in players}
        person = ps.get(0)
        if spawns:
            spawns.learn(now, enemies, players)
        if o.game == "reach" and now - roots_t > 0.5:      # the ground vehicles and who drives them, twice a second
            roots_t = now
            try:
                roots = vehicle_scan(live, players)
            except Exception as e:
                roots = {}
                if not getattr(main, "scan_warned", False):
                    main.scan_warned = True
                    print("vehicle scan failed (hijacking off):", type(e).__name__, e, flush=True)
        if person and person["pos"] and (not trail or math.dist(trail[-1][:2], person["pos"][:2]) > 0.75):
            trail.append(person["pos"])
            trail = trail[-600:]
        if person and person["pos"] and person.get("vehicle", -1) in (None, -1) and len(hist) < 30000 \
                and (not hist or math.dist(hist[-1][:2], person["pos"][:2]) > 0.75):
            seg = 0 if not hist else hist[-1][3] + (0 if math.dist(hist[-1][:3], person["pos"][:3]) < 6.0 else 1)   # (a jump: respawn, a lift)
            hist.append((person["pos"][0], person["pos"][1], person["pos"][2], seg))
        for slot, index in botctl.status(m)["bots"].items():
            if index not in bots:
                bots[index] = Escort(slot, index, o.tier, rng)
                bots[index].roam, bots[index].shared = o.game == "reach" and not o.follow, roam_goals
            me = ps.get(index)
            if spawns and me and person and me["team"] != person["team"]:
                spawns.step(now, index, me, players, enemies)
            if not me or not me["pos"] or not person or not person["pos"]:
                continue
            foes = [e for e in enemies if e["team"] != me["team"]]     # (Versus' Elite round: the Covenant are allies)
            veh = me.get("vehicle", -1)
            if o.game == "reach" and veh not in (None, -1):            # at the wheel (a hijacked one): drive it
                rv = roots.get(veh)
                if rv is None:
                    continue
                fwd, strafe, yaw, pitch, buttons = bots[index].drive(now, me, rv["pos"], person, foes)
                botctl.act(m, slot, fwd, strafe, yaw, pitch, buttons)
                continue
            fwd = None
            if o.game == "reach" and roots:                           # an enemy-driven vehicle near: hijack it
                b0 = bots[index]
                cand = [v for v in roots.values() if v["driver"] and v["driver"]["team"] != me["team"]
                        and b0.board_ban.get(v["handle"], 0) < now
                        and math.hypot(v["pos"][0] - me["pos"][0], v["pos"][1] - me["pos"][1]) < BOARD_R
                        and abs(v["pos"][2] - me["pos"][2]) < 4.0]
                if cand:
                    best = min(cand, key=lambda v: math.dist(v["pos"], me["pos"]))
                    r = b0.hijack(now, me, best)
                    if r:
                        fwd, strafe, yaw, pitch, buttons = r
            if fwd is None:
                fwd, strafe, yaw, pitch, buttons = bots[index].think(now, me, person, trail, foes, hist)
            botctl.act(m, slot, fwd, strafe, yaw, pitch, buttons)
            b = bots[index]
            tgt = next((e for e in enemies if e["index"] == b.target), None)
            log.write(json.dumps(dict(t=round(now, 3), id=index, pos=[round(v, 2) for v in me["pos"]],
                                      owner_d=round(math.dist(me["pos"], person["pos"]), 2), target=b.target,
                                      target_hp=round(tgt["health"] + tgt["shield"], 3) if tgt else None,
                                      fire=bool(buttons & 1), cmd=[round(fwd, 2), round(strafe, 2)],
                                      enemies=len(enemies), hp=round(me["health"], 2))) + "\n")
        time.sleep(max(0.0, 1.0 / HZ - (time.time() - tick)))
    log.close()
    print(json.dumps(score(os.path.join(HERE, "..", "logs", f"escort_{o.game}.jsonl"))))


def score(path):
    rows = [json.loads(l) for l in open(path)]
    by = {}
    for r in rows:
        by.setdefault(r["id"], []).append(r)
    hits = kills = fire_ticks = stuck = moving = 0
    for rs in by.values():
        for a, b in zip(rs, rs[1:]):
            fire_ticks += a["fire"]
            if a["target"] is not None and a["target"] == b["target"] and a["target_hp"] is not None and b["target_hp"] is not None:
                hits += b["target_hp"] < a["target_hp"] - 0.005
            if a["target"] is not None and a["target"] != b["target"] and a["target_hp"] is not None and a["target_hp"] < 0.35:
                kills += 1                           # dropped a nearly dead target: it died (or vanished)
            if abs(a["cmd"][0]) + abs(a["cmd"][1]) > 0.3:
                moving += 1
                stuck += math.dist(a["pos"][:2], b["pos"][:2]) < 0.02
    dists = sorted(r["owner_d"] for r in rows)
    return dict(minutes=round((rows[-1]["t"] - rows[0]["t"]) / 60, 1) if rows else 0, bots=len(by), fire_ticks=fire_ticks,
                damage_ticks=hits, likely_kills=kills, median_person_dist=dists[len(dists) // 2] if dists else None,
                stuck_share=round(stuck / max(moving, 1), 2))


def selftest():
    e = Escort(0, 1, "average", random.Random(1))
    person = dict(pos=(20.0, 0.0, 0.0), aim=(1.0, 0.0, 0.0))
    trail = [(x, 0.0, 0.0) for x in range(0, 21)]
    me = dict(pos=(0.0, 0.0, 0.0), aim=(0.0, 1.0, 0.0))
    fwd, strafe, yaw, pitch, b = e.think(0.0, me, person, trail, [])
    assert abs(wrap(yaw + math.atan2(strafe, fwd))) < 0.2 and fwd > 0.9 and abs(yaw) < 0.2, "walks east, facing east"
    near = dict(pos=(17.0, 0.0, 0.0), aim=(1.0, 0.0, 0.0))
    assert e.think(0.1, near, person, trail, [])[:2] == (0.0, 0.0), "holds within 6 units of the person"
    foe = dict(index=1007, pos=(17.0, 10.0, 0.0), health=1.0, shield=1.0)
    assert not e.think(0.2, near, person, trail, [foe])[4] & 1, "no shot before the reaction time"
    assert any(e.think(0.6 + k / 30, near, person, trail, [foe])[4] & 1 for k in range(8)), "fires after it"
    print("escort self-check OK")


if __name__ == "__main__":
    selftest() if "--selftest" in sys.argv else main()
