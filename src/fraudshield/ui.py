from __future__ import annotations

import html
import plotly.graph_objects as go

TEAL = "#087F78"
INK = "#122A33"
AMBER = "#C77812"
RED = "#C03B49"
COLORS = {"high": RED, "medium": AMBER, "low": TEAL}

STYLE = """
<style>
.stApp { background: #F6F8FA; }
.block-container { max-width: 1480px; padding-top: 2rem; padding-bottom: 3rem; }
[data-testid="stSidebar"] { background: #EDF2F3; border-right: 1px solid #DAE3E7; }
h1, h2, h3 { letter-spacing: -.035em; color: #122A33; }
.eyebrow { color: #087F78; font-size: .74rem; font-weight: 700; letter-spacing: .14em; text-transform: uppercase; margin-bottom: .6rem; }
.brand { display: flex; align-items:center; gap: 12px; padding: 12px 0 20px; color: #122A33; font-size: 21px; font-weight: 750; }
.brand-mark { background: #087F78; color:white; width:38px; height:42px; border-radius:12px 12px 18px 18px; text-align:center; line-height:42px; font-size:24px; }
.hero { display:grid; grid-template-columns:1.6fr 1fr; gap:30px; padding:38px; margin:0 0 24px; background:linear-gradient(115deg,#E3F2ED,#EEF3F5); border:1px solid #CFE1DE; border-radius:24px; }
.hero h1 { font-size:clamp(2rem,3.5vw,3.6rem); line-height:1.08; max-width:680px; margin:6px 0 18px; }
.hero p { color:#49616A; font-size:1.04rem; line-height:1.65; max-width:580px; }
.pill { display:inline-block; font-size:.74rem; border:1px solid #B4D1CA; padding:5px 10px; border-radius:30px; color:#08685F; background:#F8FFFB; }
.payment-preview { align-self:center; background:white; padding:24px; border-radius:18px; box-shadow:0 16px 40px #29483A15; border:1px solid #D9E5E1; }
.preview-line { display:flex; justify-content:space-between; margin:12px 0; color:#5E7078; font-size:.9rem; }
.preview-amount { font-size:2.25rem; font-weight:750; color:#122A33; letter-spacing:-.045em; }
.preview-alert { background:#FFF1F1; border:1px solid #F3D5D8; border-radius:12px; padding:12px; color:#94303F; font-size:.85rem; }
.feature { padding:23px; background:white; border:1px solid #DEE6EB; border-radius:16px; margin-bottom:12px; min-height:150px; }
.feature h4 { margin:7px 0; color:#122A33; font-size:1.05rem; }
.feature p { color:#5C717B; margin:0; font-size:.92rem; line-height:1.55; }
.index { font-size:.78rem; color:#087F78; letter-spacing:.12em; font-weight:700; }
.result { padding:22px 26px; background:white; border:1px solid #DBE4E8; border-left:5px solid var(--band); border-radius:16px; margin:12px 0; }
.result h2 { color:var(--band); margin:0 0 9px; }
.result p { color:#485E69; margin:0; }
.muted { color:#647B85; font-size:.9rem; line-height:1.65; }
[data-testid="stMetric"] { padding:16px 18px; background:white; border:1px solid #DDE5EA; border-radius:14px; }
[data-testid="stMetricValue"] { font-size:1.75rem; color:#122A33; }
[data-testid="stForm"] { border-color:#D8E3E7; border-radius:18px; background:white; }
button[kind="primary"] { background:#087F78; border-color:#087F78; }
@media(max-width:800px) { .hero { grid-template-columns:1fr; padding:24px; } .payment-preview { display:none; } .block-container { padding-top:1rem; } }
</style>
"""


def esc(text) -> str:
    return html.escape(str(text))


def chart_style(fig, height=320):
    fig.update_layout(template="plotly_white", height=height,
                      paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                      font={"family": "Arial", "color": "#47616D", "size": 12},
                      margin={"l": 16, "r": 16, "t": 24, "b": 24},
                      colorway=[TEAL, "#568EAA", AMBER, RED, "#8AA99B"],
                      legend_title_text="")
    return fig


def gauge(score: float, band: str):
    fig = go.Figure(go.Indicator(
        mode="gauge+number", value=score, number={"suffix": " / 100", "font": {"size": 36, "color": INK}},
        gauge={"axis": {"range": [0, 100], "tickvals": [0, 40, 70, 100]},
               "bar": {"color": COLORS[band], "thickness": .32},
               "bgcolor": "white", "borderwidth": 0,
               "steps": [{"range": [0, 40], "color": "#DCF0E8"},
                         {"range": [40, 70], "color": "#FAEACF"},
                         {"range": [70, 100], "color": "#F5DDE0"}]}))
    return chart_style(fig, 255)
