"""Read live Halo CE state from a running MCC (read-only) for the brain.

Layout (MCC 1.3528 halo1.dll, docs/CE-03-live-game-state.md):
players array header at [halo1+0x1C40480]; player +0x20 name (16 wchar), +0x4C team (0 red, 1 blue),
+0x64 unit handle.
Object headers at [halo1+0x1C42248] (12-byte entries, +8 = offset); object = [halo1+0x2D9CDF8] + 0x34 + offset.
Biped: +0x18 position, +0x30 body forward, +0x234 aiming vector (snaps to the action's facing),
+0x240 looking vector (head, turns ~320 deg/s), +0x9C health, +0xA0 shield (fractions; max 75 each),
+0x2D3 current weapon slot (int8; +0x2D5 the slot asked), +0x2D8 weapon inventory (4 handles). Any object +0 = its tag id.
Tags: [halo1+0x2D9CE10] = tag header mapped at virtual 0x50000000; +0 tag array (0x20 per tag:
+0 class fourcc, +0xC tag id, +0x10 path pointer), +0xC tag count.
"""
import math
import os
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
from memread import Game  # noqa: E402

PLAYERS_PTR, OBJECT_HEADERS_PTR, OBJECT_POOL_PTR, TAGS_PTR = 0x1C40480, 0x1C42248, 0x2D9CDF8, 0x2D9CE10
TAG_BASE = 0x50000000
NONE = 0xFFFFFFFF
# tag file name -> the brain's weapon name (others keep their file name, spaces as underscores)
WEAPON_ALIAS = {"sniper_rifle": "sniper", "rocket_launcher": "rockets", "plasma_cannon": "fuelrod",
                "mp_needler": "needler", "energy_sword": "sword",
                "energy_blade": "sword", "flak_cannon": "fuelrod"}          # Halo 2 (and Halo 3's sword)
# Reach / H2A / Halo 4 weapon tag name -> brain weapon name (brain/bot.py WEAPON_RANGE / WEAPON_RANK keys), for the map
# exports' placements (tools/reach_export.py) and the guns read live by tag name (LiveH4F.weapon_name): one table, so a
# gun in hand and the same gun on the map have one name. Names not listed pass through (dmr, magnum, shotgun ...).
TAG_ALIAS = {"sniper_rifle": "sniper", "rocket_launcher": "rockets", "energy_sword": "sword",
             "flak_cannon": "fuelrod",               # Reach's fuel rod gun tag
             "plasma_repeater": "plasma_rifle",      # ponytail: the Elite plasma rifle's Spartan cousin, same role
             "spartan_laser": "spartan_laser", "gravity_hammer": "gravity_hammer",
             # Halo 2 Anniversary (palette names without their h2a_ prefix)
             "br": "battle_rifle", "smg_suppressed": "smg", "brute_plasma": "brute_plasma_rifle",
             "fuel_rod_gun": "fuelrod", "fuel_rod_cannon": "fuelrod", "fuel_rod": "fuelrod",
             # Halo 4 (ordnance names without ord_weap_storm_ / palette names without storm_)
             "covenant_sniper": "beam_rifle", "spartan_laser_mp": "spartan_laser",
             "rail_gun": "railgun", "incineration_launcher": "incineration_cannon",   # no brain name yet:
             "sticky_grenade_launcher": "sticky_detonator", "ord_spread_gun": "scattershot",   # the game's own names
             "spread_gun": "scattershot",            # (Halo 4's live tag: storm_spread_gun, Adrift)
             "overshield": "powerup_super_shield", "activecamo": "powerup_invisibility",   # brain/maps.py POWER's H2 names
             "storm_rifle": "plasma_rifle",          # ponytail: the Elite plasma rifle's successor, same role
             "suppressor": "smg",                    # ponytail: Promethean full-auto, the SMG's role
             "new_flag": "flag", "skull": "ball", "oddball": "ball",   # the objectives in hand (Halo 4, H2A)
             # the infected's own swords (Halo 4 Flood's storm_flood_sword, H2A's h2a_energy_sword_infected): lunge
             # like the sword (their tags: autoaim range 2.25, angle 6, as the sword's); unnamed, a Flood played its
             # prongs as a gun (band 5-25 u: backed off inside 5 u, swung from across the room; owner, issue #3)
             "flood_sword": "sword", "energy_sword_infected": "sword"}
# the infected's own weapons by their tag names: seeing one in a hand says Infection / Flood whatever the variant's
# name (brain/modes.py mode_of)
ZOMBIE_TAGS = ("storm_flood_sword", "h2a_energy_sword_infected")


def brain_name(name):
    """A weapon / ordnance tag name -> the brain's weapon name (unknown ones pass through)."""
    for p in ("ord_weapon_storm_", "ord_weap_storm_", "ord_weapon_", "ord_weap_", "storm_", "h2a_"):
        if name.startswith(p):
            name = name[len(p):]
            break
    return TAG_ALIAS.get(name, name)


def flags_by_stand(weapons, held, stands, tags, teams, skip=(), third=(), loose=True, anchored=()):
    """CTF flags where the game keeps no tag names (Halo 3): weapons [(handle, tag, parent, pos)], held {weapon handle:
    carrier player}, stands {team: Vec}. A tag is a flag's once an object of it lies on a stand (within 0.6 units, 1 up
    or down); a flag object's team is the stand nearest where it was first seen. tags / teams persist between calls.
    Returns [{team, pos, carrier}]. One flag tag a match, never one a player holds right now: a gun dropped on a stand
    taught its tag on Reach (Boardwalk CTF: a bot's swap on their stand made every rifle a "flag", and a rifle in its
    hands kept it the carrier after the capture). A flag first seen carried is the carrier's enemy's (its object sits
    at 0, 0, 0)."""
    out = []
    held_tags = {tag for h, tag, parent, pos in weapons if h in held}
    for h, tag, parent, pos in weapons:
        home = min(stands, key=lambda t: (pos[0] - stands[t].x) ** 2 + (pos[1] - stands[t].y) ** 2)
        on_stand = ((pos[0] - stands[home].x) ** 2 + (pos[1] - stands[home].y) ** 2 < 0.6 ** 2
                    and abs(pos[2] - stands[home].z) < 1.0)
        if tag not in tags:     # (skip: tags of the map's own guns; a held object reads 0, 0, 0: never on a stand)
            # or an object in a third weapon slot (third: handles): carried, not a gun (a brain started mid-carry);
            # loose=False: no loose objects, only those and ones hung on a marker on a stand (anchored: the ball)
            if tags or tag in skip or not (h in third or (on_stand and (h in anchored or (loose and tag not in held_tags)))):
                continue
            tags.add(tag)
        car = held.get(h)
        if h not in teams:      # ponytail: two teams assumed for a flag first seen carried (Multi Flag, 3-4 teams: unknown)
            teams[h] = 1 - car["team"] % 2 if car else home
        # stand: where it lives (its team in multi-flag CTF; One Flag's owner can differ: LiveH3.flags), handle: this object
        out.append(dict(team=teams[h], stand=teams[h], handle=h, pos=car["pos"] if car else pos,
                        carrier=car["index"] if car else None))
    return out


def one_flag_owner(flag, players, state):
    """One Flag: the flag stays on its stand while the teams swap sides each round, so its the player is the round's
    defenders, the team that spawns beside it (Reach Boardwalk: at round 2 every blue player spawned 1-3 u from the
    flag, every red one 50 u away; the stand-team reading made blue try to steal its own flag for 4 minutes). A new
    flag object = a new round: the first team seen within 12 u of it owns it. state persists between calls."""
    if flag["handle"] != state.get("handle"):
        state.update(handle=flag["handle"], team=None)
    if state["team"] is None and flag["carrier"] is None:
        near = [p["team"] for p in players if p.get("pos") is not None
                and (p["pos"][0] - flag["pos"][0]) ** 2 + (p["pos"][1] - flag["pos"][1]) ** 2 < 12 ** 2]
        if near:
            state["team"] = max(set(near), key=near.count)
    return state["team"] if state["team"] is not None else flag["team"]


class Walked:
    """Where units have really stood on foot this match (a bot's spawn pick must be ground someone walked: the AI's first
    sight is often out of bounds, a spawn list holds spots under ramps and stairs, a zone centre can lie past a ledge:
    live Corvette and Glacier, Nov 11, an all-bot team came back outside and under the map). Cells of CELL u, each with the
    heights (0.5 u steps) seen there. A unit counts only AGE s after it was first seen, so where it spawned in does not.
    near(x, y, z): somebody walked within about a cell of it, at that height (VERT u)."""
    CELL, AGE, VERT, MIN_KNOWN = 3.0, 6.0, 1.2, 200

    def __init__(s):
        s.cells, s.born = {}, {}

    def _key(s, x, y):
        return (math.floor(x / s.CELL), math.floor(y / s.CELL))

    def feed(s, now, units):
        """units: [(id, (x, y, z))], alive and on foot."""
        for uid, pos in units:
            if now - s.born.setdefault(uid, now) >= s.AGE:
                s.cells.setdefault(s._key(pos[0], pos[1]), set()).add(round(pos[2] * 2) / 2)

    def near(s, x, y, z):
        kx, ky = s._key(x, y)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for h in s.cells.get((kx + dx, ky + dy), ()):
                    if abs(h - z) <= s.VERT:
                        return True
        return False

    def known(s):
        return len(s.cells) >= s.MIN_KNOWN


class Live:
    # the unit's desired zoom byte (0xFF none, 0 first level, 1 second), for the trace's "uz" (tools/live_bots.py
    # unit_zoom): CE +0x301 (ghidra/ce_uc.c line 65); Halo 2 +0x255 and Halo 3 +0x299 below; None: not decoded
    UNIT_ZOOM = 0x301
    SLOTS_AT = 0x2D8       # unit 4 weapon handles (balls()): Halo 2 +0x22C, Halo 3 +0x268, Reach +0x350

    def __init__(s):
        s.g = Game()
        s._tag_paths = {}

    def tag_path(s, tag_id):
        if tag_id not in s._tag_paths:
            hdr = s.g.u64(s.g.base + TAGS_PTR)
            arr = struct.unpack("<I", s.g.read(hdr, 4))[0] - TAG_BASE
            ptr = struct.unpack("<I", s.g.read(hdr + arr + (tag_id & 0xFFFF) * 0x20 + 0x10, 4))[0] - TAG_BASE
            s._tag_paths[tag_id] = s.g.read(hdr + ptr, 128).partition(bytes(1))[0].decode("ascii", "replace")
        return s._tag_paths[tag_id]

    def weapon_name(s, weapon_handle):
        o = s.object(weapon_handle)
        if not o:
            return "none"
        name = s.tag_path(struct.unpack("<I", s.g.read(o, 4))[0]).rsplit("\\", 1)[-1].replace(" ", "_")
        return WEAPON_ALIAS.get(name, name)

    BATTERY = {"plasma_pistol", "plasma_rifle"}     # no rounds: a charge (not decoded)

    def ammo(s, weapon_handle):
        """Rounds left in a CE weapon: reserve +0x286 plus magazine +0x28A (verified on the
        pistol: 48 + 12). -1 = unknown (battery weapons, no object)."""
        o = s.object(weapon_handle)
        if not o or s.weapon_name(weapon_handle) in s.BATTERY:
            return -1
        reserve, mag = struct.unpack_from("<hxxh", s.g.read(o + 0x286, 6))
        return max(0, reserve) + max(0, mag)

    def mag(s, weapon_handle):
        """Rounds in a CE weapon's magazine (+0x28A), -1 unknown."""
        o = s.object(weapon_handle)
        if not o or s.weapon_name(weapon_handle) in s.BATTERY:
            return -1
        return max(0, struct.unpack("<h", s.g.read(o + 0x28A, 2))[0])

    def ground_weapons(s):
        """[(name, pos)] of loaded weapons lying loose (no parent: not in anyone's hands)."""
        out = []
        for h, typ, path, pos in s.objects((2,)):
            o = s.object(h)
            if o and struct.unpack("<I", s.g.read(o + 0xD8, 4))[0] == NONE and s.ammo(h) != 0:
                n = path.rsplit("\\", 1)[-1].replace(" ", "_")   # empty drops are no use: skipped
                out.append((WEAPON_ALIAS.get(n, n), pos))
        return out

    def _array(s, ptr):
        hdr = s.g.u64(s.g.base + ptr)
        mx, size = struct.unpack("<hh", s.g.read(hdr + 0x20, 4))
        rel = struct.unpack("<i", s.g.read(hdr + 0x34, 4))[0]
        return hdr + rel, mx, size

    # a vehicle's body as each game keeps it on the object, the field its players' health is read from (a vehicle is a
    # unit): (offset, True when the float is damage taken, 1.0 = gone, not health left)
    VEH_VITAL = (0x9C, False)
    # the game's own "destroyed" bit: object damage flags byte at DEAD_AT, bit 2 (CE's _object_dead_bit). DERIVED from each
    # DLL's object_get_health script function (backups/mcc-2025.08.16, the Ghidra build), which answers 0 when it is set:
    # Halo 2 +0x10A, Halo 3 +0x110, ODST +0x104, Reach +0x138, Halo 4 / H2A +0x16C. None (CE): its MP vehicles cannot
    # die, the body field below decides. Not yet read live on a wreck
    DEAD_AT = None
    # where object_get_health reads health in Reach / H4 / H2A: a block placed per object, its size (int16) at
    # HEALTH_BLOCK, its offset (uint16) next, the health float +0x10 into it; None: the fixed VEH_VITAL field. (Reach's
    # +0x128 read before climbs back to full on a wreck: Forge World 20261102, a destroyed Scorpion a full ride for 48 s)
    HEALTH_BLOCK = None

    def vehicle_dead(s, handle):
        """A wreck: the game's destroyed bit (DEAD_AT) where decoded. Else (CE) a vehicle whose body field read 0 after it
        was read alive this match, so a field that reads 0 from the start keeps every vehicle usable."""
        if s.DEAD_AT is not None:
            o = s.object(handle)
            return bool(o) and bool(s.g.read(o + s.DEAD_AT, 1)[0] & 4)
        left = s.vehicle_left(handle)
        if left is None:
            return False
        seen = s.__dict__.setdefault("_veh_alive", set())
        if not math.isfinite(left) or left > 0.02:
            if math.isfinite(left) and left <= 1.5:
                seen.add(handle)
            return False
        return handle in seen

    def vehicle_left(s, handle):
        """The health left in a vehicle's body (HEALTH_BLOCK, else the VEH_VITAL field), None without its object."""
        o = s.object(handle)
        if not o:
            return None
        if s.HEALTH_BLOCK is not None:
            size, at = struct.unpack("<hH", s.g.read(o + s.HEALTH_BLOCK, 4))
            return struct.unpack("<f", s.g.read(o + at + 0x10, 4))[0] if size >= 0x18 else None
        off, dmg = s.VEH_VITAL
        v = struct.unpack("<f", s.g.read(o + off, 4))[0]
        return 1.0 - v if dmg else v

    def object(s, handle):
        if handle == NONE:
            return None
        ents, _, _ = s._array(OBJECT_HEADERS_PTR)
        salt, off = struct.unpack("<H6xi", s.g.read(ents + (handle & 0xFFFF) * 12, 12))
        if salt != handle >> 16:
            return None
        return s.g.u64(s.g.base + OBJECT_POOL_PTR) + 0x34 + off

    def objects(s, types=None):
        """[(handle, type, tag path, pos)] for live objects (type 0 biped, 1 vehicle, 2 weapon,
        3 equipment/powerup, 6 scenery)."""
        ents, mx, _ = s._array(OBJECT_HEADERS_PTR)
        b = s.g.read(ents, mx * 12)
        pool = s.g.u64(s.g.base + OBJECT_POOL_PTR) + 0x34
        out = []
        for i in range(mx):
            salt, typ = struct.unpack_from("<H", b, i * 12)[0], b[i * 12 + 3]
            if not salt or (types is not None and typ not in types):
                continue
            o = pool + struct.unpack_from("<i", b, i * 12 + 8)[0]
            head = s.g.read(o, 0x24)
            out.append(((salt << 16) | i, typ, s.tag_path(struct.unpack_from("<I", head, 0)[0]),
                        struct.unpack_from("<3f", head, 0x18)))
        return out

    VARIANT = 0x2E9F770        # the running game variant: +0x30 engine (1 ctf, 2 slayer, 3 oddball,
    KING = 0x2E31FD0           # 4 king, 5 race), +0x34 teams. King globals (CE source's king_globals,
                               # found by the hill flags' coordinates): +0 hill point count, +4 12
                               # points (3 floats), +0x94 convex hull (12 x 2), +0xF4 hill centre

    def engine(s):
        return struct.unpack("<i", s.g.read(s.g.base + s.VARIANT + 0x30, 4))[0]

    # an object's parent (what it rides, its holder): +0xD8, the field players() reads for the vehicle a unit rides. The
    # vehicle list's default (+0x14) is another field in CE: live Blood Gulch Race, all 8 race vehicles read 0x2CDD1E..
    # there and were left out as guns riding something, so no bot ever took one; +0xD8 read NONE on all 8 and on 5 guns
    PARENT_AT = 0xD8

    def race_type(s):
        """Race's type: 0 normal (in order), 1 any order, 2 flag rally; the variant's game type options at +0x7C (MCC's
        own .bin files share this layout: +0x30 engine, +0x58 score, +0x78 time all match the live reads, and +0x7C is 1
        in Crazy King, 0 in King: King's moving hill, the first option; every built-in race 0). INFERRED in memory."""
        return struct.unpack("<i", s.g.read(s.g.base + s.VARIANT + 0x7C, 4))[0]

    def tracker(s, player):
        """Enemies show on the motion tracker: the variant's flags (+0x38) bit 0 "Other Player on Radar", bit 6
        friends only (live: All 0x03, Friends Only 0x43, None 0x02). The same for every player."""
        f = s.g.read(s.g.base + s.VARIANT + 0x38, 1)[0]
        return bool(f & 1) and not f & 0x40

    SLAYER = 0x2E32120        # slayer_globals (CE source): team_score[16], then individual_score[16]
    GAME_TICKS = 0x1B86E28     # ticks since the match started (30 a second)
    # variant: +0x34 teams, +0x58 score to win, +0x78 time limit in ticks (0 = none). All found on a
    # Team Slayer variant set to 35 / 12 min in the lobby (tools/find_counters.py for the scores)

    def match_state(s, players=None):
        """dict(score={team: points}, limit, time_left s or None) for the score posture (brain/match.py).
        Slayer reads the engine's own scores; other engines: not decoded yet (score {}, limit kept)."""
        v = s.g.read(s.g.base + s.VARIANT, 0x80)
        engine, teams = struct.unpack_from("<ii", v, 0x30)
        limit, tlimit = struct.unpack_from("<i", v, 0x58)[0], struct.unpack_from("<i", v, 0x78)[0]
        ticks = struct.unpack("<i", s.g.read(s.g.base + s.GAME_TICKS, 4))[0]
        left = max(0.0, (tlimit - ticks) / 30.0) if tlimit > 0 else None
        score = {}
        if engine == 2:
            sc = struct.unpack("<32i", s.g.read(s.g.base + s.SLAYER, 128))
            if teams:
                score = {0: sc[0], 1: sc[1]}
            else:                                 # free for all: each player is his own team
                for p in players or s.players():
                    score[p["team"]] = sc[16 + p["index"]]
        return dict(score=score, limit=limit, time_left=left)

    def king_hill(s):
        """(centre, radius) of the active hill, or None."""
        b = s.g.read(s.g.base + s.KING, 0x100)
        n = struct.unpack_from("<i", b, 0)[0]
        if not 0 < n <= 12:
            return None
        c = struct.unpack_from("<3f", b, 0xF4)
        r = max(math.dist(c[:2], struct.unpack_from("<2f", b, 4 + 12 * k)) for k in range(n))
        return c, r

    def balls(s, players):
        """Oddball: [{pos, carrier}] from ball weapon objects (carried = in an inventory). Halo 2 too (its tag names,
        its slots at +0x22C: SLOTS_AT)."""
        held = {}
        for p in players:
            o = s.object(p["unit"])
            if o:
                for w in struct.unpack("<4I", s.g.read(o + s.SLOTS_AT, 16)):
                    held[w] = p
        out = []
        for h, typ, path, pos in s.objects((2,)):
            if path.endswith("\\ball"):
                car = held.get(h)
                out.append(dict(pos=car["pos"] if car else pos, carrier=car["index"] if car else None))
        return out

    def flags(s, players, stands=None):
        """CTF flags: [{team, pos, carrier}] from flag weapon objects (+0x74 = owner team). The
        carrier is the player with the flag in any inventory slot (it rides in the inventory)."""
        held = {}
        for p in players:
            o = s.object(p["unit"])
            if o:
                for w in struct.unpack("<4I", s.g.read(o + 0x2D8, 16)):
                    held[w] = p
        out = []
        for h, typ, path, pos in s.objects((2,)):
            if not path.endswith("\\flag"):
                continue
            car = held.get(h)
            if car:
                pos = car["pos"]
            team = struct.unpack("<h", s.g.read(s.object(h) + 0x74, 2))[0]
            out.append(dict(team=team, pos=pos, carrier=car["index"] if car else None))
        return out

    def players(s):
        """[{index, name, team, unit, pos, fwd, aim, health, shield, weapon}] per player slot in use (pos None = dead)."""
        base, mx, size = s._array(PLAYERS_PTR)
        out = []
        for i in range(mx):
            p = base + i * size
            if not struct.unpack("<H", s.g.read(p, 2))[0]:
                continue
            b = s.g.read(p, 0x68)
            unit, team = struct.unpack_from("<I", b, 0x64)[0], struct.unpack_from("<i", b, 0x4C)[0]
            name = b[0x20:0x40].decode("utf-16-le", "replace").partition(chr(0))[0]
            o = s.object(unit)
            d = dict(index=i, name=name, team=team, unit=unit, pos=None, fwd=None, aim=None, vehicle=-1, health=0.0, shield=0.0,
                     weapon="none")
            if o:
                cur = struct.unpack("<b", s.g.read(o + 0x2D3, 1))[0]   # (an int16 at +0x2D2 read 256 on slot 1)
                slots = struct.unpack("<4I", s.g.read(o + 0x2D8, 16))
                d["weapon"] = s.weapon_name(slots[cur]) if 0 <= cur < 4 else "none"
                d["weapons"] = [s.weapon_name(w) for w in slots if w != NONE]
                d["ammo"] = [s.ammo(w) for w in slots if w != NONE]
                d["mag"] = [s.mag(w) for w in slots if w != NONE]
                parent = struct.unpack("<I", s.g.read(o + 0xD8, 4))[0]   # the vehicle ridden, if any
                d["vehicle"] = parent if parent != NONE else -1
                b = s.g.read(o, 0x240)
                d.update(pos=struct.unpack_from("<3f", b, 0x18), fwd=struct.unpack_from("<3f", b, 0x30),
                         aim=struct.unpack_from("<3f", b, 0x234),
                         health=struct.unpack_from("<f", b, 0x9C)[0], shield=struct.unpack_from("<f", b, 0xA0)[0])
            out.append(d)
        for d in out:                # enemies on his motion tracker (tracker() per game)
            d["tracker"] = s.tracker(base + d["index"] * size)
        return out


class LiveH2(Live):
    """Halo 2 Classic (halo2.dll, MCC 1.3528, docs/H2-01): players [halo2+0xE80A28] (16 x 0x224;
    +0x2C unit, +0x3C name, +0xB8 team); object headers [halo2+0x18B7398] (12 bytes: +0 salt, +3
    type, +8 offset into the pool at ([halo2+0x18B7360] + 0x57) & ~0xF). Object +0 tag, +0x14
    parent, +0x64 position, +0x70 body forward; unit +0x168 aiming vector, +0xEC health (of 45),
    +0xF0 shield (of 70), +0x226 primary weapon index, +0x22C 4 weapon handles. Tag names: [halo2+0x15E4B68] int32 offsets by
    tag index into the names buffer at [halo2+0x15E4B78]."""
    UNIT_ZOOM = 0x255      # the unit's desired zoom byte (found live with tools/zoom_probe.py --scan)
    VEH_VITAL = (0xEC, False)   # (Live.vehicle_left: the unit health field players() reads)
    DEAD_AT = 0x10A             # (Live.vehicle_dead; ghidra/h2_object_get_health.c)
    # a gun's reserve and magazine (int16), found live: the magazine fell 60 -> 14 in 3 s of SMG fire in all 7 bots'
    # guns (+0x230), the reserve paid each refill (+0x22E: 120 -> 60 in 7 of 7); Halo 3 overrides
    AMMO_AT, MAG_AT = 0x22E, 0x230

    def rounds(s, handle):
        """(reserve + magazine, magazine) of a gun; (-1, -1) unknown: this game's fields are not decoded, or the
        kind's magazine has never held a round (battery weapons keep 0 there: read as empty they would be dropped)."""
        o = s.object(handle) if s.AMMO_AT else None
        if not o:
            return -1, -1
        tag = struct.unpack("<I", s.g.read(o, 4))[0]
        reserve = struct.unpack("<h", s.g.read(o + s.AMMO_AT, 2))[0]
        mag = struct.unpack("<h", s.g.read(o + s.MAG_AT, 2))[0]
        top = s.__dict__.setdefault("_rounds_seen", {})
        top[tag] = max(top.get(tag, 0), reserve, mag)
        mags = s.__dict__.setdefault("_mag_seen", {})       # (the fullest magazine of the kind: weapon_name's fallback)
        mags[tag] = max(mags.get(tag, 0), mag)
        if top[tag] <= 0:
            return -1, -1
        return max(0, reserve) + max(0, mag), max(0, mag)
    PARENT_AT = 0x14       # object +0x14 parent (Halo 3 +0x10)
    SLOTS_AT = 0x22C       # unit +0x22C 4 weapon handles (as flags() reads them)

    def ground_weapons(s):
        """[(name, pos)] of guns lying loose (no parent) with rounds left; unknown rounds (battery guns) kept, Halo 3's
        flags (named by their stand, not their tag) left out. Live's version reads CE's offsets (parent +0xD8, rounds
        +0x286): wrong here, and its weapon_name call marked every loose Halo 3 gun as held (second review)."""
        out, flags = [], s.__dict__.get("_flag_tags", ())
        for h, typ, path, pos in s.objects((2,)):
            o = s.object(h)
            if (not o or struct.unpack("<I", s.g.read(o + s.PARENT_AT, 4))[0] != NONE or s.rounds(h)[0] == 0
                    or struct.unpack("<I", s.g.read(o, 4))[0] in flags):
                continue
            n = path.rsplit("\\", 1)[-1].replace(" ", "_")
            out.append((WEAPON_ALIAS.get(n, n), pos))
        return out
    PLAYERS, HEADERS, POOL, NAME_OFFSETS, NAMES = 0xE80A28, 0x18B7398, 0x18B7360, 0x15E4B68, 0x15E4B78

    def __init__(s):
        s.g = Game("halo2.dll")
        s._tag_paths = {}

    def tag_path(s, tag_id):
        if tag_id not in s._tag_paths:
            offs, names = s.g.u64(s.g.base + s.NAME_OFFSETS), s.g.u64(s.g.base + s.NAMES)
            off = struct.unpack("<i", s.g.read(offs + 4 * (tag_id & 0xFFFF), 4))[0]
            s._tag_paths[tag_id] = s.g.read(names + off, 160).partition(bytes(1))[0].decode("ascii", "replace")
        return s._tag_paths[tag_id]

    def _array(s, ptr):
        hdr = s.g.u64(s.g.base + ptr)
        mx, size = struct.unpack("<ii", s.g.read(hdr + 0x20, 8))
        return hdr + struct.unpack("<q", s.g.read(hdr + 0x48, 8))[0], mx, size

    def _pool(s):
        return (s.g.u64(s.g.base + s.POOL) + 0x57) & ~0xF

    def object(s, handle):
        if handle == NONE:
            return None
        ents, _, _ = s._array(s.HEADERS)
        salt, off = struct.unpack("<H6xi", s.g.read(ents + (handle & 0xFFFF) * 12, 12))
        if salt != handle >> 16:
            return None
        return s._pool() + off

    def objects(s, types=None):
        ents, mx, _ = s._array(s.HEADERS)
        b = s.g.read(ents, mx * 12)
        pool = s._pool()
        out = []
        for i in range(mx):
            salt, typ = struct.unpack_from("<H", b, i * 12)[0], b[i * 12 + 3]
            if not salt or (types is not None and typ not in types):
                continue
            o = pool + struct.unpack_from("<i", b, i * 12 + 8)[0]
            tag = struct.unpack("<I", s.g.read(o, 4))[0]
            out.append(((salt << 16) | i, typ, s.tag_path(tag), struct.unpack("<3f", s.g.read(o + 0x64, 12))))
        return out

    def flags(s, players, stands=None):
        """CTF flags: weapon objects ...\\flag, owner team at object +0xDC (s16; -1 on other
        weapons; +0x182 holds it too). Carried = in a unit's inventory (+0x22C)."""
        held = {}
        for p in players:
            o = s.object(p["unit"])
            if o:
                for w in struct.unpack("<4I", s.g.read(o + 0x22C, 16)):
                    held[w] = p
        out = []
        for h, typ, path, pos in s.objects((2,)):
            if not path.endswith("\\flag"):
                continue
            car = held.get(h)
            if car:
                pos = car["pos"]
            team = struct.unpack("<h", s.g.read(s.object(h) + 0xDC, 2))[0]
            out.append(dict(team=team, pos=pos, carrier=car["index"] if car else None))
        return out

    def map_name(s):
        """The running map's scenario name (lockout, ...): the tag named scenarios\\multi\\..\\<n>\\<n>
        (tag order differs between loads: tag 0 is the scenario on one, globals on another)."""
        import re
        names = s.g.read(s.g.u64(s.g.base + s.NAMES), 1 << 19) or b""
        m = re.search(rb"scenarios\\multi\\(?:\w+\\)*(\w+)\\\1\x00", names)
        return m.group(1).decode() if m else None

    VARIANT = 0xE71CD0     # the running game variant (same layout as halo2\game_variants\*.bin;
                           # the copy the engine code reads): +0x44 engine (1 CTF, 2 Slayer,
                           # 3 Oddball, 4 King...), +0x48 flags (bit 0 teams), +0x50 score to win

    H2_TICKS = 0xE720B4    # int32 game ticks, 60 a second (next to the variant; HUD 7:26 left ~ 1720)

    def tracker(s, player):
        """The variant's Motion Sensor (flags +0x48 bit 1: live, On 0xDA, Off 0xD8). Every player."""
        return bool(s.g.read(s.g.base + s.VARIANT + 0x48, 1)[0] & 2)

    def match_state(s, players=None):
        """The variant's score to win (+0x50) and time limit in seconds (+0x54, 480 for an 8-minute game) with
        time left from the ticks. Team scores: Halo 2 keeps none (team score = the sum of the players'
        scores, which are not decoded yet), so score is {} and live_bots tallies deaths instead."""
        b = s.g.read(s.g.base + s.VARIANT, 0x58)
        limit, tlimit = struct.unpack_from("<ii", b, 0x50)
        ticks = struct.unpack("<i", s.g.read(s.g.base + s.H2_TICKS, 4))[0]
        left = max(0.0, tlimit - ticks / 60.0) if tlimit > 0 else None
        return dict(score={}, limit=max(0, limit), time_left=left)

    def variant(s):
        b = s.g.read(s.g.base + s.VARIANT, 0x54)
        engine, flags, score = struct.unpack_from("<I", b, 0x44)[0], struct.unpack_from("<I", b, 0x48)[0], struct.unpack_from("<I", b, 0x50)[0]
        return dict(engine=engine, teams=bool(flags & 1), score=score,
                    name=b[4:0x44].decode("utf-16-le", "replace").partition(chr(0))[0])

    def engine(s):
        """The running variant's engine (1 CTF, 2 Slayer, 3 Oddball, 4 King): Live's version read CE's address in this
        DLL. Halo 3 reads its own variant()."""
        return s.variant()["engine"]

    # the active King hill's number (the map's "King Hill <n>" netgame flags): int32 at +0x29C from the block halo2.dll
    # +0xE80500 points to (live, Lockout Team King, Moving Hill 30 s: it stepped 1, 5, 4 at 9, 39 and 69 s, 30 s apart,
    # always one of Lockout's six hills; logs/tmp/h2_hill_index.py). Before: no hill, Team King played as Slayer
    KING_HILL = (0xE80500, 0x29C)
    HILL_R = 3.0             # ponytail: Halo 2's hill boundary (its King Hill flags) not sized; Halo 3's measure

    def set_map(s, objs):
        """Each hill as its flags draw it (Halo 2: a hill's corners are several "King Hill <n>" flags): {n: (centre,
        radius)}, the centre their middle, the radius the farthest corner's (HILL_R at least)."""
        pts = {}
        for f in objs.get("netgame_flags", []):
            if f["type"].startswith("King Hill") and f["type"].split()[-1].isdigit():
                pts.setdefault(int(f["type"].split()[-1]), []).append(f["pos"])
        s._h2_hills = {}
        for k, ps in pts.items():
            c = tuple(sum(p[i] for p in ps) / len(ps) for i in range(3))
            s._h2_hills[k] = (c, max([s.HILL_R] + [math.dist(c[:2], p[:2]) for p in ps]))

    def king_hill(s):
        """(centre, radius) of the active hill, or None (no King game: the number is off the map's hills)."""
        p = s.g.u64(s.g.base + s.KING_HILL[0])
        b = s.g.read(p + s.KING_HILL[1], 4) if p else None
        if not b:
            return None
        return s.__dict__.get("_h2_hills", {}).get(struct.unpack("<i", b)[0])

    OBJ_PATHS = {"assault": ("\\assault_bomb", "\\bomb")}   # (Halo 2's tag names, as its "\flag" and "\ball")

    def objective_items(s, players, stands, key):
        """Halo 2's mode objects by their tag names (Assault's bombs: modes.Watch): [{pos, carrier, stand}], the stand the
        one it was last seen lying on (1.5 u), None before it was. Carried = in a player's weapon slots, as balls. Not
        read live yet: the bomb's path is Halo 2's naming as its flag's and ball's, unconfirmed."""
        ends = s.OBJ_PATHS.get(key, ())
        if not ends:
            return []
        held = {}
        for p in players:
            o = s.object(p["unit"])
            if o:
                for w in struct.unpack("<4I", s.g.read(o + s.SLOTS_AT, 16)):
                    held[w] = p
        last = s.__dict__.setdefault("_obj_stand", {})
        out = []
        for h, typ, path, pos in s.objects((2,)):
            if not path.endswith(ends):
                continue
            car = held.get(h)
            if car is None:
                k = min(stands, key=lambda k: (stands[k].x - pos[0]) ** 2 + (stands[k].y - pos[1]) ** 2, default=None)
                if k is not None and math.dist((stands[k].x, stands[k].y, stands[k].z), pos) < 1.5:
                    last[h] = k
            out.append(dict(pos=car["pos"] if car else pos, carrier=car["index"] if car else None, stand=last.get(h)))
        return out

    def players(s):
        base, mx, size = s._array(s.PLAYERS)
        out = []
        for i in range(mx):
            b = s.g.read(base + i * size, 0xC0)
            if not struct.unpack_from("<H", b, 0)[0]:
                continue
            unit = struct.unpack_from("<I", b, 0x2C)[0]
            name = b[0x3C:0x5C].decode("utf-16-le", "replace").partition(chr(0))[0]
            o = s.object(unit)
            d = dict(index=i, name=name, team=b[0xB8], unit=unit, pos=None, fwd=None, aim=None, vehicle=-1,
                     health=0.0, shield=0.0, weapon="none")
            if o:
                u = s.g.read(o, 0x240)
                slots = struct.unpack_from("<4I", u, 0x22C)
                cur = struct.unpack_from("<b", u, 0x226)[0]          # primary weapon index
                d["weapons"] = [s.weapon_name(w) for w in slots if w != NONE]
                d["weapon"] = s.weapon_name(slots[cur]) if 0 <= cur < 4 else "none"
                rounds = [s.rounds(w) for w in slots if w != NONE]
                d["ammo"], d["mag"] = [a for a, _ in rounds], [g for _, g in rounds]
                parent = struct.unpack_from("<I", u, 0x14)[0]
                d["vehicle"] = parent if parent != NONE else -1
                d.update(pos=struct.unpack_from("<3f", u, 0x64), fwd=struct.unpack_from("<3f", u, 0x70),
                         aim=struct.unpack_from("<3f", u, 0x168),
                         health=struct.unpack_from("<f", u, 0xEC)[0], shield=struct.unpack_from("<f", u, 0xF0)[0])
            out.append(d)
        for d in out:                # enemies on his motion tracker (tracker() per game)
            d["tracker"] = s.tracker(base + d["index"] * size)
        return out


class LiveH3(LiveH2):
    """Halo 3 (halo3.dll, MCC 1.3528, docs/H3-01). Engine globals in the game thread's TLS block
    for halo3.dll (tools/tls.py): +0x10 players array header, +0x38 object header array (header
    +0x20 max, +0x24 element size, +0x48 data pointer). Player (0x2F8): +0x28 unit, +0x58 name,
    +0xF0 team (int). Object entry (0x18): +0 salt, +3 type, +0x10 object address. Object +0
    tag, +0x10 parent, +0x50 position, +0x5C forward; unit +0x1AC aiming vector, +0xF4 health,
    +0xF8 shield (fractions), +0x262 primary weapon index, +0x268 4 weapon handles. Map: the cache
    header at halo3+0x20A9058 ('daeh'), +0xE0 scenario path. Halo 3 keeps no tag names in
    memory: weapons are named by matching loose weapons to the map's placements (learn_names)."""
    UNIT_ZOOM = 0x299      # the unit's desired zoom byte (ghidra/h3_input.c line 28)
    VEH_VITAL = (0xF4, False)   # (Live.vehicle_left: the unit health field players() reads)
    DEAD_AT = 0x110             # (Live.vehicle_dead; ghidra/h3_object_get_health.c)
    AMMO_AT, MAG_AT = 0x236, 0x23A   # a gun's reserve and magazine (int16): found live (tools/ammo_scan.py h3)
    PARENT_AT = 0x10
    # the player's Motion Tracker Mode as the game resolved his traits (byte in the player datum: 1 off, 2 allies
    # only, 3 normal; live against the lobby setting): H3 +0x214, Reach +0x350, H2A / Halo 4 +0x758
    TRACKER_AT = 0x214
    # Halo 3 magazine sizes that belong to one gun only (measured: every H3 trace; 6 is the brute shot and the
    # shotgun, 8 the magnum and the missile pod: not used)
    MAG_NAMES = {32: "assault_rifle", 36: "battle_rifle", 60: "smg", 40: "spike_rifle", 19: "needler",
                 18: "covenant_carbine", 5: "excavator", 4: "sniper"}

    def world_pos(s, rel, parent, depth=4):
        """Where a unit riding something really is: its +0x50 is then relative to the parent (the player on a
        Cyberdyne turret: read as (-0.39, 0, -0.02) for 40 s, so no bot ever shot at him). The parent's position
        plus the offset turned by the parent's forward (+0x5C) and up (+0x68), up the chain of parents."""
        pos = rel
        while parent != NONE and depth > 0:
            o = s.object(parent)
            if not o:
                break
            p = s.g.read(o, 0x74)
            ppos, f, up = (struct.unpack_from("<3f", p, k) for k in (0x50, 0x5C, 0x68))
            left = (up[1] * f[2] - up[2] * f[1], up[2] * f[0] - up[0] * f[2], up[0] * f[1] - up[1] * f[0])
            pos = tuple(ppos[k] + pos[0] * f[k] + pos[1] * left[k] + pos[2] * up[k] for k in range(3))
            parent, depth = struct.unpack_from("<I", p, s.PARENT_AT)[0], depth - 1
        return pos

    def tracker(s, player):
        """Enemies show on this player's motion tracker (anything but off / allies only; an unread 0 counts as on)."""
        return s.g.read(player + s.TRACKER_AT, 1)[0] not in (1, 2)
    # object layout for flags(): TLS offset of the object headers, the entry's type byte, object position; unit slots
    OBJECTS_AT, TYPE_AT, POS_AT, SLOTS_AT = 0x38, 3, 0x50, 0x268
    CACHE_HEADER = 0x20A9058

    def __init__(s):
        import tls
        s.g = Game("halo3.dll")
        s._tls = tls
        s._blk = None
        s._names = {}            # tag id -> weapon name, learned from placements
        s._placements = []       # (name, pos) of the map's weapons, from objects.json
        s._find()

    def _find(s):
        for _, b in s._tls.blocks(s.g):
            h = s.g.u64(b + 0x10)
            if h and s.g.read(h + 0x2C, 4) == b"@t@d":
                s._blk = b
                return
        raise RuntimeError("no Halo 3 game thread")

    def _hdr(s, off):
        h = s.g.u64(s._blk + off)
        if not h or s.g.read(h + 0x2C, 4) != b"@t@d":
            s._find()                       # a new match runs on a new game thread
            h = s.g.u64(s._blk + off)
        return h

    def _array(s, off):
        h = s._hdr(off)
        mx, size = struct.unpack("<ii", s.g.read(h + 0x20, 8))
        return s.g.u64(h + 0x48), mx, size

    def map_name(s):
        path = s.g.read(s.g.base + s.CACHE_HEADER + 0xE0, 256).partition(bytes(1))[0].decode("ascii", "replace")
        return path.rsplit("\\", 1)[-1] or None

    def set_map(s, objs):
        s._placements = [(e["item"], e["pos"]) for e in objs.get("equipment", [])]
        # vehicles keep no tag names either: a vehicle's kind is the placement it was first seen standing on
        s._vplacements = [(e.get("item") or e.get("vehicle") or "", e["pos"]) for e in objs.get("vehicles", [])]
        from .core import Vec
        s._ball_spots = {k: Vec(*f["pos"]) for k, f in enumerate(
            f for f in objs.get("netgame_flags", []) if f["type"] == "oddball_ball")}
        s._hill_bounds = []                   # (pos, radius) of each King hill the scenario sizes (Reach)
        s._hill_spots = [tuple(f["pos"]) for f in objs.get("netgame_flags", []) if f["type"] == "koth_hill"]
        for f in objs.get("netgame_flags", []):
            b = f.get("boundary")
            if f["type"] == "koth_hill" and b:
                r = b["w"] if b["shape"] == "cylinder" else min(b["w"], b["l"] or b["w"]) / 2
                if r > 0:
                    s._hill_bounds.append((tuple(f["pos"]), r))

    def tag_path(s, tag_id):
        return s._names.get(tag_id, "unknown")

    def object(s, handle):
        if handle == NONE:
            return None
        ents, mx, _ = s._array(0x38)
        if (handle & 0xFFFF) >= mx:
            return None
        e = s.g.read(ents + (handle & 0xFFFF) * 0x18, 0x18)
        if struct.unpack_from("<H", e, 0)[0] != handle >> 16:
            return None
        return struct.unpack_from("<Q", e, 0x10)[0]

    def objects(s, types=None):
        ents, mx, _ = s._array(0x38)
        b = s.g.read(ents, mx * 0x18)
        out = []
        for i in range(mx):
            salt, typ = struct.unpack_from("<H", b, i * 0x18)[0], b[i * 0x18 + 3]
            if not salt or (types is not None and typ not in types):
                continue
            o = struct.unpack_from("<Q", b, i * 0x18 + 0x10)[0]
            head = s.g.read(o, 0x5C)
            tag, parent = struct.unpack_from("<I", head, 0)[0], struct.unpack_from("<I", head, 0x10)[0]
            pos = struct.unpack_from("<3f", head, 0x50)
            if typ in (2, 3) and parent == NONE and tag not in s._names and not s._was_held((salt << 16) | i):
                s._learn(tag, pos, 1.5 if typ == 3 else 0.75)   # weapons, and equipment (Halo 3's pickups) on a placement
            if typ == 1 and parent == NONE and tag not in s._names:
                s._learn_vehicle(tag, pos)
            out.append(((salt << 16) | i, typ, s._names.get(tag, "unknown"), pos))
        return out

    # Halo 3 equipment out in the world (GitHub #9): its own tag, "objects\\equipment\\X\\X" (the pickup is X_equipment),
    # live Rat's Nest Team King Nov 6: a bot's thrown trip mine was "tripmine\\tripmine" 3.7 u off its spot for 26 s
    # (the pickup at 0x0a7d, the mine at 0x0a88), its owner's player handle at +0xBC (a frag's: player 5, its unit at
    # +0xC0). Names by brain/equipment.py ITEM
    DEPLOYED_KINDS = {"tripmine": "trip_mine", "bubbleshield": "bubble_shield", "powerdrain": "power_drain",
                      "regenerator": "regenerator", "jammer": "radar_jammer", "superflare": "flare",
                      "instantcover": "deployable_cover", "gravlift": "grav_lift", "autoturret": "autosentry"}
    DEPLOYED_OWNER = 0xBC

    def deployed(s, players, now):
        """[{kind, pos, team, t}] of the equipment out in the match (snap.deployed: brain/equipment.py keeps bots out of
        an enemy's trip mine and power drain, reads domes and heal fields); team -1 when its owner is unknown (kept clear
        of by everyone, as an enemy's); t when first seen (now: the snapshot's clock)."""
        from .core import Vec
        if s._h3_names() is None:
            return []
        ents, mx, _ = s._array(0x38)
        b = s.g.read(ents, mx * 0x18)
        team_of = {p["index"]: p.get("team", -1) for p in players or ()}
        first = s.__dict__.setdefault("_deployed_seen", {})
        out, live_ = [], set()
        for i in range(mx):
            salt, typ = struct.unpack_from("<H", b, i * 0x18)[0], b[i * 0x18 + 3]
            if not salt or typ != 3:
                continue
            o = struct.unpack_from("<Q", b, i * 0x18 + 0x10)[0]
            head = s.g.read(o, 0xC0)
            if not head:
                continue
            kind = s._deployed_kind(struct.unpack_from("<I", head, 0)[0])
            if kind is None:
                continue
            h = (salt << 16) | i
            live_.add(h)
            owner = struct.unpack_from("<I", head, s.DEPLOYED_OWNER)[0]
            out.append(dict(kind=kind, pos=Vec(*struct.unpack_from("<3f", head, 0x50)),
                            team=team_of.get(owner & 0xFFFF, -1) if owner != NONE else -1, t=first.setdefault(h, now)))
        for h in [h for h in first if h not in live_]:
            del first[h]
        return out

    def _h3_names(s):
        names = s.__dict__.get("_h3_tag_names")
        if names is None:
            s._melee_name(0)                      # (loads the map's tag names)
            names = s.__dict__.get("_h3_tag_names")
        return names or None

    def _deployed_kind(s, tag):
        cache = s.__dict__.setdefault("_deployed_kinds", {})
        if tag not in cache:
            names, k = s._h3_names() or [], tag & 0xFFFF
            parts = names[k].split("\\") if k < len(names) and names[k] else []
            cache[tag] = (s.DEPLOYED_KINDS.get(parts[-1]) if len(parts) == 4 and parts[:2] == ["objects", "equipment"]
                          and parts[-1] == parts[-2] else None)
        return cache[tag]

    def _was_held(s, handle):
        """A gun object someone has held is not the map's copy on a spawn: a bot that swapped its rifle for the
        rockets left that rifle on the rocket spawn, and every rifle read "rockets". Objects, not kinds:
        f703acf skipped every kind anyone held, so starting guns (held by all from the first tick) were never
        named from their map copies (Construct: all "unknown")."""
        return handle in s.__dict__.get("_ever_held", ())

    def _learn(s, tag, pos, r=0.75, kinds=None):
        """A loose object's kind from the nearest map placement (of these kinds), within 0.75 u; within r when that is a
        Halo 3 equipment spot: equipment settles off its spot (1.5 u), and the player who just took it stands up to 3 u
        from it (guide 20 review: 8 of 14 Halo 3 pickups read "unknown", so the bot never used them)."""
        from .equipment import NAMES
        spots = [p for p in s._placements if kinds is None or p[0] in kinds]
        best = min(spots, key=lambda p: sum((p[1][k] - pos[k]) ** 2 for k in range(3)), default=None)
        if best and sum((best[1][k] - pos[k]) ** 2 for k in range(3)) < (r if best[0] in NAMES else 0.75) ** 2:
            s._names[tag] = best[0]

    def _learn_vehicle(s, tag, pos):
        """A vehicle standing on a vehicle placement (within 1.5 u: it settles on its wheels) names its tag."""
        best = min(getattr(s, "_vplacements", []), key=lambda p: sum((p[1][k] - pos[k]) ** 2 for k in range(3)),
                   default=None)
        if best and sum((best[1][k] - pos[k]) ** 2 for k in range(3)) < 1.5 ** 2:
            s._names[tag] = best[0]

    def weapon_name(s, weapon_handle):
        o = s.object(weapon_handle)
        if not o:
            return "none"
        tag = struct.unpack("<I", s.g.read(o, 4))[0]
        s.__dict__.setdefault("_ever_held", set()).add(weapon_handle)   # (asked for guns in hands: _was_held)
        own = s._own_gun(tag) if s.MAG_NAMES else None     # (Halo 3: the map's tag names first, _own_gun)
        if own:
            return own
        if tag not in s._names and s.MAG_NAMES:
            # a gun the map never places (Epitaph has no assault rifle, the spawn gun: 69-80% of a match read "unknown")
            # is named by its full magazine, when that size belongs to one gun only
            by_mag = s.MAG_NAMES.get(s.__dict__.get("_mag_seen", {}).get(tag))
            if by_mag:
                s._names[tag] = by_mag
        name = s._names.get(tag, "unknown")
        return WEAPON_ALIAS.get(name, name)

    def _own_gun(s, tag):
        """Halo 3: a gun's name from the map's own tag names (_melee_name), only one the brain has a range for; None
        else. OBSERVED live (Rat's Nest Infection, Nov 4): energy_blade, battle_rifle, assault_rifle, gravity_hammer
        each at their tags' indexes, while the placement match had named the battle rifle "assault_rifle"."""
        cache = s.__dict__.setdefault("_own_guns", {})
        if tag not in cache:
            from .bot import WEAPON_RANGE
            n = s._melee_name(tag)
            n = WEAPON_ALIAS.get(n, n)
            cache[tag] = n if n in WEAPON_RANGE else None
        return cache[tag]

    def _melee_name(s, tag):
        """Halo 3: a weapon's name by the map's own tag names (tools/reach_tag_names.py reads Halo 3's .map files too):
        "objects\\weapons\\...\\X\\X" at the tag's index, else None. Rat's Nest places no sword, so Infection's zombies
        held "unknown" and played their swords as guns (band 5-25 u: backed off inside 5 u, swung from far; owner,
        issue #3)."""
        names = s.__dict__.get("_h3_tag_names")
        if names is None:
            try:
                sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
                import reach_tag_names
                names = reach_tag_names.cached(s.map_name(), "h3")
            except Exception:
                names = []
            s._h3_tag_names = names
        k = tag & 0xFFFF
        parts = names[k].split("\\") if k < len(names) and names[k] else []
        return parts[-1] if len(parts) >= 3 and "weapons" in parts and parts[-1] == parts[-2] else None

    def flags(s, players, stands=None):
        """Halo 3 CTF flags (not yet seen live; the player's CTF game ran as Slayer without them: no bot touched a
        flag). Halo 3 keeps no tag names, so the flag is the weapon object lying on a CTF stand (the map's
        netgame flags) when first seen, its team that stand's; every object of that tag is a flag from then on
        (a captured one respawns as a new object at home). Carried = in a unit's weapon slots (SLOTS_AT), its
        position the carrier's. Reach the same with its own layout (Boardwalk CTF: both flags lay exactly on
        stands 0 and 1)."""
        if not stands:
            return []
        weapons, held = s._weapons_held(players)
        # a flag hung on a stand that rides a vehicle (Sandtrap: each team's on its Elephant, none on the map's stand
        # points: Multi Flag read as Slayer) is where that team's stand is now: each to the nearest map stand's team,
        # nearest pairs first. live_stands: for the brain (tools/live_bots.py puts them in the map's flags)
        from .core import Vec
        from .vehicles import ELEPHANT
        live, used, rets = {}, set(), {}
        for d2, t, h, pos in sorted(((pos[0] - st.x) ** 2 + (pos[1] - st.y) ** 2, t, h, pos) for h, tag, parent, pos in weapons
                                    if h in s._riding for t, st in stands.items()):
            if t not in live and h not in used:
                live[t] = Vec(*pos)
                used.add(h)
                g = s._riding_frame.get(h)
                if g:     # where that team captures rides the same vehicle (live_returns, the brain's capture point)
                    # ponytail: the Elephant's marker for any stand on a vehicle; Sandtrap's are the only ones known
                    f, u, r = g[3:6], g[6:9], ELEPHANT["flag_return"]
                    lft = (u[1] * f[2] - u[2] * f[1], u[2] * f[0] - u[0] * f[2], u[0] * f[1] - u[1] * f[0])
                    rets[t] = Vec(*(g[k] + r[0] * f[k] + r[1] * lft[k] + r[2] * u[k] for k in range(3)))
        # (a carried flag's stand is not seen: its last place stands)
        s.live_stands = live = {**getattr(s, "live_stands", {}), **live}
        s.live_returns = {**getattr(s, "live_returns", {}), **rets}
        if live:
            stands = {**stands, **live}
        out = flags_by_stand(weapons, held, stands, s.__dict__.setdefault("_flag_tags", set()),
                             s.__dict__.setdefault("_flag_team", {}), s._placed(weapons), s._third)
        s._max_flags = max(s.__dict__.get("_max_flags", 0), len(out))
        if len(out) == 1 and s._max_flags == 1:   # One Flag (never two in the match: not CTF's moment after a capture)
            out[0]["team"] = one_flag_owner(out[0], players, s.__dict__.setdefault("_one_flag", {}))
        return out

    def objective_items(s, players, stands, key):
        """Objects of a mode found as flags are (flags_by_stand): the weapon object first seen on one of its stands
        ({k: Vec}: Assault's bomb, Stockpile's flags), its tags kept per mode (key). [{pos, carrier, stand}]"""
        weapons, held = s._weapons_held(players)
        tags = s.__dict__.setdefault("_obj_tags", {}).setdefault(key, set())
        teams = s.__dict__.setdefault("_obj_team", {}).setdefault(key, {})
        return [dict(pos=f["pos"], carrier=f["carrier"], stand=f["stand"])
                for f in flags_by_stand(weapons, held, stands, tags, teams, s._placed(weapons), s._third)]

    def balls(s, players):
        """Oddball (Halo 3, Reach keep no tag names): the ball is the weapon object first seen lying on a ball spawn
        (the map's 'oddball_ball' netgame flags, set_map), found the way flags_by_stand finds flags. [{pos, carrier}]"""
        spots = s.__dict__.get("_ball_spots")
        if not spots:
            return []
        weapons, held = s._weapons_held(players)
        # learned only off its spawn marker (Reach hangs the ball under it) or a third slot, never from a loose gun on a
        # spawn: Boardwalk's needle rifle lies 0.45 u from ball spawn 2, and the ball spawns there too
        return [dict(pos=f["pos"], carrier=f["carrier"]) for f in flags_by_stand(
            weapons, held, spots, s.__dict__.setdefault("_ball_tags", set()), s.__dict__.setdefault("_ball_team", {}),
            (), s._third, loose=False, anchored=s._attached)]

    HUD_WAYPOINTS = 0xF0     # TLS slot: the host's HUD waypoints, 0x74 bytes each
    HILL_R = 3.0             # ponytail: Halo 3's hill boundary not decoded; the scorers stood within ~3 u

    def king_hill(s):
        """(centre, radius) of the active King hill. Found in the host's HUD waypoints (the block at TLS slot 0xF0): a
        waypoint's +0 object handle (the hill's marker), +0x10 kind 0x41, +0x14 scale 1.0, +0x24 its place (a little
        above the hill), +0x30 1 while shown (the hill before it stays listed with 0). Matched to the map's hill in 3D
        (Construct stacks two hills), else the waypoint's place. Checked against the score over six hill moves
        (Construct Crazy King: each time a team scored, its players stood on that hill)."""
        base = s.g.u64(s._blk + s.HUD_WAYPOINTS) if s._blk else 0
        b = s.g.read(base, 0x3000) if base else None
        if not b:
            return None
        for o in range(0, len(b) - 0x34, 4):
            if (b[o + 0x10] == 0x41 and struct.unpack_from("<I", b, o + 0x10)[0] == 0x41
                    and struct.unpack_from("<f", b, o + 0x14)[0] == 1.0 and struct.unpack_from("<I", b, o + 0x30)[0] == 1):
                pos = struct.unpack_from("<3f", b, o + 0x24)
                best = min(s.__dict__.get("_hill_spots", []), key=lambda h: sum((h[k] - pos[k]) ** 2 for k in range(3)),
                           default=None)
                if best is not None and sum((best[k] - pos[k]) ** 2 for k in range(3)) < 3.0 ** 2:
                    return best, s.HILL_R
                return (pos[0], pos[1], pos[2] - 1.5), s.HILL_R
        return None

    def marked_players(s, raw):
        """Player indexes the host's HUD marks as a VIP: a waypoint (+0 the player's own datum handle, +0x30 shown) whose
        kind (+0x10) has bit 0x40 (Construct VIP, live: the host's four teammates were listed with kinds 0, 4, 8, 0x20
        and now and then 0x41; the one enemy listed, always 0x41: the enemy VIP everyone sees). Not the Juggernaut: no
        marker on a body in that mode (brain/modes.py reads his overshield). tools/h3_waypoints.py lists the block."""
        players = {p["handle"]: p["index"] for p in raw if p.get("handle") is not None and p.get("pos") is not None}
        base = s.g.u64(s._blk + s.HUD_WAYPOINTS) if s._blk else 0
        b = s.g.read(base, 0x3000) if base and players else None
        if not b:
            return set()
        out = set()
        for o in range(0, len(b) - 0x34, 4):
            h = struct.unpack_from("<I", b, o)[0]
            if (h in players and struct.unpack_from("<I", b, o + 0x30)[0] == 1
                    and struct.unpack_from("<I", b, o + 0x10)[0] & 0x40
                    and 0.0 < struct.unpack_from("<f", b, o + 0x14)[0] < 100.0):
                out.add(players[h])
        return out

    def item_stands(s):
        """Places of the stands objective items hang on (a weapon object whose parent is a type-10 object): Halo 3's
        Assault bomb lies on a stand the map export does not list (Construct: the neutral bomb at the centre)."""
        ents, mx, _ = s._array(s.OBJECTS_AT)
        b = s.g.read(ents, mx * 0x18)
        out = []
        for i in range(mx):
            if not struct.unpack_from("<H", b, i * 0x18)[0] or b[i * 0x18 + s.TYPE_AT] != 2:
                continue
            parent = struct.unpack("<I", s.g.read(struct.unpack_from("<Q", b, i * 0x18 + 0x10)[0] + s.PARENT_AT, 4))[0]
            pi = parent & 0xFFFF
            if parent != NONE and pi < mx and b[pi * 0x18 + s.TYPE_AT] == 10:
                po = struct.unpack_from("<Q", b, pi * 0x18 + 0x10)[0]
                out.append(struct.unpack("<3f", s.g.read(po + s.POS_AT, 12)))
        return out

    def _weapons_held(s, players):
        """([(handle, tag, +0x14, pos)] of every weapon object, {weapon handle: player} of those in a unit's slots)."""
        held, s._third, s._attached, s._riding, s._riding_frame = {}, set(), set(), {}, {}
        for p in (p for p in players if p.get("pos") is not None):
            o = s.object(p["unit"])
            if o:
                for k, w in enumerate(struct.unpack("<4I", s.g.read(o + s.SLOTS_AT, 16))):
                    if w != NONE:
                        held[w] = p
                        if k >= 2:          # past the two guns: the objective (Reach's flag went to slot 2)
                            s._third.add(w)
        ents, mx, _ = s._array(s.OBJECTS_AT)
        b = s.g.read(ents, mx * 0x18)
        weapons = []
        by_unit = {p["unit"]: p for p in players if p.get("pos") is not None} if s.PARENT_CARRIES else {}
        for i in range(mx):
            salt, typ = struct.unpack_from("<H", b, i * 0x18)[0], b[i * 0x18 + s.TYPE_AT]
            if not salt or typ != 2:
                continue
            head = s.g.read(struct.unpack_from("<Q", b, i * 0x18 + 0x10)[0], s.POS_AT + 12)
            h, parent = (salt << 16) | i, struct.unpack_from("<I", head, s.PARENT_AT)[0]
            pos = struct.unpack_from("<3f", head, s.POS_AT)
            if parent in by_unit and h not in held:
                held[h] = by_unit[parent]       # carried outside the weapon slots (Halo 4's flag: PARENT_CARRIES)
            # (not a carrier from +0x14: on Reach both flags at home point it at one player for ~5 s at a time)
            if s.ATTACHED_RELATIVE and parent != NONE and h not in held and abs(pos[0]) < 2 and abs(pos[1]) < 2:
                # riding something that is no player's hands: Reach's ball sits on its spawn marker at (0, 0, 0.2)
                # (Boardwalk Oddball): its place is the marker's plus that
                po = s.object(parent)
                if po and po not in (s.object(p["unit"]) for p in players if p.get("pos") is not None):
                    base = struct.unpack("<3f", s.g.read(po + s.POS_AT, 12))
                    pos = (base[0] + pos[0], base[1] + pos[1], base[2] + pos[2])
                    # and that marker on a vehicle (Halo 3 Sandtrap: each CTF flag's stand rides its team's Elephant;
                    # the flag read 1.4 u from the map's origin and Multi Flag played as Slayer): the vehicle's place
                    # plus the offset along its axes (forward, then up, after its position)
                    gp = struct.unpack("<I", s.g.read(po + s.PARENT_AT, 4))[0] if s.CHAIN_ATTACHED else NONE
                    gpo = s.object(gp) if gp != NONE else None
                    if gpo:
                        g = struct.unpack("<9f", s.g.read(gpo + s.POS_AT, 36))
                        f, u = g[3:6], g[6:9]
                        left = (u[1] * f[2] - u[2] * f[1], u[2] * f[0] - u[0] * f[2], u[0] * f[1] - u[1] * f[0])
                        pos = tuple(g[k] + pos[0] * f[k] + pos[1] * left[k] + pos[2] * u[k] for k in range(3))
                        if (parent & 0xFFFF) < mx and b[(parent & 0xFFFF) * 0x18 + s.TYPE_AT] == 10:
                            s._riding[h] = gp       # on a stand (a type 10 object), not a gun on a turret: a flag
                            s._riding_frame[h] = g  # (and where that vehicle is: its flag return, flags())
                    s._attached.add(h)
            weapons.append((h, struct.unpack_from("<I", head, s.TAG_AT)[0], parent, pos))
        if s.OBJECTIVE_NAMES:
            s._name_objectives(weapons)
        return weapons, held

    TAG_AT = 0                 # object +0 tag (Halo 4 / H2A +8)
    PARENT_CARRIES = False     # an object whose parent is a player's body is his (Halo 4; Reach's +0x14 is no carrier)
    ATTACHED_RELATIVE = True   # an object on a marker holds its place relative to it (Reach's ball; Halo 4's absolute)
    CHAIN_ATTACHED = True      # ... and that marker on a vehicle, oriented by its forward / up after POS_AT (Halo 3, live
    # unit length and square; the other games' are not checked: off there)
    OBJECTIVE_NAMES = {}       # {tag set: weapon names}: objectives named by the map's tag names (LiveH4F)

    def _name_objectives(s, weapons):
        """Seed the objective tag sets flags_by_stand learns into, from the map's tag names (OBJECTIVE_NAMES; a key
        (attr, mode) is objective_items' set for that mode): exact where a tag id indexes its name (Halo 4, H2A), so no
        gun on a stand is ever taken for a flag. Each tag is looked up once."""
        kinds = s.__dict__.setdefault("_tag_kind", {})
        for h, tag, parent, pos in weapons:
            if tag in kinds:
                continue
            kinds[tag] = name = s._own_name(tag, "weapons")
            for key, names in s.OBJECTIVE_NAMES.items():
                if name in names:
                    attr, mode = key if isinstance(key, tuple) else (key, None)
                    tags = s.__dict__.setdefault(attr, {} if mode else set())
                    (tags.setdefault(mode, set()) if mode else tags).add(tag)

    def _placed(s, weapons):
        """Tags with an object on one of the map's weapon placements: guns, never a flag or a ball (Boardwalk's needle
        rifle lies 0.45 u from a ball spawn and was learned as the ball)."""
        return {tag for h, tag, parent, pos in weapons
                if any(sum((p[k] - pos[k]) ** 2 for k in range(3)) < 0.75 ** 2 for _, p in s._placements)}

    def variant(s):
        """The running game variant inside the game globals (TLS +0x48): +0x16CC game engine (2
        Slayer; H2 numbering assumed for the rest), +0x170C flags bit 0 teams (verified: 2v2
        Hardcore TS with Teams Enabled 7, Disabled 6)."""
        gg = s.g.u64(s._blk + 0x48)
        engine = struct.unpack("<i", s.g.read(gg + 0x16CC, 4))[0]
        return dict(engine=engine, teams=bool(s.g.read(gg + 0x170C, 1)[0] & 1), score=0, name="")

    # game globals (TLS +0x48), each checked against the HUD in two matches (50 / 12 min, 25 / 9 min):
    H3_TEAM_SCORE = 0x249C0    # one 0x18-byte record per team, int32 score first (red 11->14, blue 12->15)
    H3_SCORE_LIMIT = 0x17BC    # int16 score to win (50, then 25)
    H3_TIME_LIMIT = 0x170D     # byte, minutes (12, then 9; 0 = none), next to the teams flags
    H3_TICKS = 0x101B4         # int32 game ticks, 60 a second (counts from a few s before the clock)

    def match_state(s, players=None):
        """Team scores, the variant's score to win and time left (brain/match.py). Halo 3 layout."""
        if s._blk is None:
            s.players()
        gg = s.g.u64(s._blk + 0x48)
        teams = bool(s.g.read(gg + 0x170C, 1)[0] & 1)
        limit = struct.unpack("<h", s.g.read(gg + s.H3_SCORE_LIMIT, 2))[0]
        minutes = s.g.read(gg + s.H3_TIME_LIMIT, 1)[0]
        ticks = struct.unpack("<i", s.g.read(gg + s.H3_TICKS, 4))[0]
        score = {}
        if teams:
            b = s.g.read(gg + s.H3_TEAM_SCORE, 0x30)
            score = {0: struct.unpack_from("<i", b, 0)[0], 1: struct.unpack_from("<i", b, 0x18)[0]}
        left = max(0.0, minutes * 60.0 - ticks / 60.0) if minutes else None
        return dict(score=score, limit=max(0, limit), time_left=left)

    def players(s):
        base, mx, size = s._array(0x10)
        out = []
        for i in range(mx):
            b = s.g.read(base + i * size, 0xF4)
            if not struct.unpack_from("<H", b, 0)[0]:
                continue
            unit = struct.unpack_from("<I", b, 0x28)[0]
            name = b[0x58:0x78].decode("utf-16-le", "replace").partition(chr(0))[0]
            o = s.object(unit)
            d = dict(index=i, name=name, team=struct.unpack_from("<i", b, 0xF0)[0], unit=unit, pos=None, fwd=None,
                     aim=None, vehicle=-1, health=0.0, shield=0.0, weapon="none",
                     handle=(struct.unpack_from("<H", b, 0)[0] << 16) | i)   # (the player's own datum: HUD markers)
            if o:
                u = s.g.read(o, 0x28C)
                slots = struct.unpack_from("<4I", u, 0x268)
                cur = struct.unpack_from("<b", u, 0x262)[0]
                d["weapons"] = [s.weapon_name(w) for w in slots if w != NONE]
                d["weapon"] = s.weapon_name(slots[cur]) if 0 <= cur < 4 else "none"
                w = s.object(slots[cur]) if 0 <= cur < 4 else None
                d["wtag"] = struct.unpack("<I", s.g.read(w, 4))[0] if w else None   # (Infection: who is a zombie)
                rounds = [s.rounds(w) for w in slots if w != NONE]
                d["ammo"], d["mag"] = [a for a, _ in rounds], [g for _, g in rounds]
                parent = struct.unpack_from("<I", u, 0x10)[0]
                d["vehicle"] = parent if parent != NONE else -1
                d.update(pos=s.world_pos(struct.unpack_from("<3f", u, 0x50), parent), fwd=struct.unpack_from("<3f", u, 0x5C),
                         aim=struct.unpack_from("<3f", u, 0x1AC),
                         health=struct.unpack_from("<f", u, 0xF4)[0], shield=struct.unpack_from("<f", u, 0xF8)[0])
                if s.EQUIPMENT_AT:
                    # Halo 3's carried equipment (unit +0x288: unit_has_equipment and the use-equipment
                    # event read it, ghidra/h3_has_equipment.c, h3_equipment_use.c), named by placement
                    from .equipment import NAMES
                    h = struct.unpack_from("<I", u, s.EQUIPMENT_AT)[0]
                    d["equipment"] = NAMES.get(s.weapon_name(h), "unknown") if h != NONE else ""
                    taken = s.__dict__.setdefault("_eq_taken", {})        # player -> the handle in his slot
                    eo = s.object(h) if d["equipment"] == "unknown" and taken.get(i) != h else None
                    if eo:
                        # just taken, unnamed: the equipment spot within 3 u of him names it (never a gun's spot)
                        s._learn(struct.unpack("<I", s.g.read(eo, 4))[0], d["pos"], 3.0, NAMES)
                        d["equipment"] = NAMES.get(s.weapon_name(h), "unknown")
                    taken[i] = h
            out.append(d)
        for d in out:                # enemies on his motion tracker (tracker() per game)
            d["tracker"] = s.tracker(base + d["index"] * size)
        return out

    EQUIPMENT_AT = 0x288       # Halo 3 (Reach, ODST and H4 have their own players())


class LiveReach(LiveH3):
    """Halo: Reach (haloreach.dll, MCC 1.3528). Engine globals in the game thread's TLS block:
    +0x10 object header array, +0x18 players array, +0x48 game globals. Array header: +0x20
    element size, +0x28 '@t@d', +0x2C max, +0x50 data pointer. Player (0x490): +0x28 unit, +0xB0
    name. Object entry (0x18): +0 salt, +4 type (2 weapon, 3 equipment), +0x10 object address.
    Object +0 tag, +0xC next sibling, +0x10 first child, +0x14 parent, +0x44 position (feet),
    +0x50 forward; unit +0x350 4 weapon handles, +0x374 armor ability."""
    UNIT_ZOOM = None      # the unit's desired zoom byte (not decoded)
    DEAD_AT, HEALTH_BLOCK = 0x138, 0x184   # (Live.vehicle_dead / vehicle_left; ghidra/hr_object_get_health.c)
    MAG_NAMES = {}        # (Halo 3's magazine sizes are not this game's)
    # a gun's reserve and magazine (int16), found live (Beachhead Firefight Nov 6, logs/tmp/reach_ammo_scan.py): a
    # magnum's magazine +0x2CA fell one a shot and refilled to 8 as the reserve +0x2C6 paid it (8 -> 0, 18 -> 10,
    # 10 -> 2), the reserve rose 0 -> 18 at a pickup. Unread before: Reach bots had no ammo, so none ever went for a
    # gun when theirs ran dry (Bot.rearm; GitHub #12)
    AMMO_AT, MAG_AT = 0x2C6, 0x2CA
    # loose guns by this game's parent field (+0x14, the vehicle a seated unit rides and the holder of a flag here);
    # rounds unknown keep a gun. CE's reader (+0xD8, CE rounds) found no Reach power weapon lying loose, so no bot was
    # ever sent for one (director.power_runs; 0 runs live match 220515). H4 / H2A inherit it with theirs (+0x24)
    ground_weapons = LiveH2.ground_weapons
    CACHE_HEADER = 0x274DCB0   # 'daeh', +0xE0 scenario path (as Halo 3)
    # the loadout slot a player's next spawn uses: the host's player_loadout_request handler writes
    # it to player +0x3E4 and +0x3E8 (ghidra/hr_loadout_perform.c); botcore SET_LOADOUT does it for bots
    LOADOUT, PLAYERS_AT = 0x3E4, 0x18
    # armor abilities (static: not yet checked live): the unit's handle (unit_has_equipment,
    # ghidra/hr_equipment_hs.c), the type block its tag fills (tools/equipment_types.py: the kits' own
    # tags; ghidra/hr_ability_queries.c reads the same blocks), running while the object's activation
    # tick is set (+0x1D8) or its active time runs (+0x1E0, ghidra/hr_ability_active.c)
    ABILITY_AT, TAG_RVAS = 0x374, (0xC1A600, 0x4E39F20)       # unit field; tag instance table, data bases
    TYPES_AT, TYPE_COUNT = 0x28C, 24
    TYPE_ORDER = ((5, "armor_lock"), (17, "jetpack"), (18, "hologram"), (20, "evade"), (22, "sprint"),
                  (1, "drop_shield"), (3, "active_camo"), (16, "health_pack"), (0, "powerup"))

    OBJECTS_AT, TYPE_AT, POS_AT, SLOTS_AT, PARENT_AT = 0x10, 4, 0x44, 0x350, 0x14   # LiveH3.flags on Reach's layout
    CHAIN_ATTACHED = False     # (its orientation after POS_AT not checked: LiveH3)

    def _unpack(s, v):
        """A packed tag data address: base by the top 4 bits, plus 4 x the whole value (as the game does)."""
        return s.g.u64(s.g.base + s.TAG_RVAS[1] + 8 * (v >> 28)) + v * 4

    def _tag_def(s, tag):
        table = s.g.u64(s.g.base + s.TAG_RVAS[0])
        return s._unpack(struct.unpack("<I", s.g.read(table + 4 + (tag & 0xFFFF) * 8, 4))[0])

    def _type_counts(s, d):
        b = s.g.read(d + s.TYPES_AT, 12 * s.TYPE_COUNT)
        return [struct.unpack_from("<i", b, 12 * k)[0] for k in range(s.TYPE_COUNT)]

    def _ability_tag(s, o):
        return struct.unpack("<H", s.g.read(o, 2))[0]

    def _ability_on(s, o):
        b = s.g.read(o + 0x1D8, 0xC)
        return struct.unpack_from("<i", b, 0)[0] != -1 or struct.unpack_from("<i", b, 8)[0] > 0

    def ability(s, handle):
        """(kind, running) of an armor ability handle; ("", False) for none, "unknown" when its tag
        fills no block we know."""
        o = s.object(handle) if handle != NONE else None
        if not o:
            return "", False
        try:
            tag = s._ability_tag(o)
            kinds = s.__dict__.setdefault("_ability_kind", {})
            if tag not in kinds:
                counts = s._type_counts(s._tag_def(tag))
                kinds[tag] = next((k for i, k in s.TYPE_ORDER if i < len(counts) and counts[i] > 0), "unknown")
            return kinds[tag], s._ability_on(o)
        except (OSError, struct.error, IndexError, TypeError):
            # (TypeError: a read that failed returns None; uncaught it failed the whole tick, Spire 244.6 s)
            return "unknown", False

    def loadout(s, index):
        """A player's loadout slot, or None where the game has no loadouts."""
        if not s.LOADOUT:
            return None
        base, mx, size = s._array(s.PLAYERS_AT)
        if not 0 <= index < mx:
            return None
        return struct.unpack("<i", s.g.read(base + index * size + s.LOADOUT, 4))[0]

    # game globals (TLS +0x48) on Boardwalk TU Team Slayer DMR set to 25 / 9 min, against the HUD:
    REACH_TEAM_SCORE = 0x28518   # int32 per team, 8 bytes apart (red 0->1->3->5, blue 1->6->7->8)
    REACH_TICKS = 0x1F0EC        # int32 game ticks, 60 a second
    REACH_SCORE_LIMIT = 0x10D28  # int16 score to win (25, then 50 in a second game)
    REACH_TIME_LIMIT = 0x17A6    # byte, minutes (9, then 12; 0 = none)

    def match_state(s, players=None):
        """Reach team scores (and the limit / clock once decoded). Halo 4 and H2A derive from this
        class but have their own layout: they override match_state."""
        if s._blk is None:
            s.players()
        gg = s.g.u64(s._blk + 0x48)
        b = s.g.read(gg + s.REACH_TEAM_SCORE, 16)
        score = {0: struct.unpack_from("<i", b, 0)[0], 1: struct.unpack_from("<i", b, 8)[0]}
        limit = struct.unpack("<h", s.g.read(gg + s.REACH_SCORE_LIMIT, 2))[0] if s.REACH_SCORE_LIMIT else 0
        left = None
        if s.REACH_TIME_LIMIT:
            minutes = s.g.read(gg + s.REACH_TIME_LIMIT, 1)[0]
            ticks = struct.unpack("<i", s.g.read(gg + s.REACH_TICKS, 4))[0]
            left = max(0.0, minutes * 60.0 - ticks / 60.0) if minutes else None
        return dict(score=score, limit=max(0, limit), time_left=left)

    def __init__(s):
        import tls
        s.g = Game("haloreach.dll")
        s._tls = tls
        s._blk = None
        s._names = {}
        s._placements = []
        s._last_dmg = {}          # unit -> (shield damage, body damage) last read
        s._bare = set()           # units seen taking body damage with no shield damage
        s._bare_players = set()
        s._find()

    MAGAZINE = {15: "dmr", 32: "assault_rifle", 8: "magnum", 21: "needle_rifle", 4: "sniper", 6: "shotgun",
                2: "rockets"}   # ponytail: by magazine size; battery weapons stay "unknown"

    def weapon_name(s, weapon_handle):
        """By the map placement the kind was seen lying on (LiveH3._learn), else by the largest
        magazine seen for the tag (weapon +0x2CA rounds loaded): variants like SWAT remove every
        map weapon, and Reach keeps no tag names."""
        o = s.object(weapon_handle)
        if not o:
            return "none"
        tag = struct.unpack("<I", s.g.read(o, 4))[0]
        s.__dict__.setdefault("_ever_held", set()).add(weapon_handle)   # held: never names a spawn (_was_held)
        if tag in s._names:
            return WEAPON_ALIAS.get(s._names[tag], s._names[tag])
        # by the map's own tag name, as vehicles (live Spire Invasion: 36% of bot time read "unknown", the Elites'
        # plasma guns have no magazine); only guns the brain has a range for (the flag, core, bomb, skull keep their
        # own handling)
        own = s.__dict__.setdefault("_own_gun", {})
        if tag not in own:
            from .bot import WEAPON_RANGE
            n = s._own_name(tag, "weapons")
            n = WEAPON_ALIAS.get(n, n)
            own[tag] = n if n in WEAPON_RANGE else None
        if own[tag]:
            return own[tag]
        mag = s.__dict__.setdefault("_mag", {})
        mag[tag] = max(mag.get(tag, 0), struct.unpack("<h", s.g.read(o + 0x2CA, 2))[0])
        return s.MAGAZINE.get(mag[tag], "unknown")

    def _vitals(s, unit, shield_dmg, body_dmg, player=True):
        """(shield, health) as 0..1 left. A variant without shields (SWAT) has no field for it:
        the shield-damage float simply stays 0, so 1 - damage would read full shields. A unit
        whose body takes a hit while its shield damage stays 0 has none; after two such units
        players the whole match counts as shieldless (AI units only count for themselves: Grunts
        never have shields)."""
        prev = s._last_dmg.get(unit)
        if shield_dmg > 1e-3:                    # this unit has shields after all; so does the match
            s._bare.discard(unit)
            s._bare_players.discard(unit)
            if player:
                s._shields_seen = True
        elif prev and body_dmg > prev[1] + 1e-3 and prev[0] == 0.0:
            s._bare.add(unit)                    # (a fall hurts the body past full shields too)
            if player:
                s._bare_players.add(unit)
        s._last_dmg[unit] = (shield_dmg, body_dmg)
        # the whole match counts as shieldless (SWAT) only if no player's shield was ever hit:
        # two falls in H2A made every shield read 0, so shield hits looked like misses
        bare = unit in s._bare or (player and len(s._bare_players) >= 2 and not getattr(s, "_shields_seen", False))
        return (0.0 if bare else max(0.0, 1.0 - shield_dmg)), max(0.0, 1.0 - body_dmg)

    def _find(s):
        for _, b in s._tls.blocks(s.g):
            h = s.g.u64(b + 0x18)
            if h and s.g.read(h + 0x28, 4) == b"@t@d":
                s._blk = b
                return
        raise RuntimeError("no Halo: Reach game thread")

    def _hdr(s, off):
        h = s.g.u64(s._blk + off)
        if not h or s.g.read(h + 0x28, 4) != b"@t@d":
            s._find()
            h = s.g.u64(s._blk + off)
        return h

    def _array(s, off):
        h = s._hdr(off)
        size, mx = struct.unpack_from("<i8xi", s.g.read(h + 0x20, 0x10))
        return s.g.u64(h + 0x50), mx, size

    def object(s, handle):
        if handle == NONE:
            return None
        ents, mx, _ = s._array(0x10)
        if (handle & 0xFFFF) >= mx:
            return None
        e = s.g.read(ents + (handle & 0xFFFF) * 0x18, 0x18)
        if struct.unpack_from("<H", e, 0)[0] != handle >> 16:
            return None
        return struct.unpack_from("<Q", e, 0x10)[0]

    def _own_name(s, tag, folder):
        """A tag's object name, "...\\<folder>\\...\\X\\X", from the map's tag names (brain/forge.py): at the tag's index
        or up to 4 before it (live Forge World: an index fell 2 past its own name, on a shader / texture). None."""
        try:
            from .forge import _tag_names
            names = _tag_names(s)
        except Exception:
            return None
        k = tag & 0xFFFF
        for j in range(min(k, len(names) - 1), max(-1, k - 5), -1):
            parts = names[j].split("\\")
            # (or an infected's own gun filed in another weapon's folder: Halo 4's prongs at
            # "...\\melee\\storm_energy_sword\\storm_flood_sword", live Adrift Flood: they read "unknown")
            if folder in parts and len(parts) >= 2 and (parts[-1] == parts[-2] or parts[-1] in ZOMBIE_TAGS):
                return parts[-1]
        return None

    def loose_flags(s):
        """[(x, y, z)] of CTF flag objects ("...\\weapons\\multiplayer\\flag\\flag" by the map's tag names) lying with no
        parent: on a Forge map variant the flags start on stands the scenario never places (Forge World: CTF played as
        Slayer, the export's stands are the blank canvas's)."""
        return s.loose_named("flag")

    def loose_named(s, name):
        """[(x, y, z)] of weapon objects named ...\\multiplayer\\<name>\\<name> (the map's tag names) lying with no parent:
        loose_flags, and Headhunter's skulls (brain/modes.py Watch)."""
        ents, mx, _ = s._array(s.OBJECTS_AT)
        b = s.g.read(ents, mx * 0x18)
        out, cache = [], s.__dict__.setdefault("_named_tags", {}).setdefault(name, {})
        for i in range(mx):
            if not struct.unpack_from("<H", b, i * 0x18)[0] or b[i * 0x18 + s.TYPE_AT] != 2:
                continue
            o = struct.unpack_from("<Q", b, i * 0x18 + 0x10)[0]
            head = s.g.read(o, 0x50)
            tag, parent = struct.unpack_from("<I", head, 0)[0], struct.unpack_from("<I", head, s.PARENT_AT)[0]
            if tag not in cache:
                cache[tag] = s._own_name(tag, "multiplayer") == name
            if cache[tag] and parent == NONE:
                out.append(struct.unpack_from("<3f", head, s.POS_AT))
        return out

    def _learn_vehicle(s, tag, pos):
        """On its placement (LiveH3), else by the map's own tag name (the .map file's names in tag-index order,
        brain/forge.py): a vehicle the scenario never places is named too (Forge World: every vehicle is the map
        variant's, all read "unknown" and the bots barely used them)."""
        super()._learn_vehicle(tag, pos)
        if tag not in s._names:
            # (live Forge World: the warthog's, ghost's and mongoose's index fell 2 past their own names)
            name = s._own_name(tag, "vehicles")
            if name:
                s._names[tag] = name

    def objects(s, types=None):
        ents, mx, _ = s._array(0x10)
        b = s.g.read(ents, mx * 0x18)
        out = []
        for i in range(mx):
            salt, typ = struct.unpack_from("<H", b, i * 0x18)[0], b[i * 0x18 + 4]
            if not salt or (types is not None and typ not in types):
                continue
            o = struct.unpack_from("<Q", b, i * 0x18 + 0x10)[0]
            head = s.g.read(o, 0x50)
            tag, parent = struct.unpack_from("<I", head, 0)[0], struct.unpack_from("<I", head, 0x14)[0]
            pos = struct.unpack_from("<3f", head, 0x44)
            if typ == 2 and parent == NONE and tag not in s._names and not s._was_held((salt << 16) | i):
                s._learn(tag, pos)
            if typ == 2 and tag not in s._names:
                # no map placement under it: its own tag name (GitHub #11: a Forge creation's guns lie where the canvas
                # has no placement, so all read "unknown" and none was ever in snap.ground_weapons for a bot to take)
                own = s._own_name(tag, "weapons")
                if own:
                    s._names[tag] = WEAPON_ALIAS.get(own, own)
            if typ == 1 and parent == NONE and tag not in s._names:
                s._learn_vehicle(tag, pos)
            elif typ == 1 and tag not in s._names:
                # a gun riding on a vehicle (a Warthog's chaingun, a Falcon's side guns: vehicles of their own) by the
                # map's own tag name only, never a placement near it (that is its vehicle's). Unnamed before: every
                # Spire gunner read "unknown" (9,606 lines in 4 games), so the chaingun's height never applied and the
                # gunner shot 0.3 u high
                s._names[tag] = s._own_name(tag, "vehicles") or "unknown"
            out.append(((salt << 16) | i, typ, s._names.get(tag, "unknown"), pos))
        return out

    # the variant's teams flag: game globals +0x17A4 bit 0 (6 in Infection and Headhunter, 7 in CTF and Territories,
    # live, logs/tmp/gg_*.bin). Teams were assumed before: free-for-all games got red and blue bots and ended in seconds
    # (only one bot joined Infection)
    TEAMS_FLAGS = 0x17A4
    TRACKER_AT = 0x350         # (LiveH3.tracker; Unchanged 3, Off 1, Allies Only 2 seen live)

    VARIANT_SLOT = 0x48        # the TLS block holding the variant (Reach: the game globals)

    def _vbase(s):
        if s._blk is None:
            s.players()
        return s.g.u64(s._blk + s.VARIANT_SLOT)

    def variant(s):
        teams = bool(s.g.read(s._vbase() + s.TEAMS_FLAGS, 1)[0] & 1)
        return dict(engine=s.engine(), teams=teams, score=0, name=s.variant_name())

    VARIANT_NAME = 0x1574      # the variant's name (UTF-16) in the game globals ("CAPTURE THE FLAG")
    # Reach runs every game type on one scripted engine (Megalo); the variant's engine icon tells which (+0x1524, the
    # same at +0x1774: CTF 0, Oddball 2, King 3 in dumps of each on, tools/reach_variant_dump.py; Slayer 1 by Reach's
    # order), here in CE's numbering. Other icons (Juggernaut, Assault ...) play as Slayer; the built-in names are the
    # fallback for an icon out of range
    ENGINE_ICON, ICON_ENGINE = 0x1524, {0: 1, 1: 2, 2: 3, 3: 4}
    ENGINE_WORDS = ((("FLAG", "CTF"), 1), (("ODDBALL",), 3), (("HILL", "KING", "KOTH"), 4))
    # the active hill: the handle of its marker object in the game globals (TU Crazy King on Boardwalk: it went hill 3,
    # 1, 2, 7 a minute apart, and blue scored 3 while standing on hill 1 as it named hill 1)
    REACH_HILL = 0x253C8
    HILL_R = 3.0               # ponytail: the hill's boundary (a Forge shape) not decoded; players within 4 u scored

    def variant_name(s):
        return s.g.read(s._vbase() + s.VARIANT_NAME, 64).decode("utf-16-le", "replace").partition(chr(0))[0]

    def engine_icon(s):
        """The variant's engine icon as read (modes.H2A_ICONS)."""
        return struct.unpack("<i", s.g.read(s._vbase() + s.ENGINE_ICON, 4))[0]

    def engine(s):
        icon = struct.unpack("<i", s.g.read(s._vbase() + s.ENGINE_ICON, 4))[0]
        if 0 <= icon <= 16:
            return s.ICON_ENGINE.get(icon, 2)
        name = s.variant_name().upper()
        return next((e for words, e in s.ENGINE_WORDS if any(w in name for w in words)), 2)

    def invasion(s):
        """Invasion is on (the player: not Invasion Slayer, which plays as Slayer): by the variant's name."""
        name = s.variant_name().upper()
        return "INVASION" in name and "SLAYER" not in name

    # player +0x414: the object the game respawns the player at. Invasion's script gives every player its team's zone
    # of the phase (live Spire, all 8 players: an inv_res_p1 hill beacon of their side in phase 1, inv_res_p2 in phase 2)
    RESPAWN_ZONE_AT = 0x414
    # Rally's destination: the rally flag the HUD marks, the Megalo object variable before REACH_HILL (live Boneyard
    # Rally, Nov 4: +0x253C4 / +0x253C8 read (7, 3) then (5, 7): +0x253C8 takes over +0x253C4's flag at each "Destination
    # Moved", and with +0x253C8 on flag 5 the HUD's marker read 168 m, flag 6's distance, which +0x253C8 named next:
    # +0x253C8 is the last destination). Halo 4 / H2A: not read (None)
    RALLY_DEST = 0x253C4

    def rally_dest(s):
        """The position of Rally's / Rocket Race's live destination object, or None."""
        if not s.RALLY_DEST or type(s) is not LiveReach:
            return None
        o = s.object(struct.unpack("<I", s.g.read(s._vbase() + s.RALLY_DEST, 4))[0])
        return struct.unpack("<3f", s.g.read(o + s.POS_AT, 12)) if o else None

    def respawn_zone(s, index):
        """(x, y, z, yaw) of the respawn zone object the game gives player `index` (RESPAWN_ZONE_AT), or None."""
        base, mx, size = s._array(s.PLAYERS_AT)
        if not 0 <= index < mx:
            return None
        a = s.object(struct.unpack("<I", s.g.read(base + index * size + s.RESPAWN_ZONE_AT, 4))[0])
        head = s.g.read(a, 0x5C) if a else None
        if not head:
            return None
        x, y, z = struct.unpack_from("<3f", head, s.POS_AT)
        fx, fy = struct.unpack_from("<2f", head, 0x50)
        return x, y, z, math.atan2(fy, fx)

    def spawn_point(s, index):
        """(x, y, z, yaw) of the spawn point stored in player `index` (+0x3EC: position, then facing): where the game last
        put him, its own pick for his team (Firefight's death screen, reach.cpp hr_bot_spawn_point). None before his first."""
        base, mx, size = s._array(s.PLAYERS_AT)
        if not 0 <= index < mx:
            return None
        x, y, z, fx, fy, _ = struct.unpack("<6f", s.g.read(base + index * size + 0x3EC, 24))
        if not all(math.isfinite(v) for v in (x, y, z, fx, fy)) or not (x or y or z):
            return None
        return (x, y, z, math.atan2(fy, fx))

    def core(s, players, stand, key="core"):
        """Invasion's core: the weapon object first seen on its stand (a Vec), found as flags are (flags_by_stand),
        once phase 3 puts it there. [{pos, carrier}] (empty before phase 3). key: another object's own tags (Breakpoint's
        phase 2 bomb, "bomb")."""
        weapons, held = s._weapons_held(players)
        # no map-gun skip here: Spire's map places a fuel rod 0.4 u from the core's stand, so the power module's tag
        # counted as a placed gun and was never taken for the core (hung on its stand 0.5 u up; live). Learned only from
        # the object on its stand, never from a third weapon slot: a detached plasma turret is carried there, and the
        # player's turret read as the core carried 36 s into phase 1
        return [dict(pos=f["pos"], carrier=f["carrier"]) for f in flags_by_stand(
            weapons, held, {0: stand}, s.__dict__.setdefault(f"_{key}_tags", set()),
            s.__dict__.setdefault(f"_{key}_team", {}), (), (), anchored=s._attached)]

    def present(s, points, r=0.35, names=None):
        """Which of points (Vec) have an object standing at them (within r, 1 up or down): Invasion's gates are there
        while closed. names: each point's own object name (the gate's piece, as the map's tag names give it): then only
        that object counts there, any object where the name is None or the map's names are not read. Any object
        counted, a danger zone marker 0.3 u from Boneyard's laser field by the drop to the core kept that gate standing
        all phase 3: the attackers' way down went round it, 17,000 rows at its foot and no core run (live
        20261105-033712). Every object's position read once."""
        tags = None
        if names and any(names):
            try:
                from .forge import _tag_names
                tags = _tag_names(s) or None
            except Exception:
                tags = None
        ents, mx, _ = s._array(s.OBJECTS_AT)
        b = s.g.read(ents, mx * 0x18)
        here = set()
        for i in range(mx):
            if not struct.unpack_from("<H", b, i * 0x18)[0]:
                continue
            o = struct.unpack_from("<Q", b, i * 0x18 + 0x10)[0]
            try:
                pos = struct.unpack("<3f", s.g.read(o + s.POS_AT, 12))
            except OSError:
                continue
            name = False                          # (read once, only for an object at a point)
            for k, p in enumerate(points):
                if abs(pos[0] - p.x) < r and abs(pos[1] - p.y) < r and abs(pos[2] - p.z) < 1.0:
                    if tags is not None and names[k]:
                        if name is False:
                            try:
                                t = struct.unpack("<I", s.g.read(o + getattr(s, "TAG_AT", 0), 4))[0] & 0xFFFF
                                name = tags[t].rsplit("\\", 1)[-1] if t < len(tags) else None
                            except OSError:
                                name = None
                        if name != names[k]:
                            continue
                    here.add(k)
        return here

    def king_hill(s):
        """(centre, radius) of the active hill, or None."""
        if s.REACH_HILL is None:
            return None
        base = s._vbase()
        o = next((x for x in (s.object(struct.unpack("<I", s.g.read(base + at, 4))[0])
                              for at in (s.REACH_HILL if isinstance(s.REACH_HILL, tuple) else (s.REACH_HILL,))) if x), None)
        if not o:
            return None
        pos = struct.unpack("<3f", s.g.read(o + s.POS_AT, 12))
        spot = min(getattr(s, "_hill_spots", []), key=lambda h: sum((h[k] - pos[k]) ** 2 for k in range(3)), default=None)
        if spot is not None and sum((spot[k] - pos[k]) ** 2 for k in range(3)) < 1.0:
            pos = spot                         # the map's own hill (Halo 4's beacon stands 0.32 u above it)
        # its size: the map's hill of that spot (the scenario's boundary, tools/reach_export.py): a cylinder's
        # radius, half a box's short side; else HILL_R
        best = min(getattr(s, "_hill_bounds", []), key=lambda h: sum((h[0][k] - pos[k]) ** 2 for k in range(3)),
                   default=None)
        r = best[1] if best is not None and sum((best[0][k] - pos[k]) ** 2 for k in range(3)) < 1.0 else s.HILL_R
        return pos, r

    def players(s):
        base, mx, size = s._array(0x18)
        out = []
        for i in range(mx):
            b = s.g.read(base + i * size, 0x130)
            if not struct.unpack_from("<H", b, 0)[0]:
                continue
            unit = struct.unpack_from("<I", b, 0x28)[0]
            name = b[0xB0:0xD0].decode("utf-16-le", "replace").partition(chr(0))[0]
            o = s.object(unit)
            # the joined configuration sits at +0x90 (name +0x20, team byte +0x98)
            d = dict(index=i, name=name, team=b[0x128], unit=unit, pos=None, fwd=None,
                     aim=None, vehicle=-1, health=0.0, shield=0.0, weapon="none")
            if o:
                u = s.g.read(o, 0x400)
                slots = struct.unpack_from("<4I", u, 0x350)
                d["weapons"] = [s.weapon_name(w) for w in slots if w != NONE]
                parent = struct.unpack_from("<I", u, 0x14)[0]
                d["vehicle"] = parent if parent != NONE else -1
                sh, hp = s._vitals(unit, *struct.unpack_from("<2f", u, 0x124))     # damage taken, 1.0 = gone
                d.update(pos=struct.unpack_from("<3f", u, 0x44), fwd=struct.unpack_from("<3f", u, 0x50),
                         aim=struct.unpack_from("<3f", u, 0x208), shield=sh, health=hp)
                # in hand: unit +0x34A, the index of the current slot (the melee and the game's input read it,
                # ghidra/hr_melee_event.c); slot 0 was assumed, which hid every switch
                held = slots[u[0x34A]] if u[0x34A] < 4 else NONE
                d["weapon"] = s.weapon_name(held) if held != NONE else (d["weapons"][0] if d["weapons"] else "none")
                rounds = [s.rounds(w) for w in slots if w != NONE]
                d["ammo"], d["mag"] = [a for a, _ in rounds], [g for _, g in rounds]
                d["equipment"], d["ability_on"] = s.ability(struct.unpack_from("<I", u, s.ABILITY_AT)[0])
            out.append(d)
        for d in out:                # enemies on his motion tracker (tracker() per game)
            d["tracker"] = s.tracker(base + d["index"] * size)
        return out

    def firefight(s):
        # (the DLC Firefight maps lie elsewhere: levels\dlc\ff_unearthed\ff_unearthed and levels\dlc\cex_ff_halo\cex_ff_halo
        # (Installation 04), live Nov 6: read as not Firefight, so no bots joined there, GitHub #12. The game mode byte reads
        # 2, multiplayer, as in PvP; no PvP map's name has "ff_")
        path = s.g.read(s.g.base + s.CACHE_HEADER + 0xE0, 128).partition(bytes(1))[0]
        return path.startswith(b"levels\\firefight\\") or b"ff_" in path.replace(b"/", b"\\").rsplit(b"\\", 1)[-1]

    # unit's side byte: Spartan players 1, Elite players 7, the Covenant AI 3 (live Beachhead Nov 6, Classic and Versus)
    UNIT_TEAM = 0x1CA

    AI_DEAD_BIT, AI_DEAD_HEALTH = True, True     # the two dead-body tests of ai_units (False: off, if one reads a live unit dead)

    def ai_body_dead(s, addr, u):
        """A dead AI body: the game's destroyed bit (DEAD_AT, bit 2: object_get_health answers 0 when it is set), or the
        health read through the object's health block (HEALTH_BLOCK, as Live.vehicle_left) at or under 0. Not read live
        on a body yet (as DEAD_AT's note says): the two switches above turn either test off."""
        if s.AI_DEAD_BIT and u[s.DEAD_AT] & 4:
            return True
        if s.AI_DEAD_HEALTH:
            try:
                size, at = struct.unpack("<hH", u[s.HEALTH_BLOCK:s.HEALTH_BLOCK + 4])
                if size >= 0x18 and struct.unpack("<f", s.g.read(addr + at + 0x10, 4))[0] <= 0.0:
                    return True
            except Exception:
                pass
        return False

    def ai_units(s, players):
        """Firefight: the AI's bipeds as player-like dicts (index 1000 + object slot): human-side AI (UNIT_TEAM 1, 2) on
        the Spartan players' team, the rest on the Elite players' (Versus, or an Elite-side bot: GitHub #12 "bots on
        the elites side will kill AI allies"), -1 with no one on that side.
        Not a player's unit (+0x1D0 player), not riding (a dropship's passengers sit at
        positions relative to it), not dead (body damage 1.0)."""
        punits = {p["unit"]: p["team"] for p in players}
        ents, mx, _ = s._array(0x10)
        b = s.g.read(ents, mx * 0x18)
        out, side = [], {}
        for i in range(mx):
            salt = struct.unpack_from("<H", b, i * 0x18)[0]
            if not salt or b[i * 0x18 + 4] != 0:
                continue
            addr = struct.unpack_from("<Q", b, i * 0x18 + 0x10)[0]
            u = s.g.read(addr, 0x214)
            if ((salt << 16) | i) in punits:
                side.setdefault(u[s.UNIT_TEAM], punits[(salt << 16) | i])
                continue
            shield_dmg, body_dmg = struct.unpack_from("<2f", u, 0x124)
            if struct.unpack_from("<I", u, 0x14)[0] != NONE or body_dmg >= 1.0:
                continue
            if s.ai_body_dead(addr, u):      # a dead body lies about for a while: its body damage can read back low, and
                continue                     # the bots shot at it (the owner, Nov 10: "shooting at dead bodies")
            if struct.unpack_from("<I", u, 0x1D0)[0] != NONE:   # a player's (a bot's old body)
                continue
            out.append(dict(index=1000 + i, name="ai", team=u[s.UNIT_TEAM], human=u[s.UNIT_TEAM] in (1, 2), unit=(salt << 16) | i,
                            pos=struct.unpack_from("<3f", u, 0x44), fwd=struct.unpack_from("<3f", u, 0x50),
                            aim=struct.unpack_from("<3f", u, 0x208), vehicle=-1, weapon="unknown", weapons=[],
                            **dict(zip(("shield", "health"), s._vitals((salt << 16) | i, shield_dmg, body_dmg, player=False)))))
        for d in out:                # (the Covenant, any of theirs, fight beside the Elite players)
            d["team"] = side.get(1 if d["team"] in (1, 2) else 7, -1)
        return out

    SEAT_AT = 0x336      # a unit's seat in the vehicle it rides (the game's own enter/exit test reads it: reach.cpp)

    def ai_riders(s, players):
        """Firefight: the AI sitting in vehicles, which ai_units leaves out (a rider's position is relative to its
        vehicle): [{index, unit, vehicle (the object it is parented to: the vehicle, or a turret on it), seat, human,
        team (as ai_units: the team of the players on its side, -1 with none), aim}].
        Without these a Ghost, Wraith or Warthog the Covenant drives reads as EMPTY: bots queued up to drive it, and
        nobody hijacked it. Same tests as ai_units for a dead body or a player's unit; the seat byte is read apart (it
        lies past the 0x214 bytes read per unit) and is None where the read fails."""
        punits = {p["unit"]: p["team"] for p in players}
        ents, mx, _ = s._array(0x10)
        b = s.g.read(ents, mx * 0x18)
        out, side = [], {}
        for i in range(mx):
            salt = struct.unpack_from("<H", b, i * 0x18)[0]
            if not salt or b[i * 0x18 + 4] != 0:
                continue
            addr = struct.unpack_from("<Q", b, i * 0x18 + 0x10)[0]
            u = s.g.read(addr, 0x214)
            handle = (salt << 16) | i
            if handle in punits:
                side.setdefault(u[s.UNIT_TEAM], punits[handle])
                continue
            parent = struct.unpack_from("<I", u, 0x14)[0]
            if parent == NONE:
                continue
            if (parent & 0xFFFF) >= mx or b[(parent & 0xFFFF) * 0x18 + 4] != 1:   # (parented to a vehicle object only)
                continue
            if struct.unpack_from("<2f", u, 0x124)[1] >= 1.0 or s.ai_body_dead(addr, u):
                continue
            if struct.unpack_from("<I", u, 0x1D0)[0] != NONE:
                continue
            try:
                seat = struct.unpack("<b", s.g.read(addr + s.SEAT_AT, 1))[0]
            except Exception:
                seat = None
            out.append(dict(index=1000 + i, unit=handle, vehicle=parent, seat=seat, sidebyte=u[s.UNIT_TEAM],
                            human=u[s.UNIT_TEAM] in (1, 2), aim=struct.unpack_from("<3f", u, 0x208)))
        for d in out:
            d["team"] = side.get(1 if d["sidebyte"] in (1, 2) else 7, -1)
        return out

class LiveODST(LiveH3):
    """Halo 3: ODST Firefight (halo3odst.dll, MCC 1.3528; docs/NEXT-01). The Halo 3 layout with
    other offsets: TLS +0x10 players (header +0x20 max, +0x24 element size 0x418, +0x48 data), +0x20
    objects (entries +3 type, +0x10 address). Player +0x28 unit, +0x58 name. Unit +0x10 parent,
    +0x50 position, +0x5C forward, +0x198 aim, +0xE8 health / +0xEC shield (fractions), +0x274
    weapon set / +0x276 primary index, +0x27C weapon handles. Cache header 'daeh' at +0x20EF068."""
    UNIT_ZOOM = None      # the unit's desired zoom byte (not decoded)
    VEH_VITAL = (0xE8, False)   # (Live.vehicle_left: the unit health field players() reads)
    DEAD_AT = 0x104             # (Live.vehicle_dead; ghidra/odst_object_get_health.c)
    MAG_NAMES = {}        # (Halo 3's magazine sizes are not this game's)
    AMMO_AT = None        # rounds not decoded for this game (LiveH2.rounds reads unknown)
    ground_weapons = Live.ground_weapons   # unchanged (CE's offsets) until this game's are checked live
    CACHE_HEADER = 0x20EF068
    CHAIN_ATTACHED = False     # (ODST's object orientation not checked: LiveH3)

    def match_state(s, players=None):
        return None            # Firefight: no team score race

    def flags(s, players, stands=None):
        return []              # Firefight: no flags (LiveH3.flags is Halo 3's object layout)

    def __init__(s):
        import tls
        s.g = Game("halo3odst.dll")
        s._tls = tls
        s._blk = None
        s._names = {}
        s._placements = []
        s._find()

    def _array(s, off):
        h = s._hdr(off)
        mx, size = struct.unpack("<ii", s.g.read(h + 0x20, 8))
        return s.g.u64(h + 0x48), mx, size

    def object(s, handle):
        if handle == NONE:
            return None
        ents, mx, _ = s._array(0x20)
        if (handle & 0xFFFF) >= mx:
            return None
        e = s.g.read(ents + (handle & 0xFFFF) * 0x18, 0x18)
        return struct.unpack_from("<Q", e, 0x10)[0] if struct.unpack_from("<H", e, 0)[0] == handle >> 16 else None

    def objects(s, types=None):
        ents, mx, _ = s._array(0x20)
        b = s.g.read(ents, mx * 0x18)
        out = []
        for i in range(mx):
            salt, typ = struct.unpack_from("<H", b, i * 0x18)[0], b[i * 0x18 + 3]
            if not salt or (types is not None and typ not in types):
                continue
            o = struct.unpack_from("<Q", b, i * 0x18 + 0x10)[0]
            head = s.g.read(o, 0x5C)
            tag, parent = struct.unpack_from("<I", head, 0)[0], struct.unpack_from("<I", head, 0x10)[0]
            pos = struct.unpack_from("<3f", head, 0x50)
            if typ == 2 and parent == NONE and tag not in s._names and not s._was_held((salt << 16) | i):
                s._learn(tag, pos)
            if typ == 1 and parent == NONE and tag not in s._names:
                s._learn_vehicle(tag, pos)
            out.append(((salt << 16) | i, typ, s._names.get(tag, "unknown"), pos))
        return out

    def map_name(s):
        path = s.g.read(s.g.base + s.CACHE_HEADER + 0xE0, 256).partition(bytes(1))[0].decode("ascii", "replace")
        return path.rsplit("\\", 1)[-1] or None

    def firefight(s):
        return True                 # ODST has no PvP in MCC: every match is Firefight

    def variant(s):
        return dict(engine=0, teams=True, score=0, name="firefight")

    def _unit(s, u, d):
        slots = struct.unpack_from("<4I", u, 0x27C)
        cur = struct.unpack_from("<b", u, 0x276)[0]
        d["weapons"] = [s.weapon_name(w) for w in slots if w != NONE]
        d["weapon"] = s.weapon_name(slots[cur]) if 0 <= cur < 4 else "none"
        parent = struct.unpack_from("<I", u, 0x10)[0]
        d["vehicle"] = parent if parent != NONE else -1
        d.update(pos=struct.unpack_from("<3f", u, 0x50), fwd=struct.unpack_from("<3f", u, 0x5C),
                 aim=struct.unpack_from("<3f", u, 0x198),
                 health=struct.unpack_from("<f", u, 0xE8)[0], shield=struct.unpack_from("<f", u, 0xEC)[0])

    def players(s):
        base, mx, size = s._array(0x10)
        out = []
        for i in range(mx):
            b = s.g.read(base + i * size, 0x80)
            if not struct.unpack_from("<H", b, 0)[0]:
                continue
            unit = struct.unpack_from("<I", b, 0x28)[0]
            d = dict(index=i, name=b[0x58:0x78].decode("utf-16-le", "replace").partition(chr(0))[0], team=0, unit=unit,
                     pos=None, fwd=None, aim=None, vehicle=-1, health=0.0, shield=0.0, weapon="none")
            o = s.object(unit)
            if o:
                s._unit(s.g.read(o, 0x290), d)
            out.append(d)
        return out

    def ai_units(s, players):
        """The Covenant: bipeds that are no player's unit, not riding (dropship passengers),
        alive. Index 1000 + object slot, team -1."""
        punits = {p["unit"] for p in players}
        ents, mx, _ = s._array(0x20)
        b = s.g.read(ents, mx * 0x18)
        out = []
        for i in range(mx):
            salt = struct.unpack_from("<H", b, i * 0x18)[0]
            if not salt or b[i * 0x18 + 3] != 0 or ((salt << 16) | i) in punits:
                continue
            d = dict(index=1000 + i, name="ai", team=-1, unit=(salt << 16) | i, vehicle=-1)
            s._unit(s.g.read(struct.unpack_from("<Q", b, i * 0x18 + 0x10)[0], 0x290), d)
            if d["vehicle"] != -1 or d["health"] <= 0.0:
                continue
            d["vehicle"] = -1
            out.append(d)
        return out


class LiveH4F(LiveReach):
    """Halo 2 Anniversary (groundhog.dll) and Halo 4 (halo4.dll), MCC 1.3528 (docs/NEXT-01). The Reach layout with other
    offsets: TLS +0x8 players (header +0x20 element size, +0x28 '@t@d', +0x2C max, +0x50 data), +0x18 objects (entries
    +4 type, +0x10 address). Player +0x2C unit, +0xC0 name, +0x1E0 team (configuration +0x140). Unit +0x24 parent (what
    it rides), +0x40 position (verified by a jump), +0x4E0 aim with pitch, +0x638 weapon indexes, +0x640 weapon handles,
    +0x140 max body / shield (45 / 70); damage taken at +0x158 / +0x15C (Reach's layout; live traces: shields drain
    before health, 0..1)."""
    PATHS = {"halo4.dll": 0x2A30288, "groundhog.dll": 0x1457018}     # the running map's path ('levels\multi\<map>\<map>')
    TRACKER_AT = 0x758         # (LiveH3.tracker; both games: Unchanged 3, Off 1 seen live)
    # (Live.vehicle_dead / vehicle_left: halo4.dll 0x180683124 and groundhog.dll 0x180683e80, object_get_health)
    DEAD_AT, HEALTH_BLOCK = 0x16C, 0x1D0
    AMMO_AT = None              # (rounds not decoded here: Reach's +0x2C6 / +0x2CA are not these games')

    # objects for LiveH3.flags / balls / objective_items (Adrift): TLS +0x18, entry +4 type (2 weapon), object +8 tag,
    # +0x24 parent (a held gun's: the unit; a map gun's: the weapon pad it rests on), +0x40 position, absolute
    # attached or not; unit +0x640 the 4 weapon handles
    OBJECTS_AT, TYPE_AT, POS_AT, SLOTS_AT, PARENT_AT, TAG_AT = 0x18, 4, 0x40, 0x640, 0x24, 8
    ATTACHED_RELATIVE = False
    # the flag in hand is in no weapon slot: its parent is the carrier's body (Adrift CTF: a bot took it twice, unseen,
    # and stood 20 s on the stand pressing Use)
    PARENT_CARRIES = True
    # the objectives by the map's tag names (each tag id's low 16 bits index its name exactly, live Adrift;
    # tools/reach_tag_names.py caches every map's): Halo 4 new_flag / storm_skull / storm_blam_bomb, H2A h2a_flag /
    # h2a_oddball / h2a_assault_bomb
    OBJECTIVE_NAMES = {"_flag_tags": ("new_flag", "flag", "h2a_flag"), "_ball_tags": ("storm_skull", "h2a_oddball"),
                       ("_obj_tags", "assault"): ("storm_blam_bomb", "h2a_assault_bomb")}
    # the game variant: the block at TLS +0x40 (dumps of Infinity Rumble, Infinity Slayer, CTF, Oddball and King on
    # Adrift, logs/tmp/h4v_dump.py): +0x7C engine icon (Reach's numbering: CTF 0, Slayer 1, Oddball 2, King 3), +0xCC
    # name (UTF-16), +0x2FC flags bit 0 teams (Rumble 0xEE, the others 0xEF / 0xE7), +0x2FF minutes (MATCH)
    VARIANT_SLOT, ENGINE_ICON, VARIANT_NAME, TEAMS_FLAGS = 0x40, 0x7C, 0xCC, 0x2FC

    # Halo 2 Anniversary (groundhog.dll) on Lockdown Team Slayer 30 / 9 min, against the HUD:
    # (TLS slot, offset into the block it points to)
    # score: int32, one 0x1C-byte record per team (H2A red 0->5->7, blue 0->4->9; H4 Infinity Slayer
    #   red 60 / blue 50 on Adrift). ticks: int32 game ticks, 60 a second, in every player record (the
    #   max is used), ahead of the HUD by the deploy countdown (H2A ~10 s, H4 ~5 s); a cell at
    #   tls+0x18:+0x2FD6C matched one H2A session and froze in the next (tools/find_clock.py).
    #   limits: (slot, score to win int16, minutes byte), from two lobbies (tools/limit_candidates.py)
    MATCH = {"groundhog.dll": dict(score=(0x8, 0x159AC), ticks=0xA94, limits=(0x40, 0x3EECE, 0x2FF)),   # 30/9, 50/12
             "halo4.dll": dict(score=(0x8, 0x158EC), ticks=0xA8C, limits=(0x40, 0x3EE0E, 0x2FF))}   # 600/12, 500/10

    def match_state(s, players=None):
        """H2A / H4 team scores, score to win and time left."""
        m = s.MATCH.get(s._dll)
        if not m:
            return None
        if s._blk is None:
            s.players()
        slot, off = m["score"]
        p = s.g.u64(s._blk + slot) if s._blk else None
        if not p:                      # the block went away (a map load): find it again next time
            s._blk = None
            return None
        b = s.g.read(p + off, 0x20)
        score = {0: struct.unpack_from("<i", b, 0)[0], 1: struct.unpack_from("<i", b, 0x1C)[0]}
        limit, left = 0, None
        if m["limits"]:
            lslot, loff, moff = m["limits"]
            base = s.g.u64(s._blk + lslot)
            limit = struct.unpack("<h", s.g.read(base + loff, 2))[0]
            minutes = s.g.read(base + moff, 1)[0]
            pbase, mx, size = s._array(0x8)
            ticks = max(struct.unpack("<i", s.g.read(pbase + i * size + m["ticks"], 4) or b"\0" * 4)[0] for i in range(mx))
            left = max(0.0, minutes * 60.0 - ticks / 60.0) if minutes else None
        return dict(score=score, limit=max(0, limit), time_left=left)

    PLAYERS_AT = 0x8
    # armor abilities (static: not yet checked live): unit +0x664 (unit_has_equipment,
    # ghidra/h4_has_equipment.c, h2a_has_equipment.c), object tag at +8; the definition's "abilities"
    # block (count, address; Halo 4 +0x2F4, H2A +0x2FC), elements 0x23C bytes, type block k at element
    # +0x48 + 12 k (ghidra/h4_type_accessor.c, h2a_type_accessor.c); running while the ability's state
    # at object +0x494 is set and +0x16C bit 2 is clear (ghidra/h4_current_ability.c)
    ABILITY_AT, TYPE_COUNT = 0x664, 36
    TYPE_ORDER = ((34, "hardlight_shield"), (26, "autosentry"), (27, "promethean_vision"), (20, "hologram"),
                  (19, "jetpack"), (22, "thruster"), (24, "sprint"), (4, "active_camo"), (1, "drop_shield"),
                  (0, "powerup"), (8, "regen_field"))

    def _ability_tag(s, o):
        return struct.unpack("<H", s.g.read(o + 8, 2))[0]

    def _type_counts(s, d):
        n, v = struct.unpack("<iI", s.g.read(d + s.ABILITIES, 8))
        if n < 1:
            return []
        b = s.g.read(s._unpack(v) + 0x48, 12 * s.TYPE_COUNT)
        return [struct.unpack_from("<i", b, 12 * k)[0] for k in range(s.TYPE_COUNT)]

    def _ability_on(s, o):
        return (struct.unpack("<i", s.g.read(o + 0x494, 4))[0] != -1
                and not struct.unpack("<I", s.g.read(o + 0x16C, 4))[0] & 4)

    def __init__(s, dll="halo4.dll"):
        import tls
        s.g = Game(dll)
        s._dll = dll
        # the player_loadout_request handler writes the slot to two player fields: Halo 4 +0x944 and
        # +0x948 (FUN_1801c67a0, ghidra/h4_loadout_set.c), H2A +0x94C and +0x950 (FUN_1801c5be0,
        # ghidra/h2a_loadout_set.c)
        s.LOADOUT = {"halo4.dll": 0x944, "groundhog.dll": 0x94C}.get(dll, 0)
        s.TAG_RVAS = {"halo4.dll": (0x107C0B0, 0x496A180), "groundhog.dll": (0x106DFB0, 0x33DCA80)}[dll]
        s.ABILITIES = {"halo4.dll": 0x2F4, "groundhog.dll": 0x2FC}[dll]
        # the active King hill: its beacon's handle in the variant block (Halo 4 King on Adrift: +0x3EE3C named a new
        # beacon every 60 s as the hill moved, but nothing for the first ~20 s of a match while a team scored; +0x408D8
        # holds the opening hill from the start: the fallback). H2A +0x3EF04 (Lockdown Team Crazy King: it named hill 3
        # while red alone stood on hill 3 and scored, then hill 0 the second blue began scoring there)
        s.REACH_HILL = {"halo4.dll": (0x3EE3C, 0x408D8), "groundhog.dll": 0x3EF04}.get(dll)
        s.TAG_GAME = {"halo4.dll": "h4", "groundhog.dll": "h2a"}[dll]     # (the map's tag names: brain/forge.py)
        # a vehicle object's forward vector, its up vector the next 12 bytes (Halo 4 Exile: unit length and square in
        # all 11 vehicles, parked up z 0.96-1.00, a Warthog on its side -0.07; POS_AT + 12 is a length-20 vector, not
        # the facing). H2A not checked yet: none (no up vector, facing as before)
        s.ORIENT_AT = {"halo4.dll": 0x70}.get(dll)
        s._tls = tls
        s._blk = None
        s._names = {}
        s._placements = []
        s._last_dmg, s._bare, s._bare_players = {}, set(), set()
        s._find()

    def _find(s):
        for _, b in s._tls.blocks(s.g):
            h = s.g.u64(b + 0x8)
            if h and s.g.read(h + 0x28, 4) == b"@t@d":
                s._blk = b
                return
        raise RuntimeError("no game thread for " + s._dll)

    def map_name(s):
        off = s.PATHS.get(s._dll)
        if off is None:
            return None
        path = s.g.read(s.g.base + off, 128).partition(bytes(1))[0].decode("ascii", "replace")
        return path.rsplit("\\", 1)[-1] or None

    def firefight(s):
        return False

    def objects(s, types=None):
        ents, mx, _ = s._array(0x18)
        b = s.g.read(ents, mx * 0x18)
        out = []
        for i in range(mx):
            salt, typ = struct.unpack_from("<H", b, i * 0x18)[0], b[i * 0x18 + 4]
            if not salt or (types is not None and typ not in types):
                continue
            o = struct.unpack_from("<Q", b, i * 0x18 + 0x10)[0]
            head = s.g.read(o, 0x50)
            tag = struct.unpack_from("<I", head, 0x8)[0]   # +0 is the same for every object here
            pos = struct.unpack_from("<3f", head, 0x40)
            h = (salt << 16) | i
            if typ == 2 and h not in getattr(s, "_held", ()) and not s._was_held(h) and tag not in s._names:
                s._learn(tag, pos)       # +0x14 is no parent here (0x3040 on foot): skip held guns instead
            if typ == 1 and tag not in s._names and struct.unpack_from("<I", head, s.PARENT_AT)[0] == NONE:
                n = s._vehicle_name(tag)  # (a gun riding on a vehicle has it as its parent: left as it was)
                if n:
                    s._names[tag] = n
            out.append(((salt << 16) | i, typ, s._names.get(tag, "unknown"), pos))
        return out

    def _vehicle_name(s, tag):
        """A vehicle's file name at its tag's own index (the names are exact here, and its folder can differ from it:
        h2a_mongoose\\h2a_gungoose; every vehicle read "unknown" before, so no bot planned a ride: Bloodline)."""
        try:
            from .forge import _tag_names
            names = _tag_names(s)
        except Exception:
            return None
        k = tag & 0xFFFF
        p = names[k].split("\\") if k < len(names) else []
        return p[-1] if "vehicles" in p[:-1] else None

    def weapon_name(s, weapon_handle):
        o = s.object(weapon_handle)
        if not o:
            return "none"
        tag = struct.unpack("<I", s.g.read(o + 0x8, 4))[0]                            # tag at +0x8 (H2A)
        s.__dict__.setdefault("_ever_held", set()).add(weapon_handle)   # held: never names a spawn (_was_held)
        if tag not in s._names:
            s._learn(tag, None)
        name = s._names.get(tag, "unknown")
        return WEAPON_ALIAS.get(name, name)

    def _learn(s, tag, pos):
        """A gun's name from the map's own tag names (exact here: tools/reach_tag_names.py), as the export names the
        map's placements (brain_name). Learning from the nearest placement named a rifle in a bot's hands
        "ord_grenade_doubleplasma" (Adrift CTF); equipment (not under weapons) is still learned that way."""
        own = s._own_name(tag, "weapons")
        if own:
            s._names[tag] = brain_name(own)
            s.zombie_seen = s.__dict__.get("zombie_seen", False) or own in ZOMBIE_TAGS   # (modes.mode_of)
        elif pos is not None:
            LiveReach._learn(s, tag, pos)

    def object(s, handle):
        if handle == NONE:
            return None
        ents, mx, _ = s._array(0x18)
        if (handle & 0xFFFF) >= mx:
            return None
        e = s.g.read(ents + (handle & 0xFFFF) * 0x18, 0x18)
        return struct.unpack_from("<Q", e, 0x10)[0] if struct.unpack_from("<H", e, 0)[0] == handle >> 16 else None

    def players(s):
        base, mx, size = s._array(0x8)
        out, held = [], set()
        for i in range(mx):
            b = s.g.read(base + i * size, 0x1E4)
            if not struct.unpack_from("<H", b, 0)[0]:
                continue
            unit = struct.unpack_from("<I", b, 0x2C)[0]
            d = dict(index=i, name=b[0xC0:0xE0].decode("utf-16-le", "replace").partition(chr(0))[0], team=b[0x1E0],
                     unit=unit, pos=None, fwd=None, aim=None, vehicle=-1, health=0.0, shield=0.0, weapon="none")
            o = s.object(unit)
            if o:
                u = s.g.read(o, 0x668)
                slots = struct.unpack_from("<4I", u, 0x640)
                held.update(w for w in slots if w != NONE)
                cur = u[0x63A] if u[0x63A] < 4 else 0
                d["weapons"] = [s.weapon_name(w) for w in slots if w != NONE]
                d["weapon"] = s.weapon_name(slots[cur]) if slots[cur] != NONE else "none"
                # what it rides: +0x24 (the parent, as for a held gun), its seat at +0x2C (-1 on foot). +0x14 is flags
                # (0x3040 on foot, 0x2040 seated): read as the parent, nobody ever rode here (Bloodline: a bot in a
                # Ghost read on foot, kept pressing Use and got out again, 210 boardings in two minutes)
                parent = struct.unpack_from("<I", u, s.PARENT_AT)[0]
                d["vehicle"] = parent if parent != NONE and s.object(parent) else -1
                sh, hp = s._vitals(unit, *struct.unpack_from("<2f", u, 0x158))
                d.update(pos=struct.unpack_from("<3f", u, 0x40), aim=struct.unpack_from("<3f", u, 0x4E0), shield=sh, health=hp)
                d["equipment"], d["ability_on"] = s.ability(struct.unpack_from("<I", u, s.ABILITY_AT)[0])
            out.append(d)
        s._held = held
        for d in out:                # enemies on his motion tracker (tracker() per game)
            d["tracker"] = s.tracker(base + d["index"] * size)
        return out


if __name__ == "__main__":
    cls = LiveH4F if "--h4" in sys.argv else LiveODST if "--odst" in sys.argv else LiveReach if "--reach" in sys.argv else LiveH3 if "--h3" in sys.argv else LiveH2 if "--h2" in sys.argv else Live
    for d in cls().players():
        print(d)
