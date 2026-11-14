"""The real brain/modes.py + core.py + dom_base.py together (no game): python3 tests/test_modes_integration.py
Run from the folder that holds brain/ (this test adds it to the path)."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from brain.core import Vec, PlayerState, Snapshot
from brain import modes, dom_base as D

FLAGS = [{"type": "Dom_base", "pos": [30.0, 0.0, 0.0]}, {"type": "Dom_base", "pos": [0.0, 0.0, 0.0]},
         {"type": "dom_base_zone", "pos": [30.0, 0.0, 0.0]}]        # (a capital label; zones listed far one first)


class Live:
    def dom_objects(self): return []


def mk(players, w, now):
    snap = Snapshot(time=now, players=players, mode="territories")
    w.update(Live(), snap, now)
    return snap


def P(i, team, x, y=0.0, **k): return PlayerState(id=i, team=team, name=f"b{i}", pos=Vec(x, y, 0.0), **k)

take = lambda a, p: setattr(a, "action", True)
from brain.core import Action

# 1 label read in any case, zones 1:1 with the dom_base flags, dom_labels set
w = modes.Watch("dominion", {"netgame_flags": FLAGS}, "h4")
snap = mk([P(1, 0, 90.0)], w, 0.0)
assert len(snap.obj_zones) == 2 and len(snap.dom_labels) == 2, "Dom_base read"
assert modes.DOM_HOLD_S == D.DOM_BASE_CAPTURE_S == 2.8 and D.DOM_BASE_HOLD_S == 5.0 and modes.DOM_BASE_R == 1.0

# 2 a bot 2.5 u from the (0,0) terminal (labels listed the other way round: matched by position, not index)
D.reset(); w = modes.Watch("dominion", {"netgame_flags": FLAGS}, "h4")
claimed = released = None; owner_at = None; lock_ticks = 0
t = 0.0
while t < 10.0:
    bot = P(1, 0, 0.4)
    snap = mk([bot, P(9, 0, 90.0, is_bot=False)], w, t)        # (a human far off: never given an order or a lock)
    assert 9 not in snap.dom_orders, "human has no order"
    a = Action(forward=1.0, fire=True)
    h = D.step(1, 0, t, bot, snap, a, take)
    if h is not None:
        lock_ticks += 1
        claimed = claimed if claimed is not None else t
        assert a.forward == 0 and not a.fire
    elif claimed is not None and released is None:
        released = t
    mine = [z for z in snap.obj_zones if (z[0] - Vec(0, 0, 0)).flat() < 1]
    if owner_at is None and mine[0][2] == 0:
        owner_at = t
    t = round(t + 0.05, 3)
assert claimed == 0.0, claimed
assert owner_at is not None and abs(owner_at - D.DOM_BASE_CAPTURE_S) < 0.11, owner_at   # belief flips with the capture time
assert released is not None and abs(released - (D.DOM_BASE_SETTLE_S + D.DOM_BASE_HOLD_S)) < 0.11, released   # the lock outlasts the flip
assert D.claims_count() if hasattr(D, "claims_count") else not D.CLAIMS, "claim released"
assert abs(lock_ticks * 0.05 - (D.DOM_BASE_SETTLE_S + D.DOM_BASE_HOLD_S)) < 0.2, lock_ticks

# 3 a bot at 3.5 u is outside: no lock, no ownership
D.reset(); w = modes.Watch("dominion", {"netgame_flags": FLAGS}, "h4")
for k in range(200):
    now = k * 0.05; bot = P(1, 0, 1.4); snap = mk([bot], w, now)
    assert D.step(1, 0, now, bot, snap, Action(), take) is None
assert all(z[2] == -1 for z in snap.obj_zones), "owner stays neutral outside 1.0 u"

# 4 two bots: one claim, one owner
D.reset(); w = modes.Watch("dominion", {"netgame_flags": FLAGS}, "h4")
held = {1: 0, 2: 0}
for k in range(60):
    now = k * 0.05; bs = [P(1, 0, 0.4), P(2, 0, 0.0, 0.4)]; snap = mk(bs, w, now)
    for b in bs:
        if D.step(b.id, 0, now, b, snap, Action(), take) is not None: held[b.id] += 1
assert held[1] == 60 and held[2] == 0, held
print("modes integration ok")
