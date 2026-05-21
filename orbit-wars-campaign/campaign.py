"""Run an Orbit Wars campaign for a single bot through a graph of territories.

Each node in territories.json is one Orbit Wars match with its own seed and
engine config (boardSize, episodeSteps, sunRadius, cometSpeed, ...). The
campaign engine walks the graph from `start`, plays each unlocked territory
in turn, and ends on the first loss or when the boss is conquered.

Usage:
    python campaign.py --bot ../basic4/main.py
    python campaign.py --bot ../basic4/main.py --spec territories.json --out campaign.html
"""

import argparse
import contextlib
import io
import json
import logging
import os
import sys
import time
import webbrowser
from collections import deque


def _suppress_native_output():
    sys.stdout.flush()
    sys.stderr.flush()
    devnull_fd = os.open(os.devnull, os.O_WRONLY)
    saved_out = os.dup(1)
    saved_err = os.dup(2)
    os.dup2(devnull_fd, 1)
    os.dup2(devnull_fd, 2)
    return saved_out, saved_err, devnull_fd


def _restore_native_output(saved):
    saved_out, saved_err, devnull_fd = saved
    sys.stdout.flush()
    sys.stderr.flush()
    os.dup2(saved_out, 1)
    os.dup2(saved_err, 2)
    os.close(saved_out)
    os.close(saved_err)
    os.close(devnull_fd)


def _make_isolated_loader(kagent):
    """Two bots may have same-named local modules; isolate each bot's sys.modules
    around its act() call so they don't clobber each other globally."""
    orig = kagent.get_last_callable

    def isolated(raw, fallback=None, path=None):
        if path is None:
            return orig(raw, fallback=fallback, path=path)
        bot_dir = os.path.dirname(os.path.abspath(path))
        baseline = set(sys.modules)
        inner = orig(raw, fallback=fallback, path=path)
        bot_mods = {}
        for k in list(sys.modules):
            if k in baseline:
                continue
            m = sys.modules.get(k)
            mfile = (getattr(m, "__file__", None) or "").replace("\\", "/")
            bdir = bot_dir.replace("\\", "/")
            if mfile.startswith(bdir + "/"):
                bot_mods[k] = sys.modules.pop(k)
        inner_argcount = inner.__code__.co_argcount if hasattr(inner, "__code__") else 2

        def wrapper(observation, configuration):
            saved = {k: sys.modules.get(k) for k in bot_mods}
            for k, v in bot_mods.items():
                sys.modules[k] = v
            sys.path.insert(0, bot_dir)
            try:
                return inner(*([observation, configuration][:inner_argcount]))
            finally:
                try:
                    sys.path.remove(bot_dir)
                except ValueError:
                    pass
                for k, prev in saved.items():
                    if prev is None:
                        sys.modules.pop(k, None)
                    else:
                        sys.modules[k] = prev
        return wrapper

    kagent.get_last_callable = isolated


def _import_make():
    """kaggle_environments imports every env in its envs/ dir on init; patch
    listdir so only orbit_wars loads (big startup speedup)."""
    logging.getLogger("kaggle_environments.envs.open_spiel_env.open_spiel_env").setLevel(logging.ERROR)
    real_listdir = os.listdir

    def only_orbit_wars(path):
        items = real_listdir(path)
        return ["orbit_wars"] if "orbit_wars" in items else items

    saved = _suppress_native_output()
    try:
        os.listdir = only_orbit_wars
        try:
            from kaggle_environments import make
            from kaggle_environments import agent as kagent
        finally:
            os.listdir = real_listdir
        _make_isolated_loader(kagent)
        return make
    finally:
        _restore_native_output(saved)


def _resolve_bot(bot_arg, repo_root):
    """Accepts main.py path, a folder containing main.py, or 'random'."""
    if bot_arg == "random":
        return "random"
    if os.path.isabs(bot_arg) or os.path.exists(bot_arg):
        path = bot_arg
    else:
        path = os.path.join(repo_root, bot_arg)
    if os.path.isdir(path):
        path = os.path.join(path, "main.py")
    if not os.path.exists(path):
        raise FileNotFoundError(f"Bot not found: {bot_arg} (looked at {path})")
    return os.path.abspath(path)


def _resolve_npc(npc_name, repo_root):
    path = os.path.join(repo_root, "npc", npc_name, "main.py")
    if not os.path.exists(path):
        raise FileNotFoundError(f"NPC '{npc_name}' not found at {path}")
    return os.path.abspath(path)


def play_territory(make, bot_path, npc_path, config):
    """Run one Orbit Wars match. Returns (env_json_dict, bot_won, scores)."""
    env = make("orbit_wars", configuration=config, debug=True)
    stderr_buf = io.StringIO()
    stdout_buf = io.StringIO()
    with contextlib.redirect_stderr(stderr_buf), contextlib.redirect_stdout(stdout_buf):
        env.run([bot_path, npc_path])
    env_json = json.loads(json.dumps(env.toJSON(), default=str))
    final = env_json["steps"][-1]
    rewards = [s.get("reward") for s in final]
    # Bot is index 0, NPC is index 1. Higher reward wins; tie counts as loss.
    bot_r = rewards[0] if rewards[0] is not None else float("-inf")
    npc_r = rewards[1] if rewards[1] is not None else float("-inf")
    return env_json, bot_r > npc_r, rewards


def walk_campaign(spec, bot_path, repo_root, make):
    territories = spec["territories"]
    start = spec["start"]
    boss = spec.get("boss")

    conquered = set()
    unlocked = {start}
    results = []

    def pick_next():
        # Visit in territories.json declaration order, but defer the boss
        # until everything else is conquered (or exhausted).
        non_boss = [t for t in territories if t in unlocked and t not in conquered and t != boss]
        if non_boss:
            return non_boss[0]
        if boss and boss in unlocked and boss not in conquered:
            return boss
        return None

    print(f"\n=== {spec.get('name', 'Campaign')} ===")
    print(f"Hero: {os.path.relpath(bot_path, repo_root)}\n")

    while True:
        next_t = pick_next()
        if next_t is None:
            break
        t_spec = territories[next_t]
        npc_name = t_spec["npc"]
        npc_path = _resolve_npc(npc_name, repo_root)
        config = {"seed": t_spec["seed"]}
        config.update(t_spec.get("config", {}))

        is_boss = next_t == boss
        marker = "[BOSS] " if is_boss else ""
        print(f"{marker}Territory: {next_t}  (vs {npc_name}, seed={t_spec['seed']})")
        t0 = time.time()
        env_json, won, rewards = play_territory(make, bot_path, npc_path, config)
        dt = time.time() - t0
        outcome = "WON " if won else "LOST"
        print(f"  -> {outcome} in {dt:.1f}s  rewards={rewards}\n")
        results.append({
            "id": next_t, "spec": t_spec, "npc": npc_name,
            "env_json": env_json, "won": won, "rewards": rewards, "is_boss": is_boss,
        })

        if not won:
            print("Campaign ends: hero defeated.")
            break

        conquered.add(next_t)
        for n in t_spec.get("neighbors", []):
            if n in territories and n not in conquered:
                unlocked.add(n)
        unlocked.discard(next_t)

        if is_boss:
            print("Campaign complete: the throne is yours.")
            break

    return results


def build_payload(results, spec, bot_path, repo_root):
    """Adapt each match into the visualize.py multi-match payload format,
    encoding territory info into the per-match label."""
    from visualize import _build_match

    hero_name = os.path.basename(os.path.dirname(bot_path)) or os.path.basename(bot_path)
    matches = []
    for i, r in enumerate(results, start=1):
        t_id = r["id"]
        outcome = "WIN" if r["won"] else "LOSS"
        boss_tag = " [BOSS]" if r["is_boss"] else ""
        label = f"Ep {i}{boss_tag}: {t_id} ({outcome})  -  {hero_name} vs {r['npc']}"
        match = _build_match(
            r["env_json"],
            bot_names=[bot_path, _resolve_npc(r["npc"], repo_root)],
            label=label,
            seed=r["spec"]["seed"],
        )
        matches.append(match)
    return matches


def main():
    parser = argparse.ArgumentParser(description="Run an Orbit Wars campaign.")
    parser.add_argument("--bot", required=True, help="Hero bot path or folder (e.g. ../basic4)")
    parser.add_argument("--spec", default="territories.json", help="Campaign spec JSON")
    parser.add_argument("--out", default="campaign.html", help="Output HTML path")
    parser.add_argument("--no-open", action="store_true", help="Do not open the browser")
    parser.add_argument("--cinema", action="store_true", help="Open viewer in cinema mode")
    parser.add_argument("--ultra", action="store_true", help="Open viewer in ultra cinema mode")
    parser.add_argument("--save-state", action="store_true",
                        help="Write campaign results to state/last_run.json")
    args = parser.parse_args()

    repo_root = os.path.dirname(os.path.abspath(__file__))
    spec_path = args.spec if os.path.isabs(args.spec) else os.path.join(repo_root, args.spec)
    with open(spec_path, "r", encoding="utf-8") as f:
        spec = json.load(f)

    bot_path = _resolve_bot(args.bot, repo_root)
    make = _import_make()
    results = walk_campaign(spec, bot_path, repo_root, make)

    if args.save_state:
        state_path = os.path.join(repo_root, "state", "last_run.json")
        os.makedirs(os.path.dirname(state_path), exist_ok=True)
        summary = [{"id": r["id"], "npc": r["npc"], "won": r["won"],
                    "rewards": r["rewards"], "is_boss": r["is_boss"]} for r in results]
        with open(state_path, "w", encoding="utf-8") as f:
            json.dump({"campaign": spec.get("name"), "hero": bot_path,
                       "results": summary}, f, indent=2)
        print(f"State written to {state_path}")

    wins = sum(1 for r in results if r["won"])
    total = len(results)
    print(f"\nFinal: {wins}/{total} territories conquered.")

    from visualize import _write_multi
    matches = build_payload(results, spec, bot_path, repo_root)
    out_path = args.out if os.path.isabs(args.out) else os.path.join(repo_root, args.out)
    abs_out = _write_multi(out_path, matches)
    print(f"Wrote {abs_out}")

    if not args.no_open:
        anchor = "#ultra" if args.ultra else ("#cinema" if args.cinema else "")
        webbrowser.open(f"file://{abs_out}" + anchor)


if __name__ == "__main__":
    main()
