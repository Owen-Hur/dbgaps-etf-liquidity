"""
일별 AUM 스냅샷 수집 (경량) — 매일 장 마감 후 실행 권장
ETF 핵심지표(총순자산/시가총액/괴리율)만 빠르게 수집해 aum_snapshots.csv에 누적.
스냅샷이 2일 이상 쌓이면 대시보드 '자금 흐름' 탭에서 순유입/유출 추정이 활성화됨.

수동 실행:   python3 snapshot_job.py
매일 자동:   crontab -e  →  30 16 * * 1-5  cd "/path/to/DB GAPS" && python3 snapshot_job.py
"""

import os
import sys
import time
import warnings
from datetime import datetime

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd
from etf_data import load_etf_list, fetch_naver_etf_indicator, save_daily_snapshot

XLSX_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "제12회 GAPS ETF 리스트 (v260509).xlsx")


def main():
    etf_list = load_etf_list(XLSX_PATH)
    rows = []
    total = len(etf_list)
    print(f"[{datetime.now():%Y-%m-%d %H:%M}] {total}개 ETF 지표 수집 시작")

    for i, row in etf_list.iterrows():
        code = row["ticker_code"]
        ind = fetch_naver_etf_indicator(code)
        rows.append({
            "ticker_code": code,
            "name": row["name"],
            "latest_close": ind.get("nav", 0),
            **ind,
        })
        if (i + 1) % 30 == 0:
            print(f"  {i+1}/{total}")
        time.sleep(0.1)

    df = pd.DataFrame(rows)
    df = df[df.get("total_nav_billion", 0) > 0] if "total_nav_billion" in df.columns else df

    if df.empty:
        print("⚠️  수집된 지표가 없습니다. 네트워크/소스 점검 필요.")
        return

    path = save_daily_snapshot(df)
    print(f"✓ 스냅샷 저장: {path} ({len(df)}개 ETF, {datetime.now():%Y-%m-%d})")


if __name__ == "__main__":
    main()
