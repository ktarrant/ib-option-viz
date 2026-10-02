"""Entrypoint: the page config, the terminal chrome and the top function bar.

Every page runs inside this script, so it alone sets the config and the CSS.
"""

import streamlit as st

from optionviz.ui import theme

st.set_page_config(page_title="Options History", layout="wide")
theme.page_css()

st.navigation(
    [
        st.Page("pages/0_Overview.py", title="Overview", icon=":material/dashboard:", default=True),
        st.Page("pages/5_Chart.py", title="Chart", icon=":material/candlestick_chart:"),
        st.Page("pages/1_Events.py", title="Events", icon=":material/bolt:"),
        st.Page("pages/3_Backtest.py", title="Backtest", icon=":material/insights:"),
        st.Page("pages/4_Model_Evaluation.py", title="Model Eval", icon=":material/query_stats:"),
        st.Page("pages/2_Coverage.py", title="Coverage", icon=":material/dataset:"),
    ],
    position="top",
).run()
