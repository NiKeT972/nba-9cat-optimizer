"""Trade Analyzer: my category totals before vs after a proposed trade."""

import streamlit as st

from app.charts import diverging_category_bars
from app.common import data_or_stop, player_picker, rosters, settings
from src import config
from src.valuation.decisions import analyze_trade

st.title("Trade Analyzer")
players = data_or_stop()
s = settings()
r = rosters()
my_ids = [i for i in r[config.MY_TEAM] if i in players.index]
if not my_ids:
    st.info("Enter your roster on the **My Team** page first.")
    st.stop()

c1, c2 = st.columns(2)
with c1:
    give = player_picker("I give", players.loc[my_ids], [], "trade_give")
with c2:
    get = player_picker("I get", players.drop(index=my_ids), [], "trade_get")
with st.expander("Optional: partner's full roster (to see their side)"):
    partner = player_picker("Partner roster", players.drop(index=my_ids), get, "trade_partner")

if not give or not get:
    st.info("Pick at least one player on each side.")
    st.stop()

res = analyze_trade(players, my_ids, give, get, punt=s["punt"], their_ids=partner if partner else None)
verdict = "accept" if res.total_z_delta > 0 and res.need_weighted_delta > 0 else \
          "decline" if res.total_z_delta < 0 and res.need_weighted_delta < 0 else "mixed — check categories"

m1, m2, m3 = st.columns(3)
m1.metric("Total value change", f"{res.total_z_delta:+.2f} z", help="Sum of per-game z change over non-punted categories")
m2.metric("Need-weighted change", f"{res.need_weighted_delta:+.2f}", help="Weighted toward your weakest categories")
m3.metric("Verdict", verdict)
if res.dropped:
    st.caption("To make room you'd drop: " + ", ".join(players.loc[res.dropped, "name"]))
if res.their_total_z_delta is not None:
    st.caption(f"Their side: {res.their_total_z_delta:+.2f} z")

c1, c2 = st.columns([2, 3])
with c1:
    st.plotly_chart(diverging_category_bars(res.categories["z_delta"], "Category change (z)"),
                    use_container_width=True, theme="streamlit")
with c2:
    cats = res.categories[["before", "after", "delta", "z_delta"]].copy()
    pct_rows = ["FG%", "FT%"]
    fmt = cats.astype(object)
    for row in cats.index:
        f = "{:.3f}" if row in pct_rows else "{:.1f}"
        fd = "{:+.3f}" if row in pct_rows else "{:+.1f}"
        fmt.loc[row] = [f.format(cats.loc[row, "before"]), f.format(cats.loc[row, "after"]),
                        fd.format(cats.loc[row, "delta"]), f"{cats.loc[row, 'z_delta']:+.2f}"]
    st.markdown("**Per-game team totals**")
    st.dataframe(fmt.rename(columns={"before": "Before", "after": "After", "delta": "Change", "z_delta": "z change"}),
                 use_container_width=True)
    st.caption(f"Improves: {', '.join(res.improved) or '—'} · Hurts: {', '.join(res.hurt) or '—'}")
