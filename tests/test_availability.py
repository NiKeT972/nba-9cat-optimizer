"""Tests for form lines, games missed and last-games tables."""

from __future__ import annotations

import pandas as pd
import pytest

from src.data.availability import form_lines, games_missed, last_games
from tests.test_projections import make_logs

AS_OF = pd.Timestamp("2026-11-20")


def schedule(team: str, dates) -> pd.DataFrame:
    return pd.DataFrame({"game_id": [f"g{i}" for i in range(len(dates))], "date": pd.to_datetime(dates),
                         "home_team": team, "away_team": "OPP", "home_team_id": 1, "away_team_id": 2})


def test_form_lines_use_only_the_window():
    logs = make_logs(1, "P", pd.date_range("2026-10-25", periods=10, freq="2D"), pts=[10.0] * 7 + [30.0] * 3)
    form = form_lines(logs, AS_OF, days=12)  # Nov 8 .. Nov 19 -> the last 3 games
    assert form.loc[1, "games"] == 3
    assert form.loc[1, "pts"] == pytest.approx(30.0)


def test_games_missed_counts_team_games_since_last_appearance():
    team_dates = pd.date_range("2026-11-01", periods=9, freq="2D")  # Nov 1 .. Nov 17
    logs = make_logs(1, "Hurt", team_dates[:5])  # played the first 5, missed the last 4
    logs = pd.concat([logs, make_logs(2, "Healthy", team_dates)])
    logs["team"] = "BOS"
    missed = games_missed(logs, schedule("BOS", team_dates), AS_OF)
    assert missed[1] == 4 and missed[2] == 0


def test_games_missed_without_schedule_is_zero():
    logs = make_logs(1, "P", pd.date_range("2026-11-01", periods=3))
    assert games_missed(logs, pd.DataFrame(), AS_OF).tolist() == [0]


def test_last_games_newest_first_and_capped():
    logs = make_logs(1, "P", pd.date_range("2026-11-01", periods=8), pts=list(range(8)))
    lg = last_games(logs, AS_OF, n=5)[1]
    assert len(lg) == 5 and lg["pts"].tolist() == [7, 6, 5, 4, 3]
