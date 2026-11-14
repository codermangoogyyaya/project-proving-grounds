"""One bot: perception with reaction delay, human aim, decisions, Halo combat habits."""
import math
import random
from dataclasses import dataclass

from .core import Vec, Action, dist, angles_to, wrap, eye, CROUCH_HEAD, CROUCH_CHEST
from .equipment import Kit, game_of
from .profiles import HANDS
from . import profiles
from .terrain import MAX_DROP, MAX_RISE, ROUTE_JUMP, ROUTE_CROUCH, EYE, CHEST
from . import vehicles
from . import dom_base

FOV_HALF = math.radians(35)      # CE default 70 degree horizontal FOV
PERIPHERAL_HALF = math.radians(55)  # noticed later, at the edge of attention
SIGHT_RANGE = 80.0
RADAR_RANGE = 25.0               # CE motion tracker, world units
# (lo, hi) world units (1 = 10 ft) the weapon wins at: past hi the bot closes in, under lo it backs off (and
# switches if it can). Guide 01 bands: Short under 4, Medium 4-10, Long 10-20. Automatics and close-quarters
# weapons have no lo: they want the man close (guide 03: "spray plus melee at Short band"); precision rifles
# back off only at arm's length; a shotgun, sword or hammer must get within its reach to do anything
WEAPON_RANGE = {"pistol": (1.5, 30.0), "assault_rifle": (0.0, 5.0), "sniper": (8.0, 80.0),
                "rockets": (2.5, 35.0), "fuelrod": (2.5, 30.0), "plasma_pistol": (0.0, 10.0),
                "plasma_rifle": (0.0, 6.0), "needler": (0.0, 8.0), "shotgun": (0.0, 1.8),
                "flamethrower": (0.0, 2.0),
                # Halo 2
                "battle_rifle": (1.5, 35.0), "smg": (0.0, 4.0), "magnum": (0.0, 15.0), "sword": (0.0, 1.6),
                "brute_shot": (2.0, 25.0), "covenant_carbine": (1.5, 35.0), "beam_rifle": (8.0, 80.0),
                "brute_plasma_rifle": (0.0, 5.0), "sentinel_beam": (0.0, 5.0),
                # Halo 3
                "spartan_laser": (3.0, 70.0), "missile_pod": (2.5, 30.0), "excavator": (0.0, 1.2),
                "gravity_hammer": (0.0, 1.0), "spike_rifle": (0.0, 4.0),
                # Halo: Reach
                "dmr": (1.5, 40.0), "needle_rifle": (1.5, 35.0), "focus_rifle": (6.0, 60.0),
                "grenade_launcher": (2.0, 30.0), "plasma_launcher": (2.5, 35.0), "concussion_rifle": (1.5, 25.0),
                "plasma_repeater": (0.0, 6.0),
                # Halo 4 (H2A uses the Halo 2 names)
                "lightrifle": (1.5, 45.0), "binary_rifle": (8.0, 80.0), "railgun": (2.5, 40.0),
                "incineration_cannon": (3.0, 25.0), "scattershot": (0.0, 2.0), "lmg": (0.0, 7.0),
                "sticky_detonator": (2.0, 25.0), "boltshot": (0.0, 8.0)}
# Range classes: how good each gun is in each band, 0..1. Bands in world units (1 = 10 ft, guide 01): Short under 4,
# Medium 4-10, Long 10-20, Extreme past 20. A loadout is worth the best gun it has in each band (loadout_value);
# spikers, needlers and plasma pistols add nothing to an AR, a BR adds Medium and Long. Guns not listed: from
# WEAPON_RANGE
BAND_EDGES = (4.0, 10.0, 20.0)
BAND_WEIGHT = (0.3, 0.35, 0.25, 0.1)   # ponytail: one weighting for every map; per map from its fight distances if needed
BANDS = {
    # Halo CE
    "pistol": (0.8, 1.0, 1.0, 0.6), "assault_rifle": (1.0, 0.6, 0.1, 0.0), "sniper": (0.2, 0.6, 1.0, 1.0),
    "rockets": (0.4, 1.0, 0.7, 0.3), "fuelrod": (0.4, 0.9, 0.6, 0.2), "plasma_pistol": (0.7, 0.5, 0.1, 0.0),
    "plasma_rifle": (0.9, 0.5, 0.1, 0.0), "needler": (0.7, 0.6, 0.1, 0.0), "shotgun": (1.0, 0.2, 0.0, 0.0),
    "flamethrower": (0.9, 0.0, 0.0, 0.0),
    # Halo 2
    "battle_rifle": (0.7, 1.0, 1.0, 0.4), "smg": (0.9, 0.4, 0.05, 0.0), "magnum": (0.5, 0.5, 0.2, 0.0),
    "sword": (1.0, 0.3, 0.0, 0.0), "brute_shot": (0.5, 0.9, 0.4, 0.1), "covenant_carbine": (0.6, 1.0, 1.0, 0.5),
    "beam_rifle": (0.2, 0.6, 1.0, 1.0), "brute_plasma_rifle": (0.9, 0.4, 0.1, 0.0), "sentinel_beam": (0.8, 0.5, 0.1, 0.0),
    # Halo 3
    "spartan_laser": (0.4, 1.0, 1.0, 1.0), "missile_pod": (0.3, 0.8, 0.6, 0.3), "excavator": (1.0, 0.2, 0.0, 0.0),
    "gravity_hammer": (1.0, 0.2, 0.0, 0.0), "spike_rifle": (0.8, 0.4, 0.05, 0.0),
    # Halo: Reach
    "dmr": (0.6, 1.0, 1.0, 0.7), "needle_rifle": (0.6, 1.0, 0.9, 0.5), "focus_rifle": (0.1, 0.8, 1.0, 0.8),
    "grenade_launcher": (0.4, 0.9, 0.4, 0.1), "plasma_launcher": (0.3, 0.9, 0.7, 0.3), "concussion_rifle": (0.6, 0.8, 0.3, 0.05),
    "plasma_repeater": (0.9, 0.7, 0.2, 0.0),
    # Halo 4
    "lightrifle": (0.6, 1.0, 1.0, 0.7), "binary_rifle": (0.2, 0.6, 1.0, 1.0), "railgun": (0.6, 1.0, 0.7, 0.3),
    "incineration_cannon": (0.6, 1.0, 0.5, 0.1), "scattershot": (1.0, 0.2, 0.0, 0.0), "lmg": (0.9, 0.7, 0.3, 0.0),
    "sticky_detonator": (0.4, 0.9, 0.5, 0.1), "boltshot": (0.8, 0.4, 0.05, 0.0)}


# the same gun name, another game's gun: Reach's and Halo 4's magnum kills in 5-6 shots (guide 03), Halo 2 / 3's does not
BANDS_GAME = {"reach": {"magnum": (0.8, 0.8, 0.4, 0.1)}, "h4": {"magnum": (0.8, 0.8, 0.4, 0.1)}}


def band(d):
    """0 Short, 1 Medium, 2 Long, 3 Extreme."""
    return sum(d >= e for e in BAND_EDGES)


def proficiency(w, d=None, game=None):
    """A gun's band row, or its proficiency at distance d. Unlisted guns: 1 in the bands WEAPON_RANGE covers, else 0.2."""
    row = BANDS_GAME.get(game, {}).get(w) or BANDS.get(w)
    if row is None:
        lo, hi = WEAPON_RANGE.get(w, (5.0, 25.0))
        mids = (2.0, 7.0, 15.0, 30.0)
        row = tuple(1.0 if lo <= m <= hi else 0.2 for m in mids)
    return row if d is None else row[band(d)]


def loadout_value(guns, rank, game=None):
    """What a set of guns is worth: each band's best gun, weighted, plus a power weapon's pull (rank above 3.5: the
    sniper, sword, laser, rockets), rank(w) the game's own ranking. A power weapon covers its bands at half: it runs
    dry in a few shots, so the BR stays beside the laser and the AR goes."""
    guns = [w for w in guns if w not in ("none", "unknown", "flag", "ball")]
    if not guns:
        return 0.0
    cover = sum(BAND_WEIGHT[b] * max(proficiency(w, game=game)[b] * (0.5 if rank(w) >= 4.0 else 1.0) for w in guns)
                for b in range(4))
    return cover + 0.4 * max(0.0, max(rank(w) for w in guns) - 3.5)


VEHICLE_TRIP = 35.0          # goals farther than this make a free vehicle nearby worth taking (brain/vehicles.py)
GROUND_RISE = 15.0           # u: a goal this far over the bot is no trip for a ground vehicle (Spire's core, 37 u up)
GETAWAY_PARK_R = 3.0         # u: the getaway ride parks this near its spot (director.vehicle_jobs)
RIDE_OUT_R = 10.0            # a rider this near his objective gets out (a runner at their flag: director.vehicle_jobs taxi)
CALLED_WAIT_R = 15.0         # a driver waits (8 s at most) for a teammate called aboard while he is this near (crew_coming)


# projectile speed in world units per second (CE kit tags, tools/ce_projectiles.py: mean of
# initial and final velocity); what a bot leads moving targets with. Hitscan-fast guns default 360.
PROJECTILE_SPEED = {"pistol": 300.0, "assault_rifle": 324.0, "sniper": 1000.0, "shotgun": 120.0,
                    "plasma_pistol": 20.0, "plasma_rifle": 37.0, "needler": 60.0, "rockets": 11.0,   # needles home: aim near
                    "fuelrod": 9.5, "flamethrower": 3.6,
                    # the tanks' main guns, from a skilled player's shots (Avalanche, logs/tmp/owner_rides.py): his
                    # Scorpion led a crossing man 0.00 s under 20 u (232 shots) and 0.15 s at 20-30 u, the Wraith's
                    # mortar 0.13 s at 10-20 u and 0.34 s at 20-30 u (19). As "rockets" (11 u/s) the Scorpion led 2.3 s
                    # at 25 u: "this guy is missing all his scorpion shots"
                    "tank_shell": 170.0, "mortar": 75.0,
                    # Halo 2 / 3 (ponytail: the kits' projectile tags are not read yet; CE-like values)
                    "brute_shot": 20.0, "missile_pod": 15.0, "spike_rifle": 40.0, "brute_plasma_rifle": 37.0}

SPLASH = {"rockets", "fuelrod", "tank_shell", "mortar", "brute_shot", "missile_pod", "grenade_launcher", "plasma_launcher", "concussion_rifle",
          "incineration_cannon", "sticky_detonator"}
# one-at-a-time weapons where a headshot kills an unshielded player (CE pistol and sniper, the
# BR / carbine / magnum / beam rifle later): good players aim them at the head, always in SWAT
PRECISION = {"pistol", "sniper", "battle_rifle", "covenant_carbine", "magnum", "beam_rifle", "dmr", "needle_rifle",
             "lightrifle", "binary_rifle"}
def crouched(p):
    """He is crouched (a player state, or None: not known, standing)."""
    return bool(p is not None and getattr(p, "crouching", False))


TREMOR = 0.0025                       # rad: the hand's wobble per tick while tracking a man (Aim.step)
COMMIT_S, COMMIT_WEAK_S = 6.0, 10.0   # a fight lasts until the target dies or is unseen this long (shield down: longer)
TARGET_HOLD = 1.0                      # s a new target in view is kept before another may take over (guide 09 4.5)
HUNT_KEEP = 4.0                        # s fresher another man's trace must be to change whom a hunt goes after (decide)
UPGRADE_GAIN = 0.08                    # loadout_value a loose gun must add to be worth the trip (upgrade_point)
UPGRADE_R, UPGRADE_ON_WAY = 14.0, 6.0  # u: a better gun this near is weighed; this near, its post does not hold it back
EYES_KEEP = 3.0                        # s every bot keeps watching where a man it saw went (look_point)
HIT_KEEP, HIT_WHIP = 2.0, 0.35         # a hit is turned to this long at least, with this spin at least (Normal's): Easy too
HEARD_KEEP = 1.5                       # a shot heard is looked toward this long at most (look_point)
# a melee press within this (a skilled player's Reach game: 19 close melees at 0.37-1.91 u, median 0.80, 18 within
# 1.18; bots pressing from 1.55 u, raised from 1.3 for the rate, swung at people well out of reach)
MELEE_R = 1.25
DRY_FIGHT_R = 2.5      # u: with every gun empty, a man nearer than this is still fought (a melee), Bot.choose
SWING_R, SWING_COS = 3.0, 0.7   # a melee press needs an enemy this near (a sword lunge 2.25 u) within 45 deg (melee_check)
HEAR_RANGE = 30.0                      # gunfire this close is heard: about where it came from (Perception.update)
ON_THE_WAY = 2.0                       # u: Easy takes only guns this close, it never goes out of its way
COVER_NEAR = 6.0                    # the farthest cover a top bot goes for in a losing fight (less for lower tiers)
SHIELD_BACK = 10.0                  # guide 06: a popped shield is 7-10 s out of the fight; after that untouched, back
ZONE_STEP_IN = 3.0   # u: a zone holder fighting this near its zone (on its floor) fights its way in (keep_in_zone)
STAY_R, STAY_HOLD_R = 2.0, 0.6   # u: this near a loose objective it works by standing on it, a bot stays on it (stay_on),
                                 # its fights kept within STAY_HOLD_R of it (the pickups came 0.07-0.59 u off; at 0.9 a fight drifted 1.3 u off)
STAY_SCORE = 1.4     # staying on it: over all but a fight (a man in sight 1.5 +; it shoots from on it)
DEFEND_R, ESCORT_R = 15.0, 12.0     # how far a defender strays from its post, an escort from our flag carrier (keep_role)
DEFEND_HOLD = 6.0                   # a CTF defender closes on a man only this near its post (fight): from cover otherwise
CLOSE_FIGHT_R = 8.0                 # a man in sight this close is fought whatever the role (decide)
PRESS_NEAR = 3.0                    # u: a press (press_on) walks in to here; closer, push_in and the melee take over
POWER_NEAR = 15.0                   # u: a power weapon up this near is not held back by a post (keep_role)
REGROUP_MATE = 8.0                  # a teammate this close is support (brain/tracefmt.py FORM_R): no pull-back (regroup_point)
HUNT_R = 12.0                       # an Invasion defender sent at the carrier / a territory being taken strays no farther
LOOK_CONE = math.radians(50)        # walking out of a fight, a deliberate look stays this close to the way it goes
H2A_FEET = 0.38                     # H2A reports a body this far above its floor (tools/ground_offset.py: Lockout 0.38,
                                    # Zenith median 0.41); the nav's points lie on the floor
FEET_GAMES = ("h2a_", "h4_")        # ... and Halo 4, the same engine (ground_offset.py, Nov 4: blood_cavern, creeper, blood_crash median
                                    # 0.39-0.40; Halo 3, Reach, Halo 2 0.00-0.02): Bot.feet
GRAVITY = 4.1                       # u/s^2 in Reach (the player's fall onto a Spire heat vent: 1.1 u/s faster
                                    # each 0.27 s)
ESCORT_TRAIL = 3.0                 # an escort's spot: this far behind the carrier, on the side away from home
RUN_DETOUR = 8.0                    # a CTF runner's side trip (a fight, an item) past this many units off its line: strays
PRESS_COVER_R = 12.0                # u: an Invasion attacker covering the territory strays past this off its spot (keep_role)
STAGE_D, STAGE_WAIT = 18.0, 6.0     # a CTF runner this near their flag with no teammate within 8 u waits this long for one
STAGE_CLEAR_WAIT = 8.0              # ... and this long, with company, for their base to clear (guide 07: keep_role)
STAGE_OUT = 6.0                     # a wait under way ends only this far past STAGE_D (a cover spot on the edge reset it)
STAGE_MATE = 12.0                   # company: a teammate this close (the lead escort clears 9 u ahead of the runner)
# A CTF runner with company goes in, guarded or not; inside GRAB_R of their flag on its stand the grab outranks a new
# fight and a retreat (GRAB_SCORE; a committed fight still wins). Both Halo 3 CTF games went 0-0 live; the sim on The
# Pit, 8v8 Legendary, 3 x 300 s: 2 pulls, 1 capture. Its runners spent 39% of their time 12-18 u out holding for a
# clear base (8v8: never clear), 16-29% backing off to cover, and no life of 41 came within 6 u of the stand
# (logs/tmp/runner_probe.py). GRAB_R 0 and STAGE_NEED_CLEAR True: the old play (A/B)
GRAB_R, GRAB_SCORE = 8.0, 2.4
GRAB_NEEDS_COMPANY, GRAB_MATE_R = True, 10.0   # (wave 2: guarded, the grab only with a teammate this near: grab_ok)
STAGE_NEED_CLEAR = False
KING_TAKE_IN = True                 # a King hill held by theirs is gone into with company, not waited out (keep_role)
SCREEN_R = 10.0                     # u: a carry's screen strays no farther from its spot (keep_role)
ONE_FLAG_PRESS = True               # One Flag attackers press as Invasion attackers do (Bot.pressing; False: A/B)
STALE_S = 6.0                       # a fight with nobody hurt this long (past 6 u): the shots do not land (break_stalemates)
# where a precision weapon aims is a decision, not the hands (those stay the same every tier): Easy aims at
# the chest 65% of the time, shield or not; the rest time the head shot (guide 03: center of mass until his
# shield pops, then the head), as often as guide 01 says: a Regular about half the time, Veteran consistent,
# Elite near-perfect. In the player's Settlement game chest shots landed 50%, head shots 31% Easy 20% (the
# player: Easy bots kept up with Legendary in H2A SWAT, where one head shot kills)
HEAD_AIM = {"novice": 0.20, "average": 0.5, "strong": 0.9, "top": 0.97}

# how much a player wants each weapon in hand (CE: the pistol is the all-round king)
WEAPON_RANK = {"rockets": 5.0, "sniper": 4.0, "fuelrod": 4.0, "shotgun": 3.0, "pistol": 3.2,
               "plasma_rifle": 2.2, "assault_rifle": 2.0, "needler": 2.0, "flamethrower": 2.0, "plasma_pistol": 1.5,
               # Halo 2: the battle rifle is the all-round king, the magnum and SMG are spawn weapons
               "sword": 4.5, "beam_rifle": 4.0, "battle_rifle": 3.6, "covenant_carbine": 3.0, "brute_shot": 2.6,
               "brute_plasma_rifle": 1.9, "magnum": 1.8, "smg": 1.6, "sentinel_beam": 1.4,
               # Halo 3 (the AR is a fair starting weapon there; excavator = the Mauler)
               "spartan_laser": 4.6, "gravity_hammer": 4.0, "excavator": 2.6, "missile_pod": 2.2,
               "spike_rifle": 1.6,
               # Halo: Reach (the DMR is the all-round king)
               "dmr": 3.6, "needle_rifle": 3.0, "focus_rifle": 3.2, "grenade_launcher": 3.0,
               "plasma_launcher": 3.8, "concussion_rifle": 2.8,
               # Halo 4: the DMR / BR / light rifle trade the all-round crown; the binary rifle and
               # incineration cannon are the power weapons
               "lightrifle": 3.5, "binary_rifle": 4.4, "railgun": 4.0, "incineration_cannon": 4.6,
               "scattershot": 3.0, "lmg": 2.4, "sticky_detonator": 2.8, "boltshot": 1.8}
FIRE_GAP = {"pistol": 0.19, "assault_rifle": 0.07, "sniper": 0.9, "rockets": 1.1, "fuelrod": 0.6,  # human cadence floor
            "plasma_pistol": 0.15, "plasma_rifle": 0.1, "needler": 0.12, "shotgun": 0.75, "flamethrower": 0.05,
            # Halo 2 (fire is a press: each pulse is one shot or one burst)
            "battle_rifle": 0.45, "smg": 0.07, "magnum": 0.2, "sword": 0.6, "brute_shot": 0.9,
            "covenant_carbine": 0.25, "beam_rifle": 0.5, "brute_plasma_rifle": 0.09, "sentinel_beam": 0.05,
            # Halo 3 (ponytail: the laser charges while held, pulses do not charge it)
            "spartan_laser": 3.0, "missile_pod": 0.8, "excavator": 0.5, "gravity_hammer": 1.0, "spike_rifle": 0.08,
            # Halo: Reach
            "dmr": 0.4, "needle_rifle": 0.3, "focus_rifle": 0.05, "grenade_launcher": 1.0,
            "plasma_launcher": 2.0, "concussion_rifle": 0.6,
            # Halo 4
            "lightrifle": 0.45, "binary_rifle": 1.5, "railgun": 1.6, "incineration_cannon": 1.8,
            "scattershot": 0.7, "lmg": 0.07, "sticky_detonator": 1.0, "boltshot": 0.3}
# automatic weapons (a held trigger streams rounds; the rest fire once a press). Guide 01 Short band
# (under 4 units): bloom hardly matters there, so an automatic sprays (guide 01: the AR's "spray plus
# melee at Short band"); taps are for Medium and Long. Rifles keep their pacing: Halo 3's BR needs
# about 0.5 s between bursts (guide 01: 4 bursts in 1.5 s), more than FIRE_GAP's 0.45
AUTO = {"assault_rifle", "smg", "smg_silenced", "plasma_rifle", "plasma_rifle_red", "brute_plasma_rifle",
        "sentinel_beam", "spike_rifle", "needler", "flamethrower", "lmg", "focus_rifle", "plasma_repeater",
        "storm_rifle", "suppressor"}
BEAM = {"focus_rifle", "sentinel_beam"}   # a continuous beam: held on the man at any range, never in bursts
NADE_CLEAR, NADE_FUSE = 4.5, 3.0          # u, s: the spot its own grenade went is kept clear of this long
SHORT_BAND = 4.0
CLOSE = 2.5      # inside this a fight is close quarters: the automatic or shotgun comes out, rifles spam
# the fastest a skilled player fires each semi-automatic (s between presses) when misses cost little:
# Short band spam (guide 03: "At Short band spam"). FIRE_GAP stays the paced Medium cadence, a little
# slower at Long
FIRE_FAST = {"pistol": 0.14, "magnum": 0.15, "dmr": 0.2, "needle_rifle": 0.16, "covenant_carbine": 0.16,
             "battle_rifle": 0.38, "lightrifle": 0.38, "plasma_pistol": 0.12, "boltshot": 0.15,
             "sniper": 0.6, "beam_rifle": 0.4, "binary_rifle": 1.2,
             # up close the shotgun is pressed as the player does, the game firing as soon as it can: his presses 0.24
             # s apart (median of 30, five Reach collector files, logs/tmp/owner_shotgun.py); at 0.75 s the bot fired
             # under its rate
             "shotgun": 0.2, "scattershot": 0.2}
# a splash weapon's own blast reaches the shooter inside this (guide 03: a rocket kills within ~8 ft and
# strips shields to ~15 ft): closer than this the bot switches, melees, or fires only as a last trade
BLAST = {"rockets": 2.0, "fuelrod": 1.8, "plasma_launcher": 1.6, "incineration_cannon": 2.2,
         "grenade_launcher": 1.5, "brute_shot": 1.2, "concussion_rifle": 1.2, "missile_pod": 1.5,
         "sticky_detonator": 1.5}
# weapons fired by a held charge (guide 03): "release" fires when let go (plasma pistol overcharge, the
# plasma launcher's bombs, railgun, boltshot), "auto" fires by itself once charged while still held
# (spartan laser); seconds of hold the bot wants
CHARGE = {"plasma_pistol": ("release", 0.9), "plasma_launcher": ("release", 1.6), "railgun": ("release", 1.1),
          "boltshot": ("release", 1.0), "spartan_laser": ("auto", 3.0)}
# the plasma launcher on a vehicle: its bombs lock one by one while the reticle holds it, then the red reticle; the player
# (Breakpoint 20261102-184132, a Banshee at 14-25 u, logs/tmp/pl_spells.py) held his reticle within 4 deg of it 2.4, 2.2
# and 4.4 s before letting go, the last volley killed it. Charged and the reticle on it this long, then let go
PL_LOCK_S, PL_LOCK_R, PL_SLIP = 2.2, 1.5, 0.3   # s, u (the reticle on a vehicle: within this of its middle), s
# melee-only weapons attack with the trigger the moment the reticle turns red: the man within the weapon's autoaim range
# and its autoaim angle of the crosshair (the game's own lunge test). OBSERVED in the kits' weapon tags (tool.exe
# export-tag-to-xml): Reach, Halo 4, H2A swords (and Halo 4's Flood prongs, H2A's infected sword) autoaim range 2.25 u,
# angle 6 deg; the gravity hammer (Reach, Halo 4) 1.35 u, 10 deg
LUNGE = {"sword": 2.25, "gravity_hammer": 1.35}
RUSH_R = 10.0         # u: a sword or hammer this near is met with the shotgun (the Flood close 3 u/s, 6 with a thruster)
LUNGE_ANGLE = {"sword": math.radians(6.0), "gravity_hammer": math.radians(10.0)}
LUNGE_BODY = 0.15     # u: half a Spartan's width, the part of him the red cone may touch (INFERRED: tags give width 0)
# ... per game where the kit has no tags here: a skilled player's Halo 3 sword kills came from first presses at 1.2-2.0 u
# (Construct, The Pit: logs/tmp/swordlunge.py over his collect files; H3EK's tags are packed); Halo 2's lunge is the
# long one (guide 03: ~22 ft)
LUNGE_GAME = {"h3": {"sword": 1.8}, "h2": {"sword": 2.2}}
# games whose sword / hammer attack is the melee action (Reach's tags: "melee only"; Halo 4 Flood live: 138 trigger
# presses no damage, with the melee bit 44 presses 9 kills; H2A INFERRED: Halo 4's engine)
MELEE_TRIGGER = {"reach", "h4", "h2a"}
SWORD_OPEN = 8.0     # u: past this a sword or hammer holder does not cross the open at a man aiming at it (sword_cover)
CLASH_MELEE = (0.15, 0.7)   # s after its own sword press: a sword man still standing in reach is a clash (parried), and
                            # the winner's quick melee follows (owner, issue #3); Easy never does it
# the gap between sword presses while he is in reach: the player pressed every 0.15-0.22 s as he closed until one landed
# (a press past the red reticle whiffs, the next one lunges); one press then 0.6 s of nothing missed live (The Pit
# 199.95 s: the one press at 1.5 u, no damage, dead at 200.19)
LUNGE_GAP = 0.17
SHOTGUN_SLACK = 0.7   # a shotgun fires this far past its band: the two-shot range (guide 03: ~20 ft)
OUT_OF_REACH = 2.2    # past reach() times this a gun holds fire but for a deterrent burst (fight): Medium-band bursts
                      # stay (an SMG at 8 u, an AR at 10, guide 03), across the map does not
STRAFE_JUMP = 0.187   # chance of a jump at each strafe change in a fight (times jump skill and 0.5 + jumpiness; 0.22 before)
STRAFE_PAUSE = 0.25   # chance of a 0.2-0.4 s stop at a juke's turn (0.40 and 0.25-0.5 s from: live Spire
                      # fight strafe 0.90 u/s to the player's 0.70, then Forge World 0.55 to his
                      # 0.89)
HOP_RATE = 0.306      # hops a second while running out of a fight (times jump skill and 0.5 + jumpiness; 0.15 before: Legendary 1.9 jumps a minute to the player's 3.8, 0.24 to, 0.36 to)
PLANT = 0.4           # share of fights a bot plants its feet for the long shots (past 10 u, precision guns 14; 0.5, 8 and 12 before; 0.3 to: strafe 0.88-1.02 u/s to the player's 0.70)
AIR_HIGH, AIR_LOW = 9.0, 2.5   # aircraft over the ground: travelling in painted high air, attack runs in painted low air
AIR_LOOK_MAX = 60.0   # u: the farthest a pilot's probe looks ahead (Bot.air_guard; tools/vehicle_clearance.py's FAR)
AIR_SIDES = (math.pi / 2, -math.pi / 2, math.pi / 4, -math.pi / 4, 3 * math.pi / 4, -3 * math.pi / 4)   # its side probes
# Bot.travel_boost: from this far from where it goes (u), the nose within the cone of its way, open for the seconds at
# twice its speed (the player's ground ttc 10th percentile 0.6-0.9 s at his speed, tools/vehicle_clearance.py)
TRAVEL_BOOST_FROM, TRAVEL_BOOST_CONE, TRAVEL_BOOST_S = 15.0, math.radians(20.0), 1.0
CHAINGUN_HEAT = (13.5, 3.3)   # s: Reach's Warthog chaingun, fire to overheat and all cooled again (Bot.chaingun_heat)
# A Banshee's attack runs, a skilled player's (Spire match 232250: 169 s, 17 runs, 20 turn-arounds;
# logs/tmp/owner_banshee.py): a run starts 15-30 u out (median 20) and closes to 1-15 u, nose on him; the plasma cannons
# in taps from 10-28 u (median 15.6); the second weapon (a bomb, Reach's fuel rod) held about 2.4 s from 7-36 u (median
# 21.5), about every 6 s; past him it flies on straight and climbs to 20-28 u off, then turns back hard (13 to 4 u/s)
PASS_START, PASS_OVER, PASS_EXTEND, PASS_OUT_S, PASS_START_H, PASS_START_MIN = 15.0, 6.0, 22.0, 6.0, 15.0, 12.0
CANNON_R, FIRE2_R, FIRE2_HOLD, FIRE2_EVERY = 28.0, (12.0, 36.0), 2.4, 6.0
AIR_GUN_R = 1.5       # u: an aircraft's guns fire with its nose this near his middle (Bot.air_pass)
AIR_JINK, AIR_JINK_S = 0.4, 1.0   # rad, s: shot at in a run's way in (out of gun range) or way out, a swerve that long
# an attack run's ground check (Bot.air_guard): its path along the nose AIR_PULL_S ahead at its speed, under AIR_RUN_FLOOR
# over the floor there: pull out and end the run. (The old rule, 6.8 u over the highest floor within 6 u of four points
# 2.5 s on, fired on 44% of a replayed Shrine Banshee's ticks and held every dive 16 u over him: tools/air_replay.py)
AIR_PULL_S, AIR_RUN_FLOOR = 1.2, 1.5
AIR_CEIL = 3.0       # u: off a run, floors this far over an aircraft still count as ones to climb over (Bot.air_guard)
AIR_VTOL_CLIMB = 3.0  # u/s: a Falcon's / Hornet's climb on its vertical input (trace 20261105-201917: 3.0-3.2 u/s up)
AIR_ROOF_M = 2.5     # u: an aircraft's middle stays this far under the map's roof (Terrain.air_roof; Bot.air_roof_guard)
# (in Reach that right button is the Banshee's boost, not a weapon: his speed held 8.8 u/s, not held 4.3, every hold of
# 1 s or more faster; logs/tmp/owner_rmb_speed.py.) Reach's fuel rod is cycled to like a secondary: 10 shots from 10-31
# u (median 16.5), about every 17 s; the cycle to the shot 0.67 s, the shot to the cycle back 0.44 s
# (logs/tmp/owner_fuelrod.py, his key 1 pairs)
FUELROD_R, FUELROD_EVERY, FUELROD_FIRE, FUELROD_SHOT, FUELROD_BACK = (10.0, 31.0), 15.0, 0.67, 0.15, 0.44
RAM_ERR = {"average": 20.0, "strong": 12.0}   # a ram's heading error, degrees at most (guide 18 2.1; Easy never rams)
BOOST_R, BOOST_CONE, BOOST_HOLD, BOOST_EVERY = 8.0, math.radians(20.0), 0.85, 3.0   # Bot.ram_boost (a skilled player's)
FLEE_D, FLEE_PROGRESS, FLEE_REST = 8.0, 0.6, 5.0   # a boarder fled by open ground FLEE_D u on; under FLEE_PROGRESS u
                                                   # in a flee: none for FLEE_REST s (Bot.drive, open_way)
BOARD_GIVE_UP, BOARD_BAN = 4.0, 20.0   # s pressing Use beside an enemy vehicle before it is left alone, and for how long
BOARD_TRY_R, BOARD_SPEED = 6.0, 3.0   # u, u/s: an enemy vehicle this near, not running at us faster, is boarded (board_check)
# the player's Scorpion board (Boneyard 20261102-192517, 544-557 s, logs/tmp/board_detail.py): its driver fought a man
# 36 u off the other way (its view 110-170 deg off the line to him), the tank under 1.5 u/s; he walked in from 11 u,
# waited 1.8-2.6 u off its front 3 s, got on from 2.3 u, and its driver was dead 1.6 s later (hits 0.55 s apart, melee
# only, no grenade), he stepped off 1 s after. A tank whose driver looks BOARD_BLIND off us is stalked from BOARD_STALK_R
BOARD_STALK_R, BOARD_BLIND, BOARD_FRONT, BOARD_MELEE_EVERY = 11.0, math.radians(90.0), 2.0, 0.55   # u, rad, u, s
TANK_FETCH_R = 25.0   # u: an enemy tank about, a launcher this near is fetched by anyone Normal and up (vehicle_mission)
IDLE_JUMP = 0.0255    # jumps a second standing about out of a fight, for nothing (times jump skill and 0.3 + jumpiness)
JUMP_SHOT = 0.2125    # chance of a jump shot at each shot (times jumpiness and jump skill; 0.16 before, 0.25 to)
OBJ_NADE = 0.85       # a grenade at a man at the objective on the way in, a second (times the grenades trait; 0.15, 0.30, 0.60: 0.99, 0.89, 1.30 a minute, Spire, the player 2.37)
NADE_RATE = 1.0       # another grenade into a long fight, a second (times the grenades trait), 3-7 s apart, from 4 u (0.55, 6 u and 4-9 s to: 1.16 a minute live on Spire, 13 of 201 presses; 62% of the fight time under 6 u; 0.07, 0.15, 0.22 on: Legendary 0.86, 1.4, 1.5 a minute to the player's 2.37)


def aims_at(e, me, within=0.4):
    """Whether player e's view points at me (within `within` rad of the line to my chest)."""
    yaw, pitch = angles_to(eye(e), me.pos + Vec(0, 0, 0.35))
    return math.hypot(wrap(yaw - e.yaw), pitch - e.pitch) < within


def vpos(v):
    """A vehicle's place as a Vec (the live game's are [x, y, z], the sim's and the tests' may be Vecs)."""
    q = v["pos"]
    return q if isinstance(q, Vec) else Vec(*q[:3])


def reach(w, game=None):
    """How far a gun's shots do anything: a sword's lunge or a hammer's swing, a shotgun's band plus its two-shot
    slack, else the top of its band. One rule for the trigger (fight) and the finishing hold (choose_weapon)."""
    if w in LUNGE:
        return LUNGE_GAME.get(game, {}).get(w, LUNGE[w])
    hi = WEAPON_RANGE.get(w, (5.0, 25.0))[1]
    return hi + SHOTGUN_SLACK if w in ("shotgun", "scattershot") else hi


def red_reticle(me, at, w, game=None):
    """A sword's or hammer's reticle is red on a man at `at`: within the weapon's reach (its autoaim range) and the view
    (me.yaw, me.pitch) within its autoaim angle of his body (the angle plus his half width, or half height, at that
    range)."""
    d = dist(me.pos, at)
    if w not in LUNGE or d > reach(w, game):
        return False
    ya, pa = angles_to(eye(me), at + Vec(0, 0, 0.35))
    d = max(d, 0.3)
    return (abs(wrap(ya - me.yaw)) <= LUNGE_ANGLE[w] + math.atan2(LUNGE_BODY, d)
            and abs(pa - me.pitch) <= LUNGE_ANGLE[w] + math.atan2(0.35, d))
# close-quarters weapons: preferred inside CLOSE over a rifle
CQ = (AUTO - {"focus_rifle"}) | {"shotgun", "scattershot", "sword", "gravity_hammer", "excavator", "flamethrower",
                                  "boltshot"}   # (the focus rifle's held beam is a long-range gun: review F4)
# weapons with a scope (guide 03 zoom column); zoomed past ZOOM_FROM units when the bot is not being hit
SCOPED = {"pistol", "sniper", "battle_rifle", "covenant_carbine", "dmr", "needle_rifle", "beam_rifle",
          "lightrifle", "binary_rifle", "focus_rifle", "rockets", "spartan_laser", "railgun"}
ZOOM_FROM = {"sniper": 6.0, "beam_rifle": 6.0, "binary_rifle": 6.0, "focus_rifle": 6.0, "rockets": 8.0,
             "spartan_laser": 8.0, "railgun": 8.0}
ZOOM_FAR = 20.0  # snipers use the second zoom level past this (guide 03: "second for Extreme band only")
SNIPERS = {"sniper", "beam_rifle", "binary_rifle", "focus_rifle"}   # sniper-type: the only scopes an Easy bot uses
LOCK_GAMES = {"h2", "h2a", "h4"}   # rockets lock on a vehicle held in the reticle (guide 03; Reach: air only)
# guns that kill vehicles (guide 04 counters on foot: rockets, laser, missile pod, plasma launcher, fuel rod, and the
# plasma pistol's EMP in Halo 2, 3 and Reach): what the Grenadier fetches when an enemy vehicle is about, what every
# bot brings out against one (choose_weapon), what loadouts.py asks for after a vehicle kill
ANTI_VEHICLE = {"plasma_pistol", "rockets", "spartan_laser", "plasma_launcher", "grenade_launcher", "railgun",
                "sticky_detonator", "fuelrod", "missile_pod", "incineration_cannon"}
# a driver breaks off when one of these is aimed at it, and keeps away from a man with an EMP gun (guide 17 §4.6; EMP
# stops engines from Halo 3 on, Reach's grenade launcher on a held detonation)
VEHICLE_KILLERS = {"rockets", "spartan_laser", "plasma_launcher", "missile_pod", "railgun", "fuelrod",
                   "incineration_cannon"}
EMP_GUNS = {"plasma_pistol", "grenade_launcher"}
EMP_GAMES = {"h3", "odst", "reach", "h4"}
# games where the magnum is a real gun (guide 03: Reach's 5-shot kill, Halo 4's 6), the AR a fair one (Reach: the
# plasma repeater is the Elites' assault rifle; the plasma pistol is the weakest gun in the game, kept for its
# overcharge: the repeater had no rank or band row, read as a 2 u gun, and the pistol won every fight past 4 u)
RANK_GAME = {"reach": {"magnum": 2.6, "assault_rifle": 2.2, "plasma_repeater": 2.3, "plasma_pistol": 1.0}, "h4": {"magnum": 2.2, "assault_rifle": 2.6},
             "h3": {"magnum": 1.2}}


@dataclass
class Belief:
    pos: Vec
    vel: Vec
    t: float           # when last confirmed
    seen: bool         # in view right now
    source: str        # eyes | radar | callout
    weak: bool = False  # a teammate called him cracked / one shot


@dataclass
class Order:
    role: str          # runner | escort | defender | hunter | mid | free
    point: Vec = None
    target: int = -1
    via: tuple = ()    # waypoints to pass first (attack lane)


class Perception:
    def __init__(s, prof, rng):
        s.p, s.rng = prof, rng
        s.beliefs = {}
        s.pending = {}       # enemy id -> time recognition completes
        s.recognized = set()
        s.lost_at = {}
        s.dropped = {}       # enemy id -> when recognition lapsed (quick to pick up again)
        s.last_seen = {}     # enemy id -> last time our eyes had him (radar and callouts do not count)
        s.clear_at = {}      # enemy id -> last time a clear line ran from our eye to him (Bot.line_clear: no shot without)
        s.mags = {}          # enemy id -> (gun, rounds in its magazine) at the last look: his gunfire (fired)

    def fired(s, e):
        """Did enemy e fire since the last look? His magazine went down with the same gun in hand (live games read
        every player's magazine; -1 unknown: never)."""
        i = e.weapons.index(e.weapon) if e.weapon in e.weapons else -1
        mag = e.mag[i] if 0 <= i < len(e.mag) else -1
        last = s.mags.get(e.id)
        s.mags[e.id] = (e.weapon, mag)
        return mag >= 0 and last is not None and last[0] == e.weapon and mag < last[1]

    def react_time(s, peripheral, d=99.0):
        t = s.p.reaction * math.exp(s.rng.gauss(0, 0.25))  # log-normal around the tier median
        if d < 3.0 and s.p.reaction > HANDS["reaction"]:
            t *= HANDS["reaction"] / s.p.reaction   # Easy: a man in its face, at a human's speed
        return t * (1.6 if peripheral else 1.0)

    def hurt_by(s, now, me, snap, los=None):
        """Shot by someone we do not see: like the HUD's damage arc, the bot learns roughly where
        from. The attacker is the enemy aiming at us (smallest angle between its aim and the line
        to us); within 0.25 rad it becomes a belief to turn toward and recognition is quick."""
        hp = me.shield + me.health
        loss = getattr(s, "last_hp", hp) - hp
        hurt = loss > 0.01
        s.last_hp = hp
        if not hurt:
            return None
        s.losses = [x for x in getattr(s, "losses", []) if now - x[0] < 2.0] + [(now, loss)]   # damage taken lately
        s.hurt_at = now
        best, best_off = None, 0.25
        loose, loose_off = None, 0.7
        for e in snap.players:
            if e.team == me.team or not e.alive or dist(e.pos, me.pos) > SIGHT_RANGE:
                continue
            yaw, pitch = angles_to(eye(e), me.pos + Vec(0, 0, 0.35))
            off = math.hypot(wrap(yaw - e.yaw), pitch - e.pitch)
            if off < best_off:
                best, best_off = e, off
            elif off < loose_off and dist(e.pos, me.pos) < 35.0:
                loose, loose_off = e, off
        # nobody aiming within 0.25 rad (a shooter leading us or aiming high, a burst's spread): the man aiming nearest
        # our way within 0.7 rad, found a little slower, as a person turns to the HUD's damage arc (live Boneyard: with
        # the attribution missed, shot bots walked on to their post and a third of the first hits went unanswered)
        sloppy = best is None and loose is not None
        best = best if best is not None else loose
        if best is None:
            # nobody aiming our way at all (a grenade, splash, a vehicle's gun): the nearest enemy who can see us, else
            # the nearest one, found slower still, as a person turns to the HUD's damage arc (live Spire: 25 of 31
            # unanswered first hits had no enemy aiming at the bot, and the bot kept walking to its post)
            near = [e for e in snap.players if e.team != me.team and e.alive and dist(e.pos, me.pos) < 30.0]
            seeing = [e for e in near if los is not None and los(e.pos, me.pos)]
            best = min(seeing or near, key=lambda e: dist(e.pos, me.pos), default=None)
            sloppy = best is not None
        if best is not None:
            burst = best.id != getattr(s, "attacker_id", None) or now - getattr(s, "attacker_t", -9.0) > 1.0
            if burst:
                # a new shooter (or a new burst): a person notices who it is in about his reaction time, off to the
                # side (Bot.shot_by_other; live Spire: half the Legendary bots turned in 0.0-0.11 s, the player 0.40)
                s.attacker_notice = now + s.react_time(True) * (1.4 if sloppy else 1.0)
                if not sloppy and best.weapon not in BLAST:
                    s.shot_line = (best.pos, me.pos, now)   # a round's line to us, a burst once (Bot.plan: learned)
            s.attacker_id, s.attacker_t = best.id, now   # who is hurting us (decide: turn on him)
            if not sloppy:
                # aiming straight at us as his shot landed: he has a line to us (Perception.update). Only for a first hit
                # out of nowhere: not while fighting a man in sight (Easy's tunnel vision, a turn on a second shooter is
                # Bot.shot_by_other's), nor from the man just fought who broke sight (the fight's own memory chases him)
                sure = getattr(s, "attacker_sure", (None, -9.0))
                answering = sure[0] == best.id and now - sure[1] < 1.5
                engaged = (any(k != best.id for k in s.visible_enemies())
                           or now - s.last_seen.get(best.id, -9.0) < 1.0)
                if answering or not engaged:
                    s.attacker_sure = (best.id, now)
        if best is not None:
            # a man known from before but not in sight now hurts us: where he shoots from now, to turn to (only the
            # unrecognised were turned to: hit by one seen a while ago, a bot kept looking where it was. Live, three
            # Reach matches: 4-16% of first hits got no turn at all, Legendary's 16% the most, it knows the most)
            b = s.beliefs.get(best.id)
            if not b or not b.seen or now - b.t > 0.3:
                s.beliefs[best.id] = Belief(best.pos, best.vel, now, False, "hit")
        return best

    def update(s, now, me, snap, los, callouts):
        new_sightings = []
        s.hurt_by(now, me, snap, los)
        for e in snap.players:
            if e.team == me.team or not e.alive:
                s.beliefs.pop(e.id, None) if not e.alive else None
                s.recognized.discard(e.id); s.pending.pop(e.id, None)
                continue
            d = dist(me.pos, e.pos)
            yaw, _ = angles_to(me.pos, e.pos)
            off = abs(wrap(yaw - me.yaw))
            shot = s.fired(e)
            # in view, or at arm's length anywhere (his footsteps, the edge of the screen: a man circling us is never
            # lost behind our back; the player's Reach game); a flare in the eyes: nothing. The man aiming straight at
            # us whose shot just hurt us has a line to us whatever the level data says (live Boneyard: 22 of 26
            # unanswered first hits came from men on ledges 5-6 u up that the line-of-sight model made solid, even for
            # their own aim on the bot; the bots turned their heads and never fought them)
            sure = getattr(s, "attacker_sure", (None, -9.0))
            shooting = sure[0] == e.id and now - sure[1] < 1.5 and d < 30.0
            in_view = (off < PERIPHERAL_HALF and d < SIGHT_RANGE or d < 2.5) and not getattr(s, "blind", False)
            line = in_view and los(me.pos, e.pos)
            if line:
                s.clear_at[e.id] = now
            visible = in_view and (shooting or line)
            if visible and e.camo_until > now and d > 4 and s.rng.random() < 0.9:
                visible = False     # active camo: only a shimmer, mostly missed past a few units
            if visible:
                s.lost_at.pop(e.id, None)
                if e.id in s.recognized:
                    s.beliefs[e.id] = Belief(e.pos, e.vel, now, True, "eyes")
                    s.last_seen[e.id] = now
                elif e.id not in s.pending:
                    # (the man shooting us: a person finds him by the damage arc in about his usual reaction time; 0.5
                    # of it before turned Legendary onto a shooter in 0.21 s to the player's 0.40, live Spire)
                    quick = 1.0
                    if now - s.dropped.get(e.id, -9.0) < 3.0:
                        quick = min(quick, 0.35)   # the man we just lost behind a pillar: no new double take
                    s.pending[e.id] = now + s.react_time(off > FOV_HALF, d) * quick
                elif now >= s.pending[e.id]:
                    del s.pending[e.id]
                    s.recognized.add(e.id)
                    s.beliefs[e.id] = Belief(e.pos, e.vel, now, True, "eyes")
                    s.last_seen[e.id] = now
                    new_sightings.append(e)
                continue
            # not visible: lose recognition after a short grace, keep the memory
            s.pending.pop(e.id, None)
            if e.id in s.recognized:
                s.lost_at.setdefault(e.id, now)
                if now - s.lost_at[e.id] > 1.2:    # a strafe or a jump out of view is not forgetting
                    s.recognized.discard(e.id)
                    s.dropped[e.id] = now
            b = s.beliefs.get(e.id)
            if b:
                b.seen = False
            # motion tracker (if the match settings show him enemies): moving, not crouched, in range; skill decides
            # whether it is noticed
            moving = e.vel.flat() > 0.6 and not e.crouching
            if me.tracker and d < RADAR_RANGE and moving and not getattr(s, "jammed", False) and s.rng.random() < s.p.radar * 0.15:
                s.beliefs[e.id] = Belief(e.pos, Vec(), now, False, "radar")
            # his gunfire within earshot: about where the shots came from, a belief look_point pre-aims toward. Not over
            # a hit a moment ago (that turns the bot on its shooter); Easy (aware 0) does not listen for it
            b = s.beliefs.get(e.id)
            if (shot and d < HEAR_RANGE and s.rng.random() < s.p.aware
                    and not (b is not None and b.source == "hit" and now - b.t < 1.5)):
                jit = Vec(s.rng.gauss(0.0, 1.0), s.rng.gauss(0.0, 1.0), 0.0)
                s.beliefs[e.id] = Belief(e.pos + jit, Vec(), now, False, "heard")
        for c in callouts if s.p.teamwork > 0 else ():   # teammates' callouts: the zone only, no velocity
            # (Easy, teamwork 0, plays for itself and does not listen)
            if c.kind == "flag_taken" or c.speaker == me.id or now < c.avail.get(me.id, c.t_heard):
                continue            # not understood yet (listener timing, research 07)
            k = ("callout", c.zone)
            if k not in s.beliefs or s.beliefs[k].t < c.t_event:
                s.beliefs[k] = Belief(c.pos_said, Vec(), c.t_event, False, "callout",
                                      weak=c.kind in ("cracked", "one_shot"))
        for k in [k for k, b in s.beliefs.items() if now - b.t > s.p.memory]:
            del s.beliefs[k]
        present = {p.id for p in snap.players}
        for k in [k for k in s.beliefs if isinstance(k, int) and k not in present]:
            del s.beliefs[k]                # gone from the game (a hologram that died, a player who left):
            s.recognized.discard(k)         # a "seen" belief of nobody crashed decide()
        return new_sightings

    def visible_enemies(s):
        return [k for k, b in s.beliefs.items() if isinstance(k, int) and b.seen and k in s.recognized]


class Aim:
    """Re-aims at a tracking interval, flicks with Fitts-law timing, overshoots sometimes,
    leads imperfectly and tightens while it stays on one target (focus)."""

    def __init__(s, prof, rng):
        s.p, s.rng = prof, rng
        s.next_sample = 0.0
        s.goal = None          # (yaw, pitch) the hand is moving toward
        s.flick = None         # (start_yaw, start_pitch, goal_yaw, goal_pitch, t0, dur)
        s.err = (0.0, 0.0)
        s.target = None
        s.focus = 0.0

    def step(s, now, dt, me, target_id, tpos, tvel, aim_head, gun=None, origin=None, crouched=False):
        # gun: what the shot comes out of when it is not the gun in hand (a vehicle's turret: vehicles.GUNNER_GUN);
        # origin: where that shot leaves (vehicles.gun_height), else the eye; crouched: the target is (his head and
        # chest lower: core.CROUCH_HEAD, CROUCH_CHEST)
        if target_id != s.target:
            s.target, s.focus = target_id, 0.0
        s.focus = min(1.0, s.focus + dt / 1.5)
        # The hand tracks the target every tick (where it is now); what is re-judged only at the
        # tier's sampling interval is the read of its velocity, the lead and the error. A stale
        # velocity read is what makes a juke work; a stale *position* (shooting where the target
        # was 0.1-0.3 s ago) missed strafing targets nearly always (live traces).
        sample = now >= s.next_sample
        if sample:
            s.next_sample = now + s.p.track * s.rng.uniform(0.8, 1.2)
            s.v_est = tvel
            s.lead_k = s.p.lead * s.rng.uniform(0.6, 1.2)
            spread = s.p.aim_err * (1.0 - 0.7 * s.focus)
            s.err = (s.rng.gauss(0, spread), s.rng.gauss(0, spread * 0.6))
        d = dist(me.pos, tpos)
        gun = gun or me.weapon
        lead_t = d / PROJECTILE_SPEED.get(gun, 360.0)   # flight time of this gun's shot
        # lead: bullet flight plus the ~0.05 s from our read of the game to the shot leaving;
        # explosives at the feet (the splash still kills a near miss), else chest or head
        h = (0.05 if gun in SPLASH else (CROUCH_HEAD if aim_head else CROUCH_CHEST) if crouched else
             0.62 if aim_head else 0.35)
        aim_pt = tpos + getattr(s, "v_est", tvel) * ((lead_t + 0.05) * getattr(s, "lead_k", s.p.lead)) + Vec(0, 0, h)
        # from where our eye is when the shot leaves: a person's hand follows his own strafe without thinking.
        # Uncounted, bots moving fast sideways hit 25% to 31% standing, and Legendary, always strafing, hit
        # 40% in the sim to Easy's 52%
        gy, gp = angles_to((origin or eye(me)) + me.vel * 0.05, aim_pt)
        gy, gp = gy + s.err[0], gp + s.err[1]
        # how fast the aim point swings (a man crossing): the hand trails it by about rate x lag (on_target)
        prev = getattr(s, "goal_prev", None)
        s.goal_rate = abs(wrap(gy - prev[0])) / max(now - prev[1], 1e-3) if prev and 0 < now - prev[1] < 0.2 and             target_id == s.target else 0.0
        s.goal_prev = (gy, now)
        if sample:
            delta = math.hypot(wrap(gy - me.yaw), gp - me.pitch)
            # a flick: ballistic move, maybe overshoot, then corrections follow. One under way runs out
            # (re-planning it every sample restarted it slow: a man circling a bot at arm's length was
            # never caught, the error grew from 0.01 to 1.75 rad; the player's Reach game), and it
            # lands where he will be by then (his angular speed around us), not where he was
            if (delta > 0.35 and target_id is not None      # flicks are for a target; looking around
                    and (s.flick is None or abs(wrap(gy - s.flick[2])) > 0.6)):   # turns smoothly (below)
                s.focus = 0.0
                over = 1.0 + (s.rng.uniform(0.08, 0.18) if s.rng.random() < s.p.overshoot else 0.0)
                dur = (0.06 + 0.07 * math.log2(1 + delta / 0.05)) * (s.p.track / 0.18) ** 0.3 * s.p.flick   # (a person flicks
                # onto a man at up to 435 deg/s, p95 of the player's Cyberdyne acquisitions; 0.08 + 0.09 peaked near
                # 200)
                dur = max(dur, 1.5 * delta / s.p.turn)   # its peak speed within the turn speed (Bot.turn_cap; the smooth
                                                         # swing peaks at 1.5x its mean)
                v = getattr(s, "v_est", tvel) - me.vel
                rx, ry = aim_pt.x - me.pos.x, aim_pt.y - me.pos.y
                omega = (rx * v.y - ry * v.x) / max(rx * rx + ry * ry, 0.25)
                land = gy + omega * dur * s.lead_k / max(s.p.lead, 1e-3)
                s.flick = (me.yaw, me.pitch, me.yaw + wrap(land - me.yaw) * over,
                           me.pitch + (gp - me.pitch) * over, now, dur)
        s.goal = (gy, gp)
        if s.flick:
            y0, p0, y1, p1, t0, dur = s.flick
            u = min(1.0, (now - t0) / dur)
            u = u * u * (3 - 2 * u)  # smooth start and stop
            if u >= 1.0:
                s.flick = None
            return y0 + (y1 - y0) * u, p0 + (p1 - p0) * u
        gy, gp = s.goal or (me.yaw, me.pitch)
        # smooth pursuit, max turn per tick: on a target 5.4 rad/s; walking and looking around a person turns about
        # 4 rad/s (a bot used to whip 180 degrees to its route in 0.3 s and turn its back on a man it was about to
        # notice; at 2.4 rad/s it walked into corners still facing the last leg, 16% of its walking on a path faced
        # 60+ degrees off it). A hand lags the eye: it closes the gap with a ~0.09 s time constant, not 60% of it
        # every tick (the player's Cyberdyne games: bots kept the crosshair on the body 80-94% of their trigger
        # time to his 68%, tools/fluidity.py), plus a small tremor (his aim wobbles 0.23 degrees a tick while
        # tracking, theirs 0.07-0.17). Heroic and Legendary's hands are quicker (profiles.SHARP: lag 0.065 / 0.045
        # s)
        rate = (9.0 if target_id is not None else 6.5) * dt
        gain = 1.0 - math.exp(-dt / s.p.lag)
        if target_id is None and abs(wrap(gy - me.yaw)) > 1.2 and getattr(s, "quick_ok", False):
            rate, gain = 12.0 * dt, max(gain, 0.5)   # a new way to go behind us: one quick turn (a person swings
                                                      # 180 degrees in about 0.3 s, then walks)
        whip = getattr(s, "whip", 0.0)
        if target_id is None and whip > 0 and abs(wrap(gy - me.yaw)) > 0.2:
            # shot by a man out of view: spin to where it comes from (Legendary 18 rad/s, about 0.25 s for 180
            # degrees; the player answers a first hit in 0.17-0.56 s, Legendary took 0.7-1.4 s turning at
            # walking speed: Construct, logs/tmp/shot_case.py)
            rate, gain = (8.0 + 10.0 * whip) * dt, max(gain, 0.3 + 0.4 * whip)
        dy = max(-rate, min(rate, wrap(gy - me.yaw)))
        dp = max(-rate, min(rate, gp - me.pitch))
        tremor = s.rng.gauss(0, TREMOR * (s.p.extra or {}).get("tremor_k", 1.0)) if target_id is not None else 0.0
        return me.yaw + dy * gain + tremor, me.pitch + dp * gain + tremor * 0.5

    def on_target(s, me, tpos, yaw=None, pitch=None, slack=1.0):
        """Whether the bot *thinks* it is on target: the crosshair it is sending (yaw, pitch; else its view) is
        on his body where its own (error-laden) aim plan puts him: 0.12 to each side and 0.22 up or down of
        the aim point, a Spartan's size (guide 09 4.8: no firing while still swinging on), times slack (close
        quarters: fire.) It used to allow 0.35 to the side and 0.56 up or down at any range, and fired while
        still turning onto the man missed. Whether the shot really hits is the game's business."""
        if not s.goal:
            return False
        d = max(dist(me.pos, tpos), 0.3)
        gy, gp = s.goal
        yaw, pitch = (me.yaw if yaw is None else yaw), (me.pitch if pitch is None else pitch)
        # plus the hand's steady trail behind a swinging aim point (its rate times the hand's lag, capped at one more
        # body width): a smooth pursuit never closes on a moving point, and a strong bot dead on a man crossing at 2.2
        # u/s 8 u off (0.000-0.003 rad) held fire 0.9 s, its plan's lead point 0.015 rad ahead of the hand, the window
        # 0.014 (tools/test_equipment.py part 11; logs/tmp/dbg_aim.py). The aim itself is unchanged
        trail = min(getattr(s, "goal_rate", 0.0) * s.p.lag, math.atan2(0.12, d))
        return abs(wrap(gy - yaw)) < math.atan2(0.12 * slack, d) + trail and abs(gp - pitch) < math.atan2(0.22 * slack, d)

    def landing(s, now, early, target_id):
        """In the last `early` share of a flick onto this target (profiles.LOOSE: a person fires as his swing ends)."""
        return s.flick is not None and s.target == target_id and now - s.flick[4] >= (1.0 - early) * s.flick[5]


class Bot:
    def __init__(s, pid, team, profile, mapdata, rng=None):
        s.id, s.team, s.p, s.map = pid, team, profile, mapdata
        s.rng = rng or random.Random(pid)
        s.per = Perception(profile, s.rng)
        s.aim = Aim(profile, s.rng)
        s.game = game_of(mapdata.get("name", ""))
        s.kit = Kit(profile, s.game)   # equipment, abilities, powerups
        s.next_decide = 0.0
        s.goal = ("roam", None)
        s.order = Order("free")
        s.strafe_dir, s.next_strafe_flip = 1, 0.0
        s.next_fire = 0.0
        s.fired_last = False
        s.grenade_ready_at = 0.0
        s.last_killer = -1
        s.reports = []          # events for the director, drained each tick
        s.roam_point = None
        s.engaged_since = None
        s.item_err = {}         # item index -> {timer value: this bot's timing error}
        s.item_skip = {}        # item index -> skip until (stood on its spot and got nothing)
        s.item_timers = {}

    # --- director hooks
    def set_order(s, order, sure=False):
        # sure: a person's call (a honk) is answered by every bot that takes orders at all (Easy does not)
        if order.role != s.order.role and (s.p.teamwork <= 0 or not sure and s.rng.random() > s.p.teamwork):
            return  # "rogue" moment: keeps doing its own thing this round
        if order.role != s.order.role or order.via != s.order.via:
            s.via_left = list(order.via)
        s.order = order

    def objective_point(s, me):
        # a route point counts as passed when reached, or when it is behind us (we got closer to
        # the goal another way, e.g. chasing a fight); never walk back to it
        while getattr(s, "via_left", None) and (dist(me.pos, s.via_left[0]) < 5 or
                                                 dist(me.pos, s.order.point) < dist(s.via_left[0], s.order.point)):
            s.via_left.pop(0)
        return s.via_left[0] if getattr(s, "via_left", None) else s.order.point

    def lead(s, me, snap):
        """Who an escort goes with: our carrier (a person too), else the runner the director named (Order.target)."""
        c = next((p for p in snap.players if p.team == me.team and p.has_flag and p.alive and p.id != me.id), None)
        if c is None and s.order.role == "escort" and s.order.target >= 0:
            c = snap.player(s.order.target)
            c = c if c is not None and c.alive and c.team == me.team and c.id != me.id else None
        return c

    def live_point(s, me, snap):
        """Where the order's point is now (the director re-plans every 2 s): an escort keeps ESCORT_TRAIL off its lead
        on the side away from our home (behind a carrier, between him and the chase; ahead of a runner, into their
        base first); a CTF hunter goes where our flag is. None: the order's own point."""
        o = s.order
        if o.role == "escort":
            c = s.lead(me, snap)
            if c is None:
                return None
            home = s.map["flags"].get(s.team % 2)
            theirs = (snap.flags or {}).get(1 - me.team % 2) if snap.mode == "ctf" else None
            if (not c.has_flag and theirs is not None and theirs.state == "home"
                    and 2.0 < dist(c.pos, theirs.pos) < STAGE_D and s.id % 2 == 0):
                # the runner waits for a clear base (keep_role): the lead escort goes in and clears it (guide 07, "lead
                # escort: clears the route"), 9 u on toward their flag; the other stays with the runner
                f = theirs.pos - c.pos
                return c.pos + f * (min(9.0, f.flat() - 2.0) / f.flat())
            d = c.pos - home if home is not None else Vec()
            if snap.mode == "invasion" and c.has_flag and snap.inv_delivery is not None:
                # the core / bomb run: the lead escort ahead on the way to the delivery point, the other behind (guide
                # 07: "lead escort clears the route ... rear escort kills chasers"; the CTF home means nothing here)
                d = (snap.inv_delivery - c.pos) * (1.0 if s.id % 2 == 0 else -1.0)
            if getattr(s, "_terrain", None) is None:
                from .maps import terrain
                s._terrain = terrain(s.map)
            ok = (lambda q: s._terrain.ground_ok(q.x, q.y, c.pos.z, drop=0.6) and s._terrain.los(c.pos, q))
            if snap.mode == "firefight":
                # the person's teammates: a step behind him, each its own spot, turned round him past a wall or a drop
                # (live s172-s173 Beachhead: median 0.4-1.2 u, sent onto him, the spot behind him in a wall)
                back = c.yaw + math.pi                # ten spots round him 36 degrees apart, behind him first
                turns = sorted((k * math.pi / 5 for k in range(-5, 5)), key=abs)
                good = [q for q in (c.pos + Vec(math.cos(back + a) * ESCORT_TRAIL, math.sin(back + a) * ESCORT_TRAIL, 0.0)
                                    for a in turns) if ok(q)]
                return good[s.id % 3 % len(good)] if good else c.pos
            n = d.flat()
            if n < 0.5:
                return c.pos
            side = 1.5 if s.id % 2 else -1.5          # two escorts: one each side, never one spot
            q = c.pos + Vec(d.x / n * ESCORT_TRAIL - d.y / n * side, d.y / n * ESCORT_TRAIL + d.x / n * side, 0)
            return q if ok(q) else c.pos
        if o.role == "hunter" and snap.mode == "ctf":
            f = (snap.flags or {}).get(me.team)
            if f is not None and f.state != "home":
                return f.pos
        return None

    def stage(s, me, snap, scores, pull, clear_wait=STAGE_CLEAR_WAIT, near=0.0, group=1, wait=STAGE_WAIT, need_clear=True):
        """A runner near an objective to take (their flag on its stand, a loose core or bomb) goes in with company: no
        teammate within STAGE_MATE, it waits (in cover where there is some) up to STAGE_WAIT s for one (lone pulls died
        on the stand within 6 s); with company it waits for a clear spot, STAGE_CLEAR_WAIT s at most (guide 07, Multi
        Flag setup: "the pull starts when at least two enemies are dead or more than Medium band from their flag"; live,
        Boardwalk: 3 of 6 carriers were shot within 5 u of the stand by the defender still on it). With 30 s left it
        goes anyway (One Flag: a failed run and a timeout are the same). True while it waits."""
        # (a wait under way holds until STAGE_OUT past the edge: its cover spot could lie just outside STAGE_D, and the
        # bot then went back and forth across the edge, each crossing a new wait: 18 u from their flag for minutes,
        # Boardwalk sims, one flag run in 560 s)
        out = STAGE_D + (STAGE_OUT if getattr(s, "stage_since", None) is not None else 0.0)
        if (not near < dist(me.pos, pull) < out) or (snap.time_left is not None and snap.time_left < 30):
            s.stage_since = None
            return False
        mates = sum(p.team == me.team and p.alive and p.id != me.id and dist(p.pos, me.pos) < STAGE_MATE
                    for p in snap.players)
        dead = sum(1 for p in snap.players if p.team != me.team and not p.alive)
        clear = dead >= 2 or not any(snap.time - b.t < 4.0 and dist(b.pos, pull) < BAND_EDGES[1]
                                     for k, b in s.per.beliefs.items() if isinstance(k, int))
        if mates and (clear or not need_clear):
            return False              # (the clock runs on: a defender in and out of view never holds it for ever)
        if not need_clear:
            clear_wait = wait         # (alone: it waits for company only, guarded or not)
        if group > 1 and mates >= group:
            return False              # a group goes in together, clear or not (group: the Invasion core's runners)
        s.stage_since = getattr(s, "stage_since", None) or snap.time
        if snap.time - s.stage_since >= (wait if clear else clear_wait):
            return False
        cover = s.cover_from(me, [pull])
        spot = cover if cover is not None and dist(me.pos, cover) < 5.0 and dist(cover, pull) < STAGE_D else me.pos
        for key in [key for key in scores if key[0] == "objective"]:
            scores[key] *= 0.3
        scores[("hold", spot)] = 1.3
        return True

    def keep_role(s, scores, me, snap, seen):
        """Role discipline: a defender stays within DEFEND_R of its post, an escort within ESCORT_R of our carrier, a CTF
        runner on its line to the flag (RUN_DETOUR). Fights, hunts and items past that lose weight by the tier's
        teamwork (Normal 0.55, Legendary 0.95; Easy none), unless the man is on us or the gun is dry; away from it,
        getting back outranks them. Live, Boardwalk CTF: defenders spent 0-7% of their defending time at home."""
        o, k = s.order, s.p.teamwork
        if k <= 0 or me.has_flag:
            return
        goal_pt, firm = None, None
        core_rush = s.urgent(me, snap)
        # (in the team's blitz: director.blitz_tick)
        urgent = core_rush or s.pressing(me, snap) or getattr(s, "blitz_until", 0.0) > snap.time
        if o.role == "hunter" and snap.mode == "invasion" and o.point is not None and snap.inv_attackers != me.team:
            # the pile-on: a defender sent at the core's carrier or at a territory being taken goes there, not after
            # other men. The carrier where he is now, not where the order saw him
            c = snap.player(snap.inv_core_carrier) if snap.inv_core_carrier is not None else None
            goal_pt = c.pos if c is not None and c.alive and c.team != me.team else o.point
            strays, away = (lambda q: dist(q, goal_pt) > HUNT_R), dist(me.pos, goal_pt) > 0.5 * HUNT_R
        elif o.role == "screen" and o.point is not None:
            # a carry's screen (director.role_slots): it holds its spot between our carrier and their base, the chase
            # fought there, nothing far off it
            goal_pt = o.point
            strays, away = (lambda q: dist(q, goal_pt) > SCREEN_R), dist(me.pos, goal_pt) > 0.5 * SCREEN_R
        elif o.role == "defender" and o.point is not None:
            post = o.point
            # an Invasion attacker taking a territory has to stand in it (brain/invasion.py TERRITORY_R)
            r = DEFEND_R
            king = snap.mode == "king" and snap.hill is not None and dist(post, snap.hill) < 0.5
            if snap.mode == "invasion" and snap.inv_attackers == me.team and snap.inv_phase in (1, 2):
                r = min((zr for z, zr in zip(snap.inv_zones, snap.inv_zone_r) if dist(z, post) < 3.0), default=2.5)
            elif snap.mode in ("territories", "infection"):     # a zone is held from inside it (brain/modes.py)
                r = min((zr for z, zr, o in snap.obj_zones if dist(z, post) < 3.0), default=DEFEND_R)
            elif king:          # the scorer holds the hill from inside it (guide 19 KOTH-STATIC-01/02), not 15 u round it
                r = snap.hill_r or DEFEND_R
            strays, away = (lambda q: dist(q, post) > r), dist(me.pos, post) > 0.5 * r
            if snap.mode == "ctf":
                # its guns and hunts stay in its area, 10 u round its post, at every tier that keeps a role (live
                # Boardwalk, Normal / Heroic: at three of four pulls no defender was home, off 19-31 u after a gun or
                # a radar dot; the teamwork leash took off only 38% for Normal)
                firm = lambda q: dist(q, post) > 10.0
            elif king:
                firm = strays   # (guns and hunts outside the hill: a quarter)
                # KOTH-STATIC-10: "Never push one at a time. A lone pusher dies to the RING players": a man of theirs in
                # the hill, and it waits outside for company, as a CTF pull (stage)
                if any(isinstance(k, int) and snap.time - b.t < 4.0 and dist(b.pos, post) < r
                       for k, b in s.per.beliefs.items()):
                    # (with company it goes in, a hill held by theirs is never "clear": the sim's Heroic and Legendary
                    # waited outside while Easy stood on it, docs/oct31/sims.md)
                    if s.stage(me, snap, scores, post, near=r, need_clear=not KING_TAKE_IN):
                        return
                else:
                    s.stage_since = None
        elif o.role == "hunter" and snap.mode == "ctf" and s.live_point(me, snap) is not None:
            # our flag is out: its returners stay on it (the carrier, or the dropped flag), as the Invasion pile-on;
            # with no leash any committed fight or item beat the return
            goal_pt = s.live_point(me, snap)
            strays, away = (lambda q: dist(q, goal_pt) > HUNT_R), dist(me.pos, goal_pt) > 0.5 * HUNT_R
        elif o.role == "escort" and s.lead(me, snap) is not None:
            c = s.lead(me, snap).pos
            strays, away = (lambda q: dist(q, c) > ESCORT_R), dist(me.pos, c) > 0.5 * ESCORT_R
        elif o.role in ("mid", "escort") and o.point is not None and s.pressing(me, snap):
            # an attacker covering the territory from around it (director.invasion_slots: the overlooks, the escorts with
            # nobody to escort) keeps to its spot as the ones in it do: it had no leash, and items won its choices
            # (match 214711 at 295-303 s: a mid's item 1.19 over its objective 0.70)
            goal_pt = o.point
            strays, away = (lambda q: dist(q, goal_pt) > PRESS_COVER_R), dist(me.pos, goal_pt) > 0.5 * PRESS_COVER_R
        elif o.role == "runner" and snap.mode == "ctf" and o.point is not None:
            # a CTF runner keeps to its line to the flag (live: in 150 s no runner came within 8 u of their stand,
            # every closest approach a side fight or an item): RUN_DETOUR more than the straight way strays
            goal = s.objective_point(me)
            straight = dist(me.pos, goal)
            strays, away = (lambda q: dist(me.pos, q) + dist(q, goal) - straight > RUN_DETOUR), True
            theirs = (snap.flags or {}).get(1 - me.team % 2)
            if theirs is not None and theirs.state == "home" and dist(me.pos, theirs.pos) >= GRAB_R and \
                    s.stage(me, snap, scores, theirs.pos, need_clear=STAGE_NEED_CLEAR):
                away = False
            elif theirs is None or theirs.state != "home":
                s.stage_since = None
            if theirs is not None and theirs.state != "carried" and dist(me.pos, theirs.pos) < GRAB_R:
                # at the flag: no backing off to cover a step from it (GRAB_R)
                for key in [key for key in scores if key[0] == "retreat"]:
                    scores[key] *= 0.3
        elif o.role == "runner" and snap.mode == "invasion" and s.grab_point is not None:
            # the core or Breakpoint's bomb: the CTF runner's line to it, and the same pull rule, shorter (live: both
            # Breakpoint bomb carriers died 17 s on, chased by its stand's defenders; on Spire the defenders respawn on
            # the core's platform and 20 s holds 5 u off it lost the phase), never within 4 u. Without the line it
            # played free: live Spire the core lay loose 176 s and no runner came within 4.6 u of it, every nearest
            # approach a fight, a gun or a vehicle; the sim, 0 pickups in 4 x 150 s
            goal = goal_pt = s.grab_point
            straight = dist(me.pos, goal)
            strays, away = (lambda q: dist(me.pos, q) + dist(q, goal) - straight > RUN_DETOUR), True
            # (still held: three go in together, 10 s at most to gather; one by one they died on its campers)
            if s.stage(me, snap, scores, goal, clear_wait=10.0, near=4.0, group=2, wait=10.0):
                away = False
        else:
            return
        att = getattr(s.per, "attacker_id", None)
        # (the last man who shot it, only for 3 s: the id stayed until death, and defenders, escorts and territory
        # holders chased him across the map: review)
        fresh_hit = snap.time - getattr(s.per, "attacker_t", -99.0) < 3.0
        fetch = getattr(s, "fetch", None)
        for key in list(scores):
            if key in getattr(s, "mission_keys", ()) or (fetch is not None and key == ("item", fetch[0])):
                continue              # the Grenadier's anti-vehicle mission, a power weapon run: outrank its post
                                      # (an attacker on the clock is sent only for one on its way: director.press_fetch_ok)
            kind, arg = key
            if kind == "fight":
                b = s.per.beliefs.get(arg)
                if b is None or (arg == att and fresh_hit) or (arg in seen and dist(me.pos, b.pos) < (4.0 if urgent else 8)):
                    continue          # on us, or right here: fight back wherever (the core at stake: only at arm's length)
                q = b.pos
            elif kind in ("hunt", "item", "hold") and isinstance(arg, Vec):
                if (kind == "item" and s.dry(me)) or (kind == "hunt" and (any(
                        b.source == "hit" and b.pos == arg for b in s.per.beliefs.values())
                        or (fresh_hit and att in s.per.beliefs and s.per.beliefs[att].pos == arg))):
                    continue          # out of rounds, or shot by a man not seen yet: that first (also one the radar
                                      # already showed: its update took the belief's "hit" mark within 0.7 s, and the
                                      # turn to him lost to the post, live Spire: 9 of 23 unanswered hits)
                q = arg
            else:
                continue
            if kind == "item" and dist(me.pos, q) < UPGRADE_ON_WAY and not core_rush and not s.out_of_held(me, snap, q):
                continue              # a gun on its way (a step or two): taken whatever its post (an attacker too)
            if kind == "item" and not urgent and dist(me.pos, q) < POWER_NEAR and s.power_spawn(q):
                continue              # a power weapon up near by (best_item: up by arrival): worth the step off its post
                                      # (live: power spawns won 6-13% of the decisions they showed in, the post's 1 -
                                      # 0.7 teamwork cut them to about 0.3; logs/tmp/power_items.py)
            if firm is not None and kind in ("item", "hunt") and firm(q):
                scores[key] *= 0.25
            elif strays(q):
                scores[key] *= 0.15 if urgent else 1.0 - 0.7 * k
        if away:
            pt = goal_pt or s.live_point(me, snap) or s.objective_point(me)
            scores[("objective", pt)] = max(scores.get(("objective", pt), 0.0), 1.2 + 0.6 * k)

    @staticmethod
    def urgent(me, snap):
        """Invasion phase 3, defending: the core, on its stand or carried, is the whole job. Its post or its carrier
        outranks every fight but a man shooting it or at arm's length (keep_role), and no trip goes by vehicle (choose).
        Match 221404: at 383 s five of eight Elites fought attackers at the base, at 390 s four went for a Banshee there
        while he took the core; three ever came near him."""
        return snap.mode == "invasion" and snap.inv_phase == 3 and snap.inv_core is not None and snap.inv_attackers != me.team

    @staticmethod
    def pressing(me, snap):
        """Invasion, attacking, every phase: the territory being taken or the core is the whole job, as the defenders' core
        is theirs (keep_role: only a man shooting it or at arm's length is fought off its line, side trips a seventh).;
        "Spartans have no urgency at all to get to the top of the spire". Unlike urgent, it leaves the vehicles (a ride
        up is a way to the core) and the power weapon runs to the attackers. One Flag, attacking (the round's only flag
        theirs): the same (live Avalanche, round 1: the attackers spent 57% of their runner time 40-50 u off the flag by
        their own base, on vehicles, items and side fights)."""
        if snap.mode == "ctf" and ONE_FLAG_PRESS:
            f = snap.flags or {}
            return len(f) == 1 and me.team not in f
        return snap.mode == "invasion" and snap.inv_phase in (1, 2, 3) and snap.inv_attackers == me.team

    def enemy_vehicle(s, now, me, snap):
        """The nearest enemy vehicle this bot knows of: an enemy it saw or was told about (its beliefs, within its
        memory) who rides one. (where, his id) or None."""
        best = None
        for k, b in s.per.beliefs.items():
            if not isinstance(k, int) or now - b.t > s.p.memory:
                continue
            e = snap.player(k)
            if e is None or not e.alive or e.vehicle == -1 or e.team == me.team:
                continue
            d = dist(me.pos, b.pos)
            if best is None or d < best[0]:
                best = (d, b.pos, k)
        return (best[1], best[2]) if best else None

    def anti_vehicle_gun(s, me):
        """Does it hold a loaded gun that kills vehicles (ANTI_VEHICLE; unknown ammo counts as loaded)?"""
        return any(w in ANTI_VEHICLE and (i >= len(me.ammo) or me.ammo[i] != 0) for i, w in enumerate(me.weapons))

    def explosive_source(s, now, me, snap):
        """Where to get a gun that kills vehicles: one lying on the ground in sight or close (snap.ground_weapons), or
        a spawn the bot knows (the map's items, by its map knowledge and the respawn timers best_item keeps). The
        nearest; None when it knows of none."""
        out = []
        for name, q in getattr(snap, "ground_weapons", None) or ():
            if name in ANTI_VEHICLE and (dist(me.pos, q) < 12 or s.T().los(eye(me), q + Vec(0, 0, 0.2))):
                out.append((dist(me.pos, q), q, name))
        if s.p.map_iq >= 0.15:                      # Easy knows no weapon spawns, only what it sees
            for i, it in enumerate(s.map.get("items", [])):
                if (it["name"] in ANTI_VEHICLE and it.get("reachable", True) and now >= s.item_skip.get(i, 0.0)
                        and s.item_timers.get(i, 0.0) <= now + dist(me.pos, it["pos"]) / 2.3):
                    out.append((dist(me.pos, it["pos"]), it["pos"], it["name"]))
        return min(out, key=lambda x: x[0]) if out else None

    def vehicle_mission(s, now, me, snap, scores):
        """The Grenadier's job when an enemy vehicle is about: without a gun that kills vehicles it goes for the nearest
        one it knows of (a launcher on the ground or a spawn); with one it hunts the vehicle (threat puts the crew
        first, choose_weapon brings the launcher out, the grenade logic sticks the vehicle up close). It outranks
        starting a new fight and its role's post (keep_role), not a fight it is in or a retreat to cover. The flag
        carrier keeps carrying."""
        s.mission_keys = ()
        if me.has_flag or me.vehicle not in (None, -1):    # (in a seat: no walk to a launcher)
            return
        v = s.enemy_vehicle(now, me, snap)
        if v is None:
            return
        # an enemy tank is everyone's job, Normal and up: a launcher in hand goes after it, one lying within
        # TANK_FETCH_R is fetched; the Grenadier as before for any vehicle
        e = snap.player(v[1])
        ev = next((x for x in snap.vehicles if e is not None and x["handle"] == e.vehicle), None)
        tank = ev is not None and vehicles.kind_of(ev["kind"]) in vehicles.TANKS and s.p.tactics > 0.15
        if s.p.personality != "spammer" and not tank:
            return
        if s.anti_vehicle_gun(me):
            # in sight: fight its crew (threat() puts them first); out of sight: go where it was last seen
            key = ("fight", v[1]) if v[1] in s.per.visible_enemies() else ("hunt", v[0])
            scores[key] = max(scores.get(key, 0.0), 2.3)
            s.notes.append("anti-vehicle: " + ("taking it out" if key[0] == "fight" else "hunting it"))
        else:
            src = s.explosive_source(now, me, snap) if getattr(s, "can_pickup", True) else None   # (H2A / H4: no pickups yet)
            if src is None or (s.p.personality != "spammer" and src[0] > TANK_FETCH_R):
                return
            key = ("item", src[1])
            scores[key] = max(scores.get(key, 0.0), 2.4)
            s.notes.append(f"anti-vehicle: going for the {src[2]}")
        s.mission_keys = (key,)

    def break_stalemates(s, now, me, snap, scores):
        """A fight past 6 u where nobody's shield or health has moved for STALE_S: the shots do not land (glass, a
        rail the level data lacks). It drops to a quarter of its weight so the bot's role or a better angle wins.
        Live, Boardwalk CTF: a runner shot at a man 11.8 u off and 3 u up for 40 s, neither hurt (dry_fire_check
        learns such a wall only on its third dry episode, ~30 s of fire)."""
        mine, tracks = me.shield + me.health, s.__dict__.setdefault("stale", {})
        for key in [k for k in scores if k[0] == "fight"]:
            t, b = snap.player(key[1]), s.per.beliefs.get(key[1])
            if t is None or not t.alive or b is None:
                continue
            hp, rec = t.shield + t.health, tracks.get(key[1])
            if rec is None or not b.seen or now - rec[3] > 2.0 or hp < rec[1] - 0.01 or mine < rec[2] - 0.01:
                # new, back after a break, someone got hurt, or out of sight (the committed chase: nobody can be hurt)
                tracks[key[1]] = [now, hp, mine, now]
                continue
            rec[1:] = [hp, mine, now]
            if now - rec[0] > STALE_S and dist(me.pos, b.pos) > 6.0:
                scores[key] *= 0.25

    def on_death(s, killer_id):
        s.last_killer = killer_id
        # a new life picks its route again (the director's pick_route, which steers off where the team keeps dying):
        # kept, a runner took the lane it first drew every life of the match (the director keeps a live runner's route),
        # one lane all game on the three-lane maps (Lockout, Island, Guardian)
        s.order = Order(s.order.role, s.order.point, s.order.target)
        s.via_left, s.prong_passed = [], False      # (a blitz's prong again from the respawn: director.prong_via)
        s.per = Perception(s.p, s.rng)
        s.aim = Aim(s.p, s.rng)
        s.engaged_since = None

    # --- per tick
    def think(s, now, dt, snap, los, nav, callouts, items=None):
        me0 = snap.player(s.id)
        haz = s.kit.hazards(me0, snap) if me0 is not None and getattr(snap, "deployed", None) else ()
        if haz:   # an enemy trip mine or a power drain out: every way this tick goes round it (GitHub #9, Terrain.nav)
            nav = (lambda nav0: lambda a, b, **kw: nav0(a, b, hazards=haz, **kw))(nav)
        act = s.plan(now, dt, snap, los, nav, callouts, items)
        me = snap.player(s.id)
        if act.melee and me is not None and me.alive and me.vehicle in (None, -1):
            s.melee_check(me, snap, act)
        if me and me.alive:
            s.turn_cap(me, act, dt)
        return act

    def turn_cap(s, me, act, dt):
        """No view turns quicker than the profile's turn speed; the walk is re-aimed from the view it wanted to the view it
        gets, so it still goes where it meant to."""
        dy, dp = wrap(act.yaw - me.yaw), act.pitch - me.pitch
        most = s.p.turn * max(dt, 1e-3)
        turn = math.hypot(dy, dp)
        if turn <= most:
            return
        k = most / turn
        act.yaw, act.pitch = me.yaw + dy * k, me.pitch + dp * k
        if me.vehicle != -1:
            return                             # (a vehicle's throttle is not a walk: it steers toward the view)
        lag = dy * (1.0 - k)                   # how far the view falls short of the one the walk was planned in
        c, sn = math.cos(lag), math.sin(lag)
        f, st = act.forward, act.strafe
        act.forward = max(-1.0, min(1.0, f * c - st * sn))
        act.strafe = max(-1.0, min(1.0, f * sn + st * c))

    DOM_BASE_SYSTEM = True      # the Dom_base capture (brain/dom_base.py); False: the old dom_act alone

    def plan(s, now, dt, snap, los, nav, callouts, items=None):
        me = snap.player(s.id)
        if s.DOM_BASE_SYSTEM and me is not None and dom_base.holds(s.id):
            # Locked on a base last tick: standing still on purpose. The planner still runs (its perception keeps
            # updating) but must not read the stillness as being stuck: that wiggled, backed off and taught the nav
            # walls that are not there (unstuck: shuffle_until is its "in place on purpose" exemption).
            s.shuffle_until = now + 0.2
            s.stuck_since = s.stuck_pos = None
            s.wiggle_until = s.backoff_until = 0
        act = s._plan(now, dt, snap, los, nav, callouts, items)
        me = snap.player(s.id)
        if me is None:
            return act
        if s.DOM_BASE_SYSTEM and snap.mode == "territories" and getattr(snap, "dom_orders", None) is not None:   # (Dominion only)
            o = getattr(s, "dom", None)
            s.dom_hold = None                               # (the old committed hold is not used)
            held = dom_base.step(s.id, s.team, now, me, snap, act, lambda a, p: s.take(me, a, p), s.DOM_TERMINAL_Z)
            if held is not None:
                s.notes.append(f"dominion: Dom_base capture {held:.1f}/{dom_base.DOM_BASE_SETTLE_S + dom_base.DOM_BASE_HOLD_S:.1f} s")
                return act
            ended = dom_base.ENDED.get(s.id)
            if ended and ended[0] == now:
                s.notes.append(f"dominion: capture ended after {ended[1]:.1f} s: {ended[2]}")
            if o and o["kind"] == "capture" and o["clear"]:
                return s.dom_act(now, me, snap, act)        # only the shooting-down of a base's turrets / shields
            if o and o["kind"] == "capture":
                d = (me.pos - o["pos"]).flat()
                if dom_base.DOM_BASE_RADIUS < d <= 8.0:
                    s.notes.append(f"dominion: outside the capture radius ({d:.1f} u)")
            return act
        return s.dom_act(now, me, snap, act) if getattr(s, "dom", None) else act

    def dom_order(s, me, snap):
        """Halo 4 Dominion (brain/modes.py dominion_orders; snap.dom_orders, one order per bot, every tick): where this bot
        goes. Capture and defend both stand AT the base's terminal (the order's pos; the zone radius is the use reach, so
        the defender code keeps it right there). A base with the other team's turrets or shields still up
        (order["clear"]): stand off 12 u from the nearest, and dom_act shoots it down first. Sets s.dom (None: no order)."""
        s.dom = None
        d = getattr(snap, "dom_orders", None)
        if snap.mode != "territories" or not d or me.id not in d or me.vehicle not in (None, -1):
            return
        o = s.dom = d[me.id]
        pt = o["pos"]
        if o["clear"]:
            fp = o["clear"][0][1]
            far = Vec(me.pos.x - fp.x, me.pos.y - fp.y, 0.0)
            if far.flat() > 15.0:
                pt = fp + far.norm() * 12.0
            else:                                   # near enough: stay where it is (not into the base)
                keep = s.order.point if s.order.role == "defender" and s.order.point is not None else None
                pt = keep if keep is not None else me.pos
        if s.order.role != "defender" or s.order.point is None or dist(s.order.point, pt) > 0.3:
            s.order, s.via_left = Order("defender", pt), []

    DOM_REACH, DOM_TERMINAL_Z = 1.0, 1.0      # u from the terminal; aim at the upright console, not its floor-level origin

    def dom_act(s, now, me, snap, act):
        """Dominion's hands (after the plan). A man of theirs in sight within 10 u first: the fight keeps the aim (5 u
        once at the terminal: the hold is finished unless he is that close, or we are dying). Then:
          clear: the other team's turret or shield at that base is shot down before going in;
          capture: the last steps to the terminal are walked here (the defender post stops short), and at DOM_REACH (1.0 u)
                   the bot STANDS STILL and takes the base the way it takes a bomb or a flag, Bot.take (Use held, the
                   view on it), continuously until the base changes hands or the bot dies. Do not pulse or retry Use: Halo 4's decoded Action mapping is a hold (press=false)."""
        from .modes import DOM_HOLD_S
        o = s.dom
        if not me.alive or not o or o["zone"] >= len(snap.obj_zones or []):
            return act
        pos = o["pos"]
        if snap.obj_zones[o["zone"]][2] == me.team:
            s.dom_hold = None                               # ours: done
            return act
        # Once Use has started, capture is a committed interaction: do not let the combat planner,
        # nearby enemies, or movement recovery cancel it. Keep sending a stationary Use action until
        # the base changes hands (checked above) or this bot dies (checked at the top).
        hold = getattr(s, "dom_hold", None)
        if o["kind"] == "capture" and hold and hold["zone"] == o["zone"] and not o["clear"]:
            from .modes import DOM_HOLD_S
            held = now - hold["t"]
            aim = pos + Vec(0, 0, s.DOM_TERMINAL_Z)
            act.forward = act.strafe = 0.0
            act.jump = act.fire = act.fire2 = act.grenade = act.melee = False
            act.reload = act.equipment = act.crouch = act.sprint = False
            act.cycle = act.turret = False
            s.take(me, act, aim)                         # keep Use held; no timed release/re-press
            act.forward = act.strafe = 0.0
            s.notes.append(f"dominion: capture locked {held:.1f} s")
            return act

        near = (me.pos - pos).flat() <= s.DOM_REACH + 0.25 and abs(me.pos.z - pos.z) < 2.0
        try:
            foe = min((dist(me.pos, s.per.beliefs[k].pos) for k in s.per.visible_enemies()), default=99.0)
        except Exception:
            foe = 99.0
        dying = me.health < 0.5 and (me.shield < 0.05 or snap.shieldless)
        if o["kind"] != "capture" or foe < (5.0 if near else 10.0) or dying:
            s.dom_hold = None
            return act
        if o["clear"]:
            s.dom_hold = None
            fp = o["clear"][0][1] + Vec(0, 0, 0.4)
            seen = False
            try:
                seen = s.per.los(eye(me), fp, "shot")        # (a round's line: Perception.los)
            except Exception:
                pass
            if dist(me.pos, fp) <= 22.0 and seen:
                act.yaw, act.pitch = angles_to(eye(me), fp)
                act.fire = True
                s.notes.append("dominion: shooting the base's " + o["clear"][0][0])
            return act
        aim = pos + Vec(0, 0, s.DOM_TERMINAL_Z)
        d = (me.pos - pos).flat()
        if d > s.DOM_REACH:
            s.dom_hold = None
            if d <= 4.0:                                    # the last steps, straight at it, slowly
                act.yaw, act.pitch = angles_to(eye(me), aim)
                act.forward, act.strafe, act.fire, act.jump = 0.6 if d > 1.6 else 0.35, 0.0, False, False
                s.notes.append("dominion: closing on the terminal")
            return act
        # at the terminal: stop, aim at it, and hold Use continuously until ownership changes
        hold = getattr(s, "dom_hold", None)
        if not hold or hold["zone"] != o["zone"]:
            hold = s.dom_hold = {"zone": o["zone"], "t": now}
        held = now - hold["t"]
        act.forward = act.strafe = 0.0
        act.jump = act.fire = False
        s.take(me, act, aim)                                # Use held continuously, no release/re-press cycle
        act.forward = act.strafe = 0.0                      # (take keeps the walk under the turned view: none here)
        s.notes.append(f"dominion: capturing {held:.1f} s")
        return act

    def _plan(s, now, dt, snap, los, nav, callouts, items=None):
        me = snap.player(s.id)
        act = Action(yaw=me.yaw, pitch=me.pitch)
        s.item_timers = items or {}
        s.now = now
        s.vehicles_now = getattr(snap, "vehicles", ())     # (move_to: an Elephant's bay, way_in)
        s.assaulting = snap.mode == "invasion" and snap.inv_phase == 3 and snap.inv_attackers == me.team   # (exit_held)
        s.mates_now = [p.pos for p in snap.players if p.team == me.team and p.alive and p.id != s.id]   # (exit_watched)
        s.carrying_now = me.has_flag
        s.planned_jump = False
        s.notes = []                # what overrode the plan this tick (edge guard, unstuck): tools/observer.py
        s.att = None                # what it looks at and why, [kind, what, x, y] (brain/tracefmt.py)
        s.fx = None
        s.nav_cmd = None
        s.shot_now = False          # a round left the gun this tick (a held charge is not a shot: dry_fire_check)
        if not me.alive:
            s.charge_since = s.on_since = None
            return act
        if snap.mode == "race" and me.id in snap.race_next:
            # Race (brain/modes.py Race): always this racer's own next gate, read every tick, at the wheel too (the
            # director's role pass is every 2 s: a gate just touched would be driven back to)
            g = snap.race_next[me.id]
            if s.order.role != "runner" or s.order.point is None or dist(s.order.point, g) > 0.1:
                s.order, s.via_left = Order("runner", g), []
            s.drive_to = g
        # Firefight: a bot goes where it likes. The director's escort / defender / hunter orders (a step behind the person,
        # a post) are dropped every tick, so with nothing else to do it roams the map (roam: away from where enemies were
        # last seen). Vehicle orders (a honk: ride, board, drive) are the person's own ask and stay.
        s.ff_free = snap.mode == "firefight"
        if s.ff_free and s.order.role not in ("free", "ride", "board", "drive"):
            s.order, s.via_left = Order("free"), []
        s.dom_order(me, snap)               # Dominion: this bot's capture / defend job, every tick
        prev = getattr(s, "last_pos", None)
        s.last_pos, s.last_weapon = me.pos, me.weapon      # for how this life ends (brain/loadouts.py)
        if prev is not None and (me.pos - prev).flat() > 4.0:
            s.came_through(now, me, prev)                  # out of a teleporter: watch its exit
        # an objective lying loose for us to take, or ours lying dropped to return by standing on it (move_to's last steps)
        s.grab_point = s.loose_point(me, snap)
        ret = s.return_point(me, snap) if s.grab_point is None else None
        if ret is not None:
            s.grab_point = ret
        g = s.grab_point if snap.mode != "race" and not me.has_flag and me.vehicle in (None, -1) else None
        s.stay_at = g if g is not None and (me.pos - g).flat() < STAY_R and abs(me.pos.z - g.z) < 2.0 else None
        s.walk_onto = ret if ret is not None else s.stay_at    # walked onto, not loitered round (plan, the objective)
        s.kit.perceive(s, now, me, snap)                   # flares, radar static, decoys, enemy abilities
        s.per.blind, s.per.jammed = now < s.kit.blind_until, now < s.kit.jammed_until
        # sides that change in a match (Halo 3 Juggernaut: a new juggernaut, everyone else allied against him;
        # Infection): a man now on our side is no target, nor a memory to hunt (live: at 60 s every bot still fought the
        # man it had fought before the juggernaut changed, its foes list only the juggernaut)
        allies = {p.id for p in snap.players if p.team == me.team and p.id != me.id}
        for k in [k for k in s.per.beliefs if k in allies]:
            del s.per.beliefs[k]
        if s.goal[0] == "fight" and s.goal[1] in allies:
            s.goal, s.next_decide = ("roam", None), now
        busy = s.goal[0] in ("fight", "retreat")
        for e in s.per.update(now, me, snap, los, callouts):
            s.reports.append(("seen", e.id, e.pos, now, busy))
            if e.has_flag:
                s.reports.append(("flag_carrier", e.id, e.pos, now, busy))
        line = getattr(s.per, "shot_line", None)
        if line is not None and line[2] == now and s.map is not None:
            s.T().learn_shot_through(line[0], line[1])   # his round reached us through what the data calls a wall: a
            s.per.shot_line = None                       # fence it does not name (Terrain.learn_shot_through)
        if now >= s.next_decide:
            s.next_decide = now + s.p.decide * s.rng.uniform(0.8, 1.3)
            was, s.goal = s.goal, s.decide(now, me, snap)
            if s.goal[0] == "fight" and s.goal != was:
                s.target_at = now              # a new target: held TARGET_HOLD s (pick_target)
        kind, arg = s.goal
        if isinstance(arg, Vec) and kind in ("objective", "item", "hunt", "hold"):
            s.headway(now, me, (kind, arg), snap)
        else:
            s.way_key = None        # fighting, driving: the clock starts again when we travel
        if me.vehicle != -1:
            s.drive(now, dt, me, snap, act, nav)
            s.air_guard(me, act, snap)
            s.air_roof_guard(me, act, snap)
            s.fire_gate(now, me, act, snap)
            return act
        # on foot: the next seat is a new one, in the same vehicle too (H2A Bloodline s133: out of a Warthog's gun with
        # nobody driving, back in at the wheel, it kept "gunner" and its old seat clock and was out again at once, 162
        # exits in 9 minutes)
        s.seat_for = None
        if kind == "vehicle":
            v = next((v for v in snap.vehicles if v["handle"] == arg), None)
            want = {"ride": "gunner", "board": "passenger"}.get(s.order.role, "driver")   # (board: a passenger seat)
            # (a teammate's on its side or roof is no ride either, nor a wreck or a dud: vehicles.usable)
            if v and s.dud_check(now, dt, me, v) and (not v["occupied"] or (want != "driver" and v.get("team") == me.team)):
                # (the other side's door every 3 s it does not get in: a Falcon's second gun)
                vp = s.seat_point(v, want, side=1 if getattr(s, "enter_tries", 0) // 90 % 2 == 0 else -1)
                s.move_to(me, vp, act, nav)
                # looking where it goes, the seat at the end (the view stayed where it was: live Boneyard, a bot walked
                # 6.4 s to a vehicle looking 168 degrees away). A man it watches stays within LOOK_CONE of the way, and
                # a step 60+ degrees off the view slows while the view comes round, as on any walk (watching freely,
                # Normal to Legendary walked to vehicles 13-22% of their walking time looking 60+ degrees off their
                # way, Spire King and High Ground, logs/tmp/look_intent.py; the player 9% in all)
                look = s.look_point(me, vp, nav)
                if (math.hypot(act.forward, act.strafe) > 0.5 and s.att and s.att[0] in ("watch", "post", "flank")
                        and not str(s.att[1]).endswith("hit") and s.att[1] != "teleporter"):
                    look = s.walk_cone(me, look, act.yaw + math.atan2(act.strafe, act.forward))
                act.yaw, act.pitch = s.aim.step(now, dt, me, None, look, Vec(), False)
                if (abs(math.atan2(act.strafe, act.forward)) > math.radians(60) and not s.notes
                        and abs(act.forward) + abs(act.strafe) > 0.1):
                    act.forward, act.strafe = act.forward * 0.3, act.strafe * 0.3
                s.enter_riders = v.get("riders", 0)     # who was in it: tells our seat once in (drive)
                s.enter_want = want                     # the seat it asks for (drive: where that is the one it gets)
                if dist(me.pos, vp) < 1.6 or dist(me.pos, Vec(*v["pos"])) < 2.2:
                    act.action = int(now * 6) % 2 == 0   # tap action until in: only presses count
                    # which seat (Reach: native fills the enter-seat interaction; a bot's own is empty, so Use alone
                    # entered nothing)
                    s.enter_tries = getattr(s, "enter_tries", 0) + 1
                    seat = vehicles.reach_seat(v["kind"], want, s.enter_tries // 90)   # the next seat every 3 s
                    if seat:
                        act.seat, act.turret = seat
                elif s.vehicle_stalled(now, me, v, vp):
                    s.next_decide = now
                    s.notes.append("vehicle: no way to it, given up")
            else:
                s.next_decide = now          # taken or gone: think again
            s.unstuck(now, me, act, snap)
            return act
        if kind == "board":                  # an enemy's vehicle (board_check): to its driver's seat, Use there
            v = next((v for v in snap.vehicles if v["handle"] == arg), None)
            if v is None or not v.get("occupied") or v.get("team") == me.team or not vehicles.usable(v):
                s.next_decide = now
                return act
            vp = s.seat_point(v, "driver")
            f = v.get("fwd")
            if vehicles.kind_of(v["kind"]) in vehicles.TANKS and f and math.hypot(f[0], f[1]) > 0.1:
                n = math.hypot(f[0], f[1])    # a tank from its front (the player's board, BOARD_FRONT)
                vp = Vec(v["pos"][0] + f[0] / n * BOARD_FRONT, v["pos"][1] + f[1] / n * BOARD_FRONT, v["pos"][2])
            s.move_to(me, vp, act, nav)
            act.yaw, act.pitch = s.aim.step(now, dt, me, None, Vec(*v["pos"]), Vec(), False)
            if dist(me.pos, vp) < 1.8 or dist(me.pos, Vec(*v["pos"])) < 2.6:
                act.action = int(now * 6) % 2 == 0
                # Use beside it that does not take within BOARD_GIVE_UP: that vehicle is left alone BOARD_BAN s (live
                # Avalanche 064500: #7 pressed Use 1.3 u from a Warthog's seat 200 s, its driver fleeing into a wall
                # the whole time)
                t = getattr(s, "board_try", None)
                if t is None or t[0] != arg:
                    t = s.board_try = (arg, now)
                # (Reach: its boarding seats, a second each; the other games' seat numbers are not Reach's)
                seat = vehicles.reach_seat(v["kind"], "board" if s.game == "reach" else "driver", int(now - t[1]))
                if seat:
                    act.seat, act.turret = seat
                if now - t[1] > BOARD_GIVE_UP:
                    s.__dict__.setdefault("board_ban", {})[arg] = now + BOARD_BAN
                    s.board_try, s.next_decide = None, now
                    s.note(f"board: the {v['kind']} would not take me, leaving it")
            s.unstuck(now, me, act, snap)
            return act
        if kind == "fight":
            s.fight(now, dt, me, snap, arg, act, nav)
        elif kind == "retreat" and s.per.visible_enemies():
            # back off toward safety (a wall between us and them if LiDAR found one) while still
            # shooting at the closest threat; by the nav, a straight line can run into a wall
            t = min(s.per.visible_enemies(), key=lambda k: dist(me.pos, s.per.beliefs[k].pos))
            s.fight(now, dt, me, snap, t, act, nav)
            yaw = act.yaw
            s.move_to(me, arg, act, nav)
            act.yaw = yaw
            act.strafe = act.strafe * 0.6 + s.strafe_dir * 0.4
        else:
            s.engaged_since = s.charge_since = s.on_since = None
            point, post = (arg if isinstance(arg, Vec) else s.roam(me)), None
            point = s.detour(now, me, kind, point)
            small = None                         # the radius of a zone too small to loiter in (below)
            # (a CTF hunter neither: it chases a carrier, or stands on our dropped flag to return it). A runner whose
            # objective is a place to hold (a zone, the hill) works it too: Easy's runners stood on their Invasion
            # territory's middle with no input 44% of their objective time, standing 0.28 to the player's 0.13
            # (Spire); the core, a flag or a bomb is still run at and taken
            held = s.zone_at(snap, point) is not None or (snap.hill is not None and dist(point, snap.hill) < 0.5)
            # (nor a defender going to the dropped core to return it, or one on an objective it stays on: stood on, not
            # loitered round 2.5 u off; live Spire 20261105-143525, the core's defender 1.15-1.36 u off it)
            wo = getattr(s, "walk_onto", None)
            onto = wo is not None and isinstance(arg, Vec) and dist(arg, wo) < 1.0
            if kind == "hold" or (kind == "objective" and (s.order.role not in ("runner", "escort") or held)
                                  and not me.has_flag and not (s.order.role == "hunter" and snap.mode == "ctf")
                                  and not onto):
                on_hill = snap.hill is not None and dist(point, snap.hill) < 0.5      # (the hill holder stays inside it)
                wander = 0.6 * snap.hill_r if on_hill else (4.0 if snap.mode == "slayer" else 2.5)
                if snap.mode == "infection" and not snap.obj_zones:
                    wander = 1.0       # in the hold's room (director: a few entrances), not out of its door
                # a zone to stand in (Invasion territories, Territories, havens): inside its own size. Boneyard's are
                # boxes about 0.9 u from the centre to the edge; 2.5 u of loitering kept attackers outside (live)
                zr = s.zone_at(snap, point)
                if zr is not None and (snap.mode != "invasion" or snap.inv_attackers == me.team):
                    wander = 0.6 * zr
                    small = zr if zr < 1.6 else None
                if kind == "objective" and s.order.role == "defender" and snap.mode == "ctf":
                    post, point = point, s.post_loiter(now, me, point)
                else:
                    post, point = point, s.loiter(now, me, point, wander)
            elif kind == "retreat":
                # recharging in cover: watch out of it, not the wall it hides behind
                post = point
            s.move_to(me, point, act, nav)
            gate = s.tele_watching(now)
            if gate is not None and (me.pos - gate).flat() >= 2.0:
                act.forward = act.strafe = 0.0      # off the teleporter's exit: it stands and watches it (look_point)
                act.sprint = False
            if small and (me.pos - post).flat() < small + 0.3 and abs(act.forward) + abs(act.strafe) < 0.2:
                s.zone_shuffle(now, me, post, act)
            if (s.p.jump > 0 and me.vel.flat() > 2.0 and not s.planned_jump and not getattr(s, "route", None)
                    and s.rng.random() < HOP_RATE * dt * s.p.jump * (0.5 + s.p.jumpy)):
                act.jump = True    # a hop on the run (Easy never: jump skill 0); the edge guard stops one
                                   # at a drop
            elif (s.p.jump > 0 and me.vel.flat() < 0.5 and kind in ("hold", "objective") and not s.planned_jump
                  and s.rng.random() < IDLE_JUMP * dt * s.p.jump * (0.3 + s.p.jumpy)):
                act.jump = True    # a jump for nothing while it waits about
            s.aim.whip = 0.0
            look = s.look_point(me, point, nav, post)
            # a person's eyes go up and down as he moves: a ledge, the floor above, a doorway, the ground ahead. Bots
            # held one height (look pitch spread 4.7 degrees to the player's 9.5, live Spire): a new height every
            # 1.2-3.5 s, -9 to +11 degrees, carried by the aim's own smoothing
            if now >= getattr(s, "pitch_next", 0.0):
                s.pitch_next, s.pitch_off = now + s.rng.uniform(1.2, 3.5), math.radians(s.rng.uniform(-7.5, 9.0))
            dl = (look - me.pos).flat()
            if dl > 1.0:
                look = Vec(look.x, look.y, look.z + dl * math.tan(getattr(s, "pitch_off", 0.0)))
            # (the quick turn only on the move with no man half-noticed: standing at spawn it swung away from the
            # enemy in front before it could see him, tools/test_focus.py)
            walking = math.hypot(act.forward, act.strafe) > 0.5
            if (walking and s.att and s.att[0] in ("watch", "post", "flank") and not str(s.att[1]).endswith("hit")
                    and gate is None):     # (off a teleporter's exit it backs away already turned to it)
                # walking, a person looks about where he goes and glances, he does not walk on looking 60+ degrees away
                # (live Spire: 43k walking ticks slowed to 0.3 by the turn rule below while watching a man, a radar
                # dot, a post's sightline or a flank; speed varying 0.36 to the player's 0.23). Shot at: the whole turn
                # (whip)
                look = s.walk_cone(me, look, act.yaw + math.atan2(act.strafe, act.forward))
            # (the quick turn also when it is told to walk but slowed by the turn rule below: under 1 u/s it never came)
            s.aim.quick_ok = (me.vel.flat() > 1.0 or walking) and not s.per.pending
            act.yaw, act.pitch = s.aim.step(now, dt, me, None, look, Vec(), False)
            arr = getattr(s, "arrive", None)       # Heroic and up: a grenade onto a landing he is 0.8-2.5 s from (a lift
            if (arr is not None and s.att and s.att[1] == "landing" and s.p.tier in ("strong", "top")   # exit: INTER-LIFT-02)
                    and now >= s.grenade_ready_at and 0.8 <= arr[1] - now <= 2.5 and 6.0 < dist(me.pos, arr[0]) < 18.0
                    and abs(wrap(angles_to(eye(me), arr[0])[0] - act.yaw)) < 0.3):
                act.grenade, s.grenade_ready_at = True, now + s.rng.uniform(5, 10)
                s.notes.append("grenade: onto the landing")
            # walking 60+ degrees off where it looks (a new goal behind it, the view still swinging round): slow down
            # until it faces the way, as a person turns and then walks (Cyberdyne sims: the median command on such
            # ticks pointed 116 degrees off the view) (not on the steps move_to places exactly: a run-up, a take-off,
            # onto an objective. Spire, live and sim: the carrier faced the jump's landing and backed to its take-off
            # at 0.3, 200 s on the top; the attackers' last steps onto the core's stand ring were too slow to hop it)
            off = abs(math.atan2(act.strafe, act.forward)) if abs(act.forward) + abs(act.strafe) > 0.1 else 0.0
            # (75 degrees before: walking 60+ degrees off the view 16% of the time to the player's 9%, live Spire)
            if off > math.radians(60) and not s.notes and now >= getattr(s, "exact_until", 0.0) and gate is None:
                act.forward, act.strafe = act.forward * 0.3, act.strafe * 0.3
            if kind == "hold":
                act.crouch = s.p.crouchy > 0.5 and dist(me.pos, point) < 1.5
            if (kind == "objective" and snap.mode != "slayer" and s.p.tactics > 0.15 and now >= s.grenade_ready_at
                    and not me.has_flag and me.weapon not in LUNGE      # (a Flood: 15 throws into its run, live Adrift)
                    and s.rng.random() < OBJ_NADE * dt * s.p.grenades):
                # a grenade at the objective as it goes in: at a man believed there (seen, heard or called out in the
                # last 3 s, 8-20 u off) who is about where it looks, with no teammate by him (Invasion territories, a
                # flag room: people throw first, the player 2.37 a minute; Legendary 0.56 on Spire)
                for k, b in s.per.beliefs.items():
                    if (isinstance(k, int) and now - b.t < 3.0 and 8.0 < dist(me.pos, b.pos) < 20.0
                            and dist(b.pos, point) < 8.0
                            and abs(wrap(angles_to(eye(me), b.pos)[0] - act.yaw)) < 0.5
                            and not any(p.team == me.team and p.alive and p.id != me.id and dist(p.pos, b.pos) < 3.0
                                        for p in snap.players)):
                        act.grenade, s.grenade_ready_at = True, now + s.rng.uniform(5, 10)
                        s.notes.append("grenade: into the objective")
                        break
            if kind == "objective" and snap.ball is not None and snap.ball_carrier is None and dist(me.pos, snap.ball) < 1.2:
                s.take(me, act, snap.ball)     # pick up the ball
            if kind == "item" and dist(me.pos, point) < 1.2:
                key = (round(point.x), round(point.y))
                took = getattr(s, "took_at", None)
                if now < getattr(s, "rearm_skip", {}).get(key, 0.0):
                    pass                        # (taken or tried here: no press until the skip runs out, whatever the goal)
                elif took is not None and took[0] == key and tuple(me.weapons) != took[1]:
                    # the swap went through (the hands changed): no more presses for a gun here, the one put down lies
                    # on the same spot (pressed on, the two swapped back and forth: live Spire 137.8-138.4 s,
                    # Warthog_2's rifle and the gun he took swapped 10 times in 0.7 s)
                    s.rearm_skip = getattr(s, "rearm_skip", {})
                    s.rearm_skip[key] = now + 30.0
                    k = next((k for k, it in enumerate(s.map.get("items", [])) if it["pos"] == point), None)
                    if k is not None:
                        s.item_skip[k] = now + 30.0
                    s.next_decide, s.took_at = now, None
                else:
                    if took is None or took[0] != key:
                        s.took_at = (key, tuple(me.weapons))
                    drop = s.gun_to_drop(me, kind, point)
                    ready = drop is None or [w for w in me.weapons if w != "none"][drop] == me.weapon
                    act.action = ready and int(now * 6) % 2 == 0   # tap: a press counts once the weapon is in reach
                                                                    # (holding the gun to swap out: gun_to_drop)
            if kind == "item" and (me.pos - point).flat() < 1.5:
                s.item_wait = getattr(s, "item_wait", None) or now
                if now - s.item_wait > 4.0:         # here, nothing to take (or out of reach): move on
                    k = next((k for k, it in enumerate(s.map.get("items", [])) if it["pos"] == point), None)
                    if k is not None:
                        s.item_skip[k] = now + 30.0
                    else:                           # a dropped gun (upgrade_point, rearm_point): not again soon
                        s.rearm_skip = getattr(s, "rearm_skip", {})
                        s.rearm_skip[(round(point.x), round(point.y))] = now + 30.0
                    s.next_decide, s.item_wait = now, None
            else:
                s.item_wait = None
        on_flag = bool(snap.flags) and not me.has_flag and any(
            t != me.team and f.state != "carried" and dist(me.pos, f.pos) < 1.2 for t, f in snap.flags.items())
        if me.weapon != getattr(s, "last_wpn", None):
            s.last_wpn, s.wpn_changed = me.weapon, now
        # (only a read that just turned: a gun the map's names do not cover reads "unknown" all along, and a runner with
        # one stood on their flag 40 s never pressing Use, live Boardwalk: no red flag run all match)
        # the Invasion core just left our hands: the weapon read shows a gun again before the carrier read clears (0.4-0.6 s)
        # and the quiets below held the re-take 1.5 s more (live Boneyard B12 527.3 s: the carrier fought 0.3 u off the
        # dropped core and died without taking it back; it lay loose 120 s)
        core_out = (snap.mode == "invasion" and me.weapon not in ("unknown", "flag")
                    and s.wpn_changed > getattr(s, "use_quiet_until", 0.0) - 1.5)
        if core_out:
            s.flag_quiet_until = 0.0
        elif me.has_flag or (on_flag and me.weapon in ("unknown", "flag") and now - s.wpn_changed < 1.0):
            # it is in our hands: the weapon read shows it the tick it happens, the flag read 0.4-0.6 s later. Until
            # then no Use and no weapon switch: in Reach either one drops the flag and the held Use took it again, the
            # gun in hand flipping flag / rifle every tick
            s.flag_quiet_until = now + 1.5
        if on_flag and now >= getattr(s, "flag_quiet_until", 0.0):
            # on an enemy flag: take it whatever the goal (a fight on their stand too)
            s.take(me, act, next(f.pos for t, f in snap.flags.items()
                                 if t != me.team and f.state != "carried" and dist(me.pos, f.pos) < 1.2))
        if (me.has_flag and not core_out) or (snap.mode == "invasion" and snap.inv_phase == 3 and me.weapon == "unknown"
                                              and now - s.wpn_changed < 1.0):   # (just turned: an unnamed gun is "unknown")
            s.use_quiet_until = now + 1.5   # just carrying: no Use on a read that lags behind the carry (the core in hand
                                            # reads "unknown" 0.5 s before the carrier read: Use dropped it)
        elif core_out:
            s.use_quiet_until = 0.0
        spot = None if me.has_flag else (s.on_loose_objective(me, snap) or s.use_flag_point(me, snap, s.game))
        if spot is not None and now >= getattr(s, "use_quiet_until", 0.0):
            s.take(me, act, spot)  # on a bomb / stockpile flag / the Invasion core: take it (live: stood on the core)
        # carrying: no gun, but the flag or ball swings: hit a man at arm's length (one press a swing). Not the Invasion
        # core: a melee press drops it (live: 18 of 18 carrier melees on Spire, 2 of 2 on Boneyard ended the carry within
        # 1 s, logs/tmp/invasion_regress/melee_drop.py; Breakpoint's bomb 0 of 12, CTF flags 0 of 42)
        if me.has_flag and not (snap.mode == "invasion" and not snap.inv_bomb):
            close = [k for k in s.per.visible_enemies() if dist(me.pos, s.per.beliefs[k].pos) < MELEE_R]
            if close and now >= getattr(s, "melee_at", 0.0):
                b = s.per.beliefs[min(close, key=lambda k: dist(me.pos, s.per.beliefs[k].pos))]
                act.yaw, act.pitch = angles_to(eye(me), b.pos + Vec(0, 0, 0.35))
                act.melee, s.melee_at = True, now + s.rng.uniform(0.6, 0.9)
                s.notes.append("carrier melee")
        if snap.mode == "race" and kind in ("fight", "retreat") and me.id in snap.race_next and me.vehicle in (None, -1):
            # a racer shoots on its way and does not stop to duel or hide (RACE-01; live Rat Race, 16 players: 9-21
            # kills and 0 laps each in 3 min): the walk goes on to its gate, the aim and trigger stay on the man
            walk = Action(yaw=act.yaw, pitch=act.pitch)
            s.move_to(me, snap.race_next[me.id], walk, nav)
            h, m = walk.yaw + math.atan2(walk.strafe, walk.forward), min(1.0, math.hypot(walk.forward, walk.strafe))
            act.forward, act.strafe, act.jump = m * math.cos(h - act.yaw), m * math.sin(h - act.yaw), act.jump or walk.jump
            s.notes.append("race: shooting on the way")
        elif kind == "fight" and snap.mode == "invasion" and s.pressing(me, snap) and me.vehicle in (None, -1) \
                and not me.has_flag and isinstance(arg, int) and arg in s.per.beliefs:
            # an attacker on the clock shoots on its way to the territory / the core, as the racer does: the aim and
            # trigger on the man, the walk to the objective. Only for a man 6 u off or more, and not inside its zone
            # (keep_in_zone holds it there). The owner's Spire Invasion 20261105-233940 at 297.9 s ("THESE SPARTANS ARE
            # JUST CONCERNED WITH FIGHTING NOT THE OBJECTIVE"): phase 2's attackers stood 15-30 u off their territory
            # half the time, 7% in 6 u of it, and picked a fight there 27% of the time (a goal of objective never shoots)
            pt = s.order.point if s.order.point is not None else s.objective_point(me)
            zr = s.zone_at(snap, pt) if pt is not None else None
            if pt is not None and dist(me.pos, s.per.beliefs[arg].pos) > 6.0 and dist(me.pos, pt) > (zr or 2.0):
                walk = Action(yaw=act.yaw, pitch=act.pitch)
                s.move_to(me, pt, walk, nav)
                h, m = walk.yaw + math.atan2(walk.strafe, walk.forward), min(1.0, math.hypot(walk.forward, walk.strafe))
                act.forward, act.strafe, act.jump = m * math.cos(h - act.yaw), m * math.sin(h - act.yaw), act.jump or walk.jump
                s.notes.append("invasion: shooting on the way")
        used = s.kit.act(s, now, dt, me, snap, act)       # equipment / ability, and steering clear of mines
        if used:
            s.notes.append(used)
        s.give_way(me, snap, act)
        s.crouch_spots(me, act)
        s.own_grenade(now, me, act)
        if kind == "fight":
            # (after the kit's dash, the give-way and the grenade step: live B15 holders still left 1.4-2.4 u fighting
            # with the clamp in the fight alone; the evade and the give-way stepped them out after it)
            s.keep_in_zone(me, snap, act)
        s.obstacle_guard(now, me, act, snap)
        press = getattr(s.kit, "press", None)
        if act.equipment and press is not None and press[0] == "sprint" and abs(act.strafe) > 0.1:
            # giving way or stepping round a wall after the kit stepped it aside: a strafe ends Reach's sprint, so the
            # step is steered instead. Not the unstuck wiggle: an escape
            Kit.sprint_steer(dt, me, act)
        s.unstuck(now, me, act, snap)
        s.no_wall_jump(me, act)
        s.edge_guard(now, me, act)
        s.fire_gate(now, me, act, snap)
        s.firefight_hold(now, me, snap, act)
        s.fired_last = s.shot_now
        if s.shot_now:
            s.last_shot_t = now
        act.weapon = s.choose_weapon(now, me, kind, arg, snap)
        if now < getattr(s, "flag_quiet_until", 0.0):
            act.weapon, act.action, act.reload = -1, False, False     # the flag just came into our hands (above)
        elif act.weapon < 0 and kind not in ("fight", "retreat") and not me.has_flag:   # (nothing to reload in the objective)
            s.reload_check(now, me, act)
        return act

    def reload_check(s, now, me, act):
        """Reload between fights, never with a man in view (guide 03: "the bot reloads after every kill, never
        with an enemy in view"), once the magazine is down to 40% of the most it has held and rounds are left
        in reserve. Halo reloads an empty magazine by itself on the next trigger pull."""
        if me.weapon not in me.weapons or s.per.visible_enemies():
            return
        i = me.weapons.index(me.weapon)
        mag = me.mag[i] if i < len(me.mag) else -1
        total = me.ammo[i] if i < len(me.ammo) else -1
        if mag < 0:
            return
        top = s.__dict__.setdefault("mag_max", {})
        top[me.weapon] = max(top.get(me.weapon, 0), mag)
        if total > mag and mag <= 0.4 * top[me.weapon] and now >= getattr(s, "reload_at", 0.0):
            act.reload, s.reload_at = True, now + 3.0
            s.notes.append("reload between fights")

    def rank(s, w):
        return RANK_GAME.get(s.game, {}).get(w, WEAPON_RANK.get(w, 1.0))

    def gun_to_drop(s, me, kind, point):
        """About to pick up a gun with both slots full: the inventory slot to hold so the swap throws away the right one
        (Halo swaps the gun in hand): an empty gun first, else the lowest ranked. None: not picking up a gun.: holding
        its BR it took the laser for its empty AR's sake, dropped the BR, then went back for the BR and dropped the
        laser, every 0.5 s for 13 s."""
        inv = [w for w in me.weapons if w not in ("none",)]
        if kind != "item" or not isinstance(point, Vec) or len(inv) < 2 or (me.pos - point).flat() > 2.0:
            return None
        if any(it["pos"] == point and it["kind"] != "weapon" for it in s.map.get("items", [])):
            return None                                   # equipment, a powerup: no gun goes
        ammo = [me.ammo[i] if i < len(me.ammo) else -1 for i in range(len(inv))]
        lying = getattr(s, "pickup_gun", None)
        new = lying[1] if lying and (lying[0] - point).flat() < 1.0 else next(
            (it["name"] for it in s.map.get("items", []) if it["pos"] == point), None)
        return s.drop_slot(inv, ammo, new)

    def choose_weapon(s, now, me, kind, arg, snap):
        """Switch to the weapon that fits. At once: the gun in hand is dry; its magazine ran out mid-fight with
        the other gun loaded (the switch beats the reload); its blast would reach us; the plasma pistol's
        overcharged bolt just left (the noob combo's precision shot). In a fight by range otherwise: the
        automatic, shotgun or sword up close, the rifle at range, with hysteresis (no switch while finishing a
        man, none sooner than 1.2 s after the last). Out of a fight the best all-rounder. Returns an inventory
        slot or -1 to keep the current one. Never while carrying the objective: a switch drops it (Reach
        Oddball: a bot dropped the ball 0.3 s after taking it)."""
        if me.has_flag:
            return -1
        if snap is not None and snap.mode == "invasion" and snap.inv_phase == 3 and me.weapon == "unknown":
            return -1      # the core in hand (it reads "unknown", before the carrier read): a switch drops it, and the held
                           # Use took it back swapping the rifle out, every tick
        inv = [w for w in me.weapons if w not in ("none",)]
        for obj in ("flag", "ball"):  # carry it in hand: Halo 2 waits for the player to select a
            if obj in inv:              # picked-up weapon (CE holds it itself); the ball scores in hand
                return inv.index(obj) if me.weapon != obj else -1
        drop = s.gun_to_drop(me, kind, arg)
        if drop is not None:
            return drop if inv[drop] != me.weapon else -1     # hold the gun the pickup will swap out
        n = len(inv)
        ammo = {i: (me.ammo[i] if i < len(me.ammo) else -1) for i in range(n)}       # -1 = not known
        mag = {i: (me.mag[i] if i < len(me.mag) else -1) for i in range(n)}
        cur = inv.index(me.weapon) if me.weapon in inv else -1
        b = s.per.beliefs.get(arg) if kind == "fight" else None
        if b is None:
            # no fight this tick (backing off, a hunt) but a man close by: the guns still go by his range, not the
            # all-rounder (live Boardwalk: three AR -> DMR swaps with a man 2-3.4 units away)
            near = min(((dist(me.pos, v.pos), k) for k, v in s.per.beliefs.items()
                        if isinstance(k, int) and now - v.t < 3.0), default=None)
            if near and near[0] < SHORT_BAND:
                arg, b = near[1], s.per.beliefs[near[1]]
        d = dist(me.pos, b.pos) if b else None
        recruit = s.p.tactics <= 0.15

        def pick(i, why):
            s.switch_ok_at = now + s.rng.uniform(1.2, 2.0)
            s.swap_why = why
            s.notes.append("switch: " + why)
            return me.weapons.index(inv[i])

        ready = [i for i in range(n) if i != cur and ammo[i] != 0 and mag[i] != 0 and inv[i] not in BLAST]
        if cur >= 0 and ammo[cur] == 0:
            # dry in hand: anything loaded. A Recruit clicks the empty gun a moment first
            s.dry_since = getattr(s, "dry_since", None) or now
            loaded = [i for i in range(n) if i != cur and ammo[i] != 0]
            if loaded and (not recruit or now - s.dry_since > s.rng.uniform(0.6, 1.2)):
                s.dry_since = None
                return pick(max(loaded, key=lambda i: s.rank(inv[i])), "dry")
        else:
            s.dry_since = None
        if cur >= 0 and d is not None and ready:
            if mag[cur] == 0 and not recruit and d < 15.0:
                return pick(ready[0], "empty magazine mid-fight")       # faster than the reload
            if me.weapon in BLAST and d < BLAST[me.weapon] + 0.5:
                return pick(ready[0], "inside its own blast")
        if cur >= 0 and me.weapon == "plasma_pistol" and 0.1 <= now - getattr(s, "combo_at", -9.0) < 1.5:
            # the bolt has left (a switch on the release tick could eat the shot): the head shot (guide 03)
            prec = [i for i in ready if inv[i] in PRECISION]
            if prec:
                s.combo_at = -9.0
                return pick(prec[0], "noob combo")
        if n < 2 or now < getattr(s, "switch_ok_at", 0.0):
            return -1
        if kind not in ("fight", "retreat") and d is None and cur >= 0 and not s.per.visible_enemies():
            # between fights (where think runs reload_check: not in a retreat either, third review B1) a gun whose
            # magazine ran out comes back out to be reloaded (reload_check reloads the gun in hand only; live CE: an
            # AR with 491 in reserve stayed empty in the pocket). Not one named like the gun in hand: a switch by
            # name cannot tell them apart (two Halo 3 "unknown"s, N8)
            empty = [i for i in range(n) if i != cur and inv[i] != me.weapon and mag[i] == 0 and ammo[i] > 0
                     and s.rank(inv[i]) >= s.rank(me.weapon)]
            if empty:
                return pick(empty[0], "reload the empty one")
        if d is not None and recruit:
            d = None       # range bands are a Regular's rule and up; a Recruit "uses the AR at Long band" (guide 01 s9)
        t = snap.player(arg) if d is not None else None
        if (t is not None and t.shield <= 0.25 and cur >= 0 and ammo[cur] != 0 and mag[cur] != 0 and me.weapon not in BLAST
                and d <= reach(me.weapon, s.game)):
            return -1      # a shot or two from the kill: finish him with what is in hand (a switch costs ~0.5 s), if it
                           # reaches him (review F1: a shotgun or sword kept at 15 units)

        def score(i):
            w = inv[i]
            if ammo[i] == 0:
                return -9.0                            # never pick an empty gun
            if d is not None and d < 15.0 and i != cur and mag[i] == 0:
                return -9.0                            # nor, mid-fight, one with its magazine out: "empty magazine
                                                       # mid-fight" swaps it straight back (live CE: every 1.5-2 s)
            v = s.rank(w)
            if d is not None:
                v += 3.0 * proficiency(w, d, s.game) - 1.0     # its range class at his distance (fully proficient +2, not -1)
                if w in LUNGE and abs(b.pos.z - me.pos.z) < 2.0:
                    # the sword or hammer out before he is in reach (a switch takes ~0.5 s) and kept until he is well
                    # away; not for a man on another floor it cannot run at. The gun the rest of the time (the player:
                    # sword users held it out at 4-15 units for 85% of their sword fights)
                    v += 2.5 if d < (8.0 if i == cur else 6.0) else 0.0
                if w in CQ:                            # up close the automatic / shotgun / sword comes out
                    v += 2.0 if d < CLOSE else 1.0 if d < SHORT_BAND else 0.0
                    if t is not None and t.weapon in LUNGE and d < RUSH_R:
                        # a sword or hammer coming for us (the Flood): the gun that stops him at arm's length is out
                        # before he gets there (owner's Halo 4 Flood, trace 20261106-144807: 41% of the Flood that
                        # reached a survivor found him holding the magnum, swapped to it at 5.6 u)
                        v += 2.5
                elif w in PRECISION and w not in ("pistol", "magnum") and d < CLOSE:
                    v -= 1.0                           # a rifle against a man strafing at arm's length
                if (w == "plasma_pistol" and t is not None and t.shield > 0.6 and d < 10.0
                        and any(x in PRECISION for x in inv)):
                    # the noob combo's opener: strip him, then the head shot (4.0 + the in-band +2 it had before the
                    # range classes rated it 0.5). Reach's overcharge is slow: there only up close
                    v += 3.0 if s.game == "reach" else 5.5
                if t is not None and t.vehicle != -1 and w in ANTI_VEHICLE:
                    v += 4.0 if s.p.personality == "spammer" else 2.0   # a vehicle: the gun that kills it (guide 04)
                if w in BLAST:                         # its own blast: anything else (a launcher kept at point
                    v -= 6.0 if d < BLAST[w] + 0.5 else 2.0 if d < BLAST[w] + 2.0 else 0.0   # blank)
            elif not recruit:
                # between fights, the gun for the fights to come: its range classes weighted by how often each band is
                # fought (BAND_WEIGHT), so the precision rifle is in hand when the next man shows at Medium (Spire,
                # carrying a needle rifle or DMR beside an AR or repeater, bots walked between fights with the automatic
                # in hand 18% of the time, logs/tmp/heldgun.py: by rank alone the needle rifle was 0.7 better, under the
                # 1.0 a switch needs)
                v += 3.0 * sum(BAND_WEIGHT[k] * proficiency(w, game=s.game)[k] for k in range(4)) - 1.0
            return v

        best = max(range(n), key=score)
        # ponytail: the magnum's edge on the shotgun at Medium band is exactly this 1.0, and only floating point
        # (3.0 * 0.8 = 2.4000000000000004) makes it a switch; right against a gunner, so kept. Make it explicit if the
        # band table changes
        if best != cur and (cur < 0 or score(best) > score(cur) + 1.0):
            return pick(best, "range" if d is not None else "best gun")
        return -1

    def threat(s, now, me, snap, k):
        """Guide 01 section 10 threat score for an enemy we see: his weapon in its band (power weapon
        100, in band 70, out of band 30), how close (melee 1.5 .. extreme 0.4), shields gone (a kill
        to take now, guide 01 catch-all rule 2: x1.15), aiming at us (1.3) or away (0.5), +40 carrying
        the objective, +30 when a teammate is already shooting him (team shot), a little for a grudge."""
        b = s.per.beliefs[k]
        e = snap.player(k)
        d = dist(me.pos, b.pos)
        base = 60.0
        if e is not None:
            lo, hi = WEAPON_RANGE.get(e.weapon, (5.0, 25.0))
            inband = lo - 1.0 <= d <= hi
            base = (100.0 if WEAPON_RANK.get(e.weapon, 1.0) >= 4.0 else 70.0) if inband else 30.0
        # The section 10 weights as they stand are a Recruit's read; the better the player, the more of what
        # decides fights it weighs (section 9: Veteran and up use the range, cover and team-shot rules in
        # full). Sim (tools/difficulty_report.py --human): no harm, any gain within the noise; the live game
        # is the test
        t = s.p.tactics
        v = base * (1.5 + 0.4 * t if d < 0.8 else 1.2 + 0.4 * t if d < 4 else 1.0 if d < 12 else 0.7 if d < 30 else 0.4)
        if e is not None:
            if e.shield <= 0.0:
                v *= 1.15
            yaw, pitch = angles_to(eye(e), me.pos + Vec(0, 0, 0.35))
            off = math.hypot(wrap(yaw - e.yaw), pitch - e.pitch)
            # aiming away: less danger to a Recruit, a free kill to a tactical player
            v *= 1.3 if off < 0.2 else (0.5 + 0.6 * t if off > 1.6 else 1.0)
            if off > 0.4 and t > 0.15 and any(
                    p.team == me.team and p.alive and p.id != me.id and dist(p.pos, e.pos) < 30
                    and abs(wrap(angles_to(eye(e), p.pos)[0] - e.yaw)) < 0.2 for p in snap.players):
                v *= 1.0 + 0.6 * t       # busy shooting a teammate: third-party him (two on one)
            if e.has_flag or k in snap.vips:     # (the enemy VIP: killing him scores, guide 07 VIP)
                v += 40.0
        if getattr(s, "team_focus", {}).get(k, 0) - (1 if s.goal == ("fight", k) else 0) > 0:
            # team shot (section 10 +30, a Regular's): in full from Veteran up, a Recruit "fights whatever it sees"
            v += 60.0 * s.p.teamwork
        if k == s.last_killer:
            v += 15.0 * s.p.grudge
        if e is not None and e.vehicle != -1 and s.p.personality == "spammer" and s.anti_vehicle_gun(me):
            v += 45.0      # the Grenadier's mission, armed: the vehicle's crew before anyone on foot (vehicle_mission)
        if k == getattr(s.per, "attacker_id", None) and now - getattr(s.per, "attacker_t", -9.0) < 1.5:
            # the one shooting us right now: a tactical player turns on him; a novice stays on whoever
            # it was shooting (tunnel vision, its bad spot). the player's game: 30 of 51 bots he
            # killed were not fighting him in their last 3 s
            v += 60.0 * s.p.tactics
        if e is not None:
            v *= s.kit.threat_mod(s, now, me, e, snap)   # armor lock, decoys, sprinters, jetpacks (guide 08)
        return v

    def pick_target(s, now, me, snap, seen):
        """Highest threat; a new target must beat the current one by 25% (no flip-flopping between two
        and killing neither: guide 01 section 10). A novice tunnels: twice the threat to switch. A
        kill under way is the fastest removal (guide 01): the margin grows with the damage he has
        taken, and a target in view is held TARGET_HOLD s unless someone is at arm's length (guide 09
        4.5 and 4.8: a switch costs a re-acquire). the player's Settlement game: the threat bonuses
        swung past 25% every second in a 13-player lobby; Legendary bots switched twice as often as
        Easy ones, won 11% of their fights to Easy's 26% and their team finished last."""
        scored = {k: s.threat(now, me, snap, k) for k in seen}
        best = max(scored, key=scored.get)
        cur = s.goal[1] if s.goal[0] == "fight" else None
        if cur in scored and best != cur and s.shot_by_other(now, me, snap, best, cur, seen):
            return best
        if cur in scored and best != cur:
            close = dist(me.pos, s.per.beliefs[best].pos) <= 1.5
            if (now - getattr(s, "target_at", -9.0) < TARGET_HOLD or s.finishing(snap, seen)) and not close:
                return cur              # (a kill about to land goes first: it rode on the margin's jitter before)
            e = snap.player(cur)
            done = 1.0 - (e.shield + e.health) / 2.0 if e is not None else 0.0
            if scored[best] < (1.25 + 0.75 * (1.0 - s.p.tactics)) * (1.0 + 2.0 * max(0.0, done)) * scored[cur]:
                return cur
        return best

    def shot_by_other(s, now, me, snap, k, cur, seen):
        """Turn on k right now, whatever the hold and the kill under way? When k is the one hitting us this second
        and the man we fight is not even aiming at us (Heroic and Legendary; a kill about to land goes first). the
        player's Construct games: of 26 first hits from someone else mid-fight, Legendary never turned in 1.5 s on
        19, and died to the man it ignored (logs/tmp/shot_case.py)."""
        if s.p.tactics < 0.5 or k != getattr(s.per, "attacker_id", None) or now - getattr(s.per, "attacker_t", -9.0) > 1.0:
            return False
        if now < getattr(s.per, "attacker_notice", 0.0):
            return False                # not noticed yet who it is (Perception.hurt_by)
        e = snap.player(cur)
        return e is not None and not aims_at(e, me) and not s.finishing(snap, seen)

    def turn_ok(s, now, me, seen, d_unseen, snap=None, k=None):
        """Turn from the man in view to one we cannot see (a radar dot, a hit from behind)? Only when he is
        much closer than the man we fight (the player closing from 3 units on a bot shooting someone 10
        off) and the fight is past its first TARGET_HOLD s, or he is shooting us and the man we fight is
        not (shot_by_other). Live, Boardwalk: Legendary turned to 97 such dots in 9 minutes, its fights
        lasted 1.2 s and it won 8% of them (Easy 29%)."""
        cur = s.goal[1] if s.goal[0] == "fight" else None
        if cur not in seen:
            return True
        if snap is not None and k is not None and s.shot_by_other(now, me, snap, k, cur, seen):
            return True
        return (dist(me.pos, s.per.beliefs[cur].pos) > 2.0 * d_unseen
                and now - getattr(s, "target_at", -9.0) >= TARGET_HOLD)

    def finishing(s, snap, seen):
        """A kill about to land: the man we fight is in view with his shield nearly gone (a DMR is two
        shots from the kill). A good player takes it before he turns to a dot on the radar."""
        cur = s.goal[1] if s.goal[0] == "fight" else None
        e = snap.player(cur) if cur in seen else None
        return e is not None and e.shield <= 0.25 and (not snap.shieldless or e.health <= 0.5)   # (no shields: hurt)

    def melee_check(s, me, snap, act):
        """A melee press goes only at an enemy in the swing (within SWING_R, SWING_DEG of the facing) with no teammate
        nearer in it. Live Beachhead Firefight Nov 6 (4 matches): presses went on after the Elite they were at died, and
        12 swung with a teammate first in the swing, 0.4-1.4 u off (GitHub #12: "melee the air and lock on the player
        after combat ... team kills")."""
        c, sn = math.cos(act.yaw), math.sin(act.yaw)

        def ahead(q):
            dx, dy = q.x - me.pos.x, q.y - me.pos.y
            d = math.hypot(dx, dy)
            return d if 1e-3 < d < SWING_R and abs(q.z - me.pos.z) < 1.5 and (dx * c + dy * sn) / d > SWING_COS else None
        foes = [d for d in (ahead(p.pos) for p in snap.players if p.alive and p.team != me.team and p.id != me.id)
                if d is not None]
        mates = [d for d in (ahead(p.pos) for p in snap.players if p.alive and p.team == me.team and p.id != me.id)
                 if d is not None]
        if not foes or (mates and min(mates) < min(foes)):
            act.melee, act.lunge_at = False, -1
            if getattr(s, "notes", None) is not None:
                s.notes.append("melee held: " + ("a teammate first in the swing" if foes else "nobody in the swing"))

    def dry_fights(s, scores, me):
        """Every gun empty and one to take: a fight is only a man at arm's length (a melee), the rest weigh 0.3. Live
        Beachhead Firefight Nov 6: a bot dry from 159 s weighed guns 6 u off at 3.0 and fought on at 3.45, nothing to
        shoot with."""
        if not (s.dry(me) and me.vehicle in (None, -1) and any(k[0] == "item" for k in scores)):
            return
        for k in [k for k in scores if k[0] == "fight"]:
            b = s.per.beliefs.get(k[1])
            if b is None or dist(me.pos, b.pos) > DRY_FIGHT_R:
                scores[k] *= 0.3

    def decide(s, now, me, snap):
        seen = s.per.visible_enemies()
        scores = {}
        post = getattr(s, "posture", 0.5)     # the team's score posture (director, brain/match.py)
        post = s.kit.posture(now, me, post)   # its own overshield: aggressive while it lasts (EQ-OS-02)
        friends = sum(1 for p in snap.players if p.team == me.team and p.alive and p.id != me.id
                      and dist(p.pos, me.pos) < 12)
        hit_recent = now - getattr(s, "hit_at", -9.0) < 2.0
        avoid = False
        if seen and not me.has_flag:
            t = s.pick_target(now, me, snap, seen)
            # an enemy in view outranks the director's orders (objective tops out at 1.4): a
            # human shoots the man in front of him. Before this fights lost to orders and lasted
            # 0.84 s median, 956 of 965 ending with the target alive (H2A Zenith)
            scores[("fight", t)] = 1.5 + 0.4 * s.p.aggression
            if s.order.role == "runner" and dist(me.pos, s.per.beliefs[t].pos) > 12:
                scores[("fight", t)] -= 1.2 * s.p.objective   # runners skip far fights
            if me.id in snap.vips and dist(me.pos, s.per.beliefs[t].pos) > 8:
                scores[("fight", t)] -= 0.6 * s.p.objective   # the VIP holds back from fights (guide 07 VIP)
            # enemy about to win, or a lead to protect: fight only two-on-one, or when he is on us
            # (guide 01 section 12: "fight only two-on-one, never cross open lanes")
            avoid = (post <= 0.3 and len(seen) > friends and not hit_recent
                     and dist(me.pos, s.per.beliefs[t].pos) > 8)
            e, dt_ = snap.player(t), dist(me.pos, s.per.beliefs[t].pos)
            lo, hi = WEAPON_RANGE.get(e.weapon if e else "", (5.0, 25.0))
            if (e is not None and s.p.tactics >= 0.75 and not hit_recent and friends == 0 and e.shield > 0.5 and dt_ > 6
                    and WEAPON_RANK.get(e.weapon, 1.0) >= 4.0 > WEAPON_RANK.get(me.weapon, 1.0) and lo - 1.0 <= dt_ <= hi):
                # his power weapon in its band, none of ours, nobody with us: no fight, break line of sight and
                # regroup (guide 01 section 10, engagement rule 5; Veteran and up, section 9). Only into cover
                # right there: turning to run in the open from a sniper or rockets is a shot in the back
                cover = s.cover_from(me, [s.per.beliefs[t].pos])
                if cover is not None and dist(me.pos, cover) <= COVER_NEAR * 0.5:
                    scores[("fight", t)] = 0.3
                    scores[("retreat", cover)] = 2.0
            if avoid:
                scores[("fight", t)] = 0.3
        if s.goal[0] == "fight" and not me.has_flag and not avoid:
            # finish the fight: the same target until it dies or has been out of sight COMMIT_S (longer
            # with its shield down), chasing where it was (fight: aim there, close in). Objectives and
            # items no longer pull a bot out of a live fight: 60% of Lockdown fights ended that way with
            # the target alive (tools/fight_ends.py)
            tgt = snap.player(s.goal[1])
            gone = now - s.per.last_seen.get(s.goal[1], -9.0)
            weak = tgt is not None and tgt.shield <= 0.05 and (not snap.shieldless or tgt.health <= 0.5)
            if tgt and tgt.alive and gone < (COMMIT_WEAK_S if weak else COMMIT_S):
                # the commitment is to fighting, not to one man: when the threat ranking (pick_target,
                # 25% stickiness built in) picked someone else in view, the commitment goes with it
                # (the committed score pinned bots on their target while the player killed them)
                keep = s.goal if not seen or me.has_flag or t == s.goal[1] else ("fight", t)
                scores[keep] = max(scores.get(keep, 0.0), 2.6 + 0.3 * s.p.aggression)
        if s.p.aware > 0.5 and not me.has_flag:      # Heroic and Legendary (a Regular tunnels in its fights)
            # a radar dot or a hit from a man not seen yet, close by: an aware player checks it even
            # mid-fight (the player's Reach game: bots shot someone 10 units off while the player closed in
            # from 3 units on their radar); a cracked target in view is finished first. A novice tunnels
            cur = s.goal[1] if s.goal[0] == "fight" else None
            near = [(dist(me.pos, b.pos), k) for k, b in s.per.beliefs.items() if isinstance(k, int) and not b.seen
                    and b.source in ("radar", "hit") and now - b.t < 1.0 and k != cur and dist(me.pos, b.pos) < 3.5]
            if near and not s.finishing(snap, seen) and s.turn_ok(now, me, seen, min(near)[0], snap, min(near)[1]):
                # a hunt (look_point watches the corner he would come round). As a "fight" on a man never seen it
                # aimed at the dot through the wall, and the next decision skipped that dot (the target in hand), went
                # for an item, then fought the dot again: every 0.13 s, never firing while the player shot it
                scores[("hunt", s.per.beliefs[min(near)[1]].pos)] = 2.7 + 0.5 * s.p.aware
        # back off to recharge only when losing the trade: cracked and outnumbered, or the
        # enemy is healthier. Otherwise commit (a human finishes a fight he is winning). How cracked
        # is cracked follows the posture: careful near the enemy's win, stubborn when all-in.
        thr = max(0.05, min(0.45, 0.25 + 0.5 * (0.5 - post)))
        # (a match without shields, Team SWAT: every shield reads 0, so cracked is the body at half)
        cracked = me.health < 0.5 if snap.shieldless else me.shield < thr and me.health < 0.8
        threats = [s.per.beliefs[k].pos for k in seen]
        reach = COVER_NEAR * (0.5 + 0.5 * s.p.tactics)  # how far a bot will go for cover (top: 6 units)
        if cracked and seen and s.p.tactics > 0.15:      # novices fight it out wherever they stand
            mine = me.shield + me.health
            theirs = max(((snap.player(k).shield + snap.player(k).health) for k in seen if snap.player(k)), default=0.0)
            if len(seen) > 1 or theirs > mine + 0.3:
                # losing: break line of sight, but only to cover that is right there
                cover = s.cover_from(me, threats)
                # ... cover reached before the damage coming in kills us (run 2.25 u/s); else fight it out
                taking = sum(x for t, x in getattr(s.per, "losses", []) if now - t < 1.5) / 1.5
                if cover is not None and dist(me.pos, cover) <= reach and dist(me.pos, cover) / 2.25 < 0.6 * mine / max(taking, 1e-3):
                    scores[("retreat", cover)] = 2.5 + 0.5 * s.p.tactics + 0.3 * s.p.caution
                    s.recharge_until = now + 7.0  # guide 06: a popped shield is 7-10 s out of the fight
        moved = s.relocate_point(now, me, snap)
        if moved is not None:
            scores[("retreat", moved)] = 2.4
        regroup = s.regroup_point(now, me, snap, seen, post)
        if regroup is not None:
            scores[("retreat", regroup)] = 3.0     # over the committed fight (2.6 + 0.3 aggression)
        recent = [b.pos for k, b in s.per.beliefs.items() if isinstance(k, int) and now - b.t < 10.0]
        # untouched for SHIELD_BACK the shield is full in every Halo, whatever it reads: no recharge retreat past that
        # (Reach read 0 shields all match on and Heroic / Legendary bots hid at cover for minutes)
        recharged = now - getattr(s.per, "hurt_at", -99.0) > SHIELD_BACK
        if (not seen and not me.has_flag and me.shield < 0.5 and s.p.tactics > 0.15 and recent
                and s.goal[0] != "fight" and not recharged and not snap.shieldless):     # (no shield to recharge)
            # a fight is over and the shield is down: recharge behind cover near here before the next
            # one, rather than walk on cracked (a novice does; that is its bad spot)
            cover = s.cover_from(me, recent)
            if cover is not None and dist(me.pos, cover) <= reach + 2.0:
                scores[("retreat", cover)] = max(scores.get(("retreat", cover), 0.0), 1.6 + 0.6 * s.p.tactics)
                s.recharge_until = now + 7.0
        if avoid:
            away = s.cover_from(me, threats) or s.away_from(me, threats)
            scores[("retreat", away)] = 1.0
        # retreated: stay out until the shield is back (re-peeking at half shield loses: guide 01
        # section 11), unless someone walks right into us
        close = any(dist(me.pos, s.per.beliefs[k].pos) < 5 for k in seen)
        if (now < getattr(s, "recharge_until", 0.0) and me.shield < 0.95 and not recharged and not close
                and s.goal[0] == "retreat" and not snap.shieldless):
            scores[s.goal] = max(scores.get(s.goal, 0.0), 2.3)
        o = s.order
        if o.role in ("runner", "escort", "defender", "hunter", "mid", "screen") and o.point:
            live = s.live_point(me, snap)
            pt = live or s.objective_point(me)
            scores[("objective", pt)] = 0.5 + 0.7 * s.p.objective
            if o.role == "runner" and dist(me.pos, o.point) < 10:
                # at the stand: grab it, unless a defender is right there (clear the room first;
                # a CE carrier cannot shoot back)
                guarded = any(dist(s.per.beliefs[k].pos, o.point) < 12 for k in seen)
                scores[("objective", pt)] += 0.0 if guarded else 1.0
                if snap.mode == "ctf" and s.grab_ok(me, snap, o.point, seen):
                    # inside GRAB_R the grab, unguarded or with company: it beats a new fight, not a committed one
                    scores[("objective", pt)] = max(scores[("objective", pt)], GRAB_SCORE)
            if me.has_flag:
                scores[("objective", pt)] = 3.0  # carrying: go home, do not stop to fight
            elif o.role == "hunter" and live is not None:
                # our flag is out: get it back (its carrier, or stand on it the 15 s Reach's return takes)
                scores[("objective", pt)] = 1.3 + 0.6 * s.p.teamwork
            elif getattr(s, "blitz_until", 0.0) > now:
                # the team's blitz (director.blitz_tick): its rally, then the objective, ahead of side trips
                scores[("objective", pt)] = max(scores[("objective", pt)], 1.3 + 0.6 * s.p.teamwork)
        unseen = {k: b for k, b in s.per.beliefs.items() if not (isinstance(k, int) and b.seen)}
        known = list(unseen.values())
        if known and not me.has_flag:
            rank = lambda k: unseen[k].t + (3.0 if unseen[k].weak else 0.0)
            hk = max(unseen, key=rank)
            # the man we hunt stays the one we hunt while what we know of him is nearly as fresh as the newest: the
            # newest radar dot flipped between two men 92 times a minute of Legendary hunting and the bots milled about
            # between them
            cur = getattr(s, "hunt_key", None)
            if s.goal[0] == "hunt" and cur in unseen and rank(cur) >= rank(hk) - HUNT_KEEP:
                hk = cur
            s.hunt_key, b = hk, unseen[hk]
            # a called-out cracked enemy is a free kill: humans push it. Behind: hunt harder; ahead or the
            # enemy about to win: let them come (score posture). A chase into the unknown loses to whoever
            # else is there (guide 07 Team Slayer: "never by a solo chase"; FFA, and so any lobby of three
            # teams or more: never start a fight, the third party wins it): a tactical player hunts a weak
            # man, or with a mate against one team; otherwise it holds and lets them come (the player's
            # Settlement game, four teams: 82% of Legendary deaths came mid-hunt, Easy's 16%)
            crowd = len({p.team for p in snap.players if p.team != me.team}) >= 2
            cautious = 1.0 - 0.6 * s.p.tactics if (friends == 0 or crowd) and not b.weak else 1.0
            want = (0.3 + 0.6 * s.p.aggression + (0.35 if b.weak else 0.0)) * (0.4 + 1.2 * post) * cautious
            if me.weapon in SNIPERS and s.p.tactics > 0.15 and dist(me.pos, b.pos) < 25.0:
                # gun sense: a sniper holds its lane and lets him show, it does not walk round the corner into him
                want *= 0.4
            if snap.mode == "infection" and s.order.role == "defender":
                want *= 0.2      # Infection: a survivor in its hold, an infected at its corner: let him come (issue #3)
            scores[("hunt", b.pos)] = max(scores.get(("hunt", b.pos), 0.0), want)   # (never under the close-dot check's)
            hit = [b for b in known if b.source == "hit" and now - b.t < 2.0]
            if hit:   # being shot by someone we have not seen: turn and find him (everyone does)
                scores[("hunt", hit[-1].pos)] = max(scores.get(("hunt", hit[-1].pos), 0.0), 1.4 + 0.4 * s.p.aggression)
            att = getattr(s.per, "attacker_id", None)
            # (not before it notices who it is, its reaction time off to the side: turning at once, Legendary answered a
            # shot in 0.20 s to the player's 0.40, live Spire; the mid-fight turn, shot_by_other, waited already)
            if (att is not None and now - getattr(s.per, "attacker_t", -9.0) < 1.0 and s.p.tactics > 0.15
                    and now >= getattr(s.per, "attacker_notice", 0.0)
                    and att not in seen and (s.goal[0] != "fight" or s.goal[1] != att) and att in s.per.beliefs
                    and not s.finishing(snap, seen)
                    and s.turn_ok(now, me, seen, dist(me.pos, s.per.beliefs[att].pos), snap, att)):
                # shot from behind while fighting someone else: a tactical player turns to find him
                # (it beats the committed fight); a novice keeps shooting the man in front
                scores[("hunt", s.per.beliefs[att].pos)] = 2.7 + 0.5 * s.p.tactics
        # Easy knows no power spots; a camper with an objective job works that, not the team's nearest hold: on every
        # CTF map checked the first hold IS the flag stand, so camper defenders scored it 0.74 over their cover post
        # 0.71 and crouched on the flag
        if (s.p.camping > 0.5 and s.p.map_iq > 0.15 and not me.has_flag
                and s.order.role not in ("runner", "escort", "defender", "hunter")):
            spot = min(s.map["holds"][s.team], key=lambda h: dist(h, me.pos))
            scores[("hold", spot)] = 0.2 + 0.6 * s.p.camping
        if not me.has_flag and getattr(s, "can_pickup", True):   # H2A / H4: pickups not decoded: no trips
            it = s.best_item(now, me)
            if it:
                scores[("item", it[0])] = it[1]
            rearm = s.rearm_point(me, snap)
            if rearm is not None:     # out of ammo: nothing matters more than a loaded gun
                scores[("item", rearm)] = 3.0 if s.dry(me) else (1.4 if snap.mode == "firefight" else 0.9)
            up = s.upgrade_point(me, snap, reach=UPGRADE_R)
            # a better gun lying close (a dead man's sniper): a player takes it unless a man is on him
            close_foe = any(k in s.per.beliefs and dist(me.pos, s.per.beliefs[k].pos) < 12.0 for k in seen)
            if up is not None and not close_foe and not hit_recent:
                # (on its way, a step or two: over the walk back to its post, 1.2 + 0.6 at most; keep_role lets it be)
                scores[("item", up)] = max(scores.get(("item", up), 0.0),
                                           1.9 if dist(me.pos, up) < UPGRADE_ON_WAY else 1.2 + 0.4 * s.p.map_iq)
            fetch = getattr(s, "fetch", None)
            if fetch is not None and now < fetch[2]:
                # the team's run for a power weapon lying loose (director.power_runs): this bot is the one sent, its
                # post does not hold it back (keep_role; 1.85 is over the way back to a post, 1.2 + 0.6 at most, under a
                # fight it is in, 1.5 + 0.4 aggression + 0.2 kept) (not over a man in sight within 15 u or one hurting
                # us: a fetcher who walked past fights lost Normal a third of its kills in the sim once runs went to
                # every mode isoR / isoP)
                busy = hit_recent or any(dist(me.pos, s.per.beliefs[k].pos) < 15.0 for k in seen)
                scores[("item", fetch[0])] = max(scores.get(("item", fetch[0]), 0.0), 1.2 if busy else 1.85)
            # urgency: while a man runs with our flag or the core, a gun lying about waits unless ours are dry or it is
            # right by us (one theft drew 66 gun choices from its 6 defenders in 10 s, Boardwalk)
            if s.theft(me, snap) and not s.dry(me):
                for k in [k for k in scores if k[0] == "item" and dist(me.pos, k[1]) > 3.0]:
                    scores[k] *= 0.25
        s.vehicle_mission(now, me, snap, scores)
        s.keep_role(scores, me, snap, seen)
        s.break_stalemates(now, me, snap, scores)
        if snap.mode == "race":
            # Race scores laps, not kills (RACE-01): the gate first; far fights, hunts and guns lying about only on the
            # way (a man close in is still fought, below). Live Rat Race: bots chose fights in 50-90% of their
            # decisions and the objective in 1-25%, 4 gates taken by 16 players in 44 s
            for k in scores:
                if k[0] in ("fight", "hunt", "item", "hold"):
                    scores[k] *= 0.3
        if not me.has_flag and scores:
            # a man in sight within CLOSE_FIGHT_R is fought whatever the post (live Spire Invasion: an attacker and a
            # defender walked 14 s side by side at 0.4 u to the same territory, each scoring its post 2.0 / 1.8 over
            # the other 1.7 / 1.6, neither shooting; 37% of the match's decisions with a man in sight under 8 u chose
            # the post). Shields down, backing off to cover still wins past arm's length (a CTF runner at their flag
            # grabs it first: only a man at arm's length is fought there, GRAB_R)
            theirs = (snap.flags or {}).get(1 - me.team % 2) if snap.mode == "ctf" and s.order.role == "runner" else None
            grab = theirs is not None and theirs.state != "carried" and s.grab_ok(me, snap, theirs.pos, seen)
            for key in [k for k in scores if k[0] == "fight" and k[1] in seen]:
                b = s.per.beliefs.get(key[1])
                d = dist(me.pos, b.pos) if b is not None else 99.0
                if d < (3.0 if grab else CLOSE_FIGHT_R) and regroup is None:   # (alone against two: regroup_point)
                    top = max(v for k, v in scores.items() if k[0] != "retreat" or d < 3.0)
                    scores[key] = max(scores[key], top + 0.25)
        infected = False
        if snap.mode == "infection":
            from .modes import zombie_team
            infected = zombie_team(snap) == me.team
            if infected:
                # the infected take no guns, seats or rests: they hunt (owner, Nov 4: Halo 3's zombies "weren't
                # aggressive enough"; his Deadlock game: their decisions went to items 49%, retreats 6%, vehicles 4%,
                # where Halo 4's Flood, with pickups off there, hunted; 1.0 conversions a zombie-minute to 1.5-2.5)
                for k in [k for k in scores if k[0] in ("item", "vehicle", "retreat", "hold")]:
                    del scores[k]
        s.board_check(me, snap, scores)
        s.stay_on(scores, me, snap, seen)
        bans = getattr(s, "goal_ban", {})
        grab = getattr(s, "grab_point", None)
        for k in [k for k in scores if k[0] in ("objective", "item", "hunt", "hold") and bans.get(s._goal_key(k), 0) > now]:
            if k[0] == "objective" and grab is not None and dist(k[1], grab) < 1.0:
                continue                    # an objective to pick up is never given up (headway; Spire, live)
            del scores[k]                   # gave up on it lately: it could not be reached
            if k[0] == "objective" and s.order.point is not None:
                # another spot by the objective, not a roam
                alt = s.alt_post(now, k[1], bans)
                if alt is not None:
                    scores[("objective", alt)] = 0.5 + 0.7 * s.p.objective
        # what the bot weighed and why (tools/observer.py): the scores and the facts behind them
        mv = next((x for x in snap.vehicles if x["handle"] == me.vehicle), None) if me.vehicle not in (None, -1) else None
        if (mv is not None and getattr(s, "seat", None) == "driver"
                and (vehicles.spec(mv["kind"]) or {}).get("move") == "air"):
            # a pilot does not fly off for a gun it cannot take from the seat or away from the man it should hunt: live
            # (Shrine 20261104-154714, bot 9's Banshee) item and retreat were 1221 of its 2852 choices, the fight 331
            for k in [k for k in scores if k[0] in ("item", "retreat")]:
                del scores[k]
        s.dry_fights(scores, me)
        s.last_scores = scores
        s.last_flags = dict(post=round(post, 2), seen=len(seen), friends=friends, avoid=avoid, cracked=cracked,
                            recharge=now < getattr(s, "recharge_until", 0.0), hit=hit_recent)
        if not scores:
            return ("roam", None)
        if s.goal in scores and s.goal[0] != "hold":
            scores[s.goal] += 0.2           # humans commit: no flip-flopping between two close goals
                                            # (a camper still gets restless now and then)
        best = max(scores, key=lambda k: scores[k] + s.rng.uniform(0, 0.15))
        # the director called it onto a gun or a seat (a honk): it goes unless the fight is close. Any fight used to
        # win: live, Boneyard, two bots called to a Warthog's gun traded shots 25 u away and never got there
        close_fight = best[0] == "fight" and best[1] in s.per.beliefs and dist(me.pos, s.per.beliefs[best[1]].pos) < 8.0
        # (the core at stake: on foot, by the lift and the drops; on a loose objective: no seat, no ride, stay_on)
        urgent = s.urgent(me, snap) or getattr(s, "stay_at", None) is not None
        if s.order.role in ("ride", "board", "drive") and not close_fight and not urgent:
            # (drive: the team's pick for a role, director.crew_orders, from up to its CREW_REACH)
            v = next((v for v in snap.vehicles if v["handle"] == s.order.target), None)
            if v is not None and dist(me.pos, Vec(*v["pos"])) < (50 if s.order.role == "drive" else 30) \
                    and vehicles.usable(v, now, s.duds()) and not s.veh_banned(v, now):
                s.vehicle_until = max(getattr(s, "vehicle_until", 0.0), now + 4.0)
                return ("vehicle", v["handle"])
        if s.goal[0] == "vehicle" and best[0] != "fight" and not urgent:
            v = next((v for v in snap.vehicles if v["handle"] == s.goal[1]), None)
            if (v and (not v["occupied"] or s.order.role in ("ride", "board")) and now < getattr(s, "vehicle_until", 0.0)
                    and vehicles.usable(v, now, s.duds()) and not s.veh_banned(v, now)):
                return s.goal                  # committed to this vehicle run
            if now >= getattr(s, "vehicle_until", 0.0):
                s.vehicle_retry_at = now + 20.0   # could not get in: walk for a while
        far = best[1] if isinstance(best[1], Vec) else None
        errand = getattr(s, "errand", None)
        if errand is not None and not close_fight:
            # spawned at River in phases 2-3 for the free Warthog or Falcon by it: straight to it, then on to the fight
            v = next((v for v in snap.vehicles if v["handle"] == errand[0]), None)
            if (now < errand[1] and v is not None and not v["occupied"] and me.vehicle in (None, -1) and not urgent
                    and vehicles.usable(v, now, s.duds())
                    and not me.has_flag and not s.veh_banned(v, now)):
                s.drive_to = far or s.objective_point(me)
                s.vehicle_until = max(getattr(s, "vehicle_until", 0.0), now + 4.0)
                return ("vehicle", v["handle"])
            s.errand = None
        # (a race is a whole lap of gates 15-50 u apart: every racer rides, RACE-01)
        # (not for a gate 12 u off: out of its Ghost under a gate on a ledge, a racer walks up, it does not get back in)
        trip = dist(me.pos, far) if far else 0.0
        trip = 40.0 if snap.mode == "race" and (trip > 12.0 or s.game == "reach") else trip   # (Reach: riding only counts)
        # (Firefight teammates stay on foot with the person; a ride to his gun is its own order: live Beachhead Nov 6,
        # 596 rows off to a Mongoose for a hunt 35.5 u away, and driving, they went into walls, GitHub #12)
        ff_mate = snap.mode == "firefight" and s.order.role not in ("ride", "board", "drive")   # (free bots walk too)
        if (far and trip > VEHICLE_TRIP and not me.has_flag and best[0] != "fight" and not urgent and not infected
                and not ff_mate and now >= getattr(s, "vehicle_retry_at", 0.0)):
            # any vehicle the table knows (brain/vehicles.py), by its value, nearness and the trip's length
            # (not on wheels to a goal far over it: live Spire 20261105-060020 phase 3, five attackers took turns in
            # the Warthog at the spire's foot for the core 37 u up, 300-1,200 rows each "waiting for crew", no pickup)
            pick = max((v for v in snap.vehicles if vehicles.usable(v, now, s.duds()) and not s.veh_banned(v, now)
                        and s.trip_ok(v, me, far)),
                       key=lambda v: vehicles.ride_score(v, me, far, trip), default=None)
            if (pick and vehicles.ride_score(pick, me, far, trip) > 0.15
                    and (snap.mode == "race" or s.rng.random() < 0.3 + 0.6 * s.p.map_iq)):
                s.drive_to = far               # where the trip goes
                s.vehicle_until = now + 8.0
                return ("vehicle", pick["handle"])
        return best

    @staticmethod
    def trip_ok(v, me, goal):
        """Vehicle v can make a trip to goal: an aircraft anywhere, a ground vehicle not to a goal GROUND_RISE over the
        bot (Spire's core on the spire's top, 37 u up)."""
        return goal.z - me.pos.z <= GROUND_RISE or (vehicles.spec(v["kind"]) or {}).get("move") == "air"

    def board_check(s, me, snap, scores):
        """Boarding: Halo 2 on, Normal and up on foot, an enemy-driven ground vehicle (BOARDABLE) within BOARD_TRY_R that
        is not running at it nose first over BOARD_SPEED (nor passing at twice that): its driver's seat is gone for (a
        hijack; a tank's hull), over any fight there. Not with the objective in hand."""
        if s.game == "ce" or s.p.tactics <= 0.15 or me.has_flag or me.vehicle not in (None, -1):
            return
        best = None
        for v in snap.vehicles:
            if (vehicles.kind_of(v["kind"]) not in vehicles.BOARDABLE or not v.get("occupied")
                    or v.get("team") in (me.team, -1, None) or getattr(s, "board_ban", {}).get(v["handle"], 0.0) > snap.time):
                continue
            vp = Vec(*v["pos"])
            d = dist(me.pos, vp)
            drv = next((p for p in snap.players if p.vehicle == v["handle"] and p.alive and p.team != me.team), None)
            if drv is None:
                continue
            speed = drv.vel.flat() if drv.vel is not None else 0.0
            # a slow tank whose driver looks away from us (BOARD_BLIND) is walked up on from farther (the player's board)
            stalk = (vehicles.kind_of(v["kind"]) in vehicles.TANKS and speed <= BOARD_SPEED / 2 and
                     abs(wrap(math.atan2(me.pos.y - vp.y, me.pos.x - vp.x) - drv.yaw)) > BOARD_BLIND)
            if d > (BOARD_STALK_R if stalk else BOARD_TRY_R):
                continue
            nose = abs(wrap(math.atan2(me.pos.y - vp.y, me.pos.x - vp.x) - s.heading(drv, v))) < math.radians(45)
            if (speed > BOARD_SPEED and nose) or speed > 2 * BOARD_SPEED:
                continue
            if best is None or d < best[0]:
                best = (d, v)
        if best is None:
            return
        scores[("board", best[1]["handle"])] = max(max(scores.values(), default=0.0) + 0.3, 2.6)
        s.note(f"board: the {best[1]['kind']}")

    def grab_ok(s, me, snap, flag, seen):
        """The grab first at their flag (GRAB_R): inside GRAB_R, with no man of theirs seen within 12 u of it or with a
        teammate within GRAB_MATE_R of us (alone and guarded the room is cleared first: a carrier cannot shoot back, and
        the sim's lone pulls on The Pit died 0.9-2.3 s after the grab with nobody of ours within 21 u)."""
        if dist(me.pos, flag) >= GRAB_R:
            return False
        if not GRAB_NEEDS_COMPANY:
            return True
        guarded = any(k in s.per.beliefs and dist(s.per.beliefs[k].pos, flag) < 12 for k in seen)
        return not guarded or any(p.team == me.team and p.alive and p.id != me.id and dist(p.pos, me.pos) < GRAB_MATE_R
                                  for p in snap.players)

    def duds(s):
        """Vehicles nobody gets into lately (handle -> until), shared by every bot on the map (vehicles.DUD_S)."""
        return s.map.setdefault("_vehicle_duds", {})

    def dud_check(s, now, dt, me, v):
        """Whether v may still be gone for: vehicles.usable, and the time this bot has stood within DUD_NEAR of it on the
        way in without getting in under DUD_S (then it is a dud for everyone DUD_FOR s)."""
        duds = s.duds()
        if not vehicles.usable(v, now, duds):
            return False
        t = getattr(s, "veh_try", None)
        # (an empty one only: a teammate's, a person's above all, is never shunned for a seat that did not take)
        if not v.get("occupied") and dist(me.pos, Vec(*v["pos"])) < vehicles.DUD_NEAR:
            t = s.veh_try = [v["handle"], 0.0] if t is None or t[0] != v["handle"] else t
            t[1] += dt
            if t[1] > vehicles.DUD_S:
                duds[v["handle"]], s.veh_try = now + vehicles.DUD_FOR, None
                s.note(f"vehicle: cannot get into the {v['kind']}, leaving it")
                return False
        return True

    @staticmethod
    def theft(me, snap):
        """An enemy is running with our flag (CTF) or with the Invasion core while we defend it."""
        f = (snap.flags or {}).get(me.team)
        if f is not None and f.state == "carried" and f.carrier not in (None, -1):
            return True
        return (snap.mode == "invasion" and snap.inv_core_carrier is not None and snap.inv_attackers != me.team
                and not snap.inv_armed)

    @staticmethod
    def zone_at(snap, point):
        """The size of the objective zone a post is in (Invasion's territories, Territories, Infection's havens,
        Headhunter's drop points), or None when the post is not one. (Headhunter's runners stood still on their drop
        point, no input, 41-65% of Normal to Legendary's standing time: Boardwalk, logs/tmp/stand_why.py)"""
        zones = list(zip(snap.inv_zones, snap.inv_zone_r)) if snap.mode == "invasion" else \
            [(z, r) for z, r, o in snap.obj_zones] if snap.mode in ("territories", "infection", "headhunter") else []
        best = min(zones, key=lambda zr: dist(zr[0], point), default=None)
        return best[1] if best is not None and dist(best[0], point) < best[1] + 0.5 else None

    def held_zone(s, snap):
        """(centre, radius) of the zone this bot's post is to be held from inside (an Invasion attacker's territory, an
        Infection zone, the hill), else None: the zones the post leash keeps it in (role "defender")."""
        if getattr(s, "stay_at", None) is not None:
            return s.stay_at, STAY_HOLD_R             # on a loose objective it works by standing on it (stay_on)
        o = s.order
        if o.role != "defender" or o.point is None:
            return None
        if snap.mode == "invasion" and snap.inv_attackers == s.team and snap.inv_phase in (1, 2):
            zones = list(zip(snap.inv_zones, snap.inv_zone_r))
        elif snap.mode == "infection":
            # (not Territories: both teams' holders fought inside the same zone and it stayed contested, the sim's
            # Boneyard 4 takes against 9 without, 32 points against 109: logs/tmp/invasion_regress/zonesim.py)
            zones = [(z, r) for z, r, _ in snap.obj_zones]
        elif snap.mode == "king" and snap.hill is not None:
            zones = [(snap.hill, snap.hill_r or DEFEND_R)]
        else:
            return None
        best = min(zones, key=lambda zr: dist(zr[0], o.point), default=None)
        return best if best is not None and dist(best[0], o.point) < 3.0 else None

    def out_of_held(s, me, snap, q):
        """In the zone it holds (held_zone) and q outside it: a gun there is no step 'on its way' (keep_role's
        UPGRADE_ON_WAY). Live Boneyard B22 R1: the Elites' 12.0 s uncontested hold of the south territory ended with the
        lone holder stepping 1.4 u out for a gun lying 1.8 u off (B14 R1 the same)."""
        z = s.held_zone(snap)
        if z is None:
            return False
        c, r = z
        return (me.pos - c).flat() <= r + 0.3 and abs(me.pos.z - c.z) < 2.0 and (q - c).flat() > r

    def keep_in_zone(s, me, snap, act):
        """A zone holder in its zone fights from inside it: the fight's steps (band, strafe, juke, chase) lose their
        outward part past 0.7 of the zone's size, and past 0.85 of it it steps back in. Live Boneyard B9-B12, the
        attackers' phase 1 holds: 13 of 18 ended with the holder 1.2-2.7 u out of a box 0.86 u to its edge fighting
        (logs/tmp/invasion_regress/holdends.py), none held the 20 s a capture takes. Within ZONE_STEP_IN of it on its
        floor, the line in clear, it fights its way in: the steps toward or away from the man become steps into the zone,
        the strafe kept (B13 R1: the Elites' fights on the platform 1-4 u off the territories, 7.5 s in in 270 s)."""
        z = s.held_zone(snap)
        if z is None or me.vehicle != -1:
            return
        c, r = z
        ox, oy = me.pos.x - c.x, me.pos.y - c.y
        d = math.hypot(ox, oy)
        h, m = act.yaw + math.atan2(act.strafe, act.forward), min(1.0, math.hypot(act.forward, act.strafe))
        vx, vy = m * math.cos(h), m * math.sin(h)
        ux, uy = (ox / d, oy / d) if d > 1e-3 else (0.0, 0.0)
        out = vx * ux + vy * uy
        # (the step in: an Invasion territory or a loose objective it stays on; a hill's taker waits outside for company,
        # KOTH-STATIC-10, stage. A core's or flag's origin sits up to 0.8 u over the feet: Spire 20261105-143525)
        stay = getattr(s, "stay_at", None) is not None
        if (snap.mode == "invasion" or stay) and r + 0.3 < d <= r + ZONE_STEP_IN and \
                abs(me.pos.z - c.z) < (1.2 if stay else 0.6) and \
                s.T().clearance(s.feet(me), math.atan2(-uy, -ux), far=d) >= d - r:
            vx, vy = vx - out * ux - 0.9 * ux, vy - out * uy - 0.9 * uy
            n = math.hypot(vx, vy)
            cy, sy = math.cos(act.yaw), math.sin(act.yaw)
            act.forward, act.strafe = (vx * cy + vy * sy) / max(1.0, n), (-vx * sy + vy * cy) / max(1.0, n)
            act.sprint = False
            s.note("fight: stepping into the zone")
            return
        if d > r + 0.3 or abs(me.pos.z - c.z) > 2.0:
            return                                   # not in it: the post brings it in
        if d > 0.7 * r and out > 0.0:
            vx, vy = vx - out * ux, vy - out * uy
        if d > 0.85 * r:
            vx, vy = vx - 0.6 * ux, vy - 0.6 * uy
        n = math.hypot(vx, vy)
        if n > 1.0:
            vx, vy = vx / n, vy / n
        if (vx, vy) != (m * math.cos(h), m * math.sin(h)):
            cy, sy = math.cos(act.yaw), math.sin(act.yaw)
            act.forward, act.strafe = vx * cy + vy * sy, -vx * sy + vy * cy
            act.sprint = False
            s.note("fight: in the zone")

    @staticmethod
    def loose_point(me, snap):
        """Where an objective this bot may take lies loose (the Invasion core or Breakpoint's bomb for the attackers,
        Assault's bomb, a Stockpile flag not in our goal), the nearest; None. Race: this racer's next gate (walked onto
        all the way, move_to's last steps: live Rat Race, bots stopped 1.1-1.6 u off a gate the brain counts at 1.05)."""
        if snap.mode == "race":
            return snap.race_next.get(me.id)
        if snap.mode == "invasion":
            # (Breakpoint's armed bomb: the defenders' to walk onto and disarm, never the attackers' to take again)
            ok = ((snap.inv_attackers == me.team) != snap.inv_armed) and snap.inv_core is not None                 and snap.inv_core_carrier is None
            return snap.inv_core if ok else None
        if snap.mode not in ("assault", "stockpile"):
            return None
        goals = snap.obj_goals.get(me.team) or [] if snap.mode == "stockpile" else []
        # (Assault's armed bomb, as Breakpoint's: the one on our goal ours to walk onto and disarm, ours on theirs never
        # taken again; modes.Watch marks it)
        free = [it["pos"] for it in snap.obj_items if it["carrier"] is None and (
                it.get("armed") == me.team if it.get("armed") is not None else
                it.get("team", 8) in (8, me.team) and not any(dist(it["pos"], g) < 2.0 for g in goals))]
        return min(free, key=lambda q: dist(q, me.pos), default=None)

    @staticmethod
    def return_point(me, snap):
        """Where an objective of the other side's to take lies dropped for this bot to return by standing on it: the
        Invasion core or Breakpoint's bomb off its stand (a defender's: Reach takes it home after the variant's Core
        Return Time on it, 30 s on the owner's Tempest), our CTF flag dropped; None."""
        if snap.mode == "invasion":
            ok = (snap.inv_attackers != me.team and not snap.inv_armed and snap.inv_core is not None
                  and snap.inv_core_carrier is None and snap.inv_stand is not None
                  and (snap.inv_core - snap.inv_stand).flat() > 1.5)
            return snap.inv_core if ok else None
        f = (snap.flags or {}).get(me.team) if snap.mode == "ctf" else None
        return f.pos if f is not None and f.state == "dropped" else None

    def stay_on(s, scores, me, snap, seen):
        """On a loose objective it works by standing on it (stay_at: within STAY_R of grab_point, on foot): the core or
        a bomb to take or disarm, the core or our flag to return. It stays on it until it is done: the walk onto it
        (STAY_SCORE) over every post, gun, hunt, ride and reposition; a fight is fought from on it (held_zone:
        keep_in_zone holds its steps within STAY_HOLD_R). It leaves only to save its life: shield gone, body under
        half, a man in sight (a retreat then still counts). Live Spire 20261105-143525: the core lay loose 129 s 9.6 u
        short of the delivery point, never returned; 26 defender visits within 2 u of it, the median 1.8 s, each ended
        by a new post, a gun, a fight 2-6 u off or a reposition after a kill (tools/test_core_stay.py)."""
        q = getattr(s, "stay_at", None)
        if q is None:
            return
        dying = bool(seen) and me.health < 0.5 and (me.shield < 0.05 or snap.shieldless)
        # (never under what its objective scored already: a runner at the stand weighs it 2+ over a new fight)
        job = max([STAY_SCORE] + [v for k, v in scores.items() if k[0] == "objective"])
        for k in [k for k in scores if not (k[0] == "fight" or (k[0] == "retreat" and dying))]:
            del scores[k]
        scores[("objective", q)] = job

    @staticmethod
    def on_loose_objective(me, snap, reach=0.9):
        # (0.9: the pickups came 0.07-0.59 u off the objective, Breakpoint's bomb and Spire's core; from 1.6, kept since
        # Breakpoint's pedestal held attackers 1.38-1.66 u off it, Use only swapped their guns for the one lying by
        # Spire's stand and turned their view off the defenders there: no carrier in 256 s of phase 3, live. Getting in
        # close is move_to's last steps)
        """Where the objective it stands on and may take lies (None: not on one): loose_point's within reach (Use on an
        armed bomb on our goal disarms it). Picking up is take()."""
        q = Bot.loose_point(me, snap) if snap.mode != "race" else None     # (a gate is run through: nothing to take)
        return q if q is not None and dist(me.pos, q) < reach else None

    @staticmethod
    def use_flag_point(me, snap, game, reach=0.9):
        """H2A's CTF flag is taken with Use, as a gun (native/h4f.cpp f_interaction); the other games take it by walking
        onto it. The other team's flag, not carried, within reach: where to hold Use (take); None. (Live Lockdown Multi
        Flag CTF, Nov 6 s177: runners stood 0.41-0.89 u from red's flag 122 rows, Use in none of them, 0-0)"""
        if game != "h2a" or snap.mode != "ctf" or me.has_flag:
            return None
        f = (snap.flags or {}).get(1 - me.team % 2)
        return f.pos if f is not None and f.state != "carried" and dist(me.pos, f.pos) < reach else None

    @staticmethod
    def take(me, act, pos):
        """Pick up the objective at pos: Use held with the view on it. Reach's Use takes what the view is on: with a gun
        lying on the CTF stand it swapped guns instead (Boardwalk, live: a runner 50 s on the flag), and a Spire runner
        0.6 u off the core held Use 45 s looking past it (live; the pickups came as a view swept over it). The walk
        keeps its way under the turned view."""
        move, sp = act.yaw + math.atan2(act.strafe, act.forward), math.hypot(act.forward, act.strafe)
        act.yaw, act.pitch = angles_to(eye(me), pos)
        act.forward, act.strafe = sp * math.cos(move - act.yaw), sp * math.sin(move - act.yaw)
        act.action = True

    @staticmethod
    def seat_point(v, seat, side=1):
        """Where to stand to get into a vehicle's seat: the driver's door is front left, a gun at the back (a Warthog's
        turret, an Elephant's guns), by the vehicle's facing; its middle when the facing is not read (CE). A Falcon's guns
        are at its side doors, one each side (side +1 left, -1 right)."""
        c = Vec(*v["pos"])
        f = v.get("fwd")
        sp = vehicles.spec(v["kind"])
        if not f or not sp or sp["move"] == "none":
            return c
        n = math.hypot(f[0], f[1]) or 1.0
        fx, fy = f[0] / n, f[1] / n
        if seat == "gunner" and vehicles.kind_of(v["kind"]) == "falcon":
            # (it stood 1 u behind his Falcon 12 s, as far from both side guns, by neither door; botcore takes the
            # gun nearest the bot)
            return Vec(c.x - fy * 1.6 * side, c.y + fx * 1.6 * side, c.z)
        if seat == "gunner" and "gunner" in sp["seats"]:
            back = 2.0 if sp["move"] != "air" else 1.0
            return Vec(c.x - fx * back, c.y - fy * back, c.z)
        return Vec(c.x + fx * 0.3 - fy * 1.3, c.y + fy * 0.3 + fx * 1.3, c.z)   # front left

    def weak_point(s, me, k, b, snap, gun):
        """Where an explosive or gauss shot goes on a tank (guide 17 §4.4; Reach's materials: its back hatch takes 3x a
        large explosion and 2x a direct hit): its rear, when we are behind its beam; else where he is. On foot too, with
        a gun that kills vehicles (audit §4.4: rockets never went at the hatch)."""
        p = snap.player(k)
        pv = next((x for x in snap.vehicles if x["handle"] == p.vehicle), None) if p is not None and p.vehicle != -1 \
            else None
        f = pv.get("fwd") if pv is not None else None
        if ((gun not in ("cannon", "rocket_hog", "revenant_mortar", "gauss") and gun not in VEHICLE_KILLERS)
                or not f or math.hypot(f[0], f[1]) < 0.1
                or vehicles.kind_of(pv["kind"]) not in vehicles.TANKS):
            return b.pos
        n = math.hypot(f[0], f[1])
        if f[0] * (me.pos.x - b.pos.x) + f[1] * (me.pos.y - b.pos.y) >= 0:
            return b.pos                                  # in front of its beam: the hatch is out of sight
        return Vec(b.pos.x - f[0] / n * 0.8, b.pos.y - f[1] / n * 0.8, b.pos.z)

    def crew_target(s, me, snap, v, seen, own=False):
        """Who a seat shoots, or the driver keeps the guns' arc on (guide 17 §4.5): the man in sight that our gunner's
        view is on (a person's or a bot's: the snapshot's view), else, for a gun hung on one side of the vehicle (a
        Falcon's), those on that side (it cannot swing across); of them the one the gun should fight (vehicle_pick:
        own, the gunner itself, or the driver steering for it), a passenger the closest. None: nobody in sight (or
        nobody worth the gun's shot)."""
        if not seen:
            return None
        bel = s.per.beliefs
        if not own and v is not None:
            for p in snap.players:
                if p.id != me.id and p.alive and p.team == me.team and p.vehicle == v["handle"] and p.gunner_seat:
                    on = min(seen, key=lambda k: abs(wrap(math.atan2(bel[k].pos.y - me.pos.y, bel[k].pos.x - me.pos.x)
                                                          - p.yaw)))
                    if abs(wrap(math.atan2(bel[on].pos.y - me.pos.y, bel[on].pos.x - me.pos.x) - p.yaw)) < 0.3:
                        return on
        side = 1 if me.turret.endswith("left") else -1 if me.turret.endswith("right") else 0
        f = v.get("fwd") if v is not None else None
        if own and side and f and math.hypot(f[0], f[1]) > 0.1:
            head = math.atan2(f[1], f[0])
            mine = [k for k in seen if side * wrap(math.atan2(bel[k].pos.y - me.pos.y, bel[k].pos.x - me.pos.x) - head) > 0]
            seen = mine or seen
        if own or s.seat == "driver":
            return s.vehicle_pick(me, v, "gunner", seen, snap)
        return min(seen, key=lambda k: dist(me.pos, bel[k].pos))

    FORWARD_GUNS = {"ghost", "banshee", "chopper", "gungoose", "hornet", "sabre", "seraph", "revenant"}

    def vehicle_pick(s, me, v, seat, seen, snap):
        """The man in sight this seat's gun should fight (vehicles.target_score: guide 17 §4.2), the nearer of equals;
        None when its gun has nothing worth a shot (Reach's rocket hog holding fire on men on foot)."""
        kind = vehicles.kind_of(v["kind"]) if v is not None else None
        gun = vehicles.seat_gun(kind, seat) if kind else None
        if gun is None or not seen:
            return min(seen, key=lambda k: dist(me.pos, s.per.beliefs[k].pos)) if seen else None
        head, fwd_gun = s.heading(me, v), seat == "driver" and kind in s.FORWARD_GUNS
        own = snap.flags.get(me.team) if snap.flags else None
        air = (vehicles.spec(kind) or {}).get("move") == "air" and seat == "driver"
        best, top = None, 0.0
        for k in seen:
            b, p = s.per.beliefs[k], snap.player(k)
            if p is None:
                continue
            pv = next((x for x in snap.vehicles if x["handle"] == p.vehicle), None) if p.vehicle != -1 else None
            d = dist(me.pos, b.pos)
            obj = (2.0 if p.has_flag or (snap.mode == "invasion" and snap.inv_core_carrier == k) else
                   1.5 if snap.hill is not None and dist(b.pos, snap.hill) < max(snap.hill_r, 1.0) else
                   1.3 if own is not None and dist(b.pos, own.home) < 3.3 else 1.0)
            if air:                              # an aircraft's bolts and bombs splash: men bunched up go first
                obj *= 1.0 + 0.15 * sum(1 for j in seen if j != k and dist(s.per.beliefs[j].pos, b.pos) < 4.0)
            sc = vehicles.target_score(
                s.game, kind, gun, seat, vehicles.kind_of(pv["kind"]) if pv is not None else None,
                killer=p.weapon in VEHICLE_KILLERS, objective=obj,
                off=abs(wrap(math.atan2(b.pos.y - me.pos.y, b.pos.x - me.pos.x) - head)) if fwd_gun else None,
                up=math.atan2(b.pos.z - me.pos.z, max((b.pos - me.pos).flat(), 0.1)), moving=b.vel.flat(), d=d,
                in_ram=d < 18.0 and p.vehicle == -1) - 1e-4 * d
            if sc > top:
                best, top = k, sc
        return best

    @staticmethod
    def heading(me, v):
        """Which way the vehicle faces (its forward read; the view where the reader has none)."""
        f = v.get("fwd") if v is not None else None
        return math.atan2(f[1], f[0]) if f and math.hypot(f[0], f[1]) > 0.1 else me.yaw

    def ram_ok(s, me, v, st, snap, foe, b):
        """Run this target down (guide 17 §4.3)? A ground vehicle that rams (STRATEGY splatter), the target within 18 u: a
        man on foot with nothing that kills vehicles or, from Halo 2 on, a light vehicle ours kills head on
        (vehicles.RAM_VEHICLES). Never a man in Armor Lock: the rammer dies (Reach: REACH-CTF-04, KOTH-02, ASS-02). A
        vehicle with a gun of its own commits only with him within its nose's RAM_THETA (or on its run at him already);
        one without a gun turns onto him. Never Easy."""
        if (s.p.tier == "novice" or not st.get("splatter") or foe is None or b is None or dist(me.pos, b.pos) >= 18.0
                or snap.mode == "race"                  # (a racer keeps its line: RACE-01, no ramming)
                or (foe.equipment == "armor_lock" and foe.ability_on)):
            return False
        kind = vehicles.kind_of(v["kind"]) if v is not None else None
        if foe.vehicle == -1:
            if foe.weapon in ANTI_VEHICLE:
                return False
        else:
            fv = next((x for x in snap.vehicles if x["handle"] == foe.vehicle), None)
            if s.game == "ce" or fv is None or vehicles.kind_of(fv["kind"]) not in vehicles.RAM_VEHICLES.get(kind, ()):
                return False
        if v is None or not vehicles.driver_armed(v) or (getattr(s, "ram", None) or (None,))[0] == foe.id:
            return True
        off = abs(wrap(math.atan2(b.pos.y - me.pos.y, b.pos.x - me.pos.x) - s.heading(me, v)))
        return off < math.radians(vehicles.RAM_THETA.get(kind, 15.0))

    def evade(s, now, me, v, sp, snap):
        """The move that answers the worst threat in sight (guide 17 §4.6) as (why, yaw, forward, strafe, vertical,
        pitch), or None. A man within 5 u (15 m) holding an EMP gun (Halo 3 on): full throttle away from him. A vehicle
        killer aimed at us within 40 u: out of his line (a 90 degree change beats a tracking rocket), an aircraft also
        dives. An enemy Scorpion's or Wraith's barrel within 10 degrees of us within 60 u: out of its line, an aircraft
        climbing over it. An enemy Banshee within 40 u: a Falcon or Hornet breaks off, away and down. A man in Armor
        Lock within 1.5 u: away. A man on foot who could board us, in view or a radar dot under 1 s old: away. Out of a
        line: a hover vehicle slides across it nose kept, a car turns across it. Tanks hold (they duel, and back off
        boarders in drive); a threat is answered after the tier's reaction time, by its juke trait, rolled once a
        threat (Easy never dodges)."""
        kind = vehicles.kind_of(v["kind"])
        if kind in ("scorpion", "wraith", "mantis", "elephant") or sp["move"] == "none":
            return None
        air, vtol, hover = sp["move"] == "air", bool(sp.get("vtol")) or kind == "hornet", sp["move"] == "hover"
        head = s.heading(me, v)
        first, roll = s.__dict__.setdefault("threat_first", {}), s.__dict__.setdefault("threat_roll", {})
        live, best = set(), None
        for p in snap.players:
            bel = s.per.beliefs.get(p.id)
            if p.team == me.team or not p.alive or bel is None:
                continue
            q = p.pos if bel.seen else bel.pos            # (a radar dot: where it showed him)
            d = dist(me.pos, q)
            # a man on foot close enough to board us (guide 17 §4.1 boarding_alert_radius: 6 m, 2 u; 10 m, 3.3 u, for a
            # hovering aircraft; 18 TIER-VEH-07), never the man we are running down; behind the seat he is often only
            # on the radar (§4.6: "Accelerate and turn away ... a vehicle moving at full speed cannot be boarded"). An
            # aircraft only hovering: a Banshee's attack run passes low over its man faster than he runs
            board = (p.vehicle == -1 and d < (3.3 if air else 2.0) and not (air and me.vel.flat() > 3.0)
                     and p.id != (getattr(s, "ram", None) or (None,))[0] and now >= getattr(s, "no_flee_until", 0.0))
            if not bel.seen and not (board and bel.source == "radar" and now - bel.t < 1.0):
                continue
            away = math.atan2(me.pos.y - q.y, me.pos.x - q.x)          # his line to us
            aimed = abs(wrap(p.yaw - away))
            pv = next((x for x in snap.vehicles if x["handle"] == p.vehicle), None) if p.vehicle != -1 else None
            pk = vehicles.kind_of(pv["kind"]) if pv is not None else None
            # out of his line, across it on our side of it: a car turns that way, a hover vehicle slides that way nose
            # kept (strafe is to the left)
            md = away + (math.pi / 2 if wrap(head - away) > 0 else -math.pi / 2)
            out = (head, math.cos(md - head), math.sin(md - head)) if hover else (md, 1.0, 0.0)
            m = None
            if board:
                m = (3, "away from a boarder", away if air else s.open_way(me, away), 1.0, 0.0, None, None)
            elif p.vehicle == -1 and p.weapon in EMP_GUNS and s.game in EMP_GAMES and d < 5.0:
                m = (3, f"away from the {p.weapon}", away, 1.0, 0.0, None, None)
            elif p.vehicle == -1 and p.equipment == "armor_lock" and p.ability_on and d < 1.5:
                m = (3, "away from Armor Lock", away, 1.0, 0.0, None, None)
            elif p.vehicle == -1 and p.weapon in VEHICLE_KILLERS and d < 40.0 and aimed < 0.12:
                m = (2, f"out of a {p.weapon}'s line", *out, -1.0 if vtol else None, -0.35 if air and not vtol else None)
            elif pk in ("scorpion", "wraith") and not p.gunner_seat and d < 60.0 and aimed < math.radians(10.0):
                m = ((2, f"over a {pk}'s barrel", head, 1.0, 0.0, 1.0 if vtol else None, None if vtol else 0.45) if air
                     else (2, f"out of a {pk}'s line", *out, None, None))
            elif pk == "banshee" and vtol and d < 40.0:
                m = (1, "breaking off from a Banshee", away, 1.0, 0.0, -1.0, None)
            if m is None:
                continue
            key = (p.id, m[1])
            live.add(key)
            t0 = first.setdefault(key, now)
            if now - t0 < s.p.reaction or not roll.setdefault(key, s.rng.random() < s.p.juke):
                continue
            if best is None or (m[0], -d) > (best[0], -best[-1]):
                best = (*m, d)
        for key in [k for k in first if k not in live]:      # a threat gone: the next one is noticed and rolled anew
            first.pop(key, None)
            roll.pop(key, None)
        return None if best is None else best[1:-1]

    # --- vehicles (v2: every kind in brain/vehicles.py, by seat)
    def rider_look(s, now, dt, me, v):
        """A gunner or passenger with nobody in sight looks for someone, as people in a seat do: toward the last place it
        knew a man (8 s), else across its gun's side (a side gun) or all round, 90 degrees a second. The yaw to send."""
        bel = [(b.t, b.pos) for k, b in s.per.beliefs.items()
               if isinstance(k, int) and now - b.t < 8.0 and dist(me.pos, b.pos) < 45.0]
        side = 1 if me.turret.endswith("left") else -1 if me.turret.endswith("right") else 0
        f = v.get("fwd")
        if bel:
            want = angles_to(eye(me), max(bel, key=lambda x: x[0])[1])[0]
            s.att = ["scan", "last seen", round(me.pos.x, 1), round(me.pos.y, 1)]
        elif side and f and math.hypot(f[0], f[1]) > 0.1:
            want = math.atan2(f[1], f[0]) + side * (math.pi / 2 + 0.8 * math.sin(now * 0.8))
            s.att = ["scan", "gun side", round(me.pos.x, 1), round(me.pos.y, 1)]
        else:
            want = me.yaw + 1.0
            s.att = ["scan", "all round", round(me.pos.x, 1), round(me.pos.y, 1)]
        d = wrap(want - me.yaw)
        return me.yaw + max(-1.6 * dt, min(1.6 * dt, d))

    def crew_coming(s, now, me, v, sp, snap, seen):
        """Guide 17 4.5 (a transport driver waits at the pickup until its riders are in or 8 s pass) and the player: just
        in at the wheel with a seat free, wait while a teammate on foot, person or bot, runs to it from within 8 u (8 s
        at most), or stands at its door (3 s: a look whether he gets in). Never with an objective aboard or a carrier to
        fetch, never once shot since getting in or with an enemy in sight inside 20 u; nobody near: off at once."""
        t = now - s.seat_since
        ids, t_call = getattr(s, "called_aboard", {}).get(me.vehicle, ((), -99.0))
        air = sp is not None and sp["move"] == "air"
        # (a transport aircraft's gunners are called from farther, director.AIR_CALL: it waits for them longer)
        called = set(ids) if now - t_call < (12.0 if air else 6.0) else set()   # (director.rider_calls' call)
        if sp is None or len(sp["seats"]) < 2 or v.get("riders", 0) >= len(sp["seats"]) or (t > 8.0 and not called):
            return False
        if me.has_flag or s.order.role == "ferry" or me.shield + me.health < getattr(s, "seat_vitals", 2.0) - 0.05:
            return False
        if any(dist(me.pos, s.per.beliefs[k].pos) < 20.0 for k in seen):
            return False
        vp = Vec(*v["pos"])
        for p in snap.players:
            if p.team != me.team or p.id == me.id or not p.alive or p.vehicle != -1:
                continue
            d = dist(p.pos, vp)
            if d < 3.0 and t < 3.0:
                return True                          # at its door
            if p.id in called and d < CALLED_WAIT_R * (2.0 if air else 1.0):
                return True                          # called aboard and on his way (the driver flagged him down)
            if 0.1 < d < 8.0 and ((vp.x - p.pos.x) * p.vel.x + (vp.y - p.pos.y) * p.vel.y) / d > 1.0:
                return True                          # running to it
        return False

    def hold_for_crew(s, now, me, act, air):
        """Stopped for crew to get in (crew_coming, a called gunner, a pickup). An aircraft sets down where it is: no
        throttle, nose level, a Falcon's lift down, and air_guard's landing rules (no pull-up off the floors near it).
        Live Spire 20261105-143525, 1031-1040 s: a Falcon waiting for the teammate called to its gun hovered 5-7 u over
        him as he walked under it (the floors rule climbed it off the ground by the spire's foot), and he never got in.
        Its take-off afterwards starts again from the ground (air_guard: air_held)."""
        act.forward = act.strafe = 0.0
        if air:
            act.pitch, act.vertical = 0.0, -1.0
            s.landing, s.air_dest, s.air_held = True, me.pos, now

    def drive(s, now, dt, me, snap, act, nav):
        """In a vehicle. Its seat: from who was in it when this bot got in (empty: the driver's seat, the door it walked
        up to; one in: the gun where the vehicle has one; else a passenger seat; a turret's only seat is its gun).
        A gunner or passenger aims and fires and never steers, and gets out once nobody drives. The driver steers by
        facing: a Ghost, Scorpion or Banshee shoot where they face; a Warthog's driver circles the fight at range so
        its gunner works, and waits a few seconds for a gunner the director called; a pilot holds height over the
        ground. A ferry order (the director: pick up our flag carrier) drives to him, then to where he scores."""
        if not all(math.isfinite(c) for c in (me.pos.x, me.pos.y, me.pos.z)):
            return                                # (no place read this tick: Spire, live, a tick failed on it)
        v = next((x for x in snap.vehicles if x["handle"] == me.vehicle), None)
        sp = vehicles.spec(v["kind"]) if v else None
        if now < getattr(s, "exit_until", 0.0):   # getting out: Use held (a one-tick press never left a Reach Warthog:
            act.action, act.fire = True, False    # a driver sat wedged 200 s with the throttle down, Boneyard)
            act.forward = act.strafe = 0.0
            return
        if now < getattr(s, "reverse_until", 0.0):   # backing off what it ran into
            act.forward, act.strafe, act.yaw, act.fire = -1.0, 0.0, s.reverse_yaw, False
            return
        if getattr(s, "seat_for", None) != me.vehicle:          # just got in
            s.seat_for, s.seat_since, s.exit_tries, s.still_at = me.vehicle, now, 0, None
            s.veh_try = None                             # (dud_check: it got in)
            s.rod = None                                 # (a Banshee is got into on its cannons)
            s.seat_vitals = me.shield + me.health        # (crew_coming: shot since getting in)
            inside = getattr(s, "enter_riders", 0)
            # where botcore seats a bot by the seat it asks for (Halo 4 / H2A by the seat's own flags, native/h4f.cpp
            # f_board; Reach by its index), that is its seat. Told by the riders inside, the first one into a Warthog's
            # passenger seat "drove" it from there and one at the wheel behind a gunner only aimed: nobody steered (H2A
            # Bloodline: Warthog "drivers" at a median 0.00 u/s with the throttle down, two "gunners" in one Warthog
            # and nobody at the wheel)
            asked = getattr(s, "enter_want", None)
            by_ask = asked in ("driver", "passenger") and s.map["name"].startswith(("h2a_", "h4_", "reach_"))
            # (where its guns ride on it as vehicles of their own, Reach's, a seat that is not one of them is a passenger's:
            # a Falcon's troop seat read as its gun)
            s.seat = ("gunner" if (sp and sp["move"] == "none") or me.gunner_seat else asked if by_ask else
                      "driver" if inside == 0 else
                      "passenger" if v is not None and v.get("turrets") else
                      "gunner" if sp and "gunner" in sp["seats"][1:] and inside == 1 else "passenger")
            # the team's pick for a role (director.crew_orders): it works the vehicle where the fights and the objective
            # are, never getting out at the end of a trip A tank too
            s.crew_ride = me.vehicle if s.seat == "driver" and (
                s.order.role == "drive" or (v is not None and vehicles.kind_of(v["kind"]) in vehicles.TANKS)) else None
            if s.crew_ride is not None:
                s.drive_to = s.patrol_point(me)
        seen = s.per.visible_enemies()
        bt = getattr(s, "board_try", None)
        if (bt is not None and bt[0] == me.vehicle and now - bt[1] < 15.0
                and any(p.vehicle == me.vehicle and p.alive and p.team != me.team for p in snap.players)):
            # on an enemy's vehicle we boarded (board_check): beat its driver out (and plant a grenade outside Reach).
            # Only the boarder: live (Boneyard 20261103-183101) every boarded driver stopped and meleed as well
            act.forward = act.strafe = 0.0
            # a press every BOARD_MELEE_EVERY (the player's); in Reach melee alone killed the driver, no grenade
            act.fire, act.melee = False, int(now / (BOARD_MELEE_EVERY / 2)) % 2 == 0
            if now - s.seat_since > 1.0 and getattr(s, "board_nade", None) != me.vehicle and s.game != "reach":
                act.grenade, s.board_nade = True, me.vehicle
            s.note("vehicle: aboard theirs, beating it")
            return
        if s.seat != "driver":
            if s.order.role == "give_seat":      # our objective holder needs this seat (director): out
                act.action, act.fire = int(now * 6) % 2 == 0, False
                act.forward = act.strafe = 0.0
                return
            gunner, fire, aimed = s.seat == "gunner", False, False
            tid = s.crew_target(me, snap, v, seen, own=gunner)
            if tid is not None and (gunner or not me.has_flag):
                aimed = True
                # a gunner shoots the seat's gun (its lead and aim point: vehicles.gunner_gun); a passenger his own gun at
                # the man the crew's gun is on, and not at all while he carries the objective (guide 17 §4.5)
                b = s.per.beliefs[tid]
                gun = vehicles.gunner_gun(v, me.turret) if gunner and v is not None else None
                aim_at = s.weak_point(me, tid, b, snap, vehicles.seat_gun(vehicles.kind_of(v["kind"]), "gunner")) \
                    if gunner and v is not None else b.pos
                h = vehicles.gun_height(me.turret, vehicles.kind_of(v["kind"]) if v is not None else None) if gunner else None
                act.yaw, act.pitch = s.aim.step(now, dt, me, tid, aim_at, b.vel, False, gun=gun,
                                                origin=me.pos + Vec(0, 0, h) if h is not None else None,
                                                crouched=crouched(snap.player(tid)))
                # a mounted gun is held down while it tracks, as people spray a turret: 2.5 times the hand gun's window,
                # 4 times once firing (live: a Falcon gunner 0.4 deg off a man 16 u away fired on 34% of its ticks by
                # the hand gun's window)
                fire = s.aim.on_target(me, aim_at, slack=(4.0 if getattr(s, "seat_fire", False) else 2.5) if gunner else 1.0)
                s.att = ["aim", tid, round(b.pos.x, 1), round(b.pos.y, 1)]
            elif gunner and v is not None and vehicles.kind_of(v["kind"]) == "warthog":
                # a chaingun spins up (5 to 15 rounds a second over its first second, Reach's tag: guide 17 §4.4): a man
                # who just went out of sight is expected back, so it stays spun up on where he went for 2 s
                gone = [k for k, q in s.per.beliefs.items() if isinstance(k, int) and now - q.t < 2.0
                        and dist(me.pos, q.pos) < 30.0]
                if gone:
                    k = max(gone, key=lambda k: s.per.beliefs[k].t)
                    q = s.per.beliefs[k]
                    act.yaw, act.pitch = s.aim.step(now, dt, me, None, q.pos, Vec(), False, gun="assault_rifle")
                    fire = aimed = True
                    s.att = ["spin", k, round(q.pos.x, 1), round(q.pos.y, 1)]
            if not aimed and v is not None and not me.has_flag:
                act.yaw = s.rider_look(now, dt, me, v)     # nobody in sight: look for him (a carrier rides it out)
            if gunner and s.game == "reach" and "chaingun" in (getattr(me, "turret", "") or ""):
                fire = s.chaingun_heat(dt, fire)
            act.fire = s.seat_fire = fire
            act.forward = act.strafe = 0.0
            # taken there (a taxi, director.vehicle_jobs): a runner out by their flag, a passenger by his objective, as
            # people hop off at the fight they rode to
            if me.has_flag and s.order.point is not None and (me.pos - s.order.point).flat() < 5.0 and now - s.seat_since > 1.0:
                act.action, act.fire = True, False   # ferried home: out on our stand to score
                s.note("vehicle: home with the flag, out")
                return
            goal = s.objective_point(me) if s.order.point is not None and not me.has_flag else None
            if (goal is not None and now - s.seat_since > 1.5 and (me.pos - goal).flat() < RIDE_OUT_R and snap.mode != "race"
                    and (s.order.role == "runner" or (not gunner and s.order.role in ("escort", "mid", "hunter")))):
                act.action, act.fire = True, False
                s.note("vehicle: there, out")
                return
            # nobody drives: get out. Alone in it, or still 5 s with no person aboard (a gunner and a passenger sat in a
            # Warthog with nobody at the wheel to the end, H2A Bloodline; a person may have parked to fight). A gunner
            # with a man to shoot keeps shooting first
            at = getattr(s, "still_at", None)
            if at is None or dist(me.pos, at) > 1.5:
                s.still_at, s.still_since = me.pos, now
            parked = now - s.still_since > 5.0 and not (gunner and tid is not None) and \
                not any(p.vehicle == me.vehicle and not p.is_bot and p.alive for p in snap.players)
            if v is not None and (v.get("riders", 0) <= 1 or parked) and sp and sp["move"] != "none" and now - s.seat_since > 4.0:
                act.action, act.fire = True, False
            return
        r = getattr(s, "ram", None)              # a run at a man (ram_ok): over when he is dead or after 6 s
        if r is not None:
            f = snap.player(r[0])
            if f is None or not f.alive or now - r[1] > 6.0:
                if f is not None and not f.alive and now - r[1] < 6.0 and dist(me.pos, f.pos) < 4.0:
                    # never stop beside the kill: on at full throttle 1.5 s (guide 17 §4.3 post_hit)
                    s.through_until, s.through_yaw = now + 1.5, s.heading(me, v)
                s.ram = None
        if now < getattr(s, "through_until", 0.0):   # a run that hit or missed: on through, then look again
            act.yaw, act.forward, act.strafe, act.fire = s.through_yaw, 1.0, 0.0, False
            s.att = ["through", None, round(me.pos.x, 1), round(me.pos.y, 1)]
            return
        if now >= getattr(s, "evade_until", 0.0) and v is not None and sp is not None:
            last = getattr(s, "evade_mv", None)
            if (last is not None and last[0] == "away from a boarder" and getattr(s, "evade_from", None) is not None
                    and now - s.evade_until < 0.5 and dist(me.pos, s.evade_from) < FLEE_PROGRESS):
                # the last flee got nowhere (a wall, a corner by the bases: live Avalanche 064500, 216 s of ground
                # stalls fleeing boarders): no flee for FLEE_REST s, the boarder is fought or run down instead
                s.no_flee_until, s.evade_mv = now + FLEE_REST, None
                s.note("vehicle: cannot get away from the boarder, fighting")
            mv = s.evade(now, me, v, sp, snap)
            if mv is not None:                   # held 1.2 s: one clean break, not a twitch every tick
                s.evade_until, s.evade_mv, s.evade_from = now + 1.2, mv, me.pos
                s.note("vehicle: " + mv[0])
        if now < getattr(s, "evade_until", 0.0):
            why, act.yaw, act.forward, act.strafe, vert, pitch = s.evade_mv
            if vert is not None:
                act.vertical = vert
            if pitch is not None:
                act.pitch = pitch
            act.fire = False
            s.att = ["evade", None, round(me.pos.x, 1), round(me.pos.y, 1)]
            return
        # guide 17 §4.1 "ANY -> RETREAT when ... driver_shield_down (H2/H3)": a driver sits in the open in Halo 2 and 3;
        # his shield broken with a man in view, he drives to the nearest teammate, else home, 4 s at a time (§4.6: "Move
        # toward the nearest friendly spawn or armoured friendly"). Normal and up (guide 18 2.1: Easy fights to the death)
        if (now >= getattr(s, "retreat_until", 0.0) and s.game in ("h2", "h3") and sp is not None and seen
                and sp["move"] not in ("air", "none") and me.shield <= 0.05 and not snap.shieldless
                and s.p.tactics > 0.15 and s.order.role != "ferry"):
            mates = [p.pos for p in snap.players if p.team == me.team and p.alive and p.id != me.id
                     and p.vehicle != me.vehicle]
            to = min(mates, key=lambda q: dist(me.pos, q)) if mates else s.map["flags"].get(s.team % 2)
            if to is not None and dist(me.pos, to) > 6.0:
                s.retreat_until, s.retreat_to = now + 4.0, to
                s.note("vehicle: shield down, back to " + ("a teammate" if mates else "base"))
        if now < getattr(s, "retreat_until", 0.0) and dist(me.pos, s.retreat_to) > 4.0:
            w = s.T().vnav(me.pos, s.retreat_to) if hasattr(s.T(), "vnav") else nav(me.pos, s.retreat_to)
            w = w if w is not None else s.retreat_to
            act.yaw, act.forward, act.strafe, act.fire = math.atan2(w.y - me.pos.y, w.x - me.pos.x), 1.0, 0.0, False
            s.att = ["retreat", None, round(w.x, 1), round(w.y, 1)]
            return
        target = getattr(s, "drive_to", None) or s.roam(me)
        if s.order.role == "ferry":              # pick up our carrier, then run him home
            c = snap.player(s.order.target)
            target = c.pos if c is not None and c.alive and c.vehicle != me.vehicle else (s.order.point or target)
        elif getattr(s, "vjob", None) == "getaway" and (me.pos - target).flat() < GETAWAY_PARK_R:
            # the getaway ride (director.vehicle_jobs): parked by their base, the passenger seat free, until our carrier
            # has the flag (then ferry); it stays in and does not wander off to fights
            act.forward = act.strafe = 0.0
            s.att = ["park", None, round(me.pos.x, 1), round(me.pos.y, 1)]
            s.note("vehicle: getaway, waiting for the carrier")
            return
        air = sp is not None and sp["move"] == "air"
        if getattr(s, "vjob", None) == "pickup" and s.order.role != "ferry" and (me.pos - target).flat() < 6.0:
            # by the teammate it came for (director.gunner_pickup): stopped for him to take the gun
            s.hold_for_crew(now, me, act, air)
            s.att = ["park", None, round(me.pos.x, 1), round(me.pos.y, 1)]
            s.note("vehicle: picking up a gunner")
            return
        wants_gunner = sp is not None and sp["gun"] == "gunner"
        # Easy uses a vehicle to get somewhere: no waiting for crew, no fight tactics (no runs, bombs, circling,
        # ramming); it drives its trip and shoots what it meets in front of it (meet, below)
        easy = s.p.tier == "novice"
        if wants_gunner and v is not None and v.get("riders", 0) == 1 and now - s.seat_since < 6.0 \
                and s.order.role in ("ride", "ferry", "driver_wait") and not easy:
            s.hold_for_crew(now, me, act, air)  # the gunner is coming (the director called one): wait for him
            return
        if v is not None and not easy and s.crew_coming(now, me, v, sp, snap, seen):
            s.hold_for_crew(now, me, act, air)  # a teammate is coming for a free seat: wait for him
            s.note("vehicle: waiting for crew")
            return
        st = vehicles.strategy(v["kind"]) if v is not None else dict(role="strafe", rng=(8.0, 25.0))
        gunned = wants_gunner and v is not None and bool(v.get("gunner") or v.get("riders", 0) >= 2)
        armed = v is not None and vehicles.driver_armed(v)
        # a taxi with riders for the objective (director.vehicle_jobs) drives its trip as Easy does, its gunner and its
        # own gun on whoever it meets, no circling round a fight (Avalanche, round 1: the attackers' runners spent 25%
        # of their time in vehicles and 57% of it 40-50 u from the flag, by their own base)
        meet, seen = (seen, []) if easy or getattr(s, "vjob", None) in ("taxi", "getaway", "pickup") else ([], seen)
        if seen and not armed and not gunned:
            # no gun at the wheel and none manned (a lone Warthog or Falcon, a Mongoose): it runs a man on foot down
            # when it can, else goes on with its trip: transports fight nobody (guide 17 §4.2)
            near = min(seen, key=lambda k: dist(me.pos, s.per.beliefs[k].pos))
            if air or not s.ram_ok(me, v, st, snap, snap.player(near), s.per.beliefs[near]):
                seen = []
        ctid = s.crew_target(me, snap, v, seen) if seen and gunned else None   # (the man the gun is on, or should be)
        if seen and gunned and ctid is None:
            seen = []                            # nobody worth its gun's shot: on with the trip
        f = snap.player(ctid) if ctid is not None else None
        if f is not None and f.vehicle not in (None, -1) and s.ram_ok(me, v, st, snap, f, s.per.beliefs[ctid]):
            # a light vehicle head on: a gunned Warthog runs it down too, its gunner shooting on (guide 17 H2-PUR-02 /
            # H4-PUR-01: the circle came first and it never rammed)
            gunned, seen = False, [ctid]
        if seen and not gunned:                  # its own gun (or none): by the vehicle's role (vehicles.STRATEGY)
            tid = (s.vehicle_pick(me, v, "driver", seen, snap) if armed else None) or \
                min(seen, key=lambda k: dist(me.pos, s.per.beliefs[k].pos))
            b = s.per.beliefs[tid]
            # (an explosive shot on a tank goes to its back hatch when we are behind it: weak_point)
            aim_at = s.weak_point(me, tid, b, snap, vehicles.seat_gun(vehicles.kind_of(v["kind"]), "driver")) \
                if v is not None else b.pos
            # a tank's view from its camera over the hull, a mortar lobbed (vehicles.DRIVER_VIEW: a skilled player's
            # shots)
            kind = vehicles.kind_of(v["kind"]) if v is not None else None
            cam, arc = vehicles.driver_view(kind, (b.pos - me.pos).flat())
            aim_at = aim_at + Vec(0.0, 0.0, (b.pos - me.pos).flat() * math.tan(arc))
            act.yaw, act.pitch = s.aim.step(now, dt, me, tid, aim_at, b.vel, False, gun=st.get("gun"),
                                            origin=me.pos + Vec(0.0, 0.0, cam) if cam else None,
                                            crouched=crouched(snap.player(tid)))
            s.att = ["aim", tid, round(b.pos.x, 1), round(b.pos.y, 1)]
            d = dist(me.pos, b.pos)
            lo, hi = st["rng"]
            foe = snap.player(tid)
            passes = air and st["role"] == "air"
            if st["role"] == "charge" and not air:
                # a Chopper's guns point where it does: straight at him, the boost into him close in (the player's
                # Chopper Avalanche: heading 9 deg off his travel while firing, 6 of 7 boosts with a man 3-32 u ahead,
                # 4 of them under 6 u, held 0.6-1.2 s)
                act.forward, act.strafe = (1.0 if d > 3.0 else 0.7), 0.0
                s.ram_boost(now, me, v, b, act)
                s.att = ["charge", tid, round(b.pos.x, 1), round(b.pos.y, 1)]
            elif passes:
                s.air_pass(now, me, tid, b, aim_at, act)    # a Banshee's attack runs: a skilled player's (air_pass)
            elif st["role"] == "artillery":
                # holds its range and keeps moving side on between shots; a man close enough to board it is backed
                # away from (research: Wraith and Scorpion are boarded from the flanks; the mortar faces its target)
                act.forward = -1.0 if d < vehicles.BOARD_D else -0.5 if d < lo else 1.0 if d > hi else 0.0
                act.strafe = s.strafe_dir * 0.8
            elif not air and s.ram_ok(me, v, st, snap, foe, b):
                # run him down (a man on foot with nothing that kills vehicles, a light vehicle head on): full throttle
                # at where he will be. One who sidestepped more than a vehicle's width off our nose close in is not
                # swerved into (it rolls a car, or leaves it stopped beside him): on through (guide 17 §4.3 abort)
                if (getattr(s, "ram", None) or (None,))[0] != tid:
                    # its heading error, one roll a run (guide 18 2.1 ram commit precision: Normal 20 deg, Heroic 12;
                    # Legendary as it was, none)
                    s.ram = (tid, now, math.radians((s.p.extra or {}).get("ram_err", RAM_ERR.get(s.p.tier, 0.0))) * s.rng.uniform(-1.0, 1.0))
                lead = b.pos + b.vel * min(1.0, d / 10.0)
                way, head = math.atan2(lead.y - me.pos.y, lead.x - me.pos.x) + s.ram[2], s.heading(me, v)
                if d < 5.0 and abs(wrap(way - head)) < math.pi / 2 and abs(math.sin(wrap(way - head))) * d > 1.0 \
                        and now - s.ram[1] > 0.5:
                    s.through_until, s.through_yaw = now + 1.5, head
                act.yaw = way
                act.forward, act.strafe = 1.0, 0.0
                s.att = ["splatter", tid, round(b.pos.x, 1), round(b.pos.y, 1)]
                s.ram_boost(now, me, v, b, act)
            else:                                # a gun run side on, in its range, never parked
                act.forward = 1.0 if d > hi else 0.5 if d > lo else -0.4
                act.strafe = s.strafe_dir * (0.8 if d <= hi else 0.4) * max(0.5, s.p.strafe)
            Ta = s.air_terrain() if air else None
            low = s.air_heights(Ta)[0] if air else AIR_LOW
            # an aircraft, or a man high over his floor: air to air at his height (straight at him), not a low run
            if (air and not passes and dist(me.pos, b.pos) > 12
                    and Ta.height_over(b.pos.x, b.pos.y, b.pos.z) < low + 1.5):
                w = Ta.fly_way(me.pos, b.pos, "low")   # the attack run: in along the low airspace, at strafing height
                if w is not None:
                    act.yaw = math.atan2(w.y - me.pos.y, w.x - me.pos.x)
                    act.pitch = max(-0.35, min(0.45, (low - Ta.height_over(me.pos.x, me.pos.y, me.pos.z)) * 0.12))
                    act.forward, s.att = 1.0, ["path", None, round(w.x, 1), round(w.y, 1)]
            if now >= s.next_strafe_flip:
                s.strafe_dir, s.next_strafe_flip = -s.strafe_dir, now + s.rng.uniform(0.5, 1.4)
            if not passes:
                act.fire = armed and (s.aim.on_target(me, aim_at) or dist(me.pos, b.pos) < 15)
        elif seen:                               # a gunner's vehicle: circle the fight in its range, side on
            tid = ctid                            # (the man its gun is on: guide 17 §4.5, the arc kept on him)
            b = s.per.beliefs[tid]
            to = math.atan2(b.pos.y - me.pos.y, b.pos.x - me.pos.x)
            d = dist(me.pos, b.pos)
            near_r, far_r = st["rng"]             # (vehicles.STRATEGY: a Warthog's from a skilled player's driving)
            act.yaw = to + (math.pi / 2 if s.strafe_dir > 0 else -math.pi / 2) * (1.0 if d < far_r else 0.4)
            if d < near_r:
                act.yaw = to + math.pi * 0.8 * s.strafe_dir     # too close: open the range
            act.pitch, act.forward = 0.0, 1.0
            if sp.get("vtol"):
                # a Falcon's guns hang on its sides: it hovers side on, the manned side to him, holding its range by
                # sliding toward or away from him and drifting slowly round (guide 17 §3: people hover for their
                # gunners; §4.5). Its heading is turned no more than it must be: with both sides manned, the nearer
                sides = vehicles.gun_sides(v)
                left, right = to - math.pi / 2, to + math.pi / 2       # headings that put him on our left / right
                f = v.get("fwd")
                head = math.atan2(f[1], f[0]) if f and math.hypot(f[0], f[1]) > 0.1 else me.yaw
                act.yaw = left if sides == {"left"} else right if sides == {"right"} else \
                    min((left, right), key=lambda y: abs(wrap(y - head)))
                lo, hi = st["rng"]
                toward = 1.0 if act.yaw == left else -1.0                # (strafe is to the left)
                act.strafe = toward * (0.6 if d > hi else -0.6 if d < lo else 0.0)
                act.forward = 0.35 * s.strafe_dir
            if now >= s.next_strafe_flip:
                s.strafe_dir, s.next_strafe_flip = -s.strafe_dir, now + s.rng.uniform(3.0, 6.0)
            s.att = ["circle", tid, round(b.pos.x, 1), round(b.pos.y, 1)]
        else:
            if air:                              # the painted high airspace when there is one (tools/paint.py)
                w = s.air_terrain().fly_way(me.pos, target, "high") or target
            else:                                # the vehicle's own ground (no doors, stairs, corridors: Terrain.vnav)
                T = getattr(s, "_terrain", None)
                if T is None:
                    from .maps import terrain
                    T = s._terrain = terrain(s.map)
                w = T.vnav(me.pos, target) if hasattr(T, "vnav") else nav(me.pos, target)
                w = w if w is not None else target       # (no route read: straight at it)
                # as near the goal as the vehicle's ground goes: out here. Only on that ground: off it, its edge is 0.25
                # u away, so a Warthog wedged by it "arrived" every tick, was sent on again and never counted as stuck
                # (H2A Bloodline: 0.35 throttle into a rock for 30 s, no reverse; drivers under 0.5 u/s 61% of the time
                # with the throttle down). The end is vnav answering where it is, never a short step: on Spire's
                # people's road by a wall the next point was 0.25 u on and a driver got out 39 u short
                vg = getattr(T, "vroomy", None)
                on_vg = vg is None or bool(vg[T._node(me.pos)])
                s.ground_end = w == me.pos and on_vg
            act.yaw = math.atan2(w.y - me.pos.y, w.x - me.pos.x)
            act.pitch, act.forward = 0.0, 1.0
            f = v.get("fwd") if v else None      # a sharp turn ahead: ease off so it turns instead of sliding wide
            if f and math.hypot(f[0], f[1]) > 0.1:
                off = abs(wrap(act.yaw - math.atan2(f[1], f[0])))
                act.forward = 1.0 if off < 0.6 else 0.6 if off < 1.6 else 0.35
            if v is not None and act.forward >= 1.0 and dist(me.pos, target) > TRAVEL_BOOST_FROM:
                s.travel_boost(now, me, v, act, air)
            s.att = ["path", None, round(w.x, 1), round(w.y, 1)]
            if meet and armed:                   # Easy on its trip: the gun on whoever is in front of its nose
                head = s.heading(me, v)
                act.fire = any(dist(me.pos, q.pos) < 35.0 and abs(wrap(math.atan2(q.pos.y - me.pos.y, q.pos.x - me.pos.x)
                                                                       - head)) < 0.25
                               for q in (s.per.beliefs[k] for k in meet))
        s.fuel_rod(now, act)
        h, s.landing, s.air_dest = None, False, target   # (air_dest: air_guard looks no further than it when landing)
        if air:                                  # hold its height over the floor under it (pitch up when low, down when high)
            T = s.air_terrain()
            h = T.height_over(me.pos.x, me.pos.y, me.pos.z)
            if not seen:
                # travelling: the height people fly here (learned, maps/<map>/rides.json), else over painted high air,
                # else 5 u; within 10 u of where it goes, down to land and get out (it held 5-9 u and circled its
                # destination: a Banshee 244 s within 28 u of where it got in, Spire)
                learned = getattr(T, "air_heights", None)
                # (no flying learned here: a skilled player's median height in this kind, vehicles.CLEARANCE
                # travel)
                cl = vehicles.clearance(vehicles.kind_of(v["kind"])) if v else vehicles.clearance("banshee")
                # (nothing learned: over this map's own buildings and hills, Terrain.air_levels, never under people's
                # travel height in this kind)
                want = learned[1] if learned else max(cl["travel"], T.air_levels()[1])
                want = max(want, cl["over"])                      # (never under his low point at speed)
                # where it goes stands higher than the floor under it (Spire's top beside its 30 u drop): in over it, 4
                # u above its level, before setting down (live match 230700: a Falcon held 4-12 u under the top's level
                # by the core 90 s, its height taken over the ground under it; "flying into spire, not on top of it")
                up = target.z - (me.pos.z - h)
                if s.urgent(me, snap):
                    want = h + (snap.inv_core.z + 6.0 - me.pos.z)   # guarding the core: 6 u over its own height
                elif up > 3.0:
                    want = max(want, up + 4.0)
                elif (dist(me.pos, target) < 10 and s.order.role != "ferry" and getattr(s, "crew_ride", None) != me.vehicle
                      and getattr(s, "vjob", None) not in ("escort", "hunt", "anti_vehicle")):   # (a job: no landing on it)
                    want, s.landing = 0.0, True                    # (setting down: air_guard looks only to it)
                act.pitch = max(-0.35, min(0.45, (want - h) * 0.12))
            if sp.get("vtol"):
                # a Falcon climbs on its vertical input: pitch and throttle left a landed one where it was (live,
                # tools/falcon_probe.py; +1 climbed it, 0 sank it, +0.5 held it). Travelling at the learned height, in a
                # fight at the strafing one, down to land by where it goes
                goal = want if not seen else s.air_heights(T)[0]
                act.vertical = ((-1.0 if h > 0.3 else 0.0) if goal <= 0.0 else 1.0 if goal - h > 1.0 else
                                -1.0 if goal - h < -1.5 else 0.5)
        # get out at the destination or when stuck (rocks, walls the foot path squeezes past); a pilot only low down
        moving = getattr(s, "drive_pos", None)
        if moving is None or dist(me.pos, moving) > 1.5:
            s.drive_pos, s.drive_since = me.pos, now
        spot = getattr(s, "wedge_spot", None)
        if spot is not None and dist(me.pos, spot) > 4.0:
            s.wedge_spot, s.reversed_n = None, 0       # got away from where it was wedged
        # (aircraft too, after 4 s: a Banshee sat 252 s at full throttle and climb under Spire's spire, 3% of it moving,
        # live; since air_guard backs it off: here a pilot stuck low down gets out)
        stuck = now - s.drive_since > (4.0 if air else 2.5)
        if air and s.order.role != "ferry":
            # over its destination and not getting to it: give that place up after 5 s (Spire: under the spire's
            # overhang, the goal on its top 20 u above, it circled 10 s with its heading flipping, never "stuck":
            # it kept moving)
            over = (me.pos - target).flat() < 10.0 and dist(me.pos, target) >= 6.0
            s.circle_since = (getattr(s, "circle_since", None) or now) if over else None
            c = snap.inv_core
            if s.urgent(me, snap) and (me.pos - c).flat() < 18.0 and (me.pos - target).flat() < 10.0:
                # defending the core from the air: no landing; round the core 15 u out, 6 u over its height (above),
                # a sixth of a turn a leg, attack runs on whoever comes (air_pass)
                a = math.atan2(me.pos.y - c.y, me.pos.x - c.x) + math.pi / 3
                s.drive_to, s.circle_since = Vec(c.x + 15.0 * math.cos(a), c.y + 15.0 * math.sin(a), c.z), None
                return
            if over and now - s.circle_since > 5.0:
                s.drive_to, s.circle_since, s.drive_pos = s.patrol_point(me), None, None
                s.note("air: cannot get to it from here")
                return
        # (under a goal 8+ u up that its ground cannot climb to, the core on Spire's top: as near as it goes. A Warthog
        # sat 100 s under the spire, live match 220515, its driver's goal the core)
        under = not air and target.z - me.pos.z > 8.0 and (target - me.pos).flat() < 15.0
        end = (getattr(s, "ground_end", False) or under) and dist(me.pos, target) >= 6
        # (a racer drives through its gate: the next one takes over when the game counts it; out only where its ground
        # ends short of the gate, or under a gate up on a ledge: live Blood Gulch, Ghosts circled 1.8-2 u across and
        # 2.5 u under gate 3 for 30 s and more, never out)
        race_out = snap.mode == "race" and s.game != "reach" and (getattr(s, "ground_end", False) or (
            target.z - me.pos.z > 1.5 and (target - me.pos).flat() < 6.0))
        arrived = ((dist(me.pos, target) < 6 and snap.mode != "race") or end or race_out) and s.order.role != "ferry"
        s.ground_end = False
        # (a runner at the wheel gets out at their flag, whatever it boarded as: live Avalanche 070402, runners took a
        # Mongoose, Warthog or Wraith as the team's crew, then drove 1-3 u under the flag platform, 4 u below it, for
        # 1,100-5,000 rows each, never out: the flag was never carried in 558 s)
        working = ((getattr(s, "crew_ride", None) == me.vehicle and s.order.role != "runner")
                   or getattr(s, "vjob", None) in ("escort", "hunt", "anti_vehicle")
                   or (v is not None and not vehicles.taxi_ok(v["kind"])))
        if arrived and working:
            # a role's vehicle: on to the next place it is needed (a way its ground does not go: anywhere else); waiting
            # where it is needed is not being stuck - UNLESS it's parked right against a piece forge.py already
            # confirmed has no collision: "arrived" (dist < 6) can be true while actually wedged short of the real
            # target, and resetting the stuck clock every tick here is exactly how a taxi's Warthog sat wedged for
            # 224 s in the Avalanche CTF match above - called "arrived" every tick, never once let stuck accumulate
            from . import forge
            near_bad = forge.missing_near(me.pos)
            s.drive_to, arrived = s.patrol_point(me) if not end else s.roam(me), False
            if not near_bad:
                s.drive_pos, s.drive_since, stuck = me.pos, now, False
        if stuck and not arrived and not air and getattr(s, "reversed_n", 0) < 2:
            # wedged: back off 1.2 s and try again (twice at a spot), then get out. (A pilot: air_guard's stall
            # recovery, every time and toward open air; this one gave up after two and pushed on at the wall:
            # Sidewinder)
            s.reversed_n = getattr(s, "reversed_n", 0) + 1
            s.wedge_spot = getattr(s, "wedge_spot", None) or me.pos
            # (on the ground with the view 60 degrees to a side: a reversing Warthog swings its nose round to the view, a
            # three-point turn, and the next try the other way; straight back and on again met the same rock)
            s.reverse_until, s.reverse_yaw = now + 1.2, me.yaw + s.strafe_dir * math.pi / 3
            s.strafe_dir = -s.strafe_dir
            s.drive_pos = None
            return
        if stuck and not arrived and v is not None and vehicles.kind_of(v["kind"]) in vehicles.TANKS and working:
            # a tank is never left while it works: wedged twice here, it takes another way
            s.drive_to, s.drive_pos, s.reversed_n, s.wedge_spot = s.roam(me), None, 0, None
            s.note("vehicle: wedged, another way (the tank is kept)")
            return
        # (a pilot gets out within a safe drop, under MAX_DROP: 1.5 u kept a Banshee bobbing over the core on Spire's
        # top, live match 220515, 200 s, never out)
        if (arrived or stuck) and (not air or (h is not None and h < 2.5)):
            if now < getattr(s, "exit_block_until", 0.0):
                # its exits did not happen here: drive on to the fight instead of sitting holding Use (live: 9 of 12
                # bot seats ended only in death; parked drivers held Use 35-64% of their seat time)
                if stuck and not air:
                    # wedged meanwhile: back off on a three-point turn, every time it sticks (live Avalanche CTF match
                    # 064500: a taxi's Warthog in the red garage 220 s, its trip's end the flag on the floor above, 4.8
                    # u off: "arrived" every tick, drive_pos cleared every tick here, so never stuck and never a
                    # reverse; Use and reverse never pressed in 224 s, two more drivers the same: 248 s of no-note
                    # stalls)
                    s.reverse_until, s.reverse_yaw = now + 1.2, me.yaw + s.strafe_dir * math.pi / 3
                    s.strafe_dir = -s.strafe_dir
                    s.drive_pos = None
                    s.note("vehicle: wedged, backing out")
                    return
                s.drive_to = s.patrol_point(me)  # (drive_pos kept: a wedged vehicle still counts as stuck)
                return
            # a try counts once a second: CE's exit plays 0.9 s in the seat, and its 9 presses meanwhile were 3 "failed"
            # tries, the 30 s block (live Blood Gulch Race: back in a Warthog by the rock 13 s later, wedged, no exit)
            if now - getattr(s, "exit_try_t", -9.0) >= 1.0:
                s.exit_tries, s.exit_try_t = getattr(s, "exit_tries", 0) + 1, now
            elif s.game in ("ce", "h2"):          # (one press a second there: the exit under way, stopped)
                act.action, act.forward, act.fire = False, 0.0, False
                return
            if s.exit_tries >= 3:                # (the third try still in the seat: 30 s of driving before the next)
                s.exit_tries, s.exit_block_until = 0, now + 30.0
            act.action, act.forward, act.fire = True, 0.0, False
            # held in Reach (a one-tick press did not get out) and, untested, the games after Halo 2; CE and Halo 2: a press
            s.exit_until = now + (0.8 if s.map["name"].startswith(("reach_", "h3_", "odst_", "h4_", "h2a_")) else 0.1)
            s.reversed_n = 0
            s.drive_to = None if not stuck else target
            s.drive_pos = None
            if end or race_out:   # as far as vehicles go on this trip: on foot from here, not back to the next vehicle
                # for the same trip (live Boneyard: out 10 u from the Elite spawn twice, each time back to another one;
                # Blood Gulch Race: out under the rock's gate, its walk round to the climb 12 u off, back in a Warthog)
                s.vehicle_retry_at = max(getattr(s, "vehicle_retry_at", 0.0), now + (60.0 if race_out else 30.0))

    def air_pass(s, now, me, tid, b, aim_at, act):
        """A Banshee's attack runs (PASS_* above, the player's flying): "in", the nose on him (it flies where it looks:
        act.yaw / act.pitch come aimed from drive), the cannons on target inside CANNON_R, the second weapon held
        FIRE2_HOLD on target inside FIRE2_R (a bomb, outside Reach, only from 2 u or more over him); within PASS_OVER
        flat of him, or with him behind, "out": on along the run's heading, climbing to the travel height, until
        PASS_EXTEND flat off or PASS_OUT_S, then the turn back (half throttle its first second) into the next run.
        Never hovering in place (the old side-to-side gun run read as glitching)."""
        Ta = s.air_terrain()
        h = Ta.height_over(me.pos.x, me.pos.y, me.pos.z)
        travel = s.air_heights(Ta)[1]
        flat = (b.pos - me.pos).flat()
        run = getattr(s, "air_run", None)
        if run is None or run[1] != tid:
            run = s.air_run = ["in" if flat >= PASS_START else "out", tid, now, me.yaw]
        behind = abs(wrap(math.atan2(b.pos.y - me.pos.y, b.pos.x - me.pos.x) - me.yaw)) > math.pi / 2
        if run[0] == "in" and (flat < PASS_OVER or (behind and flat < PASS_START) or (h < 5.0 and flat < 12.0)):
            run[:] = ["out", tid, now, me.yaw]                 # past him: on, straight, climbing
        # (the next run starts from the player's run height: 10-35 u up; flown in a harness the old 0.3 climb to 22 u
        # off left it 3-8 u over the floor, his median was 26)
        start_h = max(PASS_START_MIN, min(travel, PASS_START_H))   # (a map with no flying learned: travel 9 u)
        far = flat >= PASS_EXTEND and h >= start_h - 1.0
        if run[0] == "out" and (far or now - run[2] > PASS_OUT_S):
            run[:] = ["in", tid, now, me.yaw]                  # far and high enough: the turn back into the next run
        act.strafe = 0.0
        # shot at (its shield or body down since the last tick): a jink, a side swerve held AIR_JINK_S, outside its own
        # firing (and Reach's boost with it, its decoded right button); a straight line is what gets a Banshee killed
        vit = me.shield + me.health
        if vit < getattr(s, "air_vit", vit) - 0.02 and now >= getattr(s, "air_jink", (0.0,))[0]:
            s.air_jink = (now + AIR_JINK_S, s.rng.choice((-1.0, 1.0)))
        s.air_vit = vit
        jink = getattr(s, "air_jink", (0.0, 0.0))
        jinking = now < jink[0] and (run[0] == "out" or dist(me.pos, b.pos) > CANNON_R)
        if run[0] == "out":
            act.yaw, act.pitch, act.forward = run[3], (0.6 if h < start_h else 0.0), 1.0
            act.fire = False
            act.fire2 = now < getattr(s, "fire2_until", 0.0)   # (a press already going is held through)
            if jinking:
                act.yaw += AIR_JINK * jink[1]
                act.fire2 = act.fire2 or s.game == "reach"
                s.note("air: shot at, jinking")
            s.boost(act)
            s.att = ["pass", tid, round(b.pos.x, 1), round(b.pos.y, 1)]
            return
        act.forward = 0.5 if now - run[2] < 1.0 else 1.0
        # (the ground on the way in: air_guard's look along the dive pulls out of it and ends the run; the flat 4 u floor
        # that held the nose up here kept every dive off him). Out of gun range it holds the run's height and dives
        # once in range: nosed straight at him from 60 u it glided down into the rising ground between and the pull-out
        # ended the run before a shot (tools/air_replay.py, Shrine)
        if dist(me.pos, b.pos) > CANNON_R and h < start_h:
            act.pitch = max(act.pitch, min(0.4, (start_h - h) * 0.08))
        if jinking:
            act.yaw += AIR_JINK * jink[1]
            act.fire2 = act.fire2 or s.game == "reach"
            s.note("air: shot at, jinking")
        # the nose on him within AIR_GUN_R (the cannons' bolts and their aim assist), not only the hand gun's man-sized
        # window: live (Shrine 20261104-154714, bot 9's Banshee) 428 run ticks under 28 u fired 43
        ya, pa = angles_to(eye(me), aim_at)
        d = dist(me.pos, b.pos)
        r = math.atan2(AIR_GUN_R, max(d, 1.0))
        on = s.aim.on_target(me, aim_at) or (abs(wrap(ya - act.yaw)) < r and abs(pa - act.pitch) < r)
        act.fire = on and d < CANNON_R
        lo, hi = FIRE2_R
        if now < getattr(s, "fire2_until", 0.0):
            act.fire2 = True
        elif (on and lo <= d <= hi and now >= getattr(s, "fire2_next", 0.0)
              and (s.game == "reach" or me.pos.z - b.pos.z >= 2.0)):
            s.fire2_until, s.fire2_next = now + FIRE2_HOLD, now + FIRE2_EVERY
            act.fire2 = True
        if (s.game == "reach" and on and FUELROD_R[0] <= d <= FUELROD_R[1] and getattr(s, "rod", None) is None
                and now >= getattr(s, "rod_next", 0.0)):
            s.rod, s.rod_next = ["to", now], now + FUELROD_EVERY    # Reach's fuel rod: fuel_rod() from drive
        s.boost(act)
        s.att = ["run", tid, round(b.pos.x, 1), round(b.pos.y, 1)]

    def boost(s, act):
        """Reach: the player's right button in a Banshee is its boost (his speed held 8.8 u/s, not 4.3), and that button
        sets the action's +0x3C float to 1 (decoded live, tools/decode_buttons.py fire2=rmb: also bits at +0x08, +0x48,
        +0xA0, not sent: a table entry clears its bits every tick, and +0x3C is the Falcon's climb). botcore writes
        +0x3C from the vertical input (shared.h hb_vertical), so the boost hold goes out as vertical 1."""
        if s.game == "reach" and act.fire2:
            act.vertical = 1.0

    def chaingun_heat(s, dt, fire):
        """Reach's Warthog chaingun overheats: heat climbs over CHAINGUN_HEAT[0] s of fire and falls over CHAINGUN_HEAT[1]
        s off; at full the gunner lets go until it is all gone (an overheated gun cools right down)."""
        heat = getattr(s, "heat", 0.0)
        if getattr(s, "overheated", False):
            fire = False
            s.overheated = heat > 0.0
        elif fire and heat >= 1.0:
            fire, s.overheated = False, True
        s.heat = max(0.0, min(1.0, heat + (dt / CHAINGUN_HEAT[0] if fire else -dt / CHAINGUN_HEAT[1])))
        return fire

    def travel_boost(s, now, me, v, act, air):
        """The boost on the way somewhere, as the player held it (vehicles.TRAVEL_BOOST: his median hold, then his median
        gap): only along a straight leg that is open for it (the nose within TRAVEL_BOOST_CONE of where it goes, and no
        solid thing inside TRAVEL_BOOST_S s at twice its speed: the probe tools/vehicle_clearance.py measured him with),
        never at Easy (ram_boost's rule). Dropped the moment the leg closes."""
        k = vehicles.kind_of(v["kind"])
        hg = vehicles.TRAVEL_BOOST.get(k)
        # where the boost reaches the game: Reach (Bot.boost), Halo 3's ground boosters (its decoded fire2, ram_boost);
        # elsewhere the second input is a Banshee's bomb, or nothing where it is not decoded
        if hg is None or s.p.tier == "novice" or not (s.game == "reach" or (s.game == "h3" and k in vehicles.BOOSTS)):
            return
        holding = now < getattr(s, "tboost_until", 0.0)
        if not holding and now < getattr(s, "tboost_next", 0.0):
            return
        T = s.air_terrain() if air else s.T()
        head = s.heading(me, v)
        if abs(wrap(act.yaw - head)) > TRAVEL_BOOST_CONE:
            s.tboost_until = 0.0
            return
        need = max(4.0, 2.0 * max(me.vel.flat(), 0.5 * vehicles.spec(k)["speed"]) * TRAVEL_BOOST_S)
        floor = me.pos.z - T.height_over(me.pos.x, me.pos.y, me.pos.z)
        clear = T.fly_clear(me.pos, head, need, floor=floor) if air else \
            T.fly_clear(Vec(me.pos.x, me.pos.y, floor), head, need, dzs=(0.8, 1.4), floor=floor)
        if clear < need:
            s.tboost_until = 0.0
            return
        if not holding:
            s.tboost_until, s.tboost_next = now + hg[0], now + hg[0] + hg[1]
            s.note("vehicle: boost on the way")
        act.fire2 = True
        s.boost(act)

    def open_way(s, me, away):
        """The heading to flee a boarder by on open ground: the vehicle's nav (Terrain.vnav) toward a point FLEE_D u
        away from him, else, of the turns off that line, the first whose ground is drivable FLEE_D u on (vroomy); else
        the line itself."""
        T = s.T()
        far = Vec(me.pos.x + FLEE_D * math.cos(away), me.pos.y + FLEE_D * math.sin(away), me.pos.z)
        w = T.vnav(me.pos, far) if hasattr(T, "vnav") else None
        if w is not None and (w - me.pos).flat() > 0.3:
            return math.atan2(w.y - me.pos.y, w.x - me.pos.x)
        vg = getattr(T, "vroomy", None)
        if vg is not None:
            for off in (0.6, -0.6, 1.2, -1.2, 1.9, -1.9):
                q = Vec(me.pos.x + FLEE_D * math.cos(away + off), me.pos.y + FLEE_D * math.sin(away + off), me.pos.z)
                try:
                    if vg[T._node(q)]:
                        return away + off
                except (IndexError, ValueError):
                    continue
        return away

    def ram_boost(s, now, me, v, b, act):
        """The boost into a man close ahead (a Ghost or Chopper, vehicles.BOOSTS; the player's Chopper and Ghost boosts
        Avalanche: 6 of 7 Chopper boosts with a man ahead, 4 under 6 u, held 0.6-1.2 s, median 0.83; his one Ghost boost
        from 9 u, a kill): inside BOOST_R and BOOST_CONE of the nose, held BOOST_HOLD, then not for BOOST_EVERY. Sent as
        the vehicle's second input (fire2): in Reach Bot.boost puts it out as +0x3C; Halo 3 through its decoded "fire2"
        (maps/_inputs/h3.json, master 86f0e19); Halo 2 and the rest: noted, not sent where it is not decoded."""
        k = vehicles.kind_of(v["kind"]) if v else None
        if k not in vehicles.BOOSTS or s.p.tier == "novice":
            return
        if now < getattr(s, "boost_until", 0.0):
            act.fire2 = True
        else:
            d = (b.pos - me.pos).flat()
            off = abs(wrap(math.atan2(b.pos.y - me.pos.y, b.pos.x - me.pos.x) - s.heading(me, v)))
            if d < BOOST_R and off < BOOST_CONE and now >= getattr(s, "boost_next", 0.0):
                s.boost_until, s.boost_next = now + BOOST_HOLD, now + BOOST_EVERY
                act.fire2 = True
                s.note("vehicle: boost into him")
        s.boost(act)

    def fuel_rod(s, now, act):
        """Reach's Banshee fuel rod, cycled to like a secondary: the cycle press, then one shot, then the cycle back to the
        cannons, timed as his; no cannon fire while it is selected. Run every tick from drive until it is back on the
        cannons, whatever the run is doing (a fight lost mid-way still cycles back)."""
        r = getattr(s, "rod", None)
        if r is None:
            return
        k = now - r[1]
        act.fire = False
        if r[0] == "to":
            act.cycle = k < 1e-6                     # (the press, on the tick it started)
            if k >= FUELROD_FIRE:
                r[:] = ["shot", now]
                act.fire = True
        elif r[0] == "shot":
            act.fire = k < FUELROD_SHOT
            if k >= FUELROD_SHOT:
                r[:] = ["back", now]
        elif k >= FUELROD_BACK:
            act.cycle, s.rod = True, None            # back on the cannons

    def patrol_point(s, me):
        """Where a vehicle goes when its trip is over and it cannot get out: the latest enemy it knows of, else the
        objective (when far), else a roam point."""
        known = [b for k, b in s.per.beliefs.items() if isinstance(k, int)]
        if known:
            return max(known, key=lambda b: b.t).pos
        p = s.objective_point(me)
        return p if p is not None and dist(me.pos, p) > 10.0 else s.roam(me)

    @staticmethod
    def dry(me):
        """Every gun this player carries is known to be empty."""
        return bool(me.ammo) and all(a == 0 for a in me.ammo)

    def rearm_point(s, me, snap):
        """Where to get a loaded gun: the nearest loose weapon (dropped by the dead or on its
        spawn) when all guns are dry, or a better one close by when the gun in hand is low."""
        if not snap.ground_weapons or not me.ammo:
            return None
        # low: under 6 rounds and under half the most this bot has held of that gun. Not under 6 alone: the rockets
        # carry 2 + 4, so a bot that just took them was "low", went back for its own dropped magnum and swapped them
        # away, then took the rockets again, four swaps a second
        i = me.weapons.index(me.weapon) if me.weapon in me.weapons else -1
        held = me.ammo[i] if 0 <= i < len(me.ammo) else -1
        top = s.__dict__.setdefault("ammo_max", {})
        if held >= 0:
            top[me.weapon] = max(top.get(me.weapon, 0), held)
        low = s.dry(me) or (0 <= held < 6 and held < 0.5 * top.get(me.weapon, 0))
        reach = 60.0 if s.dry(me) else 12.0
        if snap.mode == "firefight":
            # Firefight has no ammo spawns: a bot short of ammo takes a gun off the ground (an Elite's drop, a dead
            # Spartan's) from far off. Low: the fullest gun it carries is under a third of the most it has held of it, or
            # under 6 rounds. Only with no enemy in sight, a fight first.
            for w, a in zip(me.weapons, me.ammo):
                if a >= 0:
                    top[w] = max(top.get(w, 0), a)
            left = [a / top[w] if top.get(w) else 1.0 for w, a in zip(me.weapons, me.ammo) if a >= 0 and w != "none"]
            if left and (max(left) < 0.34 or all(0 <= a < 6 for a in me.ammo if a >= 0)):
                low = True
            if low and not s.dry(me):
                if s.per.visible_enemies():
                    return None
                reach = 45.0
        if not low:
            return None
        # a spot tried without getting anything (someone took it, or it gives nothing) waits
        skip = getattr(s, "rearm_skip", {})
        now = snap.time
        tried = getattr(s, "rearm_at", None)
        total = sum(a for a in me.ammo if a > 0)
        if tried and dist(me.pos, tried[0]) < 1.5:
            if total > tried[2]:
                s.rearm_at = None                      # it worked
            elif now - tried[1] > 3.0:
                skip[(round(tried[0].x), round(tried[0].y))] = now + 30.0
                s.rearm_skip, s.rearm_at = skip, None
        near = [(dist(me.pos, q), q) for n, q in snap.ground_weapons
                if n not in ("flag", "ball", "none") and skip.get((round(q.x), round(q.y)), 0.0) <= now]
        near = [x for x in near if x[0] < reach]
        if not near:
            return None
        q = min(near, key=lambda x: x[0])[1]
        if dist(me.pos, q) < 1.5 and (not getattr(s, "rearm_at", None) or dist(s.rearm_at[0], q) > 0.5):
            s.rearm_at = (q, now, total)               # standing on it now: see if ammo comes
        return q

    def upgrade_point(s, me, snap, reach=10.0):
        """A loose gun within reach that makes what it carries worth more (range classes: loadout_value), into a free
        slot or in place of the gun gun_to_drop would throw away (late: "to walk past a battle rifle without batting
        an eye is kind of crazy"; this only looked with both slots full, and 93% of the time a bot carried just its
        AR). Where it lies, or None. Spots tried without a change wait (rearm_skip)."""
        if not snap.ground_weapons:
            return None
        if s.p.map_iq < 0.15:
            reach = ON_THE_WAY        # Easy: only a gun it all but walks over
        inv = [w for w in me.weapons if w not in ("none",)]
        ammo = [me.ammo[i] if i < len(me.ammo) else -1 for i in range(len(inv))]
        have = loadout_value(inv, s.rank, s.game)
        skip = getattr(s, "rearm_skip", {})
        rest = s.swap_rested(me, snap)
        best = None
        for n, q in snap.ground_weapons:
            if (n in ("flag", "ball", "none", "unknown") or n in inv or abs(q.z - me.pos.z) >= 1.0
                    or skip.get((round(q.x), round(q.y)), 0.0) > snap.time or any(dist(q, p) < 2.5 for p in rest)):
                continue
            d = dist(me.pos, q)
            if d >= reach:
                continue
            keep = inv if len(inv) < 2 else [w for k, w in enumerate(inv) if k != s.drop_slot(inv, ammo, n)]
            gain = loadout_value(keep + [n], s.rank, s.game) - have
            if gain > UPGRADE_GAIN and (best is None or gain - 0.01 * d > best[0]):
                best = (gain - 0.01 * d, q, n)
        if best is None:
            return None
        s.pickup_gun = (best[1], best[2])     # what lies there (gun_to_drop)
        if dist(me.pos, best[1]) < 1.5:
            s.upgrade_at = (best[1], tuple(me.weapons))     # standing on it: a swap here rests the spot (swap_rested)
        return best[1]

    def swap_rested(s, me, snap):
        """Spots where this bot swapped guns in the last 20 s (the gun it dropped lies there now): no upgrade from them.
        Live Spire match 230700: a bot swapped its pistol for the guns at its feet and back ten times in a second,
        leaving a pile ("theres just floating knives"); the upgrade test saw its own dropped gun as the next upgrade."""
        rest = [r for r in getattr(s, "swap_rest", []) if r[1] > snap.time]
        last = getattr(s, "upgrade_at", None)
        if last is not None and tuple(me.weapons) != last[1]:
            if dist(me.pos, last[0]) < 2.0:
                rest.append((last[0], snap.time + 20.0))
            s.upgrade_at = None
        s.swap_rest = rest
        return [p for p, _ in rest]

    def drop_slot(s, inv, ammo, new=None):
        """The slot to give up for a new gun: an empty gun first, else the one whose loss leaves the loadout worth the
        most (with the new gun, when known)."""
        def kept(k):
            return loadout_value([w for i, w in enumerate(inv) if i != k] + ([new] if new else []), s.rank, s.game)
        return min(range(len(inv)), key=lambda k: (ammo[k] != 0, -kept(k)))

    def best_item(s, now, me):
        """Power weapons and powerups by map knowledge. A bot with no map knowledge only grabs
        what it walks past; a top bot knows every spawn and times the respawns (its estimate is
        off by a few seconds, more for weaker bots)."""
        from .equipment import PICKUP_RANK
        best = None
        for i, it in enumerate(s.map.get("items", [])):
            # (a powerup it has no use for, overshielded already, is still taken for denial when the team plays safe:
            # EQP-03, "an unused overshield in our hands is better than a used one in theirs")
            deny = it["kind"] == "powerup" and getattr(s, "posture", 0.5) <= 0.3 and dist(me.pos, it["pos"]) < 15.0
            if it["name"] == me.weapon or (it["kind"] == "powerup" and me.shield > 1.0 and not deny):
                continue
            if it["kind"] == "equipment" and PICKUP_RANK.get(me.equipment, 0) >= PICKUP_RANK.get(it["name"], 0):
                continue      # one slot: a second item drops the first; never swap down (guide 08)
            if not it.get("reachable", True) or now < s.item_skip.get(i, 0.0):
                continue      # on a rock nobody can climb, or we stood on its spot and got nothing
            d = dist(me.pos, it["pos"])
            back = s.item_timers.get(i, 0.0)
            if back not in s.item_err.setdefault(i, {}):
                s.item_err[i] = {back: s.rng.gauss(0, 8.0 * (1.0 - s.p.map_iq) + 1.0)}
            eta = d / 2.3
            up_by_arrival = back + s.item_err[i][back] <= now + eta
            if s.p.map_iq < 0.15:
                # Easy knows no weapon spawns: only a gun it can see lying right in front of it, on its way: it never
                # goes out of its way for one ("easy bots do not plan, they just move mindlessly towards the shiny
                # light"; it went up to 8 u for one)
                if d < ON_THE_WAY and back <= now and s.T().los(eye(me), it["pos"] + Vec(0, 0, 0.2)):
                    score = 0.9
                else:
                    continue
            elif up_by_arrival:
                score = 0.3 + 0.9 * s.p.map_iq * it["value"] - d / 120.0
                if me.weapon == "pistol" and it["kind"] == "weapon":
                    score += 0.15
                # guide 08 powerup priority: camo for flankers and snipers, the overshield before a
                # push (posture high), speed for a runner
                if it["name"] == "camo" and s.p.personality in ("flanker", "sniper"):
                    score += 0.25
                if it["name"] == "overshield":
                    score += 0.3 * (getattr(s, "posture", 0.5) - 0.5)
                # who it goes to (PU-01 / PU-02, H3-ASS-07, REACH-CTF-03): the overshield to the bot who contests the
                # objective next (the runner, the hunter), camo to the runner (flag sneak, bomb planter; snipers above)
                if it["name"] == "overshield" and s.order.role in ("runner", "hunter"):
                    score += 0.25
                if it["name"] == "camo" and s.order.role in ("runner", "sneak"):
                    score += 0.25
                if deny:
                    score += 0.3
                if it["name"] == "speed_boost" and s.order.role == "runner":
                    score += 0.3
            else:
                continue
            if best is None or score > best[1]:
                best = (it["pos"], score)
        return best

    def fight(s, now, dt, me, snap, tid, act, nav):
        b = s.per.beliefs.get(tid)
        if not b:
            return
        if s.engaged_since is None:
            s.engaged_since = now
            # weak players often stand and shoot; Normal and up never
            s.strafing = s.p.tier != "novice" or s.rng.random() < s.p.strafe
            s.plant = s.rng.random() < PLANT           # this fight, standing for the long shots (fight)
            # open this fight with a grenade? Decided once per fight by style and tactics (Legendary: Grenadier about
            # 86%, Rusher 45%, Camper 27%, Sniper 18%). Rolled every tick of the opening 0.6 s it came out for nearly
            # everyone (99 / 91 / 75%), so the Grenadier did not stand out (tools/style_report.py)
            s.nade_open = s.rng.random() < min(1.0, s.p.grenades * 1.6 * (0.5 + 0.5 * s.p.tactics))   # (1.3 to)
            # this fight, walking in on him in the gun's band (press_on; profiles.PRESS: Legendary every fight, Heroic
            # half, no draw for the others)
            press = profiles.PRESS.get(s.p.tier, 0.0)
            s.press_fight = press >= 1.0 or (press > 0.0 and s.rng.random() < press)
        d = dist(me.pos, b.pos)
        target = snap.player(tid)
        s.dry_fire_check(me, target, tid)
        head = s.aim_head(now, me, target, tid)
        aim_at = (s.weak_point(me, tid, b, snap, me.weapon)
                  if target is not None and target.vehicle != -1 and me.weapon in VEHICLE_KILLERS else b.pos)
        act.yaw, act.pitch = s.aim.step(now, dt, me, tid, aim_at, b.vel, head, crouched=crouched(target))
        s.att = ["aim", tid, round(b.pos.x, 1), round(b.pos.y, 1)]
        lo, hi = WEAPON_RANGE.get(me.weapon, (5.0, 25.0))
        to = (b.pos - me.pos).norm()
        fwd = 1.0 if d > hi else (-0.7 if d < lo else 0.0)
        unseen = now - s.per.last_seen.get(tid, -9.0)
        # the sword or hammer runs into him, in reach too: at 1.5 u a sword bot stopped in its band and strafed full
        # stick while the AR man it charged killed it (The Pit 199.95-200.19 s: forward 0, strafe 1.0, one press)
        rush = me.weapon in LUNGE and d > 0.5
        # a CTF defender fights from its post: it does not close on a man outside the area round it, nor chase one who
        # broke sight out there. One carrying our flag is chased
        # (Infection too: survivors hold their room; not the infected: an ambush ends when the prey shows. Held, Flood at
        # their corners stood 8 u from a survivor they fought, never closing: the owner's Ragnarok game 590.6 s, "this
        # guy should have been dead long ago")
        hold = (s.order.role == "defender" and s.order.point is not None
                and (snap.mode == "ctf" or (snap.mode == "infection" and me.weapon not in LUNGE))
                and dist(b.pos, s.order.point) > DEFEND_HOLD and not (target is not None and target.has_flag))
        s.pushing = s.push_in(me, b, target, snap, d, lo) if not hold else False
        if s.pushing:
            fwd = 1.0
        s.pressed_in = not s.pushing and not hold and fwd == 0.0 and s.press_on(me, b, target, snap, d, unseen)
        if s.pressed_in:
            fwd = 1.0              # intent to kill: in band, walk in on him while strafing (not stand and trade)
        if s.keep_off(me, target, d):
            fwd = -1.0             # out of his sword's lunge or hammer's swing, shooting (guide 03 counter-sword: keep
            s.pushing = False      # away and shoot the head on his way in; audit JUG-02: hunters out of hammer reach)
        if hold:
            fwd = min(fwd, 0.0)
        # seen through a window that stops rounds (Terrain.shot_los): no shot from here, so go round as for a man out
        # of sight (standing to trade aim through bullet-proof glass is no fight)
        glassed = now - s.glassed[tid] if tid in getattr(s, "glassed", {}) else 0.0
        away = max(unseen, glassed)
        if hold and unseen > 0.5:
            s.move_to(me, s.order.point, act, nav)      # back to the post, not after him
        elif away > 0.5 and s.p.tactics > 0.15 and not (me.weapon in SNIPERS and d < 25.0):
            # (a sniper does not chase round the corner: it holds the lane he comes back into, gun sense) he broke line
            # of sight: chase by the nav to where he went (a little along his last velocity); the straight run at the
            # wall he went behind lost him in 54% of live Lockdown fights (tools/fight_ends.py --map). A novice still
            # runs straight at where he was
            s.move_to(me, b.pos + b.vel * min(1.0, unseen), act, nav)
            if glassed > 0.5:
                s.note("seen through glass: go round")
        elif rush and not hold:
            # the sword or hammer in hand: run him down by the nav (a straight line stopped at Construct's ledges, 144
            # "edge stop"s in one fight), aiming a little ahead of where he goes. Not across the open at a far man who
            # has us in his sights (sword_cover)
            s.move_to(me, s.sword_cover(now, me, b, target, d) or b.pos + b.vel * 0.4, act, nav)
        else:
            s.steer(me, me.pos + to * (10 * fwd) if fwd else me.pos, act, fwd != 0)
        nav_strafe = act.strafe               # (the nav's own side steering: a rush keeps it, below)
        # being shot right now? (shield or body dropped since last tick: with no shields, SWAT, only the body does)
        vitals = me.shield + me.health
        hit = vitals < getattr(s, "last_vitals", vitals) - 1e-3
        s.last_vitals = vitals
        if hit:
            s.hit_at = now
        under_fire = now - getattr(s, "hit_at", -9) < 0.8
        # Heroic and Legendary juke as soon as a man has them in his sights, before his shots land
        dodging = under_fire or (s.p.juke >= 0.5 and target is not None and aims_at(target, me, 0.15))
        if now >= s.next_strafe_flip:
            if dodging and s.rng.random() < s.p.juke:
                # juke: irregular side steps to throw the shooter's aim off, mostly short, now and then a longer run
                # (0.15-0.35 s steps before: the stop-start read as speed varying 0.31-0.41 to the player's 0.23)
                s.strafe_dir = -s.strafe_dir if s.rng.random() < 0.85 else s.strafe_dir
                # (0.22-0.45 / 0.45-0.8 s steps to: turned before they were up to speed, strafe 0.55 u/s)
                s.next_strafe_flip = now + (s.rng.uniform(0.3, 0.55) if s.rng.random() < 0.75 else s.rng.uniform(0.55, 0.9))
                if s.rng.random() < 0.15 * s.p.juke:
                    # crouch-juke: drops the head out of the crosshair (held: a one-tick crouch never showed)
                    s.crouch_until = now + s.rng.uniform(0.2, 0.45)
                if s.rng.random() < STRAFE_PAUSE:
                    s.strafe_pause_until = now + s.rng.uniform(0.2, 0.4)    # a person's side to side has short stops
            else:
                s.strafe_dir = -s.strafe_dir if s.rng.random() < 0.8 else s.strafe_dir
                s.next_strafe_flip = now + s.rng.uniform(0.45, 1.0)
            # how far the stick goes this step: Reach's bots strafed full stick on 74% of their aim-on fight ticks,
            # 1.12 u/s across the line to the man, strafe 0.87-0.92 to the player's 0.70 (live Spire; 0.75 of the stick
            # models 0.77, logs/tmp/strafe_model.py). Other games as they were (Halo 2 SWAT's Legendary is right)
            s.strafe_mag = s.rng.uniform(0.5, 0.95) if s.game == "reach" else 1.0
            if s.strafing and s.rng.random() < STRAFE_JUMP * s.p.jump * (0.5 + s.p.jumpy):
                act.jump = True    # a strafe jump: a good player's fights are not all on the ground (the player jumps
                                   # 3.8 times a minute, Legendary bots 0.2-0.9: tools/fluidity.py)
        act.strafe = s.strafe_dir * getattr(s, "strafe_mag", 1.0) if (s.strafing or (dodging and s.p.juke > 0)) else 0.0
        if now < getattr(s, "strafe_pause_until", 0.0):
            act.strafe = 0.0
        if getattr(s, "plant", False) and (s.p.tier == "novice" or me.weapon in SNIPERS) and (
                not dodging or (d > 16.0 and not under_fire)) and d > (14.0 if me.weapon in PRECISION else 10.0):
            act.strafe = 0.0      # a person plants his feet for a shot at range nobody is answering (bots stood still
                                  # 2-10% of the time to the player's 13%, strafed 0.9 to his 0.7, live Spire; any
                                  # gun past 8 u since game 11, precision ones past 12)
        if rush:
            # running a man down: no side-stepping away from him (strafing ate the closing speed: sword users spent
            # 49-81% of their sword fights strafing), only a half-step juke while his shots land
            juke = 0.5 * s.strafe_dir if under_fire and s.p.juke > 0 else 0.0
            act.strafe = max(-1.0, min(1.0, nav_strafe + juke))
        if now < getattr(s, "crouch_until", 0.0):
            act.crouch = True
        if target is not None:
            moved = s.kit.fight_move(s, now, me, target, snap, act)   # armor lock: back off; dome: walk in
            if moved:
                s.notes.append(moved)
        # shoot only at a man we see (or saw a moment ago): the 2 s chase after losing him is for
        # closing in and re-acquiring, not for emptying the gun into the wall he went behind
        seen_now = now - s.per.last_seen.get(tid, -9.0) < 0.25
        # a looser trigger below Legendary (profiles.LOOSE): on where he went a moment longer, firing as the swing ends
        loose = profiles.LOOSE.get(s.p.tier) if profiles.LOOSE_AIM else None
        if s.p.extra and profiles.LOOSE_AIM:     # (Scaling difficulty's own, brain/scaling.py)
            loose = dict(follow=s.p.extra["loose_follow"], early=s.p.extra["loose_early"])
        seen_fire = now - s.per.last_seen.get(tid, -9.0) < 0.25 + (loose["follow"] if loose else 0.0)
        # ... and a clear line to him now, or no shot: the grace above, the looser trigger's follow-through and a hit
        # through the level (Perception: the man shooting us counts as seen 1.5 s) emptied guns into the walls he went
        # behind. Grenades go on (the flush below)
        clear = s.line_clear(now, me, tid, snap)
        seen_fire = seen_fire and clear
        w = me.weapon
        # how close to the man the crosshair must be: his width at range; up close a spray or a spam lands with
        # the crosshair anywhere on him and a little off, so more shots beat careful ones. A Recruit's trigger
        # finger does not know that
        slack = (2.5 if w in AUTO else 1.6) if d < SHORT_BAND and s.p.tactics > 0.15 else 1.0
        on = (s.aim.on_target(me, b.pos, act.yaw, act.pitch, slack)
              or (loose is not None and s.aim.landing(now, loose["early"], tid))) and seen_fire
        if not on and seen_now and clear and d < 1.6:
            # point blank: he fills the view. A man circling us at arm's length is hit by shooting while
            # turning, not by waiting for the aim to settle, but on his body: it fired anywhere within
            # 0.6 rad of him (34 degrees) and missed the player standing still
            on = abs(wrap(math.atan2(b.pos.y - me.pos.y, b.pos.x - me.pos.x) - act.yaw)) < math.atan2(0.2, max(d, 0.3))
        if on and target is not None and not s.kit.can_shoot(now, me, target, snap):
            on = False                 # into an armor lock, through a dome, at a decoy shot once
        if s.dry(me):                  # nothing to shoot with: punch if close, else go rearm
            on = False
            if d < MELEE_R:
                act.melee = True
        blast = BLAST.get(w)
        mates_near = any(p.team == me.team and p.alive and p.id != me.id and dist(p.pos, b.pos) < (blast or 1.5)
                         for p in snap.players)
        facing = on
        if blast and (d < blast or mates_near):
            on = False                 # never fire an explosive inside its own blast or onto a teammate
            if d < MELEE_R:            # (guide 01: rockets never within 15 ft of self; melee instead)
                act.melee = True
        # inside the blast with nothing else loaded (choose_weapon switches when it can) and no swing landing:
        # being shot while holding fire is dying for nothing. A trade beats it
        hurt = now - getattr(s, "hit_at", -9.0) < 0.8
        cornered = bool(blast) and d < blast and not mates_near and (hurt or not getattr(s, "can_melee", True))
        s.cornered_since = (getattr(s, "cornered_since", None) or now) if cornered else None
        trade = cornered and facing and now - s.cornered_since > (0.4 if hurt else 1.5)
        if trade:
            on = clear
            s.notes.append("inside the blast, nothing else: trade")
        blocked = bool(blast) and (d < blast or mates_near) and not trade
        if w in LUNGE:
            # the sword lunges, the hammer swings: the moment the reticle is red (red_reticle: in the weapon's autoaim
            # range and cone), then the game's lunge locks on. Not before: a press off the red cone (0.6 rad of the view
            # before, 34 deg) swung at the air and the next swing came too late (owner, issue #3)
            on = seen_now and clear and red_reticle(me, b.pos, w, s.game) and (
                target is None or s.kit.can_shoot(now, me, target, snap))
        if (w in ("shotgun", "scattershot") and d > reach(w)
                and s.p.tactics > 0.15):
            on = False                 # a shotgun past its reach only gives us away: close in first (a Recruit
                                       # fires it anyway: out of band, guide 01 section 9)
        elif on and not trade and d > reach(w) * OUT_OF_REACH and w not in CHARGE and s.p.tactics > 0.15:
            # far past the gun's reach shots waste ammo and give us away. A short burst back at a man shooting us is
            # the deterrent, every few seconds; otherwise close in (fwd above) or switch (choose_weapon)
            if under_fire and now >= getattr(s, "deter_at", 0.0):
                s.deter_at, s.deter_until = now + s.rng.uniform(2.5, 4.0), now + s.rng.uniform(0.2, 0.35)
            if now >= getattr(s, "deter_until", 0.0):
                on = False
                if not s.notes or s.notes[-1] != "out of reach: hold fire":
                    s.notes.append("out of reach: hold fire")
        locks = (w == "rockets" and s.game in LOCK_GAMES) or w == "missile_pod"   # the pod locks wherever it is (H3,
        if locks and target is not None and target.vehicle != -1 and s.p.tactics > 0.15:   # ODST: review F3)
            s.on_since = (getattr(s, "on_since", None) or now) if on else None
            if on and now - s.on_since < 0.9:
                on = False             # hold the reticle on the vehicle until the lock (guide 03), then fire
        else:
            s.on_since = None          # the next vehicle starts its lock again (review F2: a stale time skipped it)
        hold = False
        if getattr(s, "charge_w", None) != w:
            s.charge_since = None      # a charge belongs to the gun that started it (a switch drops it)
        if w in CHARGE:
            how = ("release", 0.05) if trade else CHARGE[w]      # a trade is a tap: one bomb now
            s.charge_fire(now, me, target, tid, d, on, seen_now and clear and not blocked, act, how)
        elif w in AUTO and (d < SHORT_BAND or s.p.tactics <= 0.15 or w in BEAM):
            # up close an automatic keeps the trigger down: it stays down through a wobble off him, not just while
            # dead on. A beam at any range
            if on:
                s.spray_until = now + s.rng.uniform(0.25, 0.45)
            hold = seen_fire and now < getattr(s, "spray_until", 0.0)
            act.fire = s.shot_now = hold
        elif w in AUTO:
            # the top of Short band and past it: 3-5 round bursts keep the spread tight (guide 03)
            if on and now >= s.next_fire:
                s.burst_until = now + s.rng.uniform(0.22, 0.4)
                s.next_fire = s.burst_until + s.rng.uniform(0.15, 0.3)
            act.fire = s.shot_now = seen_fire and now < getattr(s, "burst_until", 0.0)
        elif on and now >= s.next_fire:
            act.fire = s.shot_now = True
            s.next_fire = now + s.fire_gap(w, d)
            if s.rng.random() < s.p.jumpy * JUMP_SHOT * s.p.jump:
                act.jump = True    # jump shot (never for bots with jump skill 0)
        act.zoom = s.zoom_level(now, w, d, seen_now)
        # he ducked behind cover close by: flush him out with a grenade where he went, once per time out of sight, the
        # aim already on the spot (no flick to throw) (the line to him gone 0.5-3 s, out of sight or seen through the
        # level, 4-16 u; until then 4-14 u, the aim within 0.3 rad)
        blind_for = now - s.per.clear_at.get(tid, -9.0)
        if seen_now and clear:
            s.flush_rolled = False
        elif 0.5 < blind_for < 3.0 and not getattr(s, "flush_rolled", False) and 4.0 < d < 16.0 and s.p.tactics > 0.3:
            s.flush_rolled = True
            ya, _ = angles_to(eye(me), b.pos)
            if (now >= s.grenade_ready_at and not mates_near and abs(wrap(ya - act.yaw)) < 0.45
                    and s.rng.random() < min(1.0, 1.3 * s.p.grenades * (0.4 + 0.6 * s.p.tactics))):
                act.grenade = True
                s.grenade_ready_at = now + s.rng.uniform(3, 7)
                s.notes.append("grenade: flush him out")
        if head and on and s.rng.random() < s.p.crouchy * 0.5:
            act.crouch = True      # crouch at the kill
        if (now - s.engaged_since < 0.6 and 4 < d < 25 and now >= s.grenade_ready_at and not mates_near
                and getattr(s, "nade_open", False)):   # a Regular "forgets to throw grenades" (guide 01 s9): by tactics
            act.grenade = True     # grenade opener (from 4 u: a frag's blast is under 2 u)
            s.nade_open = False
            s.grenade_ready_at = now + s.rng.uniform(3, 7)
        elif (target is not None and target.vehicle != -1 and s.p.personality == "spammer" and 3 < d < 15
              and now >= s.grenade_ready_at and not mates_near):
            act.grenade = True     # the Grenadier sticks the vehicle up close (vehicle_mission)
            s.grenade_ready_at = now + s.rng.uniform(3, 6)
            s.notes.append("grenade: the vehicle")
        elif (seen_now and 4 < d < 22 and now >= s.grenade_ready_at and not mates_near
              and s.rng.random() < NADE_RATE * dt * s.p.grenades):
            # another one into a fight that goes on (a person throws through a long fight). Easy too since (the
            # player: yes to Easy toward 70%; 0.55-0.63 a minute live, the player 2.37)
            act.grenade = True
            s.grenade_ready_at = now + s.rng.uniform(3, 7)
            s.notes.append("grenade: keep the pressure")
        spot = s.kit.grenade_spot(now, me, target, snap) if target is not None else None
        if spot is not None and 1.5 < d < 20 and now >= s.grenade_ready_at and not mates_near and w not in LUNGE:
            # beside an armor lock (the splash lands as it ends), into the heal field he stands in,
            # at a hardlight user's feet (guide 08)
            act.yaw, act.pitch = angles_to(eye(me), spot)
            act.grenade, s.grenade_ready_at = True, now + s.rng.uniform(4, 9)
            s.notes.append("grenade: counter his ability")
            s.att = ["grenade", tid, round(spot.x, 1), round(spot.y, 1)]
        if (b.pos - me.pos).flat() < MELEE_R and abs(b.pos.z - me.pos.z) < 1.3 and w not in LUNGE:
            act.melee = True
        if w in LUNGE:
            # the sword and the hammer attack with the trigger (the hammer's swing, the sword's lunge); the melee button
            # is only a bash
            act.melee = False
            # and no grenade: one at the man it runs at kept it off its own blast's spot (own_grenade, NADE_CLEAR 4.5 u,
            # 3 s), the "bubble" round him (owner, issue #3; Cyberdyne 20261031-014033: 191 of 909 sword-in-hand fight
            # rows within 4.5 u were "grenade: clear of my own", 171 of them walking away from him)
            act.grenade = False
            if act.fire and w == "sword" and now - getattr(s, "lunge_t", -9.0) > CLASH_MELEE[1]:
                s.lunge_t = now                 # the first press of this exchange
            # a sword clash (both lunged, both parried): the one still standing follows with a quick melee, once. Not
            # Easy (owner)
            since = now - getattr(s, "lunge_t", -9.0)
            if (w == "sword" and s.p.tier != "novice" and target is not None and target.alive and target.weapon == "sword"
                    and CLASH_MELEE[0] <= since <= CLASH_MELEE[1] and getattr(s, "clash_for", None) != s.lunge_t
                    and seen_now and (b.pos - me.pos).flat() < MELEE_R + 0.3 and abs(b.pos.z - me.pos.z) < 1.3):
                act.melee, act.fire, s.shot_now, s.clash_for = True, False, False, s.lunge_t
                s.notes.append("sword clash: melee")
        if w in LUNGE and act.fire and s.game in MELEE_TRIGGER:
            # Reach: a "melee only" weapon (its tag's flag) attacks through the melee action, the trigger alone did
            # nothing (live Boardwalk Infection: 1160 trigger presses in reach, no damage; with the melee bit the first
            # press lunged 1.47 u from 2.2 u and killed)
            act.melee = True
        if (act.melee and w not in LUNGE and target is not None and not s.dry(me) and me.shield < target.shield - 0.5
                and s.p.tier == "novice"):
            act.melee = False      # no melee trade on the lower shield (guide 01 Melee): shoot, back off. Easy only since
        # (by the melee's own reach, floor distance: by 3D distance a man 1.5 u off and 1.2 u under left it unset while
        # the melee was on, and a novice's tick failed, tools/test_modes.py)
        near = (b.pos - me.pos).flat() < 1.6 and abs(b.pos.z - me.pos.z) < 1.3
        s.close_since = (getattr(s, "close_since", None) or now) if near else None
        if act.melee and w not in LUNGE and s.p.tier == "novice" and not s.dry(me) and now - s.close_since < 1.5:
            act.melee = False      # Easy is slow to swing: it keeps shooting the man in its face
        if act.melee:
            # one press per swing, the swing plays out before the next (guide 01: two melees kill a full
            # shield). Reach's melee bit is press-only: a held one gave one swing per close fight
            if now < getattr(s, "melee_at", 0.0):
                act.melee = False
            else:
                s.melee_at = now + s.rng.uniform(0.6, 0.9)
        if (w in LUNGE and on) or act.melee:
            # the red reticle's target in the action (shared.h HB_TARGET_SHIFT), as a player's own input carries it: the
            # game's sword / hammer / melee lunge locks on from it; botcore had cleared it, and no bot press lunged
            act.lunge_at = tid
        # the fight's own reasons (tools/observer.py): on target or not, how long unseen, chasing by
        # the nav, held fire for a splash weapon, out of ammo
        s.fx = dict(d=round(d, 1), on=bool(on), hold=bool(hold), unseen=round(min(unseen, 99.0), 1),
                    chase=bool(unseen > 0.5 and s.p.tactics > 0.15), strafe=bool(act.strafe), press=bool(s.pressed_in),
                    splash_hold=bool(blast and (d < blast or mates_near)), dry=bool(s.dry(me)),
                    charge=round(now - s.charge_since, 2) if getattr(s, "charge_since", None) is not None else None,
                    zoom=act.zoom)

    def relocate_point(s, now, me, snap):
        """After a kill in SWAT (no shields: every shot gives the shooter away) or with a sniper, Normal and up move off
        the spot the shot came from (audit SLAY-SWAT-07, SLAY-SNIPE-03): to cover from where the dead man stood, for 3
        s. The kill: the man it fought died within a second of its last shot."""
        if s.p.tier == "novice" or not (snap.shieldless or me.weapon in SNIPERS) or me.has_flag:
            return None
        held = getattr(s, "relocate_to", None)
        if held is not None and now < getattr(s, "relocate_until", 0.0):
            return held if dist(me.pos, held) > 1.0 else None
        g = s.goal
        e = snap.player(g[1]) if g[0] == "fight" and isinstance(g[1], int) else None
        if e is None or e.alive or now - getattr(s, "last_shot_t", -9.0) > 1.0:
            return None
        spot = s.cover_from(me, [e.pos])
        if spot is None or dist(me.pos, spot) > COVER_NEAR:
            return None
        s.relocate_to, s.relocate_until = spot, now + 3.0
        s.note("a kill: off this spot")
        return spot

    def regroup_point(s, now, me, snap, seen, post):
        """Alone against two (Normal and up): no teammate within FORM_R (8 u, the director's formed-up measure) and two
        enemies on us, in sight or one of them just hit us, hurting us now and not behind us on health: break the line
        of one, to cover that blocks them both, else back toward a teammate within 15 u, shooting on the way (think's
        retreat). (Sim: with no hurt or health test it cost Normal 15% of its kills, 227 -> 193.) Not when all in
        (posture 0.75+, the blitz), not with a man to finish (shield down within 8 u), not with nowhere to go (in the
        open: fight it out). Live deaths (logs/tmp/unseen.py, crit.py): of 153 to a man not seen before his first hit,
        105 came while the bot fought someone else (it turned to him in a median 0.14 s; the crossfire killed it);
        36-45% of deaths had nobody within 8 u and 33-54% two or more enemies on the bot."""
        if s.p.tier == "novice" or me.has_flag or post >= 0.75 or getattr(s, "blitz_until", 0.0) > now:
            return None
        if any(p.team == me.team and p.alive and p.id != me.id and dist(p.pos, me.pos) < REGROUP_MATE
               for p in snap.players):
            return None
        held = getattr(s, "regroup_to", None)
        if held is not None and now < getattr(s, "regroup_until", 0.0) and dist(me.pos, held) > 1.0:
            return held                                       # (on its way: no flip back into the open each decision)
        foes = set(k for k in seen if dist(me.pos, s.per.beliefs[k].pos) < 20.0)
        att = getattr(s.per, "attacker_id", None)
        if att in s.per.beliefs and now - getattr(s.per, "attacker_t", -9.0) < 1.0:
            foes.add(att)
        if len(foes) < 2 or now - getattr(s, "hit_at", -9.0) > 1.0:
            return None                                       # (two about but not hurting us: fight on)
        mine = me.shield + me.health
        if all(snap.player(k) is None or snap.player(k).shield + snap.player(k).health < mine - 0.2 for k in foes):
            return None                                       # ahead of both on health: take them
        for k in foes:
            e = snap.player(k)
            if (k in seen and e is not None and e.shield <= 0.25 and (not snap.shieldless or e.health <= 0.5)
                    and dist(me.pos, e.pos) < 8.0):
                return None                                   # a kill to take first
        spot = s.cover_from(me, [s.per.beliefs[k].pos for k in foes])
        if spot is None or dist(me.pos, spot) > COVER_NEAR * (0.5 + 0.5 * s.p.tactics):
            mates = [p for p in snap.players if p.team == me.team and p.alive and p.id != me.id
                     and dist(p.pos, me.pos) < 15.0]
            spot = min(mates, key=lambda p: dist(p.pos, me.pos)).pos if mates else None
        if spot is None:
            return None
        s.regroup_to, s.regroup_until = spot, now + 2.0
        s.note("alone against two: pull back")
        return spot

    def vehicle_stalled(s, now, me, v, vp):
        """Walking to a vehicle and no closer to its seat for 6 s (short of the door): it cannot be reached from here, so
        give it up 30 s as headway does places. The Pit, live: bots stood still 3 u from the Warthog in blue's garage,
        the nav's next step their own spot, its 'drive' order renewing the goal for minutes (the player's corner, flags
        at 111.8, 252.9 and 259.6 s: #14, #6 and #7 at (-16.8, 14.0), goal vehicle 3799252997)."""
        d = (me.pos - vp).flat()
        w = getattr(s, "veh_way", None)
        if w is None or w[0] != v["handle"] or d < w[1] - 0.5 or now - w[3] > 1.0:   # (a new walk to it: a new clock)
            s.veh_way = (v["handle"], d, now, now)
            return False
        s.veh_way = w[:3] + (now,)
        if now - w[2] < 6.0:
            return False
        s.__dict__.setdefault("veh_ban", {})[v["handle"]] = now + 30.0
        s.veh_way, s.vehicle_until, s.vehicle_retry_at = None, now, now + 20.0
        return True

    def power_spawn(s, q):
        """A power weapon's spawn (a map item: a weapon of value 0.8 or more) at q."""
        return any(it["kind"] == "weapon" and it["value"] >= 0.8 and dist(it["pos"], q) < 0.5
                   for it in s.map.get("items", []))

    def veh_banned(s, v, now):
        return getattr(s, "veh_ban", {}).get(v["handle"], 0.0) > now

    def keep_off(s, me, target, d):
        """Normal and up, against a man with a sword or a hammer and no such weapon of our own: back off while he is within
        his reach and 1.5 u more (guide 03: "a bot without a sword keeps 25 ft away ... shoots the head during the
        approach"; audit JUG-02, the Juggernaut's hammer). A shotgun holds its ground: it wins at the corner."""
        if s.p.tier == "novice" or target is None or target.weapon not in LUNGE or me.weapon in LUNGE:
            return False
        if me.weapon in ("shotgun", "scattershot"):
            return False
        return d < reach(target.weapon, s.game) + 1.5

    def sword_cover(s, now, me, b, target, d):
        """A sword or hammer is a close-quarters weapon (owner, issue #3: "don't run across an open field at someone far
        away"): Normal and up, a man past SWORD_OPEN with a gun who looks at us is waited for in cover from him (the
        nearest within COVER_NEAR), as an ambush; None: run at him (close, he looks away, no cover, a blitz on)."""
        # (only from a gun that reaches us there: Reach Boardwalk Infection, live, a zombie hid 4 minutes 28-33 u from
        # survivors whose shotguns watched it)
        if (s.p.tier == "novice" or d <= SWORD_OPEN or target is None or target.weapon in LUNGE
                or reach(target.weapon, s.game) < d
                or getattr(s, "blitz_until", 0.0) > now or not aims_at(target, me, 0.35)):
            return None
        held = getattr(s, "sword_hide", None)
        if held is not None and now < held[1] and dist(held[0], me.pos) < COVER_NEAR + 2.0:
            return held[0]
        spot = s.cover_from(me, [b.pos])
        if spot is None or dist(spot, me.pos) > COVER_NEAR:
            return None
        s.sword_hide = (spot, now + 2.0)
        s.note("sword: to cover, not across the open")
        return spot

    def push_in(s, me, b, target, snap, d, lo):
        """Close the distance, Normal and up: with a gun better up close (an automatic, a shotgun), or on a man whose
        shield is down within 8 u, on its own floor, until melee reach; a rifle at arm's length steps in to the melee
        rather than backing off. Never a sniper or a launcher (their reach, their blast); the sword and hammer rush
        already. Live, in band the stick closed on 9-12% of fight ticks (The Pit, Spire, Avalanche; logs/tmp/gunuse.py)."""
        w = me.weapon
        if s.p.tier == "novice" or w in SNIPERS or w in BLAST or w in LUNGE or abs(b.pos.z - me.pos.z) > 1.3:
            return False
        if target is not None and target.weapon in LUNGE:
            return False                                  # never into a sword or a hammer (keep_off)
        if d <= MELEE_R * 0.8:
            return False
        weak = (target is not None and target.alive and target.shield <= 0.25
                and (not snap.shieldless or target.health <= 0.5))
        return w in CQ or (weak and d < 8.0) or d < min(lo, 2.0)

    def press_on(s, me, b, target, snap, d, unseen):
        """Intent to kill (profiles.PRESS, rolled per fight): inside the gun's band a Heroic or Legendary bot walks in on
        the man it fights, strafing on the way, instead of holding its ground: while he is in view, the only man in view
        or shield down, and the trade even or better (his shield down: always), to PRESS_NEAR. A skilled player walked
        toward the man in view 47% of the in-view time, Legendary 33% (tools/intent.py). Not with a sniper, a launcher,
        a sword or a hammer (their own rules), not onto another floor, not into his sword, not in a match without
        shields (Halo 2 SWAT's Legendary already plays as it should)."""
        if not getattr(s, "press_fight", False) or snap.shieldless or target is None or not target.alive:
            return False
        w = me.weapon
        if w in SNIPERS or w in BLAST or w in LUNGE or target.weapon in LUNGE or unseen > 0.25:
            return False
        if d <= PRESS_NEAR or abs(b.pos.z - me.pos.z) > 1.3:
            return False
        if target.shield <= 0.25:
            return True                      # his shield is down: commit to the kill
        if len(s.per.visible_enemies()) > 1:
            return False                     # two in view: hold (regroup_point and the retreat rules decide)
        return me.shield + me.health >= target.shield + target.health - 0.3

    def fire_gap(s, w, d):
        """Seconds to the next press of a one-shot-a-press weapon: spam up close, the paced cadence at Medium,
        a little slower at Long (guide 03: DMR "At Short band spam; at Medium pace at 0.4 s; at Long 0.5
        s"). A Recruit keeps one pace at every range."""
        paced = FIRE_GAP.get(w, 0.2)
        if w == "sword":
            return LUNGE_GAP * s.rng.uniform(0.9, 1.2)    # pressed again and again in reach, as the player does
        if s.p.tactics <= 0.15:
            return paced * s.rng.uniform(1.0, 1.5)
        if d < SHORT_BAND:
            return min(paced, FIRE_FAST.get(w, paced)) * s.rng.uniform(1.0, 1.2)
        return paced * (s.rng.uniform(1.1, 1.35) if d > 10.0 else s.rng.uniform(0.95, 1.2))

    def charge_fire(s, now, me, target, tid, d, on, seen, act, how):
        """Weapons fired by a held charge. Release kind: hold until charged and on him, let go to fire: the
        plasma pistol's overcharged bolt strips a full shield and the precision weapon comes out for the head
        shot (the noob combo, choose_weapon), the plasma launcher's bombs home on what the reticle held, the
        railgun's slug. Auto kind (spartan laser): held on him until it goes off by itself; losing him lets go
        (no wasted shot)."""
        kind, need = how
        w = me.weapon
        since = getattr(s, "charge_since", None)
        if w == "plasma_pistol" and (target is None or target.shield <= 0.25 or s.p.tactics <= 0.15):
            s.charge_since = None      # no shield to strip (or a Recruit, who never learned it): plain shots
            if on and now >= s.next_fire:
                act.fire = s.shot_now = True
                s.next_fire = now + s.fire_gap(w, d)
            return
        if w == "plasma_launcher" and need > 0.05 and target is not None and target.vehicle != -1:
            # on a vehicle: every bomb locked (PL_LOCK_S of the reticle on it) before letting go. The reticle is on it
            # anywhere within the vehicle's size (PL_LOCK_R), not the man-sized window "on" uses: live (Breakpoint
            # 20261103-181702, bot 5) the aim sat within 2.4 deg of a Banshee at 24-37 u for 12 s and never let go.
            # Off it over PL_SLIP s, the clock restarts
            ya, pa = angles_to(eye(me), target.pos)
            r = math.atan2(PL_LOCK_R, max(d, 0.3))
            if seen and abs(wrap(ya - act.yaw)) < r and abs(pa - act.pitch) < r:
                s.pl_on, s.pl_seen = getattr(s, "pl_on", None) or now, now
            elif now - getattr(s, "pl_seen", -9.0) > PL_SLIP:
                s.pl_on = None
            need, on = PL_LOCK_S, s.pl_on is not None and now - s.pl_on >= PL_LOCK_S and now - s.pl_seen < 0.05
        hi = WEAPON_RANGE.get(w, (5.0, 25.0))[1]
        if since is None:
            if seen and d <= hi * 1.3 and now >= s.next_fire:
                s.charge_since, s.charge_w = now, w   # charging while still closing in
                act.fire = True
            return
        held = now - since
        lost = now - s.per.last_seen.get(tid, -9.0)
        if kind == "auto":
            if lost > 0.6 or now - s.per.clear_at.get(tid, -9.0) > 0.3 or held > need + 0.4:   # (no line: let go)
                s.charge_since, s.next_fire = None, now + 0.4    # gone (let go: no shot), or it has fired
                s.shot_now = held > need
                return
            act.fire = True
            return
        if held < need or not on:
            act.fire = True            # charging, or charged and waiting for him to be in the reticle (a fight
            return                     # that ends lets go wherever it looks: think's non-fight branch)
        s.charge_since, s.next_fire = None, now + 0.35           # let go: it fires
        s.shot_now = True
        if w == "plasma_pistol":
            s.combo_at = now

    def zoom_level(s, now, w, d, seen):
        """Scope level for this shot (guide 03): rifles zoom past Short band, snipers past 6 units and to the second
        level past 20; never right after a hit (CE-Reach knock the scope off: re-zoom once the hits stop). Easy zooms
        only with a sniper-type weapon."""
        # (back in the scope 0.5 s after the last hit, Normal and up, not 1 s: of the ticks a scoped gun fought a man in
        # view past its zoom range unzoomed, waiting out a hit was 90% (The Pit) and 95% (Spire), logs/tmp/zoomwhy.py.
        # Halo 4 has no descope on a hit, only flinch: no wait there)
        wait = 0.0 if s.game == "h4" else 1.0 if s.p.tier == "novice" else 0.5
        if w not in SCOPED or not seen or now - getattr(s, "hit_at", -9.0) < wait or (s.p.tier == "novice" and w not in SNIPERS):
            return 0
        if d < ZOOM_FROM.get(w, SHORT_BAND + 0.5):
            return 0
        return 2 if w in SNIPERS and d > ZOOM_FAR else 1

    # shots on target with no damage, over at least DRY_SECONDS, before a bot decides the sight
    # line is blocked. The old 4..16 shots fired within one burst: a shield hit the reader missed
    # or a late damage read taught bots that open lines were walls, so they ignored players
    # standing in front of them (1064 learned blocks on H2A Zenith)
    DRY_SHOTS = {"novice": 40, "average": 24, "strong": 16, "top": 12}
    DRY_SECONDS = 3.0
    DRY_MIN_RANGE = 8.0            # closer than this the level data's sight line is trusted

    def dry_fire_check(s, me, target, tid):
        """Learn sight lines the level data gets wrong: count shots fired while on target since
        the target last took damage; past the tier's limit the line is marked blocked (shared by
        every bot through the terrain, saved per map), so the bot stops fighting through rock and
        moves to get a real line."""
        if target is None or not target.alive:
            return
        hp = target.shield + target.health
        st = s.__dict__.setdefault("dry_shots", {}).get(tid)
        if st is None or hp < st[1] - 0.005:
            s.dry_shots[tid] = st = [0, hp, None]    # first look, or it just took damage
        st[1] = hp
        if s.fired_last and s.aim.on_target(me, target.pos) and dist(me.pos, target.pos) > s.DRY_MIN_RANGE:
            st[0] += 1
            st[2] = st[2] if st[2] is not None else s.now
        if st[0] >= (s.p.extra or {}).get("dry_shots", s.DRY_SHOTS.get(s.p.tier, 24)) and s.now - st[2] >= s.DRY_SECONDS:
            if getattr(s, "_terrain", None) is None:
                from .maps import terrain
                s._terrain = terrain(s.map)
            s._terrain.learn_blocked(me.pos, target.pos)
            s.dry_shots[tid] = [0, hp, None]

    def alt_post(s, now, banned, bans):
        """A walkable open spot within 8 u of the order's point, 3+ u from the banned one and not banned itself, kept 30 s
        (choose_goal: a post headway gave up on is swapped for one beside it). None: none found."""
        cur = getattr(s, "alt_at", None)
        if (cur and now < cur[1] and dist(cur[0], banned) >= 3.0 and dist(cur[0], s.order.point) < 9.0
                and bans.get(s._goal_key(("objective", cur[0])), 0) <= now):
            return cur[0]
        T = s.T()
        for _ in range(3):
            p = T.open_spot(s.order.point, 8.0, s.rng)
            if p is not None and dist(p, banned) >= 3.0 and bans.get(s._goal_key(("objective", p)), 0) <= now:
                s.alt_at = (p, now + 30.0)
                return p
        return None

    @staticmethod
    def _goal_key(goal):
        return goal[0], round(goal[1].x), round(goal[1].y)

    def headway(s, now, me, goal, snap=None):
        """A human who has not got any closer to where he is going for 6 s (an item behind a teleporter frame, a
        post the level data puts in a gap he cannot fit) gives up on it for 30 s and does something else. Never a
        carrier's run: with it banned he roamed with the flag (Halo 3 Multi Flag: both carriers dropped their run
        within 6 s and wandered; six pickups, no capture). Never a zone it is to stand in (an Invasion territory, a
        Territories zone, a hill): the fight on the way stalls the walk, and the post swapped for one 3-9 u beside it
        (alt_post) kept attackers off the territory (live Spire 20261105-005649, phase 2: 2,100 of the attackers'
        decisions on such spots against 2,300 on the territory, 5 s inside it in 240 s, the phase lost)."""
        if me.has_flag:
            return
        if goal[0] == "objective" and snap is not None and (
                s.zone_at(snap, goal[1]) is not None
                or (snap.mode == "king" and snap.hill is not None and dist(goal[1], snap.hill) < (snap.hill_r or 2.5) + 0.5)):
            return
        grab = getattr(s, "grab_point", None)
        if grab is not None and goal[0] == "objective" and dist(goal[1], grab) < 1.0:
            return                     # an objective to pick up: keep at it (Spire, live: banned 2 u from the core,
                                       # the attackers roamed; banned in the lift queue 35 u under it, they hunted and
                                       # fetched guns for 30 s at a time)
        key, d = s._goal_key(goal), dist(me.pos, goal[1])
        # progress along the path, not the straight line: a ramp that winds away from the post first (Boneyard's
        # Invasion territories, 5.5 u up) read as 6 s of no progress, and every attacker gave up halfway up (sim)
        T = getattr(s, "_terrain", None)
        if T is None:
            from .maps import terrain
            T = s._terrain = terrain(s.map)
        try:
            c = float(T._field(goal[1])[T._node(me.pos)])
            if math.isfinite(c):
                d = c
        except Exception:
            pass
        if getattr(s, "way_key", None) != key or d < s.way_best - 0.5:
            s.way_key, s.way_best, s.way_t = key, d, now
        elif now - s.way_t > 6.0 and d > (1.5 if goal[0] == "item" else 4.0):   # posts: loitering is near
            s.__dict__.setdefault("goal_ban", {})[key] = now + 30.0
            s.way_key, s.next_decide = None, now

    def detour(s, now, me, kind, point):
        """Not every long trip by the shortest way: once per goal, a trip over 15 units goes by a zone off the straight
        line, 25-75 degrees aside, with a chance by the bot's route craft (Easy runs straight: routes 0) and style
        (flankers most). Reached: on to the goal."""
        if kind not in ("objective", "hunt") or s.p.routes <= 0:
            return point
        key = (kind, round(point.x), round(point.y))
        if getattr(s, "detour_key", None) != key:
            s.detour_key, s.detour_pt = key, None
            far = (point - me.pos).flat()
            want = (0.3 + 0.5 * s.p.routes) * (1.5 if s.p.personality == "flanker" else 1.0)
            if far > 15.0 and s.rng.random() < want:
                line = math.atan2(point.y - me.pos.y, point.x - me.pos.x)
                cands = []
                for z in s.map["zones"].zones:
                    c = z.center
                    dz = (c - me.pos).flat()
                    off = abs(wrap(math.atan2(c.y - me.pos.y, c.x - me.pos.x) - line))
                    if 6.0 < dz < far and (c - point).flat() < far and math.radians(25) < off < math.radians(75):
                        cands.append(c)
                if cands:
                    s.detour_pt = s.rng.choice(cands)
        if s.detour_pt is not None:
            if (me.pos - s.detour_pt).flat() < 3.0:
                s.detour_pt = None
            else:
                s.way_key = None          # (headway: the goal's clock waits while we go round)
                return s.detour_pt
        return point

    def loiter(s, now, me, post, far=2.5):
        """At a post a human is not a statue: he drifts a few steps around it, stops, peeks, and
        drifts again (campers stay longer at each spot). Only spots with floor under them, at most `far` off."""
        if (me.pos - post).flat() > max(3.5, far + 1.0):
            s.loiter_pt = None
            return post
        if getattr(s, "loiter_pt", None) is None or now > s.loiter_until:
            # (shorter stops than 1.5-4 s: bots stood still 16-28% of the time to the player's 13%, half of it at posts,
            # Cyberdyne; 0.6-2 s left Invasion bots still 2-10% of the time, Spire; campers twice as long. 0.8-2.6 s
            # until at a post with room: the Oddball side holding the ball stood 27-59% of its time at its guard posts,
            # the Normals 0.35 on average, Boardwalk; Stockpile and King posts 0.22-0.28; the player 0.13. A small zone
            # keeps the longer stops: quicker ones walked Easy 1.5 u out of a 0.9 u zone)
            room = far >= 1.5
            camp = (1.5 if room else 2.0) if s.p.camping > 0.5 else 1.0
            s.loiter_pt, s.loiter_until = post, now + s.rng.uniform(*((0.4, 1.4) if room else (0.8, 2.6))) * camp
            if getattr(s, "_terrain", None) is None:
                from .maps import terrain
                s._terrain = terrain(s.map)
            # several tries, nearer each time: one try (taken 70% of the time) failed on narrow ledges and the bot
            # stood on its post for 10 s and more, only turning its head (Construct, live)
            for r in (far, far * 0.7, far * 0.45, min(1.2, far)):
                a = s.rng.uniform(0, 2 * math.pi)
                q = post + Vec(math.cos(a) * r, math.sin(a) * r, 0)
                if s._terrain.ground_ok(q.x, q.y, post.z, drop=0.6) and s._terrain.los(post, q):
                    s.loiter_pt = q
                    break
        return s.loiter_pt

    def zone_shuffle(s, now, me, centre, act):
        """Holding a zone smaller than a walk's stopping distance (Boneyard's boxes, 0.9 u from the middle): a person
        shuffles side to side across his view in it, never a statue, pulled back toward the middle (live Boneyard: Easy
        stood still 46% of the time to the player's 13%; the loiter's 0.5 u spots ended inside the walk's arrival
        distance). Exempt from the slow-down for walking off the view (the steps are meant to be sideways)."""
        if now >= getattr(s, "shuffle_flip", 0.0):
            s.shuffle_dir = -getattr(s, "shuffle_dir", 1.0)
            s.shuffle_flip = now + s.rng.uniform(0.2, 0.45)
        c, n = math.cos(me.yaw), math.sin(me.yaw)
        vx, vy = -n * 0.4 * s.shuffle_dir + (centre.x - me.pos.x) * 2.0, c * 0.4 * s.shuffle_dir + (centre.y - me.pos.y) * 2.0
        act.forward = max(-1.0, min(1.0, vx * c + vy * n))
        act.strafe = max(-1.0, min(1.0, -vx * n + vy * c))
        s.exact_until = max(getattr(s, "exact_until", 0.0), now + 0.05)
        s.shuffle_until = now + 0.1                # (unstuck: in place on purpose)

    def jump_onto(s, me, target):
        """The landing of a people's jump (LiDAR, Terrain.hjumps) on a raised stand within 3 u of target, at least
        0.4 u over us, that the nav reaches from here (with jumps): the nearest; None without one."""
        T = s.T()
        best, near, ok = None, 0, 0
        for f, t, k, w in getattr(T, "hjumps", ()):
            q = Vec(*k["land"][:3])
            if (q - target).flat() < 3.0 and q.z > me.pos.z + 0.4:
                near += 1
                if T.reaches(me.pos, q, jump=s.p.jump >= 0.5, human=True):
                    ok += 1
                    if best is None or dist(me.pos, q) < dist(me.pos, best):
                        best = q
        s.onto_why = f"{near} landings by it, {ok} reached, {len(getattr(T, 'hjumps', ()))} jumps"   # (the trace's notes)
        return best

    def post_loiter(s, now, me, post):
        """A CTF defender works its post from cover: it moves between the LiDAR cover spots within 4 u of the post that
        see our flag stand (the post among them), a few seconds at each, peeking. loiter()'s random steps up to 2.5 u
        walked defenders out from behind the wall their post is in front of. No such spots: loiter() 1 u."""
        if (me.pos - post).flat() > 6.0:
            s.loiter_pt = None
            return post
        if getattr(s, "loiter_pt", None) is None or now > s.loiter_until:
            key = (round(post.x, 1), round(post.y, 1))
            cache = s.__dict__.setdefault("_post_spots", {})
            if key not in cache:
                if getattr(s, "_terrain", None) is None:
                    from .maps import terrain
                    s._terrain = terrain(s.map)
                T, home, eye = s._terrain, s.map["flags"].get(s.team % 2, post), Vec(0, 0, 0.6)
                cs = getattr(T, "cover_spots", None)
                spots = [post]
                if cs is not None and len(cs):
                    import numpy as np
                    near = np.nonzero((np.hypot(cs[:, 0] - post.x, cs[:, 1] - post.y) <= 4.0)
                                      & (np.abs(cs[:, 2] - post.z) < 1.5))[0]
                    for k in near[:40]:
                        q = Vec(float(cs[k][0]), float(cs[k][1]), float(cs[k][2]))
                        if T.los(q + eye, home + eye) and all(dist(q, o) >= 1.2 for o in spots):
                            spots.append(q)
                cache[key] = spots
            spots = cache[key]
            if len(spots) == 1:
                return s.loiter(now, me, post, 1.0)
            s.loiter_pt = s.rng.choice(spots)
            s.loiter_until = now + s.rng.uniform(2.0, 5.0) * (1.5 if s.p.camping > 0.5 else 1.0)
        return s.loiter_pt

    def aim_head(s, now, me, target, tid):
        """Head or chest, decided once per target, weapon and shield state (a human does not flip
        every tick), precision weapons only (HEAD_AIM): Easy goes for the head 20% of the time
        whatever his shield; the rest aim center of mass until his shield pops (SWAT: from the
        first shot), then the head as often as the tier knows to. Automatic weapons: chest."""
        if target is None or me.weapon not in PRECISION:
            return False
        bare = target.shield <= 0.0
        key = (tid, me.weapon, bare)
        if getattr(s, "head_key", None) != key:
            p = (s.p.extra or {}).get("head_aim", HEAD_AIM.get(s.p.tier, 0.5))
            s.head_key, s.head_on = key, s.rng.random() < (p if bare or s.p.tier == "novice" else 0.0)
        return s.head_on

    # --- movement helpers
    def give_way(s, me, snap, act):
        """Walk around a player standing in the way instead of into him (half of all stalls were
        bots pushing into each other at posts and doorways): bend the move up to ~70 degrees
        away from the nearest one ahead within 1.3 units; where the bend has no floor (a
        corridor) slow down and queue behind him instead."""
        mag = math.hypot(act.forward, act.strafe)
        if mag < 0.3:
            return
        move = act.yaw + math.atan2(act.strafe, act.forward)
        best = None
        for p in snap.players:
            # teammates only: an enemy in the way is the fight (bent round every man within 1.3 u, a sword or melee
            # rush turned off the man it ran at, 23-90 deg: the "bubble round the target", owner, issue #3)
            if p.id == me.id or not p.alive or p.vehicle != -1 or abs(p.pos.z - me.pos.z) > 0.8 or p.team != me.team:
                continue
            d = (p.pos - me.pos).flat()
            rel = wrap(math.atan2(p.pos.y - me.pos.y, p.pos.x - me.pos.x) - move)
            if d < 1.3 and abs(rel) < 1.0 and (best is None or d < best[0]):
                best = (d, rel, p.vel.flat() > 0.5)
        if best:
            bent = move - math.copysign(1.2 * (1.3 - best[0]) / 1.3 + 0.4, best[1])
            if getattr(s, "_terrain", None) is None:
                from .maps import terrain
                s._terrain = terrain(s.map)
            side = s.feet(me) + Vec(math.cos(bent), math.sin(bent), 0) * 0.6
            l, j, i = s._terrain._node(side)
            if not (s._terrain.lwalk[l, j, i] and abs(s._terrain.lz[l, j, i] - side.z) < 0.6):
                if not best[2]:
                    return   # he stands still: queuing never ends (live, Boneyard: four attackers stood
                             # behind each other at a crate for minutes); walk on, he gets nudged
                # no room to pass (a corridor): queue behind him, the way people do in a doorway
                f = 0.0 if best[0] < 0.9 else 0.4
                act.forward, act.strafe = act.forward * f, act.strafe * f
                return
            act.forward, act.strafe = mag * math.cos(bent - act.yaw), mag * math.sin(bent - act.yaw)

    def note(s, text):
        """Something overrode the plan this tick: the observer shows it (tools/observer.py)."""
        n = getattr(s, "notes", None)
        if n is not None and text not in n:
            n.append(text)

    TRAP_WALL = True                # a bot trapped (pushing into a wall or rock, or under an object, alone) marks the spot as
                                    # a wall at once, for good: Terrain.learn_trap, kept in maps/<map>/trapped_walls.json
                                    # and loaded into every later match's terrain (False: learn_stuck's votes, as before)
    TRAP_SAVE = True                # write those spots to that file (False: this run's marks last for the match only)
    TRAP_CROWD = 1.2                # no mark with anyone (friend or foe) within this of the bot: a body block, not a wall
    STUCK_S = 0.9                   # s of pushing without leaving 0.6 u before the wiggle (was 1.2: a body at 2.25 u/s is
                                    # 2 u on in that time, so 0.9 s on one spot is a catch, not a crowd; quicker out of
                                    # ledge lips and rock corners, and the spot is learned sooner)
    LEARN_IN_FIGHT = False          # stuck pushes during a fight teach spots (A/B: tools/sim_variant.py)
    FIGHT_LEARN_S = 2.5              # even with LEARN_IN_FIGHT off, a bot genuinely alone (no one explains the
                                      # block) stuck this long learns anyway: a wall the data lacks outlasts a
                                      # body block, which clears well under this

    REDIRECT_D = 0.55                   # a wall closer than this along the way we walk: step around it
    RUNUP_GIVE_UP = 5.0                 # s: a person's run-up not reached by then, the take-off within 2.5 u: from there

    def redirect(s, now, me, act):
        """The 'crosshair distance' (Terrain.clearance) along the way this bot walks: a wall right ahead turns the
        step toward the side with room (the nearest of +-30, +-60 degrees with clearance), before the bot runs into it.
        Not in a fight (strafing there is the fight's own) nor on a route people walked (they know it)."""
        if s.goal[0] == "fight" or getattr(s, "route", None) or me.vehicle != -1:
            return
        T = getattr(s, "_terrain", None)
        if T is None:
            from .maps import terrain
            T = s._terrain = terrain(s.map)
        walk = act.yaw + math.atan2(act.strafe, act.forward)        # (strafe +1 = left)
        ahead = T.clearance(s.feet(me), walk, 1.5)
        if ahead >= s.REDIRECT_D:
            return
        att = getattr(s, "att", None)                               # the nav's next point is short of the wall: a
        if att and att[0] == "path" and math.hypot(att[2] - me.pos.x, att[3] - me.pos.y) < ahead + 0.2:
            return                                                  # corner it turns at, not a wall in the way
        for off in (0.52, -0.52, 1.05, -1.05):
            if T.clearance(s.feet(me), walk + off, 1.5) >= 1.2:
                rel = walk + off - act.yaw
                act.forward, act.strafe = math.cos(rel), math.sin(rel)
                s.note("wall ahead: stepping around")
                return

    def blocked_way(s, T, me, snap, yaw):
        """What stops a step along yaw: "wall" (a wall or placed object nearer than REDIRECT_D, Terrain.clearance),
        "vehicle" (one within 1.5 u ahead and 0.9 u to the side: the level data has none), else None."""
        if T.clearance(s.feet(me), yaw, 1.0) < s.REDIRECT_D:
            return "wall"
        c, sn = math.cos(yaw), math.sin(yaw)
        for v in getattr(snap, "vehicles", ()) or ():
            q = vpos(v)
            dx, dy = q.x - me.pos.x, q.y - me.pos.y
            if 0.0 < dx * c + dy * sn < 1.5 and abs(dy * c - dx * sn) < 0.9 and abs(q.z - me.pos.z) < 1.5:
                return "vehicle"
        return None

    def own_grenade(s, now, me, act):
        """Its own grenade: the spot it threw at (the aim's point, s.att) it does not step into for NADE_FUSE s; a step
        toward it within NADE_CLEAR u is turned the other way."""
        if act.grenade and s.att and len(s.att) >= 4 and s.att[2] is not None:
            p = Vec(s.att[2], s.att[3], me.pos.z)
            if 1.0 < (p - me.pos).flat() < 25.0:
                s.nade_at = (p, now + NADE_FUSE)
        n = getattr(s, "nade_at", None)
        if n is None or now > n[1]:
            return
        to = n[0] - me.pos
        d = to.flat()
        if d > NADE_CLEAR or d < 1e-6:
            return
        c, sn = math.cos(act.yaw), math.sin(act.yaw)
        mx, my = c * act.forward - sn * act.strafe, sn * act.forward + c * act.strafe   # strafe + = left
        ux, uy = to.x / d, to.y / d
        toward = mx * ux + my * uy
        if toward <= 0.0:
            return
        mx, my = mx - 2.0 * toward * ux, my - 2.0 * toward * uy       # the step toward it, mirrored away
        act.forward, act.strafe = c * mx + sn * my, -sn * mx + c * my
        s.note("grenade: clear of my own")

    def obstacle_guard(s, now, me, act, snap):
        """What is right in front of the move, seen before the bot runs into it: blocked_way along the way it moves. In a
        fight (redirect stands aside there, and unstuck turned the strafe only after 0.6 s pushing into the wall): the
        strafe goes the other way, else 30-90 degrees off, else the move into it stops. Walking: round a vehicle
        (redirect does the walls), left as it was if no way round shows (unstuck's turn). A bot holding still in cover
        moves nothing here."""
        if me.vehicle != -1 or getattr(s, "route", None) or getattr(s, "lift_ride", None) or s.planned_jump:
            return
        mag = min(1.0, math.hypot(act.forward, act.strafe))
        if mag < 0.3:
            return
        T = s.T()
        move = act.yaw + math.atan2(act.strafe, act.forward)        # (strafe +1 = left)
        hit = s.blocked_way(T, me, snap, move)
        fight = s.goal[0] == "fight"
        if hit is None or (hit == "wall" and not fight):
            return
        tries = [(act.yaw + math.atan2(-act.strafe, act.forward), "the other way")] if fight and abs(act.strafe) > 0.3 else []
        tries += [(move + k, "round it") for k in (0.52, -0.52, 1.05, -1.05, 1.57, -1.57)]
        for way, how in tries:
            if s.blocked_way(T, me, snap, way) is None:
                rel = way - act.yaw
                act.forward, act.strafe = mag * math.cos(rel), mag * math.sin(rel)
                if how == "the other way":
                    s.strafe_dir, s.next_strafe_flip = -s.strafe_dir, now + 0.5
                s.note(f"{hit} ahead: {how}")
                return
        if fight:
            act.forward = act.strafe = 0.0
            s.note(f"{hit} ahead: hold")

    def no_wall_jump(s, me, act):
        """No jump into a wall at eye height: no jump clears it. A planned jump, a jump standing still and a lip the level
        lacks (the unstuck wiggle's: no wall in the data there) are left alone."""
        if not act.jump or s.planned_jump or me.vehicle != -1 or abs(act.forward) + abs(act.strafe) < 0.3:
            return
        move = act.yaw + math.atan2(act.strafe, act.forward)
        if s.T().clearance(s.feet(me), move, 0.8, height=EYE, waist=False) < 0.6:
            act.jump = False
            s.note("wall ahead: no jump")

    # A pilot pushing on AIR_STALL[0] s without getting AIR_STALL[1] u is stuck: AIR_BACK s backing off and climbing,
    # AIR_OUT s on toward the most open air, and the spot it pushed at stays blocked AIR_BLOCK_S s (AIR_BLOCK_R round)
    # for its probe ("dumb ass banshee stuck again"; live, a Banshee sat 86 s at Sidewinder's red base and another 165 s
    # under the middle arch at full throttle: drive's old recovery backed off twice a spot, then pushed on at the same
    # wall for good)
    AIR_STALL, AIR_BACK, AIR_OUT, AIR_BLOCK_S, AIR_BLOCK_R = (1.5, 0.4), 0.8, 1.6, 30.0, 2.5

    def air_open(s, T, p, yaw, floor, avoid, need, skip=None):
        """The heading nearest yaw (30-180 degrees off, one side then the other) with `need` u of clear air at the
        craft's height (Terrain.fly_clear), else the clearest one if it beats yaw's by 1 u; None if nothing does.
        skip: a heading not to take again (within 60 degrees of it: the wall it was stuck on)."""
        far = max(12.0, need)                    # (a Sabre's need is 60 u: its 3 s at speed)
        here, best = T.fly_clear(p, yaw, far, floor=floor, avoid=avoid), None
        for off in (0.5, -0.5, 1.0, -1.0, 1.6, -1.6, 2.3, -2.3, math.pi):
            w = yaw + off
            if skip is not None and abs(wrap(w - skip)) < 1.05:
                continue
            c = T.fly_clear(p, w, far, floor=floor, avoid=avoid)
            if c >= need:
                return w
            if best is None or c > best[0]:
                best = (c, w)
        return best[1] if best is not None and (best[0] > here + 1.0 or skip is not None) else None

    def air_guard(s, me, act, snap):
        """Every flyer's driver (Banshee, Hornet, Falcon, any "air" vehicle): a short-sighted LiDAR ahead every tick and a
        stall detector. The probe (Terrain.fly_clear along where it flies, 4-12 u by its speed, at its height, 0.6 u
        under and 0.8 u over): a hit ahead climbs over it where the air above is open, else turns to the nearest open
        heading, else backs off. Stuck (AIR_STALL): backs off climbing, turns to open air, and the spot stays blocked to
        its probe. Then the old rule: at speed it keeps 2 u over the highest floor within 6 u of it and of the point 6 u
        on (its height came from the floor right under it, the ground 30 u down beside the spire's top, and its attack
        passes went in 3-6 u under the top's level): pull up and ease off. Setting down at its destination (drive's
        s.landing: the guard held a Banshee 1.5-6 u over the core's top, never low enough to get out, 200 s, live match
        220515): the probe looks only as far as the spot, at its height and over it, and the floors' rule is off."""
        v = next((x for x in snap.vehicles if x["handle"] == me.vehicle), None)
        sp = vehicles.spec(v["kind"]) if v else None
        now = getattr(s, "now", 0.0)
        if (not sp or sp["move"] != "air" or getattr(s, "seat", "driver") != "driver"
                or now < getattr(s, "exit_until", 0.0) or not math.isfinite(me.pos.x + me.pos.y + me.pos.z)):
            return
        T = s.T()
        vtol, landing = sp.get("vtol"), getattr(s, "landing", False)
        floor = me.pos.z - T.height_over(me.pos.x, me.pos.y, me.pos.z)
        s.air_blocks = [b for b in getattr(s, "air_blocks", ()) if b[2] > now]
        avoid = [(b[0], s.AIR_BLOCK_R) for b in s.air_blocks] + T.oob_avoid()   # (and out of bounds: brain/oob.py)

        def climb():
            act.pitch = max(act.pitch, 0.45)
            if vtol:
                act.vertical = 1.0
        up_ok = lambda yaw: T.fly_clear(me.pos, yaw, 1.5, dzs=(1.0, 2.0, 3.0), floor=me.pos.z - 0.5, start=0.0) >= 1.5
        kind = vehicles.kind_of(v["kind"])
        cl = vehicles.clearance(kind)
        # (seconds since it got in, or since it last sat waiting for crew: hold_for_crew)
        t_in = now - max(getattr(s, "seat_since", now), getattr(s, "air_held", -1e9))
        style, lift = vehicles.TAKE_OFF.get(kind, ("forward", 0.5))
        if not landing and style == "up" and me.pos.z - floor < lift and t_in < 12.0:
            # the player's Falcon take-off: straight up first, then off
            # (vehicles.TAKE_OFF)
            act.forward = act.strafe = 0.0
            act.vertical = 1.0
            s.note("air: taking off, straight up")
            return
        boarded = (me.vehicle, getattr(s, "seat_since", None))
        launching = getattr(s, "air_launch", (None,))[0] == boarded
        if not landing and style == "forward" and t_in < vehicles.TAKE_OFF_S and (me.pos.z - floor < 1.5 or launching):
            # (from the ground only, held through its first seconds: one already aloft is no take-off)
            # it launches by itself: the nose up at once, and its way out the open air (vehicles.TAKE_OFF)
            act.pitch = max(act.pitch, lift)
            launch = getattr(s, "air_launch", None)
            if launch is None or launch[0] != boarded:
                need = max(12.0, cl["ttc"] * sp["speed"])
                w = act.yaw if T.fly_clear(me.pos, act.yaw, need, floor=floor, avoid=avoid) >= need else \
                    s.air_open(T, me.pos, act.yaw, floor, avoid, need)
                launch = s.air_launch = (boarded, act.yaw if w is None else w)
            act.yaw = launch[1]
            s.note("air: taking off toward open air")
        rec = getattr(s, "air_rec", None)
        if rec is None or now >= rec[1]:
            # (aloft only: one sat on the ground is drive's to get out of after 4 s, as before; just boarded, a Falcon
            # spools up 2 s before it climbs: live, tools/falcon_probe.py)
            push = act.forward > 0.5 and not landing and now - getattr(s, "seat_since", now) > 3.0 and me.pos.z - floor > 1.0
            st = getattr(s, "air_still", None)
            if not push or st is None or dist(me.pos, st[1]) > s.AIR_STALL[1]:
                s.air_still = (now, me.pos) if push else None
            elif now - st[0] > s.AIR_STALL[0]:
                into = act.yaw
                spot = me.pos + Vec(math.cos(into), math.sin(into), 0.0) * 1.5
                s.air_blocks.append((spot, into, now + s.AIR_BLOCK_S))
                avoid.append((spot, s.AIR_BLOCK_R))
                out = s.air_open(T, me.pos, into, floor, avoid, 6.0, skip=into)
                s.air_rec = rec = (now + s.AIR_BACK, now + s.AIR_BACK + s.AIR_OUT, into if out is None else out, into)
                s.air_still = None
        if rec is not None and now < rec[1]:
            # (a Banshee backed off 0.9 u in a second on full reverse, live Sidewinder at 33 s: it does back up)
            back = now < rec[0]
            act.yaw, act.forward, act.strafe, act.fire = (rec[3], -1.0, 0.0, False) if back else (rec[2], 0.8, 0.0, False)
            if up_ok(act.yaw):
                climb()
            s.note("air: stuck, backing off" if back else "air: stuck, out to open air")
            return
        if abs(act.forward) + abs(act.strafe) >= 0.3:
            move = act.yaw + math.atan2(act.strafe, act.forward) if vtol else act.yaw
            # as far ahead as the player keeps open at this speed (vehicles.CLEARANCE ttc: 2.5 s in a Banshee, 3 s in
            # a Sabre; the old 12 u at most was 0.6 s of a Sabre's flight)
            look = max(4.0, min(AIR_LOOK_MAX, 2.0 + cl["ttc"] * me.vel.flat()))
            dest = getattr(s, "air_dest", None)
            if landing and dest is not None:
                # setting down: only what stands between it and the spot (over Spire's core the stand's ring and pillars
                # read solid 0.2-2.4 u round it at the craft's height: a full probe would never let it land)
                look = min(look, (dest - me.pos).flat())
            d = look if look < 0.5 else T.fly_clear(me.pos, move, look, dzs=(0.0, 0.8) if landing else (-0.6, 0.0, 0.8),
                                                    floor=floor, avoid=avoid)
            if d < look:
                # throttle eased only within a second of contact (or 3 u): replayed over tonight's live pilots the probe
                # fired on 41% (Sidewinder) and 16% (Spire) of their fast free-flight rows, 35-46% of those 1+ s out
                # (logs/tmp/airprobe_replay.py); a person flying low climbs and swerves at speed
                ease = 0.3 if d < 3.0 else 0.6 if d < me.vel.flat() else 1.0
                # (not over the map's roof: climbing over the spire top's crown into its soft kill, a replayed Falcon
                # pressed 20 s at it held under the roof, never turning: air_roof_guard)
                if (up_ok(move) and T.fly_clear(me.pos + Vec(0, 0, 3.0), move, look, floor=floor, avoid=avoid) > d + 1.0
                        and me.pos.z + 3.0 <= T.air_roof(me.pos.x, me.pos.y) - AIR_ROOF_M):
                    climb()
                    act.forward, act.strafe = max(-ease, min(ease, act.forward)), act.strafe * 0.5
                    s.note("air: wall ahead, climbing over")
                else:
                    # the turn it took is held a second while it stays open (the goal's heading every tick flipped it
                    # east-west at Sidewinder's red base in tools/test_air_stuck.py: glitching)
                    turn = getattr(s, "air_turn", None)
                    need = min(look, d + 3.0)
                    w = turn[0] if turn is not None and now < turn[1] and \
                        T.fly_clear(me.pos, turn[0], need, floor=floor, avoid=avoid) >= need else \
                        s.air_open(T, me.pos, move, floor, avoid, need)
                    if w is not None and (turn is None or w != turn[0]):
                        s.air_turn = (w, now + 1.0)
                    if w is None:
                        act.forward, act.strafe = -1.0, 0.0
                        s.note("air: wall ahead, backing off")
                    else:
                        if vtol:                    # (a Falcon keeps its facing for its gunners: the stick turns)
                            m = math.hypot(act.forward, act.strafe) * min(1.0, ease + 0.2)
                            act.forward, act.strafe = m * math.cos(w - act.yaw), m * math.sin(w - act.yaw)
                        else:
                            act.yaw, act.forward = w, min(act.forward, ease + 0.2)
                        s.note("air: wall ahead, turning to open air")
        if landing:
            return
        # (the height rules below hold when slow too: under 3 u/s they were skipped and a replayed H2A Hornet crept
        # 0.5 u over Bloodline's floor for 5 s, tools/air_replay.py; the side push needs its heading from its speed)
        slow = me.vel.flat() < 3.0
        # its room at the sides (vehicles.CLEARANCE near, the player's: a Banshee 8.4 u, a Falcon 4.6 u, a Sabre 25 u
        # off anything 9 times in 10): the nearest thing beside it inside that pushes it off, the more the nearer
        head = math.atan2(me.vel.y, me.vel.x)
        dmin, off = min((T.fly_clear(me.pos, head + o, cl["near"], floor=floor, avoid=avoid), o) for o in AIR_SIDES)
        diving = (getattr(s, "att", None) or [None])[0] == "run"
        if not slow and dmin < cl["near"] * (0.5 if diving else 1.0):   # (on a run only what is close: it swung the nose off him)
            push, away = 1.0 - dmin / cl["near"], head + off + math.pi
            if vtol:
                act.forward = max(-1.0, min(1.0, act.forward + 0.6 * push * math.cos(away - act.yaw)))
                act.strafe = max(-1.0, min(1.0, act.strafe + 0.6 * push * math.sin(away - act.yaw)))
            else:
                act.yaw += max(-0.6, min(0.6, wrap(away - act.yaw))) * 0.5 * push
            s.note("air: too near something, easing off")
        if (getattr(s, "att", None) or [None])[0] in ("run", "pass"):
            # an attack run (air_pass): only the ground on its own path counts, AIR_PULL_S along the nose at its speed
            # (a dive goes down to him; the travel rule below held it 6.8 u over every roof near its line)
            spd, cp = max(me.vel.length(), 4.0), math.cos(act.pitch)
            for k in range(1, int(AIR_PULL_S / 0.2) + 1):
                f = k * 0.2 * spd
                p = Vec(me.pos.x + math.cos(act.yaw) * cp * f, me.pos.y + math.sin(act.yaw) * cp * f,
                        me.pos.z + math.sin(act.pitch) * f)
                if T.height_over(p.x, p.y, p.z) < AIR_RUN_FLOOR:
                    act.pitch = max(act.pitch, 0.35)
                    run = getattr(s, "air_run", None)
                    if run is not None and run[0] == "in":
                        run[:] = ["out", run[1], now, act.yaw]   # the run ends: on, climbing
                    s.note("air: the ground on the run, pulling out")
                    break
            return
        # a face on its way within AIR_LOOK_MAX (a wall up to a deck over it: the spire under its top) is climbed to the
        # deck's height and its over before it gets there; a VTOL, which climbs AIR_VTOL_CLIMB u/s whatever its speed,
        # slows so that it makes it. Live 20261105-201917 at 310.5 s the Falcon to the core went level into the spire's
        # side at 13.7 u/s, 9 u under its top (060020, 151837 the same spot): the probe and the floor rule each saw the
        # face a second out, too late at 3 u/s up. (A deck with open air under it on its line is a ceiling: no face.)
        # VTOLs travelling only: a Banshee climbs on its nose at its speed (Spire's replayed Banshee: no strike on the
        # side either way), and its pocket escapes are the stall recovery's (test_air_stuck: out in 8.6 s with this on)
        way = head
        face = T.fly_clear(me.pos, way, AIR_LOOK_MAX, floor=floor, avoid=avoid) if vtol and not slow else AIR_LOOK_MAX
        if face < AIR_LOOK_MAX:
            # (only decks it can fly over under the map's roof: the spire top's middle block, 3 u under Spire's safe box
            # top, is a wall the probe's rule takes it round; the ring deck round it is the top to climb to)
            fx, fy = me.pos.x + math.cos(way) * (face + 1.5), me.pos.y + math.sin(way) * (face + 1.5)
            deck = T.top_near(fx, fy, 2.0, zmax=T.air_roof(fx, fy) - AIR_ROOF_M - cl["over"] + 1.0)
            need = deck + cl["over"] - me.pos.z if math.isfinite(deck) and deck > me.pos.z else 0.0
            if need > 0.0:
                climb()
                if vtol:
                    ok = face * AIR_VTOL_CLIMB / (need + 2.0)   # the speed its climb makes the deck at (2 u spare: it slows late)
                    if me.vel.flat() > ok:
                        k = max(0.25, ok / max(me.vel.flat(), 1.0))
                        act.forward, act.strafe = act.forward * k, act.strafe * k
                s.note("air: a face ahead, climbing to its top first")
        # over every floor it will pass in its look ahead by the player's height (CLEARANCE over: 6.8 u in a Banshee,
        # 4.7 in a Falcon, 18.5 in a Sabre; was 2 u over the floors within 6 u, here and 6 u on)
        reach = min(AIR_LOOK_MAX, max(6.0, cl["ttc"] * me.vel.flat()))
        # ... on its own line (2 u of it, not 6: every roof beside the line held it), and the throttle eased only when it
        # is about to touch one. Replayed (Shrine Banshee, tools/air_replay.py) the old 6 u rule fired on 40% of its
        # ticks, half throttle each time
        # (floors up to AIR_CEIL over it only: one higher is a ceiling over it, the fly_clear probe's. Exile's crashed
        # ship 5 u over a replayed Banshee read as a floor to climb over: it pulled up into it on 70% of its ticks)
        # (where it is, only the floor under it: a slope 2 u beside it, just over it, held a replayed Exile Banshee
        # climbing along the slope at half throttle for 20 s; what is beside it is the side push's)
        # (but a deck high over it with a wall under it on its line is no ceiling: the face it would fly into. Spire's
        # top read as a ceiling and every Falcon to the core flew level into the spire's side at -31, 9 u under it,
        # live 20261105-201917 at 310.5 s (and 060020, 151837); 21316aa's zmax. Its line at its height clear only to
        # short of the point: that point's floors count at any height. Nor one under where it is going (air_dest): the
        # spire top's deck overhangs its side, open under it at the craft's height, and a replayed Falcon to the core
        # under it struck its rim with its body)
        line = T.fly_clear(me.pos, act.yaw, reach + 2.0, floor=floor, avoid=avoid)
        dest = getattr(s, "air_dest", None)
        cap = max(me.pos.z + AIR_CEIL, dest.z + 1.0 if dest is not None else -math.inf)
        top = max([me.pos.z - T.height_over(me.pos.x, me.pos.y, me.pos.z)] +
                  [T.top_near(me.pos.x + math.cos(act.yaw) * reach * f, me.pos.y + math.sin(act.yaw) * reach * f, 2.0,
                              zmax=None if line < reach * f + 2.0 else cap) for f in (1 / 3, 2 / 3, 1.0)])
        if me.pos.z < top + (1.0 if slow else cl["over"]):   # (slow: a Falcon hovers low for its gunners)
            act.pitch = max(act.pitch, 0.35)
            if me.pos.z < top + 1.0:
                act.forward = min(act.forward, 0.5)
            if sp.get("vtol"):
                act.vertical = 1.0
            s.note("air: a structure by us, pull up")

    def air_roof_guard(s, me, act, snap):
        """The last word on an aircraft's height: never over the map's roof (Terrain.air_roof: the safe zone's top, a
        kill volume's bottom) less AIR_ROOF_M. Over it: nose down, a VTOL sinks; within 1.5 u of it: no climb (a VTOL
        holds). After air_guard, whose climbs (a wall ahead, a structure by us) took Spire's Falcons over the safe box
        into the soft kill."""
        v = next((x for x in snap.vehicles if x["handle"] == me.vehicle), None)
        sp = vehicles.spec(v["kind"]) if v else None
        if not sp or sp["move"] != "air" or getattr(s, "seat", "driver") != "driver" or not hasattr(s.T(), "air_roof"):
            return
        lim = s.T().air_roof(me.pos.x, me.pos.y) - AIR_ROOF_M
        if me.pos.z > lim:
            act.pitch = min(act.pitch, -0.3)
            if sp.get("vtol"):
                act.vertical = -1.0
            s.note("air: the map's roof over us, down")
        elif me.pos.z > lim - 1.5:
            act.pitch = min(act.pitch, 0.0)
            if sp.get("vtol"):
                act.vertical = min(getattr(act, "vertical", 0.5), 0.5)

    LINE_FRESH = 0.0         # s: only this tick's line counts (Perception looks at every enemy in view every tick)

    def line_clear(s, now, me, tid, snap):
        """A clear line from our eye to enemy tid now: Perception's (Terrain.los over the level and its placed objects)
        within LINE_FRESH s, and no vehicle across it (the level data has none: a Warthog parked between takes the
        rounds). No shot without one."""
        if now - s.per.clear_at.get(tid, -9.0) > s.LINE_FRESH:
            return False
        e = snap.player(tid) if isinstance(tid, int) else None
        if e is None:
            return True
        # sight passes windows and fences, a round only fences and glass that breaks (Terrain.sees / shot_los,
        # maps/<map>/wall_kinds.json): a man seen behind bullet-proof glass gets no shot (s.glassed: since when). Only a
        # window between: where the level data has a solid wall too, Perception's line stands (the man shooting us, a
        # test's world), as before the wall kinds
        glassed = s.__dict__.setdefault("glassed", {})
        T = s.T() if s.map is not None else None
        if getattr(T, "see_clear", None) is not None and T.sees(me.pos, e.pos) and not T.shot_los(me.pos, e.pos):
            glassed.setdefault(tid, now)
            return False
        glassed.pop(tid, None)
        a, b = me.pos + Vec(0, 0, EYE), e.pos + Vec(0, 0, CROUCH_CHEST if crouched(e) else CHEST)
        ab = b - a
        L2 = ab.x * ab.x + ab.y * ab.y + ab.z * ab.z
        if L2 < 4.0:
            return True                                       # (within 2 u nothing fits between)
        for v in getattr(snap, "vehicles", ()) or ():
            if v.get("handle") in (me.vehicle, e.vehicle):
                continue
            c = vpos(v) + Vec(0, 0, 0.4)
            ac = c - a
            u = (ac.x * ab.x + ac.y * ab.y + ac.z * ab.z) / L2
            if 0.05 < u < 0.95:
                q = c - (a + ab * u)
                if q.x * q.x + q.y * q.y + q.z * q.z < 0.81:   # (within 0.9 u of its middle)
                    return False
        return True

    def fire_gate(s, now, me, act, snap):
        """The last word on the trigger, whoever pressed it (a hand gun, a seat's gun, a vehicle's): an enemy in front of
        the aim with a clear line to him (line_clear), or no shot. A charge being held stays held (letting go fires it);
        grenades are not shots (a man behind cover gets one: the flush in fight)."""
        if not act.fire:
            return
        if me.weapon in CHARGE and getattr(s, "charge_since", None) is not None and me.vehicle == -1:
            return
        for e in snap.players:
            if e.team == me.team or not e.alive or e.id == me.id:
                continue
            d = dist(me.pos, e.pos)
            off = abs(wrap(math.atan2(e.pos.y - me.pos.y, e.pos.x - me.pos.x) - act.yaw))
            if (d < 2.0 or off < max(0.15, math.atan2(1.2, d))) and s.line_clear(now, me, e.id, snap):
                return
        act.fire = s.shot_now = False
        s.note("no clear line: hold fire")

    def firefight_hold(s, now, me, snap, act):
        """Firefight: no enemy in sight (an AI unit or a bot / person on the other team the bot sees and has recognised),
        nothing is spent: no round, no grenade, no equipment. A shot at a wall, a grenade for a flush or an objective, or
        an armor ability pressed for nothing is stock that is not there for the next wave. Kept: a charge being held
        (letting go fires it, as fire_gate), the sprint ability (it is not used up), and any equipment pressed while
        something unseen is shooting the bot (survival, not waste), and melee, reloads and weapon swaps (no ammo goes)."""
        if snap.mode != "firefight" or me.vehicle not in (None, -1) or s.per.visible_enemies():
            return
        held_charge = me.weapon in CHARGE and getattr(s, "charge_since", None) is not None
        if not held_charge:
            act.fire = act.fire2 = False
            s.shot_now = False
        if act.grenade:
            act.grenade = False
            s.grenade_ready_at = min(s.grenade_ready_at, now)       # (the throw did not happen: not on cooldown for it)
        press = getattr(s.kit, "press", None)
        under_fire = now - getattr(s, "hit_at", -9.0) < 1.5
        if act.equipment and not under_fire and not (press is not None and press[0] == "sprint"):
            act.equipment = False
            s.notes.append("firefight: nobody in sight, equipment kept")

    BOXED_S, BOXED_R = 12.0, 2.0   # s walking without leaving 1.2 u of a spot, walls on every side within BOXED_R u

    def boxed_in(s, now, me, act):
        """Walls on every side within BOXED_R (the 'crosshair distance' in 16 directions) and BOXED_S s walking without
        leaving 1.2 u of one spot: a sealed pocket, no way out on foot. A person throws a grenade at his feet (the
        respawn is the way out); again every 4 s while he lives. Live Boneyard 20261105-044042: the game respawned two
        defenders INSIDE a closed 2.2 x 2.5 u crate at (254.7, 88.4, -4.0) (the BSP's walls all round, its lid 2 u up),
        217 s and 22 s there, never out alive (round end). True when it threw."""
        if me.vehicle != -1:
            return False
        if now < getattr(s, "box_hold", 0.0):           # the throw under way: the button held, the view where it went
            act.forward = act.strafe = 0.0
            act.yaw, act.pitch, act.grenade = s.box_aim[0], s.box_aim[1], True
            return True
        if getattr(s, "box_pos", None) is None or dist(me.pos, s.box_pos) > 1.2:
            s.box_pos, s.box_since = me.pos, now
            return False
        if now - s.box_since < s.BOXED_S or now < getattr(s, "box_nade_t", -9.0) + 4.0:
            return False
        T = getattr(s, "_terrain", None)
        if T is None:
            from .maps import terrain
            T = s._terrain = terrain(s.map)
        cl = [(T.clearance(me.pos, k * math.pi / 8, far=3.0), k * math.pi / 8) for k in range(16)]
        best_clear, best_yaw = max(cl)
        if best_clear >= s.BOXED_R:
            # open somewhere: not sealed (boxed_in is for sealed pockets), so don't throw grenades at the cave
            # wall — steer the bot out through the open direction instead, for BOXED_S seconds, then re-evaluate
            s.box_since = now
            if not getattr(s, "cave_steer_until", 0.0) or now > s.cave_steer_until:
                s.cave_steer_yaw = best_yaw
                s.cave_steer_until = now + s.BOXED_S
                s.note("cave wedge: steering out through open side")
            # steer: override yaw toward the open direction and walk forward
            act.yaw = s.cave_steer_yaw
            act.forward = 1.0
            act.strafe = 0.0
            act.jump = True                    # hop to clear small lips/edges in the cave mouth
            return False                       # still not "boxed in" — don't suppress firing, just redirect
        # each try another throw, held 0.3 s: at the feet, at the nearest wall level (it bounces back), up at the lid.
        # Live B20 (Spartan 2 respawned into the crate at 124.1 s, there 190+ s): 21 one-tick presses aimed at the floor
        # (sent as pitch -1.13), not one frag went off (shield 1.0 all along, carnage: 0 suicides)
        k = getattr(s, "box_tries", 0)
        s.box_tries = k + 1
        wall = min(cl)[1]
        s.box_aim = ((act.yaw, -1.45), (wall, 0.0), (act.yaw, 0.9))[k % 3]
        s.box_hold, s.box_nade_t = now + 0.3, now
        act.forward = act.strafe = 0.0
        act.yaw, act.pitch, act.grenade = s.box_aim[0], s.box_aim[1], True
        s.note(("boxed in: a grenade at my feet", "boxed in: a grenade off the wall", "boxed in: a grenade at the lid")[k % 3])
        return True

    def unstuck(s, now, me, act, snap=None):
        """If we keep trying to move but go nowhere, wiggle out in a random direction (with a
        jump) for a moment. Every shipped bot needs this; a stuck bot reads as a bot. Stuck with
        nobody near: something solid the level data lacks is ahead; paths learn to go around."""
        if s.TRAP_WALL and me.vehicle == -1:
            T_ = getattr(s, "_terrain", None)
            if T_ is not None and T_.__dict__.get("trapped") and T_.trap_walk(s.feet(me), me.vel.flat()):
                s.save_traps()        # a bot ran through a marked wall: a strike on it (two drop it); counts are kept
        moving = abs(act.forward) + abs(act.strafe) > 0.3
        # (a lift ride stands still in its column on purpose; a zone shuffle keeps within 0.9 u of the middle, inside the
        # 0.6 u-for-1.2-s test below: it wiggled an Easy holder 1.4 u out of Boneyard's box, test_fluidity_fixes 8d)
        if getattr(s, "lift_ride", None) or now < getattr(s, "shuffle_until", 0.0):
            s.stuck_since, s.stuck_pos = None, None
            return
        if not moving:
            # normally no reason to look for a wedge (it may simply have arrived) - except right beside a piece
            # forge.py already confirmed has no collision at all: there, "not trying to move" is as likely to mean
            # "arrived" was wrongly true while actually wedged in it, and nothing will ever ask it to move again
            from . import forge
            if not forge.missing_near(me.pos):
                s.stuck_since, s.stuck_pos = None, None
                return
        if now < getattr(s, "final_step_until", 0.0) and getattr(s, "grab_point", None) is not None:
            s.stuck_since, s.stuck_pos = None, None          # onto an objective: move_to hops at it itself (random
            return                                           # wiggles turned the bots off Spire's core stand)
        if s.boxed_in(now, me, act):
            return
        s.redirect(now, me, act)
        if getattr(s, "backoff_until", 0.0) > now:      # a man standing in us: step back off him
            rel = s.backoff_dir - act.yaw
            act.forward, act.strafe = math.cos(rel), math.sin(rel)
            return
        if getattr(s, "wiggle_until", 0) > now:
            if getattr(s, "wiggle_dir", None) is not None:   # wedged: out the way it came (world angle)
                rel = s.wiggle_dir - act.yaw
                act.forward, act.strafe = math.cos(rel), math.sin(rel)
            else:
                act.forward, act.strafe = s.wiggle
            # getting unstuck is navigation: every tier jumps
            act.jump = now < getattr(s, "wiggle_jump_until", 0.0) or s.rng.random() < 0.05
            s.note(f"unstuck wiggle (try {getattr(s, 'wedge_n', 1)} at this spot)")
            return
        if getattr(s, "stuck_pos", None) is None or dist(me.pos, s.stuck_pos) > 0.6:
            s.stuck_pos, s.stuck_since = me.pos, now
            s.free_pos = me.pos                # it moved: here it was free
            s.fight_bumps = 0
        elif s.goal[0] == "fight" and now - s.stuck_since > 0.6 and getattr(s, "fight_bumps", 0) < 4:
            # blocked mid-fight (a wall, a railing, a man standing in us): a player strafes the other way
            # or steps back off the man; the random wiggle is for being wedged (the player's Reach game:
            # 282 of 388 stuck episodes on scenery were mid-fight, 109 more were body blocks)
            s.fight_bumps = getattr(s, "fight_bumps", 0) + 1
            # (with a sword or hammer only a teammate: the enemy against us is the man to hit, not to step back from; a
            # sword run pushing into him backed off 0.5 s, owner, issue #3: "pushed back by a bubble")
            blocker = min((p for p in (snap.players if snap else []) if p.id != me.id and p.alive
                           and (p.team == me.team or me.weapon not in LUNGE)
                           and dist(p.pos, me.pos) < 1.3), key=lambda p: dist(p.pos, me.pos), default=None)
            if blocker is not None:
                s.backoff_dir = math.atan2(me.pos.y - blocker.pos.y, me.pos.x - blocker.pos.x)
                s.backoff_until = now + 0.5
                s.note("body block: step back")
            else:
                s.strafe_dir, s.next_strafe_flip = -s.strafe_dir, now + 0.5
                act.strafe = -act.strafe
                s.note("blocked in a fight: strafe the other way")
            s.stuck_pos, s.stuck_since = me.pos, now
        elif now - s.stuck_since > s.STUCK_S:
            # nobody in the way: only a man ahead explains it (teammates bunched beside or behind it, all stuck at
            # the same unmapped crate, kept it from ever learning the spot: Boneyard Invasion, live)
            move = act.yaw + math.atan2(act.strafe, act.forward)
            ahead_of = lambda r, cone: snap is not None and any(
                p.id != me.id and p.alive and dist(p.pos, me.pos) < r
                and abs(wrap(math.atan2(p.pos.y - me.pos.y, p.pos.x - me.pos.x) - move)) < cone for p in snap.players)
            # ... unless wedged at the spot for its fourth try with no body right in front: four red attackers wedged
            # together at Boneyard's 251, 56.5 for 100+ s, none learned it (Invasion round 2, live)
            wedged = getattr(s, "wedge_n", 0) >= 4 and not ahead_of(0.9, 0.6)
            alone = not ahead_of(1.6, 1.2) or wedged
            fight_ok = s.LEARN_IN_FIGHT or s.goal[0] != "fight" or now - s.stuck_since > s.FIGHT_LEARN_S
            if alone and not s.planned_jump and fight_ok:   # strafe in place: no wall
                if getattr(s, "_terrain", None) is None:
                    from .maps import terrain
                    s._terrain = terrain(s.map)
                ahead = me.pos + Vec(math.cos(move), math.sin(move), 0) * 0.5
                wp = getattr(s, "wp", None)
                if wp is not None and wp.z < s.feet(me).z - 0.5 and (wp - me.pos).flat() < 1.5:
                    # stuck stepping down to the nav's next point: the landing is the spot (its cost on the level we
                    # stand on left the drop as cheap as before: Sidewinder's flag rooms, live, 25+ stalls at (25.6,
                    # -30.6, -0.8) and its mirror over a drop through a beam onto a 0.25 u strip at -2.4 no person
                    # walked, four spots learned at -0.8 round it and the nav still through it)
                    ahead = Vec(wp.x, wp.y, wp.z)
                if s._terrain.ground_ok(ahead.x, ahead.y, ahead.z):   # a void ahead: the edge guard's stop
                    from . import forge
                    if s.TRAP_WALL:
                        s.mark_trap(me, ahead, move, snap)   # trapped: a wall from now on, kept for the next matches
                    else:
                        # a piece forge.py already found with no shape at all sits right here: skip the usual multi-bot
                        # vote (learn_stuck's sure=False path) - this is confirmed missing data, not one bot's say-so,
                        # so the very first bot to hit it seals it off instead of every bot re-discovering it alone
                        s._terrain.learn_stuck(ahead, sure=forge.missing_near(ahead), who=s.id)
                if wedged and s.wedge_n == 4:
                    # a lip across the way: the spot and one each side, for sure (one small disk at a time, the
                    # path squeezed past it onto the same lip: three spots there already, Boneyard)
                    px, py = -math.sin(move), math.cos(move)
                    for k in (0.0, 0.55, -0.55):
                        q = ahead + Vec(px * k, py * k, 0)
                        if s._terrain.ground_ok(q.x, q.y, q.z):
                            if s.TRAP_WALL:
                                s._terrain.learn_trap(q)
                            else:
                                s._terrain.learn_stuck(q, sure=True)
                    if s.TRAP_WALL:
                        s.save_traps()
            a = s.rng.uniform(0, 2 * math.pi)
            s.wiggle = (math.cos(a), math.sin(a))
            s.wiggle_until = now + s.rng.uniform(0.5, 1.0)
            # still in the same spot after a wiggle: wedged in scenery the level data lacks (a Lockdown bot
            # jump-shot onto an unmapped ledge and stood 334 s; 15-33 s wedges in most games). Each further
            # try starts with a held jump, every other one back toward where it last moved freely (the way
            # in), from the third try at the spot (two is common and harmless)
            wp = getattr(s, "wedge_pos", None)
            s.wedge_pos, s.wedge_n = (wp, s.wedge_n + 1) if wp is not None and dist(me.pos, wp) < 0.6 else (me.pos, 1)
            free = getattr(s, "free_pos", None)
            s.wiggle_dir, s.wiggle_jump_until = None, 0.0
            if s.wedge_n == 2 or (s.wedge_n > 2 and s.wedge_n % 4 == 0):
                # a lip the level data lacks: jump on along the way (live, Boneyard's Elite spawn nook: the way out at
                # 251, 56.5 needs a jump; random wiggles took 170 s, its spawn was its only "free" spot)
                s.wiggle_jump_until = now + 0.45
                s.wiggle_dir = move + s.rng.uniform(-0.25, 0.25)
            elif s.wedge_n >= 3:
                s.wiggle_jump_until = now + 0.35
                if s.wedge_n % 2 == 1 and free is not None and (free - me.pos).flat() > 0.3:
                    s.wiggle_dir = math.atan2(free.y - me.pos.y, free.x - me.pos.x) + s.rng.uniform(-0.4, 0.4)
            if (s.wiggle_dir is None and not s.planned_jump
                    and (s.wedge_n == 1 or (s.wedge_n >= 3 and s.wedge_n % 2 == 1))):
                # not a blind random turn: back off to the nearest open floor (Terrain.open_step: clear of walls, rock
                # corners and ledge lips, on the way on to the goal), the first try and the odd ones where there is no
                # free spot to go back to. A random wiggle at a lip or a rock corner as often went back into it
                out = s.way_out(me)
                if out is not None:
                    s.wiggle_dir = math.atan2(out.y - me.pos.y, out.x - me.pos.x) + s.rng.uniform(-0.15, 0.15)
                    s.wiggle_until = now + s.rng.uniform(0.7, 1.1)
            s.stuck_pos, s.stuck_since = me.pos, now

    def mark_trap(s, me, ahead, move, snap=None):
        """A bot alone ran into something and stood pushing (unstuck): the spot ahead of it is a wall from now on
        (Terrain.learn_trap: paths go round it while any way round exists, no straight line crosses it), saved for later
        matches. Under an object (a wall at head height with room at the knee ahead, so no wall low down in the data
        either): the body-length on, where it would stand under it, as well. True if a spot was new. Not with a man close
        by (TRAP_CROWD: that is a body block, not a wall); Terrain.learn_trap's own limits and strikes do the rest."""
        if snap is not None and any(p.id != me.id and p.alive and dist(p.pos, me.pos) < s.TRAP_CROWD
                                    for p in snap.players):
            return False
        T = s.T()
        added = T.learn_trap(ahead)
        if T.clearance(s.feet(me), move, 0.8, height=EYE, waist=False) < 0.6:
            added = T.learn_trap(ahead + Vec(math.cos(move), math.sin(move), 0) * 0.35) or added
            if added:
                s.note("trapped under an object: marked as a wall")
        elif added:
            s.note("trapped at a wall: marked")
        s.save_traps()                  # (also when it only confirmed a mark beside it: the count is kept)
        return added

    def trap_path(s):
        """maps/<map>/trapped_walls.json (forge.ROOT is the maps folder)."""
        import os
        import re
        from . import forge
        name = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(s.map["name"])) or "map"
        return os.path.join(forge.ROOT, name, "trapped_walls.json")

    def save_traps(s):
        if s.TRAP_SAVE:
            try:
                s.T().save_traps(s.trap_path())
            except Exception:
                pass                 # (a full disk or a read-only folder must not take a bot's tick down)

    def way_out(s, me):
        """Where a wedged bot backs off to: Terrain.open_step from its feet toward the goal's point, None if it has none."""
        try:
            goal = s.goal[1] if len(s.goal) > 1 and hasattr(s.goal[1], "x") and hasattr(s.goal[1], "y") else None
            return s.T().open_step(s.feet(me), goal)
        except Exception:
            return None

    def edge_guard(s, now, me, act):
        """Never walk, strafe, juke, wiggle or jump off a deadly edge (Halo 2's Lockout and
        Ascension: open edges over the void). On the ground: if the floor ~1 unit along the move
        (2 units for a jump) is a fall to death, stand still and do not jump. In the air (a
        jump shot, a knock): steer back over the last solid ground. Planned gap jumps pass."""
        if getattr(s, "_terrain", None) is None:
            from .maps import terrain
            s._terrain = terrain(s.map)
        T = s._terrain
        if getattr(s, "lift_ride", None):
            return                     # a gravity lift's column has no floor to read: the lift is the floor (ride_lift)
        if s.kit.flying(now):
            return                     # a jetpack flight up to a goal (Kit.fly_to checked the air): "air steer back" pulled
                                       # it back under the ledge it flies onto
        # (the jump window before the final steps' early return: move_to marks a person's take-off as final steps on the
        # tick it jumps, so the window was never set and "air steer back" took over 0.3 s after the nav stopped offering
        # the jump; live Spire game 10)
        if getattr(s, "planned_jump", False):
            # a long drop is a long fall: fly on to the landing all the way down (live Spire: the guard steered the
            # core carriers back under the 37 u drop's take-off onto the ledge there, and the fall killed all of them;
            # people's way runs on 3.5 u and slides down the spire's side). Gravity about 4 u/s^2
            land = getattr(s, "jump_land", None)
            drop = max(0.0, me.pos.z - land.z) if land is not None else 0.0
            s.jump_until = now + 1.2 + math.sqrt(2.0 * drop / 4.0)
        land = getattr(s, "jump_land", None)
        if (now < getattr(s, "jump_until", 0.0) and not getattr(s, "planned_jump", False) and land is not None
                and abs(me.pos.z - land.z) < 0.35 and (me.pos - land).flat() < 1.0
                and T.ground_ok(me.pos.x, me.pos.y, me.pos.z, drop=0.6 + me.pos.z - s.feet(me).z)):
            # landed: on the floor at the landing and the nav offers no jump now. The window held on 1.2 s past the end
            # of a person's walked route (every step of it a planned jump) and steered the bot back to the route's last
            # point as it walked on: live Boneyard B11-B14, the Elites' way up the plank to the -6 walkway at
            # (249.2, 90.6), the stick back and forth every 0.3 s (logs/tmp/invasion_regress/replay_think.py)
            s.jump_until = 0.0
        if now < getattr(s, "jump_until", 0.0):
            over = not T.ground_ok(me.pos.x, me.pos.y, me.pos.z, drop=4.0)
            # committed: on for the landing over the gap and over floor on the way (a slope it touches first); move_to,
            # still offered the jump, would steer back to the take-off. Over the void with a long way down: onto it, not
            # past it (fall_steer)
            land = getattr(s, "jump_land", None)
            if land is not None and over and me.pos.z - land.z > 3.0:
                s.fall_steer(me, land, act)
            elif land is not None and (over or (me.pos - land).flat() > 0.3):
                s.steer(me, land, act, True, arrive=0.05)
            if over:
                act.jump = False                                    # whatever the new goal wants
            return
        if now < getattr(s, "final_step_until", 0.0):
            return                     # the last steps (or a hop) onto an objective whose floor the nav lost (Spire's
                                       # core: 1491 "edge stop"s 2.7 u from it, live)
        # H2A reports the position 0.38 above the floor (tools/ground_offset.py; the others 0): with
        # drop 0.6 bots on solid floor read airborne and strafed off Lockout's edges in fights
        grounded = T.ground_ok(me.pos.x, me.pos.y, me.pos.z, drop=0.6 + me.pos.z - s.feet(me).z)
        # not falling for 0.4 s is floor, in the level data or not (Boneyard, live: two bots stood 418 s and 81 s at z
        # -5.25 off their spawn platform's edge, "air steer back" every tick pushing them at the last mapped ground and
        # cancelling every jump the unstuck wiggle asked for)
        rest = getattr(s, "_eg_rest", None)
        if rest is None or (me.pos - rest[1]).flat() > 0.1 or abs(me.pos.z - rest[1].z) > 0.03:
            s._eg_rest = rest = (now, me.pos)
        grounded = grounded or now - rest[0] > 0.4
        lg = getattr(s, "last_ground", None)
        if lg is not None and me.pos.z < lg.z - 5 and not getattr(s, "fall_learned", False):   # Lockout kills ~6 down
            d = me.pos - lg                # fell off where the level data has floor: learn the edge
            if d.flat() > 0.05:
                T.learn_stuck(lg + Vec(d.x, d.y, 0) * (0.4 / d.flat()), who=s.id)
            s.fall_learned = True
        if grounded:
            s.last_ground, s.fall_learned = me.pos, False
        elif lg is not None and me.pos.z < lg.z - 1.0 and not T.ground_ok(me.pos.x, me.pos.y, lg.z, drop=3.5):
            # a metre under the ledge over a deadly drop there is no way back up onto it: onto a soft landing it can
            # still reach (Spire's heat vents), as a planned drop (live Spire: the core carrier slid off the rim 1.7 u
            # past the vent drop's take-off, braked and steered back at the rim in the air, and fell 36 u to its death
            # with the vent 6.9 u off)
            land = s.soft_landing(T, me)
            if land is not None:
                s.jump_land, s.jump_until = land, now + 1.0 + s.fall_time(me, land)
                s.fall_steer(me, land, act)
                s.note("air: onto the landing below")
                act.jump = False
                return
        moving = abs(act.forward) + abs(act.strafe) >= 0.1
        c, sn = math.cos(act.yaw), math.sin(act.yaw)
        mx, my = c * act.forward - sn * act.strafe, sn * act.forward + c * act.strafe   # strafe + = left
        n = math.hypot(mx, my) or 1.0
        reach = (0.6, 1.1, 1.8) if act.jump else (0.6, 1.1)
        wp = getattr(s, "wp", None)
        to = (wp - me.pos) if wp is not None else None
        # the drop to it from the feet: H2A's body height made flat floor a step down, and the guard below skipped on
        # 20,044 of ~33,000 walking ticks (Zenith CTF)
        drop = s.feet(me).z - wp.z if wp is not None else 0.0
        if (wp is not None and not act.jump and moving and MAX_RISE < drop <= MAX_DROP + 0.3
                and to.flat() < 2.0 and T.ground_ok(wp.x, wp.y, wp.z + 0.3, drop=0.6)
                and (to.flat() < 0.15 or (mx * to.x + my * to.y) / (n * to.flat()) > 0.82)):
            # (moving at it: the command may be slowed by the turn-first rule, which made the guard use its long probes:
            # live, the bot faced 79-109 degrees with its next point at -151) the nav's own step down onto the floor
            # below (a drop it planned, up to MAX_DROP): the probe ahead and the momentum brake overshoot a narrow ledge
            # and read the floor under it as the void. Breakneck King of the Hill: 52% of 4,900 edge stops on two
            # walkway edges over the hill, 2.4 u above a ledge the path drops to; hill holders sat 20 u from it (median)
            # and the hill stood empty 65% of the match
            s.edge_since = None
            s.note("step down")
            return
        if not act.jump and getattr(s, "nav_cmd", None) == (act.forward, act.strafe):
            reach = (0.3,)    # following the path (walkable floor only): a long probe overshoots
                              # the next corner of a narrow walkway into the void and froze bots
        safe = not moving or all(T.ground_ok(me.pos.x + mx / n * k, me.pos.y + my / n * k, me.pos.z) for k in reach)
        # momentum: the probes look along the command, but a bot runs ~2.3 u/s and a strafe jump drifts on
        # after the command stops (14 of 93 Lockout deaths were falls in fights). Where it will be
        # shortly, if that is over a deadly drop: push against the motion, no jump. Man cannons and
        # vehicles (fast) are left alone; on the path a short horizon (corners of narrow walkways)
        last, s._eg_last = getattr(s, "_eg_last", None), (now, me.pos)
        ref_z = s.last_ground.z if getattr(s, "last_ground", None) is not None else me.pos.z
        v, sp = Vec(0, 0, 0), 0.0
        if last is not None and 0 < now - last[0] < 0.5:
            v = (me.pos - last[1]) * (1.0 / (now - last[0]))
            sp = v.flat()
        # (not against the motion its own brake made the tick before: on a ledge with a drop each side the brake's push
        # read as momentum over the other drop, braked back, and so on every tick, the stick never the nav's again:
        # sim Sidewinder CTF seed 3, a bot 41.6 s at the red flag room's (29.5, -31.9), 0.07 u to and fro; the probes
        # along the command below still stop a step over the edge)
        bk = getattr(s, "brake_last", None)
        own = bk is not None and last is not None and bk[0] == last[0] and v.x * bk[1] + v.y * bk[2] > 0.0
        if sp > 0 and now - getattr(s, "runup_t", -9.0) > 0.3 and not own:
            horizon = 0.15 if getattr(s, "nav_cmd", None) == (act.forward, act.strafe) else 0.4
            if 0.8 < sp < 5.0 and not T.ground_ok(me.pos.x + v.x * horizon, me.pos.y + v.y * horizon, ref_z, drop=3.5):
                bx, by = -v.x / sp, -v.y / sp
                act.forward, act.strafe, act.jump = c * bx + sn * by, -sn * bx + c * by, False
                s.note("edge brake")
                s.brake_last = (now, bx, by)
                return
        if safe:
            s.edge_since = None
        if grounded:
            if not safe and s.edge_other_way(T, me, act, reach):
                s.edge_since = None
                s.note("edge: the other way")
            elif not safe:
                act.forward, act.strafe, act.jump = 0.0, 0.0, False
                s.note("edge stop")
                # held at an edge: a person turns away and goes another way, he does not stand there until his plan
                # changes ("edge stop" was the first reason bots stood still: 8-12% of their standing live, up to 44%
                # in Cyberdyne sims). After 0.6 s the spot it walked to (a loiter step, a detour) is dropped and it
                # decides again
                s.edge_since = getattr(s, "edge_since", None) or now
                if now - s.edge_since > 0.6:
                    s.loiter_pt = s.detour_pt = None
                    s.next_decide, s.edge_since = now, None
            elif act.jump and not moving and not T.ground_ok(me.pos.x, me.pos.y, me.pos.z, drop=4.0):
                act.jump = False
            elif act.jump:             # a jump shot or a juke (planned jumps returned above): it lands up to
                # ~2.4 on along the command and the motion; floor there within 1.5 down, or no jump (a "floor"
                # 4 down was Lockout's narrow lower ledges, missed; 8 of 89 deaths were such falls)
                dirs = ([(mx / n, my / n)] if moving else []) + ([(v.x / sp, v.y / sp)] if sp > 0.8 else [])
                far = (0.6, 1.2, 1.8) if now - rest[0] > 0.4 else (0.6, 1.2, 1.8, 2.4)   # (a hop from standing)
                if not all(T.ground_ok(me.pos.x + ux * k, me.pos.y + uy * k, me.pos.z, drop=1.5)
                           for ux, uy in dirs for k in far):
                    act.jump = False
                    s.note("jump cancelled: landing over a drop")
            return
        back = getattr(s, "last_ground", None)
        # floor within 3.5 (MAX_DROP, the nav's ledge drops, + 0.5) below where it left the ground, not where it is:
        # falling, the window slid down onto a far lower floor and the bot stopped steering back (Lockout)
        over_void = not T.ground_ok(me.pos.x, me.pos.y, back.z if back is not None else me.pos.z, drop=3.5)
        if back is not None and (over_void or not safe):
            s.steer(me, back, act, True, arrive=0.05)      # air control: back over solid ground
            s.note("air steer back")
            act.jump = False

    def edge_other_way(s, T, me, act, reach):
        """A move the probes stop at an edge: the same move with the strafe the other way, the strafe alone the other way,
        the forward alone, the strafe alone, whichever first keeps floor under every probe (the guard's own: no step off
        a real ledge). A person at a ledge side-steps the other way, he does not freeze in a fight. Live Ascension:
        "edge stop" was 60% of the bots' standing fight ticks, and at every one of the 3,973 some way was safe (the
        strafe either way at 59%, toward the man at 74%: logs/tmp/edge_alt.py). Fights and pull-backs only, no jump."""
        if act.jump or s.goal[0] not in ("fight", "retreat"):
            return False                              # (a walk keeps its path's own stop and re-plan)
        f, st = act.forward, act.strafe
        c, sn = math.cos(act.yaw), math.sin(act.yaw)
        for nf, ns in ((f, -st), (0.0, -st), (f, 0.0), (0.0, st)):
            if abs(nf) + abs(ns) < 0.1:
                continue
            mx, my = c * nf - sn * ns, sn * nf + c * ns
            n = math.hypot(mx, my) or 1.0
            if all(T.ground_ok(me.pos.x + mx / n * k, me.pos.y + my / n * k, me.pos.z) for k in reach):
                act.forward, act.strafe = nf, ns
                if ns and ns * st < 0 and s.goal[0] == "fight":
                    s.strafe_dir = -s.strafe_dir          # the juke goes on the safe way
                return True
        return False

    @staticmethod
    def fall_time(me, land):
        """Seconds until a faller comes down to land's height (live velocity: me.vel.z; the sim's is 0)."""
        h, vd = max(0.0, me.pos.z - land.z), -me.vel.z         # (vd: its speed down)
        return (-vd + math.sqrt(max(0.0, vd * vd + 2.0 * GRAVITY * h))) / GRAVITY

    def fall_steer(s, me, land, act):
        """Falling onto a landing: steer from where it would come down coasting (its speed over the time left to fall to
        the landing's height) onto it; within 0.3 u it lets the stick go. A full stick at the landing put a jumper over
        Spire's heat vent 20 u up at 2.4 u/s, and he came down 2.3 u past it: 22 of 29 deaths off the top in three live
        matches (-30) came down 1.7-2.6 u off a vent. a skilled player's drop: 1.4-1.8 u/s all the way down, 0.2 u from
        the vent."""
        t = s.fall_time(me, land)
        miss = Vec(land.x - me.pos.x - me.vel.x * t, land.y - me.pos.y - me.vel.y * t, 0.0)
        if miss.flat() < 0.3:
            act.forward = act.strafe = 0.0
        else:
            s.steer(me, me.pos + miss, act, True, arrive=0.0)

    def soft_landing(s, T, me):
        """The nearest soft landing (a drop people showed onto something that breaks the fall: Spire's heat vents,
        maps/<map>/drops.json) over 2 u below it that a faller may reach: at a run's 2.4 u/s in the time left to fall
        there, plus a vent's 1 u; None. (Under its ledge a faller has no way back: a try for one is never worse.)"""
        best = None
        for _, _, k, _ in getattr(T, "hjumps", ()):
            if not k.get("soft"):
                continue
            land = Vec(*k["land"][:3])
            d = (land - me.pos).flat()
            if me.pos.z - land.z > 2.0 and d <= 2.4 * s.fall_time(me, land) + 1.0 and (best is None or d < best[0]):
                best = (d, land)
        return best[1] if best else None

    def segment_ok(s, me, w):
        """Floor all along the straight walk to waypoint w (every 0.3 units)?"""
        if getattr(s, "_terrain", None) is None:
            from .maps import terrain
            s._terrain = terrain(s.map)
        d = w - me.pos
        n = d.flat()
        return all(s._terrain.ground_ok(me.pos.x + d.x * k / n, me.pos.y + d.y * k / n, me.pos.z)
                   for k in [x * 0.3 for x in range(1, int(n / 0.3) + 1)])

    def crouch_spots(s, me, act):
        """Floor people walked crouched (LiDAR): every tier crouches on it and just before it.
        That is navigation (a low gap, a vent), not a fighting trick."""
        if getattr(s, "_terrain", None) is None:
            from .maps import terrain
            s._terrain = terrain(s.map)
        cells = getattr(s._terrain, "crouch_cells", None)
        if not cells:
            return
        from .terrain import crouch_key
        c, sn = math.cos(act.yaw), math.sin(act.yaw)
        ax, ay = me.pos.x + (c * act.forward - sn * act.strafe) * 0.5, me.pos.y + (sn * act.forward + c * act.strafe) * 0.5
        if crouch_key(me.pos.x, me.pos.y, me.pos.z) in cells or crouch_key(ax, ay, me.pos.z) in cells:
            act.crouch = True

    def lift_near(s, x, y, r=2.0):
        """A gravity lift's base within r units of (x, y) (the map's "lift" features), or None."""
        for f in s.map.get("features", []):
            if f.get("kind") == "lift" and math.hypot(f["pos"][0] - x, f["pos"][1] - y) < r:
                return Vec(*f["pos"])
        return None

    def lift_base(s, j, me):
        """The column a jump j (from nav) rides up: a gravity lift's base by its take-off (lift_near), or a launcher's
        whose people's link climbs steeply (10+ u up, 1.5+ times its flat way): Spire's lifts under the spire are filed
        as inv_mancannon launchers; run as a jump at the landing 15 u aside, its riders left the column at once. None:
        no lift."""
        up = j.z - me.pos.z
        if up <= 3.0:
            return None
        base = s.lift_near(j.sx, j.sy)
        if base is not None or up < 10.0 or up < 1.5 * math.hypot(j.x - j.sx, j.y - j.sy):
            return base
        for f in s.map.get("features", []):
            if f.get("kind") == "launcher" and math.hypot(f["pos"][0] - j.sx, f["pos"][1] - j.sy) < 2.0:
                return Vec(*f["pos"])
        return None

    def way_in(s, me, point):
        """Into an Elephant's bay for a goal in it (Sandtrap: each team's flag hangs there and is captured on its floor,
        vehicles.ELEPHANT). The level's nav has no vehicle in it: bots pushed against the hull (about 1,000 unstuck
        wiggles beside one flag) and a carrier stood under his stand 80 s. (where to head, straight): round the hull to
        the rear ramp's foot by the nav, then straight up the ramp and along the bay floor. None: no Elephant's bay in
        it."""
        E = vehicles.ELEPHANT
        for v in getattr(s, "vehicles_now", ()):
            fr = vehicles.frame_of(v) if vehicles.kind_of(v["kind"]) == "elephant" else None
            if fr is None:
                continue
            local, world = fr
            gf, gl, gz = local(point)
            f0, f1, w = E["bay"]
            if not (f0 <= gf <= f1 and abs(gl) <= w and 0.2 < gz < 2.0):
                continue
            bf, bl, bz = local(me.pos)
            (af, al), (tf, tl) = E["ramp"]
            if f0 - 0.3 <= bf <= f1 + 0.3 and abs(bl) <= w + 0.3 and 0.35 < bz < 1.3:
                return point, True                                  # on the bay floor (not its roof): along it
            if af - 0.8 <= bf <= tf + 0.3 and abs(bl - al) <= 0.8:
                return world(tf, tl, 0.5), True                     # in the ramp's lane: up it
            h0, h1, hw = E["hull"]
            side = 1.0 if bl >= 0 else -1.0
            if bf < h0 - 0.2:
                return world(af, al), False                         # behind it: to the ramp's foot
            if abs(bl) >= hw + 0.2:
                return world(h0 - 0.8, side * (hw + 0.8)), False    # beside it: its rear corner on this side
            if bf > h1 + 0.2:
                return world(h1 + 0.8, side * (hw + 0.8)), False    # in front of it: round its front corner
            return world(af, al), False                             # on or under it: off, to the foot
        return None

    def came_through(s, now, me, prev):
        """Map brain INTER-TELE-03: out of a teleporter (from 3 u of a pad to 2 u of its exit between two ticks) with a
        man seen or felt in the last 3 s nearer the pad it went in by: the chaser may follow, so it steps off the
        exit and watches it 3 s (move_to's stop in think, look_point) before it goes on. Easy never plans; a flag
        carrier runs on."""
        if s.p.tier == "novice" or me.has_flag:
            return
        pad = next(((a, b) for a, b in s.map.get("teleporters", ())
                    if (prev - a).flat() < 3.0 and (me.pos - b).flat() < 2.0), None)
        if pad is not None and any(b.source in ("eyes", "hit") and now - b.t < 3.0
                                   and (b.pos - pad[0]).flat() < (b.pos - pad[1]).flat() for b in s.per.beliefs.values()):
            s.tele_watch = (pad[1], now + 3.0, now)
            s.note("teleporter: watching its exit")

    def launches(s):
        """(where one goes in, where he comes out, seconds between) for the map's teleporters (at once) and the rides
        people made (people's links: a man cannon's, flagged ride, or a climb of 3+ u from a gravity lift's base; their
        airtime, about 2 s when not recorded). Worked out once."""
        L = getattr(s, "_launches", None)
        if L is None:
            L = [(a, b, 0.0) for a, b in s.map.get("teleporters", ())]
            for _, _, k, _ in getattr(s.T(), "hjumps", ()):
                to, ld = k.get("take_off"), k.get("land")
                if to and ld and (k.get("ride") or (ld[2] - to[2] > 3.0 and s.lift_near(to[0], to[1]) is not None)):
                    L.append((Vec(*to), Vec(*ld), float(k.get("air_s") or 2.0)))
            s._launches = L
        return L

    def arrival(s, now, me):
        """Map brain INTER-CANNON-05, the landing ambush (and the lift exit of INTER-LIFT-02, the teleporter exit of
        INTER-TELE-01): an enemy known in the last 0.5 s (seen, heard, on radar) on a launch, within 1.2 u of where one
        goes in, whose landing is within 25 u of us and in sight: the bot pre-aims the landing from then until 1.5 s
        after he is due there (s.arrive: where, due, until). Easy never plans. None: nobody on his way here."""
        if s.p.tier == "novice":
            return None
        a = getattr(s, "arrive", None)
        if a is not None and now < a[2]:
            return a[0]
        fresh = [b for k, b in s.per.beliefs.items() if isinstance(k, int) and now - b.t <= 0.5]
        for src, land, t in s.launches() if fresh else ():
            if (any((b.pos - src).flat() < 1.2 and abs(b.pos.z - src.z) < 1.5 for b in fresh)
                    and dist(me.pos, land) < 25.0 and s.T().los(eye(me), land + Vec(0, 0, 0.62))):
                s.arrive = (land, now + t, now + t + 1.5)
                s.note("landing: pre-aiming it")
                return land
        return None

    def tele_watching(s, now):
        """The teleporter exit it watches (came_through); None once the 3 s are up or anyone is seen or heard since."""
        w = getattr(s, "tele_watch", None)
        if w is None or now >= w[1] or any(b.t > w[2] for b in s.per.beliefs.values()):
            return None
        return w[0]

    def exit_held(s, me, j, point):
        """Map brain INTER-LIFT-01, INTER-CANNON-02, MOVE-01/02: never ride a lift, a man cannon or a big drop (jump j,
        from nav) into an enemy known at its exit: seen, heard, on radar or felt in the last 4 s (a callout names a
        zone, not a man: not counted) within 8 m of a lift's top or 10 m of another landing (2.6 / 3.3 units), or with a
        sightline onto it from within 25 u (exit_watched). "round": walk the way round (no more than 2.5 times as long)
        for 10 s while the goal stays put; "wait": there is none, stand at the take-off, 6 s at most, then go; "" take
        it. A hop over a gap commits to nothing and passes; Easy never plans a detour and rides. Nor does an Invasion
        attacker in phase 3: the defenders hold the spire top and the core by design, and its man cannons are the way
        up."""
        if s.p.tier == "novice" or getattr(s, "assaulting", False):
            return ""
        up, xy = j.z - me.pos.z, math.hypot(j.x - j.sx, j.y - j.sy)
        lift = s.lift_base(j, me) is not None
        if not (lift or abs(up) > 2.0 or xy > 6.0):
            return ""
        now, r = getattr(s, "now", 0.0), 2.6 if lift else 3.3
        if not s.exit_watched(now, j, r):
            return ""
        key = (round(j.sx, 1), round(j.sy, 1))
        go = getattr(s, "exit_go", None)
        if go and go[0] == key and now < go[1]:
            return ""                            # waited for it already: go
        T = getattr(s, "_terrain", None)
        if T is None:
            from .maps import terrain
            T = s._terrain = terrain(s.map)
        n = T._node(me.pos)
        walk = float(T._field(point, False, False, me.has_flag)[n])
        way = float(T._field(point, s.p.jump >= 0.5, True, me.has_flag)[n])
        if math.isfinite(walk) and walk <= 2.5 * way + 15.0:
            s.round_until, s.round_goal = now + 10.0, point
            s.note("exit held: the way round")
            return "round"
        if s.held_wait(key):
            s.note("exit held: waiting")
            return "wait"
        return ""

    def exit_watched(s, now, ex, r):
        """Whether an exit (a lift's top, a landing, a teleporter's exit) is held: a man known in the last 4 s (seen,
        heard, on radar or felt; a callout names a zone, not a man: not counted) within r of it and 2.5 u of its height,
        or within 25 u with a line of sight onto it (INTER-LIFT-01: "or holds a sightline onto it")."""
        if s.uncovered(ex, r):
            return True
        T = s.T()
        return any(isinstance(k, int) and now - b.t <= 4.0 and (((b.pos - ex).flat() < r and abs(b.pos.z - ex.z) < 2.5)
                                                                 or (dist(b.pos, ex) < 25.0 and T.los(b.pos, ex)))
                   for k, b in s.per.beliefs.items())

    def exit_cover(s, land):
        """Where a man waiting at a lift's exit would stand: the nearest of LiDAR's cover spots (floor in front of a
        wall, Terrain.cover_spots) 3-10 u off the landing, on about its floor (1.5 u), that sees it. None: no such spot.
        Worked out once per landing."""
        memo = s.__dict__.setdefault("_exit_cover", {})
        key = (round(land.x, 1), round(land.y, 1), round(land.z, 1))
        if key not in memo:
            T, memo[key] = s.T(), None
            cs = getattr(T, "cover_spots", None)
            if cs is not None and len(cs):
                import numpy as np
                d = np.hypot(cs[:, 0] - land.x, cs[:, 1] - land.y)
                near = np.nonzero((d >= 3.0) & (d <= 10.0) & (np.abs(cs[:, 2] - land.z) < 1.5))[0]
                for k in near[np.argsort(d[near])]:
                    q = Vec(float(cs[k][0]), float(cs[k][1]), float(cs[k][2]))
                    if T.los(q, land):
                        memo[key] = q
                        break
        return memo[key]

    def held_wait(s, key):
        """Standing short of a held exit with no way round (key: the exit): True while it waits, 6 s at most; then it
        goes, and goes again for 20 s (exit_held, pad_held)."""
        now = getattr(s, "now", 0.0)
        go = getattr(s, "exit_go", None)
        if go and go[0] == key and now < go[1]:
            return False                         # waited for it already: go
        wait = getattr(s, "exit_wait", None)
        if wait is None or wait[0] != key or now - wait[2] > 1.0:
            wait = (key, now, now)
        s.exit_wait = (key, wait[1], now)
        if now - wait[1] > 6.0:
            s.exit_go = (key, now + 20.0)
            return False
        return True

    def uncovered(s, ex, r):
        """Map brain INTER-LIFT-03 / MOVE-02 ("Lifts are escape routes for carriers and ball holders only when the exit is
        held by a teammate"; "Carriers use them only when the exit is held by a teammate"): carrying the flag, the ball
        or a bomb, an exit (a lift's top, a cannon's landing, a teleporter's exit) with no teammate within r + 1 u of it
        and 2.5 u of its height counts as held: the way round when there is one, else 6 s at the take-off for one to get
        there (exit_held, pad_held). Not the Invasion core's carrier in phase 3 (its man cannons and lifts are the way:
        the player, live Spire, as exit_held)."""
        if not getattr(s, "carrying_now", False) or getattr(s, "assaulting", False):
            return False
        return not any((q - ex).flat() < r + 1.0 and abs(q.z - ex.z) < 2.5 for q in getattr(s, "mates_now", ()))

    def pad_held(s, me, point, **way):
        """Map brain INTER-TELE-01: never step onto a teleporter into an enemy known at its exit (as exit_held: seen,
        heard, on radar or felt in the last 4 s within 10 m, 3.3 u, of it, or with a sightline onto it from within 25 u:
        exit_watched). The way ahead (6 u of the route nav walks, way: its jump / human / safe, looked at 4 times a
        second) ending on a teleporter's pad with the goal elsewhere (the walk down the field stops there: the way on is
        the pad's link): "round", the way round as at a held drop (no more than 2.5 times as long, the teleporters
        costed as walls: Terrain no_tele; move_to walks it 10 s while the goal stays put); none: True, it stands short
        of the pad, 6 s at most (held_wait). Easy never plans."""
        pads = s.map.get("teleporters", ())
        if s.p.tier == "novice" or not pads:
            return False
        now = getattr(s, "now", 0.0)
        c = getattr(s, "_pad_ahead", None)
        if c is None or now >= c[0] or (point - c[2]).flat() > 1.0 or (me.pos - c[3]).flat() > 1.0:
            end = ([me.pos] + s.T().path_ahead(me.pos, point, n=24, **way))[-1]
            ex = next((b for a, b in pads if (end - a).flat() < 1.0 and abs(end.z - a.z) < 2.0 and (point - a).flat() > 2.0),
                      None)
            c = s._pad_ahead = (now + 0.25, ex, point, me.pos)
        ex = c[1]
        if ex is None or not s.exit_watched(now, ex, 3.3):
            return False
        T = s.T()
        n = T._node(me.pos)
        walk = float(T._field(point, **dict(way, no_tele=True))[n])
        if walk < T.BARRIER_COST and walk <= 2.5 * float(T._field(point, **way)[n]) + 15.0:
            s.tele_round = (now + 10.0, point)
            s.note("teleporter exit held: the way round")
            return "round"
        if s.held_wait((round(ex.x, 1), round(ex.y, 1))):
            s.note("teleporter exit held: waiting")
            return True
        return False

    def carrier_take_off(s, start, land):
        """A carrier's take-off for a jump: up to 0.6 u (and no more than 30% of the way) nearer the landing than the
        person's, as far as the floor goes on (its jump peaked 0.3 u up, the delivery platform's lip 0.33 u over the
        take-off, and it landed short on the lip below it again and again)."""
        d = Vec(land.x - start.x, land.y - start.y, 0.0)
        n = d.flat()
        if n < 0.5:
            return start
        T = s.T()
        for k in (0.6, 0.4, 0.2):
            p = start + d * (min(k, 0.3 * n) / n)
            if T.ground_ok(p.x, p.y, start.z + 0.4, drop=1.0):     # (floor under it: not out over a gap)
                return Vec(p.x, p.y, start.z)
        return start

    def lineup_point(s, me, j):
        """Where a carrier lines up for a person's running jump j: on the line of his run (run-up to take-off), 1.2 u
        behind the run-up (0.8, 0.5 where the floor ends sooner), on floor and in sight of the run-up. None: no room
        behind it (straight at the run-up, as everyone else goes)."""
        lane = Vec(j.sx - j.rx, j.sy - j.ry, 0.0)
        n = lane.flat()
        if n < 0.05:
            return None
        T = s.T()
        run = Vec(j.rx, j.ry, me.pos.z)
        for d in (1.2, 0.8, 0.5):
            p = run - lane * (d / n)
            if T.ground_ok(p.x, p.y, me.pos.z + 0.4, drop=1.0) and T.los(p + Vec(0, 0, 0.5), run + Vec(0, 0, 0.5)):
                return Vec(p.x, p.y, me.pos.z)
        return None

    def judge_jump(s, me):
        """A planned jump's outcome once it is down (1.5 s on, or the long fall's window): more than 1 u from its landing
        on the flat or 0.4 u under it is short. Short twice: this bot leaves that jump (by its take-off) for 60 s and
        goes another way (jump_blocked); jumps were judged only in LiDAR mode, so a carrier tried one for good."""
        t = getattr(s, "jump_try", None)
        now = getattr(s, "now", 0.0)
        if t is None or now < max(t[2] + 1.5, getattr(s, "jump_until", 0.0) + 0.3):
            return
        key, land, _ = t
        s.jump_try = None
        if (me.pos - land).flat() <= 1.0 and me.pos.z > land.z - 0.4:
            return
        fails = s.__dict__.setdefault("jump_fails", {})
        n = fails.get(key, (0, 0.0))[0] + 1
        fails[key] = (n, now + 60.0)
        s.note(f"jump: short of its landing ({n})")

    def jump_blocked(s, nxt):
        """Whether a jump (a JumpVec, by its take-off) fell short twice for this bot in the last 60 s (judge_jump), or a
        people's route (a RouteVec, by its first point) ended short of its end twice (judge_route)."""
        at = (nxt.path[0][0], nxt.path[0][1]) if hasattr(nxt, "path") else (nxt.sx, nxt.sy)
        f = getattr(s, "jump_fails", {}).get((round(at[0], 1), round(at[1], 1)))
        return f is not None and f[0] >= 2 and getattr(s, "now", 0.0) < f[1]

    def judge_route(s, me):
        """A people's route just left (done, too slow, or pushed off): over 1 u from its end on the flat or 0.8 u under
        it is short, counted as a short jump is (judge_jump). Live Boneyard 20261105-031842: attackers walked the ramp
        route under phase 1's territories along the floor under it, z -10 for an end at -6, again and again."""
        key, end = getattr(s, "route_key", None), getattr(s, "route_end", None)
        s.route_key = s.route_end = None
        if key is None or ((me.pos - end).flat() <= 1.0 and me.pos.z > end.z - 0.8):
            return
        fails = s.__dict__.setdefault("jump_fails", {})
        n = fails.get(key, (0, 0.0))[0] + 1
        fails[key] = (n, getattr(s, "now", 0.0) + 60.0)
        s.note(f"route: short of its end ({n})")

    def off_lost_floor(s, me, nxt):
        """Standing on a floor the nav lacks (a ledge or piece its cell lost to the floors under it): the nav's next point
        is right under the bot, so the walk stops dead (live Boneyard 20261105-015943: an attacker stood 110 s at
        (279.45, 110.52, -5.87), the cell's floors -9.89 / -8.48, its next point there at -8.48). Where to step off: the
        nearest spot round it with a nav floor at its own height; None when the next point is not under it."""
        if hasattr(nxt, "sx") or (nxt - me.pos).flat() > 0.3 or me.pos.z - nxt.z < 1.2:
            return None
        T = s.T()
        j, i = T._cell(me.pos)
        if any(abs(float(z) - me.pos.z) < 0.6 for z in T.lz[:, j, i] if z == z):
            # its own floor is in the nav: the point under it is a drop the nav planned, not a lost floor (live
            # Boneyard 20261105-064324, the issue #4 stairs (256.9, 106.1, -5.0): "step off" 368 times against "step
            # down" 199 over the tread's edge to the -6.15 floor, 31.9 s; four more spells there 12-14 s)
            return None
        for r in (0.75, 1.5, 2.25):
            for k in range(8):
                a = k * math.pi / 4
                x, y = me.pos.x + math.cos(a) * r, me.pos.y + math.sin(a) * r
                if not T.inside(x, y):
                    continue
                j, i = T._cell(Vec(x, y, me.pos.z))
                if any(abs(float(z) - me.pos.z) < 0.6 for z in T.lz[:, j, i] if z == z):
                    return Vec(x, y, me.pos.z)
        return None

    def ride_lift(s, me, act):
        """On a gravity lift ride (s.lift_ride: base, landing, start): into the column's middle and stay there while it
        lifts, then off toward the landing once above it. A ride used to be run as a jump: steered at the landing 3+
        units aside it left the column before rising, and the edge guard pulled it back to the floor ("air steer back"
        446 times at Construct's lifts; CTF runners waited 80 s at the bottom of one, the player's objective list).
        False when the ride is over (landed, failed, or too long)."""
        base, land, t0 = s.lift_ride
        now = getattr(s, "now", 0.0)
        landed = me.pos.z >= land.z - 0.6 and (me.pos - land).flat() < 1.2
        failed = now - t0 > 3.0 and me.pos.z < base.z + 1.0 and (me.pos - base).flat() > 2.5
        # never into the column 3 s on and no higher than where the ride began: the way in is barred from here (a
        # people's ride from Construct's middle floor 3 u over the well: a bot pushed at its rim 20+ s, ride after ride,
        # the unstuck wiggle off for a ride; sim A/B Nov 4). This bot leaves that take-off for 60 s (jump_blocked)
        z0, key = getattr(s, "lift_from", (me.pos.z, None))
        # (beside the column or on a floor over its base: the ledge round Construct's wells is 0.9 u off the middle)
        stalled = now - t0 > 3.0 and me.pos.z < z0 + 1.0 and ((me.pos - base).flat() > 0.6 or me.pos.z > base.z + 1.5)
        if stalled and key is not None:
            # every people's take-off into this column from about this height (Construct: 3-4 a lift off the middle
            # floor, 3 s lost at each in turn)
            fails = s.__dict__.setdefault("jump_fails", {})
            fails[key] = (2, now + 60.0)
            for _, _, k, _ in getattr(s.T(), "hjumps", ()):
                to = k["take_off"]
                if math.hypot(to[0] - base.x, to[1] - base.y) < 3.0 and abs(to[2] - z0) < 1.0:
                    fails[(round(to[0], 1), round(to[1], 1))] = (2, now + 60.0)
            s.note("lift: no way into the column from here")
        if landed or failed or stalled or now - t0 > 12.0:
            s.lift_ride = None
            return False
        if me.pos.z > land.z + 0.3:
            s.steer(me, land, act, True, arrive=0.05)        # above the landing floor: step off onto it
        elif (me.pos - base).flat() > 0.3:
            s.steer(me, base, act, True, arrive=0.05)        # into the column's middle
        else:
            act.forward, act.strafe = 0.0, 0.0               # in it: the lift carries us
        act.jump = False
        s.nav_cmd = (act.forward, act.strafe)
        return True

    def move_to(s, me, point, act, nav):
        # skilled players know the jumps (gaps, ledges, crates); every bot, Easy too, uses the jumps
        # people made on this map and routes bots confirmed (LiDAR links): navigation, not combat
        # (the player: Easy bots jump to get around, never as a fighting trick)
        if getattr(s, "lift_ride", None) and s.ride_lift(me, act):
            return
        way = s.way_in(me, point)
        if way is not None:                      # a goal in an Elephant's bay (Sandtrap's flags): its own way in
            point, straight = way
            if straight:
                s.final_step_until = s.exact_until = getattr(s, "now", 0.0) + 0.3   # (the level's floor is not the bay's)
                s.wp = point
                s.steer(me, point, act, True, arrive=0.02)
                s.nav_cmd = (act.forward, act.strafe)
                s.note("elephant: up the ramp, along the bay")
                return
        route = getattr(s, "route", None)
        lu = getattr(s, "route_lineup", None)
        if route and lu is not None:             # back to the run-up first (below), then in along the person's way
            if (me.pos - lu).flat() > 0.35 and getattr(s, "now", 0.0) < s.route_until:
                s.steer(me, lu, act, True, arrive=0.02)
                s.planned_jump, s.jump_land = True, lu
                return
            s.route_lineup = None
            route = s.route = [q for q in route if (q[0] - lu).flat() > 0.01] or route
        if route:                                # inside a route people use: follow it as they did it
            near = min(range(len(route)), key=lambda k: (me.pos - route[k][0]).flat())
            jump = any(q[1] & ROUTE_JUMP for q in route[:near])   # (a take-off it came at from beside: jump here)
            del route[:near]                     # pushed ahead (or skipped a point): carry on from here
            while len(route) > 1 and (me.pos - route[0][0]).flat() < 0.4:
                jump |= bool(route.pop(0)[1] & ROUTE_JUMP)   # a take-off people jumped from: jump here
            full = getattr(s, "route_full", None)
            if jump and full and len(full) > 1 and getattr(s, "lineups", 0) < 2:
                # the hop goes along the person's way at the take-off, not across it: coming at it from the side (B15
                # Elite 7 at 52.8 s from the east, the hop north-west off the plank's side onto the floor under it;
                # 9 such "short of its end" a Boneyard match, then the route banned and the bots wiggling at the
                # plank's lip) it goes back to the person's run-up and runs in again (twice at most)
                k = next((k for k, q in enumerate(full) if q[1] & ROUTE_JUMP), None)
                if k is not None and k + 1 < len(full):
                    a, b = full[k][0], full[k + 1][0]
                    way_h, mv = math.atan2(b.y - a.y, b.x - a.x), me.vel
                    if mv.flat() > 0.8 and abs(wrap(math.atan2(mv.y, mv.x) - way_h)) > 0.7:
                        s.route, s.lineups, jump = list(full), getattr(s, "lineups", 0) + 1, False
                        s.route_lineup, s.route_until = full[0][0], getattr(s, "now", 0.0) + 6.0
                        s.note("route: lining up at its run-up")
                        s.steer(me, full[0][0], act, True, arrive=0.02)
                        s.planned_jump, s.jump_land = True, full[0][0]
                        return
            if ((len(route) == 1 and (me.pos - route[0][0]).flat() < 0.4) or getattr(s, "now", 0.0) > s.route_until
                    or (me.pos - route[0][0]).flat() > 3.0):   # done, too slow, or a fight took us away
                s.route = None
                s.judge_route(me)
            else:
                s.follow_route(me, act, jump)
                return
        grab = getattr(s, "grab_point", None) if way is None else None   # (round an Elephant: no stand moves)
        onto = getattr(s, "grab_onto", None)
        if onto is not None and s.goal[0] == "objective" and grab is not None:
            if ((onto - me.pos).flat() < 0.6 and abs(onto.z - me.pos.z) < 0.6) or getattr(s, "now", 0.0) > s.grab_onto_until:
                s.grab_onto = None           # on the stand's level (or given up): the last steps straight in, below
            else:
                point = onto                 # up the way people jumped onto it (the nav's jump links), not at the ring
                s.note("stand: up by people's jump")
        rnd = getattr(s, "grab_round", None)
        if rnd is not None and s.goal[0] == "objective" and grab is not None and (rnd - grab).flat() < 3.0:
            # round the stand to another side (move_to's last steps below chose it), by the nav, all the way even past
            # 4 u out (Spire, live: the nav ends every way to the core on its south-west side, a 1.1 u step and a
            # shield door, bots pushed there 50 s 1.8 u off it; the one pickup came from the north-east's 0.7 u ring,
            # 20 u round the platform)
            now = getattr(s, "now", 0.0)
            if (rnd - me.pos).flat() > 0.5 and now - s.grab_round_t < 25.0:   # (12 s: turned back 12.9 u out on the
                s.steer(me, nav(me.pos, rnd), act, True, arrive=0.05)          # ~20 u way round, live)
                s.exact_until = now + 0.3
                s.note("stand: round to another side")
                return
            s.grab_round, s.grab_best, s.grab_t, s.grab_side_t = None, 99.0, now, now
            s.grab_w = s.grab_side_d = 99.0
        near = (point - me.pos).flat()
        if (s.goal[0] == "objective" and near < 4.0 and abs(point.z - me.pos.z) < 1.5
                and (s.no_floor_at(point) or (grab is not None and (grab - point).flat() < 1.0))
                and (near < 1.6 and abs(point.z - me.pos.z) < 1.0 or not s.T().reaches(me.pos, point, jump=s.p.jump >= 0.5,
                                                                                       human=True))):
            # (only the last 1.6 u on its level, or when the nav has no way: Spire's core plateau has a walked ramp in
            # the nav since, and straight in from its north-east side was the 0.7 u edge bots jumped at 365 times) the
            # last steps onto a spot the nav has no floor for, straight in: Spire's Invasion core sits on a platform the
            # nav lost (its cell keeps the four floors under it); attackers stood 2.6 u off it for four minutes and
            # never took the core (live). And onto an objective to pick up, all the way: Breakpoint's bomb, attackers
            # came within 0.7-1.5 u for a second or two and drifted off, 256 s without a pickup
            target = grab if grab is not None and (grab - point).flat() < 1.0 else point
            now = getattr(s, "now", 0.0)
            d = (target - me.pos).flat()
            key = (round(target.x), round(target.y))
            if getattr(s, "grab_key", None) != key:
                s.grab_key, s.grab_best, s.grab_t, s.grab_side_t, s.grab_round = key, d, now, now, None
                s.grab_w, s.grab_side_d, s.grab_wz, s.grab_close_t = d, d, me.pos.z, None
            # in under 0.8 u 3 s and still not taken (take: Use held, the view on it): this side does not reach (Spire,
            # live: a runner pushed 45 s at 0.62 u east of the core; the pickups came from its west side at 0.25 u)
            s.grab_close_t = None if d >= 0.8 else getattr(s, "grab_close_t", None) or now
            close_stuck = s.grab_close_t is not None and now - s.grab_close_t > 3.0
            s.final_step_until = s.exact_until = now + 0.3   # (edge_guard: the floor there is real)
            s.wp = target                    # (look_point: facing it, not backing in at the last waypoint)
            hop = getattr(s, "grab_hop", None)
            if hop is not None and now - hop < 1.4:
                # a running jump: back off half a second, run at it, jump on the run once back where it stuck (+0.5 u)
                # or at 1.2 s (Spire, live: 341 standing hops against the stand's ring never got on; the one pickup
                # came from a running jump onto it; a jump 0.25 s into the run went off still moving away)
                if now - hop < 0.5:
                    s.steer(me, me.pos + (me.pos - target) * (2.0 / max(0.1, d)), act, True, arrive=0.05)
                else:
                    s.steer(me, target, act, True, arrive=0.05)
                    act.jump = d < getattr(s, "grab_hop_d", 0.0) + 0.5 or now - hop > 1.2
                    if act.jump:
                        s.grab_hop = now - 1.3   # (one jump, then on in)
                s.note("stand: running jump")
                return
            s.grab_hop = None
            s.steer(me, target, act, True, arrive=0.05)
            # under 0.3 u closer in a second: it stands on something (Spire's core sits 0.7 u up on its stand ring;
            # attackers stood 2.5 u off it for four minutes, live): jump at it. Measured over the whole second:
            # sliding along the ring gained 0.01 u a tick, and a clock reset at every 0.1 u never let a hop come
            # (live, bot 9 for 4 s). Under 0.6 u in 3.5 s: try another side
            if getattr(s, "grab_w", None) is None or d < s.grab_best:
                s.grab_best = d
            if now - s.grab_t > 1.0:
                # (up 0.5 u is progress too: a jump that got onto the core's plateau, 1 u over the floor round it, was
                # followed by another run-up whose back-off walked off it again, live)
                up = me.pos.z - getattr(s, "grab_wz", me.pos.z) > 0.5
                if getattr(s, "grab_w", 99.0) - d < 0.3 and d > 0.8 and not up:
                    s.grab_hop, s.grab_hop_d = now, d
                s.grab_t, s.grab_w, s.grab_wz = now + (1.4 if s.grab_hop == now else 0.0), d, me.pos.z
                if up:
                    s.grab_side_t, s.grab_side_d = now, d
            if now - s.grab_side_t > 3.5 and (getattr(s, "grab_side_d", 99.0) - d < 0.6 and d > 0.8 or close_stuck):
                # the far side first, then either flank: a spot 3 u out on our own floor's level (a path reaches it; the
                # stand's raised strip 2.2 u out does not), not over the edge
                here = math.atan2(me.pos.y - target.y, me.pos.x - target.x)
                T = s.T()
                # first a jump people made onto the stand (Spire's core: tonight's attackers hopped at its 1.2 u ring
                # from 2.8 u for 10 s and more, no pickup in two games; LiDAR has a person's jump onto it from the east)
                land = None if close_stuck else s.jump_onto(me, target)    # (close in: a jump lands it here again)
                s.grab_close_t = None
                if land is not None:
                    s.grab_onto, s.grab_onto_until = land, now + 15.0
                else:
                    s.note(f"stand: no people's jump up ({getattr(s, 'onto_why', '')})")
                    for turn in (math.pi, math.pi / 2, -math.pi / 2):
                        p = target + Vec(math.cos(here + turn), math.sin(here + turn), 0) * 3.0
                        if T.ground_ok(p.x, p.y, me.pos.z + 0.4, drop=0.8):
                            s.grab_round, s.grab_round_t = Vec(p.x, p.y, me.pos.z), now
                            break
                    else:
                        s.grab_side_t = now      # no other side to try: straight in again
            if now - s.grab_side_t > 3.5:
                s.grab_side_t, s.grab_side_d = now, d
            return
        rg = getattr(s, "round_goal", None)                 # (walking round a held exit to here: no people's jumps)
        people = rg is None or getattr(s, "now", 0.0) >= s.round_until or (point - rg).flat() > 3.0
        way = dict(jump=s.p.jump >= 0.5 and people, human=people, safe=me.has_flag)   # (a carrier: round big drops)
        now_ = getattr(s, "now", 0.0)
        if people and any(f[0] >= 2 and now_ < f[1] and math.hypot(k[0] - me.pos.x, k[1] - me.pos.y) < 6.0
                          for k, f in getattr(s, "jump_fails", {}).items()):
            # a person's jump or route banned here (jump_blocked): the walking way all round it, not only where the
            # nav offers the route. Asked with people's ways from a step off the route's start, the nav sent the bot
            # back to its run-up, where the ban sent it to the walking way's lip, and so on: live Boneyard B21, Elite 9
            # 24.6-104.7 s at the plank's foot (251, 82, -10), its next point flipping run-up / lip every 0.3-0.5 s
            # (replay_think: the writer is move_to's own nav)
            way = dict(safe=me.has_flag)
        tr = getattr(s, "tele_round", None)                 # (walking round a held teleporter exit: no teleporters)
        if tr is not None and getattr(s, "now", 0.0) < tr[0] and (point - tr[1]).flat() <= 3.0:
            way["no_tele"] = True
        s.judge_jump(me)
        nxt = nav(me.pos, point, **way)
        if nxt is None:
            # no way from or to a place off the numbers (Terrain.nav: a NaN; the sim's Halo 2 Lockout Assault): stand
            # this tick rather than crash it
            act.forward = act.strafe = 0.0
            s.nav_cmd = (0.0, 0.0)
            return
        if (hasattr(nxt, "sx") or hasattr(nxt, "path")) and s.jump_blocked(nxt):
            nxt = nav(me.pos, point, safe=me.has_flag)          # a jump (a route) it fell short of twice: another way
        off = s.off_lost_floor(me, nxt)
        if off is not None:
            s.steer(me, off, act, True)
            s.nav_cmd = (act.forward, act.strafe)
            s.note("floor not in the nav: step off")
            return
        held = hasattr(nxt, "sx") and s.exit_held(me, nxt, point)
        pad = not held and s.pad_held(me, point, **way)
        if held == "round":
            nxt = nav(me.pos, point, safe=me.has_flag)
        elif pad == "round":
            nxt = nav(me.pos, point, **dict(way, no_tele=True))
        elif held or pad:
            act.forward = act.strafe = 0.0                      # no way round: wait at the take-off, short of the pad
            s.nav_cmd = (0.0, 0.0)
            return
        if hasattr(nxt, "path"):                 # the way into a route: take it
            s.route = [(Vec(*q[:3]), q[3]) for q in nxt.path]
            s.route_full, s.lineups, s.route_lineup = list(s.route), 0, None
            s.route_until = getattr(s, "now", 0.0) + 3.0 + sum((a[0] - b[0]).flat() for a, b in zip(s.route, s.route[1:])) / 0.8
            s.route_key, s.route_end = (round(nxt.path[0][0], 1), round(nxt.path[0][1], 1)), s.route[-1][0]   # judge_route
            s.follow_route(me, act, False)
            return
        if hasattr(nxt, "sx"):                   # a jump: run to the take-off spot, jump, land
            lift = s.lift_base(nxt, me)
            if lift is not None:                 # a person's ride up a gravity lift: ride it
                s.lift_ride = (lift, Vec(nxt.x, nxt.y, nxt.z), getattr(s, "now", 0.0))
                s.lift_from = (me.pos.z, (round(nxt.sx, 1), round(nxt.sy, 1)))   # (ride_lift: stalled at the rim)
                s.ride_lift(me, act)
                return
            start = Vec(nxt.sx, nxt.sy, me.pos.z)
            if me.has_flag:                      # carrying: its jump falls short, so it goes later, nearer the landing
                start = s.carrier_take_off(start, nxt)
            if not math.isnan(nxt.rx):           # a person's jump: from his run-up spot, at speed
                # a person made it, so the edge guard trusts the run-up and take-off (Spire, live: the core carrier's
                # way down is a person's 35 u jump into the lift column; 1078 "edge stop"s, 200 s on the top)
                s.final_step_until = s.exact_until = getattr(s, "now", 0.0) + 0.3
                key = (round(nxt.sx, 2), round(nxt.sy, 2))
                now = getattr(s, "now", 0.0)
                if getattr(s, "runup_key", None) != key:
                    s.runup_key, s.runup_done, s.runup_t0 = key, False, now
                    s.lineup = s.lineup_point(me, nxt) if me.has_flag else None
                run = Vec(nxt.rx, nxt.ry, me.pos.z)
                lane = Vec(nxt.sx - nxt.rx, nxt.sy - nxt.ry, 0.0)
                rel = me.pos - run
                along = (rel.x * lane.x + rel.y * lane.y) / max(lane.flat(), 1e-6)     # past the run-up (> 0) or behind
                side = abs(rel.x * lane.y - rel.y * lane.x) / max(lane.flat(), 1e-6)   # off the run's line
                if not s.runup_done and getattr(s, "lineup", None) is not None:
                    # a carrier lines up behind the run-up first, so it runs at the take-off along the person's run with
                    # its momentum on the jump (live Spire: VaporRaven came at the player's drop onto the heat vent from
                    # the side, lost its footing 0.6 s on the bump by the run-up, circled 1.65 s, passed its take-off
                    # 0.68 u off, slid off the rim; the core fell where no bot reached it). 4 s at it: as before
                    if (me.pos - s.lineup).flat() < 0.35 or (along < -0.25 and side < 0.25) or now - s.runup_t0 > 4.0:
                        s.lineup, s.runup_t0 = None, now
                    else:
                        far = now - s.runup_t0 > 3.0 and (me.pos - s.lineup).flat() > 0.5
                        s.steer(me, nav(me.pos, s.lineup) if far else s.lineup, act, True, arrive=0.02)
                        s.nav_cmd, s.runup_t = (act.forward, act.strafe), getattr(s, "now", 0.0)
                        return
                if not s.runup_done and (rel.flat() < (0.35 if me.has_flag else 0.3)
                                         or (me.has_flag and along >= 0.0 and side < 0.35)
                                         or (0.3 <= along <= lane.flat() and side < 0.75)):
                    # (anyone already on the run's line past the run-up runs on from there: live Boneyard B14 / B18
                    # phase 3, attackers going back 2.8 u to the person's run-up on the narrow walkway to the drop at
                    # (265.67, 89.63), giving way to each other, 5.5 s a pass, 1,250+ rows a match there; replay_think)
                    s.runup_done = True              # (a carrier: passing it within 0.35 u, not circling back to it)
                if not s.runup_done and now - s.runup_t0 > s.RUNUP_GIVE_UP and (me.pos - start).flat() < 2.5:
                    # not at the run-up in RUNUP_GIVE_UP s, the take-off this near on its level: from here (live
                    # Boneyard 20261105-033712 / 035632, the drop to the core: 3-6 attackers at a time bunched on the
                    # way to the person's run-up 2.8 u behind the take-off, queued on each other, 17,000-24,000 rows of
                    # phase 3 there, none dropped)
                    s.runup_done = True
                    s.note("run-up not reached: from here")
                if not s.runup_done:
                    # straight at the run-up; still more than half a unit off after 3 s: by the walk graph
                    # (straight from the floor below pushed 28 times into the ledge under it, Lockout)
                    slow = now - s.runup_t0 > 3.0 and (me.pos - run).flat() > 0.5
                    s.steer(me, nav(me.pos, run) if slow else run, act, True, arrive=0.02)
                    s.nav_cmd, s.runup_t = (act.forward, act.strafe), getattr(s, "now", 0.0)
                    return
            if (me.pos - start).flat() > 0.35:
                s.steer(me, start, act, True, arrive=0.02)
                s.exact_until = getattr(s, "now", 0.0) + 0.3
                # the run to a take-off at the edge: the guard's probes saw the void past it and held
                # bots at the run-up for minutes (Lockout); trust it like the path, no braking
                s.nav_cmd, s.runup_t = (act.forward, act.strafe), getattr(s, "now", 0.0)
                return
            act.jump = True
            s.planned_jump = True                # a gap on purpose: the edge guard lets it pass
            s.jump_try = ((round(nxt.sx, 1), round(nxt.sy, 1)), Vec(nxt.x, nxt.y, nxt.z), getattr(s, "now", 0.0))   # (judge_jump)
            nxt = Vec(nxt.x, nxt.y, nxt.z)
            s.jump_land = nxt
        elif not s.segment_ok(me, nxt):
            nxt = nav(me.pos, point, lookahead=1)    # a corner-cutting waypoint over the void: next cell
        # only the final destination counts as "arrived"; a near path point still pulls us on
        s.wp = nxt                     # the next waypoint (brain/equipment.py: a jetpack up a ledge)
        s.steer(me, nxt, act, True, arrive=0.3 if dist(me.pos, point) < 0.5 else 0.02)
        s.nav_cmd = (act.forward, act.strafe)    # on the path: the edge guard trusts the nav near

    def follow_route(s, me, act, jump):
        """Toward the route's next point, crouched where people crouched, jumping where they
        jumped. A person walked it alive, so the edge guard trusts it (over a gap: fly on to the
        next point)."""
        p, flags = s.route[0]
        s.steer(me, p, act, True, arrive=0.02)
        act.crouch = bool(flags & ROUTE_CROUCH)
        act.jump = jump
        s.planned_jump, s.jump_land = True, p

    def no_floor_at(s, p):
        """The nav has no floor within 0.6 u of p's height in p's cell (a surface it lost: move_to's last steps)."""
        T = getattr(s, "_terrain", None)
        if T is None:
            from .maps import terrain
            T = s._terrain = terrain(s.map)
        j, i = T._cell(p)
        return not any(math.isfinite(z) and abs(z - p.z) < 0.6 for z in T.lz[:, j, i].tolist())

    @staticmethod
    def walk_cone(me, look, walk, cone=LOOK_CONE):
        """The look point turned to within cone of the walking direction walk (radians), same distance and height."""
        d = look - me.pos
        r = d.flat()
        if r < 1e-6:
            return look
        diff = wrap(math.atan2(d.y, d.x) - walk)
        if abs(diff) <= cone:
            return look
        a = walk + math.copysign(cone, diff)
        return Vec(me.pos.x + math.cos(a) * r, me.pos.y + math.sin(a) * r, look.z)

    def steer(s, me, point, act, go, arrive=0.3):
        if not go:
            return
        d = point - me.pos
        if d.flat() < arrive:
            return
        want = math.atan2(d.y, d.x)
        rel = wrap(want - act.yaw)
        act.forward, act.strafe = math.cos(rel), math.sin(rel)

    def look_point(s, me, point, nav=None, post=None):
        # look at the most recent threat it knows of: what it saw or what hit it, and a radar dot or a callout
        # for as long as its awareness keeps it in mind (Easy half a second, Normal about 2 s, Legendary 5 s);
        # one behind a wall: the way he would come, not the wall. At a post: out along its longest sightline.
        # Walking: down the path at eye height. It used to look at the destination (through the walls) at
        # floor height: live, Boardwalk, 27% of walking ticks faced 60+ degrees off the walk, 13% had a wall
        # within 1.5 units of the face, 9% looked 20+ degrees down
        now = getattr(s, "now", 0.0)
        if me.has_flag:
            # carrying (the core, a flag, the ball): no gun to answer a man with, so no watching, eyes on the way (live
            # Spire: the core carrier looked from a radar dot to its path and back across the spire top, its walk swung
            # with the view and went over the east edge; the core fell where no bot reached it, round lost)
            w = s.route[0][0] if getattr(s, "route", None) else (getattr(s, "wp", None) or point)
            s.att = ["path", None, round(w.x, 1), round(w.y, 1)]
            return s.ahead(me, w)
        gate = s.tele_watching(now)
        if gate is not None:                        # out of a teleporter, a chaser behind: where he would come out
            s.att = ["watch", "teleporter", round(gate.x, 1), round(gate.y, 1)]
            return gate + Vec(0, 0, 0.62)
        land = s.arrival(now, me)                   # a man on his way here by a launch: where he comes down (not over
        if land is not None and not any(b.source == "hit" and now - b.t < 1.5 for b in s.per.beliefs.values()):
            s.att = ["watch", "landing", round(land.x, 1), round(land.y, 1)]       # being shot at)
            return land + Vec(0, 0, 0.62)
        keep = 0.5 + 4.5 * s.p.aware
        # a man it saw with its own eyes stays in mind 3 s at least, Easy too: it does not plan, but it; Easy watched
        # him 0.5 s, then looked away (a hit, HIT_KEEP s at least: Easy's 0.5 s ran out before its turn to the shots
        # ended, so 22-33% of the first hits on it went unanswered, the player 6%) (a shot heard, HEARD_KEEP s at most:
        # a person glances at gunfire and walks on; watched as long as memory held, 5 s for Legendary, it looked 60 deg
        # and more off its way 14% of its walking time, 2.5 points of it at heard shots, the player 9.4%: live,
        # logs/tmp/look_off.py)
        known = [(k, b) for k, b in s.per.beliefs.items()
                 if now - b.t < (max(keep, EYES_KEEP) if b.source == "eyes" else max(keep, HIT_KEEP) if b.source == "hit"
                                 else min(keep, HEARD_KEEP) if b.source == "heard" else keep)]   # (a sighting watched as long
        walking = me.vel.flat() > 1.2
        if known:                                                                # as memory held it: 10 s)
            k, b = max(known, key=lambda kb: kb[1].t)
            kept = next(((kk, bb) for kk, bb in known if kk == getattr(s, "watch_key", None)), None)
            if kept is not None and kept[0] != k and now < getattr(s, "watch_hold", 0.0):
                k, b = kept           # (another man's gunfire a moment later: not a turn at every shot heard)
            elif k != getattr(s, "watch_key", None):
                s.watch_key, s.watch_hold = k, now + 1.0
            label = ["watch", f"#{k} {b.source}" if isinstance(k, int) else " ".join(map(str, k)),
                     round(b.pos.x, 1), round(b.pos.y, 1)]
            # shot at a moment ago: turn to where the shots come from, wall or not (the shooter sees us; our sight lines
            # are coarser than his). RapidLynx took five of the player's shots walking on, eyes on its path, to its
            # death
            hit = next(((kk, bb) for kk, bb in known if bb.source == "hit" and now - bb.t < 1.5), None)
            att = getattr(s.per, "attacker_id", None)
            if hit is None and att in s.per.beliefs and now - getattr(s.per, "attacker_t", -9.0) < 1.5:
                # the man hurting us, whatever refreshed his belief since (the radar took its "hit" mark and the next
                # radar dot took the look: a Heroic hit at 147.8 s watched #6 0.3 s, then #10 and #9 on its radar, and
                # walked on without a shot at #6; Powerhouse, 14-43% of first hits turned to and never answered)
                hit = (att, s.per.beliefs[att])
            if hit is not None:
                s.att = ["watch", f"#{hit[0]} hit", round(hit[1].pos.x, 1), round(hit[1].pos.y, 1)]
                s.aim.whip = max(s.p.aware, HIT_WHIP)   # a quick spin to it (Aim.step), not a walking turn
                return hit[1].pos
            if nav is None or s.T().los(eye(me), b.pos + Vec(0, 0, 0.35)):
                s.att = label
                return b.pos                          # in the open: on him
            # behind cover: pre-aim where he would come into view on his way to us, at any distance. Seen, shot by, on
            # radar, heard or called out alike. Walking, the look stays within LOOK_CONE of the way it goes; until only
            # glances, and only at men within 6 u
            spot = s.man_appears(me, b.pos)
            if spot is not None:
                s.att = label
                return spot
            if dist(me.pos, b.pos) < 6.0:             # no way to him found near: the way he would come
                s.att = label
                return s.ahead(me, nav(me.pos, b.pos))
            # far, no way to him near us: look where we go
        ride = getattr(s, "lift_ride", None)
        cover = s.exit_cover(ride[1]) if ride is not None and s.p.tier != "novice" else None
        if cover is not None:
            # map brain INTER-LIFT-02 ("the attacker pre-fires the exit's cover when it arrives"): up a lift with nobody
            # known, on the cover a man waiting at its exit would hold (aimed, not fired at); Easy never plans
            s.att = ["watch", "lift exit", round(cover.x, 1), round(cover.y, 1)]
            return cover + Vec(0, 0, 0.62)
        settled = getattr(s, "loiter_pt", None) is None or (me.pos - s.loiter_pt).flat() < 1.0 or not walking
        if post is not None and (me.pos - post).flat() < 3.5 and settled:
            # (walking to the next spot at a post: down the walk, not sideways along a sightline: 65% of those
            # ticks faced 60+ degrees off the walk, Cyberdyne)
            s.att = ["post", None, round(post.x, 1), round(post.y, 1)]
            beliefs = list(s.per.beliefs.values()) if s.goal[0] == "retreat" else []
            # map brain MAP-10 ("the ANCHOR's default sightline covers the hottest"): else the team's hot zone (the
            # director's hot_spot: where it saw the enemy in the last 20 s), not a random open line
            return s.post_view(me, post, max(beliefs, key=lambda b: b.t).pos if beliefs else getattr(s, "hot_spot", None))
        w = s.route[0][0] if getattr(s, "route", None) else (getattr(s, "wp", None) or point)
        if nav is not None and not getattr(s, "route", None) and (w - me.pos).flat() < 2.0 and (w - point).flat() > 1.0:
            w = nav(w, point)              # at a corner: look into the turn, as a walker does
        if s.p.aware > 0.5:
            # nothing known: a good player checks his flanks as he goes, a glance to one side then the other
            # every couple of seconds (most deaths in the sim were men never seen), only where there is room
            # to see (a corridor wall is no flank); Easy and Normal look where they walk
            if now >= getattr(s, "check_at", 0.0):
                s.check_at, s.check_until = now + s.rng.uniform(2.0, 4.0) / s.p.aware, now + 0.45
                s.check_side = -getattr(s, "check_side", 1)
            if now < s.check_until:
                h = s.ahead(me, w) - me.pos
                a = math.atan2(h.y, h.x) + s.check_side * 1.05   # (60 degrees: a check, not a turn away; 80 read
                                                                    # as walking blind)
                q = me.pos + Vec(math.cos(a), math.sin(a), 0) * 10.0 + Vec(0, 0, 0.27)
                if s.T().los(eye(me), eye(me) + Vec(math.cos(a), math.sin(a), 0) * 3.0):
                    s.att = ["flank", "left" if s.check_side > 0 else "right", round(q.x, 1), round(q.y, 1)]
                    return q
        # nobody known: pre-aim along the way it goes, the farthest point of the route still in sight, the corner it is
        # coming to, at head height; until then 8 u down the next waypoint at eye height, which looked past a corner
        # into its wall. Not on the way to a jump or along a person's recorded route (the walking way ahead turns from
        # them: it looks toward the landing), nor carrying an objective (Spire's core carrier, tools/test_invasion.py:
        # stalled under the spire top either way, and with this look it rode back up onto the top; a carrier's way is
        # drops and jumps)
        jumping = getattr(s, "planned_jump", False) or getattr(s, "route", None) or hasattr(getattr(s, "wp", None), "sx")
        lead = s.route_lead(me, point) if nav is not None and not jumping and not me.has_flag else None
        if lead is not None:
            s.att = ["path", "lead", round(lead.x, 1), round(lead.y, 1)]
            return lead
        s.att = ["path", None, round(w.x, 1), round(w.y, 1)]
        return s.ahead(me, w)

    def route_view(s, me, point, far, **way):
        """The farthest point of our way toward point still in sight from here, checked every unit up to far along it:
        where the way goes out of view (a corner, a doorway, a brow). None: no way there, or that point under 2 u off.
        way: Terrain.path_ahead's jump / human / safe / limit."""
        T, last = s.T(), None
        for q in T.path_ahead(me.pos, point, n=int(far / 0.25), **way)[3::4]:
            if not T.los(me.pos, q):
                break
            last = q
        return last if last is not None and (last - me.pos).flat() >= 2.0 else None

    def man_appears(s, me, p):
        """Where a man at p, out of sight, would come into view on his way to us: the last point of our own way to him
        still in sight (his way to us, the other way round). Of men walking round to a bot from behind cover it had the
        aim on 64-75% the moment they came into view on Settlement, Construct and Boardwalk; straight at them through
        the wall 23-32%, the nearest edge of the cover 23-34% (logs/tmp/preaim_proto.py, tools/test_preaim_moves.py). A
        search round him out to about our distance, not the map (3-5 ms on Settlement and the player's Forge World
        layout): kept 0.5 s while he moves under 1.5 u and we under 1 u."""
        now = getattr(s, "now", 0.0)
        c = getattr(s, "_appear", None)
        if c and now < c[1] and (p - c[0]).flat() < 1.5 and abs(p.z - c[0].z) < 1.0 and (me.pos - c[2]).flat() < 1.0:
            return c[3]
        d = (p - me.pos).flat()
        out = s.route_view(me, p, min(d * 1.6 + 8.0, 50.0), limit=d * 1.6 + 8.0)
        s._appear = (p, now + 0.5, me.pos, out)
        return out

    def route_lead(s, me, point):
        """Where to pre-aim on the move with nobody known: the farthest point of our route toward point still in sight,
        up to 20 u on (the corner we are coming to: whoever is round it steps out there), at head height. Kept 0.4 s."""
        now = getattr(s, "now", 0.0)
        c = getattr(s, "_lead", None)
        if c and now < c[0] and (me.pos - c[1]).flat() < 0.75 and (c[2] - point).flat() < 1.0:
            return c[3]
        lead = s.route_view(me, point, 20.0, jump=s.p.jump >= 0.5, human=True, safe=me.has_flag)
        out = Vec(lead.x, lead.y, lead.z + 0.27) if lead is not None else None
        s._lead = (now + 0.4, me.pos, point, out)
        return out

    def ahead(s, me, w):
        """An eye-level point 8 units on toward w, or along our walk when w is underfoot (aim.step aims 0.35
        above the point it is given: 0.27 more is the eye's 0.62)."""
        d = Vec(w.x - me.pos.x, w.y - me.pos.y, 0.0)
        if d.flat() < 0.5:
            d = Vec(me.vel.x, me.vel.y, 0.0) if me.vel.flat() > 0.3 else Vec(math.cos(me.yaw), math.sin(me.yaw), 0.0)
        return me.pos + d.norm() * 8.0 + Vec(0, 0, 0.27)

    def post_view(s, me, post, toward=None):
        """At a post: out along one of its most open sightlines (of 8 directions, the ones with at least 70%
        of the longest clear view), picked again every few seconds the way a camper turns his head. In cover
        (toward: where the threat was): the open side nearest him, the way he would come."""
        now = getattr(s, "now", 0.0)
        key = (round(post.x), round(post.y))
        if getattr(s, "view_key", None) != key or now > s.view_until:
            e = Vec(post.x, post.y, post.z + 0.62)
            views = []
            for k in range(8):
                a, clear = k * math.pi / 4, 0
                for r in (3, 6, 10, 15, 20, 30):
                    if not s.T().los(e, e + Vec(math.cos(a) * r, math.sin(a) * r, 0)):
                        break
                    clear = r
                views.append((clear, a))
            top = max(c for c, _ in views)
            s.view_key, s.view_until = key, now + s.rng.uniform(2.5, 6.0)
            ok = [a for c, a in views if c >= 0.7 * top]
            if toward is not None:
                ta = math.atan2(toward.y - post.y, toward.x - post.x)
                ok = [min((a for c, a in views if c >= 3), key=lambda a: abs(wrap(a - ta)), default=ok[0])]
            s.view_yaw = s.rng.choice(ok)
        return me.pos + Vec(math.cos(s.view_yaw), math.sin(s.view_yaw), 0) * 10.0 + Vec(0, 0, 0.27)

    def T(s):
        if getattr(s, "_terrain", None) is None:
            from .maps import terrain
            s._terrain = terrain(s.map)
        T = s._terrain
        if not getattr(T, "_traps_loaded", False):       # the spots earlier matches were trapped at (mark_trap): walls
            T._traps_loaded = True
            try:
                T.load_traps(s.trap_path())
            except Exception:
                pass
        return T

    def feet(s, me):
        """Where its feet are: the position less the game's height over the floor (FEET_GAMES). The level-data probes
        measure from the floor: in Halo 4 the chest ray of clearance ran 0.78 u over the floor, over a Spartan's head
        (low walls, rails and crates unseen), and flat floor read as a step down (the edge guard off on 52% of
        Halo 4's walking ticks, the owner's ca_blood_cavern games, vs 7-17% in Halo 3 / Reach / Halo 2). s.feet_off: the
        simulator's bodies stand on the floor (brain/sim.py sets 0)."""
        off = getattr(s, "feet_off", None)
        if off is None:
            off = H2A_FEET if s.map["name"].startswith(FEET_GAMES) else 0.0
        return Vec(me.pos.x, me.pos.y, me.pos.z - off) if off else me.pos

    def air_heights(s, T):
        """(attack-run, travel) heights over the ground: people's flying on this map (rides.json) if learned, else the
        map's own shape (Terrain.air_levels): nothing to paint by hand."""
        return getattr(T, "air_heights", None) or T.air_levels()

    def air_terrain(s):
        """The map's terrain for a pilot (painted airspace, ground height), built once."""
        if getattr(s, "_terrain", None) is None:
            from .maps import terrain
            s._terrain = terrain(s.map)
        return s._terrain

    def cover_from(s, me, threats):
        """The nearest wall LiDAR painted (maps/<map>/lidar.json "cover": the floor in front of an
        upright wall, glass excluded) within 10 units that stands between us and every threat:
        the wall faces each of them and their line of sight to the spot is blocked. None if none."""
        if getattr(s, "_terrain", None) is None:
            from .maps import terrain
            s._terrain = terrain(s.map)
        T = s._terrain
        cs = getattr(T, "cover_spots", None)
        if cs is None or not len(cs):
            return None
        import numpy as np
        d = np.hypot(cs[:, 0] - me.pos.x, cs[:, 1] - me.pos.y)
        near = np.nonzero((d < 10.0) & (np.abs(cs[:, 2] - me.pos.z) < 1.5))[0]
        for k in near[np.argsort(d[near])][:30]:
            x, y, z, dx, dy = (float(v) for v in cs[k])
            spot = Vec(x, y, z)
            if all(((t.x - x) * dx + (t.y - y) * dy) > 0.3 * math.hypot(t.x - x, t.y - y) and not T.los(t, spot)
                   for t in threats):
                return spot
        return None

    def away_from(s, me, threats):
        c = Vec(sum(t.x for t in threats) / len(threats), sum(t.y for t in threats) / len(threats), me.pos.z)
        return me.pos + (me.pos - c).norm() * 12

    ROAM_SAFE_R, ROAM_SAFE_R_HURT, ROAM_FRESH_S, ROAM_MAX_S = 18.0, 32.0, 10.0, 30.0

    def roam(s, me):
        if getattr(s, "ff_free", False):
            return s.free_roam(me)
        if s.roam_point is None or (me.pos - s.roam_point).flat() < 3:
            s.roam_point = s.rng.choice(s.map["zones"].zones).center
        return s.roam_point

    def free_roam(s, me):
        """Firefight: wander the whole map by its own choice, not after the person. A new spot when it gets to the last
        one, when it has been walking to it ROAM_MAX_S, or when an enemy was seen near it lately; the spot is one of a few
        zone centres picked at random that lie clear of every enemy seen in the last ROAM_FRESH_S (further off when hurt),
        and some way from here so it crosses the map rather than shuffling. No spot clear: the one furthest from them."""
        now = getattr(s, "now", 0.0)
        threats = [b.pos for k, b in s.per.beliefs.items() if isinstance(k, int) and now - b.t < s.ROAM_FRESH_S]
        hurt = me.shield + me.health < 1.0
        clear = s.ROAM_SAFE_R_HURT if hurt else s.ROAM_SAFE_R
        rp = s.roam_point
        stale = rp is None or (me.pos - rp).flat() < 3 or now - getattr(s, "roam_since", -99.0) > s.ROAM_MAX_S
        if not stale and threats and any(dist(rp, q) < clear for q in threats):
            stale = True                                  # somebody was seen at it: not there
        if stale:
            zones = [z.center for z in s.map["zones"].zones]
            picks = s.rng.sample(zones, min(8, len(zones)))
            safe = [c for c in picks if all(dist(c, q) >= clear for q in threats)]
            far = [c for c in safe if (c - me.pos).flat() > 15.0] or safe
            s.roam_point = (s.rng.choice(far) if far else
                            max(picks, key=lambda c: min((dist(c, q) for q in threats), default=0.0)))
            s.roam_since = now
        return s.roam_point
