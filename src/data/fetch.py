"""Download raw data from stats.nba.com via ``nba_api`` with retry + parquet caching.

Two raw tables are produced:

* ``player_game_logs_<season>.parquet`` – one row per player per game (LeagueGameLog, P mode).
  A single request returns the whole league, so a full refresh is one API call.
* ``schedule_<season>.parquet`` – every game of the season (ScheduleLeagueV2), plus
  ``schedule_weeks_<season>.parquet`` with the NBA's Monday–Sunday week boundaries.

Every download goes through :func:`cached_fetch`, which returns the cached parquet
when it is younger than ``config.CACHE_TTL_HOURS`` and otherwise calls the API
through :func:`call_with_retry` (sleep + exponential back-off).
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Callable, TypeVar

import pandas as pd

from src import config

logger = logging.getLogger(__name__)
T = TypeVar("T")

_last_call_ts: float = 0.0


# ---------------------------------------------------------------------------
# Generic helpers
# ---------------------------------------------------------------------------
def _throttle() -> None:
    """Sleep so consecutive API calls are at least ``API_MIN_INTERVAL_SECONDS`` apart."""
    global _last_call_ts
    wait = config.API_MIN_INTERVAL_SECONDS - (time.monotonic() - _last_call_ts)
    if wait > 0:
        time.sleep(wait)
    _last_call_ts = time.monotonic()


def call_with_retry(
    fn: Callable[[], T],
    max_retries: int = config.API_MAX_RETRIES,
    backoff_base: float = config.API_BACKOFF_BASE_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    """Call ``fn`` and retry with exponential back-off on any exception.

    stats.nba.com rate-limits aggressively and often just times out instead of
    returning 429, so every exception is treated as retryable.

    Args:
        fn: Zero-argument callable performing one API request.
        max_retries: Retries after the first attempt (total attempts = max_retries + 1).
        backoff_base: First sleep in seconds; doubles every retry (2, 4, 8, 16 ...).
        sleep: Injected sleep function (lets tests run instantly).

    Returns:
        Whatever ``fn`` returns.

    Raises:
        The last exception if every attempt fails.
    """
    for attempt in range(max_retries + 1):
        try:
            _throttle()
            return fn()
        except Exception as exc:  # noqa: BLE001 - network layer raises many types
            if attempt == max_retries:
                raise
            delay = backoff_base * (2**attempt)
            logger.warning("API call failed (%s). Retry %d/%d in %.0fs", exc, attempt + 1, max_retries, delay)
            sleep(delay)
    raise RuntimeError("unreachable")  # pragma: no cover


def is_fresh(path: Path, ttl_hours: float = config.CACHE_TTL_HOURS) -> bool:
    """Return True if ``path`` exists and was modified less than ``ttl_hours`` ago."""
    return path.exists() and (time.time() - path.stat().st_mtime) < ttl_hours * 3600


def cached_fetch(
    path: Path,
    fetch_fn: Callable[[], pd.DataFrame],
    force: bool = False,
    ttl_hours: float = config.CACHE_TTL_HOURS,
) -> pd.DataFrame:
    """Return the parquet at ``path`` if fresh, otherwise download, cache and return it.

    If the download fails but a stale cache exists, the stale cache is returned
    (with a warning) so the app keeps working offline.

    Args:
        path: Parquet cache location.
        fetch_fn: Callable that downloads and returns a DataFrame.
        force: Ignore the cache and always download.
        ttl_hours: Cache lifetime.
    """
    if not force and is_fresh(path, ttl_hours):
        logger.info("Cache hit: %s", path.name)
        return pd.read_parquet(path)
    try:
        df = call_with_retry(fetch_fn)
    except Exception:
        if path.exists():
            logger.warning("Download failed; falling back to stale cache %s", path.name)
            return pd.read_parquet(path)
        raise
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)
    logger.info("Cached %d rows -> %s", len(df), path)
    return df


# ---------------------------------------------------------------------------
# Endpoint-specific fetchers
# ---------------------------------------------------------------------------
def _download_player_game_logs(season: str) -> pd.DataFrame:
    from nba_api.stats.endpoints import leaguegamelog

    resp = leaguegamelog.LeagueGameLog(
        season=season,
        season_type_all_star=config.SEASON_TYPE,
        player_or_team_abbreviation="P",
        proxy=config.API_PROXY,
        timeout=config.API_TIMEOUT_SECONDS,
    )
    return resp.get_data_frames()[0]


def _download_schedule(season: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    from nba_api.stats.endpoints import scheduleleaguev2

    resp = scheduleleaguev2.ScheduleLeagueV2(
        season=season, proxy=config.API_PROXY, timeout=config.API_TIMEOUT_SECONDS
    )
    return resp.season_games.get_data_frame(), resp.season_weeks.get_data_frame()


def fetch_player_game_logs(season: str = config.SEASON, force: bool = False) -> pd.DataFrame:
    """Raw league-wide player game logs for ``season`` (cached).

    Completed past seasons never change, so their cache never expires.
    """
    path = config.RAW_DIR / f"player_game_logs_{season}.parquet"
    ttl = config.CACHE_TTL_HOURS if season == config.SEASON else float("inf")
    return cached_fetch(path, lambda: _download_player_game_logs(season), force=force, ttl_hours=ttl)


def fetch_schedule(season: str = config.SEASON, force: bool = False) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Raw season schedule and week table for ``season`` (both cached).

    Returns:
        ``(games, weeks)`` DataFrames exactly as returned by ScheduleLeagueV2.
    """
    games_path = config.RAW_DIR / f"schedule_{season}.parquet"
    weeks_path = config.RAW_DIR / f"schedule_weeks_{season}.parquet"
    if not force and is_fresh(games_path) and weeks_path.exists():
        return pd.read_parquet(games_path), pd.read_parquet(weeks_path)

    cache: dict[str, pd.DataFrame] = {}

    def _games() -> pd.DataFrame:
        cache["games"], cache["weeks"] = _download_schedule(season)
        return cache["games"]

    games = cached_fetch(games_path, _games, force=True)
    if "weeks" in cache:
        cache["weeks"].to_parquet(weeks_path, index=False)
        weeks = cache["weeks"]
    else:  # download failed and we fell back to stale cache
        weeks = pd.read_parquet(weeks_path) if weeks_path.exists() else pd.DataFrame()
    return games, weeks
