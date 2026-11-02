"""Build the Vercel web app into ``public/``.

    python -m app.static_site

The deployed site is a static, client-side app:

* ``public/data/app.json``: written here from the Python pipeline. It holds every
  setting the browser needs (exported from ``src/config.py``), each player's per-game
  projection and minutes variability, the schedule, the last week of box scores (for
  week-to-date matchup stats) and the backtest results.
* ``web/*``: hand-written HTML/CSS/JS, copied to ``public/``. ``web/core.js`` ports the
  valuation, decision and simulation maths; ``tests/test_web_parity.py`` checks it against
  the Python implementation.

Never downloads: it reads only the parquet cache, so it runs the same in CI, locally or offline.
"""

from __future__ import annotations

import hashlib
import json
import math
import shutil
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from src import config

REPO_URL = "https://github.com/NiKeT972/nba-9cat-optimizer"
WEB_DIR = config.PROJECT_ROOT / "web"
RECENT_DAYS = 8  # box scores shipped for week-to-date totals (a fantasy week is 7 days)


def web_config() -> dict:
    """Every tunable number the browser engine uses, straight from ``src/config.py``."""
    return {
        "categories": config.CATEGORIES,
        "labels": config.CAT_LABELS,
        "pool_size": config.ZSCORE_POOL_SIZE,
        "max_iter": config.ZSCORE_MAX_ITER,
        "need_scale": config.WAIVER_NEED_SCALE,
        "waiver_top_n": config.WAIVER_TOP_N,
        "slots": config.ACTIVE_LINEUP_SLOTS,
        "roster_spots": config.ROSTER_SPOTS,
        "num_teams": config.NUM_TEAMS,
        "week_start_weekday": config.WEEK_START_WEEKDAY,
        "sim_count": config.SIM_COUNT,
        "sim_seed": config.SIM_SEED,
        "cv_default": config.SIM_MINUTES_CV_DEFAULT,
        "tie_value": config.SIM_TIE_VALUE,
        "close_band": list(config.SIM_CLOSE_BAND),
        "inactive_days": config.INACTIVE_AFTER_DAYS,
        "proj_weights": config.PROJ_WEIGHTS,
        "shrink_minutes": config.SHRINK_SEASON_PRIOR_MINUTES,
        "backtest_min_minutes": config.BACKTEST_MIN_MINUTES,
    }


def _clean(v):
    """JSON-safe scalar (NaN -> None, numpy -> python, rounded floats keep the file small)."""
    if v is None:
        return None
    if isinstance(v, (bool,)):
        return v
    if hasattr(v, "item"):
        v = v.item()
    if isinstance(v, float):
        return None if math.isnan(v) else round(v, 5)
    return v


def export_data() -> dict:
    """Assemble app.json from the cached data. Sections are omitted when their data is missing."""
    from src.data.pipeline import load_game_logs, load_schedule
    from src.projections.project import project_players
    from src.simulation.matchup import minutes_cv

    out: dict = {"cfg": web_config(), "season": config.SEASON, "built": datetime.now(timezone.utc).isoformat(),
                 "repo": REPO_URL, "app_url": config.STREAMLIT_APP_URL or None}
    try:
        logs = load_game_logs(config.SEASON)
    except Exception as exc:  # noqa: BLE001
        print(f"No {config.SEASON} game logs ({exc}); building without player data.")
        logs = pd.DataFrame()

    if not logs.empty:
        try:
            prior = load_game_logs(config.PRIOR_SEASON)
        except Exception:  # noqa: BLE001
            prior = None
        through = logs["date"].max()
        as_of = through + pd.Timedelta(days=1)
        proj = project_players(logs, as_of=as_of, prior_logs=prior)
        cv = minutes_cv(logs, proj.index, as_of=as_of)
        cols = ["name", "team", "games", "min", "active", "days_since_game", *config.PROJECTED_STATS]
        out["players"] = [
            {"id": int(pid), **{c: _clean(r[c]) for c in cols}, "cv": _clean(cv[pid])}
            for pid, r in proj[cols].iterrows()
        ]
        out["through"] = through.strftime("%Y-%m-%d")
        out["as_of"] = as_of.strftime("%Y-%m-%d")
        recent = logs[logs["date"] > through - pd.Timedelta(days=RECENT_DAYS)]
        stat_cols = ["pts", "reb", "ast", "stl", "blk", "fg3m", "tov", "fgm", "fga", "ftm", "fta"]
        out["recent"] = [{"id": int(r.player_id), "d": r.date.strftime("%Y-%m-%d"),
                          **{c: _clean(getattr(r, c)) for c in stat_cols}} for r in recent.itertuples()]

    try:
        sched = load_schedule(config.SEASON)
        if not sched.empty:
            start = (pd.Timestamp(out.get("as_of", sched["date"].min())) - pd.Timedelta(days=7)).normalize()
            s = sched[sched["date"] >= start]
            out["schedule"] = [{"d": r.date.strftime("%Y-%m-%d"), "h": r.home_team, "a": r.away_team}
                               for r in s.itertuples()]
    except Exception as exc:  # noqa: BLE001
        print(f"No schedule ({exc}); start/sit and the simulator will be disabled.")

    bt = config.REPORTS_DIR / f"backtest_{config.BACKTEST_SEASON}.csv"
    if bt.exists():
        summary = pd.read_csv(bt, index_col=0)
        out["backtest"] = {
            "season": config.BACKTEST_SEASON,
            "rows": [{"cat": cat, **{k: _clean(v) for k, v in r.items()}} for cat, r in summary.iterrows()],
        }
    return out


def build() -> str:
    """Write public/ (index.html, assets, data/app.json) and return the index path."""
    config.OFFLINE = True  # never hit the API from the site build
    data = export_data()
    site = config.SITE_DIR
    (site / "data").mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"), allow_nan=False)  # NaN is not valid JSON
    (site / "data" / "app.json").write_text(payload, encoding="utf-8")

    version = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:10]
    for name in ["core.js", "app.js", "app.css"]:
        src = WEB_DIR / name
        version = hashlib.sha1((version + src.read_text(encoding="utf-8")).encode("utf-8")).hexdigest()[:10]
        shutil.copyfile(src, site / name)
    index = (WEB_DIR / "index.html").read_text(encoding="utf-8").replace("{{VERSION}}", version)
    out = site / "index.html"
    out.write_text(index, encoding="utf-8")  # explicit: Windows defaults to cp1252
    n = len(data.get("players", []))
    print(f"Built {out} with {n} players, {len(data.get('schedule', []))} scheduled games.")
    return str(out)


if __name__ == "__main__":
    build()
