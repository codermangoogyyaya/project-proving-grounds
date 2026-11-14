"""Forge pieces and other placed objects read from the running Halo: Reach game. A map variant's pieces (Forge World's
Hemorrhage, Paradiso ...) are in its .mvar file, whose format is not documented; but in a match they are ordinary
objects. Each live scenery / crate object gives its tag id, position and axes (+0x44 position, +0x50 forward, +0x5C up).
The game keeps no tag names in memory, but the map's .map file has them in tag-index order (tools/reach_tag_names.py),
so the tag id names the piece exactly (live, Paradiso: 0x20c2 = ff_column, 0x20ee = ff_railing_2). Its shape comes from
the editing kit (tools/object_shapes.py: maps/_shapes/reach.json boxes, maps/_shapes/reach_faces.json every collision
surface), for a match's bots and for LiDAR (brain/maps.py terrain(live_blocks=...)). Since a piece goes in face by face,
each face by its angle: a block's top is floor to stand on, its sides walls, a ramp a ramp. Before, every piece was one
solid box (nothing on it walkable). A piece with no collision model (physics boxes only) is still its solid box.

(Until pieces were guessed from their bounding radius: ambiguous, Hang 'Em High doors 'found' on Forge World. Exact
names replaced it.)

Shields (energy pieces, not solid boxes) by the player's three groups (maps/_shapes/reach_shields.json, sorted live
on Forge World): every one stops shots (a sight line through it is blocked: Terrain.set_glass); a two-way shield lets
bodies through both ways, a one-way shield one way (Terrain.set_barrier with its through direction), a no-pass shield
none (a barrier both ways, or a solid box when it is not thin).
"""
import json
import math
import os
import struct
import sys

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "maps")
# per game (the live reader's TAG_GAME): the object types read (scenery, crate; machines: doors and lifts move, left
# out) and the object's position, forward and up (Halo 4 / H2A: tools/forge_dump.py on the player's ALL ITEMS maps;
# their crates hold the Forge pieces and a map's own crates, Adrift's included)
TYPES = {"reach": (6, 10), "h4": (6, 11), "h2a": (6, 11)}
AXES_AT = {"reach": (0x44, 0x50, 0x5C), "h4": (0x64, 0x70, 0x7C), "h2a": (0x64, 0x70, 0x7C)}
# never solid, by name (brain/placed.py's list: markers, beacons, spawns ...), for the games whose scenery is read whole
SKIP = ("door", "lift", "light", "camera", "cinematic", "skybox", "fx_", "sound", "hologram", "decal", "kill_volume",
        "danger_zone", "spawn", "marker", "beacon", "mp_circle", "flag_base", "flag_stand", "drop_point", "respawn",
        "health")

# A piece smaller than this is left out of the nav (block). It was 0.35 high / 0.04 square: but a bot's step is
# brain/terrain.py MAX_RISE (0.18), so a 0.2-0.35 u kerb, low wall or rock-like prop is a wall to a walking body, and a
# piece dropped from the nav is one the bots walk straight into and stand pushing at. Anything a body cannot step onto
# stays in; only what it walks over (under a step) and slivers thinner than the body are dropped.
MIN_BLOCK_HEIGHT = 0.2
MIN_BLOCK_AREA = 0.015

_SHAPES = {}


def _shapes(game="reach"):
    if game not in _SHAPES:
        p = os.path.join(ROOT, "_shapes", game + ".json")
        _SHAPES[game] = json.load(open(p, encoding="utf-8"))["shapes"] if os.path.exists(p) else {}
    return _SHAPES[game]


def shield_groups():
    """{piece name: (group, through)}: group two_way / one_way / no_pass; through (one-way pieces) "back" = a body
    passes from the piece's front (facing) side to its back, "forward" = along its facing."""
    p = os.path.join(ROOT, "_shapes", "reach_shields.json")
    if not os.path.exists(p):
        return {}
    out = {}
    for group, g in json.load(open(p, encoding="utf-8"))["groups"].items():
        th = g.get("through") if isinstance(g.get("through"), dict) else {}
        for name in g["pieces"]:
            out[name] = (group, next((v for k, v in th.items() if name.startswith(k)), None))
    return out


def _thin(lo, hi):
    return min(hi[0] - lo[0], hi[1] - lo[1]) < 0.5


def shields(pieces, groups=None, shapes=None, game="reach"):
    """[(key, a, b, z0, z1, group, through, box)] for the shields among pieces [(name, pos, (forward, left, up))]: a, b
    their line seen from above (x, y), z0..z1 their height, through the 2D direction a body may cross a one-way one,
    box (pos, axes, min, max) for sight lines. (Reach's groups only: the player sorted Forge World's shields.)"""
    groups = groups if groups is not None else shield_groups() if game == "reach" else {}
    shapes = shapes if shapes is not None else _shapes(game)
    out = []
    for k, (name, pos, (f, l, u)) in enumerate(pieces):
        g, s = groups.get(name), shapes.get(name)
        if not g or not s:
            continue
        (x0, y0, z0), (x1, y1, z1) = s["min"], s["max"]
        if y1 - y0 >= x1 - x0:                    # a wall across its facing: its long side at the middle of its depth
            c = (x0 + x1) / 2
            a = (pos[0] + f[0] * c + l[0] * y0, pos[1] + f[1] * c + l[1] * y0)
            b = (pos[0] + f[0] * c + l[0] * y1, pos[1] + f[1] * c + l[1] * y1)
        else:
            c = (y0 + y1) / 2
            a = (pos[0] + f[0] * x0 + l[0] * c, pos[1] + f[1] * x0 + l[1] * c)
            b = (pos[0] + f[0] * x1 + l[0] * c, pos[1] + f[1] * x1 + l[1] * c)
        n = math.hypot(f[0], f[1]) or 1.0
        through = {"back": (-f[0] / n, -f[1] / n), "forward": (f[0] / n, f[1] / n)}.get(g[1])
        out.append((f"shield{k}:{name}", a, b, pos[2] + z0, pos[2] + z1, g[0], through, (pos, (f, l, u), s["min"], s["max"])))
    return out


def shield_blocks(found):
    """The thick no-pass shields as solid boxes, for the terrain's build (brain/maps.py terrain(live_blocks=...): the
    nav is made from the blocks it is built with)."""
    return [box + (key,) for key, _, _, _, _, group, _, box in found if group == "no_pass" and not _thin(box[2], box[3])]


def apply_shields(T, found):
    """Shields onto terrain T: each blocks sight lines; one-way ones are barriers with their through direction, thin
    no-pass ones barriers both ways (thick ones: shield_blocks, built in). Returns how many."""
    from .core import Vec
    for key, a, b, z0, z1, group, through, box in found:
        T.set_glass(key, *box)
        if group == "one_way" or (group == "no_pass" and _thin(box[2], box[3])):
            T.set_barrier(key, Vec(a[0], a[1], 0.0), Vec(b[0], b[1], 0.0), z0, z1,
                          through=Vec(through[0], through[1], 0.0) if through else None)
    return len(found)


def _pieces(live):
    """(name, pos, (forward, left, up)) of the live scenery and crates in the running match, named by tag id."""
    names = _tag_names(live)
    game = getattr(live, "TAG_GAME", "reach")
    types, (at_p, at_f, at_u) = TYPES.get(game, TYPES["reach"]), AXES_AT.get(game, AXES_AT["reach"])
    tag_at = getattr(live, "TAG_AT", 0)
    if live._blk is None:
        live.players()
    ents, mx, _ = live._array(live.OBJECTS_AT)
    b = live.g.read(ents, mx * 0x18)
    for i in range(mx):
        if not struct.unpack_from("<H", b, i * 0x18)[0] or b[i * 0x18 + live.TYPE_AT] not in types:
            continue
        o = struct.unpack_from("<Q", b, i * 0x18 + 0x10)[0]
        try:
            head = live.g.read(o, max(at_u, tag_at) + 12)
        except OSError:
            continue
        if not head:
            continue
        tag = struct.unpack_from("<I", head, tag_at)[0] & 0xFFFF
        f, u = struct.unpack_from("<3f", head, at_f), struct.unpack_from("<3f", head, at_u)
        nf, nu = math.sqrt(sum(v * v for v in f)) or 1.0, math.sqrt(sum(v * v for v in u)) or 1.0
        f, u = tuple(v / nf for v in f), tuple(v / nu for v in u)
        name = names[tag].rsplit("\\", 1)[-1] if tag < len(names) else None
        if name and game != "reach" and any(s in name for s in SKIP):
            continue                               # (markers, beacons, spawn points: never solid)
        yield name, struct.unpack_from("<3f", head, at_p), (f, _cross(u, f), u)


def live_pieces(live):
    """[(name, pos, (forward, left, up))] of the running match's named scenery and crates: a map variant's layout
    (brain/variants.py files it, with these, under its base map); a Halo 4 / H2A map's own crates and scenery (its export
    lists none: tools/live_bots.py puts them in the nav)."""
    return [p for p in _pieces(live) if p[0]] if _tag_names(live) else []


def live_items(live):
    """A Forge creation's power items as they lie in the match (GitHub #11): loose weapons, equipment and powerups
    (parent NONE) named by the map's own tag names, kept when brain/maps.py POWER knows them, as m["items"] rows. A
    creation filed without its .mvar file kept Forge World's own placements as its items (variant.json holds only
    scenery and crates): bots walked to guns the creation does not have and never to the ones it has."""
    from .core import Vec
    from .maps import POWER
    names = _tag_names(live)
    if not names or not hasattr(live, "_array"):
        return []
    ents, mx, _ = live._array(0x10)                      # (LiveReach.objects' layout: +4 type, +0x14 parent, +0x44 pos)
    b = live.g.read(ents, mx * 0x18)
    out = []
    for i in range(mx):
        salt, typ = struct.unpack_from("<H", b, i * 0x18)[0], b[i * 0x18 + 4]
        if not salt or typ not in (2, 3):
            continue
        o = struct.unpack_from("<Q", b, i * 0x18 + 0x10)[0]
        head = live.g.read(o, 0x50)
        tag, parent = struct.unpack_from("<I", head, 0)[0], struct.unpack_from("<I", head, 0x14)[0]
        if parent != 0xFFFFFFFF:
            continue                                      # in someone's hands (a starting gun)
        # (LiveReach._own_name: the name may sit up to 4 tags before the index on Forge World)
        key = (live._own_name(tag, "weapons") or live._own_name(tag, "powerups") or "") if hasattr(live, "_own_name") \
            else ""
        if key in POWER:
            name, kind, value = POWER[key]
            out.append(dict(name=name, kind=kind, value=value, pos=Vec(*struct.unpack_from("<3f", head, 0x44)),
                            respawn=60 if kind == "powerup" else 45))
            # (and the live reader names that tag so: it names loose objects by the nearest map placement, the canvas's
            # here, so tools/live_bots.py item_check would read every one of these as gone from its spot)
            live._names[tag] = name
    return out


def nav_pieces(pieces, game="reach"):
    """(blocks, shields) for the nav from pieces [(name, pos, axes)]: each piece's block() (thick no-pass shields as
    solid boxes too) and the shields (apply_shields). brain/maps.py builds a map variant's nav with them."""
    found = shields(pieces, game=game)
    return [b for b in (block(n, p, a, game=game) for n, p, a in pieces) if b] + shield_blocks(found), found


def _tag_names(live):
    """The running map's tag names by index (tools/reach_tag_names.py, cached per map)."""
    cache = getattr(live, "_tag_name_list", None)
    if cache is not None:
        return cache
    sys.path.insert(0, os.path.join(os.path.dirname(ROOT), "tools"))
    import reach_tag_names
    map_file = live.map_name().replace("/", "\\").rsplit("\\", 1)[-1]
    live._tag_name_list = reach_tag_names.cached(map_file, getattr(live, "TAG_GAME", "reach"))
    return live._tag_name_list


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


_FACES = {}


def _faces(game="reach"):
    """{piece name: [(material, [x, y, z, ...]), ...]}: every collision surface (tools/object_shapes.py faces())."""
    if game not in _FACES:
        p = os.path.join(ROOT, "_shapes", game + "_faces.json")
        _FACES[game] = json.load(open(p, encoding="utf-8"))["faces"] if os.path.exists(p) else {}
    return _FACES[game]


_NO_BLOCK_WARNED = set()    # (game, name, reason) already printed once: a piece silently dropped from the nav gets
                            # no collision at all (bots run through it), and that had no trace until now
NO_SHAPE_PLACEMENTS = []    # [(x, y, z), ...] every placement of a piece with no shape entry at all (not deduped by
                            # name: each instance's own spot matters). A stuck report near one of these is
                            # independently confirmed missing data, not just inferred from one bot's behavior
                            # (terrain.learn_stuck's multi-bot vote exists for the inferred case, not this one)


def _warn_no_block(game, name, reason, pos=None):
    key = (game, name, reason)
    if name and key not in _NO_BLOCK_WARNED:
        _NO_BLOCK_WARNED.add(key)
        print(f"forge: {name!r} ({game}) gets no nav collision: {reason}", file=sys.stderr)
    if pos is not None:
        NO_SHAPE_PLACEMENTS.append(tuple(pos))


def missing_near(pos, r=3.0):
    """Whether a piece confirmed to have no shape at all (_warn_no_block, NO_SHAPE_PLACEMENTS) sits within r of pos:
    a stuck report there is independently explained by known-bad data, not just one bot's say-so."""
    x, y = (pos.x, pos.y) if hasattr(pos, "x") else (pos[0], pos[1])
    return any((x - q[0]) ** 2 + (y - q[1]) ** 2 <= r * r for q in NO_SHAPE_PLACEMENTS)


def block(name, pos, axes, skip_materials=("energy", "hologram"), game="reach"):
    """One piece for the nav (nav_pieces): (pos, (forward, left, up), min, max, name[, faces]). A piece with its
    collision surfaces known carries them (faces) and goes into the nav face by face, a floor, ramp or wall by each
    face's angle, else as its solid box. None: unknown, energy and hologram alone (lights, power-ups; shields:
    apply_shields), or a box too low or small for a body to bump into. A building piece's own energy panes (the tall
    tower's, the window wall's) are solid with it. Each way a piece drops out prints once (_warn_no_block): before,
    a piece missing its shape (an unrecognized or uncatalogued name) vanished from the nav with no trace at all."""
    s, f = _shapes(game).get(name), _faces(game).get(name)
    if not s:
        _warn_no_block(game, name, "no shape entry for this name in _shapes/reach.json", pos=pos)
        return None
    if f and not all(any(m in mat for m in skip_materials) for mat, _ in f):
        return pos, axes, s["min"], s["max"], name, f
    if any(m in s.get("material", "") for m in skip_materials):
        _warn_no_block(game, name, f"shape's material ({s.get('material', '')!r}) is energy/hologram, skipped as non-solid")
        return None                               # (shields: apply_shields)
    lo, hi = s["min"], s["max"]
    if hi[2] - lo[2] < MIN_BLOCK_HEIGHT or (hi[0] - lo[0]) * (hi[1] - lo[1]) < MIN_BLOCK_AREA:
        _warn_no_block(game, name, f"shape box too low/small to matter (min={lo}, max={hi})")
        return None
    return pos, axes, lo, hi, name


# --- pieces that come and go during a match (a game type's walls: Invasion opens its phase gates by deleting them and
# puts them back for the next round). The nav is built once from the pieces read at the start (brain/maps.py), each
# stamped in for good, so a wall that appeared later was open floor to the bots (they pressed into it for minutes).
GONE_READS = 2         # reads in a row a piece must be missing to count as gone (one read can miss an object)


def piece_key(name, pos):
    """A piece's name and place to the half unit, as variant.json "dynamic" files it."""
    return f"{name}@{round(pos[0] * 2) / 2:g},{round(pos[1] * 2) / 2:g},{round(pos[2] * 2) / 2:g}"


def outline(pos, axes, lo, hi):
    """A piece's box seen from above: its four sides [(a, b)] (x, y) and its world height (z0, z1). (ponytail: the box's
    forward / left plane; a piece rolled on its side is outlined a little off.)"""
    f, l, u = axes
    pts = [(pos[0] + a * f[0] + b * l[0], pos[1] + a * f[1] + b * l[1])
           for a, b in ((lo[0], lo[1]), (hi[0], lo[1]), (hi[0], hi[1]), (lo[0], hi[1]))]
    zs = [pos[2] + a * f[2] + b * l[2] + c * u[2] for a in (lo[0], hi[0]) for b in (lo[1], hi[1]) for c in (lo[2], hi[2])]
    return [(pts[k], pts[(k + 1) % 4]) for k in range(4)], (min(zs), max(zs))


class PieceWatch:
    """A map variant's pieces read again during the match (tools/live_bots.py, every few seconds): a solid piece
    standing that the nav was not built with is a wall (its box's four sides, Terrain.set_barriers) while it stands; a
    piece the nav was built with that goes missing is dynamic: update returns it for variant.json "dynamic", and the
    next match's nav is built without it (brain/maps.py), so it is a wall here only while it stands. (A piece stamped in
    at this match's start that goes stays a wall until the match ends: the nav has no way to take a stamp out.)"""

    def __init__(s, built, dynamic=(), game="reach"):
        s.dynamic = set(dynamic)
        s.built = {piece_key(n, p) for n, p, _ in built} - s.dynamic   # stamped into this match's nav
        s.game, s.missing, s.walls, s.absent = game, {}, {}, {}

    def update(s, pieces, T):
        """pieces: this read's [(name, pos, axes)]. Puts the walls on T; returns the built-in keys newly gone."""
        here = {piece_key(n, p): (n, p, a) for n, p, a in pieces}
        for k in s.built:
            s.missing[k] = 0 if k in here else s.missing.get(k, 0) + 1
        gone = {k for k in s.built if s.missing[k] == GONE_READS} - s.dynamic
        s.dynamic |= gone
        items = {}
        for k, (n, p, a) in here.items():
            s.absent[k] = 0
            if k in s.built or k in s.walls:
                continue
            b = block(n, p, a, game=s.game)
            if b is None:
                continue
            sides, (z0, z1) = outline(b[0], b[1], b[2], b[3])
            if z1 - z0 < 0.5:
                continue                              # (a floor tile: walked on, not into)
            s.walls[k] = (sides, z0, z1)
        bars = T.__dict__.get("barriers", {})
        for k, (sides, z0, z1) in s.walls.items():
            if k not in here:
                s.absent[k] = s.absent.get(k, 0) + 1
            on = s.absent.get(k, 0) < GONE_READS
            for j, (a, b) in enumerate(sides):
                key = f"fp:{k}:{j}"
                if on or key in bars:                 # (a terrain swapped in by a grown window: put back)
                    items[key] = (_vec(a), _vec(b), z0, z1, None, on)
        if items:
            T.set_barriers(items)
        return gone


def _vec(xy):
    from .core import Vec
    return Vec(xy[0], xy[1], 0.0)
