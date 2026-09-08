"""
팀 연크크 — ETF 유동성 흐름 대시보드
실행: python3 -m streamlit run dashboard.py
"""

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from etf_data import (
    load_etf_list,
    fetch_all_etf_data,
    fetch_naver_etf_daily,
    fetch_naver_investor_trends,
    compute_flow_metrics,
    compute_momentum_score,
    match_hegemony,
    fetch_macro_indicators,
    fetch_etf_constituents,
)
import portfolio as pf

XLSX_PATH = os.path.join(os.path.dirname(__file__), "제12회 GAPS ETF 리스트 (v260509).xlsx")

WEIGHT_LIMITS = {
    "위험자산": {"limit": 70, "subs": {
        "국내주식_지수": 30, "국내주식_섹터": 15,
        "해외주식_지수": 30, "해외주식_섹터": 10,
        "FX 및 원자재": 20,
    }},
    "안전자산": {"limit": 100, "subs": {
        "국내채권_종합": 50, "국내채권_회사채": 30,
        "해외채권_종합": 50, "해외채권_회사채": 30,
        "금리연계형/초단기채권": 50,
    }},
}

CAT2_TO_CAT1 = {}
for cat1, info in WEIGHT_LIMITS.items():
    for cat2 in info["subs"]:
        CAT2_TO_CAT1[cat2] = cat1

PHASE_COLORS = {
    "2국면": "#2ecc71",
    "2국면 말미": "#f39c12",
    "1~2국면": "#e67e22",
    "1국면": "#3498db",
    "1국면 말미": "#2980b9",
    "1국면 탐색": "#9b59b6",
    "대기": "#95a5a6",
    "4국면 혼재": "#e74c3c",
    "-": "#bdc3c7",
}

st.set_page_config(
    page_title="연크크 ETF 유동성 대시보드",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
    .main-header { font-size: 1.8rem; font-weight: 700; margin-bottom: 0.5rem; }
    .sub-header { font-size: 0.95rem; color: #666; margin-bottom: 1.5rem; }
    .metric-card {
        background: #f8f9fa; border-radius: 8px; padding: 1rem;
        border-left: 4px solid #2ecc71; margin-bottom: 0.5rem;
    }
    .metric-negative { border-left-color: #e74c3c; }
    .phase-badge {
        display: inline-block; padding: 2px 10px; border-radius: 12px;
        font-size: 0.8rem; font-weight: 600; color: white;
    }
    div[data-testid="stSidebar"] { background: #1a1a2e; }
    div[data-testid="stSidebar"] .stMarkdown { color: #eee; }
</style>
""", unsafe_allow_html=True)


@st.cache_data(ttl=3600)
def get_etf_list():
    return load_etf_list(XLSX_PATH)


PRECOMPUTED_PATH = os.path.join(os.path.dirname(__file__), "etf_data_latest.parquet")


@st.cache_data(ttl=7200, show_spinner=False)
def get_all_etf_data(_etf_list, max_etfs, force_live=False):
    # 클라우드/공유 환경: 미리 수집해 커밋한 데이터를 우선 사용 (빠르고 안정적)
    if not force_live and os.path.exists(PRECOMPUTED_PATH):
        try:
            df = pd.read_parquet(PRECOMPUTED_PATH)
            if max_etfs and max_etfs < len(df):
                df = df.head(max_etfs)
            return df
        except Exception:
            pass
    # 폴백: 실시간 수집 (로컬에서 갱신 버튼을 눌렀을 때)
    return fetch_all_etf_data(_etf_list, max_etfs=max_etfs, save_snapshot=False)


@st.cache_data(ttl=1800)
def get_macro():
    return fetch_macro_indicators()


@st.cache_data(ttl=3600)
def get_constituents(ticker_code):
    return fetch_etf_constituents(ticker_code)


@st.cache_data(ttl=1800)
def get_benchmark_returns(start_date):
    """start_date 이후 KOSPI·S&P500 누적 수익률(%)."""
    import yfinance as yf
    out = {}
    for label, tk in [("KOSPI", "^KS11"), ("S&P500", "^GSPC")]:
        try:
            h = yf.Ticker(tk).history(start=start_date)
            if len(h) >= 2:
                out[label] = (h["Close"].iloc[-1] / h["Close"].iloc[0] - 1) * 100
        except Exception:
            pass
    return out


def render_tv_chart(daily, height=520):
    """TradingView Lightweight Charts로 캔들+MA20/50+거래량 렌더 (우리 데이터)."""
    import json
    d = daily.sort_values("date").copy()
    d["t"] = d["date"].dt.strftime("%Y-%m-%d")

    candles = [{"time": r.t, "open": float(r.open), "high": float(r.high),
                "low": float(r.low), "close": float(r.close)} for r in d.itertuples()]
    vols = [{"time": r.t, "value": float(r.volume),
             "color": "rgba(231,76,60,0.5)" if r.close >= r.open else "rgba(41,128,185,0.5)"}
            for r in d.itertuples()]

    def _ma(window):
        s = d["close"].rolling(window).mean()
        return [{"time": t, "value": float(v)} for t, v in zip(d["t"], s) if pd.notna(v)]

    ma20 = _ma(20) if len(d) >= 20 else []
    ma50 = _ma(50) if len(d) >= 50 else []

    html = f"""
    <div id="tvchart" style="width:100%;height:{height}px;"></div>
    <script src="https://unpkg.com/lightweight-charts@4.1.3/dist/lightweight-charts.standalone.production.js"></script>
    <script>
      const el = document.getElementById('tvchart');
      const chart = LightweightCharts.createChart(el, {{
        width: el.clientWidth || 900,
        height: {height},
        autoSize: true,
        layout: {{ background: {{ type:'solid', color:'#0e1117' }}, textColor:'#d0d0d0' }},
        grid: {{ vertLines: {{ color:'#1c1f26' }}, horzLines: {{ color:'#1c1f26' }} }},
        rightPriceScale: {{ borderColor:'#2a2e39' }},
        timeScale: {{ borderColor:'#2a2e39', timeVisible:false }},
        crosshair: {{ mode: 0 }},
        localization: {{ priceFormatter: function(p) {{ return Math.round(p).toLocaleString('en-US'); }} }},
      }});
      const candle = chart.addCandlestickSeries({{
        upColor:'#e74c3c', downColor:'#2980b9', borderVisible:false,
        wickUpColor:'#e74c3c', wickDownColor:'#2980b9',
        priceFormat: {{ type:'price', precision:0, minMove:1 }} }});
      candle.setData({json.dumps(candles)});
      const vol = chart.addHistogramSeries({{ priceFormat:{{type:'volume'}}, priceScaleId:'' }});
      vol.priceScale().applyOptions({{ scaleMargins:{{ top:0.8, bottom:0 }} }});
      vol.setData({json.dumps(vols)});
      const ma20 = chart.addLineSeries({{ color:'#f39c12', lineWidth:1, priceLineVisible:false }});
      ma20.setData({json.dumps(ma20)});
      const ma50 = chart.addLineSeries({{ color:'#9b59b6', lineWidth:1, priceLineVisible:false }});
      ma50.setData({json.dumps(ma50)});
      chart.timeScale().fitContent();
      setTimeout(() => {{ chart.applyOptions({{ width: el.clientWidth || 900 }}); chart.timeScale().fitContent(); }}, 200);
</script>
    """
    st.components.v1.html(html, height=height + 10)


@st.cache_data(ttl=3600)
def get_daily_data(ticker_code, days):
    return fetch_naver_etf_daily(ticker_code, days)


@st.cache_data(ttl=3600)
def get_investor_data(ticker_code, days):
    return fetch_naver_investor_trends(ticker_code, days)


# ── Sidebar ──
with st.sidebar:
    st.markdown("### 🔎 필터 설정")

    etf_list = get_etf_list()
    cat1_options = ["전체"] + sorted(etf_list["cat1"].unique().tolist())
    selected_cat1 = st.selectbox("자산 구분", cat1_options)

    if selected_cat1 != "전체":
        cat2_options = ["전체"] + sorted(etf_list[etf_list["cat1"] == selected_cat1]["cat2"].unique().tolist())
    else:
        cat2_options = ["전체"] + sorted(etf_list["cat2"].unique().tolist())
    selected_cat2 = st.selectbox("세부 자산", cat2_options)

    st.markdown("---")
    st.markdown("### 📅 기간 선택")
    flow_period = st.radio(
        "수익률·수급 기준 기간", ["1일", "5일", "20일"], index=1, horizontal=True,
        help="스코어보드와 자금 흐름의 수익률·외국인/기관 수급을 이 기간 기준으로 표시합니다.",
    )
    PERIOD_SUFFIX = {"1일": "1d", "5일": "5d", "20일": "20d"}
    psuf = PERIOD_SUFFIX[flow_period]

    st.markdown("---")
    st.markdown("### ⚡ 데이터 범위")
    max_etfs = st.slider("분석 ETF 수", 10, 188, 188, step=10)
    # 개별 ETF 상세 차트는 가능한 최대 이력(상장일~현재, 네이버 최대 약 12년)을 표시
    MAX_HISTORY = 5000

    # 데이터 신선도 표시
    if os.path.exists(PRECOMPUTED_PATH):
        import datetime as _dt
        mtime = _dt.datetime.fromtimestamp(os.path.getmtime(PRECOMPUTED_PATH))
        st.caption(f"📦 수록 데이터 기준: {mtime:%Y-%m-%d %H:%M}")
    force_live = st.checkbox("🔄 실시간 재수집", value=False,
                             help="체크 시 네이버에서 직접 수집(수 분 소요). 로컬 실행용. 공유 대시보드는 커밋된 데이터를 사용하세요.")

    st.markdown("---")
    st.markdown("### 📋 헤게모니 국면 필터")
    phase_filter = st.multiselect(
        "국면 선택",
        ["2국면", "2국면 말미", "1~2국면", "1국면", "1국면 말미", "1국면 탐색", "대기", "-"],
        default=[],
        help="비워두면 전체 국면이 표시됩니다.",
    )

    st.markdown("---")
    st.markdown("### 💰 비중 상한 참고")
    st.markdown("""
    | 세부자산 | 상한 |
    |---------|:---:|
    | 국내주식_지수 | 30% |
    | 국내주식_섹터 | 15% |
    | 해외주식_지수 | 30% |
    | 해외주식_섹터 | 10% |
    | FX 및 원자재 | 20% |
    | 개별 ETF | 20% |
    | 위험자산 합계 | 70% |
    """)


# ── Main ──
st.markdown('<div class="main-header">팀 연크크 — ETF 유동성 흐름 대시보드</div>', unsafe_allow_html=True)
st.markdown('<div class="sub-header">제12회 DB GAPS 투자대회 | 헤게모니 프레임 기반 유동성 모니터링</div>', unsafe_allow_html=True)

# Data loading
with st.spinner("ETF 데이터를 불러오는 중입니다..."):
    all_data = get_all_etf_data(etf_list, max_etfs, force_live=force_live)

# Apply filters
filtered = all_data.copy()
if selected_cat1 != "전체":
    filtered = filtered[filtered["cat1"] == selected_cat1]
if selected_cat2 != "전체":
    filtered = filtered[filtered["cat2"] == selected_cat2]
if phase_filter:
    filtered = filtered[filtered["hegemony_phase"].isin(phase_filter)]

# ── Tab Layout ──
tab1, tab2, tab3, tab4, tab5, tab6, tab7 = st.tabs([
    "📊 종합 스코어보드", "💹 자금 흐름", "📈 모멘텀 분석", "🔍 개별 ETF 상세",
    "🎯 포트폴리오", "🌐 매크로 신호판", "📅 이벤트 캘린더"
])

# ── Tab 1: 종합 스코어보드 ──
with tab1:
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("분석 ETF 수", f"{len(filtered)}개")
    with col2:
        avg_momentum = filtered["momentum_score"].mean() if not filtered.empty else 0
        st.metric("평균 모멘텀 점수", f"{avg_momentum:.1f}/7")
    with col3:
        bullish = len(filtered[filtered["momentum_score"] >= 5]) if not filtered.empty else 0
        st.metric("강세 신호 (5점+)", f"{bullish}개")
    with col4:
        if not filtered.empty and "foreign_5d" in filtered.columns:
            net_foreign = filtered["foreign_5d"].sum()
            st.metric("외국인 5일 순매수", f"{net_foreign:,.0f}주")

    # ── C: 이상신호 자동 플래그 ──
    if not filtered.empty:
        flags = []
        for _, r in filtered.iterrows():
            sigs = []
            dev = r.get("deviation_rate")
            if pd.notna(dev) and abs(dev) >= 1.0:
                sigs.append(f"괴리율 {dev:+.1f}%")
            vr = r.get("vol_ratio")
            if pd.notna(vr) and vr >= 2.0:
                sigs.append(f"거래량 {vr:.1f}배 급증")
            f5 = r.get(f"foreign_{psuf}")
            if pd.notna(f5) and f5 < 0 and abs(f5) > filtered[f"foreign_{psuf}"].abs().quantile(0.9):
                sigs.append("외국인 대량 순매도")
            r1 = r.get(f"return_{psuf}")
            if pd.notna(r1) and abs(r1) >= 5.0:
                sigs.append(f"{flow_period} 수익률 {r1:+.1f}%")
            if sigs:
                flags.append({"ETF명": r["name"], "세부자산": r.get("cat2", "-"), "이상신호": " · ".join(sigs)})
        with st.expander(f"⚠️ 이상신호 감지: {len(flags)}개 ETF ({flow_period} 기준)", expanded=bool(flags)):
            if flags:
                st.dataframe(pd.DataFrame(flags), use_container_width=True, hide_index=True)
            else:
                st.write("특이 신호 없음")

    st.markdown("---")

    if not filtered.empty:
        ret_col = f"return_{psuf}"
        foreign_col = f"foreign_{psuf}"
        inst_col = f"inst_{psuf}"
        ret_label = f"{flow_period} 수익률(%)"
        foreign_label = f"외국인 {flow_period}"
        inst_label = f"기관 {flow_period}"

        display_cols = ["name", "cat2", "hegemony_phase", "hegemony_type", "momentum_score",
                        "latest_close", ret_col, "vol_ratio",
                        foreign_col, inst_col, "total_nav_billion", "deviation_rate"]
        existing_cols = [c for c in display_cols if c in filtered.columns]
        display_df = filtered[existing_cols].copy()

        rename_map = {
            "name": "ETF명", "cat2": "세부자산", "hegemony_phase": "헤게모니 국면",
            "hegemony_type": "헤게모니 유형", "momentum_score": "모멘텀 점수",
            "latest_close": "현재가", ret_col: ret_label, "vol_ratio": "거래량 비율",
            foreign_col: foreign_label, inst_col: inst_label,
            "total_nav_billion": "총순자산(억)", "deviation_rate": "괴리율(%)",
        }
        display_df = display_df.rename(columns={k: v for k, v in rename_map.items() if k in display_df.columns})

        sort_options = ["모멘텀 점수", ret_label, "거래량 비율", foreign_label, "총순자산(억)"]
        sort_options = [c for c in sort_options if c in display_df.columns]
        sort_col = st.selectbox("정렬 기준", sort_options, index=0)
        display_df = display_df.sort_values(sort_col, ascending=False, na_position="last")

        def color_momentum(val):
            if pd.isna(val):
                return ""
            # 셀 배경은 그대로 두고, 점수 글자에만 색 (다크 테마에서 잘 보이는 선명한 색)
            base = "font-weight: 700; text-align: center;"
            if val >= 5:
                return f"color: #2ecc71; {base}"   # 초록 — 강한 모멘텀
            elif val >= 3:
                return f"color: #f1c40f; {base}"   # 노랑 — 중립
            else:
                return f"color: #e74c3c; {base}"   # 빨강 — 약함

        def color_return(val):
            if pd.isna(val):
                return ""
            if val > 0:
                return "color: #e74c3c; font-weight: bold"
            elif val < 0:
                return "color: #2980b9; font-weight: bold"
            return ""

        styled = display_df.style
        if "모멘텀 점수" in display_df.columns:
            styled = styled.map(color_momentum, subset=["모멘텀 점수"])
        if ret_label in display_df.columns:
            styled = styled.map(color_return, subset=[ret_label])
        fmt = {
            "현재가": "{:,.0f}", "거래량 비율": "{:.2f}",
            "총순자산(억)": "{:,.0f}", "괴리율(%)": "{:+.2f}",
            ret_label: "{:+.2f}", foreign_label: "{:,.0f}", inst_label: "{:,.0f}",
        }
        styled = styled.format({k: v for k, v in fmt.items() if k in display_df.columns}, na_rep="-")

        st.dataframe(styled, use_container_width=True, height=600)


# ── Tab 2: 자금 흐름 ──
with tab2:
    st.markdown("### 💧 ETF 실제 자금 흐름 (총순자산 · 괴리율)")
    st.caption(
        "외국인/기관 수급은 **유통시장** 거래입니다. ETF의 진짜 자금 유입/유출은 "
        "**총순자산(AUM) 변화**로 나타납니다. 대시보드를 매일 실행하면 AUM 스냅샷이 누적되어 "
        "순설정/순환매(자금 유입/유출)를 추정할 수 있습니다."
    )

    if "total_nav_billion" in filtered.columns:
        snap_count = filtered.get("aum_snapshot_count")
        has_flow = "est_net_flow_pct" in filtered.columns and filtered["est_net_flow_pct"].notna().any()

        c1, c2, c3 = st.columns(3)
        with c1:
            st.metric("총순자산 합계", f"{filtered['total_nav_billion'].sum():,.0f}억원")
        with c2:
            avg_dev = filtered["deviation_rate"].mean() if "deviation_rate" in filtered.columns else 0
            st.metric("평균 괴리율", f"{avg_dev:+.2f}%")
        with c3:
            n_days = int(snap_count.max()) if snap_count is not None and snap_count.notna().any() else 1
            st.metric("AUM 스냅샷 누적", f"{n_days}일")

        if not has_flow:
            st.info(
                "📌 AUM 변화 기반 자금 흐름은 **2영업일 이상 스냅샷이 쌓이면** 자동으로 표시됩니다. "
                "현재는 첫 스냅샷이 저장된 상태입니다. 매일 대시보드를 실행(또는 자동 스케줄)하면 누적됩니다."
            )
        else:
            flow_df = filtered[filtered["est_net_flow_pct"].notna()].copy()
            col_in, col_out = st.columns(2)
            with col_in:
                st.markdown("**순유입 추정 TOP 10** (AUM 증가 - 가격효과)")
                top_in = flow_df.nlargest(10, "est_net_flow_pct")[
                    ["name", "cat2", "est_net_flow_pct", "aum_change_billion"]]
                st.dataframe(top_in.rename(columns={
                    "name": "ETF명", "cat2": "세부자산",
                    "est_net_flow_pct": "순유입 추정(%)", "aum_change_billion": "AUM 증감(억)",
                }).style.format({"순유입 추정(%)": "{:+.2f}", "AUM 증감(억)": "{:+,.0f}"}),
                    use_container_width=True)
            with col_out:
                st.markdown("**순유출 추정 TOP 10**")
                top_out = flow_df.nsmallest(10, "est_net_flow_pct")[
                    ["name", "cat2", "est_net_flow_pct", "aum_change_billion"]]
                st.dataframe(top_out.rename(columns={
                    "name": "ETF명", "cat2": "세부자산",
                    "est_net_flow_pct": "순유입 추정(%)", "aum_change_billion": "AUM 증감(억)",
                }).style.format({"순유입 추정(%)": "{:+.2f}", "AUM 증감(억)": "{:+,.0f}"}),
                    use_container_width=True)

        st.markdown("---")

    st.markdown(f"### 세부자산별 외국인·기관 수급 현황 ({flow_period} 기준)")

    fcol = f"foreign_{psuf}"
    icol = f"inst_{psuf}"
    flabel = f"외국인 {flow_period}"
    ilabel = f"기관 {flow_period}"

    if not filtered.empty:
        available_flow = [c for c in [fcol, icol] if c in filtered.columns]

        if available_flow:
            flow_by_cat = filtered.groupby("cat2")[available_flow].sum().reset_index()

            fig = make_subplots(rows=1, cols=2,
                                subplot_titles=[f"외국인 {flow_period} 순매수", f"기관 {flow_period} 순매수"])

            if fcol in flow_by_cat.columns:
                colors = ["#e74c3c" if v > 0 else "#2980b9" for v in flow_by_cat[fcol]]
                fig.add_trace(go.Bar(x=flow_by_cat["cat2"], y=flow_by_cat[fcol],
                                     marker_color=colors, name="외국인"), row=1, col=1)
            if icol in flow_by_cat.columns:
                colors = ["#e74c3c" if v > 0 else "#2980b9" for v in flow_by_cat[icol]]
                fig.add_trace(go.Bar(x=flow_by_cat["cat2"], y=flow_by_cat[icol],
                                     marker_color=colors, name="기관"), row=1, col=2)

            fig.update_layout(height=450, showlegend=False)
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info(f"{flow_period} 수급 데이터가 없습니다.")

        st.markdown("---")
        st.markdown(f"### ETF별 자금 흐름 상위/하위 ({flow_period} 기준)")

        col1, col2 = st.columns(2)
        with col1:
            st.markdown(f"**외국인 순매수 TOP 10 ({flow_period})**")
            if fcol in filtered.columns:
                top_foreign = filtered.nlargest(10, fcol)[["name", "cat2", fcol]].rename(columns={
                    "name": "ETF명", "cat2": "세부자산", fcol: flabel})
                st.dataframe(top_foreign.style.format({flabel: "{:,.0f}"}, na_rep="-"),
                             use_container_width=True)
        with col2:
            st.markdown(f"**기관 순매수 TOP 10 ({flow_period})**")
            if icol in filtered.columns:
                top_inst = filtered.nlargest(10, icol)[["name", "cat2", icol]].rename(columns={
                    "name": "ETF명", "cat2": "세부자산", icol: ilabel})
                st.dataframe(top_inst.style.format({ilabel: "{:,.0f}"}, na_rep="-"),
                             use_container_width=True)


# ── Tab 3: 모멘텀 분석 ──
with tab3:
    st.markdown("### 헤게모니 국면별 모멘텀 분포")

    if not filtered.empty:
        fig = px.scatter(
            filtered,
            x="return_20d" if "return_20d" in filtered.columns else "return_5d",
            y="vol_ratio" if "vol_ratio" in filtered.columns else "momentum_score",
            color="hegemony_phase",
            size="aum_billion",
            hover_name="name",
            hover_data=["cat2", "momentum_score", "foreign_5d"],
            color_discrete_map=PHASE_COLORS,
            labels={
                "return_20d": "20일 수익률 (%)",
                "return_5d": "5일 수익률 (%)",
                "vol_ratio": "거래량 비율 (현재/20일평균)",
                "hegemony_phase": "헤게모니 국면",
                "aum_billion": "AUM(억원)",
            },
        )
        fig.update_layout(height=550)
        fig.add_hline(y=1, line_dash="dash", line_color="gray", annotation_text="평균 거래량")
        fig.add_vline(x=0, line_dash="dash", line_color="gray")
        st.plotly_chart(fig, use_container_width=True)

        st.markdown("---")
        st.markdown("### 세부자산별 평균 모멘텀 점수")

        momentum_by_cat = filtered.groupby("cat2").agg(
            avg_momentum=("momentum_score", "mean"),
            count=("momentum_score", "count"),
            avg_return_5d=("return_5d", "mean") if "return_5d" in filtered.columns else ("momentum_score", "count"),
        ).reset_index().sort_values("avg_momentum", ascending=False)

        fig2 = go.Figure(go.Bar(
            x=momentum_by_cat["cat2"],
            y=momentum_by_cat["avg_momentum"],
            marker_color=[PHASE_COLORS.get("2국면") if v >= 4 else PHASE_COLORS.get("1국면") if v >= 2 else PHASE_COLORS.get("-") for v in momentum_by_cat["avg_momentum"]],
            text=momentum_by_cat["avg_momentum"].round(1),
            textposition="outside",
        ))
        fig2.update_layout(
            height=400,
            yaxis_title="평균 모멘텀 점수",
            xaxis_title="세부자산",
        )
        st.plotly_chart(fig2, use_container_width=True)


# ── Tab 4: 개별 ETF 상세 ──
with tab4:
    st.markdown("### 개별 ETF 상세 분석")

    etf_options = filtered[["ticker_code", "name"]].apply(
        lambda x: f"{x['name']} ({x['ticker_code']})", axis=1
    ).tolist() if not filtered.empty else []

    selected_etf = st.selectbox("ETF 선택", etf_options if etf_options else ["데이터 없음"])

    if selected_etf and selected_etf != "데이터 없음":
        selected_code = selected_etf.split("(")[-1].replace(")", "").strip()
        etf_info = filtered[filtered["ticker_code"] == selected_code].iloc[0]

        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric("현재가", f"{etf_info.get('latest_close', 0):,.0f}원")
        with col2:
            retp = etf_info.get(f"return_{psuf}", 0)
            st.metric(f"{flow_period} 수익률", f"{retp:+.2f}%" if pd.notna(retp) else "-")
        with col3:
            phase = etf_info.get("hegemony_phase", "-")
            color = PHASE_COLORS.get(phase, "#bdc3c7")
            st.markdown(f'<span class="phase-badge" style="background:{color}">{phase}</span>', unsafe_allow_html=True)
            st.caption("헤게모니 국면")
        with col4:
            st.metric("모멘텀 점수", f"{etf_info.get('momentum_score', 0):.0f}/7")

        daily = get_daily_data(selected_code, MAX_HISTORY)
        investor = get_investor_data(selected_code, 250)  # 수급은 약 1년치(빠름)

        if not daily.empty:
            # TradingView Lightweight Charts — 캔들 + MA20/50 + 거래량
            st.markdown("**가격 추이 (TradingView 차트)**")
            st.caption("🟧 MA20 · 🟪 MA50 · 캔들(빨강 상승/파랑 하락) · 하단 거래량 · 우리 수집 데이터")
            render_tv_chart(daily, height=520)

            # 외국인·기관 수급 (별도)
            if not investor.empty:
                fig_s = go.Figure()
                fig_s.add_trace(go.Bar(x=investor["date"], y=investor["foreign"],
                                       name="외국인", marker_color="#e67e22"))
                fig_s.add_trace(go.Bar(x=investor["date"], y=investor["institutional"],
                                       name="기관", marker_color="#3498db"))
                fig_s.update_layout(height=260, title="외국인·기관 수급", barmode="group",
                                    showlegend=True, margin=dict(t=40, b=20))
                st.plotly_chart(fig_s, use_container_width=True)

        st.markdown("---")
        st.markdown("#### 상세 지표")
        detail_cols = st.columns(3)
        with detail_cols[0]:
            st.markdown("**가격 모멘텀**")
            st.write(f"- 1일 수익률: {etf_info.get('return_1d', 0):+.2f}%")
            st.write(f"- 5일 수익률: {etf_info.get('return_5d', 0):+.2f}%")
            st.write(f"- 20일 수익률: {etf_info.get('return_20d', 0):+.2f}%")
            st.write(f"- MA20 이격도: {etf_info.get('ma20_gap', 0):+.2f}%")
            st.write(f"- MA50 이격도: {etf_info.get('ma50_gap', 0):+.2f}%")
        with detail_cols[1]:
            st.markdown("**거래량**")
            st.write(f"- 최근 거래량: {etf_info.get('latest_volume', 0):,.0f}")
            st.write(f"- 5일 평균: {etf_info.get('vol_5d_avg', 0):,.0f}")
            st.write(f"- 20일 평균: {etf_info.get('vol_20d_avg', 0):,.0f}")
            st.write(f"- 거래량 비율: {etf_info.get('vol_ratio', 0):.2f}x")
        with detail_cols[2]:
            st.markdown("**수급**")
            st.write(f"- 외국인 1일: {etf_info.get('foreign_1d', 0):,.0f}")
            st.write(f"- 외국인 5일: {etf_info.get('foreign_5d', 0):,.0f}")
            st.write(f"- 외국인 20일: {etf_info.get('foreign_20d', 0):,.0f}")
            st.write(f"- 기관 1일: {etf_info.get('inst_1d', 0):,.0f}")
            st.write(f"- 기관 5일: {etf_info.get('inst_5d', 0):,.0f}")
            st.write(f"- 기관 20일: {etf_info.get('inst_20d', 0):,.0f}")


# ── Tab 5: 포트폴리오 (실거래 원장 + 평가손익) ──
with tab5:
    st.markdown("### 포트폴리오 — 실거래 원장 · 평가손익")

    price_map = all_data.set_index("ticker_code")["latest_close"].to_dict()
    cat_map = all_data.set_index("ticker_code")[["cat1", "cat2"]].to_dict("index")

    state = pf.load_portfolio()

    # 최초 1회: 초기 자본 설정
    if state is None:
        st.info("대회 가상자산을 설정하고 시작하세요. 시작 후에는 매수/매도로 자산이 추적됩니다.")
        init_eok = st.number_input("💰 초기 가상자산 (억원)", min_value=1.0, max_value=1000.0,
                                    value=10.0, step=1.0)
        if st.button("🚀 포트폴리오 시작"):
            pf.init_portfolio(init_eok * 100_000_000)
            st.rerun()
    else:

        val = pf.valuation(state, price_map)

        # ── 자산 현황 ──
        st.markdown("#### 💼 자산 현황")
        a1, a2, a3, a4 = st.columns(4)
        with a1:
            st.metric("초기 자산", f"{val['initial_capital']/1e8:,.2f}억원")
        with a2:
            st.metric("현재 총자산", f"{val['total_asset']/1e8:,.2f}억원",
                      f"{val['total_return_pct']:+.2f}%")
        with a3:
            st.metric("평가 포지션", f"{val['holdings_value']/1e8:,.2f}억원")
        with a4:
            st.metric("현금", f"{val['cash']/1e8:,.2f}억원")
        st.caption(
            f"누적 손익(미실현+실현): {val['total_return']:+,.0f}원  ·  "
            f"실현손익: {val['realized_pnl']:+,.0f}원"
        )

        # ── 매매 입력 ──
        st.markdown("#### 🧾 매매")
        buy_tab, sell_tab = st.tabs(["매수", "매도"])

        with buy_tab:
            c1, c2, c3, c4 = st.columns([3, 1, 1.3, 1])
            with c1:
                buy_sel = st.selectbox(
                    "종목", all_data[["ticker_code", "name"]].apply(
                        lambda x: f"{x['name']} ({x['ticker_code']})", axis=1).tolist(),
                    key="buy_sel")
            buy_code = buy_sel.split("(")[-1].replace(")", "").strip()
            buy_name = buy_sel.split("(")[0].strip()
            cur_px = float(price_map.get(buy_code, 0) or 0)
            with c2:
                buy_px = st.number_input("단가", min_value=0.0, value=cur_px, step=10.0, key="buy_px")
            with c3:
                buy_qty = st.number_input("수량(주)", min_value=0, value=0, step=1, key="buy_qty")
            with c4:
                st.write("");  st.write("")
                if st.button("매수 실행", use_container_width=True):
                    ok, msg = pf.buy(state, buy_code, buy_name, buy_qty, buy_px)
                    (st.success if ok else st.error)(msg)
                    if ok:
                        st.rerun()
            if buy_qty > 0 and buy_px > 0:
                st.caption(f"예상 체결금액: {buy_qty*buy_px:,.0f}원  ·  현재 현금: {val['cash']:,.0f}원")

        with sell_tab:
            if not state["positions"]:
                st.info("보유 종목이 없습니다.")
            else:
                held = [f"{p['name']} ({c})" for c, p in state["positions"].items()]
                s1, s2, s3, s4 = st.columns([3, 1, 1.3, 1])
                with s1:
                    sell_sel = st.selectbox("보유 종목", held, key="sell_sel")
                sell_code = sell_sel.split("(")[-1].replace(")", "").strip()
                held_qty = state["positions"][sell_code]["shares"]
                cur_px_s = float(price_map.get(sell_code, 0) or 0)
                with s2:
                    sell_px = st.number_input("단가", min_value=0.0, value=cur_px_s, step=10.0, key="sell_px")
                with s3:
                    sell_qty = st.number_input(f"수량 (보유 {held_qty:,})", min_value=0,
                                               max_value=int(held_qty), value=0, step=1, key="sell_qty")
                with s4:
                    st.write("");  st.write("")
                    if st.button("매도 실행", use_container_width=True):
                        ok, msg = pf.sell(state, sell_code, sell_qty, sell_px)
                        (st.success if ok else st.error)(msg)
                        if ok:
                            st.rerun()

        # ── 보유 종목 ──
        st.markdown("#### 📊 보유 종목")
        if val["rows"]:
            hold_df = pd.DataFrame(val["rows"])
            hold_df["weight"] = hold_df["market_value"] / val["total_asset"] * 100
            hold_df["cat1"] = hold_df["ticker_code"].map(lambda c: cat_map.get(c, {}).get("cat1", "-"))
            hold_df["cat2"] = hold_df["ticker_code"].map(lambda c: cat_map.get(c, {}).get("cat2", "-"))

            def _color_pnl(v):
                if pd.isna(v): return ""
                return "color: #e74c3c; font-weight:700;" if v > 0 else ("color:#2980b9; font-weight:700;" if v < 0 else "")

            show = hold_df[["name", "cat2", "shares", "avg_price", "cur_price",
                            "market_value", "pnl", "return_pct", "weight"]].rename(columns={
                "name": "ETF명", "cat2": "세부자산", "shares": "수량", "avg_price": "매입단가",
                "cur_price": "현재가", "market_value": "평가금액", "pnl": "평가손익",
                "return_pct": "수익률(%)", "weight": "비중(%)"})
            styled = show.style.map(_color_pnl, subset=["평가손익", "수익률(%)"]).format({
                "수량": "{:,.0f}", "매입단가": "{:,.0f}", "현재가": "{:,.0f}",
                "평가금액": "{:,.0f}", "평가손익": "{:+,.0f}", "수익률(%)": "{:+.2f}", "비중(%)": "{:.1f}"})
            st.dataframe(styled, use_container_width=True)

            # ── 비중 상한 체크 (평가금액 기준) ──
            st.markdown("#### ✅ 비중 상한 체크 (현재 평가금액 기준)")
            risk_w = hold_df[hold_df["cat1"] == "위험"]["weight"].sum()
            safe_w = hold_df[hold_df["cat1"] == "안전"]["weight"].sum()
            cash_w = val["cash"] / val["total_asset"] * 100
            cc1, cc2, cc3 = st.columns(3)
            with cc1:
                st.write(f"{'✅' if risk_w <= 70 else '❌'} 위험자산: {risk_w:.1f}% / 70%")
            with cc2:
                st.write(f"{'✅' if safe_w <= 100 else '❌'} 안전자산: {safe_w:.1f}% / 100%")
            with cc3:
                st.write(f"💵 현금: {cash_w:.1f}%")

            for cat2 in hold_df["cat2"].dropna().unique():
                if cat2 == "-":
                    continue
                w = hold_df[hold_df["cat2"] == cat2]["weight"].sum()
                limit = WEIGHT_LIMITS.get(CAT2_TO_CAT1.get(cat2, ""), {}).get("subs", {}).get(cat2, 100)
                st.write(f"  {'✅' if w <= limit else '❌'} {cat2}: {w:.1f}% / {limit}%")
            for _, r in hold_df.iterrows():
                if r["weight"] > 20:
                    st.write(f"  ❌ {r['name']}: 개별 ETF 상한 20% 초과 ({r['weight']:.1f}%)")

            # ── E: ETF 레이어링 룩스루 (핵심기업 실질 노출) ──
            st.markdown("#### 🔬 핵심기업 실질 노출 (레이어링 룩스루)")
            st.caption("보유 ETF들의 상위 구성종목을 관통해 합산. 동일 기업이 여러 ETF에 겹칠수록 실질 노출이 커집니다.")
            look = {}
            for _, r in hold_df.iterrows():
                cons = get_constituents(r["ticker_code"])
                if cons.empty:
                    continue
                for _, c in cons.iterrows():
                    # 종목 실질 비중 = ETF의 포트폴리오 비중 × ETF내 종목 비중
                    contrib = r["weight"] * c["etf_weight"] / 100
                    key = c["constituent_name"]
                    look[key] = look.get(key, 0) + contrib
            if look:
                look_df = pd.DataFrame(
                    [{"기업": k, "실질 노출(%)": v} for k, v in look.items()]
                ).sort_values("실질 노출(%)", ascending=False).head(15)
                fig_lt = px.bar(look_df, x="실질 노출(%)", y="기업", orientation="h",
                                color="실질 노출(%)", color_continuous_scale="Blues")
                fig_lt.update_layout(height=450, yaxis={"categoryorder": "total ascending"},
                                     coloraxis_showscale=False)
                st.plotly_chart(fig_lt, use_container_width=True)
            else:
                st.info("구성종목 데이터를 불러오지 못했습니다.")
        else:
            st.info("보유 종목이 없습니다. 위 '매수'에서 첫 종목을 담아보세요.")

        # ── D: 자산 추이 곡선 + 벤치마크 대비 ──
        st.markdown("---")
        st.markdown("#### 📈 자산 추이 · 벤치마크 대비")
        today_str = pd.Timestamp.now().strftime("%Y-%m-%d")
        pf.record_equity(state, val["total_asset"], today_str)
        eq = pd.DataFrame(state.get("equity_history", []))
        if len(eq) >= 2:
            eq["date"] = pd.to_datetime(eq["date"])
            fig_eq = go.Figure()
            fig_eq.add_trace(go.Scatter(x=eq["date"], y=eq["total_asset"] / 1e8,
                                        mode="lines+markers", name="총자산", line=dict(color="#2ecc71")))
            fig_eq.add_hline(y=val["initial_capital"] / 1e8, line_dash="dash", line_color="#888",
                             annotation_text="초기자본")
            fig_eq.update_layout(height=350, yaxis_title="총자산(억원)", showlegend=False)
            st.plotly_chart(fig_eq, use_container_width=True)
        else:
            st.caption("📌 자산 추이 곡선은 2영업일 이상 대시보드를 열어 기록이 쌓이면 표시됩니다.")

        # 벤치마크 대비
        bm = get_benchmark_returns(today_str if len(eq) < 2 else eq["date"].iloc[0].strftime("%Y-%m-%d"))
        bcols = st.columns(3)
        with bcols[0]:
            st.metric("내 수익률", f"{val['total_return_pct']:+.2f}%")
        with bcols[1]:
            k = bm.get("KOSPI")
            st.metric("KOSPI 대비", f"{val['total_return_pct'] - k:+.2f}%p" if k is not None else "-",
                      f"KOSPI {k:+.2f}%" if k is not None else None)
        with bcols[2]:
            s = bm.get("S&P500")
            st.metric("S&P500 대비", f"{val['total_return_pct'] - s:+.2f}%p" if s is not None else "-",
                      f"S&P500 {s:+.2f}%" if s is not None else None)

        st.markdown("---")
        if st.button("🗑️ 포트폴리오 전체 초기화"):
            pf.reset_portfolio()
            st.rerun()


# ── Tab 6: 매크로 신호판 ──
with tab6:
    st.markdown("### 🌐 매크로 신호판")
    st.caption("계획서 리스크 지표 실시간 모니터링. 미 10년물 금리 4.5~5.0%는 멀티플 압축 경계 구간입니다.")
    macro = get_macro()
    if macro.empty:
        st.warning("매크로 데이터를 불러오지 못했습니다. 잠시 후 다시 시도하세요.")
    else:
        sig_color = {"danger": "#e74c3c", "warn": "#f1c40f", "ok": "#2ecc71", "neutral": "#bdc3c7"}
        for i in range(0, len(macro), 4):
            cols = st.columns(4)
            for j, (_, m) in enumerate(macro.iloc[i:i+4].iterrows()):
                with cols[j]:
                    arrow = "▲" if m["change_pct"] > 0 else ("▼" if m["change_pct"] < 0 else "–")
                    st.metric(f"{m['name']} ({m['unit']})",
                              f"{m['value']:,.2f}",
                              f"{arrow} {m['change_pct']:+.2f}%")
                    if m["note"]:
                        st.markdown(
                            f"<span style='color:{sig_color.get(m['signal'],'#999')};font-size:0.8rem'>{m['note']}</span>",
                            unsafe_allow_html=True)
        st.markdown("---")
        danger = macro[macro["signal"] == "danger"]
        warn = macro[macro["signal"] == "warn"]
        if not danger.empty:
            st.error("🔴 위험 신호: " + ", ".join(danger["name"]))
        if not warn.empty:
            st.warning("🟡 경계 신호: " + ", ".join(warn["name"]))
        if danger.empty and warn.empty:
            st.success("🟢 주요 매크로 지표 안정 구간")
        st.caption("데이터: Yahoo Finance · 약 30분 캐시")


# ── Tab 7: 이벤트 캘린더 ──
with tab7:
    st.markdown("### 📅 운용기간 이벤트 캘린더 (2026.06~08)")
    st.caption("계획서 3.5 기준 주요 촉매. 각 이벤트가 헤게모니 판단·포트폴리오에 미치는 영향을 사전 점검하세요.")

    EVENTS = [
        ("2026-06-12", "스페이스X 나스닥 상장(SPCX)", "역대 최대 IPO, 유동성 일시 쏠림 · 나스닥100 편입 변동 가능", "유동성"),
        ("2026-06-18", "FOMC 금리결정 + 점도표", "분기 회의, 변동성 최대. 하반기 인하 경로 시그널", "금리"),
        ("2026-06-18", "미국 5월 CPI/PPI", "인플레 방향성, FOMC와 연동", "물가"),
        ("2026-06-25", "BOJ 금리결정", "엔캐리 청산 리스크 직결", "엔캐리"),
        ("2026-07-25", "빅테크 2Q 실적", "CapEx 가이던스 = AI반도체 2국면 지속 핵심 판단", "실적"),
        ("2026-07-30", "FOMC 금리결정", "점도표 없는 일반 회의", "금리"),
        ("2026-07-31", "국내 반도체 2Q 실적", "삼성·SK 영업이익률 추이 점검", "실적"),
        ("2026-08-21", "잭슨홀 심포지엄", "연준 하반기 통화정책 힌트", "금리"),
        ("2026-08-27", "엔비디아 2Q 실적", "운용기간 최중요 단일 이벤트. AI반도체 3국면 전환 신호", "실적"),
    ]
    today = pd.Timestamp.now().normalize()
    cat_color = {"금리": "#e74c3c", "실적": "#3498db", "엔캐리": "#9b59b6",
                 "유동성": "#f39c12", "물가": "#16a085"}
    rows = []
    for date_str, title, desc, cat in EVENTS:
        d = pd.Timestamp(date_str)
        dday = (d - today).days
        rows.append({"날짜": date_str, "D-day": dday, "이벤트": title, "구분": cat, "영향": desc})
    ev_df = pd.DataFrame(rows).sort_values("날짜")

    upcoming = ev_df[ev_df["D-day"] >= 0]
    if not upcoming.empty:
        nxt = upcoming.iloc[0]
        st.info(f"⏭️ 다음 이벤트: **{nxt['이벤트']}** — D-{nxt['D-day']} ({nxt['날짜']})")

    for _, e in ev_df.iterrows():
        dday = e["D-day"]
        if dday < 0:
            badge = f"<span style='color:#888'>종료</span>"
        elif dday == 0:
            badge = "<span style='color:#e74c3c;font-weight:700'>D-DAY</span>"
        else:
            badge = f"<span style='color:#2ecc71;font-weight:700'>D-{dday}</span>"
        col = cat_color.get(e["구분"], "#888")
        st.markdown(
            f"<div style='padding:8px 0;border-bottom:1px solid #333'>"
            f"{badge} &nbsp; <b>{e['날짜']}</b> &nbsp; "
            f"<span style='background:{col};color:white;padding:1px 8px;border-radius:8px;font-size:0.75rem'>{e['구분']}</span> "
            f"&nbsp; <b>{e['이벤트']}</b><br>"
            f"<span style='color:#aaa;font-size:0.85rem'>{e['영향']}</span></div>",
            unsafe_allow_html=True)


# ── Footer ──
st.markdown("---")
st.markdown(
    '<div style="text-align:center; color:#999; font-size:0.8rem;">'
    '팀 연크크 | 제12회 DB GAPS 투자대회 | 헤게모니 프레임 기반 ETF 유동성 대시보드'
    '</div>',
    unsafe_allow_html=True,
)
