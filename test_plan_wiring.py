"""plan() wiring with a fake Bot (the real one needs the whole game package): python3 tests/test_plan_wiring.py"""
import ast, os, sys, textwrap
here = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(here, "..", "brain"))
sys.path.insert(0, here)
import dom_base
from test_dom_base import V, P, Snap, Act, Z   # (re-runs its checks: fine)

src = open(os.path.join(here, "..", "brain", "bot.py"), encoding="utf-8").read()
tree = ast.parse(src)
bot = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Bot")
fn = next(n for n in bot.body if isinstance(n, ast.FunctionDef) and n.name == "plan")
ns = {"dom_base": dom_base}
exec(textwrap.dedent(ast.get_source_segment(src, fn)), ns)
plan = ns["plan"]


class FV(V):
    def __sub__(s, o): return FV(s.x - o.x, s.y - o.y, 0)
    def flat(s): return (s.x ** 2 + s.y ** 2) ** 0.5


class FakeBot:
    DOM_BASE_SYSTEM, DOM_TERMINAL_Z = True, 1.0
    def __init__(s, pid, dom=None): s.id, s.team, s.dom, s.notes, s.old = pid, 0, dom, [], 0
    def _plan(s, *a):
        x = Act(); x.forward = 1.0; return x
    @staticmethod
    def take(me, act, pos): act.yaw, act.action = 9.0, True
    def dom_act(s, now, me, snap, act): s.old += 1; return act


def mk(pid, x, y): p = P(pid, x, y); p.pos = FV(x, y, 0); return p

dom_base.reset(); dom_base.set_labelled_positions(None)
snap = Snap([mk(1, 0.3, 0.0)], [(FV(0, 0, 0), 1.0, 1)]); snap.mode = "territories"; snap.dom_orders = {}
b = FakeBot(1, dom={"kind": "capture", "clear": [], "pos": FV(0, 0, 0)})
a = plan(b, 0.0, 0.05, snap, None, None, None)
assert not a.action and a.forward == 0 and a.yaw == 9.0 and b.old == 0 and any("Dom_base" in n for n in b.notes), "locked: settling"
a = plan(b, 0.6, 0.05, snap, None, None, None); assert a.action and a.forward == 0, "locked: pressed"
a = plan(b, 0.05, 0.05, snap, None, None, None)
assert b.shuffle_until > 0.05 and b.stuck_pos is None, "stuck detector parked while locked"
# old dom_act only for a base with turrets up
dom_base.reset(); snap = Snap([mk(1, 6.0, 0.0)], [(FV(0, 0, 0), 1.0, 1)]); snap.mode = "territories"; snap.dom_orders = {}
b = FakeBot(1, dom={"kind": "capture", "clear": [("turret", FV(1, 1, 0))], "pos": FV(0, 0, 0)})
plan(b, 0.0, 0.05, snap, None, None, None); assert b.old == 1, "clear branch kept"
b = FakeBot(1, dom={"kind": "capture", "clear": [], "pos": FV(0, 0, 0)}); snap.players = [mk(1, 4.0, 0.0)]
a = plan(b, 0.0, 0.05, snap, None, None, None)
assert b.old == 0 and a.forward == 1.0 and any("outside the capture radius" in n for n in b.notes), "approach left to dom_order"
# plain Territories (Halo 3 / Reach: no snap.dom_orders): never locked by Dominion's system
del snap.dom_orders; snap.mode = "territories"; snap.players = [mk(1, 0.3, 0.0)]
b = FakeBot(1, dom={"kind": "capture", "clear": [], "pos": FV(0, 0, 0)}); a = plan(b, 0, .05, snap, None, None, None)
assert b.old == 1 and a.forward == 1.0, "plain territories untouched"
# other modes keep the old path
snap.mode = "ctf"; b = FakeBot(1, dom={"kind": "capture", "clear": [], "pos": FV(0, 0, 0)}); plan(b, 0, .05, snap, None, None, None)
assert b.old == 1, "other modes untouched"
print("plan wiring ok")
