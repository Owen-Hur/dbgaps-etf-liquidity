"""
포트폴리오 원장 — 매수/매도 기록, 평가손익, 총자산 추적
portfolio.json 에 저장되어 새로고침/재실행에도 유지됨.
"""
import json
import os

PORTFOLIO_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "portfolio.json")


def load_portfolio():
    if os.path.exists(PORTFOLIO_PATH):
        try:
            with open(PORTFOLIO_PATH, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None
    return None


def save_portfolio(state):
    with open(PORTFOLIO_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def init_portfolio(initial_capital):
    state = {
        "initial_capital": float(initial_capital),
        "cash": float(initial_capital),
        "positions": {},          # code -> {name, shares, avg_price}
        "realized_pnl": 0.0,       # 누적 실현손익
    }
    save_portfolio(state)
    return state


def reset_portfolio():
    if os.path.exists(PORTFOLIO_PATH):
        os.remove(PORTFOLIO_PATH)


def buy(state, code, name, shares, price):
    shares = int(shares)
    cost = shares * price
    if shares <= 0:
        return False, "수량은 1주 이상이어야 합니다."
    if cost > state["cash"] + 1e-6:
        return False, f"현금 부족 (필요 {cost:,.0f}원 / 보유 {state['cash']:,.0f}원)"
    state["cash"] -= cost
    pos = state["positions"].get(code)
    if pos:
        total_shares = pos["shares"] + shares
        pos["avg_price"] = (pos["avg_price"] * pos["shares"] + cost) / total_shares
        pos["shares"] = total_shares
    else:
        state["positions"][code] = {"name": name, "shares": shares, "avg_price": float(price)}
    save_portfolio(state)
    return True, f"{name} {shares:,}주 매수 완료"


def sell(state, code, shares, price):
    shares = int(shares)
    pos = state["positions"].get(code)
    if not pos:
        return False, "보유하지 않은 종목입니다."
    if shares <= 0 or shares > pos["shares"]:
        return False, f"매도 수량 오류 (보유 {pos['shares']:,}주)"
    proceeds = shares * price
    state["realized_pnl"] += (price - pos["avg_price"]) * shares
    state["cash"] += proceeds
    pos["shares"] -= shares
    name = pos["name"]
    if pos["shares"] <= 0:
        del state["positions"][code]
    save_portfolio(state)
    return True, f"{name} {shares:,}주 매도 완료"


def record_equity(state, total_asset, date_str):
    """일자별 총자산을 기록(같은 날짜는 갱신). equity curve용."""
    hist = state.setdefault("equity_history", [])
    hist = [h for h in hist if h.get("date") != date_str]
    hist.append({"date": date_str, "total_asset": round(total_asset, 2)})
    hist.sort(key=lambda x: x["date"])
    state["equity_history"] = hist
    save_portfolio(state)


def valuation(state, price_map):
    """현재가(price_map: code->price)로 평가. 총자산·평가손익 등 반환."""
    rows = []
    holdings_value = 0.0
    for code, pos in state["positions"].items():
        cur = price_map.get(code, pos["avg_price"]) or pos["avg_price"]
        mkt = pos["shares"] * cur
        cost = pos["shares"] * pos["avg_price"]
        pnl = mkt - cost
        rows.append({
            "ticker_code": code,
            "name": pos["name"],
            "shares": pos["shares"],
            "avg_price": pos["avg_price"],
            "cur_price": cur,
            "market_value": mkt,
            "pnl": pnl,
            "return_pct": (pnl / cost * 100) if cost else 0.0,
        })
        holdings_value += mkt
    total_asset = state["cash"] + holdings_value
    total_return = total_asset - state["initial_capital"]
    return {
        "rows": rows,
        "holdings_value": holdings_value,
        "cash": state["cash"],
        "total_asset": total_asset,
        "initial_capital": state["initial_capital"],
        "total_return": total_return,
        "total_return_pct": (total_return / state["initial_capital"] * 100) if state["initial_capital"] else 0.0,
        "realized_pnl": state.get("realized_pnl", 0.0),
    }
