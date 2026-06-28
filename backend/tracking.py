"""作戦(daily_plan)の実結果を将来 OHLC から判定する純関数（DB/ネット非依存）。打ち手11。

resolve_outcome: 単一作戦を plan_date 当日含む以降の OHLC から判定（buy/sell・例外を投げない）。
plan_type: レジーム×方向の型分類（v1）。
aggregate_performance: 型別に fill率/勝率/平均R/平均日数を集計（n/a 除外・terminal で勝率/R）。
"""
from __future__ import annotations

EXPIRY_DEFAULT = 5


def _num(x):
    try:
        v = float(x)
        return v if v == v else None          # nan→None
    except (TypeError, ValueError):
        return None


def resolve_outcome(plan: dict, future_bars: list[dict], expiry: int = EXPIRY_DEFAULT) -> dict:
    """plan(direction/limit_price/stop_price/target_price) と plan_date 当日含む以降の OHLC
    (future_bars=[{date,open,high,low,close}] 昇順) から結果判定。

    戻り: {fill_status, outcome, exit_price, result_r, days_held, resolved_date}。
    terminal = fill_status in (n/a, expired) または outcome in (target, stop)。非終端 = pending/open。
    """
    na = {"fill_status": "n/a", "outcome": None, "exit_price": None,
          "result_r": None, "days_held": None, "resolved_date": None}
    direction = plan.get("direction")
    limit = _num(plan.get("limit_price"))
    stop = _num(plan.get("stop_price"))
    target = _num(plan.get("target_price"))
    if direction not in ("buy", "sell") or limit is None or stop is None or target is None:
        return na
    is_buy = direction == "buy"
    risk = (limit - stop) if is_buy else (stop - limit)
    if not (risk > 0):                         # 異常プラン/ゼロ除算回避
        return na

    n = len(future_bars)
    # 1) 約定（i in [0, expiry)・buy: low<=limit / sell: high>=limit）
    fill_i = None
    for i in range(min(expiry, n)):
        lo = _num(future_bars[i].get("low"))
        hi = _num(future_bars[i].get("high"))
        if (is_buy and lo is not None and lo <= limit) or \
           ((not is_buy) and hi is not None and hi >= limit):
            fill_i = i
            break
    if fill_i is None:
        if n >= expiry:                        # 窓満了・未約定 → expired(terminal)
            return {**na, "fill_status": "expired",
                    "resolved_date": str(future_bars[expiry - 1].get("date"))}
        return {**na, "fill_status": "pending"}  # 窓未経過 → 非終端（後日再解決）

    entry = limit
    # 2) 決済（fill_i+1 以降・優先順 stop>target＝backtest と一致）
    for j in range(fill_i + 1, n):
        lo = _num(future_bars[j].get("low"))
        hi = _num(future_bars[j].get("high"))
        hit_stop = (lo is not None and lo <= stop) if is_buy else (hi is not None and hi >= stop)
        hit_tgt = (hi is not None and hi >= target) if is_buy else (lo is not None and lo <= target)
        if hit_stop:
            r = (stop - entry) / risk if is_buy else (entry - stop) / risk
            return {"fill_status": "filled", "outcome": "stop", "exit_price": stop,
                    "result_r": r, "days_held": j - fill_i,
                    "resolved_date": str(future_bars[j].get("date"))}
        if hit_tgt:
            r = (target - entry) / risk if is_buy else (entry - target) / risk
            return {"fill_status": "filled", "outcome": "target", "exit_price": target,
                    "result_r": r, "days_held": j - fill_i,
                    "resolved_date": str(future_bars[j].get("date"))}
    # 未決済 → open(非終端・時価)
    close = _num(future_bars[-1].get("close"))
    r = None if close is None else ((close - entry) / risk if is_buy else (entry - close) / risk)
    return {"fill_status": "filled", "outcome": "open", "exit_price": close,
            "result_r": r, "days_held": (n - 1) - fill_i, "resolved_date": None}


def plan_type(direction, regime) -> str:
    """v1 タクソノミ: 'risk_on:buy' 形式。後で friendly 名に拡張可。"""
    return f"{regime or 'unknown'}:{direction or 'none'}"


def aggregate_performance(rows: list[dict]) -> list[dict]:
    """rows=[{plan_type,fill_status,outcome,result_r,days_held}] を型別集計。

    n_plans/fill_rate の母数は追跡対象（fill_status != 'n/a'）のみ。
    win_rate/avg_r/avg_days は filled かつ terminal(target/stop) のみ。avg_r = 期待値R。
    """
    from collections import defaultdict
    g = defaultdict(list)
    for r in rows:
        if r.get("fill_status") == "n/a":
            continue
        g[r.get("plan_type") or "unknown"].append(r)
    out = []
    for typ in sorted(g):
        items = g[typ]
        term = [x for x in items if x.get("outcome") in ("target", "stop")]
        rs = [x["result_r"] for x in term if x.get("result_r") is not None]
        days = [x["days_held"] for x in term if x.get("days_held") is not None]
        n_filled = sum(1 for x in items if x.get("fill_status") == "filled")
        wins = sum(1 for x in term if x.get("outcome") == "target")
        out.append({"type": typ, "n_plans": len(items), "n_filled": n_filled,
                    "fill_rate": n_filled / len(items) if items else None,
                    "n_resolved": len(term),
                    "win_rate": wins / len(term) * 100 if term else None,
                    "avg_r": sum(rs) / len(rs) if rs else None,
                    "avg_days": sum(days) / len(days) if days else None})
    return out
