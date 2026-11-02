"""Player Rankings: z-score value table with filters."""

import streamlit as st

from app.common import CAT_LABEL_COLS, data_or_stop, rosters, settings, style_z
from src import config
from src.data.roster import rostered_ids

st.title("Player Rankings")
players = data_or_stop()
s = settings()
punt_txt = ", ".join(config.CAT_LABELS[c] for c in s["punt"]) or "none"
st.caption(f"Per-game z-scores vs. the top {config.ZSCORE_POOL_SIZE} players · punting: {punt_txt} · "
           f"projections as of {s['as_of']:%b %d}")

c1, c2, c3, c4 = st.columns([2, 2, 1.4, 1.2], vertical_alignment="bottom")
query = c1.text_input("Search player", placeholder="e.g. Jokić")
teams = c2.multiselect("Teams", sorted(players["team"].dropna().unique()))
available = c3.toggle("Free agents only", help="Hide everyone on a roster you've entered on My Team.")
active = c4.toggle("Active only", value=True, help=f"Played within {config.INACTIVE_AFTER_DAYS} days.")
view = st.segmented_control("Show", ["Z-scores", "Per-game projections"], default="Z-scores")

df = players
if query:
    df = df[df["name"].str.contains(query, case=False, regex=False)]
if teams:
    df = df[df["team"].isin(teams)]
if available:
    df = df[~df.index.isin(rostered_ids(rosters()))]
if active:
    df = df[df["active"]]

base = ["rank", "name", "team", "games", "min"]
if view == "Per-game projections":
    stat_cols = ["pts", "reb", "ast", "stl", "blk", "fg3m", "fg_pct", "ft_pct", "tov", "fga", "fta"]
    out = df[base + ["z_total"] + stat_cols].rename(columns={
        "pts": "PTS", "reb": "REB", "ast": "AST", "stl": "STL", "blk": "BLK", "fg3m": "3PM",
        "fg_pct": "FG%", "ft_pct": "FT%", "tov": "TO", "fga": "FGA", "fta": "FTA"})
    fmt = {c: "{:.1f}" for c in ["min", "PTS", "REB", "AST", "STL", "BLK", "3PM", "TO", "FGA", "FTA"]}
    fmt.update({"FG%": "{:.3f}", "FT%": "{:.3f}", "z_total": "{:+.2f}"})
    styled = out.rename(columns={"z_total": "Value"}).style.format(
        {("Value" if k == "z_total" else k): v for k, v in fmt.items()}, na_rep="–")
else:
    out = df[base + ["z_total", *CAT_LABEL_COLS]].rename(columns={**CAT_LABEL_COLS, "z_total": "Value"})
    z_cols = ["Value", *CAT_LABEL_COLS.values()]
    styled = style_z(out, z_cols, {"min": "{:.1f}"})

st.dataframe(
    styled, hide_index=True, use_container_width=True, height=640,
    column_config={"rank": st.column_config.NumberColumn("#", width="small"),
                   "name": "Player", "team": "Team", "games": "GP", "min": "MPG"},
)
st.caption(f"{len(df)} players. Punted categories still show their z-score but don't count toward Value.")

with st.expander("How is value calculated?"):
    st.markdown(
        """
- **Projection** – blend of last-7-day, last-15-day and season per-minute rates (shrunk toward last
  season / league average when the sample is small) × projected minutes.
- **Counting stats** – `z = (x − pool mean) / pool std`; turnovers are flipped (fewer is better).
- **FG% / FT%** – z-score of *impact* = `(player% − league%) × attempts`, so volume matters.
- **Pool** – the top 156 players (12 teams × 13), re-selected until stable.
"""
    )
