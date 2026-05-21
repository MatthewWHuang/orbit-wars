# Orbit Wars Tooling

Local infrastructure for developing, testing, and visualizing bots for the [Orbit Wars](ORBIT_WARS_README.md) Kaggle competition — a real-time strategy game where bots conquer planets orbiting a sun in 2D continuous space.

This repo contains the **match runner, visualizer, tournament harness, single-file bundler, source-to-source optimizer, and campaign system**. It does **not** ship competitive bots — only a minimal example (`nearest_planet_sniper/`) so you can run something out of the box.

For the full game rules, observation/action formats, and config knobs, see [ORBIT_WARS_README.md](ORBIT_WARS_README.md). For a step-by-step walkthrough of writing and submitting a bot to Kaggle, see [agents.md](agents.md).

---

## Quick Start

```bash
pip install "kaggle-environments>=1.28.0"

# Watch a match: example bot vs random, opens in your browser
python basic_run.py nearest_planet_sniper random

# Two bots from a bots/ folder (see "Adding your own bots" below)
python basic_run.py my_bot another_bot --seed 7

# 4-player free-for-all
python basic_run.py bot_a bot_b bot_c bot_d
```

Each match writes a self-contained HTML viewer to `render.html` and opens it. Append `--cinema` to open straight in cinema mode (full-screen, no controls).

---

## What's in here

| File / folder | Purpose |
|---|---|
| [basic_run.py](basic_run.py) | Run one match. Writes an HTML viewer and opens it. |
| [tournament.py](tournament.py) | Run N matches of the same lineup in parallel, tabbed HTML viewer. |
| [visualize.py](visualize.py) | Generate the self-contained HTML viewer from an `env.toJSON()` dump. |
| [bundle.py](bundle.py) | Bundle a multi-file bot into a single `submission.py` for Kaggle. |
| [optimize.py](optimize.py) | Source-to-source optimizer (function inlining + loop unrolling) for bundled submissions. |
| [nearest_planet_sniper/](nearest_planet_sniper/) | Minimal example bot — sends ships to the nearest unowned planet when it can capture. |
| [orbit-wars-campaign/](orbit-wars-campaign/) | Single-player campaign mode: walk a graph of territories, each a custom-configured match against an NPC. |
| [bots/](bots/) | (Gitignored) Your private bots live here. The runner falls back to this dir for bot lookups. |
| [ORBIT_WARS_README.md](ORBIT_WARS_README.md) | Full game rules and observation/action reference. |
| [agents.md](agents.md) | Bot authoring guide + Kaggle submission walkthrough. |

---

## The runner — `basic_run.py`

```
python basic_run.py BOT [BOT ...] [--seed N] [--steps N] [--out PATH] [--no-render] [--cinema]
```

Accepts 1–4 bots. A bot arg can be:

- `random` — built-in random-action bot
- A path to a `.py` file — used directly as the agent
- A bare name like `basic4` — resolved as `./basic4/main.py` first, then `./bots/basic4/main.py`

The runner handles two awkward parts of running multiple bots in one Python process:

- **Slow Kaggle imports.** `kaggle_environments` walks its `envs/` directory at import and eagerly loads every environment (open_spiel is especially slow). We monkey-patch `os.listdir` during import so only `orbit_wars` is discovered.
- **Module name collisions.** If two bots both have a `physics.py`, the second one would clobber the first in `sys.modules`. The runner snapshots each bot's modules after load and swaps the right set back onto `sys.modules` (and onto `sys.path`) around each `act()` call, so each bot sees its own files.

Output: `render.html` (or `--out path`) — open it in any browser, no server needed. Includes playback controls, planet trails, per-player resource graphs, and any per-step JSON debug data your bot prints to stderr.

---

## The visualizer — `visualize.py`

`visualize.write_html(env, out_path, bot_names=...)` reduces `env.toJSON()` to the minimal per-step state (planets, fleets, comets, rewards, actions) and inlines it into a single HTML file with a vanilla-JS viewer. No build step, no external assets — works offline, easy to share, easy to embed.

The viewer also parses any line from your agent's stderr that looks like JSON and surfaces it in a per-agent debug panel. Useful for inspecting your bot's internal state turn-by-turn:

```python
import json, sys
def agent(obs):
    ...
    print(json.dumps({"plan": "rush_top_left", "target": 5}), file=sys.stderr)
    return moves
```

---

## The tournament harness — `tournament.py`

```
python tournament.py BOT BOT [BOT BOT] --matches N [--workers W] [--steps N]
```

Runs N matches of the same lineup in parallel worker processes (default workers = `cpu_count() // 2`), each with a different seed. Produces a tabbed HTML viewer where you can flip between matches and see aggregate win rates.

Same bot-resolution rules as `basic_run.py`.

---

## Bundling for Kaggle — `bundle.py`

Kaggle's submission format wants a single `main.py`. If your bot is split across files (`main.py`, `physics.py`, `planning.py`, ...), `bundle.py` embeds the helper modules as strings and registers them in `sys.modules` at import time, so `from physics import fleet_speed` keeps working unchanged inside the bundle.

```bash
python bundle.py my_bot                       # writes my_bot/submission.py
python bundle.py my_bot -o out.py
python bundle.py my_bot --submit -m "v3"      # bundle + kaggle competitions submit
```

---

## Optimizing bundled submissions — `optimize.py`

Runs two source-to-source passes on a bundled `submission.py`, alternating to a fixpoint:

1. **Inlining.** Single-`return` functions (and linear "assignments + final return" bodies, with early-return guards turned into ternaries) get expanded at call sites. Cross-module inlining is allowed when the body uses only safe names (parameters, `math.*`, builtins, module-level literal constants).
2. **Unrolling.** `for x in range(...)` and `for x in <literal-seq>` loops whose bounds resolve to concrete integers are expanded. `# @unroll N` for partial unrolling, `# @nounroll` to skip a loop, `# @noinline` to skip a function.

```bash
python optimize.py my_bot/submission.py
python optimize.py my_bot/submission.py -o my_bot/submission_opt.py --verbose
```

Useful when Kaggle's per-turn timeout is tight and you want to remove function-call and loop overhead without hand-rewriting.

---

## Campaign mode — `orbit-wars-campaign/`

A single-player progression mode. You point it at one bot and it walks a graph of territories defined in [territories.json](orbit-wars-campaign/territories.json):

```bash
cd orbit-wars-campaign
python campaign.py --bot ../bots/my_bot/main.py
```

Each territory is a match with its own seed and engine config (`boardSize`, `episodeSteps`, `sunRadius`, `cometSpeed`, ...) against an NPC opponent picked from [orbit-wars-campaign/npc/](orbit-wars-campaign/npc/) (`sniper`, `swarmer`, `turtle`). The run ends on your first loss or when you beat the boss. Output is a `campaign.html` viewer with the graph, lore, and embedded per-match playback.

Useful for stress-testing a bot across varied configurations rather than always running the default 100×100 / 500-step board.

---

## Adding your own bots

The default layout is:

```
bots/                # gitignored in this repo; init it as your own private repo
  my_bot/
    main.py          # def agent(observation, configuration) -> [[from_id, angle, ships], ...]
    physics.py       # any helper modules you want
    planning.py
```

A bot is just a directory containing `main.py` with an `agent` function. See [nearest_planet_sniper/main.py](nearest_planet_sniper/main.py) for the smallest possible example, and [agents.md](agents.md) for a full authoring walkthrough.

Once your bot is in `bots/my_bot/`, you can run it by bare name:

```bash
python basic_run.py my_bot random
python tournament.py my_bot another_bot --matches 16
python bundle.py bots/my_bot --submit -m "first try"
```

---

## License

MIT — see [LICENSE](LICENSE) if/when added.

Orbit Wars itself is a competition environment shipped in [kaggle-environments](https://github.com/Kaggle/kaggle-environments). This repo is independent tooling around it.
