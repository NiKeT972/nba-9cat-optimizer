"""Tests for the data layer: retry/back-off, parquet caching and cleaning."""

from __future__ import annotations

import os
import time

import pandas as pd
import pytest

from src.data import clean, fetch


# ---------------------------------------------------------------------------
# Fixtures shaped exactly like nba_api responses
# ---------------------------------------------------------------------------
def _raw_log_row(pid, name, team, gid, date, matchup, mins, **stats):
    base = {c: 0 for c in ["FGM", "FGA", "FG3M", "FG3A", "FTM", "FTA", "OREB", "DREB", "REB",
                           "AST", "STL", "BLK", "TOV", "PF", "PTS", "PLUS_MINUS"]}
    base.update(stats)
    return {"SEASON_ID": "22026", "PLAYER_ID": pid, "PLAYER_NAME": name, "TEAM_ID": 1610612700 + pid % 30,
            "TEAM_ABBREVIATION": team, "TEAM_NAME": team, "GAME_ID": gid, "GAME_DATE": date,
            "MATCHUP": matchup, "WL": "W", "MIN": mins, "FG_PCT": None, "FG3_PCT": None, "FT_PCT": None,
            "FANTASY_PTS": 0, "VIDEO_AVAILABLE": 1, **base}


@pytest.fixture
def raw_logs() -> pd.DataFrame:
    return pd.DataFrame([
        _raw_log_row(1, "A Guard", "BOS", "0022600001", "2026-10-21", "BOS vs. NYK", 34, PTS=25, FGM=9, FGA=18),
        _raw_log_row(1, "A Guard", "BOS", "0022600001", "2026-10-21", "BOS vs. NYK", 34, PTS=25, FGM=9, FGA=18),
        _raw_log_row(2, "B Big", "NYK", "0022600001", "2026-10-21", "NYK @ BOS", "28:30", REB=12),
        _raw_log_row(3, "C Bench", "NYK", "0022600001", "2026-10-21", "NYK @ BOS", 0),
    ])


@pytest.fixture
def raw_schedule() -> pd.DataFrame:
    rows = [
        ("0012600001", "2026-10-05T00:00:00Z", 0, 1, "BOS", 2, "NYK"),  # preseason
        ("0022600001", "2026-10-21T00:00:00Z", 1, 1, "BOS", 2, "NYK"),
        ("0022600002", "2026-10-23T00:00:00Z", 1, 2, "NYK", 3, "LAL"),
        ("0022600003", "2026-10-28T00:00:00Z", 2, 3, "LAL", 1, "BOS"),
    ]
    return pd.DataFrame(rows, columns=["gameId", "gameDateEst", "weekNumber", "homeTeam_teamId",
                                       "homeTeam_teamTricode", "awayTeam_teamId", "awayTeam_teamTricode"])


# ---------------------------------------------------------------------------
# Retry / caching
# ---------------------------------------------------------------------------
def test_call_with_retry_backs_off_exponentially(monkeypatch):
    monkeypatch.setattr(fetch.config, "API_MIN_INTERVAL_SECONDS", 0)
    sleeps: list[float] = []
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise TimeoutError("rate limited")
        return "ok"

    assert fetch.call_with_retry(flaky, max_retries=4, backoff_base=2, sleep=sleeps.append) == "ok"
    assert sleeps == [2, 4]


def test_call_with_retry_raises_after_max(monkeypatch):
    monkeypatch.setattr(fetch.config, "API_MIN_INTERVAL_SECONDS", 0)

    def always_fail():
        raise ConnectionError("down")

    with pytest.raises(ConnectionError):
        fetch.call_with_retry(always_fail, max_retries=2, backoff_base=1, sleep=lambda s: None)


def test_cached_fetch_uses_cache_then_refreshes_when_stale(tmp_path, monkeypatch):
    monkeypatch.setattr(fetch.config, "API_MIN_INTERVAL_SECONDS", 0)
    path = tmp_path / "x.parquet"
    calls = {"n": 0}

    def dl():
        calls["n"] += 1
        return pd.DataFrame({"v": [calls["n"]]})

    assert fetch.cached_fetch(path, dl, ttl_hours=1)["v"].iloc[0] == 1
    assert fetch.cached_fetch(path, dl, ttl_hours=1)["v"].iloc[0] == 1  # cache hit
    assert calls["n"] == 1

    old = time.time() - 2 * 3600
    os.utime(path, (old, old))
    assert fetch.cached_fetch(path, dl, ttl_hours=1)["v"].iloc[0] == 2  # stale -> refetch


def test_cached_fetch_falls_back_to_stale_cache_on_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(fetch.config, "API_MIN_INTERVAL_SECONDS", 0)
    monkeypatch.setattr(fetch.config, "API_MAX_RETRIES", 0)
    path = tmp_path / "x.parquet"
    pd.DataFrame({"v": [7]}).to_parquet(path)

    def broken():
        raise ConnectionError("offline")

    monkeypatch.setattr(fetch, "call_with_retry", lambda fn: fn())
    assert fetch.cached_fetch(path, broken, force=True)["v"].iloc[0] == 7


# ---------------------------------------------------------------------------
# Cleaning
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("raw,expected", [(34, 34.0), ("28:30", 28.5), ("", 0.0), (None, 0.0), (12.25, 12.25)])
def test_parse_minutes(raw, expected):
    assert clean.parse_minutes(raw) == pytest.approx(expected)


def test_clean_game_logs(raw_logs):
    tidy = clean.clean_game_logs(raw_logs)
    assert list(tidy.columns) == clean.GAME_LOG_COLUMNS
    assert len(tidy) == 2  # duplicate dropped, DNP dropped
    guard = tidy[tidy["player_id"] == 1].iloc[0]
    assert guard["is_home"] and guard["opponent"] == "NYK"
    assert guard["fgm"] == 9 and guard["fga"] == 18
    big = tidy[tidy["player_id"] == 2].iloc[0]
    assert not big["is_home"] and big["opponent"] == "BOS"
    assert big["min"] == pytest.approx(28.5)
    assert pd.api.types.is_datetime64_any_dtype(tidy["date"])


def test_clean_game_logs_empty():
    assert list(clean.clean_game_logs(pd.DataFrame()).columns) == clean.GAME_LOG_COLUMNS


def test_clean_schedule_drops_preseason(raw_schedule):
    sched = clean.clean_schedule(raw_schedule)
    assert len(sched) == 3
    assert sched["game_id"].str.startswith("002").all()


def test_games_between(raw_schedule):
    sched = clean.clean_schedule(raw_schedule)
    counts = clean.games_between(sched, "2026-10-20", "2026-10-26")
    assert counts.to_dict() == {"BOS": 1, "LAL": 1, "NYK": 2}


def test_offline_mode_never_downloads(tmp_path, monkeypatch):
    monkeypatch.setattr(fetch.config, "OFFLINE", True)
    path = tmp_path / "x.parquet"
    pd.DataFrame({"v": [1]}).to_parquet(path)
    old = time.time() - 999 * 3600
    os.utime(path, (old, old))

    def must_not_run():
        raise AssertionError("downloaded in offline mode")

    assert fetch.cached_fetch(path, must_not_run, force=True)["v"].iloc[0] == 1
    with pytest.raises(FileNotFoundError):
        fetch.cached_fetch(tmp_path / "missing.parquet", must_not_run)


def test_files_are_written_as_utf8_regardless_of_platform_default(tmp_path, monkeypatch):
    """Windows defaults to cp1252; every text write in the project must pass encoding='utf-8'."""
    import pathlib

    original = pathlib.Path.write_text

    def strict(self, data, encoding=None, *args, **kwargs):
        assert encoding == "utf-8", f"write_text without encoding='utf-8' for {self.name}"
        return original(self, data, encoding, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "write_text", strict)
    from src.data import roster
    roster.save_rosters({"My Team": [1]}, names={1: "Nikola Jokić"}, path=tmp_path / "r.json")
    assert roster.load_rosters(tmp_path / "r.json") == {"My Team": [1]}
    assert "Jokić" in (tmp_path / "r.json").read_text(encoding="utf-8")


def test_clean_schedule_drops_games_with_unknown_teams(raw_schedule):
    tbd = pd.DataFrame([("0022601201", "2026-12-04T00:00:00Z", 7, 0, None, 0, None)], columns=raw_schedule.columns)
    sched = clean.clean_schedule(pd.concat([raw_schedule, tbd], ignore_index=True))
    assert "0022601201" not in set(sched["game_id"])
    assert sched["home_team"].notna().all() and sched["away_team"].notna().all()


def test_strict_mode_raises_instead_of_using_stale_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(fetch.config, "STRICT_DOWNLOADS", True)
    monkeypatch.setattr(fetch.config, "OFFLINE", False)
    path = tmp_path / "x.parquet"
    pd.DataFrame({"v": [7]}).to_parquet(path)

    def broken():
        raise ConnectionError("blocked")

    monkeypatch.setattr(fetch, "call_with_retry", lambda fn: fn())
    with pytest.raises(ConnectionError):
        fetch.cached_fetch(path, broken, force=True)
