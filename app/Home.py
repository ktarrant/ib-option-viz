"""Entrypoint: the page config, the terminal chrome and the top function bar.

Every page runs inside this script, so it alone sets the config and the CSS.
The pages live in `views/`, not `pages/`: Streamlit still auto-discovers a
`pages/` folder, and a fresh session opened on a page's URL then ran that
page alone, without this script, so without the nav, the layout or the CSS.
"""

import streamlit as st

from optionviz.ui import theme

st.set_page_config(page_title="Options History", layout="wide")
theme.page_css()

st.navigation(
    [
        st.Page("views/0_Overview.py", title="Overview", icon=":material/dashboard:", default=True),
        st.Page("views/5_Chart.py", title="Chart", icon=":material/candlestick_chart:"),
        st.Page("views/1_Events.py", title="Events", icon=":material/bolt:"),
        st.Page("views/3_Backtest.py", title="Backtest", icon=":material/insights:"),
        st.Page("views/4_Model_Evaluation.py", title="Model Eval", icon=":material/query_stats:"),
        st.Page("views/6_News_Study.py", title="News Study", icon=":material/newspaper:"),
        st.Page("views/7_TA_Study.py", title="TA Study", icon=":material/ssid_chart:"),
        st.Page("views/2_Coverage.py", title="Coverage", icon=":material/dataset:"),
    ],
    position="top",
).run()
