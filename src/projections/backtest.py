"""Walk-forward backtest of the projection engine.

Every ``BACKTEST_STEP_DAYS`` through a completed season we:

1. project every player using only games *before* that date (``project_players`` has
   no look-ahead by construction);
2. compare with what each player actually averaged over the next
   ``BACKTEST_HORIZON_DAYS`` (one fantasy week).

Models compared
---------------
* ``blend``       - the production model (weighted windows + shrinkage + last-season prior).
* ``season_avg``  - naive baseline: raw season-to-date per-game average, no shrinkage.
* ``last7_avg``   - naive "ride the hot hand": raw per-game average over the last 7 days.

Error metrics
-------------
* Counting stats: mean absolute error (MAE) of per-game value, over players who played
  in the scoring week and are fantasy-relevant (projected >= ``BACKTEST_MIN_MINUTES`` MPG).
* FG% / FT%: attempt-weighted MAE of the percentage (a miss on a 20-FGA player matters
  more than on a 2-FGA player), mirroring how the categories are valued.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable

import numpy as np
import pandas as pd
from sklearn.metrics import mean_absolute_error

from src import config
from src.projections.project import project_players

COUNT_CATS: dict[str, str] = {k: v for k, v in config.CAT_COLUMNS.items()}
SHOT_CATS: dict[str, tuple[str, str]] = dict(config.PCT_COLUMNS)
NEEDED = sorted({*COUNT_CATS.values(), *[c for pair in SHOT_CATS.values() for c in pair]})

Model = Callable[[pd.DataFrame, pd.Timestamp], pd.DataFrame]


def _raw_average(logs: pd.DataFrame, as_of: pd.Timestamp, days: int | None) -> pd.DataFrame:
    past = logs[logs["date"] < as_of]
    if days is not None:
        past = past[past["date"] >= as_of - pd.Timedelta(days=days)]
    out = past.groupby("player_id")[[*NEEDED, "min"]].mean()
    return out


def default_models(prior_logs: pd.DataFrame | None) -> dict[str, Model]:
    """The production model plus two naive baselines."""
    return {
        "blend": lambda logs, d: project_players(logs, as_of=d, prior_logs=prior_logs),
        "season_avg": lambda logs, d: _raw_average(logs, d, None),
        "last7_avg": lambda logs, d: _raw_average(logs, d, 7),
    }


def actuals(logs: pd.DataFrame, start: pd.Timestamp, days: int) -> pd.DataFrame:
    """What each player actually did over ``[start, start + days)``: per-game counting stats and shooting totals."""
    window = logs[(logs["date"] >= start) & (logs["date"] < start + pd.Timedelta(days=days))]
    g = window.groupby("player_id")
    per_game = g[list(COUNT_CATS.values())].mean()
    shots = g[[c for pair in SHOT_CATS.values() for c in pair]].sum().add_suffix("_tot")
    return per_game.join(shots)


def backtest(
    logs: pd.DataFrame,
    prior_logs: pd.DataFrame | None = None,
    models: dict[str, Model] | None = None,
    warmup_days: int = config.BACKTEST_WARMUP_DAYS,
    step_days: int = config.BACKTEST_STEP_DAYS,
    horizon_days: int = config.BACKTEST_HORIZON_DAYS,
    min_minutes: float = config.BACKTEST_MIN_MINUTES,
) -> pd.DataFrame:
    """Run the walk-forward backtest.

    Args:
        logs: Tidy game logs of the season being evaluated.
        prior_logs: Tidy game logs of the season before (prior for the ``blend`` model).
        models: ``{name: fn(logs, as_of) -> per-game projections}``; default :func:`default_models`.
        warmup_days: Days after the first game before the first projection.
        step_days: Days between projection dates.
        horizon_days: Length of the scoring window after each projection date.
        min_minutes: Only score players the production model projects for at least this many MPG.

    Returns:
        Long table ``as_of, model, category, player_id, projected, actual, weight``.
        Every model is scored on the same players at each date.
    """
    models = models or default_models(prior_logs)
    first, last = logs["date"].min(), logs["date"].max()
    dates = pd.date_range(first + pd.Timedelta(days=warmup_days), last - pd.Timedelta(days=horizon_days - 1),
                          freq=f"{step_days}D")
    rows = []
    for as_of in dates:
        truth = actuals(logs, as_of, horizon_days)
        projections = {name: fn(logs, as_of) for name, fn in models.items()}
        ref = projections.get("blend", next(iter(projections.values())))
        eligible = set(ref.index[ref["min"] >= min_minutes]) & set(truth.index)
        for proj in projections.values():
            eligible &= set(proj.index)
        ids = sorted(eligible)
        if not ids:
            continue
        t = truth.loc[ids]
        for name, proj in projections.items():
            p = proj.loc[ids]
            for cat, col in COUNT_CATS.items():
                rows.append(pd.DataFrame({"as_of": as_of, "model": name, "category": cat, "player_id": ids,
                                          "projected": p[col].to_numpy(), "actual": t[col].to_numpy(), "weight": 1.0}))
            for cat, (m, a) in SHOT_CATS.items():
                att = t[f"{a}_tot"].to_numpy()
                keep = att > 0
                proj_pct = np.divide(p[m].to_numpy(), p[a].to_numpy(), out=np.zeros(len(ids)), where=p[a].to_numpy() > 0)
                rows.append(pd.DataFrame({"as_of": as_of, "model": name, "category": cat,
                                          "player_id": np.array(ids)[keep], "projected": proj_pct[keep],
                                          "actual": t[f"{m}_tot"].to_numpy()[keep] / att[keep], "weight": att[keep]}))
    if not rows:
        return pd.DataFrame(columns=["as_of", "model", "category", "player_id", "projected", "actual", "weight"])
    return pd.concat(rows, ignore_index=True)


def summarize(errors: pd.DataFrame, baseline: str = "season_avg") -> pd.DataFrame:
    """MAE per category (rows) and model (columns), plus % improvement of ``blend`` over ``baseline``."""
    mae = (
        errors.groupby(["category", "model"])
        .apply(lambda g: mean_absolute_error(g["actual"], g["projected"], sample_weight=g["weight"]),
               include_groups=False)
        .unstack("model")
    )
    mae = mae.reindex([c for c in config.CATEGORIES if c in mae.index])
    if "blend" in mae and baseline in mae:
        mae["blend_vs_" + baseline + "_%"] = 100 * (1 - mae["blend"] / mae[baseline])
    return mae


def to_markdown(summary: pd.DataFrame, n_dates: int, n_players: int, season: str) -> str:
    """README-ready markdown table."""
    s = summary.rename(index=config.CAT_LABELS)
    models = [c for c in ["blend", "season_avg", "last7_avg"] if c in s]
    head = "| Category | " + " | ".join(f"MAE: {m}" for m in models) + " | Blend vs season avg |"
    sep = "|---|" + "---:|" * (len(models) + 1)
    lines = [head, sep]
    for cat, r in s.iterrows():
        pct = cat.endswith("%")
        fmt = (lambda v: f"{v * 100:.2f} pp") if pct else (lambda v: f"{v:.3f}")
        imp = r.get("blend_vs_season_avg_%", np.nan)
        lines.append(f"| {cat} | " + " | ".join(fmt(r[m]) for m in models) + f" | {imp:+.1f}% |")
    note = (f"\n_{season} regular season, {n_dates} weekly projection dates, {n_players:,} player-weeks "
            f"(players projected >= {config.BACKTEST_MIN_MINUTES:.0f} MPG). FG%/FT% errors are attempt-weighted, "
            f"in percentage points. Positive improvement = lower error than the naive season average._")
    return "\n".join(lines) + "\n" + note


def main() -> None:
    """CLI: ``python -m src.projections.backtest [--season 2025-26]`` -> reports/backtest_<season>.{csv,md}."""
    from src.data.pipeline import load_game_logs

    parser = argparse.ArgumentParser(description="Walk-forward projection backtest.")
    parser.add_argument("--season", default=config.BACKTEST_SEASON)
    args = parser.parse_args()

    logs = load_game_logs(args.season)
    try:
        prior = load_game_logs(config.previous_season(args.season))
    except Exception:  # noqa: BLE001
        prior = None
        print("No prior-season data; blend model runs without last-season prior.")
    errors = backtest(logs, prior)
    summary = summarize(errors)
    config.REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    summary.to_csv(config.REPORTS_DIR / f"backtest_{args.season}.csv")
    blend = errors[(errors["model"] == "blend") & (errors["category"] == "PTS")]
    md = to_markdown(summary, errors["as_of"].nunique(), len(blend), args.season)
    (config.REPORTS_DIR / f"backtest_{args.season}.md").write_text(md + "\n")
    if update_readme(md):
        print("README.md backtest section updated.")
    print(md)


def update_readme(md: str, path=config.PROJECT_ROOT / "README.md") -> bool:
    """Replace the text between the BACKTEST markers in the README with ``md``."""
    start, end = "<!-- BACKTEST:START -->", "<!-- BACKTEST:END -->"
    if not path.exists():
        return False
    text = path.read_text()
    if start not in text or end not in text:
        return False
    before, rest = text.split(start, 1)
    _, after = rest.split(end, 1)
    path.write_text(f"{before}{start}\n{md}\n{end}{after}")
    return True


if __name__ == "__main__":
    main()
