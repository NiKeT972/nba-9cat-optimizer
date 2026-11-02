"""Shared Streamlit state: cached data loading, sidebar settings, rosters."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # Streamlit runs app/streamlit_app.py with app/ as cwd on the path
    sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from app.charts import z_cell_color  # noqa: E402
from src import config  # noqa: E402
from src.data import roster as roster_io  # noqa: E402
from src.data.pipeline import load_game_logs, load_schedule  # noqa: E402
from src.projections.project import project_players  # noqa: E402
from src.valuation.zscores import value_players  # noqa: E402

ROSTER_KEYS = [config.MY_TEAM, config.OPPONENT, config.OTHER_ROSTERED]
CAT_LABEL_COLS = {f"z_{c}": config.CAT_LABELS[c] for c in config.CATEGORIES}


# ---------------------------------------------------------------------------
# Data (cached per process; "Refresh data" clears it)
# ---------------------------------------------------------------------------
@st.cache_data(ttl=3600, show_spinner="Loading game logs…")
def load_logs(season: str, force: bool = False) -> pd.DataFrame:
    return load_game_logs(season, force=force)


@st.cache_data(ttl=24 * 3600, show_spinner="Loading last season…")
def load_prior(season: str) -> pd.DataFrame | None:
    try:
        return load_game_logs(config.previous_season(season))
    except Exception:  # noqa: BLE001 - prior is optional
        return None


@st.cache_data(ttl=6 * 3600, show_spinner="Loading schedule…")
def load_sched(season: str, force: bool = False) -> pd.DataFrame | None:
    try:
        return load_schedule(season, force=force)
    except Exception:  # noqa: BLE001
        return None


@st.cache_data(show_spinner="Projecting players…")
def projections(season: str, as_of: pd.Timestamp) -> pd.DataFrame:
    return project_players(load_logs(season), as_of=as_of, prior_logs=load_prior(season))


@st.cache_data(show_spinner="Computing z-scores…")
def valued(season: str, as_of: pd.Timestamp, punt: tuple[str, ...]) -> pd.DataFrame:
    table, _ = value_players(projections(season, as_of), punt=punt)
    return table


def data_or_stop() -> pd.DataFrame:
    """Return the valued player table for the current settings, or explain how to get data and stop."""
    try:
        logs = load_logs(config.SEASON)
    except Exception as exc:  # noqa: BLE001
        st.error(
            f"No game-log data for {config.SEASON}.\n\n"
            "Run `python -m src.data.pipeline` (and `--season "
            f"{config.PRIOR_SEASON}` for the projection prior) on a machine that can reach stats.nba.com, "
            f"then reload.\n\nDetails: `{exc}`"
        )
        st.stop()
    if logs.empty:
        st.warning(f"The {config.SEASON} game log is empty — has the season started?")
        st.stop()
    return valued(config.SEASON, settings()["as_of"], settings()["punt"])


def schedule_or_warn() -> pd.DataFrame | None:
    sched = load_sched(config.SEASON)
    if sched is None or sched.empty:
        st.warning("Schedule not available — games-remaining features are disabled. "
                   "Run `python -m src.data.pipeline` to download it.")
        return None
    return sched


# ---------------------------------------------------------------------------
# Sidebar settings shared by every page
# ---------------------------------------------------------------------------
def sidebar() -> None:
    """Render global controls once per run (punt, date, today's games, refresh)."""
    with st.sidebar:
        st.markdown("### League settings")
        st.multiselect(
            "Punt categories", config.CATEGORIES, key="punt",
            format_func=lambda c: config.CAT_LABELS[c],
            help="Excluded from total value, waiver fit and trade totals.",
        )
        st.date_input("As of", key="as_of_date", value=pd.Timestamp.today().date(),
                      help="Projection date and current fantasy week.")
        st.toggle("Count today's games", key="include_today", value=True,
                  help="Switch off once today's games have tipped off.")
        if not config.OFFLINE:
            if st.button("Refresh data from NBA", use_container_width=True):
                st.cache_data.clear()
                with st.spinner("Downloading…"):
                    load_logs(config.SEASON, force=True)
                    load_sched(config.SEASON, force=True)
                st.cache_data.clear()
                st.rerun()
        try:
            last = load_logs(config.SEASON)["date"].max()
            st.caption(f"Season {config.SEASON} · data through {last:%b %d, %Y}")
        except Exception:  # noqa: BLE001
            st.caption(f"Season {config.SEASON} · no data loaded")


def settings() -> dict:
    """Current global settings."""
    return {
        "punt": tuple(st.session_state.get("punt", [])),
        "as_of": pd.Timestamp(st.session_state.get("as_of_date", pd.Timestamp.today().date())).normalize(),
        "include_today": bool(st.session_state.get("include_today", True)),
    }


# ---------------------------------------------------------------------------
# Rosters
# ---------------------------------------------------------------------------
def rosters() -> dict[str, list[int]]:
    if "rosters" not in st.session_state:
        loaded = roster_io.load_rosters()
        st.session_state["rosters"] = {k: loaded.get(k, []) for k in ROSTER_KEYS}
    return st.session_state["rosters"]


def save_rosters(new: dict[str, list[int]], players: pd.DataFrame) -> None:
    st.session_state["rosters"] = new
    if config.ROSTER_SAVE_ENABLED:
        roster_io.save_rosters(new, names=players["name"].to_dict())


def roster_json(players: pd.DataFrame) -> str:
    lookup = players["name"].to_dict()
    return json.dumps({k: [{"player_id": int(i), "name": lookup.get(i, "")} for i in v]
                       for k, v in rosters().items()}, indent=2)


def labels(players: pd.DataFrame) -> dict[int, str]:
    """player_id -> 'Name (TEAM)' for selectors."""
    return {pid: f"{r['name']} ({r['team']})" for pid, r in players[["name", "team"]].iterrows()}


def player_picker(label: str, players: pd.DataFrame, default: list[int], key: str, **kw) -> list[int]:
    lab = labels(players)
    options = sorted(players.index, key=lambda i: lab[i])
    return st.multiselect(label, options, default=[d for d in default if d in lab],
                          format_func=lambda i: lab[i], key=key, **kw)


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------
def style_z(df: pd.DataFrame, z_cols: list[str], extra_formats: dict[str, str] | None = None):
    """Pandas Styler tinting z-score columns (blue good / red bad) with +2-decimal formatting.

    All formats go into a single ``.format`` call (a second call would reset the first).
    """
    fmt = {c: "{:+.2f}" for c in z_cols}
    fmt.update(extra_formats or {})
    return df.style.map(z_cell_color, subset=z_cols).format(fmt, na_rep="–")
