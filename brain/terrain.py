"""Real Blood Gulch terrain for the offline sim and the live brain, from the level mesh
(tools/heightmap.py): line of sight over the actual ground, cliffs and base roofs; ground height;
and paths (Dijkstra flow fields toward each goal, cached) over a 2-layer walkable grid, so a
base roof and the doorway under it are separate places.
"""
import math
import re

import numpy as np

from dataclasses import dataclass

from .core import Vec

EYE = 0.62       # standing eye height above the feet (world units, CE)
CHEST = 0.40
FEET_SLAB = 0.6  # a ray crossing the plane of a man's feet this close to him goes through the floor he stands on (_ray)
RAY_MAX = 1000.0  # u: no line of sight longer than this (_ray: its samples grow with its length)
WAIST = 0.30     # a handrail's height over the feet (the waist ray of clearance; drops through a rail: _build_nav)
RAIL_COST = 25.0  # a drop over a handrail: taken only when nothing else gets there (forbidding it cut off floors)
LID_COST = 400.0  # a drop into a column whose floor goes on at the ledge's height (_build_nav): beyond any detour
RAIL = (0.35, 0.45)  # heights over a ledge's floor where a wall is a handrail (0.25 u wall bins: clear of the step below)
NAV_CELL = 0.25  # path grid spacing = heightmap cell (the base ramps are 0.83 wide, doors about 1)
ROAD_COST = 0.4  # a vehicle edge on floor a person drove costs this much of its length: their lines are the roads
MAX_RISE = 0.18   # most a path may climb per grid edge, straight or diagonal: the base ramps rise
                  # 0.15 per cell; the 0.2-0.43 lips beside them stop a biped in game
MAX_DROP = 3.0    # dropping off a ledge is fine (no climbing back), up to this height
STAIR_RISE = 0.27  # a stair cell's climb per straight edge along its run (Terrain._stairs): Adrift's flag-deck stairs
                   # rise 0.04-0.16 a cell with risers of 0.20-0.26 between (h4_ca_warhouse heightmap)
BODY = (0.55, 0.85)  # steep faces at both heights above a floor (chest, head) wall it off; lower = steps
KNEE = 0.30          # an overhang is open here (_low_ceiling); a face from the floor up is not one
HEAD_TOP = 0.70      # the top of a standing head: a wall here or at EYE over a floor nobody walked (_low_ceiling)
WALL_COST = 1.5   # extra path cost to enter a cell touching a wall: a biped is ~0.25 wide, so paths
                  # keep to the middle of doors, ramps and gaps between the roof pillars
WALL_COST2 = 0.5  # the second ring (a cell touching a cell that touches a wall, ~0.5 u off it): a smaller push, so a
                  # path rounds a rock or a corner with room to spare instead of grazing it and catching (steering is
                  # never exact; one cell of clearance was not always enough)
LEDGE_COST = 0.8  # entering a cell on the lip of a drop (LEDGE_MIN or more): walking, not dropping. Paths ran along cliff
                  # lips and bots slid off, then had no way back up. A cost, not a wall: narrow walkways stay usable
LEDGE_MIN = 0.8   # u: a neighbour's floor this far below (or none at all) makes a cell a ledge lip
# Placed pieces face by face (add_faces): the steepest ground a body walks up, 50 degrees in Halo: Reach (the kit's
# Spartan and Elite bipeds, ground physics: maximum slope angle and uphill cutoff angle 50); a face steeper than that is a
# wall, flatter a ramp, flatter than FLAT_NZ a floor
WALK_NZ = math.cos(math.radians(50.0))
FLAT_NZ = math.cos(math.radians(2.0))
LAYER_GAP = 1.0   # tools/heightmap.py: walkable surfaces this far apart are floor levels of their own (a storey)


JUMP_RISE = 0.6   # CE: jump velocity 0.07/tick, gravity 0.003565/tick^2 -> 0.69 up; a margin kept
JUMP_REACH = 2.0  # gap length a running jump clears (about 2.9 at run speed; a margin kept)
JUMP_UP_REACH = 1.25   # how far out a ledge you jump up onto may be


@dataclass(frozen=True)
class JumpVec(Vec):
    """A waypoint across a jump: run to (sx, sy), jump, land at the point itself. A jump a person
    made also has his run-up spot (rx, ry): get there first, then run through the take-off."""
    sx: float = 0.0
    sy: float = 0.0
    rx: float = math.nan
    ry: float = math.nan


@dataclass(frozen=True)
class RouteVec(Vec):
    """The way into a route people walked (LiDAR): follow `path`, (x, y, z, flags) points, to its
    end, crouched where flagged ROUTE_CROUCH and jumping at points flagged ROUTE_JUMP."""
    path: tuple = ()


ROUTE_JUMP, ROUTE_CROUCH = 2, 4   # route point flags (the same bits as the jump and crouch buttons)

# memory each route-field cache may hold (walking, people's, jump, vehicle): a count limit (600 / 200 fields) let
# Forge World's 84 MB fields (11 million nodes) fill the PC, the brain out of memory at 834 s. A share of the PC's
# memory: the four together at most FIELD_SHARE of it, each at most FIELD_MAX (a 32 GB PC: 1.5 GB each, 16 GB: 0.8 GB,
# 8 GB: 0.4 GB; a field cache that is full recomputes the least recently used field when it is needed again)
FIELD_SHARE, FIELD_MAX = 0.2, 1.5e9


UNDER_LO, UNDER_HI = 0.6, 1.4   # u: a lone floor this far over where a person stood is no floor (walked_under)
UNDER_R = 1.5                   # u: the flat patch at that height round such a spot goes with it


def walked_under(floors, x0, y0, cell, walked):
    """People's walks ~1 u under a lone floor layer nobody walked on: the walked level wins (in place, on the
    (L, h, w) floors; returns the cells changed). The mesh merges surfaces closer than LAYER_GAP into one level at the
    higher: live Boneyard B27 / B28, a flat -9.0 slab over the -10.0 ground at the stair's foot (254.5-255.5, 54-55.5)
    held bots standing at -10 on it and sent them up a route into the stair's side (the owner's report 161417).
    Seeds: a walk with no layer within 0.3, the nearest layer UNDER_LO-UNDER_HI over it with no walk on it, and its
    four neighbours the same flat level (a crate's edge cell is not: its other side is the ground); from each, the flat
    patch at that level within UNDER_R. ponytail: one patch height per seed; a sloped phantom stays."""
    if not walked:
        return 0
    L, h, w = floors.shape
    by_cell = {}
    for ix, iy, iz in walked:
        x, y = ix * 0.25, iy * 0.25
        i, j = int((x - x0) / cell), int((y - y0) / cell)
        if 0 <= i < w and 0 <= j < h:
            by_cell.setdefault((j, i), []).append(iz * 0.1)
    def lone(j, i, z, zl):
        col = floors[:, j, i]
        fin = col[np.isfinite(col)]
        return (fin.size and not (np.abs(fin - z) <= 0.3).any() and not ((fin < z) & (fin > z - LAYER_GAP)).any()
                and not any(abs(v - zl) <= 0.3 for v in by_cell.get((j, i), ())))
    changed, r = set(), int(round(UNDER_R / cell))
    for (j, i), zs in by_cell.items():
        for z in zs:
            col = floors[:, j, i]
            over = [(float(v), k) for k, v in enumerate(col) if np.isfinite(v) and UNDER_LO <= v - z <= UNDER_HI]
            if not over:
                continue
            zl = min(over)[0]
            if not lone(j, i, z, zl):
                continue
            nb = [(j + a, i + b) for a, b in ((0, 1), (1, 0), (0, -1), (-1, 0))]
            if not all(0 <= jj < h and 0 <= ii < w and (np.abs(floors[:, jj, ii] - zl) <= 0.05).any() for jj, ii in nb):
                continue
            todo, seen = [(j, i)], {(j, i)}
            while todo:
                cj, ci = todo.pop()
                k = int(np.nanargmin(np.abs(floors[:, cj, ci] - zl)))
                floors[k, cj, ci] = z
                changed.add((cj, ci))
                for a, b in ((0, 1), (1, 0), (0, -1), (-1, 0)):
                    jj, ii = cj + a, ci + b
                    if (jj, ii) in seen or not (0 <= jj < h and 0 <= ii < w) or (jj - j) ** 2 + (ii - i) ** 2 > r * r:
                        continue
                    if (np.abs(floors[:, jj, ii] - zl) <= 0.05).any() and lone(jj, ii, z, zl):
                        seen.add((jj, ii))
                        todo.append((jj, ii))
    for j, i in changed:                             # (lowest first, as the rest of the nav reads them)
        floors[:, j, i] = np.sort(floors[:, j, i])
    return len(changed)


def field_budget(ram_bytes):
    """Bytes each of the four route-field caches may hold on a PC with this much memory (None: unknown)."""
    return FIELD_MAX if not ram_bytes else min(FIELD_MAX, FIELD_SHARE * ram_bytes / 4)


def total_ram():
    """This PC's physical memory in bytes (Windows: GlobalMemoryStatusEx), or None."""
    try:
        import ctypes

        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
        m = MEMORYSTATUSEX()
        m.dwLength = ctypes.sizeof(m)
        return m.ullTotalPhys if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)) else None
    except (AttributeError, OSError):
        return None


FIELD_BYTES = field_budget(total_ram())


def keep_field(cache, key, cost):
    """Cache a route field, the least recently used out first past FIELD_BYTES."""
    while cache and (len(cache) + 1) * cost.nbytes > FIELD_BYTES:
        cache.pop(next(iter(cache)))
    cache[key] = cost
    return cost


def cached_field(cache, key):
    """A cached route field, now the most recently used, or None."""
    cost = cache.pop(key, None)
    if cost is not None:
        cache[key] = cost
    return cost


def route_points(link):
    """A crouch or recorded route link's path as (x, y, z, flags): a crouch path is crouched all
    along; a route recorded with F6 (tools/lidar.py) keeps each point's own flags."""
    crouch = ROUTE_CROUCH if link.get("kind") == "crouch" else 0
    return [(q[0], q[1], q[2], int(q[3]) if len(q) > 3 else crouch) for q in link["path"]]


def jump_ways(link, T=None, apex=0.8):
    """"two-way" or "one-way", evaluated automatically: a gap you can jump back and forth (Lockdown's
    platforms) is two-way, a long drop you could never jump back up is one-way, and so is a climb
    no jump makes (a lift or launcher carried it, up more than 4 units). Two-way when someone has
    jumped it the other way ("back"), or when the way back climbs no more than 3/4 of the map's
    measured jump height (apex) and, for a running jump (1.2+ units), the level data has floor
    behind the landing to run up from."""
    if link.get("back", 0) > 0:
        return "two-way"
    if link.get("called") or link.get("ride") or link.get("painted"):   # a called jump / a man cannon ride / one the
        return "one-way"                                                    # the player drew (tools/paint.py) goes one way
    a, b = link["take_off"], link["land"]
    if b[2] - a[2] > 4.0:
        # up more than any jump climbs: a lift or a launcher carried it, and its way back is a fall into what pushes
        # up; a way down there is a person's own drop, a link of its own
        return "one-way"
    if link.get("apex") is not None and link["apex"] - max(a[2], b[2]) > 2.0 * apex:
        # an arc higher over both ends than a jump goes: a launcher (or a jetpack) carried it, and the way back has none
        # (Spire link 218, a person's 4.67 s flight 7.2 u over its take-off and 5.5 u over its landing, kept two-way:
        # every phase-3 attacker stood at its far end, live, 0 core carriers). A lift ride tops out at its landing: the
        # climb rule above
        return "one-way"
    if a[2] - b[2] > 0.75 * apex:                # the way back climbs more than a jump can
        return "one-way"
    xy = math.hypot(b[0] - a[0], b[1] - a[1])
    if xy >= 1.2 and T is not None:
        ux, uy = (b[0] - a[0]) / xy, (b[1] - a[1]) / xy
        if not all(T.ground_ok(b[0] + ux * k, b[1] + uy * k, b[2], drop=0.6) for k in (0.4, 0.8)):
            return "one-way"                     # no room to run up for the jump back
    return "two-way"


WALK_PACE = 4.0   # u/s: faster than a person runs or sprints (2.25 u/s a run): a link crossed quicker was carried


def walked_climb(link, apex=0.8):
    """A person's link no jump made and nothing carried: up more than twice the map's jump height (apex) and at most
    4 u (more: a lift, jump_ways), never higher on the way than where it ends (no arc), no steeper than a body walks
    (WALK_NZ) and at a walk's pace. Floor the level data lacks or has wrong (a ramp, stairs: the tracker reads the
    climb as air). Bots walk it point by point (a route, both ways), not as a jump: Boneyard's ramp under phase 1's
    territories (250.9, 81.8, -9.8) -> (249.5, 90.0, -6.0), a person's 3.4 s walk up 3.8 u, was a jump bots ran at and
    fell short of, then pushed at the ramp's side 10-29 s a time (live 20261105-022641: 8,090 attacker rows there, the
    phase lost both rounds). 107 such links on 32 maps and layouts, none a bot landed as a jump."""
    if link.get("kind") or link.get("ride") or link.get("called") or link.get("painted"):
        return False
    a, b = link["take_off"], link["land"]
    rise, xy = b[2] - a[2], math.hypot(b[0] - a[0], b[1] - a[1])
    if not 2.0 * apex < rise <= 4.0:
        return False
    top = max([q[2] for q in link.get("path") or []] + [a[2], b[2]])
    return (top <= b[2] + 0.15 and rise <= xy * math.tan(math.acos(WALK_NZ))
            and xy <= WALK_PACE * float(link.get("air_s") or 0.0))


def reverse_jump(link):
    """The way back of a two-way jump: take off where it lands, land where it took off, run up
    from behind the landing."""
    a, b = link["take_off"], link["land"]
    xy = math.hypot(b[0] - a[0], b[1] - a[1]) or 1.0
    run = [b[0] + (b[0] - a[0]) / xy * 0.8, b[1] + (b[1] - a[1]) / xy * 0.8, b[2]] if xy >= 1.2 else None
    return dict(take_off=list(b), land=list(a), runup=run)


def crouch_key(x, y, z):
    """Crouch floor at half-unit resolution (tools/lidar.py uses the same key)."""
    return (round(x * 2), round(y * 2), round(z))


def climb_ok(z_from, z_to):
    """One-way rule shared by paths and movement: climb gentle slopes, drop off ledges."""
    return -MAX_DROP <= z_to - z_from <= MAX_RISE


# --- three kinds of wall (owner, Nov 4: "windows get enemy info, fences still act as walls but can be shot through"):
# solid stops walking, sight and shots; a window (glass) walking and shots (unless it breaks), not sight; a fence
# (grate, chain-link, railing, mesh, bars) walking only. By a level's shader / a Forge piece's face material / a piece's
# name (tools/lidar_walls.py reads the maps' collision; Terrain.set_wall_kinds)
WINDOW_WORDS = ("glass", "window", "energy_shield")
FENCE_WORDS = ("fence", "grate", "grating", "chain", "chainlink", "mesh", "rail", "railing", "bar", "grill", "grille",
               "lattice", "cage")
# a glass-named thing that is no pane you see through: a light's cover, metal under a glass floor, a frosted /
# high-opacity panel, a screen, a piece "without glass" (INFERRED from the names: Reach glass_light_vertical_a,
# Guardian centerplatform_metalunderglass, H4 monitor_glass, H2A fw_stone_wall_2x1_window_noglass)
NOT_CLEAR = ("light", "lamp", "metal", "frame", "trim", "opaq", "opacity", "frosted", "underglass", "monitor", "noglass")


def wall_kind(name):
    """"window" / "fence" / None (solid) by a shader's, a collision material's or a piece's name (its last path part,
    words split on _ - . space and digits, a plural's s dropped). A wall window piece (Reach ff_wall_2x2_window) is
    no window: its pane is its own energy faces (by material)."""
    base = str(name).replace("/", "\\").split(",")[0].rsplit("\\", 1)[-1].lower()
    words = {w[:-1] if len(w) > 3 and w.endswith("s") else w for w in re.split(r"[_\-. \d]+", base)}
    if any(w in base for w in ("glass", "energy_shield")) or "window" in words and not words & {"wall", "arena"}:
        return None if any(w in base for w in NOT_CLEAR) else "window"
    if any(w in words for w in FENCE_WORDS) or "chainlink" in base or "railing" in base:
        return "fence"
    return None


def breaks(name):
    """A window that breaks, by its material's name (Reach / H4 "brittle_glass_*"; "unbreakable" says otherwise)."""
    n = str(name).lower()
    return "brittle" in n and "unbreakable" not in n


def face_cells(poly, cells, flag=False):
    """Quarter-unit cells (round(x * 4), round(y * 4)) -> (z low, z high, flag) a polygon [(x, y, z)] covers, sampled 0.1
    u apart (tools/glass_mask.py's rule)."""
    for t in range(1, len(poly) - 1):
        a, b, c = poly[0], poly[t], poly[t + 1]
        n = max(1, int(max(math.dist(a, b), math.dist(b, c), math.dist(c, a)) / 0.1))
        for i in range(n + 1):
            for j in range(n + 1 - i):
                u, w = i / n, j / n
                q = [a[k] + (b[k] - a[k]) * u + (c[k] - a[k]) * w for k in range(3)]
                key = (round(q[0] * 4), round(q[1] * 4))
                lo, hi, f = cells.get(key, (q[2], q[2], False))
                cells[key] = (min(lo, q[2]), max(hi, q[2]), f or bool(flag))


def is_flat(poly):
    """A deck (normal within 45 degrees of up), not a wall (Newell's normal)."""
    n = [0.0, 0.0, 0.0]
    for i in range(len(poly)):
        p, q = poly[i - 1], poly[i]
        n[0] += (p[1] - q[1]) * (p[2] + q[2])
        n[1] += (p[2] - q[2]) * (p[0] + q[0])
        n[2] += (p[0] - q[0]) * (p[1] + q[1])
    return abs(n[2]) > 0.7 * math.sqrt(n[0] ** 2 + n[1] ** 2 + n[2] ** 2 + 1e-12)


def kinds_out(cells):
    """{kind: {cell: (lo, hi, breaks)}} -> wall_kinds.json lists ([x4, y4, lo, hi(, breaks)])."""
    return {k: [[x, y, round(lo, 2), round(hi, 2)] + ([int(f)] if k.startswith("window") else [])
                for (x, y), (lo, hi, f) in sorted(c.items())] for k, c in cells.items()}


def piece_wall_kinds(blocks):
    """The windows and fences among placed pieces (brain/forge.py block(): (pos, (forward, left, up), min, max, name,
    faces [(material, [x, y, z, ...])])), as wall_kinds.json lists: each face by its material (H4 glass / grate, Reach
    brittle_glass, energy panes), else by the piece's name (railings; H2A fw_stone_dec_glass_*). A piece known by its
    box alone (H4 fw_unsc_c_railing_*) by its name: its box's four sides."""
    cells = {k: {} for k in ("window", "fence", "window_floor", "fence_floor")}
    for b in blocks:
        if len(b) < 5:
            continue
        (px, py, pz), (f, l, u), whole = b[0], b[1], wall_kind(b[4])
        faces = b[5] if len(b) > 5 and b[5] else []
        if not faces and whole:
            (x0, y0, z0), (x1, y1, z1) = b[2], b[3]
            faces = [(b[4], [x0, y0, z0, x1, y0, z0, x1, y0, z1, x0, y0, z1]), (b[4], [x1, y0, z0, x1, y1, z0, x1, y1, z1, x1, y0, z1]),
                     (b[4], [x1, y1, z0, x0, y1, z0, x0, y1, z1, x1, y1, z1]), (b[4], [x0, y1, z0, x0, y0, z0, x0, y0, z1, x0, y1, z1])]
        for mat, flat in faces:
            k = wall_kind(mat) or whole
            if k is None:
                continue
            poly = [(px + x * f[0] + y * l[0] + z * u[0], py + x * f[1] + y * l[1] + z * u[1],
                     pz + x * f[2] + y * l[2] + z * u[2]) for x, y, z in zip(flat[0::3], flat[1::3], flat[2::3])]
            if len(poly) >= 3:
                face_cells(poly, cells[k + "_floor" if is_flat(poly) else k], k == "window" and breaks(mat))
    return kinds_out(cells)


class Terrain:
    lid_rule = True   # drops into a column whose floor goes on at our own height cost LID_COST (_build_nav; tests: off)

    def __init__(s, npz_path, bases, extra_floor=(), blocks=(), slit_rule=False, window=None, stair_rule=False,
                 walked=None):
        # walked: cells people stood on (LiDAR walk keys, maps.terrain people_walked): a low ceiling over floor nobody
        # walked is no floor (_low_ceiling)
        s.walked_cells = walked
        # slit_rule: no drops into slits no body fits (_build_nav). Reach only: a crack rule on Halo CE cut real drops
        # (Damnation, Lockdown); Reach's 0.25 u cells on its larger maps checked on Countdown stair_rule: stairs climb
        # STAIR_RISE a cell along their run (_stairs). Halo 4 / H2A levels (Adrift)
        s.stair_rule = stair_rule
        # window: (x0, y0, x1, y1), only that part of the level (brain/variants.py: a Forge map variant's own area,
        # grown as players reach its edges). The cells keep the whole level's grid; s.full is the whole level's extent.
        s.slit_rule = slit_rule
        d = np.load(npz_path)
        s.x0, s.y0, s.cell = float(d["x0"]), float(d["y0"]), float(d["cell"])
        top = d["top"]
        h, w = top.shape
        s.full = (s.x0, s.y0, s.x0 + w * s.cell, s.y0 + h * s.cell)
        cj, ci = slice(0, h), slice(0, w)
        if window is not None:
            i0, i1 = max(0, int((window[0] - s.x0) / s.cell)), min(w, int(math.ceil((window[2] - s.x0) / s.cell)))
            j0, j1 = max(0, int((window[1] - s.y0) / s.cell)), min(h, int(math.ceil((window[3] - s.y0) / s.cell)))
            if i0 < i1 and j0 < j1:
                cj, ci = slice(j0, j1), slice(i0, i1)
                s.x0, s.y0 = s.x0 + i0 * s.cell, s.y0 + j0 * s.cell
        cut = np.ascontiguousarray                     # (a copy only when cut down: the whole level's arrays go)
        s.top = np.nan_to_num(top[cj, ci], nan=1e3)  # outside the level counts as a wall
        s.low = cut(d["low"][cj, ci])
        s.floors = cut(d["floors"][:, cj, ci])        # (L, h, w) walkable floor levels per cell, lowest first
        s.walls = cut(d["walls"][cj, ci])             # per cell: bit k = a steep face at wall_z0 + k * wall_bin
        s.wall_z0, s.wall_bin = float(d["wall_z0"]), float(d["wall_bin"])
        s.h, s.w = s.top.shape
        s.window = (s.x0, s.y0, s.x0 + s.w * s.cell, s.y0 + s.h * s.cell)
        s.bases = bases           # [(center Vec, radius, roof z)]
        s.blocked = set()         # learned sight lines the level data calls open (learn_blocked)
        s.stuck = set()           # learned spots bots could not walk through (learn_stuck)
        s.stuck_votes = {}
        s.under_fixed = walked_under(s.floors, s.x0, s.y0, s.cell, walked)   # people's walks under a lone floor
        s._add_floor(extra_floor)
        s.slope = None            # (L, h, w) a ramp floor's slope, rise over run (add_faces); None: no ramps placed
        s.piece = None            # (L, h, w) the floor level is a placed piece's (add_faces); None: none placed
        s.blocks = s.add_blocks(blocks)
        s._build_nav()
        s._fields = {}

    def clearance(s, p, yaw, far=3.0, height=CHEST, waist=True):
        """How far a body can go from p along yaw (flat) before a wall at `height` over its feet: a bot's 'crosshair
        distance'. Walls are the level's and the placed objects' (add_blocks). far when nothing is in the way.
        waist=False: that height only (a wall at eye height is one no jump clears)."""
        n = max(2, int(far / (s.cell * 0.8)))
        t = np.linspace(0.0, far, n)[1:]
        xs, ys = p.x + math.cos(yaw) * t, p.y + math.sin(yaw) * t
        j, i = s._ij(xs, ys)
        hit = s._wall_bit(s.walls[j, i], np.full(len(t), p.z + height))
        # and at the waist: a handrail under the chest ray (the player: bots ran straight into waist-high rails), unless
        # a floor there is a step or a ramp it walks onto (a stair's riser has wall bits at that height too) The waist
        # ray follows the floor along the way (up a stair it rises step by step; held at the start height, 4% of
        # Countdown's stair steps read as walls)
        if waist and s._wall_bit(s.walls[j, i], np.full(len(t), p.z + WAIST)).any():
            h = p.z
            for k in range(len(t)):
                col = s.floors[:, j[k], i[k]]
                with np.errstate(invalid="ignore"):
                    on = col[(col >= h - 0.6) & (col <= h + 0.2)]     # the floor carrying on from the last sample
                up = on[on > h + 0.05]
                if s._wall_bit(s.walls[j[k], i[k]][None], np.array([h + WAIST]))[0] and not up.size:
                    hit[k] = True
                    break
                if on.size:
                    h = float(up.max()) if up.size else float(on[np.argmin(np.abs(on - h))])
        k = int(np.argmax(hit)) if hit.any() else -1
        return float(t[k]) if k >= 0 else far

    def add_blocks(s, boxes):
        """Stamp solid placed objects (brain/placed.py: (pos, (forward, left, up), min, max, name), the box in the
        object's own axes) into the wall heights: every cell whose centre lies inside a box at a wall bin's height gets
        that bin set. The nav then treats the cells as walled at body height and sight lines stop there. Returns how
        many cells were stamped. A piece given with its faces as a sixth item goes in face by face instead (add_faces:
        floors, ramps and walls by their angle; s.face_kinds counts them)."""
        faced = [b for b in boxes if len(b) > 5 and b[5]]
        s.face_kinds = s.add_faces([(b[0], b[1], b[5]) for b in faced]) if faced else {}
        boxes = [b[:5] for b in boxes if not (len(b) > 5 and b[5])]
        if not boxes:
            return 0
        words = s.walls.shape[-1]
        nbins = 64 * words
        cells = 0
        for pos, (f, l, u), lo, hi, _name in boxes:
            corners = [(pos[0] + a * f[0] + b * l[0] + c * u[0], pos[1] + a * f[1] + b * l[1] + c * u[1],
                        pos[2] + a * f[2] + b * l[2] + c * u[2])
                       for a in (lo[0], hi[0]) for b in (lo[1], hi[1]) for c in (lo[2], hi[2])]
            xs, ys, zs = zip(*corners)
            i0, i1 = int((min(xs) - s.x0) / s.cell), int((max(xs) - s.x0) / s.cell) + 1
            j0, j1 = int((min(ys) - s.y0) / s.cell), int((max(ys) - s.y0) / s.cell) + 1
            b0 = int(math.floor((min(zs) - s.wall_z0) / s.wall_bin))
            b1 = int(math.floor((max(zs) - s.wall_z0) / s.wall_bin))
            i0, j0, b0 = max(i0, 0), max(j0, 0), max(b0, 0)
            i1, j1, b1 = min(i1, s.w), min(j1, s.h), min(b1, nbins - 1)
            if i0 >= i1 or j0 >= j1 or b0 > b1:
                continue
            jj, ii, bb = np.meshgrid(np.arange(j0, j1), np.arange(i0, i1), np.arange(b0, b1 + 1), indexing="ij")
            dx = s.x0 + (ii + 0.5) * s.cell - pos[0]
            dy = s.y0 + (jj + 0.5) * s.cell - pos[1]
            dz = s.wall_z0 + (bb + 0.5) * s.wall_bin - pos[2]
            a = dx * f[0] + dy * f[1] + dz * f[2]
            b = dx * l[0] + dy * l[1] + dz * l[2]
            c = dx * u[0] + dy * u[1] + dz * u[2]
            pad = 0.5 * s.cell
            inside = ((a >= lo[0] - pad) & (a <= hi[0] + pad) & (b >= lo[1] - pad) & (b <= hi[1] + pad)
                      & (c >= lo[2] - 0.5 * s.wall_bin) & (c <= hi[2] + 0.5 * s.wall_bin))
            if not inside.any():
                continue
            jj, ii, bb = jj[inside], ii[inside], bb[inside]
            np.bitwise_or.at(s.walls, (jj, ii, bb >> 6), np.left_shift(np.uint64(1), (bb & 63).astype(np.uint64)))
            cells += len(np.unique(jj * s.w + ii))
        return cells

    def add_faces(s, pieces):
        """Placed pieces face by face. pieces: [(pos, (forward, left, up), polygons)], polygons [(material, [x, y, z...])]
        in the piece's own axes, outward by their winding (tools/object_shapes.py faces). By its angle a face is a floor
        (flatter than FLAT_NZ), a ramp (up to the steepest walkable slope, WALK_NZ, and running down at least a cell: a
        block's 0.1 u wide 11 degree edge lip goes with the floor it edges) or a wall (steeper, or facing down). Floors
        and ramps become walkable floor levels of the cells under them (a ramp's keep its slope, s.slope, so the nav
        climbs it: _build_nav; tools/lidar.py marks them); walls set the wall bins they pass through, as the level's
        steep faces do (tools/heightmap.py). Floor a piece buries (the ground under a block: between a face of the piece
        below it facing down and one above it facing up) is floor no more. Returns {kind: faces}."""
        kinds = {"floor": 0, "ramp": 0, "wall": 0}
        tri, nzs, tans, who = [], [], [], []        # world triangles, their face's normal z and slope, their piece
        for k, (pos, axes, polys) in enumerate(pieces):
            M, p0 = np.asarray(axes, float), np.asarray(pos, float)
            for _mat, flat in polys:
                P = np.asarray(flat, float).reshape(-1, 3) @ M + p0
                t = np.stack([np.repeat(P[:1], len(P) - 2, 0), P[1:-1], P[2:]], 1)      # the polygon as a fan
                n = np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0]).sum(0)
                if np.linalg.norm(n) < 1e-9:
                    continue
                n = n / np.linalg.norm(n)
                h = math.hypot(n[0], n[1])
                run = np.ptp(P[:, :2] @ (n[:2] / h)) if h > 1e-9 else 0.0                # its length downhill
                ramp = WALK_NZ <= n[2] < FLAT_NZ and run >= s.cell
                kinds["ramp" if ramp else "floor" if n[2] >= WALK_NZ else "wall"] += 1
                tri.append(t), nzs.append(np.full(len(t), n[2])), who.append(np.full(len(t), k))
                tans.append(np.full(len(t), h / n[2] if ramp else 0.0))
        if not tri:
            return kinds
        tri, nz, tan, who = np.concatenate(tri), np.concatenate(nzs), np.concatenate(tans), np.concatenate(who)
        s._face_walls(tri[nz < WALK_NZ])
        # every face not upright, seen from above: the cells under it and its height there
        flat = np.nonzero(np.abs(nz) > 1e-3)[0]
        tk, cj, ci, cz = s._face_cells(tri[flat])
        tk = flat[tk]
        cell = cj * s.w + ci
        top = s.top.reshape(-1)
        np.maximum.at(top, cell, cz)
        s.top = top.reshape(s.top.shape)
        walk = nz[tk] >= WALK_NZ
        cells = np.unique(cell)
        old = s.floors[:, cells // s.w, cells % s.w]
        if s.slope is None:
            s.slope = np.zeros(s.floors.shape, np.float32)
            s.piece = np.zeros(s.floors.shape, bool)   # a floor level that is a placed piece's (power positions)
        oslope = s.slope[:, cells // s.w, cells % s.w]
        opiece = s.piece[:, cells // s.w, cells % s.w]
        lv, col = np.nonzero(np.isfinite(old))
        q_c = np.r_[cells[col], cell[walk]]
        q_z = np.r_[old[lv, col], cz[walk]]
        q_t = np.r_[oslope[lv, col], tan[tk][walk]]   # a ramp's slope, rise over run (0: a floor)
        q_p = np.r_[opiece[lv, col], np.ones(int(walk.sum()), bool)]
        # buried: in a cell, between a face of one piece below facing down and the next face above facing up
        key = lambda c, z: c * 4096.0 + (np.clip(z, -2000.0, 2000.0) + 2048.0)
        probe = key(q_c, q_z + 0.05)
        buried = np.zeros(len(q_c), bool)
        for k in np.unique(who[tk]):
            m = who[tk] == k
            order = np.argsort(key(cell[m], cz[m]))
            sk, sc, sup = key(cell[m], cz[m])[order], cell[m][order], nz[tk][m][order] > 0
            a = np.searchsorted(sk, probe, side="right")
            b = a - 1
            above = (a < len(sk)) & (sc[np.minimum(a, len(sk) - 1)] == q_c) & sup[np.minimum(a, len(sk) - 1)]
            below = (b >= 0) & (sc[np.maximum(b, 0)] == q_c) & ~sup[np.maximum(b, 0)]
            buried |= above & below
        q_c, q_z, q_t, q_p = q_c[~buried], q_z[~buried], q_t[~buried], q_p[~buried]
        # the cells' floor levels again: a new level where a surface is LAYER_GAP over the last (tools/heightmap.py
        # stack_floors), each at its highest surface, with that surface's slope and whether it is a piece's
        order = np.lexsort((q_z, q_c))
        q_c, q_z, q_t, q_p = q_c[order], q_z[order], q_t[order], q_p[order]
        start = np.r_[True, q_c[1:] != q_c[:-1]]
        cum = np.cumsum(np.r_[False, np.diff(q_z) >= LAYER_GAP] & ~start)
        level = cum - cum[np.nonzero(start)[0]][np.cumsum(start) - 1]
        last = np.r_[(q_c[1:] != q_c[:-1]) | (level[1:] != level[:-1]), True]
        need = int(level.max()) + 1 if len(level) else 0
        if need > s.floors.shape[0]:
            grow = (need - s.floors.shape[0],) + s.floors.shape[1:]
            s.floors = np.concatenate([s.floors, np.full(grow, np.nan, s.floors.dtype)])
            s.slope = np.concatenate([s.slope, np.zeros(grow, s.slope.dtype)])
            s.piece = np.concatenate([s.piece, np.zeros(grow, bool)])
        s.floors[:, cells // s.w, cells % s.w] = np.nan
        s.slope[:, cells // s.w, cells % s.w] = 0.0
        s.piece[:, cells // s.w, cells % s.w] = False
        s.floors[level[last], q_c[last] // s.w, q_c[last] % s.w] = q_z[last]
        s.slope[level[last], q_c[last] // s.w, q_c[last] % s.w] = q_t[last]
        s.piece[level[last], q_c[last] // s.w, q_c[last] % s.w] = q_p[last]
        return kinds

    def _face_walls(s, tri):
        """The wall bins faces tri (T, 3, 3) pass through, each sampled densely just inside its edges (as
        tools/heightmap.py rasterize_walls). Pieces over the level's wall range get more bins."""
        if not len(tri):
            return
        words = int(math.ceil((float(tri[..., 2].max()) - s.wall_z0) / s.wall_bin / 64.0))
        if words > s.walls.shape[-1]:
            s.walls = np.concatenate([s.walls, np.zeros(s.walls.shape[:2] + (words - s.walls.shape[-1],), np.uint64)], -1)
        pts = []
        for a, b, c in tri:
            k = int(max(np.linalg.norm(b - a), np.linalg.norm(c - a), np.linalg.norm(c - b)) / (s.cell * 0.4)) + 2
            u, t = np.meshgrid(np.linspace(0.01, 0.98, k), np.linspace(0.01, 0.98, k))
            keep = u + t <= 0.99
            pts.append(a + np.outer(u[keep], b - a) + np.outer(t[keep], c - a))
        pts = np.concatenate(pts)
        j = np.floor((pts[:, 1] - s.y0) / s.cell).astype(int)
        i = np.floor((pts[:, 0] - s.x0) / s.cell).astype(int)
        bit = np.floor((pts[:, 2] - s.wall_z0) / s.wall_bin).astype(int)
        ok = (j >= 0) & (j < s.h) & (i >= 0) & (i < s.w) & (bit >= 0) & (bit < 64 * s.walls.shape[-1])
        np.bitwise_or.at(s.walls, (j[ok], i[ok], bit[ok] >> 6), np.left_shift(np.uint64(1), (bit[ok] & 63).astype(np.uint64)))

    def _face_cells(s, tri):
        """The cell centres under faces tri (T, 3, 3) seen from above: (face index, cell j, cell i, the face's height
        there) arrays."""
        out = []
        for k, ((ax, ay, az), (bx, by, bz), (cx, cy, cz)) in enumerate(tri):
            den = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
            if abs(den) < 1e-12:
                continue
            i0 = max(int(math.ceil((min(ax, bx, cx) - s.x0) / s.cell - 0.5)), 0)
            i1 = min(int(math.floor((max(ax, bx, cx) - s.x0) / s.cell - 0.5)), s.w - 1)
            j0 = max(int(math.ceil((min(ay, by, cy) - s.y0) / s.cell - 0.5)), 0)
            j1 = min(int(math.floor((max(ay, by, cy) - s.y0) / s.cell - 0.5)), s.h - 1)
            if i0 > i1 or j0 > j1:
                continue
            J, I = np.mgrid[j0:j1 + 1, i0:i1 + 1]
            X, Y = s.x0 + (I + 0.5) * s.cell, s.y0 + (J + 0.5) * s.cell
            l1 = ((by - cy) * (X - cx) + (cx - bx) * (Y - cy)) / den
            l2 = ((cy - ay) * (X - cx) + (ax - cx) * (Y - cy)) / den
            l3 = 1.0 - l1 - l2
            inside = (l1 >= -1e-6) & (l2 >= -1e-6) & (l3 >= -1e-6)
            if inside.any():
                out.append((np.full(int(inside.sum()), k), J[inside], I[inside], (l1 * az + l2 * bz + l3 * cz)[inside]))
        if not out:
            return (np.zeros(0, int),) * 3 + (np.zeros(0),)
        return tuple(np.concatenate(v) for v in zip(*out))

    def _add_floor(s, cells):
        """Floor people walked that the level data lacks, reviewed and approved (tools/review_moves.py,
        maps/<map>/moves_review.json: crate tops, ledges, rails): each (x, y, z) becomes a floor level of its
        cell unless the cell has one within 0.3 already; a cell with no free level adds one for all."""
        s.walked_ok = [tuple(c) for c in cells if s.inside(c[0], c[1])]
        for x, y, z in s.walked_ok:
            j, i = (int(v) for v in s._ij(np.asarray(x), np.asarray(y)))
            col = s.floors[:, j, i]
            fin = col[np.isfinite(col)]
            if fin.size and np.min(np.abs(fin - z)) <= 0.3:
                # the level's floor within 0.3: the walked height is the truth there (Spire's core ramp rises 0.1 a
                # half unit; snapped to the floor under it, its rise was lost and no way onto the plateau existed)
                l = int(np.nanargmin(np.abs(col - z)))
                if abs(float(col[l]) - z) > 0.02:
                    s.floors[l, j, i] = z
                continue
            if fin.size == s.floors.shape[0]:
                s.floors = np.concatenate([s.floors, np.full((1,) + s.floors.shape[1:], np.nan, s.floors.dtype)])
            vals = np.sort(np.append(fin, z))
            s.floors[:, j, i] = np.nan
            s.floors[:len(vals), j, i] = vals

    def inside(s, x, y):
        """Is (x, y) in this terrain's window (the whole level unless a map variant's area: brain/variants.py)? Things
        outside it (a learned spot, a link, a spawn) are left out, not pinned to its edge."""
        return s.window[0] <= x < s.window[2] and s.window[1] <= y < s.window[3]

    # --- height queries
    def _ij(s, x, y):
        return (np.clip(((y - s.y0) / s.cell).astype(int), 0, s.h - 1),
                np.clip(((x - s.x0) / s.cell).astype(int), 0, s.w - 1))

    def top_at(s, x, y):
        j, i = s._ij(np.asarray(x), np.asarray(y))
        return s.top[j, i]

    def ground(s, x, y):
        """Lowest surface (canyon floor, inside a base the flag-room floor)."""
        j, i = s._ij(np.asarray(x), np.asarray(y))
        z = s.low[j, i]
        return float(z) if np.isfinite(z) else 0.0

    def base_of(s, p):
        for k, (c, r, roof) in enumerate(s.bases):
            if math.hypot(p.x - c.x, p.y - c.y) < r:
                return k
        return -1

    # --- line of sight: eye of a to chest of b, over the real top surface
    @staticmethod
    def _blk_key(a, b):
        return (int(a.x // 2), int(a.y // 2), int(a.z // 2), int(b.x // 2), int(b.y // 2), int(b.z // 2))

    BLOCK_VOTES, BLOCK_MAX = 3, 300   # dry episodes per line before it counts; keys per map, then stop

    def learn_blocked(s, a, b):
        """Bots kept shooting from a at b, on target, and nothing landed: the level data misses
        something solid between (geometry the BSP mesh does not show). Both directions, 2-unit
        cells; saved per map (maps.save_learned). Only on the third dry episode of the same line
        and up to BLOCK_MAX keys: one episode a line is a miss streak as often as a wall, and the
        blocks hide whole cell pairs from every bot (H2A Lockdown 246 -> 720 keys in one night,
        kills 80 -> 40 a game)."""
        key = s._blk_key(a, b)
        if key in s.blocked or len(s.blocked) >= s.BLOCK_MAX:
            return
        votes = s.__dict__.setdefault("blocked_votes", {})
        votes[key] = votes.get(key, 0) + 1
        if votes[key] < s.BLOCK_VOTES:
            return
        s.blocked.add(key)
        s.blocked.add(s._blk_key(b, a))

    STUCK_RADIUS, STUCK_COST = 0.35, 400.0   # cost beyond any detour: a wall unless there is no other way
    STUCK_HALO, STUCK_HALO_COST = 0.8, 6.0   # a soft ring round the disk: an odd-shaped rock is bigger than one cell, and
                                             # a path that skirts the disk's edge still scrapes the rest of it
    CORNER_RULE = True   # no diagonal edge past a blocked corner (_build_nav, can_step): the body clips the corner cell
    STUCK_MAX = 300                           # per map: more means something else is wrong; stop learning
    STUCK_VOTES, STUCK_BOTS, STUCK_PER_MATCH = 3, 2, 10   # bot reports: 3 of a spot from 2+ bots; 10 new a match

    def learn_stuck(s, p, sure=False, who=None):
        """A bot pushed toward p, alone, and did not move, or fell off the map there: the level
        data may be wrong at p (solid scenery the mesh lacks: rocks, trees, teleporter frames;
        floor that is not there). On the second report of the same spot (sure: from the saved
        file) paths go around a small disk at p (a cost, not a wall: a wrong guess only makes a
        detour). Not inside a learned disk (on Lockout's narrow walkways that cascaded until
        bots could not move), at most STUCK_MAX per map. Saved per map."""
        key = (round(p.x, 1), round(p.y, 1), round(p.z, 1))
        if key in s.stuck or len(s.stuck) >= s.STUCK_MAX or not s.inside(p.x, p.y):
            return
        # a spot people stood on is no wall (LiDAR walk cells, maps.terrain: people_walked): a bot that stalled there
        # met a crowd or a give-way. Spire's 300 learned spots, the cap, were 228 cells the player stood in
        # (Construct 213 of 232): detours round open floor, and the stall at the spire's foot that held the Elites
        # off the core carrier (live 094415, 470-520 s) could never be learned
        pw = getattr(s, "people_walked", None)
        if pw and any((round(p.x / 0.25), round(p.y / 0.25), round(p.z / 0.1) + k) in pw for k in range(-3, 4)):
            return
        if any(abs(k[0] - p.x) < 0.5 and abs(k[1] - p.y) < 0.5 and abs(k[2] - p.z) < 1 for k in s.stuck):
            return
        if not sure and who is not None:
            # a piece the forge reader found no shape for sits right here (forge.missing_near): known-bad data, so the
            # report needs no vote
            try:
                from .forge import missing_near
                sure = bool(missing_near(p))
            except Exception:
                pass
        if not sure:
            vote = (round(p.x * 2), round(p.y * 2), round(p.z))
            if who is None:                      # LiDAR / simulator tools: the second report
                s.stuck_votes[vote] = s.stuck_votes.get(vote, 0) + 1
                if s.stuck_votes[vote] < 2:
                    return
            else:
                # a bot in a live or simulated match: one bot's 1.2 s without progress is as often a
                # crowd, a give-way or a guard stop as scenery; learned spots grew 65 -> 118 in two
                # Lockdown games. The third report from two or more bots, 10 new a match
                votes = s.__dict__.setdefault("bot_votes", {})
                n, bots = votes.get(vote, (0, frozenset()))
                n, bots = n + 1, bots | {who}
                votes[vote] = (n, bots)
                if n < s.STUCK_VOTES or len(bots) < s.STUCK_BOTS or getattr(s, "stuck_new", 0) >= s.STUCK_PER_MATCH:
                    return
                s.stuck_new = getattr(s, "stuck_new", 0) + 1
        s.stuck.add(key)
        s._stuck_cost(key, (s.graph_to, getattr(s, "graph_jump_to", None), getattr(s, "graph_h_to", None)))
        s._fields.clear()
        getattr(s, "_jfields", {}).clear()
        getattr(s, "_hfields", {}).clear()

    # Trapped spots (learn_trap) are walls, and permanent, so they are checked, not trusted:
    TRAP_VERSION = 2
    TRAP_PER_MATCH = 10            # new marks a match (loading the saved file does not count): a cascade stops here
    TRAP_AREA_R, TRAP_AREA_MAX = 2.0, 6   # and no more than this many marks within that radius (one rock, not a wall of them)
    TRAP_STRIKES = 2               # bots seen RUNNING through a marked spot (trap_walk): the mark was wrong, it goes
    TRAP_PASS_SPEED = 1.0          # u/s: a bot moving this fast inside a mark's disk is not stopped by it (a run is 2.25)
    TRAP_TTL_DAYS = 21.0           # a mark nobody has confirmed (n == 1: one report) and nothing has touched this long goes
    TRAP_WARN = 150                # a map with more marks than this prints a warning on load: something is marking too much

    def _trap_meta(s):
        """{(x, y, z): {"n": reports, "first": t, "last": t, "strikes": n}} of the marks (learn_trap)."""
        return s.__dict__.setdefault("trapped", {})

    def learn_trap(s, p, meta=None):
        """A bot was trapped at p (it ran into a wall or a rock, or under an object, and stood pushing): mark the spot as
        a wall for good, at once, without learn_stuck's votes (3 reports from 2 bots). The paths price it as a wall
        (BARRIER_COST: round it while any way round exists, through it only when there is none), the look ahead takes no
        straight line over it (_straight_ok), and save_traps / load_traps keep it for every later match.
        Checked, not trusted (a wrong wall is as permanent as a right one): TRAP_PER_MATCH new marks a match and
        TRAP_AREA_MAX in any TRAP_AREA_R (a cascade), spots people walked and the STUCK_MAX cap (learn_stuck) still stand,
        a report beside an existing mark confirms it (n + 1: a real wall keeps catching bots that come near), and
        trap_walk drops a mark bots run through. meta: a saved mark's counts (load_traps), not new. True if new."""
        import time
        key = (round(p.x, 1), round(p.y, 1), round(p.z, 1))
        trapped = s._trap_meta()
        near = next((k for k in trapped if abs(k[0] - p.x) < 0.5 and abs(k[1] - p.y) < 0.5 and abs(k[2] - p.z) < 1), None)
        if near is not None:
            if meta is None:
                trapped[near]["n"] += 1
                trapped[near]["last"] = time.time()
            return False
        if key in s.stuck:
            if meta is None:
                return False
            # a saved mark that came in as an ordinary stuck spot first (maps.py loads nav_learned.json before this):
            # priced as a wall from here on, with its counts, strikes and expiry
            graphs = (s.graph_to, getattr(s, "graph_jump_to", None), getattr(s, "graph_h_to", None))
            s._stuck_cost(key, graphs, sign=-1.0)
            trapped[key] = dict(meta)
            s._stuck_cost(key, graphs)
            s._fields.clear()
            getattr(s, "_jfields", {}).clear()
            getattr(s, "_hfields", {}).clear()
            return True
        if meta is None:
            if s.__dict__.get("trap_new", 0) >= s.TRAP_PER_MATCH:
                return False
            if sum(1 for k in trapped if (k[0] - p.x) ** 2 + (k[1] - p.y) ** 2 <= s.TRAP_AREA_R ** 2) >= s.TRAP_AREA_MAX:
                return False
        now = time.time()
        trapped[key] = {"n": 1, "first": now, "last": now, "strikes": 0} if meta is None else dict(meta)
        s.learn_stuck(p, sure=True)            # (after the mark is filed: _stuck_cost reads it to price this one as a wall)
        if key not in s.stuck:
            trapped.pop(key, None)
            return False
        if meta is None:
            s.trap_new = s.__dict__.get("trap_new", 0) + 1
        return True

    def forget_trap(s, key):
        """Take a mark off for good: its cost off the graphs, out of the stuck set, and out of the file at the next save."""
        trapped = s._trap_meta()
        if key not in trapped:
            return False
        s._stuck_cost(key, (s.graph_to, getattr(s, "graph_jump_to", None), getattr(s, "graph_h_to", None)), sign=-1.0)
        trapped.pop(key)                       # (after the cost: _stuck_cost reads the mark to know it priced a wall)
        s.stuck.discard(key)
        s.__dict__.setdefault("trap_gone", set()).add(key)
        s._fields.clear()
        getattr(s, "_jfields", {}).clear()
        getattr(s, "_hfields", {}).clear()
        return True

    def trap_walk(s, p, speed):
        """A bot stands at p moving `speed` u/s: inside a mark's disk at a run it is not a wall. A strike on that mark
        (once in 10 s); TRAP_STRIKES of them (counted across matches: they are saved) and it is forgotten. True when a
        strike was given (save_traps then)."""
        import time
        trapped = s.__dict__.get("trapped")
        if not trapped or speed < s.TRAP_PASS_SPEED:
            return False
        r2, now = s.STUCK_RADIUS ** 2, time.time()
        for key, m in trapped.items():
            if (key[0] - p.x) ** 2 + (key[1] - p.y) ** 2 <= r2 and abs(key[2] - p.z) < 0.6:
                if now - m.get("struck", 0.0) < 10.0:
                    return False
                m["struck"], m["strikes"] = now, m.get("strikes", 0) + 1
                if m["strikes"] >= s.TRAP_STRIKES:
                    s.forget_trap(key)
                return True
        return False

    @staticmethod
    def _read_traps(path):
        """{key: meta} from a trapped_walls.json: version 2 rows are dicts, version 1 rows are [x, y, z]."""
        import json
        import time
        out = {}
        try:
            with open(path, encoding="utf-8") as f:
                rows = json.load(f).get("spots", ())
        except (OSError, ValueError, TypeError, AttributeError):
            return out
        now = time.time()
        for r in rows:
            try:
                x, y, z = (r["x"], r["y"], r["z"]) if isinstance(r, dict) else r[:3]
                m = r if isinstance(r, dict) else {}
                out[(round(float(x), 1), round(float(y), 1), round(float(z), 1))] = {
                    "n": int(m.get("n", 1)), "first": float(m.get("first", now)), "last": float(m.get("last", now)),
                    "strikes": int(m.get("strikes", 0))}
            except (KeyError, TypeError, ValueError, IndexError):
                continue
        return out

    def save_traps(s, path):
        """Write the marks to path (JSON, version 2), merged with what is there already (another match's, another bot's:
        counts keep the higher, strikes too), less the ones forgotten here and the unconfirmed ones nobody has touched
        for TRAP_TTL_DAYS."""
        import json
        import os
        import time
        disk = s._read_traps(path)
        for k in s.__dict__.get("trap_gone", ()):
            disk.pop(k, None)
        for k, m in s.__dict__.get("trapped", {}).items():
            d = disk.get(k)
            disk[k] = {"n": max(m["n"], d["n"]) if d else m["n"], "first": min(m["first"], d["first"]) if d else m["first"],
                       "last": max(m["last"], d["last"]) if d else m["last"],
                       "strikes": max(m.get("strikes", 0), d["strikes"]) if d else m.get("strikes", 0)}
        now = time.time()
        keep = sorted(k for k, m in disk.items() if m["n"] >= 2 or now - m["last"] <= s.TRAP_TTL_DAYS * 86400)[:s.STUCK_MAX]
        rows = [dict(x=k[0], y=k[1], z=k[2], **disk[k]) for k in keep]
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"version": s.TRAP_VERSION, "spots": rows}, f)
            os.replace(tmp, path)               # (whole file or none: a match ending mid-write leaves the old one)
        except OSError:
            return False
        return True

    def load_traps(s, path):
        """Put the saved marks (save_traps) on this terrain as walls, less the expired unconfirmed ones. Returns how many
        were new; a map over TRAP_WARN marks prints a warning (stderr) and review_traps lists the suspect ones."""
        import sys
        import time
        saved, now, n = s._read_traps(path), time.time(), 0
        if len(saved) > s.TRAP_WARN:
            print(f"terrain: {len(saved)} trapped walls saved in {path}: more than {s.TRAP_WARN}, something marks too "
                  f"much (review_traps)", file=sys.stderr)
        for k, m in saved.items():
            if m["n"] < 2 and now - m["last"] > s.TRAP_TTL_DAYS * 86400:
                continue
            n += s.learn_trap(Vec(*k), meta=m)
        return n

    def _stuck_near(s, x, y, z, r):
        """Is a learned spot within r (flat) of (x, y) at about height z? Indexed by whole units; rebuilt when the set grew."""
        idx = s.__dict__.get("_stuck_idx")
        if idx is None or idx[0] != len(s.stuck):
            grid = {}
            for k in s.stuck:
                grid.setdefault((int(k[0] // 1), int(k[1] // 1)), []).append(k)
            idx = s._stuck_idx = (len(s.stuck), grid)
        gx, gy = int(x // 1), int(y // 1)
        for a in (gx - 1, gx, gx + 1):
            for b in (gy - 1, gy, gy + 1):
                for k in idx[1].get((a, b), ()):
                    if (k[0] - x) ** 2 + (k[1] - y) ** 2 <= r * r and abs(k[2] - z) < 0.6:
                        return True
        return False

    def _node_at(s, x, y, z, tol=1.0):
        """(layer, row, col) of the floor at (x, y) nearest height z within tol, or None."""
        j, i = s._cell(Vec(x, y, z))
        col = [(abs(float(s.lz[l, j, i]) - z), l) for l in range(s.L) if np.isfinite(s.lz[l, j, i])]
        d, l = min(col, default=(9e9, -1))
        return (l, j, i) if d <= tol else None

    def apply_paint(s, layers):
        """The map's hand painting. "walk" is added before this as floor (maps.terrain, extra_floor). "block": entering a
        painted cell costs a wall (STUCK_COST, as a learned stuck spot: a wall unless there is no other way). "drive_s /
        drive_m / drive_l": ground a vehicle may drive whatever the wall-room rule says (_build_vnav). "fly_high": the
        airspace aircraft travel through, "fly_low": where they take off and swoop in to attack; an older "fly" layer is
        high (fly_way)."""
        graphs = (s.graph_to, getattr(s, "graph_jump_to", None), getattr(s, "graph_h_to", None))
        for x, y, z in layers.get("block", []):
            if s.inside(x, y):
                s._stuck_cost((x, y, z), graphs)
        s._fields.clear()
        getattr(s, "_jfields", {}).clear()
        getattr(s, "_hfields", {}).clear()
        s.drive_paint = [n for k in ("drive_s", "drive_m", "drive_l") for c in layers.get(k, []) if s.inside(c[0], c[1])
                         for n in [s._node_at(*c)] if n is not None]
        s.hand_drive = set(s.drive_paint)        # (a skilled player's: never left off for a wedge, _build_vnav)
        s.fly_cells = {k: {(round(x * 2), round(y * 2)): z for x, y, z in layers.get("fly_" + k, []) +
                           (layers.get("fly", []) if k == "high" else [])} for k in ("low", "high")}
        s.__dict__.pop("graph_v", None)          # the vehicles' ground is built again with the painted drive cells
        s.__dict__.pop("_fly_fields", None)

    def top_near(s, x, y, r, zmax=None):
        """The highest floor of any level within r of (x, y) (a roof, the spire's top), -inf where none is; with zmax only
        floors at or under it (an aircraft's: a deck high over it is a ceiling, not a floor to climb over)."""
        j, i = s._cell(Vec(x, y, 0.0))
        k = int(r / s.cell) + 1
        win = s.lz[:, max(0, j - k):j + k + 1, max(0, i - k):i + k + 1]
        win = win[np.isfinite(win) & (win <= (zmax if zmax is not None else np.inf))]
        return float(win.max()) if win.size else float("-inf")

    def fly_clear(s, p, yaw, far=12.0, dzs=(-0.6, 0.0, 0.8), floor=None, start=None, avoid=()):
        """An aircraft's short-sighted LiDAR: how far it flies from p along yaw (flat, from `start`) before something solid
        at any of the heights p.z + dzs: a steep face (the wall bins), a floor slab within 0.4 u (a deck, a roof, a
        hillside), or under the lowest surface. Floors no higher than floor + 0.5 (the ground it stands on or sets down
        on) do not count. avoid: [(Vec, r)] spots the pilot remembered as blocked (Bot.air_guard: what the level data
        lacks), within 3 u of its height. far when nothing is in the way. (Samples 0.8 of a cell apart, as clearance's:
        0.5 u apart a one-cell wall slipped between them, Sidewinder's red base 0.6 u off read open.)"""
        t = np.arange(s.cell * 0.8 if start is None else start, far + 1e-6, s.cell * 0.8)
        xs, ys = p.x + math.cos(yaw) * t, p.y + math.sin(yaw) * t
        j, i = s._ij(xs, ys)
        nj = np.clip(((ys - s.y0) / NAV_CELL).astype(int), 0, s.nh - 1)
        ni = np.clip(((xs - s.x0) / NAV_CELL).astype(int), 0, s.nw - 1)
        col, low = s.lz[:, nj, ni], s.low[j, i]
        base = -np.inf if floor is None else floor + 0.5
        hit = np.zeros(len(t), bool)
        with np.errstate(invalid="ignore"):
            for dz in dzs:
                z = np.full(len(t), p.z + dz)
                hit |= s._wall_bit(s.walls[j, i], z)
                hit |= np.any(np.isfinite(col) & (np.abs(col - z) < 0.4) & (col > base), axis=0)
                hit |= np.isfinite(low) & (z < low) & (low > base)
        for a in avoid:                                  # (spot, r) or (spot, r, height band: out of bounds)
            q, r, band = a if len(a) == 3 else (a[0], a[1], 3.0)
            if abs(q.z - p.z) < band and (q.x - p.x) ** 2 + (q.y - p.y) ** 2 < (far + r) ** 2:   # (in reach only:
                hit |= (xs - q.x) ** 2 + (ys - q.y) ** 2 < r * r              # a map's kill zones are 100s of discs)
        k = int(np.argmax(hit)) if hit.any() else -1
        return float(t[k]) if k >= 0 else far

    def height_over(s, x, y, z):
        """Height of (x, y, z) over the floor right under it: the highest of the nav's floors there at or below z (a
        roof, a bridge, the spire top), else the ground (aircraft, tools/lidar.py RideWatch; ground() is the lowest)."""
        if not s.inside(x, y):
            return z - s.ground(x, y)
        j, i = s._cell(Vec(x, y, z))
        zs = [float(v) for v in s.lz[:, j, i] if math.isfinite(v) and v <= z + 0.3]
        return z - (max(zs) if zs else s.ground(x, y))

    def add_rides(s, drive, air, heights=None, marks=None):
        """What people and bots drove and flew (maps/<map>/rides.json, tools/lidar.py RideWatch; maps.rides_layers):
        drive, cells a vehicle was driven over, the vehicles' ground whatever the wall-room rule says (as painted drive
        cells); air {"low"|"high": {half-unit cell: floor z}}, flown airspace by the learned heights; heights (strafing,
        travelling) clearance over the floor; marks: take-offs, landings, turn-backs. Added to the painting, applied
        at once (the vehicles' ground is built again on the next drive)."""
        # (each sample marks what the vehicle covers, about 0.4 u round it: one cell a sample left gaps in a 0.25 u grid)
        foot = [(dx * NAV_CELL, dy * NAV_CELL) for dx in range(-2, 3) for dy in range(-2, 3) if dx * dx + dy * dy <= 5]
        s.drive_paint = list(set(getattr(s, "drive_paint", [])) | {   # (applied again after every save: no repeats)
            n for c in drive for dx, dy in foot if s.inside(c[0] + dx, c[1] + dy)
            for n in [s._node_at(c[0] + dx, c[1] + dy, c[2], tol=0.6)] if n is not None and s.lwalk[n]})
        fc = s.__dict__.setdefault("fly_cells", {"low": {}, "high": {}})
        for k in ("low", "high"):
            layer = fc.setdefault(k, {})
            for c, z in (air or {}).get(k, {}).items():
                layer.setdefault(c, z)            # (a painted cell keeps its painted floor)
        if heights:
            s.air_heights = heights
        for k, v in (marks or {}).items():
            s.__dict__.setdefault("air_marks", {})[k] = v
        s.__dict__.pop("graph_v", None)
        s.__dict__.pop("_fly_fields", None)

    AIR_LOW_MIN, AIR_LOW_MAX = 2.5, 6.0      # attack runs: this far over the ground at least / at most
    AIR_HIGH_MIN, AIR_HIGH_MAX = 6.0, 25.0   # travel

    def air_levels(s):
        """(low, high): an aircraft's attack-run and travel heights over the ground under it, from the map's own shape
        (the owner: "automatically determine low flight height and high fly height by distance from ground"). Low: over
        the knee-to-roof clutter near the ground (the median of how much higher the top surface gets within 3 u of a
        spot, + 1.5 u), so a strafing run clears crates, rocks and rails. High: over the buildings and hills a straight
        trip passes (the median rise within 8 u, + 3 u). Measured over open sky only (cells whose
        top surface is a floor: no roof over it). Kept once per terrain; a map's learned heights (people's flying,
        rides.json air_heights) win over these."""
        got = s.__dict__.get("_air_levels")
        if got is not None:
            return got
        from scipy.ndimage import maximum_filter
        top = np.where(s.top < 999, s.top, np.nan)
        with np.errstate(all="ignore"):
            fl = np.nanmax(np.where(np.isfinite(s.floors), s.floors, -np.inf), axis=0)
        sky = np.isfinite(top) & np.isfinite(fl) & (np.abs(top - fl) < 0.5)      # open sky over a floor
        if sky.sum() < 50:
            s._air_levels = (s.AIR_LOW_MIN, 9.0)
            return s._air_levels
        filled = np.where(np.isfinite(top), top, np.nanmin(top))

        def rise(r):
            k = 2 * max(1, int(round(r / s.cell))) + 1
            return (maximum_filter(filled, size=k) - filled)[sky]

        # (medians: the 90th percentile counted canyon walls and Spire's cliffs as things to fly over, 18-38 u where
        # people fly 8-15)
        low = float(np.clip(np.percentile(rise(3.0), 50) + 1.5, s.AIR_LOW_MIN, s.AIR_LOW_MAX))
        high = float(np.clip(np.percentile(rise(8.0), 50) + 3.0, max(s.AIR_HIGH_MIN, low + 2.0), s.AIR_HIGH_MAX))
        s._air_levels = (round(low, 1), round(high, 1))
        return s._air_levels

    def fly_way(s, a, b, layer="high", ahead=6):
        """The next point toward b through a painted airspace layer ("high" travel, "low" take-off and attack runs;
        half-unit cells, 8 ways round), `ahead` cells along; its z the floor painted under it. None when nothing is
        painted there, a or b is off it, or there is no way through (fly straight then). The cost from every cell to
        b's cell is searched once per goal cell and kept (pilots ask every tick)."""
        cells = getattr(s, "fly_cells", {}).get(layer)
        if not cells:
            return None
        import heapq
        near = lambda p: min(cells, key=lambda c: (c[0] - p.x * 2) ** 2 + (c[1] - p.y * 2) ** 2)
        st, go = near(a), near(b)
        if (st[0] - a.x * 2) ** 2 + (st[1] - a.y * 2) ** 2 > 36 or (go[0] - b.x * 2) ** 2 + (go[1] - b.y * 2) ** 2 > 36:
            return None                              # 3 u or more off the painted air: not in it
        steps = [(dx, dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1) if dx or dy]
        fields = s.__dict__.setdefault("_fly_fields", {})
        cost = fields.get((layer, go))
        if cost is None:
            cost, todo = {go: 0.0}, [(0.0, go)]
            while todo:
                d, c = heapq.heappop(todo)
                if d > cost.get(c, 9e9):
                    continue
                for dx, dy in steps:
                    n = (c[0] + dx, c[1] + dy)
                    if n in cells and d + math.hypot(dx, dy) < cost.get(n, 9e9):
                        cost[n] = d + math.hypot(dx, dy)
                        heapq.heappush(todo, (cost[n], n))
            if len(fields) > 64:
                fields.clear()
            fields[(layer, go)] = cost
        if st not in cost:
            return None                              # painted air, but not joined to b's
        c = st
        for _ in range(ahead):                       # downhill to the goal
            nxt = min(((c[0] + dx, c[1] + dy) for dx, dy in steps), key=lambda n: cost.get(n, 9e9))
            if cost.get(nxt, 9e9) >= cost[c]:
                break
            c = nxt
        return Vec(c[0] / 2, c[1] / 2, cells[c])

    def _stuck_cost(s, key, graphs, sign=1.0):
        """Make entering the disk around a learned spot (x, y, z) cost STUCK_COST in these graphs, and the ring round it
        (out to STUCK_HALO) STUCK_HALO_COST: the way round a rock that is bigger than the disk."""
        x, y, z = key
        j0, i0 = s._cell(Vec(x, y, z))
        # a spot a bot was trapped at (learn_trap) is a wall: BARRIER_COST, taken only when there is no way round at all
        core = s.BARRIER_COST if tuple(key) in s.__dict__.get("trapped", ()) else s.STUCK_COST
        rings = ((s.STUCK_RADIUS, core, None), (s.STUCK_HALO, s.STUCK_HALO_COST, s.STUCK_RADIUS))
        for radius, cost, inner in rings:
            r, ri2 = int(radius / NAV_CELL), ((int(inner / NAV_CELL)) ** 2 if inner else -1)   # (ri2 -1: no hole)
            nodes = [(l * s.nh + j) * s.nw + i
                     for j in range(max(0, j0 - r), min(s.nh, j0 + r + 1))
                     for i in range(max(0, i0 - r), min(s.nw, i0 + r + 1))
                     if (j - j0) ** 2 + (i - i0) ** 2 <= r * r and (j - j0) ** 2 + (i - i0) ** 2 > ri2
                     for l in range(s.L) if np.isfinite(s.lz[l, j, i]) and abs(s.lz[l, j, i] - z) < 0.6]
            for g in graphs:
                if g is not None:
                    for n in nodes:                  # graph_to row n = every edge that enters node n
                        g.data[g.indptr[n]:g.indptr[n + 1]] += cost * sign

    def los(s, a, b, mode=None):
        """Eye of a to chest of b, or else to b's head (a human sees a head over a low wall). Every wall stops it, windows
        and fences too (set_wall_kinds), and Forge shields: the solid line (moves, reach, cover). mode "see": sight
        (sees), "shot": a round's line (shot_los)."""
        if not math.isfinite(a.x + a.y + a.z + b.x + b.y + b.z):
            return False                             # (a NaN belief failed a whole tick, live Spire)
        if s.blocked and math.hypot(b.x - a.x, b.y - a.y) > 8.0 and s._blk_key(a, b) in s.blocked:
            return False                             # learned blocks never hide a man in front of you
        return s._ray(a, b, CHEST, mode) or s._ray(a, b, EYE, mode)

    def sees(s, a, b):
        """Sight from a's eye to b: through windows, fences and Forge shields (see-through), stopped by solid walls."""
        return s.los(a, b, "see")

    def shot_los(s, a, b):
        """A round's line from a's eye to b: solid walls, bullet-proof windows and Forge shields stop it; fences and
        windows that break do not."""
        return s.los(a, b, "shot")

    SHOT_VOTES, SHOT_MAX = 3, 300    # hits through the same spot before it counts as shoot-through; cells per map

    def learn_shot_through(s, a, b):
        """A round from a (feet; his eye) hit b (feet; his chest) where the level data has a wall or a deck in the way:
        that spot lets rounds (and sight) through, a fence the map's data does not name (a grate, a railing). Per
        quarter-unit cell and half-unit height, on the SHOT_VOTES-th hit (separate episodes: Bot.learn_hits), up to
        SHOT_MAX cells; s.walls_learned holds them as fence cells [x4, y4, lo, hi] (maps.save_learned:
        walls_learned.json, loaded as fences). Returns the cells it learned."""
        if s.shot_los(a, b) or not math.isfinite(a.x + a.y + a.z + b.x + b.y + b.z):
            return []
        za, zb = a.z + EYE, b.z + CHEST
        dx, dy = b.x - a.x, b.y - a.y
        n = max(3, int(max(math.hypot(dx, dy), abs(zb - za)) / (s.cell * 0.8)))
        if n > 4000:
            return []
        t = np.linspace(0.0, 1.0, n)[1:-1]
        xs, ys, zs = a.x + dx * t, a.y + dy * t, za + (zb - za) * t
        j, i = s._ij(xs, ys)
        W = s.walls[j, i].copy()
        clear = getattr(s, "shot_clear", None)
        if clear is not None:
            k = min(W.shape[-1], clear.shape[-1])
            W[:, :k] &= ~clear[j, i, :k]
        hit = s._wall_bit(W, zs)
        fl = s.floors[:, j, i]
        with np.errstate(invalid="ignore"):
            deck = (((np.r_[za, zs[:-1]] - fl) * (zs - fl) < 0) & np.isfinite(fl)).any(axis=0)
        votes, learned = s.__dict__.setdefault("shot_votes", {}), s.__dict__.setdefault("walls_learned", [])
        new = []
        for k in np.flatnonzero(hit | deck):
            x, y = s.x0 + (i[k] + 0.5) * s.cell, s.y0 + (j[k] + 0.5) * s.cell
            key = (round(x * 4), round(y * 4), int(math.floor(zs[k] * 2)))
            votes[key] = votes.get(key, 0) + 1
            if votes[key] == s.SHOT_VOTES and len(learned) + len(new) < s.SHOT_MAX:
                new.append([key[0], key[1], round(key[2] / 2, 2), round(key[2] / 2 + 0.5, 2), int(bool(deck[k]))])
        if new:
            learned += new                            # (the fifth item: 1 a deck, 0 a wall)
            s._clear_cells([c for c in new if not c[4]], shot=True, floor=False)
            s._clear_cells([c for c in new if c[4]], shot=True, floor=True)
        return new

    def _clear_cells(s, cells, shot, floor):
        """Let sight (and shots: shot) through the walls (floor: the decks) of cells [x4, y4, lo, hi(, breaks)]
        (set_wall_kinds, learn_shot_through)."""
        if getattr(s, "see_clear", None) is None:
            s.see_clear = np.zeros(s.walls.shape, np.uint64)
            s.shot_clear = np.zeros(s.walls.shape, np.uint64)
            s.see_floor = np.full((s.h, s.w, 2), np.nan, np.float32)
            s.shot_floor = np.full((s.h, s.w, 2), np.nan, np.float32)
        words = min(s.walls.shape[-1], s.see_clear.shape[-1])
        used = 0
        for r in cells:
            x, y = r[0] * 0.25, r[1] * 0.25           # (the cell's centre: round(x * 4); it spans 0.125 each way)
            if not s.inside(x, y):
                continue
            jj, ii = s._ij(np.asarray([x - 0.12, x + 0.12, x - 0.12, x + 0.12]),
                           np.asarray([y - 0.12, y - 0.12, y + 0.12, y + 0.12]))
            through = shot or (len(r) > 4 and bool(r[4]))
            ji = set(zip(jj.tolist(), ii.tolist()))
            if floor:
                for F in (s.see_floor, s.shot_floor) if through else (s.see_floor,):
                    for j, i in ji:
                        lo, hi = F[j, i]
                        F[j, i] = (r[2], r[3]) if np.isnan(lo) else (min(lo, r[2]), max(hi, r[3]))
                used += 1
                continue
            b0 = max(0, int(math.floor((r[2] - s.wall_z0) / s.wall_bin)) - 1)
            b1 = min(64 * words - 1, int(math.floor((r[3] - s.wall_z0) / s.wall_bin)) + 1)
            if b1 < b0:
                continue
            used += 1
            for j, i in ji:
                for bb in range(b0, b1 + 1):
                    bit = np.uint64(1) << np.uint64(bb & 63)
                    s.see_clear[j, i, bb >> 6] |= bit
                    if through:
                        s.shot_clear[j, i, bb >> 6] |= bit
        return used

    def set_wall_kinds(s, data):
        """The map's windows and fences (tools/lidar_walls.py, maps/<map>/wall_kinds.json: quarter-unit cells [x4, y4,
        lo, hi(, breaks)] with the height range their faces cover): walls that block walking but not sight (windows,
        fences) or not shots (fences, windows that break). s.see_clear / s.shot_clear hold the wall bits sight / shot
        lines skip; the walking map and los keep every wall. Flat ones (window_floor: a glass floor, fence_floor: a grate
        deck) as s.see_floor / s.shot_floor: per cell the (low, high) of a deck sight / shots pass. Returns the cells
        used per kind."""
        s.see_clear = None                            # (set again: a rebuilt terrain starts clean)
        used = {k: s._clear_cells(data.get(k, ()), shot=k.startswith("fence"), floor=k.endswith("_floor"))
                for k in ("window", "fence", "window_floor", "fence_floor")}
        s.wall_kinds = used
        return used

    def _ray(s, a, b, part, mode=None):
        """Eye of a to height part of b over the real level: blocked by a steep face (wall bitmask)
        at the ray's height, by the ray crossing a floor level (a slab between storeys), or by the ray
        going under the lowest surface (inside a hill). Open air under overhangs stays open. mode "see" / "shot": the
        wall bits windows and fences let through are skipped (set_wall_kinds); "see" passes Forge shields."""
        dx, dy = b.x - a.x, b.y - a.y
        L = math.hypot(dx, dy)
        za, zb = a.z + EYE, b.z + part
        if L < 0.5 and abs(zb - za) < 1.0:
            return True
        if not L < RAY_MAX:          # (no sight that far, and its samples grow with it: one 2 million u long, from a bad
            return False             # place read, took 84 MB and killed the brain, Forge; NaN too)
        if mode != "see" and s._through_glass((a.x, a.y, za), (b.x, b.y, zb)):
            return False                             # a shield in the way stops a shot and a move (sight passes it)
        # the floor a man stands on stops a ray from straight below him, and the shooter's own floor one from straight
        # above: whatever the heightmap holds
        for fz, cx, cy in ((b.z, b.x, b.y), (a.z, a.x, a.y)):
            if (za - fz) * (zb - fz) < 0:            # the ray crosses that floor's plane ...
                u = (fz - za) / (zb - za)
                if math.hypot(a.x + dx * u - cx, a.y + dy * u - cy) < FEET_SLAB:
                    return False                     # ... under his feet
        n = max(3, int(max(L, abs(zb - za)) / (s.cell * 0.8)))
        t = np.linspace(0.0, 1.0, n)[1:-1]          # skip both end points (the players themselves)
        xs, ys, zs = a.x + dx * t, a.y + dy * t, za + (zb - za) * t
        j, i = s._ij(xs, ys)
        lows = np.nan_to_num(s.low[j, i], nan=-1e3)
        if (zs < lows - 0.05).any():
            return False                             # through the ground
        W = s.walls[j, i]
        clear = getattr(s, "see_clear" if mode == "see" else "shot_clear", None) if mode else None
        if clear is not None:
            W = W.copy()
            k = min(W.shape[-1], clear.shape[-1])    # (Forge pieces can add wall words after the kinds went in)
            W[:, :k] &= ~clear[j, i, :k]
        if s._wall_bit(W, zs).any():
            return False                             # a wall at the ray's height (windows and fences: set_wall_kinds)
        fl = s.floors[:, j, i]                       # (L, n) floor levels under the samples
        z0, z1 = np.r_[za, zs[:-1]], zs
        with np.errstate(invalid="ignore"):
            cross = ((z0 - fl) * (z1 - fl) < 0) & np.isfinite(fl)
            deck = getattr(s, "see_floor" if mode == "see" else "shot_floor", None) if mode else None
            if deck is not None and cross.any():     # a glass floor / grate deck it sees (shoots) through
                lo, hi = deck[j, i, 0], deck[j, i, 1]
                cross &= ~((fl >= lo - 0.3) & (fl <= hi + 0.3))
        return not cross.any()                       # through a floor slab

    def aim_hit(s, o, yaw, pitch, reach=150.0):
        """Where a view from o along yaw / pitch first meets the level within reach (the ground, a wall at its height, a
        floor slab: _ray's tests), else None: sky, or off the map's edge. tracefmt.crosshair."""
        cp = math.cos(pitch)
        dx, dy, dz = math.cos(yaw) * cp, math.sin(yaw) * cp, math.sin(pitch)
        t = np.arange(1, int(reach / (s.cell * 0.8)) + 1) * (s.cell * 0.8)
        xs, ys, zs = o.x + dx * t, o.y + dy * t, o.z + dz * t
        on = (xs >= s.x0) & (xs < s.x0 + s.w * s.cell) & (ys >= s.y0) & (ys < s.y0 + s.h * s.cell)
        n = int(np.argmin(on)) if not on.all() else len(t)    # (stops at the edge: the clipped cells past it are not there)
        if n == 0:
            return None
        xs, ys, zs = xs[:n], ys[:n], zs[:n]
        j, i = s._ij(xs, ys)
        W = s.walls[j, i]
        if getattr(s, "see_clear", None) is not None:   # (the view passes windows and fences: set_wall_kinds)
            W = W.copy()
            k = min(W.shape[-1], s.see_clear.shape[-1])
            W[:, :k] &= ~s.see_clear[j, i, :k]
        hit = (zs < np.nan_to_num(s.low[j, i], nan=-1e3) - 0.05) | s._wall_bit(W, zs)
        fl = s.floors[:, j, i]
        with np.errstate(invalid="ignore"):
            hit |= (((np.r_[o.z, zs[:-1]] - fl) * (zs - fl) < 0) & np.isfinite(fl)).any(axis=0)
        if not hit.any():
            return None
        k = int(np.argmax(hit))
        return Vec(float(xs[k]), float(ys[k]), float(zs[k]))

    # --- navigation. Layer l is the l-th walkable floor level in a cell, lowest first (levels are
    # at least tools/heightmap.py LAYER_GAP apart: a doorway, the roof over it, a catwalk above). A position belongs to
    # the layer nearest its own height. Node (l, j, i) has id (l * nh + j) * nw + i.
    def _build_nav(s):
        step = int(round(NAV_CELL / s.cell))
        s.lz = s.floors[:, ::step, ::step]
        s.L = s.lz.shape[0]
        s.nh, s.nw = s.lz.shape[1:]
        s.lwalk = np.isfinite(s.lz) & ~s._walled(s.walls[::step, ::step])
        s.lwalk &= ~s._low_ceiling(s.walls[::step, ::step])
        for x, y, z in getattr(s, "walked_ok", ()):    # floor people walked, reviewed and approved: walkable whatever
            j, i = s._cell(Vec(x, y, z))                  # the wall bits say (Spire's ramp foot, walked by the player,
            col = s.lz[:, j, i]                           # read as walled: the ramp had no way on)
            if np.isfinite(col).any():
                l = int(np.nanargmin(np.abs(col - z)))
                if abs(float(col[l]) - z) <= 0.3:
                    s.lwalk[l, j, i] = True
        s.nstep = step
        # the walkable grid as a sparse directed graph (8-neighbour, same climb rule as
        # movement), searched with SciPy's compiled Dijkstra
        from scipy.sparse import coo_matrix
        N = s.nh * s.nw
        idx = np.arange(s.L * N).reshape(s.L, s.nh, s.nw)
        rows, cols, wts = [], [], []
        z = np.nan_to_num(s.lz, nan=1e6)
        s.near_wall = s._near_wall(step)              # (also sets s.near_ledge)
        s.near_wall2 = s._ring2(s.near_wall)
        # a handrail at a ledge: wall at waist height over the upper floor, in its cell or the one below the drop. The
        # chest-and-head wall test lets it through, so paths dropped off walkways right through rails: such a drop
        # costs RAIL_COST more
        nwalls = s.walls[::step, ::step]
        rail_at = lambda w, zz: s._wall_bit(w, zz + RAIL[0]) | s._wall_bit(w, zz + RAIL[1])
        rail_here = [rail_at(nwalls, z[l]) for l in range(s.L)]
        # a drop into a slit or pinhole no body fits through: 5+ of the 8 cells around the landing still have floor at
        # the upper height (Countdown: a 0.25 u seam in the third floor over the flag stand read as a way down; bots
        # stood on the floor above the flag for a minute)
        padz = np.pad(s.lz, ((0, 0), (1, 1), (1, 1)), constant_values=np.nan)
        # a placed ramp's cells climb as steeply as the ramp (add_faces: up to 50 degrees, steeper than MAX_RISE allows)
        tz = s.tz = s.slope[:, ::step, ::step] if s.slope is not None else None
        # stairs (stair_rule): their risers step over MAX_RISE, so the nav walled off Adrift's flag decks (live:
        # runners stood under the flag 4-7 u off for minutes, no pull in 178 s)
        stairs = s._stairs() if s.stair_rule else None

        def slit(dst, za, at):
            # tested at the drops only (`at`): over the whole map for every pair of levels it took 79 s of a Forge World
            # build with seven levels
            jj, ii = np.nonzero(at)
            pj, pi, zz = jj + dst[0].start + 1, ii + dst[1].start + 1, za[jj, ii]
            n = np.zeros(len(jj), np.int8)
            for oj in (-1, 0, 1):
                for oi in (-1, 0, 1):
                    if oj or oi:
                        with np.errstate(invalid="ignore"):
                            n += (np.abs(padz[:, pj + oj, pi + oi] - zz) < 0.3).any(axis=0)
            out = np.zeros(at.shape, bool)
            out[jj, ii] = n >= 5
            return out
        for dj, di in ((0, 1), (1, 0), (1, 1), (1, -1)):
            dl = NAV_CELL * (1.414 if dj and di else 1.0)
            a = (slice(0, s.nh - dj), slice(max(0, -di), s.nw - max(0, di)))
            b = (slice(dj, s.nh), slice(max(0, di), s.nw - max(0, -di)))
            for la in range(s.L):
                for lb in range(s.L):
                    both = s.lwalk[la][a] & s.lwalk[lb][b]
                    if not both.any():
                        continue
                    za, zb = z[la][a], z[lb][b]
                    for sgn, (src, dst, ls, ld, rise) in zip((1, -1), ((a, b, la, lb, zb - za), (b, a, lb, la, za - zb))):
                        lim = MAX_RISE if tz is None else np.maximum(MAX_RISE, np.maximum(tz[ls][src], tz[ld][dst]) * dl + 0.02)
                        if stairs is not None:      # along a stair's run (x edges by x stairs, y by y, diagonals by either)
                            st = stairs[0] if not dj else stairs[1] if not di else stairs[0] | stairs[1]
                            lim = np.where(st[ls][src] | st[ld][dst], np.maximum(lim, STAIR_RISE * dl / NAV_CELL), lim)
                        ok = both & (rise <= lim) & (rise >= -MAX_DROP)
                        if dj and di and s.CORNER_RULE and ok.any():
                            # no cutting a corner: a diagonal step passes the corner point of the two cells beside it,
                            # and a body ~a cell wide clips whichever of them is a wall or a rock (bots caught on rock
                            # corners and door frames pushing at a step the grid called free). Both cells beside it must
                            # be walkable floor at the step's start or end height; the way round through the open one
                            # (two straight steps) is still there, so nothing is cut off
                            ok &= s._corners_clear(ok, src, ls, ld, sgn * dj, sgn * di, z[ls][src], z[ld][dst],
                                                   lim)
                        drop = ok & (rise < -lim)               # off a ledge (a stair step or a ramp down is not one)
                        over, extra = drop, None
                        if drop.any():
                            if s.slit_rule:
                                ok &= ~slit(dst, z[ls][src], drop)
                            if s.lid_rule:
                                # the landing column has floor between the landing and our own height: the ledge (a
                                # bridge, a walkway) goes on over it, so a body walks onto that floor and never falls
                                # through it (Halo 4 ca_blood_cavern, the owner's "stuck on this wall": paths dropped 2.6 u off
                                # the ledge at (-8.1, 6.4) into the floor under it; 75% of 824 s of wall pushes in his
                                # two games). Priced LID_COST, not cut: at the 0.25 u cells a one-cell slot people drop
                                # through reads the same (cut, Lockout lost 17-43 walked cells). A floor a little over
                                # ours there (a rail's or a wall's top) is priced as a rail: a drop over it is a jump
                                jj, ii = np.nonzero(drop & ok)
                                col = s.lz[(slice(None),) + dst][:, jj, ii]
                                za_, zl_ = z[ls][src][jj, ii], z[ld][dst][jj, ii]
                                with np.errstate(invalid="ignore"):
                                    lid = ((col > zl_ + 0.3) & (col <= za_ + MAX_RISE)).any(axis=0)
                                    rail = ((col > za_ + MAX_RISE) & (col < za_ + BODY[1])).any(axis=0)
                                extra = np.zeros(drop.shape)
                                extra[jj[rail], ii[rail]] = RAIL_COST
                                extra[jj[lid], ii[lid]] = LID_COST
                            drop &= ok
                            jj, ii = np.nonzero(drop)
                            over = np.zeros(drop.shape, bool)
                            over[jj, ii] = rail_here[ls][src][jj, ii] | rail_at(nwalls[dst][jj, ii], z[ls][src][jj, ii])
                        rows.append(idx[ls][src][ok]); cols.append(idx[ld][dst][ok])
                        walk = rise >= -lim                  # (a step on the level, not a drop: a landing is no lip)
                        wts.append(dl + np.abs(rise[ok]) * 2.0 + WALL_COST * s.near_wall[ld][dst][ok] + RAIL_COST * over[ok]
                                   + WALL_COST2 * s.near_wall2[ld][dst][ok]
                                   + LEDGE_COST * (s.near_ledge[ld][dst] & walk)[ok]
                                   + (extra[ok] if extra is not None else 0.0))
        r, c, w = np.concatenate(rows), np.concatenate(cols), np.concatenate(wts)
        s.graph = coo_matrix((w, (r, c)), shape=(s.L * N, s.L * N)).tocsr()
        s.graph_to = s.graph.T.tocsr()   # reversed: Dijkstra from a goal gives every node's cost to it
        s.degree = np.diff(s.graph.indptr).reshape(s.L, s.nh, s.nw)   # 0 = a sliver you cannot leave

    def _wall_bit(s, walls, z):
        """Is there a steep face at height z? walls: (..., WORDS) uint64 bit rows matching z's shape
        (plus the word axis); heights outside the recorded range count as no wall."""
        b = np.floor((z - s.wall_z0) / s.wall_bin).astype(np.int64)
        inside = (b >= 0) & (b < 64 * walls.shape[-1])
        bc = np.clip(b, 0, 64 * walls.shape[-1] - 1)
        word = np.take_along_axis(walls, (bc >> 6)[..., None], axis=-1)[..., 0]
        return inside & ((np.right_shift(word, (bc & 63).astype(np.uint64)) & np.uint64(1)) != 0)

    def _stairs(s):
        """(along x, along y): per level and node, a stair cell, its floor climbing on through both neighbours on that
        axis (each step 0.03-0.3; a neighbour's floor is its level nearest this one). A lip beside a ramp has flat floor
        across it, so only edges along a run get STAIR_RISE."""
        z = s.lz
        out = []
        for axis in (2, 1):
            nb = []
            for sh in (1, -1):
                g = np.roll(z, sh, axis=axis)
                edge = [slice(None)] * 3
                edge[axis] = slice(0, 1) if sh == 1 else slice(-1, None)
                g[tuple(edge)] = np.nan                     # (no wrap round the level's border)
                near = np.empty_like(z)
                for l in range(z.shape[0]):                 # (a level at a time: Forge canvases hold millions of cells)
                    d = np.abs(g - z[l])
                    k = np.argmin(np.where(np.isnan(d), np.inf, d), axis=0)
                    near[l] = np.take_along_axis(g, k[None], axis=0)[0]
                nb.append(near)
            with np.errstate(invalid="ignore"):
                a, b = z - nb[0], nb[1] - z
                out.append((((a >= 0.03) & (b >= 0.03)) | ((a <= -0.03) & (b <= -0.03))) & (np.abs(a) <= 0.3)
                           & (np.abs(b) <= 0.3))
        return out

    def _walled(s, walls):
        """Per layer: a wall passes through a standing body in this cell (thin walls and door
        frames the top-down layers miss). walls: (h, w, WORDS) uint64 height bins."""
        z = np.nan_to_num(s.lz, nan=-1e6)
        wl = np.broadcast_to(walls[None], (s.L,) + walls.shape)
        # a wall covers chest AND head height; a stair riser or a curb in the cell does not
        return s._wall_bit(wl, z + BODY[0]) & s._wall_bit(wl, z + BODY[1])

    def _low_ceiling(s, walls):
        """Per layer: a wall at head height (EYE or HEAD_TOP), open at the knee, over a floor no person walked: a rock or
        slab leaning over it that a standing body does not fit under (a face from the floor up is _walled's). _walled lets it through (no wall at the chest and at BODY[1]: a 0.62-0.84 u
        slab), and Spire's nav ran the Elites under the rock lip at (-130.1, -343.8) where nobody walked, 22-70 s stalls
        in S6-S9 (logs/tmp/invasion_regress/bodygrid.py); people walked the open strip a cell south of it. ponytail: only
        on a map with people's walks (walked); without them the wall bits alone cannot tell a ceiling from a face a
        person stood in front of (Spire: 36% of such cells were walked)."""
        low = np.zeros(s.lz.shape, bool)
        if not s.walked_cells:
            return low
        z = np.nan_to_num(s.lz, nan=-1e6)
        wl = np.broadcast_to(walls[None], (s.L,) + walls.shape)
        cand = np.isfinite(s.lz) & (s._wall_bit(wl, z + EYE) | s._wall_bit(wl, z + HEAD_TOP)) & ~s._wall_bit(wl, z + KNEE)
        for l, j, i in np.argwhere(cand):
            x, y = s.x0 + (i + 0.5) * NAV_CELL, s.y0 + (j + 0.5) * NAV_CELL
            k0, k1, kz = round(x / 0.25), round(y / 0.25), round(float(s.lz[l, j, i]) / 0.1)
            if not any((k0, k1, kz + dz) in s.walked_cells for dz in range(-3, 4)):
                low[l, j, i] = True
        return low

    def _corners_clear(s, ok, src, ls, ld, sj, si, zs, zd, lim):
        """For the diagonal edges `ok` (a mask over the source slice `src`), stepping (sj, si) cells: does each of the two
        cells beside the step (the source's neighbour along j and along i) hold walkable floor at the step's start or end
        height (within the edge's climb limit)? Returns the mask of edges that do. Tested at the edges that survived
        only, a million at a time (Forge canvases hold millions of cells)."""
        jj, ii = np.nonzero(ok)
        out = np.zeros(ok.shape, bool)
        rj, ri = jj + src[0].start, ii + src[1].start
        tol = (lim[jj, ii] if np.ndim(lim) else lim) + 0.03
        for lo in range(0, len(jj), 1 << 20):
            sl = slice(lo, lo + (1 << 20))
            a_, b_, za_, zb_ = rj[sl], ri[sl], zs[jj[sl], ii[sl]], zd[jj[sl], ii[sl]]
            t_ = tol[sl] if np.ndim(tol) else tol
            good = np.ones(len(a_), bool)
            for mj, mi in ((a_ + sj, b_), (a_, b_ + si)):
                zl = s.lz[:, mj, mi]
                with np.errstate(invalid="ignore"):
                    hit = s.lwalk[:, mj, mi] & ((np.abs(zl - za_) <= t_) | (np.abs(zl - zb_) <= t_))
                good &= hit.any(axis=0)
            out[jj[sl], ii[sl]] = good
        return out

    def _ring2(s, near):
        """The cells that touch a near-wall cell (4-neighbours, same level) and are not one themselves: the second ring."""
        out = near.copy()
        out[:, 1:, :] |= near[:, :-1, :]
        out[:, :-1, :] |= near[:, 1:, :]
        out[:, :, 1:] |= near[:, :, :-1]
        out[:, :, :-1] |= near[:, :, 1:]
        return out & s.lwalk & ~near

    def _near_wall(s, step):
        """Per node: does a neighbour cell rise into a wall (no surface you could step onto, and
        solid above head-ish height)? Drop-offs are not walls. Also sets s.near_ledge: a neighbour with no floor within
        LEDGE_MIN under this one (and no wall: the lip of a drop)."""
        top = s.top[::step, ::step]
        pad = lambda a, v: np.pad(a, 1, constant_values=v)
        near = np.zeros(s.lz.shape, bool)
        ledge = np.zeros(s.lz.shape, bool)
        for l in range(s.L):
            z = s.lz[l]
            for dj in (-1, 0, 1):
                for di in (-1, 0, 1):
                    if not (dj or di):
                        continue
                    sl = (slice(1 + dj, 1 + dj + s.nh), slice(1 + di, 1 + di + s.nw))
                    ntop = pad(top, 1e3)[sl]
                    step_ok = np.zeros_like(near[l])
                    level = np.zeros_like(near[l])
                    for m in range(s.L):
                        nz = pad(s.lz[m], np.nan)[sl]
                        with np.errstate(invalid="ignore"):
                            step_ok |= (nz - z <= MAX_RISE) & (nz - z >= -MAX_DROP)
                            level |= (nz - z <= MAX_RISE) & (nz - z >= -LEDGE_MIN)
                    with np.errstate(invalid="ignore"):
                        wall = ~step_ok & (ntop > z + 0.3)
                        near[l] |= wall
                        ledge[l] |= ~level & ~(ntop > z + 0.3)
        s.near_ledge = ledge & s.lwalk
        return near & s.lwalk

    def _cell(s, p):
        return (min(max(int((p.y - s.y0) / NAV_CELL), 0), s.nh - 1),
                min(max(int((p.x - s.x0) / NAV_CELL), 0), s.nw - 1))

    def open_spot(s, center, r, rng, tries=48, ring=2.0, dz=2.5):
        """A walkable spot within r of center, on about its level (dz), out in the open: of `tries` random cells, one of
        the most open (the share of walkable cells at its level `ring` u around it), else None."""
        j0, i0 = s._cell(center)
        k = int(r / NAV_CELL)
        found = []
        for _ in range(tries):
            j, i = j0 + rng.randint(-k, k), i0 + rng.randint(-k, k)
            if not (0 <= j < s.nh and 0 <= i < s.nw) or (j - j0) ** 2 + (i - i0) ** 2 > k * k:
                continue
            l = s._layer(j, i, center.z)
            if l is None or abs(float(s.lz[l, j, i]) - center.z) > dz:
                continue
            if getattr(s, "oob", None) is not None and s.oob[l, j, i]:
                continue                         # (never a spot out of bounds: set_oob)
            z, ok, n = float(s.lz[l, j, i]), 0, 0
            for a in range(16):
                for d in (ring * 0.5, ring):
                    jj = j + int(round(math.sin(a * math.pi / 8) * d / NAV_CELL))
                    ii = i + int(round(math.cos(a * math.pi / 8) * d / NAV_CELL))
                    n += 1
                    if 0 <= jj < s.nh and 0 <= ii < s.nw and any(
                            s.lwalk[ll, jj, ii] and abs(float(s.lz[ll, jj, ii]) - z) < 0.6 for ll in range(s.L)):
                        ok += 1
            found.append((ok / n, Vec(s.x0 + (i + 0.5) * NAV_CELL, s.y0 + (j + 0.5) * NAV_CELL, z)))
        if not found:
            return None
        found.sort(key=lambda f: -f[0])
        return rng.choice(found[:max(1, len(found) // 4)])[1]

    def ground_ok(s, x, y, z, drop=4.0):
        """Is there a floor at (x, y) at most `drop` below height z (or a step above it)? False
        over the open void (Halo 2's Lockout, Ascension) or outside the map: stepping there is
        a fall to death."""
        if not (s.x0 <= x < s.x0 + s.nw * NAV_CELL and s.y0 <= y < s.y0 + s.nh * NAV_CELL):
            return False
        j, i = s._cell(Vec(x, y))
        f = s.lz[:, j, i]
        if np.any(np.isfinite(f) & (f >= z - drop) & (f <= z + 0.8)):
            return True
        # no floor in reach: a drop only if the column's lowest surface is far below (the void and its
        # distant bottom, or nothing at all); a base wall or a rock is solid there. A lowest surface
        # well ABOVE head height is a ceiling or an overhang with open air under it, not a wall: Halo 3
        # Construct's top-level edge reads "low" 17 over the void at 10.5 and bots walked off it four
        # times in one match. A door lintel just over the head stays solid (3 units)
        low = s.low[j * s.nstep, i * s.nstep]
        return bool(np.isfinite(low) and z - drop <= low <= z + 3.0)

    def _layer(s, j, i, z, reach_from=None, tan=0.0):
        """Layer of cell (j, i) nearest height z (only layers a step from reach_from can get to, if given; tan: the slope
        of the floor stepped from, a placed ramp's as the nav's edges). None if there is none."""
        best = None
        for l in range(s.L):
            if not s.lwalk[l, j, i]:
                continue
            if reach_from is not None and not climb_ok(reach_from, s.lz[l, j, i]):
                if s.tz is None or not -MAX_DROP <= s.lz[l, j, i] - reach_from <= (
                        max(tan, float(s.tz[l, j, i])) * NAV_CELL * 1.414 + 0.02):
                    continue
            if best is None or abs(s.lz[l, j, i] - z) < abs(s.lz[best, j, i] - z):
                best = l
        return best

    def _node(s, p):
        """Nav node for a position: its layer by height, and if a wall blocks that cell (the bot
        is hugging it), the nearest open cell on the same level."""
        j, i = s._cell(p)
        zs = [(abs(s.lz[l, j, i] - p.z), l) for l in range(s.L) if np.isfinite(s.lz[l, j, i])]
        l = min(zs)[1] if zs else 0
        if zs and min(zs)[0] > 1.0 and p.z > s.lz[l, j, i]:
            # a thing standing on a stand whose own cell's floor is far below (Spire's Invasion core: its cell's only
            # floor is a hole 2 u under it, and every route ended down there, never on the plateau beside it): the
            # nearest walkable cell within a unit at about its height
            best = None
            for jj in range(max(0, j - 4), min(s.nh, j + 5)):
                for ii in range(max(0, i - 4), min(s.nw, i + 5)):
                    for ll in range(s.L):
                        if s.lwalk[ll, jj, ii] and -1.0 < p.z - s.lz[ll, jj, ii] < 1.0:
                            d = (jj - j) ** 2 + (ii - i) ** 2
                            if best is None or d < best[0]:
                                best = (d, ll, jj, ii)
            if best:
                return best[1:]
        elif zs and min(zs)[0] > 0.6 and p.z < s.lz[l, j, i]:
            # under its own cell's nearest floor by more than a step: it stands on a floor whose edge the cell's centre
            # misses (a trough's edge under a stair's tread). The neighbour's floor at its height, not the tread over
            # it: read as on the tread, the next point was its own cell and it never moved on (live Boneyard
            # 20261105-041255, the issue #4 stairs at (258.2, 106.5): 10-16 s spells at -6.15 under the -5.08 tread)
            best = None
            for jj in range(max(0, j - 2), min(s.nh, j + 3)):
                for ii in range(max(0, i - 2), min(s.nw, i + 3)):
                    for ll in range(s.L):
                        if s.lwalk[ll, jj, ii] and abs(p.z - s.lz[ll, jj, ii]) < 0.4:
                            d = (jj - j) ** 2 + (ii - i) ** 2
                            if best is None or d < best[0]:
                                best = (d, ll, jj, ii)
            if best:
                return best[1:]
        if s.lwalk[l, j, i]:
            return l, j, i
        z = s.lz[l, j, i] if zs else p.z
        for r in (1, 2, 3):
            best = None
            for jj in range(max(0, j - r), min(s.nh, j + r + 1)):
                for ii in range(max(0, i - r), min(s.nw, i + r + 1)):
                    for ll in range(s.L):
                        if s.lwalk[ll, jj, ii] and abs(s.lz[ll, jj, ii] - z) < 0.5:
                            d = (jj - j) ** 2 + (ii - i) ** 2
                            if best is None or d < best[0]:
                                best = (d, ll, jj, ii)
            if best:
                return best[1:]
        return l, j, i

    def _center(s, l, j, i):
        # waypoints at cell centres: a corner is also a cell boundary, and a bot standing on
        # one would count as being in the next cell and never move on
        x, y = s.x0 + (i + 0.5) * NAV_CELL, s.y0 + (j + 0.5) * NAV_CELL
        return Vec(x, y, float(s.lz[l, j, i]))

    def _straight_ok(s, a, l, j, i, ground=None):
        """Can you walk in a straight line from a to node (l, j, i) without climbing a wall? ground (a vehicle's: vnav
        passes vroomy): a wall's cell on that ground is no wall; its painted floor runs by walls (Spire's people's road
        at (-139.9, -348.3): the look ahead stopped at the next cell)."""
        b = s._center(l, j, i)
        L = math.hypot(b.x - a.x, b.y - a.y)
        n = max(2, int(L / (NAV_CELL * 0.5)))
        start = s._node(a)
        prev = s.lz[start]
        tan = float(s.tz[start]) if s.tz is not None else 0.0
        ll = None
        oob = getattr(s, "oob", None)                # (set_oob: no shortcut into out of bounds, unless leaving it)
        oob = oob if oob is not None and not oob[start] else None
        ledge = getattr(s, "near_ledge", None)
        for k in range(1, n + 1):
            t = k / n
            px, py = a.x + (b.x - a.x) * t, a.y + (b.y - a.y) * t
            jj, ii = s._cell(Vec(px, py))
            ll = s._layer(jj, ii, prev, reach_from=prev, tan=tan)
            if s.stuck and (jj, ii) != start[1:] and s._stuck_near(px, py, prev, s.STUCK_RADIUS + 0.1):
                return False      # a spot bots were trapped at: no straight line over it (the path search prices it)
            if ll is None or s.lz[ll, jj, ii] < prev - 0.6:
                return False      # (no drop off a ledge: the path search knows its rails and slits, Countdown)
            if oob is not None and oob[ll, jj, ii]:
                return False
            if (s.near_wall[ll, jj, ii] and (ground is None or not ground[ll, jj, ii]) and (jj, ii) != start[1:]
                    and (jj, ii) != (j, i)):
                return False      # a shortcut would scrape a wall: follow the (centred) path instead
            if (ledge is not None and ledge[ll, jj, ii] and (ground is None or not ground[ll, jj, ii])
                    and (jj, ii) != start[1:] and (jj, ii) != (j, i)):
                return False      # ... or run the lip of a drop (the path search priced the lip; a shortcut slides off it)
            prev = s.lz[ll, jj, ii]
            tan = float(s.tz[ll, jj, ii]) if s.tz is not None else 0.0
        return ll == l

    def add_links(s, pairs, cost=1.0):
        """One-way shortcuts (teleporters): walking onto pair[0] puts you at pair[1]."""
        from scipy.sparse import coo_matrix
        g = s.graph.tocoo()
        rows, cols, wts = [g.row], [g.col], [g.data]
        s.links = getattr(s, "links", {})
        for a, b in pairs:
            if not (s.inside(a.x, a.y) and s.inside(b.x, b.y)):
                continue
            lb, jb, ib = s._node(b)
            # the trigger covers the pad and a little around it: link every floor node within
            # 0.75 units that is at most 1.5 under the pad's top (pads are raised discs)
            j0, i0 = s._cell(a)
            for j in range(max(0, j0 - 3), min(s.nh, j0 + 4)):
                for i in range(max(0, i0 - 3), min(s.nw, i0 + 4)):
                    if (j - j0) ** 2 + (i - i0) ** 2 > 9:
                        continue
                    for l in range(s.L):
                        if s.lwalk[l, j, i] and -1.5 <= s.lz[l, j, i] - a.z <= 0.5:
                            s.links[(l, j, i)] = ((lb, jb, ib), a)
                            rows.append(np.array([(l * s.nh + j) * s.nw + i]))
                            cols.append(np.array([(lb * s.nh + jb) * s.nw + ib]))
                            wts.append(np.array([cost]))
        n = s.L * s.nh * s.nw
        s.graph = coo_matrix((np.concatenate(wts), (np.concatenate(rows), np.concatenate(cols))), shape=(n, n)).tocsr()
        s.graph_to = s.graph.T.tocsr()
        s.degree = np.diff(s.graph.indptr).reshape(s.L, s.nh, s.nw)
        s._fields.clear()

    def build_jumps(s):
        """Jump links for skilled players: from floor cells at an edge (fewer than 8 walk
        neighbours) to floors up to JUMP_RISE higher within JUMP_UP_REACH (a crate, a ledge), or
        on about the same level or lower across a gap up to JUMP_REACH, with no wall in between at
        chest and head height. Kept in a second graph (graph_jump); walking bots never use it."""
        from scipy.sparse import coo_matrix
        r = int(JUMP_REACH / NAV_CELL)
        offs = [(dj, di) for dj in range(-r, r + 1) for di in range(-r, r + 1)
                if 2 < dj * dj + di * di <= r * r]
        border = s.lwalk & (s.degree < 8)
        ls, js, is_ = np.nonzero(border)
        rows, cols, wts = [], [], []
        s.jumps = {}
        for dj, di in offs:
            jj, ii = js + dj, is_ + di
            ok = (jj >= 0) & (jj < s.nh) & (ii >= 0) & (ii < s.nw)
            a_l, a_j, a_i, b_j, b_i = ls[ok], js[ok], is_[ok], jj[ok], ii[ok]
            dist = math.hypot(dj, di) * NAV_CELL
            za = s.lz[a_l, a_j, a_i]
            mids = [(a_j + int(round(dj * f)), a_i + int(round(di * f))) for f in (1 / 3, 2 / 3)]
            for lb in range(s.L):
                zb = s.lz[lb, b_j, b_i]
                with np.errstate(invalid="ignore"):
                    rise = zb - za
                    up = (rise > MAX_RISE) & (rise <= JUMP_RISE) & (dist <= JUMP_UP_REACH)
                    across = (np.abs(rise) <= MAX_RISE) & (dist > 0.75)
                    down = (rise < -MAX_RISE) & (rise >= -MAX_DROP) & (dist > 0.75)
                    want = s.lwalk[lb, b_j, b_i] & (up | across | down)
                if not want.any():
                    continue
                with np.errstate(invalid="ignore"):
                    # across/down jumps must cross a real gap: no floor near our level on the way
                    mid_floor = np.zeros(want.shape, bool)
                    for mj, mi in mids:
                        for lm in range(s.L):
                            mid_floor |= s.lwalk[lm, mj, mi] & (np.abs(s.lz[lm, mj, mi] - za) <= 0.5)
                    want &= up | ~mid_floor
                    zr = np.maximum(za, zb)
                    # nothing to bump into on the way: no floor (a rock top, a crate) inside the arc
                    for mj, mi in mids:
                        for lm in range(s.L):
                            zm = s.lz[lm, mj, mi]
                            want &= ~((zm > za + 0.4) & (zm < zr + 1.5))
                    walled = np.zeros(want.shape, bool)
                    for mj, mi in mids:
                        w = s.walls[mj * s.nstep, mi * s.nstep]
                        walled |= s._wall_bit(w, zr + BODY[0]) & s._wall_bit(w, zr + BODY[1])
                    want &= ~walled
                for k in np.nonzero(want)[0]:
                    src = (int(a_l[k]), int(a_j[k]), int(a_i[k]))
                    dst = (lb, int(b_j[k]), int(b_i[k]))
                    s.jumps.setdefault(src, []).append(dst)
                    rows.append((src[0] * s.nh + src[1]) * s.nw + src[2])
                    cols.append((dst[0] * s.nh + dst[1]) * s.nw + dst[2])
                    wts.append(dist * 1.5 + 1.0)            # a jump is a little slower and riskier
        n = s.L * s.nh * s.nw
        g = s.graph.tocoo()
        s.graph_jump = coo_matrix((np.r_[g.data, wts], (np.r_[g.row, rows], np.r_[g.col, cols])),
                                  shape=(n, n)).tocsr()
        s.graph_jump_base = s.graph_jump     # without people's jumps (add_human_links builds on it)
        s.graph_jump_to = s.graph_jump.T.tocsr()
        s._jfields = {}
        return len(wts)

    FLOORED = 0.9   # walked_climb: this share of the person's path over a nav floor (within 1.2 u under, 0.3 over)

    def floored(s, k):
        """A walked climb (walked_climb) is a walk over floor the level has wrong: the nav's floor under the person's
        path, at most 1.2 u down (Boneyard's ramp under phase 1, its plank's lip smoothed: every point), at FLOORED of
        its points. Where a third of it has none (a rock face steeper than the floor test: Spire's climb up to phase 1's
        territory A at (-135.4, -357.8), 64%) it stays the jump it was: walked, 4 of 37 attacker visits got up and
        phase 1 was lost all 4 rounds of S5 / S6 (8 of 8 taken as jumps, S1-S4; S7 with them off: taken at 85 s)."""
        pts = [q[:3] for q in (k.get("path") or [])] or [k["take_off"], k["land"]]
        n = 0
        for q in pts:
            if s.inside(q[0], q[1]):
                j, i = s._cell(Vec(*q))
                n += any(q[2] - 1.2 <= z <= q[2] + 0.3 for z in s.lz[:, j, i].tolist() if math.isfinite(z))
        return n >= s.FLOORED * len(pts)

    def add_human_links(s, links):
        """Jumps people made in this game and landed (tools/lidar.py, maps/<map>/lidar.json): proven
        by doing, so every bot that jumps at all may use them, in every game (the CE-only jump
        physics of build_jumps is not needed). A link whose take-off or landing is not on a
        walkable node is skipped. Edges go into graph_h (walking + these) and graph_jump. Safe to
        call again with the whole list as it grows (LiDAR mode, live): spots learned since load
        (learn_stuck) keep their cost."""
        from scipy.sparse import coo_matrix
        s.hjumps = []                            # (src node, dst node, link, edge cost)
        s.hroutes = []                           # crouch routes both ways, recorded routes one way: (src node, dst node, path, edge cost)
        rows, cols, wts = [], [], []
        for k in links:
            a, b = Vec(*k["take_off"][:3]), Vec(*k["land"][:3])
            if not (s.inside(a.x, a.y) and s.inside(b.x, b.y)):
                continue
            src, dst = s._node(a), s._node(b)
            if src == dst or not (s.lwalk[src] and s.lwalk[dst]):
                continue
            if abs(s.lz[src] - a.z) > 0.7 or abs(s.lz[dst] - b.z) > 0.7:
                continue
            climb = walked_climb(k, getattr(s, "jump_apex", 0.8)) and s.floored(k)   # (a walk: both ways)
            if k.get("kind") in ("crouch", "route") or climb:   # about the path's length; a crouch path or a climb
                path = route_points(k)                  # both ways, a recorded route only the way it was done
                cost = sum(math.dist(p[:2], q[:2]) for p, q in zip(path, path[1:])) * 1.5 + 0.5
                ways = ((src, dst, path), (dst, src, path[::-1])) if k.get("kind") == "crouch" or climb else \
                    ((src, dst, path),)
                if climb:
                    # up it, a hop on at the take-off, where the person left the ground (its foot is a lip: walked
                    # at, live 20261105-031842's attackers went its whole length on the floor under it, z -10), from
                    # his run-up first: coming at the take-off from beside, the hop went across the foot, not onto it
                    # (live 033058: from the west, every hop landed east of the ramp on the floor)
                    up, f0 = ((*path[0][:3], path[0][3] | ROUTE_JUMP),) + tuple(path[1:]), src
                    run = k.get("runup")
                    if run and s.inside(run[0], run[1]):
                        rn = s._node(Vec(*run[:3]))
                        if s.lwalk[rn] and abs(s.lz[rn] - a.z) < 0.5 and math.hypot(run[0] - a.x, run[1] - a.y) < 3.0:
                            up, f0 = ((run[0], run[1], run[2], 0),) + up, rn
                    ways = ((f0, dst, up), ways[1])
                for f, t, pth in ways:
                    s.hroutes.append((f, t, tuple(pth), cost))
                    rows.append((f[0] * s.nh + f[1]) * s.nw + f[2])
                    cols.append((t[0] * s.nh + t[1]) * s.nw + t[2])
                    wts.append(cost)
                continue
            w = math.hypot(b.x - a.x, b.y - a.y) * 1.5 + 1.0
            if b.z - a.z >= 10.0:
                # a launch up (a man cannon, a lift): its time aloft at a run's 2.25 u/s, not 1.5 x its flat length
                # (Spire, live match 230700: the mini base cannon's 24 u flight cost 38 and a bot by its pad walked
                # on to the middle lift, "just missed man cannon"; the middle lift's 6 s ride cost 5)
                w = float(k.get("air_s") or math.hypot(b.x - a.x, b.y - a.y) / 3.5) * 2.25 + 1.0
            ways = [(src, dst, k)]
            if jump_ways(k, s, getattr(s, "jump_apex", 0.8)) == "two-way":
                ways.append((dst, src, reverse_jump(k)))     # usable back the other way too
            for f, t, kk in ways:
                s.hjumps.append((f, t, kk, w))
                rows.append((f[0] * s.nh + f[1]) * s.nw + f[2])
                cols.append((t[0] * s.nh + t[1]) * s.nw + t[2])
                wts.append(w)
        n = s.L * s.nh * s.nw
        g = s.graph.tocoo()
        s.graph_h = coo_matrix((np.r_[g.data, wts], (np.r_[g.row, rows], np.r_[g.col, cols])), shape=(n, n)).tocsr()
        s.graph_h_to = s.graph_h.T.tocsr()
        rebuilt = [s.graph_h_to]
        if hasattr(s, "graph_jump"):
            g = s.graph_jump_base.tocoo()        # not the last call's graph: its people's edges would add up
            s.graph_jump = coo_matrix((np.r_[g.data, wts], (np.r_[g.row, rows], np.r_[g.col, cols])), shape=(n, n)).tocsr()
            s.graph_jump_to = s.graph_jump.T.tocsr()
            rebuilt.append(s.graph_jump_to)
            s._jfields = {}
        for key in s.stuck:                      # spots learned so far, on the new graphs too
            s._stuck_cost(key, rebuilt)
        s._hfields = {}
        s._refresh_barriers()                    # (the new graphs carry none yet)
        return len(wts)

    # --- barriers: placed objects the level mesh does not have (Invasion's force-field doors and gates, Forge pieces).
    # Each is a vertical rectangle seen from above as segment a-b, standing from z0 to z1. A one-way shield lets a body
    # through in one direction only (`through`, a unit vector); anything else blocks both ways while it is on. Its
    # cost goes on every graph edge that crosses the segment at standing height (BARRIER_COST: a wall, unless there
    # is no other way at all), on whichever graphs exist, and comes off when the barrier goes (a gate opening).
    BARRIER_COST = 1e5

    def set_barrier(s, key, a, b, z0, z1, through=None, on=True):
        """Add, move, switch on or off one barrier (key: any hashable name). Cheap when nothing changes."""
        bars = s.__dict__.setdefault("barriers", {})
        new = (a.x, a.y, b.x, b.y, z0, z1, None if through is None else (through.x, through.y), on)
        if bars.get(key) == new:
            return
        bars[key] = new
        s._refresh_barriers()

    def set_barriers(s, items):
        """set_barrier for many at once, {key: (a, b, z0, z1, through, on)}, with one refresh (brain/forge.py
        PieceWatch: a wall of pieces appearing together)."""
        bars = s.__dict__.setdefault("barriers", {})
        changed = False
        for key, (a, b, z0, z1, through, on) in items.items():
            new = (a.x, a.y, b.x, b.y, z0, z1, None if through is None else (through.x, through.y), on)
            if bars.get(key) != new:
                bars[key], changed = new, True
        if changed:
            s._refresh_barriers()

    def set_glass(s, key, pos, axes, lo, hi):
        """A piece that stops shots but not sight (a Forge shield, brain/forge.py): its box (pos, (forward, left, up),
        min, max in its own axes); los and shot_los through it are blocked, sees passes it."""
        s.__dict__.setdefault("glass", {})[key] = (np.asarray(pos, float), np.asarray(axes, float), np.asarray(lo, float),
                                                   np.asarray(hi, float))

    def _through_glass(s, p, q):
        """Does the segment p-q pass through a glass box (slab test in the box's own axes)?"""
        for pos, axes, lo, hi in getattr(s, "glass", {}).values():
            a = axes @ (np.asarray(p, float) - pos)
            d = axes @ (np.asarray(q, float) - pos) - a
            t0, t1 = 0.0, 1.0
            for k in range(3):
                if abs(d[k]) < 1e-9:
                    if a[k] < lo[k] or a[k] > hi[k]:
                        break
                    continue
                u, v = (lo[k] - a[k]) / d[k], (hi[k] - a[k]) / d[k]
                t0, t1 = max(t0, min(u, v)), min(t1, max(u, v))
                if t0 > t1:
                    break
            else:
                return True
        return False

    def crosses_barrier(s, a, b):
        """Does a step from a to b go through a barrier that is on (a one-way shield the wrong way)? The simulator's
        bodies use it: a gate is solid in the game, and bots walked through them in simulated Invasion."""
        for ax, ay, bx, by, z0, z1, thru, on in getattr(s, "barriers", {}).values():
            if not on or max(a.z, b.z) < z0 - 1.0 or min(a.z, b.z) > z1 - 0.3:
                continue
            side = lambda ux, uy, vx, vy, wx, wy: (vx - ux) * (wy - uy) - (vy - uy) * (wx - ux)
            if ((side(ax, ay, bx, by, a.x, a.y) > 0) != (side(ax, ay, bx, by, b.x, b.y) > 0)
                    and (side(a.x, a.y, b.x, b.y, ax, ay) > 0) != (side(a.x, a.y, b.x, b.y, bx, by) > 0)):
                if thru is None or (b.x - a.x) * thru[0] + (b.y - a.y) * thru[1] < 0:
                    return True
        return False

    def _crossing(s, g, bar):
        """Data indices of reversed graph g (row = edge end, column = edge start) whose edge crosses barrier bar."""
        ax, ay, bx, by, z0, z1, thru, _ = bar
        ends = getattr(g, "_hb_ends", None)
        if ends is None:                         # every edge's two node positions, once per graph
            n = s.nh * s.nw
            dst = np.repeat(np.arange(g.shape[0]), np.diff(g.indptr))
            src = g.indices
            def xyz(v):
                l, r = np.divmod(v, n)
                j, i = np.divmod(r, s.nw)
                return s.x0 + (i + 0.5) * NAV_CELL, s.y0 + (j + 0.5) * NAV_CELL, s.lz[l, j, i]
            ends = g._hb_ends = (xyz(src), xyz(dst))
        (x1, y1, z1s), (x2, y2, z2s) = ends
        pad = NAV_CELL * 2
        near = ((np.minimum(x1, x2) <= max(ax, bx) + pad) & (np.maximum(x1, x2) >= min(ax, bx) - pad) &
                (np.minimum(y1, y2) <= max(ay, by) + pad) & (np.maximum(y1, y2) >= min(ay, by) - pad))
        k = np.nonzero(near)[0]
        if not k.size:
            return k
        px, py, qx, qy = x1[k], y1[k], x2[k], y2[k]
        side = lambda ux, uy, vx, vy, wx, wy: np.sign((vx - ux) * (wy - uy) - (vy - uy) * (wx - ux))
        cross = ((side(ax, ay, bx, by, px, py) != side(ax, ay, bx, by, qx, qy)) &
                 (side(px, py, qx, qy, ax, ay) != side(px, py, qx, qy, bx, by)))
        zs = np.minimum(z1s[k], z2s[k])
        cross &= (np.maximum(z1s[k], z2s[k]) >= z0 - 1.0) & (zs <= z1 - 0.3)   # stands within the barrier's height
        if thru is not None:                     # a one-way shield: only the wrong way is blocked
            cross &= (qx - px) * thru[0] + (qy - py) * thru[1] < 0
        return k[cross]

    def _refresh_barriers(s):
        bars = getattr(s, "barriers", {})
        for name in ("graph_to", "graph_jump_to", "graph_h_to"):
            g = getattr(s, name, None)
            if g is None:
                continue
            old = getattr(g, "_hb_applied", None)
            if old is not None and old.size:
                g.data[old] -= s.BARRIER_COST
            idx = [s._crossing(g, bar) for bar in bars.values() if bar[7]]
            idx = np.unique(np.concatenate(idx)) if idx else np.zeros(0, np.int64)
            g.data[idx] += s.BARRIER_COST
            g._hb_applied = idx
        s._apply_oob()
        s._fields.clear()
        getattr(s, "_jfields", {}).clear()
        getattr(s, "_hfields", {}).clear()

    # --- out of bounds (brain/oob.py): learned spots of the games' kill and soft kill zones, a soft wall for every bot
    def set_oob(s, spots, version=None, volumes=None):
        """Each spot's floors within oob.R (flat) and oob.DZ of its height are out of bounds, and the floors of the map's
        known volumes (volumes: oob.volumes, kept from the last call when None; oob.floor_mask): every step into one
        costs BARRIER_COST (the reversed walking graphs, as a barrier's: _apply_oob), a goal there moves to the nearest
        floor out of it (_reachable_node), the vehicles' ground leaves them out (built again on the next drive), a
        pilot's probe sees each spot and volume (oob_avoid). Cheap when the version has not changed."""
        from . import oob as O
        if volumes is not None and volumes is not getattr(s, "_oob_vols", None):
            s._oob_vols = volumes
            s._oob_vmask = O.floor_mask(volumes, s.lz, s.x0, s.y0, NAV_CELL) if volumes else None
            s._oob_air = [(Vec(x, y, z), r, band) for x, y, z, r, band in O.air_discs(volumes)]
            s._oob_version = None
        if version is not None and getattr(s, "_oob_version", None) == version:
            return
        s._oob_version = version
        vm = getattr(s, "_oob_vmask", None)
        mask = vm.copy() if vm is not None else np.zeros(s.lz.shape, bool)
        k = int(O.R / NAV_CELL) + 1
        for x, y, z, *_ in spots:
            if not s.inside(x, y):
                continue
            j, i = s._cell(Vec(x, y, z))
            j0, j1, i0, i1 = max(0, j - k), min(s.nh, j + k + 1), max(0, i - k), min(s.nw, i + k + 1)
            yy, xx = np.mgrid[j0:j1, i0:i1]
            disk = (s.y0 + (yy + 0.5) * NAV_CELL - y) ** 2 + (s.x0 + (xx + 0.5) * NAV_CELL - x) ** 2 <= O.R ** 2
            with np.errstate(invalid="ignore"):
                band = np.isfinite(s.lz[:, j0:j1, i0:i1]) & (np.abs(s.lz[:, j0:j1, i0:i1] - z) < O.DZ)
            mask[:, j0:j1, i0:i1] |= band & disk[None]
        s.oob = mask if mask.any() else None
        s.oob_spots = [Vec(float(x), float(y), float(z)) for x, y, z, *_ in spots]
        s._refresh_barriers()
        s.__dict__.pop("graph_v", None)          # (its ground leaves them out: _build_vnav)

    def _apply_oob(s):
        """Out of bounds' cost on whichever reversed walking graphs exist (row = an edge's end): off from the last
        time, on again for the cells now out (_refresh_barriers runs it after every graph build, as barriers')."""
        flat = s.oob.ravel() if getattr(s, "oob", None) is not None else None
        ends = np.nonzero(flat)[0] if flat is not None else np.zeros(0, np.int64)
        for name in ("graph_to", "graph_jump_to", "graph_h_to"):
            g = getattr(s, name, None)
            if g is None:
                continue
            old = getattr(g, "_hb_oob", None)
            if old is not None and old.size:
                g.data[old] -= s.BARRIER_COST
            idx = np.concatenate([np.arange(g.indptr[r], g.indptr[r + 1]) for r in ends]) if ends.size else \
                np.zeros(0, np.int64)
            g.data[idx] += s.BARRIER_COST
            g._hb_oob = idx

    def oob_avoid(s):
        """The out of bounds spots for a pilot's probe (fly_clear's avoid): (spot, oob.R, oob.AIR_DZ), and the known
        volumes' discs (oob.air_discs)."""
        from . import oob as O
        return [(q, O.R, O.AIR_DZ) for q in getattr(s, "oob_spots", ())] + getattr(s, "_oob_air", [])

    def air_roof(s, x, y):
        """The highest an aircraft may be at (x, y) by the map's known volumes (set_oob): the top of the safe zone over
        it (out of a soft_safe volume is the soft kill: Spire's safe box ends at -16.2, 6.7 u over the core, and every
        pilot that flew to the core went over it and died 9-10 s later, live Oct 31 - Nov 5), and the bottom of a kill
        volume over its floors (Spire's kill_roof); +inf where none says. A safe top or kill bottom under the highest
        floor there says nothing of its air. ponytail: sphere volumes left out (Spire's 150 u safe sphere read as a
        radius would cover the spire top those pilots died over); cached per 2 u cell."""
        from . import oob as O
        vols = getattr(s, "_oob_vols", None)
        if not vols:
            return math.inf
        cache = s.__dict__.setdefault("_roof", {})
        if cache.get("vols") is not vols:
            cache.clear()
            cache["vols"] = vols
        key = (int(x // 2.0), int(y // 2.0))
        if key in cache:
            return cache[key]
        j, i = s._cell(Vec(x, y, 0.0))
        col = s.lz[:, j, i]
        top = float(np.nanmax(col)) if np.isfinite(col).any() else s.ground(x, y)
        safe, roof = [], math.inf
        for v in vols:
            if v.get("shape") == "sphere" or v["kind"] not in O.KILLS + O.SAFES:
                continue
            lo, hi = O.bounds(v)
            mid = (lo[2] + hi[2]) / 2.0
            if not O.contains(v, (x, y, mid)).any():
                continue
            if v["kind"] in O.SAFES and hi[2] > top + 2.0:
                safe.append(float(hi[2]))
            elif v["kind"] in O.KILLS and lo[2] > top + 2.0:
                roof = min(roof, float(lo[2]))
        if safe:
            roof = min(roof, max(safe))
        cache[key] = roof
        return roof

    def out_of_bounds(s, p):
        """Is p on a floor out of bounds (set_oob)?"""
        if getattr(s, "oob", None) is None or not s.inside(p.x, p.y):
            return False
        return bool(s.oob[s._node(p)])

    def set_spawns(s, points):
        """Mark the nodes a player can reach from the map's spawn points; goals elsewhere (the top
        of a rock you cannot climb) are moved to the nearest reachable node."""
        from scipy.sparse.csgraph import breadth_first_order
        s.reach = np.zeros(s.L * s.nh * s.nw, bool)
        points = [p for p in points if s.inside(p.x, p.y)]
        if not points:                             # none in a variant's window: its own spawns come later
            s.reach = s.lwalk.copy()
            s._fields.clear()
            return
        for p in points:
            l, j, i = s._node(p)
            start = (l * s.nh + j) * s.nw + i
            if not s.reach[start]:                 # people's jumps count: a ledge only they reach is reachable
                g = getattr(s, "graph_h", s.graph)
                s.reach[breadth_first_order(g, start, directed=True, return_predecessors=False)] = True
        # traps: floor a player reaches (a drop in) that no way leads out of to any spawn, over every edge the nav has
        # (walking, teleporters, jump links, people's jumps and rides). Movement never steps into one from outside
        # (can_step): h3_construct's lift wells, 2.5 u under the rim, were such a pocket; with LiDAR's lift links
        # drawing bots to the rims, fighters backed into them and pushed at the wall there for the rest of the life
        # (sim A/B Nov 4: 2,667 bot-ticks in the wells in 90 s, none without LiDAR)
        back = np.zeros(s.L * s.nh * s.nw, bool)
        g_to = getattr(s, "graph_jump_to", None)
        g_to = g_to if g_to is not None else getattr(s, "graph_h_to", s.graph_to)
        for p in points:
            l, j, i = s._node(p)
            start = (l * s.nh + j) * s.nw + i
            if not back[start]:
                back[breadth_first_order(g_to, start, directed=True, return_predecessors=False)] = True
        s.trap = (s.reach.ravel() & ~back).reshape(s.L, s.nh, s.nw)
        s.reach = s.reach.reshape(s.L, s.nh, s.nw)
        s._fields.clear()

    def _reachable_node(s, l, j, i, z):
        """(l, j, i) if reachable, else the nearest reachable node (xy first, then height); never one out of bounds
        (set_oob: a goal there moves out of it)."""
        reach, oob = getattr(s, "reach", None), getattr(s, "oob", None)
        if (reach is None or reach[l, j, i]) and (oob is None or not oob[l, j, i]):
            return l, j, i
        # up to 10 units around; out of bounds, on its own level first (not the floor under a cliff it sits on the edge of)
        out = oob is not None and oob[l, j, i]
        for level in ((True, False) if out else (False,)):
            for r in range(1, 41):
                j0, j1, i0, i1 = max(0, j - r), min(s.nh, j + r + 1), max(0, i - r), min(s.nw, i + r + 1)
                sub = reach[:, j0:j1, i0:i1] if reach is not None else s.lwalk[:, j0:j1, i0:i1]
                if oob is not None:
                    sub = sub & ~oob[:, j0:j1, i0:i1]
                if level:
                    with np.errstate(invalid="ignore"):
                        sub = sub & (np.abs(s.lz[:, j0:j1, i0:i1] - z) < 1.5)
                if sub.any():
                    ls, js, is_ = np.nonzero(sub)
                    d = (js + j0 - j) ** 2 + (is_ + i0 - i) ** 2 + (s.lz[ls, js + j0, is_ + i0] - z) ** 2 * 4
                    k = int(np.argmin(d))
                    return int(ls[k]), int(js[k] + j0), int(is_[k] + i0)
        return l, j, i

    def reaches(s, a, b, jump=False, human=False):
        """Does the nav have a way from a to b (b's own floor, or the one beside it a thing on a stand is reached from)?"""
        l, j, i = s._node(a)
        return bool(np.isfinite(s._field(b, jump, human)[l, j, i]))

    RISKY_DROP = 8.0     # a person's jump down more than this is costed like a wall for a carrier (safe=True)

    def _risky(s, graph, tele=False):
        """Data positions in a transposed graph of people's jumps that drop more than RISKY_DROP. tele: every edge onto
        or off a teleporter's trigger instead (add_links: stepping on one takes it, so a walk over the pad is a ride;
        priced off its link alone, the way round walked over the pad)."""
        memo = s.__dict__.setdefault("_risky_memo", {})
        if memo.get((id(graph), tele), (None,))[0] is not graph:
            if tele:
                pads = sorted({(l * s.nh + j) * s.nw + i for l, j, i in getattr(s, "links", {})})
                pos = np.unique(np.concatenate([np.arange(graph.indptr[r], graph.indptr[r + 1]) for r in pads] +
                                               [np.nonzero(np.isin(graph.indices, pads))[0]])) if pads else []
            else:
                pos = []
                for f, t, k, w in getattr(s, "hjumps", ()):
                    if k["take_off"][2] - k["land"][2] > s.RISKY_DROP:
                        r, c = (t[0] * s.nh + t[1]) * s.nw + t[2], (f[0] * s.nh + f[1]) * s.nw + f[2]
                        lo, hi = graph.indptr[r], graph.indptr[r + 1]
                        pos += [lo + int(h) for h in np.nonzero(graph.indices[lo:hi] == c)[0]]
            memo[(id(graph), tele)] = (graph, np.array(pos, dtype=np.int64))
        return memo[(id(graph), tele)][1]

    def _field(s, goal, jump=False, human=False, safe=False, limit=None, no_tele=False, hazards=()):
        # limit: costs out to this far only (a search round the goal, not the map: brain/bot.py man_appears)
        """Cost-to-goal over the walkable grid (Dijkstra), cached per goal node; with jump=True over the grid plus jump
        links (and people's jumps), with human=True plus people's jumps. safe=True: people's jumps down more than
        RISKY_DROP cost a wall (a carrier goes round them, any way there is; live Spire, both core carriers died under
        the 37 u drop people use, 39 of 43 others lived). no_tele=True: the teleporters cost a wall too (map brain
        INTER-TELE-01: the way round a held exit, Bot.pad_held)."""
        l, j, i = s._node(goal)
        if not -2.0 < goal.z - s.lz[l, j, i] < 1.0:
            # no floor near the goal's height in its cell (a cell keeps four floors: Spire's core platform lost to the
            # four under it, and attackers waited 35 u under the core, live): a neighbour's floor at its height
            r = 8
            j0, j1, i0, i1 = max(0, j - r), min(s.nh, j + r + 1), max(0, i - r), min(s.nw, i + r + 1)
            with np.errstate(invalid="ignore"):          # (a floor at or up to 2 u under it: the live core hangs
                dz = goal.z - s.lz[:, j0:j1, i0:i1]       # 0.5 u over its stand, 1.2 over the platform)
                sub = s.lwalk[:, j0:j1, i0:i1] & (dz > -0.5) & (dz < 2.0)
            if getattr(s, "reach", None) is not None and (sub & s.reach[:, j0:j1, i0:i1]).any():
                sub &= s.reach[:, j0:j1, i0:i1]      # (one a path reaches: else the reachable floor below was taken)
            if sub.any():
                ls, js, is_ = np.nonzero(sub)
                k = int(np.argmin((js + j0 - j) ** 2 + (is_ + i0 - i) ** 2))
                l, j, i = int(ls[k]), int(js[k] + j0), int(is_[k] + i0)
        l, j, i = s._reachable_node(l, j, i, goal.z)
        sj, si = j - j % 2, i - i % 2          # share fields between goals in the same half-unit square
        key = (l, sj, si) if s.lwalk[l, sj, si] and abs(s.lz[l, sj, si] - s.lz[l, j, i]) < 0.3 and \
            (getattr(s, "oob", None) is None or not s.oob[l, sj, si]) else (l, j, i)   # (not one out of bounds)
        jump = jump and hasattr(s, "graph_jump")
        human = human and not jump and hasattr(s, "graph_h")      # graph_jump already holds people's jumps
        cache = s._jfields if jump else s._hfields if human else s._fields
        graph = s.graph_jump_to if jump else s.graph_h_to if human else s.graph_to
        risky = s._risky(graph) if safe and (jump or human) else ()
        ckey = ("safe",) + key if len(risky) else key       # (same cache: every clear clears these too)
        tele = s._risky(graph, True) if no_tele else ()
        if len(tele):
            risky, ckey = np.concatenate([np.asarray(risky, dtype=np.int64), tele]), ("tele",) + ckey
        haz = s._hazard_edges(graph, hazards) if hazards else ()
        if len(haz):      # (each hazard set its own field: mines are few and short-lived)
            risky, ckey = np.concatenate([np.asarray(risky, dtype=np.int64), haz]), ("haz", hazards) + ckey
        if limit is not None:
            limit = 10.0 * math.ceil(limit / 10.0)          # (shared out to the next 10 u)
            ckey = ("limit", limit) + ckey
        f = cached_field(cache, ckey)
        if f is not None:
            return f
        from scipy.sparse.csgraph import dijkstra
        start = (key[0] * s.nh + key[1]) * s.nw + key[2]
        old = graph.data[risky].copy() if len(risky) else None
        if old is not None:
            graph.data[risky] = s.BARRIER_COST
        try:
            cost = dijkstra(graph, directed=True, indices=start,
                            limit=np.inf if limit is None else limit).reshape(s.L, s.nh, s.nw)
        finally:
            if old is not None:
                graph.data[risky] = old
        return keep_field(cache, ckey, cost)

    VEH_ROOM, VEH_RISE, VEH_DROP = 3, 0.12, 1.5   # cells clear of walls around; climb and drop a cell, world units
    # the player's driving keeps off walls (tools/vehicle_clearance.py, recorded Forge World drives: 10th percentile
    # 1.4-2.2 u from the nearest wall cell, quarter 2.3-3.8 u, median 4.0-5.3 u for the Ghost, Mongoose, Gauss hog,
    # Revenant, Scorpion; the shortest line hugged every corner at VEH_ROOM): an edge into a cell nearer a wall than
    # VEH_PREF u costs up to VEH_WALL_COST times more, falling off to none at VEH_PREF
    VEH_PREF, VEH_WALL_COST = 3.0, 2.0

    def _build_vnav(s):
        """The ground a vehicle can drive: the walking graph's edges between nodes VEH_ROOM cells clear of any wall
        (doors, corridors, stair wells are out), no step up of more than VEH_RISE a cell (a ramp, not a stair),
        drops to VEH_DROP. Built once, on the first drive."""
        from scipy import ndimage
        from scipy.sparse import csr_matrix
        k = s.VEH_ROOM
        yy, xx = np.mgrid[-k:k + 1, -k:k + 1]
        disk = xx ** 2 + yy ** 2 <= k * k
        roomy = np.stack([s.lwalk[l] & ~ndimage.binary_dilation(s.near_wall[l], structure=disk) for l in range(s.L)])
        if getattr(s, "oob", None) is not None:  # out of bounds is no ground to drive (set_oob)
            roomy &= ~s.oob
        for l, j, i in getattr(s, "drive_paint", ()):   # painted by hand (tools/paint.py): drivable whatever the walls say
            roomy[l, j, i] = s.lwalk[l, j, i]
        # where bot drivers wedged, live, the floor round it no person drove (tools/wedges.py, maps.terrain T.nodrive:
        # half-unit points, each with the cells round it): off the vehicles' ground, never the walking ground. Spire:
        # people drove up to (-143, -354) and turned west, the ground ran on south and three bots wedged there
        hand = getattr(s, "hand_drive", set())
        blocked = set()
        for x, y, z in getattr(s, "nodrive", ()):
            n = s._node_at(x, y, z, tol=1.5) if s.inside(x, y) else None
            if n is not None:
                l0, j0, i0 = n
                blocked |= {(l0, j0 + dj, i0 + di) for dj in (-1, 0, 1) for di in (-1, 0, 1)
                            if 0 <= j0 + dj < roomy.shape[1] and 0 <= i0 + di < roomy.shape[2]}
        blocked -= hand
        for n in blocked:
            roomy[n] = False
        g = s.graph.tocoo()
        z = s.lz.ravel()
        with np.errstate(invalid="ignore"):
            rise = z[g.col] - z[g.row]
            keep = roomy.ravel()[g.row] & roomy.ravel()[g.col] & (rise <= s.VEH_RISE) & (rise >= -s.VEH_DROP)
        # people's driving lines are the roads: an edge on floor a person drove costs ROAD_COST (maps.rides_layers
        # T.people_drive; Spire: the player drove round west where the bots' straight line ran into a gap)
        road = np.zeros(roomy.size, dtype=bool)
        for c in getattr(s, "people_drive", ()):
            n = s._node_at(c[0], c[1], c[2], tol=1.0) if s.inside(c[0], c[1]) else None
            if n is not None:
                road[np.ravel_multi_index(n, roomy.shape)] = True
        g.data = np.where(road[g.row] & road[g.col], g.data * ROAD_COST, g.data)
        near = np.stack([ndimage.distance_transform_edt(~s.near_wall[l]) * NAV_CELL for l in range(s.L)]).ravel()
        g.data = g.data * (1.0 + s.VEH_WALL_COST * np.clip(1.0 - near[g.col] / s.VEH_PREF, 0.0, 1.0))
        del near
        s.graph_v = csr_matrix((g.data[keep], (g.row[keep], g.col[keep])), shape=g.shape)
        s.graph_v_to = s.graph_v.T.tocsr()
        # the vehicle's ground is its big pieces it can drive both ways round (strongly connected): a pocket between a
        # base's pillars is roomy too, and a base's roof only drops off (Blood Gulch: goals snapped there gave fields
        # of a few hundred nodes, and the driver fell back to the walking route over the teleporter)
        from scipy.sparse.csgraph import connected_components
        _, lab = connected_components(s.graph_v, directed=True, connection="strong")
        sizes = np.bincount(lab[roomy.ravel()], minlength=lab.max() + 1)
        s.vroomy = roomy & (sizes[lab] >= max(200, 0.02 * roomy.sum())).reshape(roomy.shape)
        for l, j, i in getattr(s, "drive_paint", ()):   # painted by hand: kept however small its piece
            s.vroomy[l, j, i] = s.lwalk[l, j, i]
        for n in blocked:                        # (the rides' cells, a bot's wedged ones too, are in drive_paint)
            s.vroomy[n] = False
        on = s.vroomy.ravel()                    # and only its edges (a step into a small piece was a dead end)
        keep = keep & on[g.row] & on[g.col]
        s.graph_v = csr_matrix((g.data[keep], (g.row[keep], g.col[keep])), shape=g.shape)
        s.graph_v_to = s.graph_v.T.tocsr()
        s._vfields = {}
        for k in ("_vground", "_vpiece", "_vnear"):
            s.__dict__.pop(k, None)

    def vnav(s, a, b, lookahead=24):
        """Next point to drive toward from a to b (vehicles: _build_vnav's ground, a longer look ahead than on foot),
        or the walking answer where the vehicle's ground does not reach (off it; no way on it: see below)."""
        if getattr(s, "graph_v", None) is None:
            s._build_vnav()
        if math.hypot(b.x - a.x, b.y - a.y) < 4.0:
            return b
        l, j, i = s._node(a)
        lb, jb, ib = s._node(b)

        def nearest_ground(l0, j0_, i0_, z):
            """The nearest node of the vehicle's ground (xy, then height; anywhere: Sidewinder's flags sit 10+ u inside
            their bases), or None when the map has none."""
            if not hasattr(s, "_vground"):
                s._vground = np.nonzero(s.vroomy)
            ls, js, is_ = s._vground
            if not len(ls):
                return None
            d = (js - j0_) ** 2 + (is_ - i0_) ** 2 + (np.nan_to_num(s.lz[ls, js, is_]) - z) ** 2 * 16
            m = int(np.argmin(d))
            return int(ls[m]), int(js[m]), int(is_[m])
        if not s.vroomy[l, j, i]:                            # off it (a garage, a corner): onto it first
            n = nearest_ground(l, j, i, a.z)
            return s._center(*n) if n else s.nav(a, b)
        if not s.vroomy[lb, jb, ib]:                         # a goal off the vehicle's ground (a flag in its base):
            n = nearest_ground(lb, jb, ib, b.z)              # drive to the nearest of it
            if n is None:
                return s.nav(a, b)
            lb, jb, ib = n
        key = (lb, jb - jb % 4, ib - ib % 4)                 # one field per unit square of goals
        if not s.vroomy[key]:
            key = (lb, jb, ib)

        def field(key):
            cost = cached_field(s._vfields, key)
            if cost is None:
                from scipy.sparse.csgraph import dijkstra
                start = (key[0] * s.nh + key[1]) * s.nw + key[2]
                cost = keep_field(s._vfields, key,
                                  dijkstra(s.graph_v_to, directed=True, indices=start).reshape(s.L, s.nh, s.nw))
            return cost
        cost = field(key)
        if not np.isfinite(cost[l, j, i]):
            # no way from here on the vehicle's ground: the walking answer where it lands on ground with one, else as
            # near the goal as this piece of the ground goes, and out there (Breakpoint, Street Sweeper: the walking
            # answer was off the ground and the next answer back onto it, every tick, never an end)
            w = s.nav(a, b)
            n = s._node(w)
            if s.vroomy[n] and np.isfinite(cost[n]):
                return w
            if "_vpiece" not in s.__dict__:
                from scipy.sparse.csgraph import connected_components
                s._vpiece = connected_components(s.graph_v, directed=True, connection="strong")[1].reshape(s.vroomy.shape)
            near, pk = s.__dict__.setdefault("_vnear", {}), (int(s._vpiece[l, j, i]), key)
            if pk not in near:
                ls, js, is_ = np.nonzero(s.vroomy & (s._vpiece == pk[0]))
                m = int(np.argmin((js - jb) ** 2 + (is_ - ib) ** 2 + (np.nan_to_num(s.lz[ls, js, is_]) - b.z) ** 2 * 16))
                near[pk] = int(ls[m]), int(js[m]), int(is_[m])
            cost = field(near[pk])
            if not np.isfinite(cost[l, j, i]):
                return s.nav(a, b)
        N, flat = s.nh * s.nw, cost.ravel()
        far = None
        for k in range(lookahead):
            n = l * N + j * s.nw + i
            nb = s.graph_v.indices[s.graph_v.indptr[n]:s.graph_v.indptr[n + 1]]
            w = s.graph_v.data[s.graph_v.indptr[n]:s.graph_v.indptr[n + 1]]
            if not len(nb):
                break
            m = int(np.argmin(flat[nb] + w))
            if not flat[nb[m]] < cost[l, j, i]:
                break
            nl, rem = divmod(int(nb[m]), N)
            l, (j, i) = nl, divmod(rem, s.nw)
            if k == 0 or s._straight_ok(a, l, j, i, s.vroomy):
                far = (l, j, i)
            else:
                break
        if far is None and cost[s._node(a)] < 1e-6 or (far is not None and cost[far] < 1e-6
                                                         and (s._center(*far) - a).flat() < 0.3):
            return a                    # at (or a cell off) the end of the vehicle's ground nearest the goal: here it
        return s._center(*far) if far else b   # stops (drive's ground_end: a = its answer, never a short step)

    def nav(s, a, b, lookahead=6, jump=False, human=False, safe=False, no_tele=False, hazards=()):
        """Next point to walk toward on the way from a to b. jump=True (skilled players) also
        uses jump links, human=True (any bot that jumps) people's jumps: then the answer may be a
        JumpVec (run to its start, jump). safe=True (a carrier): round people's big drops while any other way
        exists (_field). no_tele=True: round the teleporters while any other way exists (ponytail: the straight-line
        lookahead below can still cut a pad's edge; check _straight_ok against s.links if bots clip pads). hazards:
        ((x, y, z, r), ...) to go round while any other way exists (an enemy trip mine: Kit.hazards, GitHub #9)."""
        if not all(math.isfinite(c) for c in (a.x, a.y, a.z, b.x, b.y)):
            return None              # (a NaN place crashed a tick here, Forge: no way from or to nowhere)
        if not math.isfinite(b.z):   # a goal with no floor under it (a point off Halo 2 Ascension's edge, sim:
            b = Vec(b.x, b.y, a.z)   # the next step came back None and look_point crashed): taken on our own level
        if math.hypot(b.x - a.x, b.y - a.y) < 2.5 and s._straight_ok(a, *s._node(b)):
            return b
        human = (human or jump) and bool(getattr(s, "hjumps", None) or getattr(s, "hroutes", None))
        cost = s._field(b, jump, human, safe, no_tele=no_tele, hazards=hazards)
        l, j, i = s._node(a)
        if human and np.isfinite(cost[l, j, i]):
            # a person's jump (or crouch path) is a way in, never a detour: take it only when it is
            # the cheapest way from its start to where THIS bot is going (the path search's best
            # route uses exactly that edge) and its start is not behind us. Before this a bot that
            # passed near a jump took it whenever the landing was a little closer to its goal
            # ("possessed by a jump it never meant to do").
            def way_in(src, dst, w):
                return (np.isfinite(cost[dst]) and cost[dst] + w <= cost[src] + 1e-3
                        and cost[src] <= cost[l, j, i] + 0.5)
            for src, dst, k, w in s.hjumps:
                to = k["take_off"]
                if safe and to[2] - k["land"][2] > s.RISKY_DROP and cost[l, j, i] < s.BARRIER_COST:
                    continue      # a carrier with another way down does not take a big drop
                if math.hypot(to[0] - a.x, to[1] - a.y) < 1.6 and abs(to[2] - a.z) < 0.8 and way_in(src, dst, w):
                    run = k.get("runup") or (math.nan, math.nan)
                    return JumpVec(k["land"][0], k["land"][1], k["land"][2], to[0], to[1], run[0], run[1])
            for src, dst, path, w in getattr(s, "hroutes", ()):
                p0 = path[0]
                # (within 1.6 u, as a jump's take-off: at 1.0 a bot at a climb's foot stood 1.03-1.1 u off its run-up
                # and pushed at the lip beside it 10-12 s a time, live Boneyard 20261105-041255)
                if math.hypot(p0[0] - a.x, p0[1] - a.y) < 1.6 and abs(p0[2] - a.z) < 0.8 and way_in(src, dst, w):
                    end = path[-1]
                    return RouteVec(end[0], end[1], end[2], path)
        if jump and hasattr(s, "jumps") and np.isfinite(cost[l, j, i]):
            # a jump from here or one cell away that saves real distance: take it
            near = [(l, j, i)] + [(ll, j + dj, i + di) for dj in (-1, 0, 1) for di in (-1, 0, 1)
                                  for ll in range(s.L) if (dj or di) and 0 <= j + dj < s.nh and 0 <= i + di < s.nw]
            for src in near:
                for t in s.jumps.get(src, ()):
                    if cost[t] + 1.5 < cost[l, j, i]:
                        c, sc = s._center(*t), s._center(*src)
                        return JumpVec(c.x, c.y, c.z, sc.x, sc.y)
        if not np.isfinite(cost[l, j, i]):
            # no path from here (a pocket the grid cannot climb out of, a rock): walk to the
            # nearest spot that has one; the real game is often more forgiving than the grid
            for dz in (0.6, 1e9):            # first on about our own level, else any
                for r in range(2, 41, 2):
                    j0, j1, i0, i1 = max(0, j - r), min(s.nh, j + r + 1), max(0, i - r), min(s.nw, i + r + 1)
                    with np.errstate(invalid="ignore"):
                        sub = np.isfinite(cost[:, j0:j1, i0:i1]) & (np.abs(s.lz[:, j0:j1, i0:i1] - a.z) < dz)
                    sub[:, j - j0, i - i0] = False   # not another storey of our own cell
                    if sub.any():
                        ls, js, is_ = np.nonzero(sub)
                        d = (js + j0 - j) ** 2 + (is_ + i0 - i) ** 2
                        order = np.argsort(d)
                        # the nearest spot that has a way is often across the rock we are stuck against: the nearest few
                        # that a straight walk reaches first, else the nearest
                        for k in order[:8]:
                            if s._straight_ok(a, int(ls[k]), int(js[k] + j0), int(is_[k] + i0)):
                                return s._center(int(ls[k]), int(js[k] + j0), int(is_[k] + i0))
                        k = int(order[0])
                        return s._center(int(ls[k]), int(js[k] + j0), int(is_[k] + i0))
            return b      # nothing near (off the map): head straight
        if cost[l, j, i] < 1e-6:
            return a      # at the goal's node (an unreachable goal snaps here): stay
        link = getattr(s, "links", {}).get((l, j, i))
        if link and not no_tele and cost[link[0]] < cost[l, j, i]:
            return link[1]    # on a teleporter that is the way: step onto its centre
        far = None
        for k, (l, j, i) in enumerate(s._down(l, j, i, cost, lookahead)):
            if k == 0 or s._straight_ok(a, l, j, i):
                far = (l, j, i)       # farthest node still reachable in a straight line
            else:
                break
        return s._center(*far) if far else b

    def _hazard_edges(s, graph, hazards):
        """The graph's edges into and out of the floor within a hazard's reach ((x, y, z, r): an enemy trip mine, Kit.avoid),
        priced as a wall for one search (_field), so a way round wins whenever there is one; through it when there is not
        (Kit.steer_clear the last resort). Live Rat's Nest s178: a bot's way to the hill ran past a mine and set it off."""
        nodes = []
        for x, y, z, r in hazards:
            _, j0, i0 = s._node(Vec(x, y, z))
            n = int(math.ceil(r / NAV_CELL)) + 1
            for j in range(max(0, j0 - n), min(s.nh, j0 + n + 1)):
                for i in range(max(0, i0 - n), min(s.nw, i0 + n + 1)):
                    cx, cy = s.x0 + (i + 0.5) * NAV_CELL, s.y0 + (j + 0.5) * NAV_CELL
                    if math.hypot(cx - x, cy - y) > r:
                        continue
                    for l in range(s.L):
                        if s.lwalk[l, j, i] and abs(s.lz[l, j, i] - z) < 1.5:
                            nodes.append((l * s.nh + j) * s.nw + i)
        if not nodes:
            return np.zeros(0, dtype=np.int64)
        nodes = np.unique(np.asarray(nodes, dtype=np.int64))
        out = np.concatenate([np.arange(graph.indptr[u], graph.indptr[u + 1]) for u in nodes])
        inn = np.nonzero(np.isin(graph.indices, nodes))[0]
        return np.unique(np.concatenate([out, inn]).astype(np.int64))

    def _down(s, l, j, i, cost, n):
        """Up to n nodes on from (l, j, i), each the next step down the cost field along the graph's own edges (a drop the
        graph prices or forbids, a rail, a slit, is priced or forbidden here too; before, any neighbour a step or a drop
        away: Countdown, the waypoint dropped through a slit the graph had no edge into). Never through our own cell's
        floor: no such edge."""
        N, flat_cost = s.nh * s.nw, cost.ravel()
        oob = s.oob.ravel() if getattr(s, "oob", None) is not None else None
        for _ in range(n):
            k = l * N + j * s.nw + i
            nb = s.graph.indices[s.graph.indptr[k]:s.graph.indptr[k + 1]]
            w = s.graph.data[s.graph.indptr[k]:s.graph.indptr[k + 1]]
            if oob is not None and not oob[k]:       # (set_oob: a step in costs what the field's edges in cost; the
                w = w + np.where(oob[nb], s.BARRIER_COST, 0.0)   # field prices leaving cheap, so a cell in reads near)
            nl, rem = np.divmod(nb, N)
            nj, ni = np.divmod(rem, s.nw)
            near = (np.abs(nj - j) <= 1) & (np.abs(ni - i) <= 1)       # walking steps (not teleporters or jumps)
            if not near.any():
                return
            tot = np.where(near, flat_cost[nb] + w, np.inf)
            m = int(np.argmin(tot))
            if not flat_cost[nb[m]] < cost[l, j, i]:
                return
            l, j, i = int(nl[m]), int(nj[m]), int(ni[m])
            yield l, j, i

    def path_ahead(s, a, b, n=80, jump=False, human=False, safe=False, limit=None, no_tele=False):
        """The way from a toward b as node centres, up to n steps on (0.25 u each), down the same cost field nav walks
        (with the same jump / human / safe / no_tele it is the field nav already has; limit: a search round b only):
        the route a walker sees ahead of him (brain/bot.py route_view)."""
        if not all(math.isfinite(c) for c in (a.x, a.y, a.z, b.x, b.y, b.z)):
            return []
        cost = s._field(b, jump, human, safe, limit, no_tele)
        l, j, i = s._node(a)
        if not np.isfinite(cost[l, j, i]):
            return []
        return [s._center(*q) for q in s._down(l, j, i, cost, n)]

    def walkable(s, p):
        j, i = s._cell(p)
        return bool(s.lwalk[:, j, i].any())

    def can_step(s, a, b):
        """Movement uses exactly the path search's edge rule, so anything a path can do,
        movement can do, and movement never reaches a place paths cannot leave."""
        la, ja, ia = s._node(a)
        jb, ib = s._cell(b)
        if (ja, ia) == (jb, ib) or s.degree[la, ja, ia] == 0:
            return True       # same cell, or stuck on a sliver with no legal exit: step off it
        if abs(ja - jb) > 1 or abs(ia - ib) > 1:
            return False
        trap = getattr(s, "trap", None)
        if trap is not None and trap[la, ja, ia]:
            trap = None                       # already in a trap (a spawn or teleport in): any way about it
        za = s.lz[la, ja, ia]
        lb = s._layer(jb, ib, za, reach_from=za, tan=float(s.tz[la, ja, ia]) if s.tz is not None else 0.0)
        if lb is not None:
            if s.CORNER_RULE and ja != jb and ia != ib and not s._corner_open(ja, ia, jb, ib, za, float(s.lz[lb, jb, ib])):
                return False                               # (a diagonal past a blocked corner: _build_nav drops those edges)
            return trap is None or not trap[lb, jb, ib]    # never into a place no way leads out of (set_spawns)
        # an edge the path graph has into that cell (a stair's riser, STAIR_RISE: _layer climbs MAX_RISE only, so the
        # sim's walkers stood at every stair the nav sent them up; h4_ca_warhouse CTF: 20% of bot time in the unstuck
        # wiggle, the worst spot (8.7, -8.5) with its next point 0.4 u up a step 0.2 u on)
        N = s.nh * s.nw
        na = (la * s.nh + ja) * s.nw + ia
        row = s.graph.indices[s.graph.indptr[na]:s.graph.indptr[na + 1]]
        to = row[row % N == jb * s.nw + ib]
        return bool(len(to)) and (trap is None or not trap.ravel()[to].all())

    def _corner_open(s, ja, ia, jb, ib, za, zb, tol=0.46):
        """Both cells beside a diagonal step (ja, ia) -> (jb, ib) hold walkable floor near the step's start or end height
        (the movement side of _corners_clear; looser, so movement allows whatever a path does)."""
        for mj, mi in ((ja, ib), (jb, ia)):
            if not any(s.lwalk[l, mj, mi] and (abs(float(s.lz[l, mj, mi]) - za) <= tol or abs(float(s.lz[l, mj, mi]) - zb) <= tol)
                       for l in range(s.L)):
                return False
        return True

    def open_step(s, a, goal=None, r=3.0):
        """A spot to back off to for a bot caught on a ledge, rock or corner (call it when a bot has pushed at the same
        point without moving: brain/bot.py's unstick): the nearest cell on about its own level with every neighbour
        walkable, off the wall rings and ledge lips, preferring one the path to `goal` goes on from (finite cost), and
        straight-line walkable from where it stands. None when there is nothing within r."""
        l0, j0, i0 = s._node(a)
        z0 = float(s.lz[l0, j0, i0]) if np.isfinite(s.lz[l0, j0, i0]) else a.z
        n = int(r / NAV_CELL)
        cost = None
        if goal is not None and all(math.isfinite(c) for c in (goal.x, goal.y)):
            try:
                cost = s._field(goal if math.isfinite(goal.z) else Vec(goal.x, goal.y, a.z))
            except Exception:
                cost = None
        best = []
        for j in range(max(1, j0 - n), min(s.nh - 1, j0 + n + 1)):
            for i in range(max(1, i0 - n), min(s.nw - 1, i0 + n + 1)):
                d2 = (j - j0) ** 2 + (i - i0) ** 2
                if d2 > n * n or d2 < 2:
                    continue
                for l in range(s.L):
                    if not s.lwalk[l, j, i] or abs(float(s.lz[l, j, i]) - z0) > 0.4:
                        continue
                    if s.near_wall[l, j, i] or s.near_wall2[l, j, i] or s.near_ledge[l, j, i]:
                        continue
                    if cost is not None and not np.isfinite(cost[l, j, i]):
                        continue
                    if not s.lwalk[l, j - 1:j + 2, i - 1:i + 2].all():
                        continue
                    best.append((d2 + (0.0 if cost is None else float(cost[l, j, i]) * 0.02), l, j, i))
        for _, l, j, i in sorted(best)[:12]:            # the nearest few: first one a straight walk gets to
            if s._straight_ok(a, l, j, i):
                return s._center(l, j, i)
        return s._center(*min(best)[1:]) if best else None

    def floor_in_band(s, x, y, zmin=-1e9, zmax=1e9):
        """A floor height near (x, y) (within 3 units) inside [zmin, zmax], preferring floors players
        can reach: the lowest one (ground), for a zone with a lower bound its typical floor (median),
        with only an upper bound the highest under it. Falls back to the cell's lowest floor, else 0."""
        j0, i0 = s._cell(Vec(x, y))
        r = 12
        js, is_ = slice(max(0, j0 - r), j0 + r + 1), slice(max(0, i0 - r), i0 + r + 1)
        z = s.lz[:, js, is_]
        ok = s.lwalk[:, js, is_] & (z >= zmin) & (z <= zmax)
        reach = getattr(s, "reach", None)
        if reach is not None and (ok & reach[:, js, is_]).any():
            ok &= reach[:, js, is_]
        if ok.any():
            if zmin > -1e8:
                return float(np.median(z[ok]))          # an upper zone: its typical floor (roof, not ramp)
            return float(z[ok].max() if zmax < 1e8 else z[ok].min())
        zs = [float(s.lz[l, j0, i0]) for l in range(s.L) if s.lwalk[l, j0, i0]]
        return min(zs) if zs else 0.0

    def nav_z(s, x, y, from_z=None):
        """Walkable height at (x, y) on the path grid (what paths and movement agree on): the layer a mover at height
        from_z can step to, else the walkable one nearest from_z, else the lowest. (Not the lowest whatever from_z: a
        stair riser or a placed ramp the climb rule does not cover dropped the sim's walkers through a Forge canvas onto
        the level's floor 17 u under it, h4_ca_warhouse CTF: 16 falls in 120 s, the bots then stood at people's links up
        16-27 u, the mode matrix's 30% "stuck")"""
        j, i = s._cell(Vec(x, y))
        l = s._layer(j, i, from_z, reach_from=from_z) if from_z is not None else None
        if l is None and from_z is not None:
            l = s._layer(j, i, from_z)
        if l is None:
            l = next((k for k in range(s.L) if s.lwalk[k, j, i]), None)
        if from_z is not None:
            # a floor right under the mover that the nav closed (a low face or a ceiling over it, _low_ceiling: closed to
            # walk through, not gone): its own height, not a walkable layer far off (h4_ca_warhouse: a Forge piece's
            # edge closed the canvas cell a bot stood on, and its height snapped 3.7 u up to the piece's top)
            col = [(abs(float(s.lz[k, j, i]) - from_z), k) for k in range(s.L) if np.isfinite(s.lz[k, j, i])]
            d, k = min(col, default=(9e9, None))
            if k is not None and d < 0.5 and (l is None or abs(float(s.lz[l, j, i]) - from_z) > 1.0):
                l = k
        return float(s.lz[l, j, i]) if l is not None else 0.0


def review_traps(path, show=True):
    """List the trapped walls saved in path (Terrain.save_traps), the suspect ones marked: reported once and never
    confirmed (n 1), struck (a bot ran through it), old and untouched, or one of a cluster (4+ within 2 u: a cascade).
    Returns {(x, y, z): [reasons]} for the flagged. Delete a row from the file, or prune_traps(path, keys)."""
    import time
    rows, now = Terrain._read_traps(path), time.time()
    flagged = {}
    for k, m in sorted(rows.items()):
        why = []
        if m["n"] < 2:
            why.append("single report")
        if m["strikes"]:
            why.append(f"{m['strikes']} bot(s) ran through it")
        if now - m["last"] > 7 * 86400:
            why.append(f"untouched {int((now - m['last']) / 86400)} days")
        if sum(1 for q in rows if (q[0] - k[0]) ** 2 + (q[1] - k[1]) ** 2 <= 4.0) >= 4:
            why.append("in a cluster")
        if why:
            flagged[k] = why
        if show:
            print(f"{k[0]:9.1f} {k[1]:9.1f} {k[2]:7.1f}  n={m['n']:<3} strikes={m['strikes']}  " + ("; ".join(why) or "ok"))
    if show:
        print(f"{len(rows)} marks, {len(flagged)} suspect")
    return flagged


def prune_traps(path, keys):
    """Remove these marks ((x, y, z) as review_traps gives them) from the saved file. Returns how many went."""
    import json
    import os
    rows = Terrain._read_traps(path)
    gone = [k for k in keys if rows.pop(tuple(k), None) is not None]
    out = [dict(x=k[0], y=k[1], z=k[2], **m) for k, m in sorted(rows.items())]
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump({"version": Terrain.TRAP_VERSION, "spots": out}, f)
    os.replace(path + ".tmp", path)
    return len(gone)
