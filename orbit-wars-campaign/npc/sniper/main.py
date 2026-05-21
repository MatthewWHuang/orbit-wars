"""Sniper: only attacks when it can capture a planet with exactly the right
number of ships. Patient, efficient, and refuses to commit to losing fights."""

import math


def agent(obs):
    if isinstance(obs, dict):
        player = obs.get("player", 0)
        raw_planets = obs.get("planets", [])
    else:
        player = obs.player
        raw_planets = obs.planets

    mine = []
    targets = []
    for p in raw_planets:
        pid, owner, x, y, radius, ships, prod = p
        if owner == player:
            mine.append((pid, x, y, ships))
        else:
            targets.append((pid, x, y, ships))

    if not targets:
        return []

    moves = []
    used = set()
    for pid, mx, my, mships in mine:
        if mships < 2:
            continue
        candidates = [t for t in targets if t[0] not in used and t[3] + 1 <= mships - 1]
        if not candidates:
            continue
        target = min(candidates, key=lambda t: (t[1] - mx) ** 2 + (t[2] - my) ** 2)
        angle = math.atan2(target[2] - my, target[1] - mx)
        moves.append([pid, angle, target[3] + 2])
        used.add(target[0])
    return moves
