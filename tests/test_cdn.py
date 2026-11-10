"""Tests for the cdn.nba.com fallback source (schedule + box scores -> stats-shaped frames)."""

from __future__ import annotations

import pandas as pd
import pytest

from src.data import cdn, clean, fetch


def player(pid, name, minutes, **stats):
    base = {"points": 0, "reboundsTotal": 0, "reboundsOffensive": 0, "reboundsDefensive": 0, "assists": 0,
            "steals": 0, "blocks": 0, "turnovers": 0, "fieldGoalsMade": 0, "fieldGoalsAttempted": 0,
            "threePointersMade": 0, "threePointersAttempted": 0, "freeThrowsMade": 0, "freeThrowsAttempted": 0,
            "foulsPersonal": 0, "plusMinusPoints": 0}
    base.update(stats)
    return {"personId": pid, "name": name, "firstName": name.split()[0], "familyName": name.split()[-1],
            "played": "1" if minutes else "0", "statistics": {"minutes": minutes, **base}}


def boxscore(gid, home_players, away_players):
    return {"game": {"gameId": gid,
                     "homeTeam": {"teamId": 1610612738, "teamTricode": "BOS", "players": home_players},
                     "awayTeam": {"teamId": 1610612752, "teamTricode": "NYK", "players": away_players}}}


SCHEDULE_DOC = {"leagueSchedule": {"seasonYear": "2026-27", "gameDates": [
    {"gameDate": "11/01/2026 00:00:00", "games": [
        {"gameId": "0022600100", "gameDateEst": "2026-11-01T00:00:00Z", "weekNumber": 2, "gameStatus": 3,
         "homeTeam": {"teamId": 1610612738, "teamTricode": "BOS"}, "awayTeam": {"teamId": 1610612752, "teamTricode": "NYK"}}]},
    {"gameDate": "11/05/2026 00:00:00", "games": [
        {"gameId": "0022600150", "gameDateEst": "2026-11-05T00:00:00Z", "weekNumber": 3, "gameStatus": 3,
         "homeTeam": {"teamId": 1610612738, "teamTricode": "BOS"}, "awayTeam": {"teamId": 1610612752, "teamTricode": "NYK"}},
        {"gameId": "0022600151", "gameDateEst": "2026-11-05T00:00:00Z", "weekNumber": 3, "gameStatus": 3,
         "homeTeam": {"teamId": 1610612747, "teamTricode": "LAL"}, "awayTeam": {"teamId": 1610612744, "teamTricode": "GSW"}}]},
    {"gameDate": "11/12/2026 00:00:00", "games": [
        {"gameId": "0022600200", "gameDateEst": "2026-11-12T00:00:00Z", "weekNumber": 4, "gameStatus": 1,
         "homeTeam": {"teamId": 1610612738, "teamTricode": "BOS"}, "awayTeam": {"teamId": 1610612752, "teamTricode": "NYK"}}]},
]}}


@pytest.mark.parametrize("raw,expected", [("PT34M12.00S", 34.2), ("PT05M30.00S", 5.5), ("PT00M00.00S", 0.0),
                                          ("", 0.0), (None, 0.0), ("PT12M", 12.0)])
def test_iso_minutes(raw, expected):
    assert cdn.iso_minutes(raw) == pytest.approx(expected)


def test_schedule_frame_matches_stats_columns_and_cleans():
    sched = cdn.schedule_frame(SCHEDULE_DOC)
    tidy = clean.clean_schedule(sched)
    assert len(tidy) == 4
    assert set(tidy.columns) >= {"game_id", "date", "home_team", "away_team"}
    assert tidy.loc[tidy["game_id"] == "0022600150", "home_team"].item() == "BOS"


def test_boxscore_rows_survive_cleaning_like_stats_rows():
    doc = boxscore("0022600150",
                   [player(1, "Home Star", "PT36M30.00S", points=31, reboundsTotal=8, assists=6, fieldGoalsMade=11,
                           fieldGoalsAttempted=20, threePointersMade=3, freeThrowsMade=6, freeThrowsAttempted=7, turnovers=4),
                    player(2, "Bench Guy", "")],  # DNP
                   [player(3, "Away Big", "PT28M00.00S", reboundsTotal=12, blocks=3)])
    rows = pd.DataFrame(cdn.boxscore_rows(doc, "2026-11-05", "22026"))
    assert len(rows) == 2  # the DNP is skipped
    tidy = clean.clean_game_logs(rows)
    star = tidy[tidy["player_id"] == 1].iloc[0]
    assert star["min"] == pytest.approx(36.5) and star["pts"] == 31 and star["fga"] == 20
    assert star["is_home"] and star["opponent"] == "NYK" and star["team"] == "BOS"
    big = tidy[tidy["player_id"] == 3].iloc[0]
    assert not big["is_home"] and big["opponent"] == "BOS" and big["blk"] == 3


def cached_logs():
    rows = cdn.boxscore_rows(boxscore("0022600100", [player(1, "Home Star", "PT30M00.00S", points=20)], []),
                             "2026-11-01", "22026")
    return pd.DataFrame(rows)


def test_update_fetches_only_finished_missing_games():
    requested = []

    def fake_fetch(gid):
        requested.append(gid)
        return boxscore(gid, [player(1, "Home Star", "PT33M00.00S", points=25)], [player(3, "Away Big", "PT20M00.00S")])

    out = cdn.update_game_logs(cached_logs(), cdn.schedule_frame(SCHEDULE_DOC), today=pd.Timestamp("2026-11-10"), fetch=fake_fetch)
    assert sorted(requested) == ["0022600150", "0022600151"]  # not the cached game, not the unplayed one
    tidy = clean.clean_game_logs(out)
    assert tidy["game_id"].nunique() == 3
    assert tidy["date"].max() == pd.Timestamp("2026-11-05")


def test_update_tolerates_one_failure_but_not_all():
    def flaky(gid):
        if gid == "0022600151":
            raise ConnectionError("404")
        return boxscore(gid, [player(1, "Home Star", "PT33M00.00S")], [])

    out = cdn.update_game_logs(cached_logs(), cdn.schedule_frame(SCHEDULE_DOC), today=pd.Timestamp("2026-11-10"), fetch=flaky)
    assert set(out["GAME_ID"]) == {"0022600100", "0022600150"}

    def dead(gid):
        raise ConnectionError("blocked")

    with pytest.raises(RuntimeError):
        cdn.update_game_logs(cached_logs(), cdn.schedule_frame(SCHEDULE_DOC), today=pd.Timestamp("2026-11-10"), fetch=dead)


def test_nothing_new_returns_cache_unchanged():
    out = cdn.update_game_logs(cached_logs(), cdn.schedule_frame(SCHEDULE_DOC), today=pd.Timestamp("2026-11-02"),
                               fetch=lambda gid: pytest.fail("should not download"))
    assert len(out) == len(cached_logs())


def test_fetcher_falls_back_to_cdn_when_stats_is_blocked(tmp_path, monkeypatch):
    monkeypatch.setattr(fetch.config, "RAW_DIR", tmp_path)
    monkeypatch.setattr(fetch.config, "API_MIN_INTERVAL_SECONDS", 0)
    monkeypatch.setattr(fetch.config, "STRICT_DOWNLOADS", True)  # the CDN counts as a real download
    monkeypatch.setattr(fetch.config, "OFFLINE", False)
    season = fetch.config.SEASON
    cached_logs().to_parquet(tmp_path / f"player_game_logs_{season}.parquet")

    def blocked(*a, **k):
        raise TimeoutError("stats.nba.com read timed out")

    monkeypatch.setattr(fetch, "_download_player_game_logs", blocked)
    monkeypatch.setattr(fetch, "_download_schedule", blocked)
    monkeypatch.setattr(cdn, "download_schedule", lambda: cdn.schedule_frame(SCHEDULE_DOC))
    real_update = cdn.update_game_logs
    monkeypatch.setattr(cdn, "update_game_logs", lambda cached, sched: real_update(
        cached, sched, today=pd.Timestamp("2026-11-10"),
        fetch=lambda gid: boxscore(gid, [player(1, "Home Star", "PT33M00.00S", points=25)], [])))

    logs = fetch.fetch_player_game_logs(season, force=True)
    assert set(logs["GAME_ID"]) == {"0022600100", "0022600150", "0022600151"}
    games, _ = fetch.fetch_schedule(season, force=True)
    assert len(games) == 4
    # and the refreshed cache was written
    assert len(pd.read_parquet(tmp_path / f"player_game_logs_{season}.parquet")) == len(logs)


def test_past_seasons_never_use_the_cdn(tmp_path, monkeypatch):
    monkeypatch.setattr(fetch.config, "RAW_DIR", tmp_path)
    monkeypatch.setattr(fetch.config, "API_MIN_INTERVAL_SECONDS", 0)
    monkeypatch.setattr(fetch.config, "API_MAX_RETRIES", 0)
    monkeypatch.setattr(fetch.config, "OFFLINE", False)

    def blocked(*a, **k):
        raise TimeoutError("blocked")

    monkeypatch.setattr(fetch, "_download_player_game_logs", blocked)
    monkeypatch.setattr(cdn, "download_schedule", lambda: pytest.fail("CDN used for a past season"))
    monkeypatch.setattr(fetch, "call_with_retry", lambda fn: fn())
    with pytest.raises(TimeoutError):
        fetch.fetch_player_game_logs("2024-25", force=True)
