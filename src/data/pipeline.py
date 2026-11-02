"""Public entry points for the data layer.

Usage from the command line (refreshes the local cache)::

    python -m src.data.pipeline            # use cache if fresh
    python -m src.data.pipeline --force    # always re-download
    python -m src.data.pipeline --season 2025-26
"""

from __future__ import annotations

import argparse
import logging

import pandas as pd

from src import config
from src.data.clean import clean_game_logs, clean_schedule
from src.data.fetch import fetch_player_game_logs, fetch_schedule


def load_game_logs(season: str = config.SEASON, force: bool = False) -> pd.DataFrame:
    """Tidy player game logs for ``season`` (downloads only if the cache is stale).

    The cleaned table is also written to ``data/processed/game_logs_<season>.parquet``.
    """
    tidy = clean_game_logs(fetch_player_game_logs(season, force=force))
    config.PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    tidy.to_parquet(config.PROCESSED_DIR / f"game_logs_{season}.parquet", index=False)
    return tidy


def load_schedule(season: str = config.SEASON, force: bool = False) -> pd.DataFrame:
    """Tidy regular-season schedule for ``season`` (downloads only if the cache is stale)."""
    games, _weeks = fetch_schedule(season, force=force)
    tidy = clean_schedule(games)
    config.PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    tidy.to_parquet(config.PROCESSED_DIR / f"schedule_{season}.parquet", index=False)
    return tidy


def main() -> None:
    """CLI: refresh the cache and print a short summary."""
    parser = argparse.ArgumentParser(description="Fetch and cache NBA data.")
    parser.add_argument("--season", default=config.SEASON)
    parser.add_argument("--force", action="store_true", help="ignore cache and re-download")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    logs = load_game_logs(args.season, force=args.force)
    sched = load_schedule(args.season, force=args.force)
    print(f"Game logs : {len(logs):,} rows, {logs['player_id'].nunique()} players, "
          f"{logs['date'].min():%Y-%m-%d} -> {logs['date'].max():%Y-%m-%d}")
    print(f"Schedule  : {len(sched):,} regular-season games, "
          f"{sched['date'].min():%Y-%m-%d} -> {sched['date'].max():%Y-%m-%d}")


if __name__ == "__main__":
    main()
