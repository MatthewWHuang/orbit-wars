"""Turtle: hoards ships until its garrison is overwhelming, then unleashes
massed strikes at the weakest non-owned planet. Stateless -- decides based on
current ship totals so it works correctly across multiple matches in one run."""

import math

ATTACK_THRESHOLD = 80  # total owned ships required before attacking


def agent(obs):
    if isinstance(obs, dict):
        player = obs.get("player", 0)
        raw_planets = obs.get("planets", [])
    else:
        player = obs.player
        raw_planets = obs.planets

    mine = []
    targets = []
    total_mine = 0
    for p in raw_planets:
        pid, owner, x, y, radius, ships, prod = p
        if owner == player:
            mine.append((pid, x, y, ships))
            total_mine += ships
        else:
            targets.append((pid, owner, x, y, ships))

    if not targets or total_mine < ATTACK_THRESHOLD:
        return []

    moves = []
    for pid, mx, my, mships in mine:
        if mships < 10:
            continue
        weakest = min(targets, key=lambda t: t[4])
        angle = math.atan2(weakest[3] - my, weakest[2] - mx)
        moves.append([pid, angle, max(1, mships - 1)])
    return moves
