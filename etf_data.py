"""
ETF 유동성 흐름 데이터 수집 모듈
- 네이버 금융 크롤링 (거래량, 수급, 시가총액)
- yfinance (해외 ETF 보조 데이터)
- KRX 정보데이터시스템 (설정/환매, 순자산)
"""

import pandas as pd
import requests
from bs4 import BeautifulSoup
import yfinance as yf
import time
import json
import os
from datetime import datetime, timedelta
from io import StringIO

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
}

CACHE_DIR = os.path.join(os.path.dirname(__file__), ".cache")
os.makedirs(CACHE_DIR, exist_ok=True)


def load_etf_list(xlsx_path: str) -> pd.DataFrame:
    df = pd.read_excel(xlsx_path, sheet_name="ETF", header=4)
    df = df.iloc[:, 1:]
    df.columns = ["ticker", "name", "aum", "index", "cat1", "cat2"]
    df = df.dropna(subset=["ticker"])
    df["ticker"] = df["ticker"].astype(str).str.strip()
    df["ticker_code"] = df["ticker"].str.replace("A", "", n=1)
    return df.reset_index(drop=True)


def _cache_path(name: str) -> str:
    return os.path.join(CACHE_DIR, f"{name}.parquet")


def _is_cache_fresh(name: str, max_age_hours: int = 4) -> bool:
    path = _cache_path(name)
    if not os.path.exists(path):
        return False
    mtime = datetime.fromtimestamp(os.path.getmtime(path))
    return (datetime.now() - mtime).total_seconds() < max_age_hours * 3600


def fetch_naver_etf_daily(ticker_code: str, days: int = 60) -> pd.DataFrame:
    cache_name = f"naver_daily_{ticker_code}_{days}"
    if _is_cache_fresh(cache_name):
        return pd.read_parquet(_cache_path(cache_name))

    url = "https://fchart.stock.naver.com/sise.nhn"
    params = {"symbol": ticker_code, "timeframe": "day", "count": days, "requestType": "0"}
    try:
        resp = requests.get(url, params=params, headers=HEADERS, timeout=10)
        resp.raise_for_status()
    except Exception:
        return pd.DataFrame()

    from xml.etree import ElementTree as ET
    try:
        root = ET.fromstring(resp.text)
    except ET.ParseError:
        return pd.DataFrame()

    rows = []
    for item in root.iter("item"):
        data = item.get("data", "").split("|")
        if len(data) >= 6:
            rows.append({
                "date": pd.to_datetime(data[0]),
                "open": float(data[1]),
                "high": float(data[2]),
                "low": float(data[3]),
                "close": float(data[4]),
                "volume": float(data[5]),
            })
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values("date").reset_index(drop=True)
        df.to_parquet(_cache_path(cache_name), index=False)
    return df


def fetch_naver_investor_trends(ticker_code: str, days: int = 40) -> pd.DataFrame:
    cache_name = f"naver_investor_{ticker_code}_{days}"
    if _is_cache_fresh(cache_name):
        return pd.read_parquet(_cache_path(cache_name))

    all_rows = []
    seen_dates = set()
    page = 1
    max_pages = (days // 20) + 2

    while page <= max_pages:
        url = f"https://finance.naver.com/item/frgn.naver?code={ticker_code}&page={page}"
        try:
            resp = requests.get(url, headers=HEADERS, timeout=10)
            resp.raise_for_status()
        except Exception:
            break

        soup = BeautifulSoup(resp.text, "html.parser")
        tables = soup.select("table.type2")
        if len(tables) < 2:
            break
        table = tables[1]

        page_added = 0
        for tr in table.select("tr"):
            tds = tr.select("td")
            if len(tds) < 9:
                continue
            date_text = tds[0].get_text(strip=True)
            if not date_text or "." not in date_text:
                continue
            try:
                def _parse_num(text):
                    t = text.replace(",", "").replace("+", "")
                    sign = -1 if t.startswith("-") else 1
                    t = t.lstrip("-")
                    return sign * float(t) if t else 0.0

                dt = pd.to_datetime(date_text, format="%Y.%m.%d")
                if dt in seen_dates:
                    continue
                seen_dates.add(dt)
                all_rows.append({
                    "date": dt,
                    "close": float(tds[1].get_text(strip=True).replace(",", "")),
                    "volume": float(tds[4].get_text(strip=True).replace(",", "")),
                    "institutional": _parse_num(tds[5].get_text(strip=True)),
                    "foreign": _parse_num(tds[6].get_text(strip=True)),
                })
                page_added += 1
            except (ValueError, IndexError):
                continue
        # 새 날짜가 더 없으면 데이터 끝 → 정지
        if page_added == 0:
            break
        page += 1
        time.sleep(0.15)

    df = pd.DataFrame(all_rows)
    if not df.empty:
        df = df.drop_duplicates(subset=["date"]).sort_values("date").reset_index(drop=True)
        df.to_parquet(_cache_path(cache_name), index=False)
    return df


# ─────────────────────────────────────────────────────────────
# 네이버 ETF 핵심지표 (총순자산/시가총액/괴리율) — 현재 시점 스냅샷
# ─────────────────────────────────────────────────────────────

def _parse_kr_amount(text: str) -> float:
    """'7조 5,822억' → 75822 (억원 단위). '5,822억' → 5822."""
    if not text:
        return 0.0
    text = str(text).replace(",", "").replace(" ", "")
    total = 0.0
    jo = 0
    if "조" in text:
        jo_part, text = text.split("조", 1)
        try:
            jo = float(jo_part)
        except ValueError:
            jo = 0
    eok = 0
    if "억" in text:
        eok_part = text.split("억", 1)[0]
        try:
            eok = float(eok_part) if eok_part else 0
        except ValueError:
            eok = 0
    elif text:
        try:
            eok = float(text)
        except ValueError:
            eok = 0
    return jo * 10000 + eok  # 억원 단위


def fetch_naver_etf_indicator(ticker_code: str) -> dict:
    """네이버 모바일 API에서 ETF 핵심 지표(현재 스냅샷)를 가져온다."""
    url = f"https://m.stock.naver.com/api/stock/{ticker_code}/integration"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=10)
        data = resp.json()
    except Exception:
        return {}

    ind = data.get("etfKeyIndicator", {}) or {}
    if not ind:
        return {}

    nav_str = str(ind.get("nav", "0")).replace(",", "")
    try:
        nav = float(nav_str)
    except ValueError:
        nav = 0.0

    deviation = ind.get("deviationRate", 0) or 0
    if ind.get("deviationSign", "") == "-":
        deviation = -abs(deviation)

    return {
        "total_nav_billion": _parse_kr_amount(ind.get("totalNav", "")),   # 총순자산(억원)
        "market_value_billion": _parse_kr_amount(ind.get("marketValue", "")),  # 시가총액(억원)
        "nav": nav,
        "deviation_rate": deviation,        # 괴리율(%)
        "total_fee": ind.get("totalFee", 0),
        "return_1m": ind.get("returnRate1m", 0),
        "return_3m": ind.get("returnRate3m", 0),
        "issuer": ind.get("issuerName", ""),
    }


# ─────────────────────────────────────────────────────────────
# 일별 AUM 스냅샷 누적 — 자금 흐름 프록시의 핵심
# 매일 실행하면 날짜별 총순자산이 쌓여, AUM 증감으로 순유입/유출 추정 가능
# ─────────────────────────────────────────────────────────────

SNAPSHOT_PATH = os.path.join(os.path.dirname(__file__), "aum_snapshots.csv")


def save_daily_snapshot(snapshot_df: pd.DataFrame, snapshot_date: str = None):
    """오늘자 ETF별 총순자산/시가총액을 누적 CSV에 저장(같은 날짜는 갱신)."""
    if snapshot_date is None:
        snapshot_date = datetime.now().strftime("%Y-%m-%d")

    cols = ["ticker_code", "name", "total_nav_billion", "market_value_billion",
            "nav", "deviation_rate", "latest_close"]
    avail = [c for c in cols if c in snapshot_df.columns]
    snap = snapshot_df[avail].copy()
    snap.insert(0, "snapshot_date", snapshot_date)

    if os.path.exists(SNAPSHOT_PATH):
        existing = pd.read_csv(SNAPSHOT_PATH, dtype={"ticker_code": str})
        existing = existing[existing["snapshot_date"] != snapshot_date]
        combined = pd.concat([existing, snap], ignore_index=True)
    else:
        combined = snap
    combined.to_csv(SNAPSHOT_PATH, index=False)
    return SNAPSHOT_PATH


def load_snapshots() -> pd.DataFrame:
    if not os.path.exists(SNAPSHOT_PATH):
        return pd.DataFrame()
    df = pd.read_csv(SNAPSHOT_PATH, dtype={"ticker_code": str})
    df["snapshot_date"] = pd.to_datetime(df["snapshot_date"])
    return df.sort_values(["ticker_code", "snapshot_date"])


def compute_aum_flow(ticker_code: str) -> dict:
    """누적 스냅샷에서 AUM 증감 기반 자금 흐름 프록시를 계산.
    순수 AUM 변화에서 가격(NAV) 변화를 제거하면 설정/환매에 의한 순유입에 근사.
    """
    snaps = load_snapshots()
    if snaps.empty:
        return {}
    t = snaps[snaps["ticker_code"] == ticker_code].sort_values("snapshot_date")
    if len(t) < 2:
        return {"aum_snapshot_count": len(t)}

    first, last = t.iloc[0], t.iloc[-1]
    aum_chg = last["total_nav_billion"] - first["total_nav_billion"]
    aum_chg_pct = (last["total_nav_billion"] / first["total_nav_billion"] - 1) * 100 if first["total_nav_billion"] else 0
    nav_chg_pct = (last["nav"] / first["nav"] - 1) * 100 if first.get("nav", 0) else 0
    # 가격 효과 제거 → 순설정(자금유입) 추정 비율
    net_creation_pct = aum_chg_pct - nav_chg_pct

    return {
        "aum_snapshot_count": len(t),
        "aum_change_billion": aum_chg,
        "aum_change_pct": aum_chg_pct,
        "nav_change_pct": nav_chg_pct,
        "est_net_flow_pct": net_creation_pct,
    }


# ─────────────────────────────────────────────────────────────
# (선택) 공식 KRX OpenAPI — 인증키 활성화 시 진짜 상장좌수/순자산 시계열
# 환경변수 KRX_AUTH_KEY 설정 시 자동 사용. 현재는 권한 활성화 대기 중.
# ─────────────────────────────────────────────────────────────

KRX_OPENAPI_BASE = "http://data-dbg.krx.co.kr/svc/apis"


def fetch_krx_etf_daily(bas_dd: str, auth_key: str = None) -> pd.DataFrame:
    """KRX OpenAPI ETF 일별매매정보(etf_bydd_trd). 특정일 전종목 스냅샷.
    NAV, LIST_SHRS(상장좌수), NETASST_TOTAMT(순자산총액) 포함.
    """
    auth_key = auth_key or os.environ.get("KRX_AUTH_KEY", "")
    if not auth_key:
        return pd.DataFrame()
    url = f"{KRX_OPENAPI_BASE}/etp/etf_bydd_trd"
    try:
        resp = requests.get(url, headers={"AUTH_KEY": auth_key},
                            params={"basDd": bas_dd}, timeout=20)
        if resp.status_code != 200:
            return pd.DataFrame()
        data = resp.json()
    except Exception:
        return pd.DataFrame()

    rows = data.get("OutBlock_1", [])
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["basDd"] = bas_dd
    return df


def compute_flow_metrics(daily_df: pd.DataFrame, investor_df: pd.DataFrame) -> dict:
    metrics = {}
    if daily_df.empty:
        return metrics

    latest = daily_df.iloc[-1]
    metrics["latest_close"] = latest["close"]
    metrics["latest_volume"] = latest["volume"]
    metrics["latest_date"] = latest["date"].strftime("%Y-%m-%d")

    # 1일 지표
    if len(daily_df) >= 2:
        prev_close = daily_df["close"].iloc[-2]
        metrics["return_1d"] = (latest["close"] / prev_close - 1) * 100 if prev_close else 0
        prev_vol = daily_df["volume"].iloc[-2]
        metrics["vol_1d_ratio"] = metrics["latest_volume"] / prev_vol if prev_vol > 0 else 0

    if len(daily_df) >= 5:
        metrics["vol_5d_avg"] = daily_df["volume"].tail(5).mean()
        metrics["vol_20d_avg"] = daily_df["volume"].tail(20).mean() if len(daily_df) >= 20 else daily_df["volume"].mean()
        metrics["vol_ratio"] = metrics["latest_volume"] / metrics["vol_20d_avg"] if metrics["vol_20d_avg"] > 0 else 0

        metrics["return_5d"] = (daily_df["close"].iloc[-1] / daily_df["close"].iloc[-5] - 1) * 100
        if len(daily_df) >= 20:
            metrics["return_20d"] = (daily_df["close"].iloc[-1] / daily_df["close"].iloc[-20] - 1) * 100
            metrics["ma20"] = daily_df["close"].tail(20).mean()
            metrics["ma20_gap"] = (latest["close"] / metrics["ma20"] - 1) * 100
        if len(daily_df) >= 50:
            metrics["ma50"] = daily_df["close"].tail(50).mean()
            metrics["ma50_gap"] = (latest["close"] / metrics["ma50"] - 1) * 100

    if not investor_df.empty and len(investor_df) >= 1:
        metrics["foreign_1d"] = investor_df["foreign"].iloc[-1]
        metrics["inst_1d"] = investor_df["institutional"].iloc[-1]

    if not investor_df.empty and len(investor_df) >= 5:
        metrics["foreign_5d"] = investor_df["foreign"].tail(5).sum()
        metrics["foreign_20d"] = investor_df["foreign"].tail(20).sum() if len(investor_df) >= 20 else investor_df["foreign"].sum()
        metrics["inst_5d"] = investor_df["institutional"].tail(5).sum()
        metrics["inst_20d"] = investor_df["institutional"].tail(20).sum() if len(investor_df) >= 20 else investor_df["institutional"].sum()

    return metrics


def compute_momentum_score(metrics: dict) -> float:
    score = 0
    if metrics.get("return_5d", 0) > 0:
        score += 1
    if metrics.get("return_20d", 0) > 0:
        score += 1
    if metrics.get("ma20_gap", 0) > 0:
        score += 1
    if metrics.get("ma50_gap", 0) > 0:
        score += 1
    if metrics.get("vol_ratio", 0) > 1.2:
        score += 1
    if metrics.get("foreign_5d", 0) > 0:
        score += 1
    if metrics.get("inst_5d", 0) > 0:
        score += 1
    return score


HEGEMONY_MAP = {
    "국내주식_섹터": {
        "반도체": {"phase": "2국면", "type": "기술 진입장벽형"},
        "AI반도체": {"phase": "2국면", "type": "기술 진입장벽형"},
        "조선": {"phase": "2국면 말미", "type": "기술·사이클 복합형"},
        "방산": {"phase": "1~2국면", "type": "산업기피+기술장벽"},
        "K방산": {"phase": "1~2국면", "type": "산업기피+기술장벽"},
        "전력": {"phase": "2국면 말미", "type": "산업 기피형"},
        "AI전력": {"phase": "2국면 말미", "type": "산업 기피형"},
        "원자력": {"phase": "1국면", "type": "산업 기피형"},
        "로봇": {"phase": "1국면", "type": "기술 진입장벽형"},
        "2차전지": {"phase": "1국면 탐색", "type": "기술·사이클 복합형"},
        "헬스케어": {"phase": "1국면", "type": "기술 진입장벽형"},
        "바이오": {"phase": "1국면", "type": "기술 진입장벽형"},
    },
    "해외주식_섹터": {
        "반도체": {"phase": "2국면", "type": "기술 진입장벽형"},
        "AI": {"phase": "2국면", "type": "기술 진입장벽형"},
        "테크": {"phase": "2국면", "type": "기술 진입장벽형"},
        "배당": {"phase": "대기", "type": "금리 사이클 수혜"},
        "리츠": {"phase": "대기", "type": "금리 사이클 수혜"},
        "헬스케어": {"phase": "1국면", "type": "기술 진입장벽형"},
        "바이오": {"phase": "1국면", "type": "기술 진입장벽형"},
        "전력": {"phase": "1국면 말미", "type": "산업 기피형"},
    },
}


# ─────────────────────────────────────────────────────────────
# 매크로 신호판 (계획서 리스크 지표) — yfinance
# ─────────────────────────────────────────────────────────────

MACRO_TICKERS = [
    ("미 10년물 금리", "^TNX", "%"),
    ("VIX", "^VIX", ""),
    ("원/달러", "KRW=X", "원"),
    ("엔/달러", "JPY=X", "엔"),
    ("WTI 원유", "CL=F", "$"),
    ("금", "GC=F", "$"),
    ("구리", "HG=F", "$"),
]


def _macro_signal(name, value):
    """계획서 기준 위험 신호 판정."""
    if name == "미 10년물 금리":
        if value >= 5.0:
            return "danger", "🔴 5.0% 돌파 — 멀티플 압축 위험 구간"
        if value >= 4.5:
            return "warn", "🟡 4.5~5.0% 경계 — 고밸류 자산 축소 검토"
        return "ok", "🟢 안정 구간"
    if name == "VIX":
        if value >= 30:
            return "danger", "🔴 변동성 급등"
        if value >= 20:
            return "warn", "🟡 경계"
        return "ok", "🟢 안정"
    return "neutral", ""


def fetch_macro_indicators() -> pd.DataFrame:
    cache_name = "macro_indicators"
    if _is_cache_fresh(cache_name, max_age_hours=0.5):
        return pd.read_parquet(_cache_path(cache_name))
    import yfinance as yf
    rows = []
    for name, ticker, unit in MACRO_TICKERS:
        try:
            h = yf.Ticker(ticker).history(period="5d")
            if h.empty:
                continue
            last = float(h["Close"].iloc[-1])
            prev = float(h["Close"].iloc[-2]) if len(h) > 1 else last
            chg = (last / prev - 1) * 100 if prev else 0
            signal, note = _macro_signal(name, last)
            rows.append({"name": name, "ticker": ticker, "unit": unit,
                         "value": last, "change_pct": chg, "signal": signal, "note": note})
        except Exception:
            continue
    df = pd.DataFrame(rows)
    if not df.empty:
        df.to_parquet(_cache_path(cache_name), index=False)
    return df


# ─────────────────────────────────────────────────────────────
# ETF 구성종목 (룩스루 분석용) — 네이버 etfAnalysis
# ─────────────────────────────────────────────────────────────

def fetch_etf_constituents(ticker_code: str) -> pd.DataFrame:
    """ETF 상위 10개 구성종목(종목코드/이름/ETF내 비중)."""
    url = f"https://m.stock.naver.com/api/stock/{ticker_code}/etfAnalysis"
    try:
        data = requests.get(url, headers=HEADERS, timeout=10).json()
    except Exception:
        return pd.DataFrame()
    rows = []
    for c in data.get("etfTop10MajorConstituentAssets", []) or []:
        w = str(c.get("etfWeight", "0")).replace("%", "").replace(",", "")
        try:
            weight = float(w)
        except ValueError:
            weight = 0.0
        rows.append({
            "constituent_code": c.get("itemCode", ""),
            "constituent_name": c.get("itemName", ""),
            "etf_weight": weight,
        })
    return pd.DataFrame(rows)


def fetch_etf_net_inflow(ticker_code: str) -> pd.DataFrame:
    """ETF 누적 순유입 추이 (네이버 cumulativeNetInflowList)."""
    url = f"https://m.stock.naver.com/api/stock/{ticker_code}/etfAnalysis"
    try:
        data = requests.get(url, headers=HEADERS, timeout=10).json()
    except Exception:
        return pd.DataFrame()
    lst = data.get("cumulativeNetInflowList", []) or []
    if not lst:
        return pd.DataFrame()
    return pd.DataFrame(lst)


def match_hegemony(name: str, cat2: str) -> dict:
    if cat2 not in HEGEMONY_MAP:
        return {"phase": "-", "type": "-"}

    sector_map = HEGEMONY_MAP[cat2]
    for keyword, info in sector_map.items():
        if keyword in name:
            return info
    return {"phase": "-", "type": "-"}


def fetch_all_etf_data(etf_list: pd.DataFrame, progress_callback=None, max_etfs=None,
                       with_indicators=True, save_snapshot=True):
    results = []
    total = len(etf_list) if max_etfs is None else min(max_etfs, len(etf_list))

    for i, row in etf_list.head(total).iterrows():
        ticker_code = row["ticker_code"]
        if progress_callback:
            progress_callback(i + 1, total, row["name"])

        daily = fetch_naver_etf_daily(ticker_code)
        investor = fetch_naver_investor_trends(ticker_code)
        metrics = compute_flow_metrics(daily, investor)
        hegemony = match_hegemony(row["name"], row["cat2"])
        momentum = compute_momentum_score(metrics)

        indicator = fetch_naver_etf_indicator(ticker_code) if with_indicators else {}
        aum_flow = compute_aum_flow(ticker_code)

        results.append({
            "ticker": row["ticker"],
            "ticker_code": ticker_code,
            "name": row["name"],
            "cat1": row["cat1"],
            "cat2": row["cat2"],
            "aum_billion": row["aum"] / 100 if pd.notna(row["aum"]) else 0,
            "hegemony_phase": hegemony["phase"],
            "hegemony_type": hegemony["type"],
            "momentum_score": momentum,
            **metrics,
            **indicator,
            **aum_flow,
        })
        time.sleep(0.1)

    df = pd.DataFrame(results)
    if save_snapshot and not df.empty and "total_nav_billion" in df.columns:
        try:
            save_daily_snapshot(df)
        except Exception:
            pass
    return df
