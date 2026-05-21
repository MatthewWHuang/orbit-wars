import argparse
import contextlib
import logging
import os
import sys
import threading
import time
import webbrowser


@contextlib.contextmanager
def _suppress_output():
    sys.stdout.flush()
    sys.stderr.flush()
    devnull_fd = os.open(os.devnull, os.O_WRONLY)
    saved_out = os.dup(1)
    saved_err = os.dup(2)
    os.dup2(devnull_fd, 1)
    os.dup2(devnull_fd, 2)
    try:
        yield
    finally:
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
    return direct  # let the runner raise the usual not-found error


def main():
    parser = argparse.ArgumentParser(description="Run an Orbit Wars match.")
    parser.add_argument("bots", nargs="+", help="1 to 4 bot paths/names (e.g. main.py random)")
    parser.add_argument("--seed", type=int, default=42, help="Environment seed")
    parser.add_argument("--steps", type=int, default=None, help="Override episode step limit")
    parser.add_argument("--out", default="render.html", help="Path to write rendered HTML")
    parser.add_argument("--no-render", action="store_true", default=False, help="Skip writing the HTML render")
    parser.add_argument("--cinema", action="store_true", default=False, help="Open the viewer directly in cinema mode")
    args = parser.parse_args()

    bots = list(args.bots)
    if len(bots) > 4:
        parser.error("at most 4 bots are allowed")
    if len(bots) == 1:
        bots.append("random")
    bots = [_resolve_bot(b) for b in bots]

    logging.getLogger("kaggle_environments.envs.open_spiel_env.open_spiel_env").setLevel(logging.ERROR)
    with _suppress_output():
        # kaggle_environments imports every env in its envs/ dir on package init
        # (open_spiel especially is slow). Patch listdir so only orbit_wars loads.
        _real_listdir = os.listdir

        def _only_orbit_wars(path):
            items = _real_listdir(path)
            if "orbit_wars" in items:
                return ["orbit_wars"]
            return items

        os.listdir = _only_orbit_wars
        try:
            from kaggle_environments import make
            from kaggle_environments import agent as _kagent
        finally:
            os.listdir = _real_listdir

        # Two bots with same-named local modules (e.g. both have physics.py)
        # will collide in the global sys.modules. Wrap the loader: collect
        # each bot's bot-dir modules after build, then restore *only that
        # bot's* modules and its dir on sys.path around each act() call.
        _orig_get_last_callable = _kagent.get_last_callable

        def _isolated_get_last_callable(raw, fallback=None, path=None):
            if path is None:
                return _orig_get_last_callable(raw, fallback=fallback, path=path)
            bot_dir = os.path.dirname(os.path.abspath(path))
            baseline = set(sys.modules)
            inner = _orig_get_last_callable(raw, fallback=fallback, path=path)
            # Pull out modules that came from this bot's directory.
            bot_mods = {}
            for k in list(sys.modules):
                if k in baseline:
                    continue
                m = sys.modules.get(k)
                mfile = (getattr(m, "__file__", None) or "").replace("\\", "/")
                bdir = bot_dir.replace("\\", "/")
                if mfile.startswith(bdir + "/"):
                    bot_mods[k] = sys.modules.pop(k)

            inner_argcount = (
                inner.__code__.co_argcount if hasattr(inner, "__code__") else 2
            )

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

        _kagent.get_last_callable = _isolated_get_last_callable

        cfg = {"seed": args.seed}
        if args.steps is not None:
            cfg["episodeSteps"] = args.steps
        env = make("orbit_wars", configuration=cfg, debug=True)

    total_steps = env.configuration.episodeSteps
    print(f"Running {' vs '.join(bots)} (seed={args.seed}, up to {total_steps} steps)...")

    err = []
    def _runner():
        try:
            env.run(bots)
        except Exception as e:
            err.append(e)

    t = threading.Thread(target=_runner, daemon=True)
    t.start()
    start = time.time()
    last_reported = -1
    while t.is_alive():
        n = len(env.steps)
        if n // 25 != last_reported // 25 and n > 0:
            last_reported = n
            print(f"  step {n}/{total_steps}  ({time.time() - start:.1f}s)")
        t.join(timeout=0.5)
    t.join()
    if err:
        raise err[0]
    print(f"  done in {time.time() - start:.1f}s ({len(env.steps)} steps)")

    final = env.steps[-1]
    for i, s in enumerate(final):
        print(f"Player {i} ({bots[i]}): reward={s.reward}, status={s.status}")

    if not args.no_render:
        from visualize import write_html
        abs_out = write_html(env, args.out, bot_names=bots)
        print(f"Render written to {abs_out}")
        url = f"file://{abs_out}" + ("#cinema" if args.cinema else "")
        webbrowser.open(url)


if __name__ == "__main__":
    main()
