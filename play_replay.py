"""Render the Orbit Wars HTML viewer from an existing replay JSON.

Source can be:
  - a local path to env.toJSON() output (replay.json)
  - any http(s) URL that returns the replay JSON directly
  - a Kaggle episode URL (e.g. https://www.kaggle.com/episodes/12345 or any
    Kaggle page containing "episode-12345") -- the replay is fetched via
    the public EpisodeService.GetEpisodeReplay endpoint

Usage:
    python play_replay.py replay.json
    python play_replay.py https://www.kaggle.com/episodes/12345
    python play_replay.py replay.json --names alice bob --cinema
"""

import argparse
import json
import os
import re
import sys
import urllib.request
import webbrowser


KAGGLE_REPLAY_CDN = "https://www.kaggleusercontent.com/episodes/{episode_id}.json"


def _extract_episode_id(url):
    """Pull an episode id out of any Kaggle URL that references one.

    Handles, in priority order:
      - explicit query/fragment params: ?episodeId=NNN, &episodeId=NNN, #episodeId=NNN
      - dialog shapes: episodes-episode-NNN, episode-NNN
      - path shapes:   /episodes/NNN, /episode/NNN
    """
    patterns = [
        r"[?&#]episodeId=(\d+)",       # ...?episodeId=77422105 (incl. trailing #)
        r"episode-(\d+)",              # ...?dialog=episodes-episode-99
        r"/episodes?/(\d+)",           # /episodes/12345 or /episode/12345
    ]
    for pat in patterns:
        m = re.search(pat, url, re.IGNORECASE)
        if m:
            return int(m.group(1))
    return None


def _http_json(url, payload=None):
    headers = {
        "User-Agent": "orbit-wars-tooling/1.0",
        "Accept": "application/json",
    }
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = resp.read().decode("utf-8", errors="replace")
    return body


def _fetch_kaggle_episode(episode_id):
    # Kaggle serves public episode replays straight off their CDN as plain
    # JSON -- this is what the in-browser viewer pulls. No API key needed.
    url = KAGGLE_REPLAY_CDN.format(episode_id=episode_id)
    body = _http_json(url)
    return json.loads(body)


def load_replay(source):
    """Return the env_json dict for a path, URL, or Kaggle episode link."""
    if os.path.exists(source):
        with open(source, "r", encoding="utf-8") as f:
            return json.load(f)
    if source.startswith("http://") or source.startswith("https://"):
        episode_id = _extract_episode_id(source)
        if episode_id is not None:
            print(f"Fetching Kaggle episode {episode_id}...")
            return _fetch_kaggle_episode(episode_id)
        print(f"Fetching {source}...")
        return json.loads(_http_json(source))
    raise FileNotFoundError(f"Not a file and not a URL: {source}")


def _names_from_replay(env_json):
    """Best-effort bot names from the replay metadata."""
    info = env_json.get("info") or {}
    teams = info.get("TeamNames") or info.get("teamNames")
    if isinstance(teams, list) and teams:
        return [str(t) for t in teams]
    return None


def main():
    parser = argparse.ArgumentParser(
        description="Render Orbit Wars HTML viewer from an existing replay.")
    parser.add_argument("source",
                        help="Path to replay.json OR a Kaggle episode URL OR any URL serving the replay JSON")
    parser.add_argument("--names", nargs="+", default=None,
                        help="Override bot names (one per player)")
    parser.add_argument("--out", default="render.html",
                        help="Output HTML path (default render.html)")
    parser.add_argument("--no-open", action="store_true",
                        help="Don't open the browser")
    parser.add_argument("--cinema", action="store_true",
                        help="Open straight in cinema mode")
    args = parser.parse_args()

    env_json = load_replay(args.source)

    if not isinstance(env_json, dict) or "steps" not in env_json:
        sys.stderr.write(
            "Loaded data doesn't look like an Orbit Wars replay "
            "(missing 'steps' key).\n")
        sys.exit(1)

    names = args.names or _names_from_replay(env_json)

    from visualize import write_html
    abs_out = write_html(env_json, args.out, bot_names=names, auto_cinema=args.cinema)
    n_steps = len(env_json.get("steps", []))
    print(f"Loaded {n_steps} steps. Rendered to {abs_out}")
    if not args.no_open:
        webbrowser.open(f"file://{abs_out}")


if __name__ == "__main__":
    main()
