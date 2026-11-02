"""Weekly decision tools: start/sit, waiver ranking and trade analysis.

All functions take ``valued``: the player table returned by
:func:`src.valuation.zscores.value_players` (projections + ``z_<CAT>`` + ``z_total``),
indexed by player_id.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

import pandas as pd

from src import config
from src.data.clean import team_games
from src.valuation.team import category_need_weights, team_category_z, team_totals
from src.valuation.zscores import normalize_punt

Z_COLS = [f"z_{c}" for c in config.CATEGORIES]


# ---------------------------------------------------------------------------
# Schedule helpers
# ---------------------------------------------------------------------------
def week_window(today: pd.Timestamp | str, include_today: bool = True) -> tuple[pd.Timestamp, pd.Timestamp]:
    """Remaining dates of the fantasy week containing ``today``.

    Returns:
        ``(first_day, last_day)`` inclusive: today (or tomorrow) through the week's last day.
    """
    today = pd.Timestamp(today).normalize()
    days_into_week = (today.weekday() - config.WEEK_START_WEEKDAY) % 7
    week_end = today + pd.Timedelta(days=6 - days_into_week)
    start = today if include_today else today + pd.Timedelta(days=1)
    return start, week_end


def player_game_dates(valued: pd.DataFrame, schedule: pd.DataFrame, start, end) -> pd.DataFrame:
    """One row per (player, scheduled game date) in ``[start, end]``, based on each player's team."""
    tg = team_games(schedule)
    tg = tg[(tg["date"] >= pd.Timestamp(start)) & (tg["date"] <= pd.Timestamp(end))]
    players = valued[["team"]].reset_index()
    return players.merge(tg[["team", "date", "opponent"]], on="team", how="inner")


def games_remaining(valued: pd.DataFrame, schedule: pd.DataFrame, start, end) -> pd.Series:
    """Games each player's team plays in ``[start, end]``, indexed by player_id (0 if none)."""
    pg = player_game_dates(valued, schedule, start, end)
    return pg.groupby("player_id").size().reindex(valued.index, fill_value=0).astype(int)


# ---------------------------------------------------------------------------
# Start / sit
# ---------------------------------------------------------------------------
def start_sit(
    valued: pd.DataFrame,
    my_ids: Iterable[int],
    schedule: pd.DataFrame,
    today: pd.Timestamp | str,
    include_today: bool = True,
    slots: int = config.ACTIVE_LINEUP_SLOTS,
) -> pd.DataFrame:
    """Weekly value of each rostered player = per-game z-value x games remaining.

    Also simulates daily lineups: on each remaining day the ``slots`` highest per-game
    z-values among players with a game are started. ``games_started`` and
    ``weekly_value_started`` reflect that cap (positions are not modelled).

    Returns:
        One row per rostered player with ``name, team, active, z_total, games_left,
        weekly_value, games_started, weekly_value_started`` and per-category weekly z,
        sorted by ``weekly_value_started``.
    """
    ids = [i for i in my_ids if i in valued.index]
    roster = valued.loc[ids]
    start, end = week_window(today, include_today)

    games = games_remaining(roster, schedule, start, end)
    pg = player_game_dates(roster, schedule, start, end)
    pg["z_total"] = pg["player_id"].map(roster["z_total"])
    pg["day_rank"] = pg.groupby("date")["z_total"].rank(ascending=False, method="first")
    started = pg[pg["day_rank"] <= slots].groupby("player_id").size().reindex(roster.index, fill_value=0)

    out = roster[["name", "team", "active", "z_total"]].copy()
    out["games_left"] = games
    out["weekly_value"] = out["z_total"] * out["games_left"]
    out["games_started"] = started.astype(int)
    out["benched_games"] = out["games_left"] - out["games_started"]
    out["weekly_value_started"] = out["z_total"] * out["games_started"]
    for cat in config.CATEGORIES:
        out[f"wk_{cat}"] = roster[f"z_{cat}"] * out["games_started"]
    return out.sort_values("weekly_value_started", ascending=False)


def daily_lineups(
    valued: pd.DataFrame,
    my_ids: Iterable[int],
    schedule: pd.DataFrame,
    today: pd.Timestamp | str,
    include_today: bool = True,
    slots: int = config.ACTIVE_LINEUP_SLOTS,
) -> pd.DataFrame:
    """Day-by-day start/bench table for the rest of the week.

    Returns:
        Long table ``date, player_id, name, opponent, z_total, start`` (``start`` True for the
        top ``slots`` per day by per-game z-value).
    """
    ids = [i for i in my_ids if i in valued.index]
    roster = valued.loc[ids]
    start, end = week_window(today, include_today)
    pg = player_game_dates(roster, schedule, start, end)
    pg["name"] = pg["player_id"].map(roster["name"])
    pg["z_total"] = pg["player_id"].map(roster["z_total"])
    pg = pg.sort_values(["date", "z_total"], ascending=[True, False])
    pg["start"] = pg.groupby("date").cumcount() < slots
    return pg[["date", "player_id", "name", "team", "opponent", "z_total", "start"]].reset_index(drop=True)


# ---------------------------------------------------------------------------
# Waivers
# ---------------------------------------------------------------------------
def rank_waivers(
    valued: pd.DataFrame,
    my_ids: Iterable[int],
    rostered: Iterable[int],
    punt: Iterable[str] | None = None,
    games_left: pd.Series | None = None,
    only_active: bool = True,
    top_n: int | None = config.WAIVER_TOP_N,
) -> pd.DataFrame:
    """Rank free agents by how much they help *my weakest categories*.

    1. Sum my roster's per-category z to find where I am weak.
    2. Turn that into need weights (weak ~2, average 1, strong ~0, punted 0).
    3. ``fit = sum(need_weight * player_z)`` per free agent.
    4. Pair each free agent with my lowest-fit player as the drop: ``net_gain = fit - drop_fit``.

    Args:
        valued: Player table with z-scores.
        my_ids: My roster.
        rostered: Everyone on any roster (mine included); everyone else is a free agent.
        punt: Categories I am punting (weight 0).
        games_left: Optional games remaining this week per player; adds ``weekly_fit`` for streaming.
        only_active: Hide free agents flagged inactive (no recent games).
        top_n: Rows to return (None = all).

    Returns:
        Free agents sorted by ``weekly_fit`` if ``games_left`` is given, else ``fit``, with
        ``z_total, fit, net_gain, drop, helps`` and per-category z columns.
    """
    punt = normalize_punt(punt)
    my_ids = [i for i in my_ids if i in valued.index]
    taken = set(rostered) | set(my_ids)
    fa = valued[~valued.index.isin(taken)]
    if only_active and "active" in fa:
        fa = fa[fa["active"].astype(bool)]

    weights = category_need_weights(team_category_z(valued, my_ids), punt)
    w = weights.rename(lambda c: f"z_{c}")

    contrib = fa[Z_COLS].mul(w, axis=1)
    out = fa[["name", "team", "z_total", *Z_COLS]].copy()
    if "active" in fa:
        out.insert(2, "active", fa["active"])
    out["fit"] = contrib.sum(axis=1)
    out["helps"] = contrib.apply(
        lambda r: ", ".join(config.CAT_LABELS[c[2:]] for c in r.nlargest(3).index if r[c] > 0), axis=1
    )

    if my_ids:
        my_fit = valued.loc[my_ids, Z_COLS].mul(w, axis=1).sum(axis=1)
        worst = my_fit.idxmin()
        out["drop"] = valued.loc[worst, "name"]
        out["net_gain"] = out["fit"] - my_fit[worst]
    sort_col = "fit"
    if games_left is not None:
        out["games_left"] = games_left.reindex(out.index).fillna(0).astype(int)
        out["weekly_fit"] = out["fit"] * out["games_left"]
        sort_col = "weekly_fit"
    out = out.sort_values(sort_col, ascending=False)
    out.attrs["need_weights"] = weights
    return out.head(top_n) if top_n else out


# ---------------------------------------------------------------------------
# Trades
# ---------------------------------------------------------------------------
@dataclass
class TradeResult:
    """Outcome of a proposed trade, from my side (and optionally theirs)."""

    categories: pd.DataFrame  # index = categories; before/after/delta raw + z
    total_z_delta: float  # change in summed z over non-punted categories
    need_weighted_delta: float  # same, weighted by how much I needed each category
    improved: list[str]
    hurt: list[str]
    roster_size_change: int
    dropped: list[int]  # players auto-dropped to keep my roster size
    their_total_z_delta: float | None = None
    their_categories: pd.DataFrame | None = None


def _side(valued, before_ids, after_ids, punt):
    tb, ta = team_totals(valued, before_ids), team_totals(valued, after_ids)
    zb, za = team_category_z(valued, before_ids), team_category_z(valued, after_ids)
    cats = config.CATEGORIES
    df = pd.DataFrame({"before": tb[cats], "after": ta[cats], "z_before": zb[cats], "z_after": za[cats]})
    df["delta"] = df["after"] - df["before"]
    df["z_delta"] = df["z_after"] - df["z_before"]
    df.index = [config.CAT_LABELS[c] for c in cats]
    keep = [config.CAT_LABELS[c] for c in cats if c not in punt]
    return df, float(df.loc[keep, "z_delta"].sum()), zb


def analyze_trade(
    valued: pd.DataFrame,
    my_ids: Iterable[int],
    give: Iterable[int],
    get: Iterable[int],
    punt: Iterable[str] | None = None,
    their_ids: Iterable[int] | None = None,
    min_change: float = 0.0,
    auto_drop: bool = True,
) -> TradeResult:
    """Compare my per-game category totals before and after a trade.

    Raw totals show the real stat lines (FG%/FT% as makes/attempts); z-sums put all
    categories on one scale so they can be added up.

    Args:
        valued: Player table with z-scores.
        my_ids: My current roster.
        give: Players I send away (must be on my roster).
        get: Players I receive.
        punt: Categories excluded from the totals.
        their_ids: Optional trade partner's roster, to evaluate their side too.
        min_change: z-delta threshold for listing a category as improved/hurt.
        auto_drop: If I receive more players than I send, drop my lowest ``z_total``
            players (never the incoming ones) so the comparison is like-for-like.
            Otherwise raw totals would rise just because the roster got bigger.
    """
    punt = normalize_punt(punt)
    my_ids, give, get = list(my_ids), list(give), list(get)
    not_mine = set(give) - set(my_ids)
    if not_mine:
        raise ValueError(f"Can't trade players not on my roster: {sorted(not_mine)}")
    after = [i for i in my_ids if i not in give] + [i for i in get if i not in my_ids]
    dropped: list[int] = []
    excess = len(after) - len(my_ids)
    if auto_drop and excess > 0:
        candidates = valued.loc[[i for i in after if i not in get], "z_total"]
        dropped = list(candidates.nsmallest(excess).index)
        after = [i for i in after if i not in dropped]

    cats, total, zb = _side(valued, my_ids, after, punt)
    weights = category_need_weights(zb, punt)
    need_delta = float((cats["z_delta"].values * weights[config.CATEGORIES].values).sum())
    live = [config.CAT_LABELS[c] for c in config.CATEGORIES if c not in punt]
    improved = [c for c in live if cats.loc[c, "z_delta"] > min_change]
    hurt = [c for c in live if cats.loc[c, "z_delta"] < -min_change]

    result = TradeResult(cats, total, need_delta, improved, hurt, len(after) - len(my_ids), dropped)
    if their_ids is not None:
        their_ids = list(their_ids)
        their_after = [i for i in their_ids if i not in get] + [i for i in give if i not in their_ids]
        result.their_categories, result.their_total_z_delta, _ = _side(valued, their_ids, their_after, ())
    return result
