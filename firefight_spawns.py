"""Firefight Reach bot respawns and round-boundary detection.

The round detector is a Firefight-only adaptation of brain.invasion.Watch:
- flag a transition when 20% or fewer of the human/bot lobby are alive;
- wait for a mass respawn burst (60% of the lobby within three seconds);
- clear old spawn assignments and record the new round's actual bot spawns.
Covenant AI units are deliberately excluded from the lobby count.
"""

import json
import math
import random

SPAWN_CLEAR = 3.0
SPAWN_FOE = 25.0


class RoundWatch:
    """Firefight-only copy of Invasion Watch's round-transition signal."""

    SPAWN_WINDOW = 45.0
    ROUND_WAIT = 30.0

    def __init__(self):
        self.alive = {}
        self.births = []
        self.spawns = []
        self.round_wait_until = -1.0
        self.pending = False
        self.round_number = 1
        self.started = False

    @staticmethod
    def _lobby_players(snap, bot_ids):
        # Firefight snapshots append Covenant AI as PlayerState(name="ai", is_bot=False).
        # Count actual humans plus the registered bots controlled by this process.
        return [p for p in snap.players
                if p.id in bot_ids or (not getattr(p, "is_bot", False)
                                       and str(getattr(p, "name", "")).lower() != "ai")]

    def update(self, snap, now, bots):
        """Return True once when a new round is detected; expose pending during its transition."""
        bot_ids = set(bots)
        players = self._lobby_players(snap, bot_ids)
        if not players:
            return False

        for p in players:
            was_alive = self.alive.get(p.id, False)
            if p.alive and not was_alive:
                self.births.append(now)
                self.spawns.append((now, p.team))
            self.alive[p.id] = p.alive

        self.spawns = [x for x in self.spawns if now - x[0] < self.SPAWN_WINDOW]
        self.births = [t for t in self.births if now - t < 3.0]

        alive_count = sum(1 for p in players if p.alive)
        if alive_count * 5 <= len(players):
            self.round_wait_until = now + self.ROUND_WAIT
            self.pending = True

        burst = [x for x in self.spawns if now - x[0] < 3.0]
        need = max(3, math.ceil(0.6 * len(players)))
        new_round = self.pending and (
            len(burst) >= need or
            (now < self.round_wait_until and len(self.births) >= need)
        )

        # The initial match-start spawn burst is not a round transition.
        if not self.started:
            if alive_count > 0:
                self.started = True
            self.pending = False
            return False

        if new_round:
            self.round_number += 1
            self.pending = False
            self.round_wait_until = -1.0
            self.spawns = burst
            self.births = [t for t in self.births if now - t < 3.0]
            return True

        return False


def spawn_check(send, bots, snap, state, pools=None, rng=None, now=0.0, trace=None,
                round_pending=False):
    """Record each bot's first actual spawn this round and reuse team positions on respawn."""
    rng = rng or random.Random()

    initial = state.setdefault("initial", {})
    by_team = state.setdefault("by_team", {})
    picks = state.setdefault("picks", {})

    if round_pending:
        # Cancel old-round destinations while the game is transitioning.
        for index in list(picks):
            me = snap.player(index)
            if me is not None and not me.alive:
                send(index, math.nan, math.nan, math.nan, 0.0)
        picks.clear()
        return

    # Record each registered bot's first actual living position in this round.
    for index in bots:
        me = snap.player(index)
        if me is None or not me.is_bot or not me.alive or index in initial:
            continue

        pos = me.pos
        try:
            q = (float(pos.x), float(pos.y), float(pos.z), float(me.yaw))
        except (AttributeError, TypeError, ValueError):
            continue

        if not all(math.isfinite(v) and abs(v) < 100000 for v in q):
            continue

        team = me.team
        initial[index] = (team, q)
        pool = by_team.setdefault(team, [])
        if not any(math.dist(q[:3], old[:3]) < 1.0 for old in pool):
            pool.append(q)

        print(f"Firefight: recorded bot {index}, team {team}, "
              f"spawn {tuple(round(v, 2) for v in q[:3])}", flush=True)
        if trace:
            trace.write(json.dumps({"t": round(now, 3), "id": index, "team": team,
                                    "spawn": "initial_recorded", "at": q[:3]}) + "\n")

    alive = [p for p in snap.players if p.alive]

    # Only registered bots are moved; humans and Covenant AI remain untouched.
    for index in bots:
        me = snap.player(index)
        if me is None or not me.is_bot:
            continue
        if me.alive:
            picks.pop(index, None)
            continue
        if index in picks:
            continue

        record = initial.get(index)
        if record is None:
            continue
        team, original = record
        pool = by_team.get(team, [])
        if not pool:
            continue

        open_points = [q for q in pool if all(
            math.hypot(p.pos.x - q[0], p.pos.y - q[1]) >= SPAWN_CLEAR
            or abs(p.pos.z - q[2]) > 2.5 for p in alive
        )]
        candidates = open_points or pool
        foes = [p for p in alive if p.team != team]
        safer = [q for q in candidates if all(
            math.hypot(p.pos.x - q[0], p.pos.y - q[1]) >= SPAWN_FOE for p in foes
        )]
        q = rng.choice(safer or candidates)
        picks[index] = (q, now)
        send(index, *q)

        if trace:
            trace.write(json.dumps({"t": round(now, 3), "id": index, "team": team,
                                    "spawn": "initial_team_position", "at": q[:3]}) + "\n")
