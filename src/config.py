"""Central configuration. Every tunable number lives here — no magic numbers elsewhere.

Values that a user may reasonably want to change per machine (season, data dir,
cache TTL, proxy) can be overridden with environment variables or a ``.env`` file.
"""

from __future__ import annotations

import os
from pathlib import Path

try:  # python-dotenv is optional at runtime
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover
    pass

# ---------------------------------------------------------------------------
# Paths & season
# ---------------------------------------------------------------------------
PROJECT_ROOT: Path = Path(__file__).resolve().parents[1]
DATA_DIR: Path = PROJECT_ROOT / os.getenv("NBA_DATA_DIR", "data")
RAW_DIR: Path = DATA_DIR / "raw"
PROCESSED_DIR: Path = DATA_DIR / "processed"

SEASON: str = os.getenv("NBA_SEASON", "2026-27")
SEASON_TYPE: str = "Regular Season"

# ---------------------------------------------------------------------------
# Data fetching (nba_api / stats.nba.com)
# ---------------------------------------------------------------------------
CACHE_TTL_HOURS: float = float(os.getenv("NBA_CACHE_TTL_HOURS", "12"))
API_TIMEOUT_SECONDS: int = 30
API_MAX_RETRIES: int = 4
API_BACKOFF_BASE_SECONDS: float = 2.0  # sleeps 2, 4, 8, 16 ... between retries
API_MIN_INTERVAL_SECONDS: float = 0.8  # politeness gap between consecutive calls
API_PROXY: str | None = os.getenv("NBA_API_PROXY") or None
SCHEDULE_CDN_URL: str = "https://cdn.nba.com/static/json/staticData/scheduleLeagueV2.json"

# ---------------------------------------------------------------------------
# League settings
# ---------------------------------------------------------------------------
NUM_TEAMS: int = 12
ROSTER_SPOTS: int = 13

# The nine scoring categories. FG% and FT% are derived from makes/attempts.
COUNTING_CATS: list[str] = ["PTS", "REB", "AST", "STL", "BLK", "FG3M", "TOV"]
PCT_CATS: dict[str, tuple[str, str]] = {"FG_PCT": ("FGM", "FGA"), "FT_PCT": ("FTM", "FTA")}
CATEGORIES: list[str] = ["PTS", "REB", "AST", "STL", "BLK", "FG3M", "FG_PCT", "FT_PCT", "TOV"]
NEGATIVE_CATS: set[str] = {"TOV"}  # lower is better
CAT_LABELS: dict[str, str] = {
    "PTS": "PTS", "REB": "REB", "AST": "AST", "STL": "STL", "BLK": "BLK",
    "FG3M": "3PM", "FG_PCT": "FG%", "FT_PCT": "FT%", "TOV": "TO",
}

# Raw box-score columns kept in the tidy game-log table.
STAT_COLUMNS: list[str] = [
    "MIN", "PTS", "REB", "OREB", "DREB", "AST", "STL", "BLK", "TOV",
    "FGM", "FGA", "FG3M", "FG3A", "FTM", "FTA", "PF", "PLUS_MINUS",
]


def previous_season(season: str) -> str:
    """'2026-27' -> '2025-26'."""
    start = int(season[:4]) - 1
    return f"{start}-{str(start + 1)[-2:]}"


PRIOR_SEASON: str = previous_season(SEASON)

# ---------------------------------------------------------------------------
# Projections
# ---------------------------------------------------------------------------
# Stats projected per minute, then scaled by projected minutes. FG%/FT% are
# derived from projected makes / attempts so they stay internally consistent.
PROJECTED_STATS: list[str] = ["pts", "reb", "ast", "stl", "blk", "fg3m", "tov", "fgm", "fga", "ftm", "fta"]

# Look-back windows (calendar days before the projection date) and blend weights.
# "season" = every game this season before the projection date. Weights are normalised.
PROJ_WINDOW_DAYS: dict[str, int] = {"last7": 7, "last15": 15}
PROJ_WEIGHTS: dict[str, float] = {"last7": 0.20, "last15": 0.30, "season": 0.50}

# Shrinkage strengths (empirical-Bayes style pseudo-observations):
# season per-minute rate is shrunk toward a prior (last season's rate, or league
# average for rookies) as if the prior were worth this many minutes of play ...
SHRINK_SEASON_PRIOR_MINUTES: float = 300.0
# ... and each short window is shrunk toward the (shrunk) season rate.
SHRINK_RECENT_MINUTES: float = 60.0
# Same idea for minutes-per-game, measured in games.
SHRINK_SEASON_MPG_GAMES: float = 4.0
SHRINK_RECENT_MPG_GAMES: float = 2.0

# A player with no game in this many days is flagged inactive (likely injured / out of rotation).
INACTIVE_AFTER_DAYS: int = 10

# ---------------------------------------------------------------------------
# Z-score valuation
# ---------------------------------------------------------------------------
# Player pool the z-scores are measured against: every rostered player in the league.
ZSCORE_POOL_SIZE: int = NUM_TEAMS * ROSTER_SPOTS  # 156
# The pool is re-picked from the current top-N and the z-scores recomputed until it stops changing.
ZSCORE_MAX_ITER: int = 20
# Projection column(s) behind each category. Percentages use (makes, attempts).
CAT_COLUMNS: dict[str, str] = {
    "PTS": "pts", "REB": "reb", "AST": "ast", "STL": "stl", "BLK": "blk", "FG3M": "fg3m", "TOV": "tov",
}
PCT_COLUMNS: dict[str, tuple[str, str]] = {"FG_PCT": ("fgm", "fga"), "FT_PCT": ("ftm", "fta")}

# ---------------------------------------------------------------------------
# Decision tools (roster, start/sit, waivers, trades)
# ---------------------------------------------------------------------------
ROSTER_FILE: Path = DATA_DIR / "my_rosters.json"
MY_TEAM: str = "My Team"
OPPONENT: str = "Opponent"
OTHER_ROSTERED: str = "Other rostered players"  # everyone owned by the other 10 teams

WEEK_START_WEEKDAY: int = 0  # fantasy weeks run Monday (0) .. Sunday
ACTIVE_LINEUP_SLOTS: int = 10  # daily starting slots (positions ignored)

# Waiver "need" weighting. Each category gets weight
#     w = 2 / (1 + exp(team_z / WAIVER_NEED_SCALE))
# so a category where my team's summed z is very negative -> ~2, average -> 1,
# dominant -> ~0. Larger scale = flatter weights (closer to plain z-value ranking).
WAIVER_NEED_SCALE: float = 3.0
WAIVER_TOP_N: int = 25

# ---------------------------------------------------------------------------
# Monte Carlo matchup simulator
# ---------------------------------------------------------------------------
SIM_COUNT: int = 10_000
SIM_SEED: int | None = 42  # fixed for reproducible results; None = fresh randomness every run
# Game-to-game "role shock": every stat in a simulated game is scaled by a common
# Gamma(mean 1, CV) draw, fitted to the player's minutes variability in recent games.
# This makes counting stats over-dispersed (Gamma-Poisson) and correlated within a game.
SIM_RECENT_GAMES: int = 15
SIM_MINUTES_CV_DEFAULT: float = 0.25  # prior CV (league-typical)
SIM_MINUTES_CV_PRIOR_GAMES: float = 5.0  # shrinkage strength of the prior, in games
SIM_MINUTES_CV_MAX: float = 0.6
SIM_TIE_VALUE: float = 0.5  # a tied category counts as half a win
SIM_CLOSE_BAND: tuple[float, float] = (0.25, 0.75)  # win prob range flagged as "close / worth targeting"

# ---------------------------------------------------------------------------
# Deployment / offline mode
# ---------------------------------------------------------------------------
# stats.nba.com blocks many cloud IPs. With NBA_OFFLINE=1 the app never calls the
# API and only reads the committed parquet cache (set this on Streamlit Cloud).
OFFLINE: bool = os.getenv("NBA_OFFLINE", "0").lower() in ("1", "true", "yes")
# Disable writing rosters to disk (public demo deployments: rosters live in the browser session only).
ROSTER_SAVE_ENABLED: bool = os.getenv("NBA_ROSTER_SAVE", "1").lower() in ("1", "true", "yes")

# ---------------------------------------------------------------------------
# Backtest
# ---------------------------------------------------------------------------
REPORTS_DIR: Path = PROJECT_ROOT / os.getenv("NBA_REPORTS_DIR", "reports")
BACKTEST_SEASON: str = PRIOR_SEASON  # evaluate on last completed season
BACKTEST_WARMUP_DAYS: int = 14  # first projection date = season start + warm-up
BACKTEST_STEP_DAYS: int = 7  # make a projection every week ...
BACKTEST_HORIZON_DAYS: int = 7  # ... and score it on the following week's games
BACKTEST_MIN_MINUTES: float = 20.0  # only score fantasy-relevant players (projected MPG)

# ---------------------------------------------------------------------------
# Static portfolio site (Vercel)
# ---------------------------------------------------------------------------
SITE_DIR: Path = PROJECT_ROOT / os.getenv("NBA_SITE_DIR", "public")
SITE_TOP_N: int = 50
STREAMLIT_APP_URL: str = os.getenv("STREAMLIT_APP_URL", "")

# ---------------------------------------------------------------------------
# Advisor (plain-language roster recommendations in the web app)
# ---------------------------------------------------------------------------
ADVICE_SIM_COUNT: int = 3_000  # sims per what-if (each waiver move is re-simulated)
ADVICE_WAIVER_CANDIDATES: int = 6  # free agents tested per mode (season-long + this week)
ADVICE_MIN_GAIN: float = 0.5  # minimum need-weighted z gain before a move is suggested
ADVICE_TRADE_FAIR_TOL: float = 0.5  # partner may lose at most this much raw z (keeps offers realistic)
ADVICE_MAX_ITEMS: int = 3  # suggestions shown per section
ADVICE_SAFE_PROB: float = 0.85  # category win prob above this = safe, below 1 - this = out of reach
ADVICE_PUNT_THRESHOLD: float = -4.0  # team z below this in a category -> suggest considering a punt
