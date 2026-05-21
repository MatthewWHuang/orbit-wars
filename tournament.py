"""Run N matches of the same bot lineup in parallel and write a tabbed HTML viewer.

Usage:
    python tournament.py basic1 basic2 --matches 8 --workers 4
    python tournament.py basic1 random --matches 20 --steps 200
"""

import argparse
import contextlib
import io
import json
import logging
import multiprocessing as mp
import os
import sys
import time
import webbrowser


def _suppress_native_output():
    """Redirect fd 1/2 to devnull. Returns (saved_out_fd, saved_err_fd, devnull_fd)."""
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


BOTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bots")


def _resolve_bot(b):
    """Resolve a bot arg to a path. Falls back to ./bots/<name> if not found in cwd."""
    if b == "random":
        return b
    if b.endswith(".py"):
        return b if os.path.exists(b) else os.path.join(BOTS_DIR, b)
    direct = os.path.join(b, "main.py")
    if os.path.exists(direct):
        return direct
    fallback = os.path.join(BOTS_DIR, b, "main.py")
    if os.path.exists(fallback):
        return fallback
    return direct


def _normalize_bots(bots):
    return [_resolve_bot(b) for b in bots]


def _make_isolated_loader(kagent):
    """Wrap kaggle_environments.agent.get_last_callable to isolate bots'
    sys.modules and sys.path so same-named modules across bots don't collide."""
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
    """Import kaggle_environments while skipping every env except orbit_wars."""
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


def run_match(task):
    bots, seed, steps = task
    t0 = time.time()
    # Aggressively silence library logging in this worker.
    logging.disable(logging.WARNING)
    make = _import_make()
    cfg = {"seed": seed}
    if steps is not None:
        cfg["episodeSteps"] = steps
    env = make("orbit_wars", configuration=cfg, debug=True)
    # Capture bot stdout/stderr at the Python level (debug=True disables
    # kaggle's own capture). Stash the stderr lines into env.info['logs']
    # so the visualizer's per-step debug parser still has data to read.
    stderr_buf = io.StringIO()
    stdout_buf = io.StringIO()
    with contextlib.redirect_stderr(stderr_buf), contextlib.redirect_stdout(stdout_buf):
        env.run(bots)
    # Serialize to a plain-JSON string here so the parent doesn't need to
    # import any kaggle_environments classes to unpickle the result (which
    # would otherwise trigger the entire env loader, including OpenSpiel).
    env_json_str = json.dumps(env.toJSON(), default=str)
    return env_json_str, bots, seed, time.time() - t0


def main():
    parser = argparse.ArgumentParser(description="Run multiple Orbit Wars matches in parallel.")
    parser.add_argument("bots", nargs="+", help="Bots in the lineup (same for every match)")
    parser.add_argument("--matches", type=int, default=4, help="Number of matches to run (default 4)")
    parser.add_argument("--seed-base", type=int, default=42, help="First seed; matches use seed-base, +1, +2, ...")
    parser.add_argument("--workers", type=int, default=None, help="Parallel workers (default: min(matches, cpu_count))")
    parser.add_argument("--steps", type=int, default=None, help="Override episode step limit")
    parser.add_argument("--out", default="tournament.html", help="Output HTML path")
    parser.add_argument("--no-open", action="store_true", help="Do not open the browser")
    parser.add_argument("--cinema", action="store_true", help="Open the viewer in cinema mode")
    args = parser.parse_args()

    bots = _normalize_bots(list(args.bots))
    if len(bots) < 2 or len(bots) > 4:
        parser.error("Provide 2-4 bots.")

    tasks = [(bots, args.seed_base + i, args.steps) for i in range(args.matches)]
    workers = args.workers or min(args.matches, mp.cpu_count())

    print(f"Tournament: {' vs '.join(bots)}, {args.matches} matches, {workers} workers")
    t0 = time.time()
    results = [None] * args.matches

    with mp.Pool(workers) as pool:
        for i, res in enumerate(pool.imap_unordered(run_match, tasks)):
            env_json_str, _bots, seed, dt = res
            env_json = json.loads(env_json_str)
            idx = seed - args.seed_base
            results[idx] = (env_json, bots, seed)
            rewards = env_json["steps"][-1]
            summary = " | ".join(f"{i}:r={s.get('reward')}" for i, s in enumerate(rewards))
            print(f"  [{i+1}/{args.matches}] seed={seed} ({dt:.1f}s)  {summary}")

    print(f"All matches done in {time.time() - t0:.1f}s")

    from visualize import write_tournament_html
    matches_payload = [
        {"env": env_json, "bot_names": bot_names, "seed": seed}
        for env_json, bot_names, seed in results
    ]
    abs_out = write_tournament_html(matches_payload, args.out)
    print(f"Wrote {abs_out}")
    if not args.no_open:
        webbrowser.open(f"file://{abs_out}" + ("#cinema" if args.cinema else ""))


if __name__ == "__main__":
    mp.freeze_support()
    main()
