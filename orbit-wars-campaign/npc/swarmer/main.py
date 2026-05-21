"""Swarmer: every turn, every owned planet sends as many ships as it has at the
nearest non-owned target. No nuance, just pressure."""

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
        elif owner != player:
            targets.append((pid, x, y, ships))

    if not targets:
        return []

    moves = []
    for pid, mx, my, mships in mine:
        if mships < 2:
            continue
        nearest = min(targets, key=lambda t: (t[1] - mx) ** 2 + (t[2] - my) ** 2)
        angle = math.atan2(nearest[2] - my, nearest[1] - mx)
        moves.append([pid, angle, mships])
    return moves
