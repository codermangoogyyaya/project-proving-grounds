"""Run: python3 tests/test_dom_base.py   (dom_base.py in the parent folder; no game needed)"""
import json, math, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "brain"))
import dom_base as D


class V:
    def __init__(s, x, y, z): s.x, s.y, s.z = x, y, z


class P:
    def __init__(s, pid, x, y, z=0.0, alive=True, vehicle=-1): s.id, s.pos, s.alive, s.vehicle = pid, V(x, y, z), alive, vehicle


class Snap:
    def __init__(s, players, zones): s.players, s.obj_zones = players, zones
    def player(s, i): return next((p for p in s.players if p.id == i), None)


class Act:
    def __init__(s):
        s.yaw = s.pitch = 0.0; s.forward = 1.0; s.strafe = 0.5
        for f in "fire fire2 grenade melee reload equipment ability crouch sprint cycle turret jump action".split():
            setattr(s, f, True)
        s.action = False; s.weapon = 2; s.zoom = 1; s.vertical = 1.0; s.seat = 3; s.lunge_at = 4


AIMS = []
def take(a, p): a.yaw, a.action = 1.0, True; AIMS.append(p)


def run(bot, snap, now, team=0):
    a = Act(); me = snap.player(bot)
    return D.step(bot, team, now, me, snap, a, take), a


def fresh():
    D.reset(); D.set_labelled_positions(None)

n = 0
def ok(c, m):
    global n; n += 1
    assert c, m

# zones: (pos, radius, owner team); team 0 plays, 1 enemy, 8 neutral
Z = lambda x, y, owner: (V(x, y, 0.0), 1.0, owner)

# 1 enter radius -> locked, stationary, Use held, everything else cleared
fresh(); s = Snap([P(1, 0.4, 0.0)], [Z(0, 0, 1)])
h, a = run(1, s, 0.0)
ok(h == 0.0 and not a.action and a.forward == 0 and a.strafe == 0, "claimed: the settle, view set, no press yet")
ok(not any([a.fire, a.fire2, a.grenade, a.melee, a.reload, a.equipment, a.ability, a.crouch, a.sprint, a.cycle, a.turret, a.jump]), "buttons")
h, a = run(1, s, D.DOM_BASE_SETTLE_S + 0.05); ok(a.action and not a.cycle and not a.fire2 and a.forward == 0, "pressed after the settle")
h, a = run(1, s, D.DOM_BASE_SETTLE_S + 0.5); ok(a.action and not a.cycle and not a.fire2, "no level bits before 1 s")
h, a = run(1, s, D.DOM_BASE_SETTLE_S + 1.1); ok(a.action and a.cycle and not a.fire2, "level 1 (+0x40 bit 2) at 1 s")
h, a = run(1, s, D.DOM_BASE_SETTLE_S + 2.1); ok(a.action and a.cycle and a.fire2, "level 2 (+0x40 bit 3) at 2 s")
h, a = run(1, s, D.DOM_BASE_SETTLE_S + 2.6); ok(a.action and a.cycle and a.fire2, "held on")
fresh(); s = Snap([P(1, 0.4, 0.0)], [Z(0, 0, 1)])
h, a = run(1, s, 0.0)
ok(a.weapon == -1 and a.zoom == 0 and a.vertical == 0 and a.seat == -1 and a.lunge_at == -1, "other fields")
# outside radius: nothing
fresh(); s = Snap([P(1, 1.3, 0.0)], [Z(0, 0, 1)])
h, a = run(1, s, 0.0); ok(h is None and a.forward == 1.0 and not D.CLAIMS, "outside")
# 2 held every tick for 5 s, then released with a cooldown
fresh(); s = Snap([P(1, 0.3, 0)], [Z(0, 0, 1)])
END = D.DOM_BASE_SETTLE_S + D.DOM_BASE_HOLD_S
for t in [i * 0.1 for i in range(0, int(END * 10))]:
    h, a = run(1, s, t); ok(h is not None and a.action == (t >= D.DOM_BASE_SETTLE_S - 1e-9), f"held {t}")
h, a = run(1, s, END + 0.01); ok(h is None and not D.CLAIMS, "released after the hold")
h, a = run(1, s, END + 1.0); ok(h is None, "cooldown")
h, a = run(1, s, END + 2.2); ok(h == 0.0, "claims again after the cooldown")
# a claim is not lost between ticks 0.9 s apart, only past STALE_S
fresh(); s = Snap([P(1, 0.3, 0)], [Z(0, 0, 1)]); run(1, s, 0.0); h, _ = run(1, s, 0.9); ok(h == 0.9, "slow tick keeps the claim")
# 3 exclusive: second bot gets nothing while the first holds; first bot keeps it
fresh(); s = Snap([P(1, 0.4, 0), P(2, 0, 0.3)], [Z(0, 0, 1)])
h1, _ = run(1, s, 0.0); h2, a2 = run(2, s, 0.0)
ok(h1 is not None and h2 is None and a2.forward == 1.0, "exclusive")
h1, _ = run(1, s, 0.5); h2, _ = run(2, s, 0.5); ok(h1 is not None and h2 is None, "stays with the first")
# 4 holder dies -> claim gone, the other takes it
s.players[0].alive = False
h2, _ = run(2, s, 1.0); ok(h2 == 0.0 and D.CLAIMS[next(iter(D.CLAIMS))]["bot"] == 2, "death cancels, handoff")
h1, _ = run(1, s, 1.1); ok(h1 is None, "dead bot not locked")
# 5 leaving the radius cancels
fresh(); s = Snap([P(1, 0.3, 0)], [Z(0, 0, 1)]); run(1, s, 0.0)
s.players[0].pos = V(1.4, 0, 0); h, _ = run(1, s, 0.5); ok(h is None and not D.CLAIMS, "leaving cancels")
# 6 friendly: nothing; flips mid-hold -> released
fresh(); s = Snap([P(1, 0.3, 0)], [Z(0, 0, 0)]); h, a = run(1, s, 0.0); ok(h is None and a.forward == 1.0, "friendly")
fresh(); s = Snap([P(1, 0.3, 0)], [Z(0, 0, 1)]); run(1, s, 0.0)
s.obj_zones = [Z(0, 0, 0)]; h, a = run(1, s, 1.0); ok(h is not None and a.forward == 0 and a.action, "flipped to ours: still held")
for t in [1.1 + i * 0.1 for i in range(int((END - 1.1) * 10) - 1)]:
    h, _ = run(1, s, t); ok(h is not None, f"held on after the flip {t:.1f}")
h, _ = run(1, s, END + 0.01); ok(h is None and not D.CLAIMS, "let go at the end of the hold, base ours: no cooldown")
ok(not D.COOLDOWN, "no cooldown on a taken base")
# 7 neutral counts as capturable
fresh(); s = Snap([P(1, 0.3, 0)], [Z(0, 0, 8)]); h, _ = run(1, s, 0.0); ok(h == 0.0, "neutral")
# 8 height: a bot on the floor above does not claim
fresh(); s = Snap([P(1, 0.3, 5.0)], [Z(0, 0, 1)]); h, _ = run(1, s, 0.0); ok(h is None, "dz")
# 9 vehicle: no capture
fresh(); s = Snap([P(1, 0.3, 0, vehicle=4)], [Z(0, 0, 1)]); h, _ = run(1, s, 0.0); ok(h is None, "vehicle")
# 10 labels matched to zones by POSITION, not index (zones listed in the opposite order of the labels)
fresh(); D.set_labelled_positions([(50, 0, 0), (0, 0, 0.2)])
s = Snap([P(1, 0.3, 0)], [Z(0, 0, 1), Z(50, 0, 0)])      # zone 0 is the label list's 2nd; zone 1 (friendly) its 1st
bs = {b["key"]: b for b in D.bases(s, 0)}
ok(len(bs) == 2 and bs[(0.0, 0.0, 0.0)]["friendly"] is False and bs[(50.0, 0.0, 0.0)]["friendly"] is True, "position match")
h, _ = run(1, s, 0.0); ok(h == 0.0, "claims the enemy one by position")
# 11 an unlabelled zone is not a Dom_base when labels are known
fresh(); D.set_labelled_positions([(50, 0, 0)]); s = Snap([P(1, 0.3, 0)], [Z(0, 0, 1), Z(50, 0, 1)])
h, _ = run(1, s, 0.0); ok(h is None, "unlabelled zone ignored")
# 12 two bases, one claim each: a bot holds only one
fresh(); s = Snap([P(1, 0, 0), P(2, 10, 0)], [Z(0, 0, 1), Z(10, 0, 1)])
h1, _ = run(1, s, 0); h2, _ = run(2, s, 0); ok(h1 == 0.0 and h2 == 0.0 and len(D.CLAIMS) == 2, "two bases")
# 13 stale claim (holder stopped being stepped) is let go
fresh(); s = Snap([P(1, 0.3, 0), P(2, 0, 0.3)], [Z(0, 0, 1)]); run(1, s, 0.0)
h2, _ = run(2, s, 4.0); ok(h2 is None, "a 4 s stall does not free the claim")
h1, _ = run(1, s, 4.1); ok(h1 is not None and abs(h1 - 4.1) < 1e-9, "holder keeps its timer through the stall")
fresh(); s = Snap([P(1, 0.3, 0), P(2, 0, 0.3)], [Z(0, 0, 1)]); run(1, s, 0.0)
h2, _ = run(2, s, 7.0); ok(h2 == 0.0, "claim of a bot that is gone for 7 s is freed")
# 14 the view: level like a person's (pitch about -0.15 from the eye), toward the terminal, whatever the label's height
fresh(); me = P(1, 0.9, 0.0, 0.8); s = Snap([me], [(V(0, 0, -0.3), 1.0, 1)]); AIMS.clear(); run(1, s, 0.0)
q = AIMS[-1]; fl = math.hypot(q.x - 0.9, q.y); eye = 0.8 + 0.62
ok(abs(math.atan2(q.z - eye, fl) - D.DOM_BASE_PITCH) < 1e-6, "pitch")
ok(abs(math.atan2(q.y - 0.0, q.x - 0.9) - math.pi) < 1e-6, "yaw toward the terminal")
fresh(); me = P(1, 0.1, 0.0, 0.0); me.yaw = 1.2; s = Snap([me], [Z(0, 0, 1)]); AIMS.clear(); run(1, s, 0.0)
q = AIMS[-1]; ok(abs(math.atan2(q.y, q.x - 0.1) - 1.2) < 1e-6 and abs(q.z - (0.62 - 0.15 * 1.0 * 1.0)) < 0.02, "on top: keeps its facing, level")
# 15 the terminal's use target: the handle from the claim (settle included) to its release, by the label's place
tmp = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_targets.json")
json.dump({"mapx": {"bases": [{"pos": [0, 0, 0], "handle": "0xE55E02EF"}, {"pos": [10, 0, 0], "handle": "0xE55F02F0"}]}}, open(tmp, "w"))
ok(D.load_use_targets(tmp, "mapx") == 2 and D.load_use_targets(tmp, "other") == 0, "table by map")
fresh(); D.load_use_targets(tmp, "mapx")
s = Snap([P(1, 0.3, 0), P(2, 10.3, 0)], [Z(0, 0, 1), Z(10, 0, 1)])
ok(D.use_target(1) == 0, "no claim, no target")
run(1, s, 0.0); run(2, s, 0.0)
ok(D.use_target(1) == 0xE55E02EF and D.use_target(2) == 0xE55F02F0, "each bot has its own terminal's handle from the claim")
s.players[0].alive = False; run(1, s, 0.5); ok(D.use_target(1) == 0, "released: no target")
D.USE_TARGETS[:] = [(50.0, 0.0, 0.0, 7)]; ok(D.use_target(2) == 0, "a terminal not in the table: none")
os.remove(tmp)
print(f"{n} checks passed")
