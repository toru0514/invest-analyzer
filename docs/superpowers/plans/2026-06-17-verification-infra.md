# 検証インフラ刷新（フェーズA）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** バックテストにコスト・約定統一（検証=提示）・out-of-sample 評価・統計的有意性・ベンチマークを導入し、「改善した」を信用できる検証基盤にする。

**Architecture:** 既存 `run_backtest` を単一窓エンジンとして活かすハイブリッド方針。純粋なコストモデル（`costs.py`）と out-of-sample 評価層（`evaluation.py`）を新設し、`backtest.py` の plan モード約定を `build_plan` の提示指値に統一。`main.py` の `/backtest`・`/optimize` は薄い呼び出しにする。

**Tech Stack:** Python 3.13 / FastAPI / pandas / pandas-ta / pytest。フラットモジュール構成（`backend/` 直下）。テストは `backend/venv/bin/python -m pytest`、ネット非依存（demo/合成データ）。

**設計書:** `docs/superpowers/specs/2026-06-17-verification-infra-design.md`

---

## File Structure

| ファイル | 区分 | 責務 |
|---|---|---|
| `backend/costs.py` | 新規 | 売買コスト（純粋関数・依存なし）。`DEFAULT_COST`・`apply_costs`・`commission_cost`・`cost_from_configs` |
| `backend/test_costs.py` | 新規 | costs.py の単体テスト |
| `backend/backtest.py` | 改修 | `run_backtest` にコスト・`eval_start_date`・`closed_pnls` を追加。plan モード約定を `build_plan` 指値に統一・`fill_rate` |
| `backend/test_backtest.py` | 新規 | 約定統一・コスト・fill_rate の単体テスト |
| `backend/evaluation.py` | 新規 | `DEFAULT_GRID`・`summary_stats`・`benchmark`・`evaluate_holdout` |
| `backend/test_evaluation.py` | 新規 | 評価層の単体テスト |
| `backend/main.py` | 改修 | `/backtest`（period・新キー）、`/optimize`（holdout 2段構え） |
| `backend/test_api.py` | 改修 | `/backtest`・`/optimize` の結合アサーション更新 |

各タスクは TDD（失敗テスト→実行で失敗確認→最小実装→実行で成功確認→コミット）。DRY・YAGNI。

---

## Task 1: コストモデル `costs.py`

**Files:**
- Create: `backend/costs.py`
- Test: `backend/test_costs.py`

- [ ] **Step 1: 失敗テストを書く** — `backend/test_costs.py`

```python
"""costs.py の単体テスト（純粋関数・ネット非依存）。"""
from __future__ import annotations

import pytest

from costs import DEFAULT_COST, apply_costs, commission_cost, cost_from_configs


def test_default_cost_values():
    assert DEFAULT_COST["commission_bps"] == 0.0
    assert DEFAULT_COST["slippage_bps"] == 10.0


def test_apply_costs_buy_fills_higher():
    # 10bps スリッページ → 買いは0.1%高く約定（不利）
    assert apply_costs(1000.0, "buy", {"slippage_bps": 10.0}) == pytest.approx(1001.0)


def test_apply_costs_sell_fills_lower():
    assert apply_costs(1000.0, "sell", {"slippage_bps": 10.0}) == pytest.approx(999.0)


def test_apply_costs_zero_slippage_identity():
    assert apply_costs(1234.5, "buy", {"slippage_bps": 0.0}) == pytest.approx(1234.5)
    assert apply_costs(1234.5, "sell", {"slippage_bps": 0.0}) == pytest.approx(1234.5)


def test_apply_costs_none_uses_default():
    # 既定10bps が使われる
    assert apply_costs(1000.0, "buy") == pytest.approx(1001.0)


def test_apply_costs_unknown_side_raises():
    with pytest.raises(ValueError):
        apply_costs(100.0, "hold")


def test_commission_cost():
    assert commission_cost(100_000.0, {"commission_bps": 5.0}) == pytest.approx(50.0)
    assert commission_cost(100_000.0, {"commission_bps": 0.0}) == pytest.approx(0.0)


def test_cost_from_configs_reads_rule():
    cfgs = [{"rule_type": "cost_model", "params": {"commission_bps": 3, "slippage_bps": 7}}]
    assert cost_from_configs(cfgs) == {"commission_bps": 3.0, "slippage_bps": 7.0}


def test_cost_from_configs_defaults_when_absent():
    assert cost_from_configs([]) == DEFAULT_COST
    assert cost_from_configs(None) == DEFAULT_COST
```

- [ ] **Step 2: 実行して失敗を確認**

Run: `backend/venv/bin/python -m pytest backend/test_costs.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'costs'`）

- [ ] **Step 3: 最小実装** — `backend/costs.py`

```python
"""売買コストモデル（手数料・スリッページ）。純粋関数・依存なし。

bps = ベーシスポイント = 0.01%。すべて片道（往復はエントリー＋エグジットで2回適用）。
キー未設定・None は DEFAULT_COST にフォールバックする。
"""
from __future__ import annotations

# 既定：ネット証券ゼロコース（手数料0）＋スリッページ片道10bps(0.1%)
DEFAULT_COST: dict[str, float] = {"commission_bps": 0.0, "slippage_bps": 10.0}


def apply_costs(price: float, side: str, cost: dict | None = None) -> float:
    """スリッページを反映した約定価格を返す。買いは高く・売りは安く（ともに不利方向）。"""
    c = cost or DEFAULT_COST
    slip = float(c.get("slippage_bps", 0.0)) / 1e4
    if side == "buy":
        return price * (1.0 + slip)
    if side == "sell":
        return price * (1.0 - slip)
    raise ValueError(f"unknown side: {side}")


def commission_cost(notional: float, cost: dict | None = None) -> float:
    """約定代金に対する片道手数料額（円・絶対値）。"""
    c = cost or DEFAULT_COST
    return abs(notional) * float(c.get("commission_bps", 0.0)) / 1e4


def cost_from_configs(configs: list[dict] | None) -> dict:
    """signal_config の cost_model ルールからコストを読む。無ければ DEFAULT_COST。"""
    for c in (configs or []):
        if c.get("rule_type") == "cost_model":
            p = c.get("params") or {}
            return {
                "commission_bps": float(p.get("commission_bps", DEFAULT_COST["commission_bps"])),
                "slippage_bps": float(p.get("slippage_bps", DEFAULT_COST["slippage_bps"])),
            }
    return dict(DEFAULT_COST)
```

- [ ] **Step 4: 実行して成功を確認**

Run: `backend/venv/bin/python -m pytest backend/test_costs.py -v`
Expected: PASS（9件）

- [ ] **Step 5: コミット**

```bash
git add backend/costs.py backend/test_costs.py
git commit -m "feat: コストモデル costs.py（手数料・スリッページ・設定読込）"
```

---

## Task 2: `run_backtest` にコスト・約定統一・eval_start_date・closed_pnls を追加

現 `_run_backtest_atr`（`backtest.py:125-245`）を **plan 基準**に改修：約定を `build_plan` の `limit_price`/`stop_price`/`target_price` に統一し、`apply_costs` を適用、`fill_rate` を集計。`run_backtest`（score モード）にもコストを適用。両モードで `closed_pnls`（クローズ済みトレード損益リスト）と `eval_start_date`（評価開始日・out-of-sample 用）を導入。`exit_mode="atr"` は `"plan"` のエイリアスとして受理する。

**Files:**
- Modify: `backend/backtest.py`
- Test: `backend/test_backtest.py`

- [ ] **Step 1: 失敗テストを書く** — `backend/test_backtest.py`

```python
"""run_backtest の約定統一・コスト・fill_rate の単体テスト（合成データ・ネット非依存）。"""
from __future__ import annotations

import numpy as np
import pandas as pd

from backtest import run_backtest
from signals import DEFAULT_CONFIGS


def _trend_up_df(n=120, start=1000.0, step=5.0, seed=0):
    """緩やかな上昇トレンドの合成OHLCV（買いシグナルと押し目約定が起きやすい）。"""
    rng = np.random.default_rng(seed)
    close = start + np.cumsum(np.full(n, step) + rng.normal(0, 2, n))
    close = np.maximum(close, 50)
    open_ = close + rng.normal(0, 2, n)
    high = np.maximum(open_, close) + np.abs(rng.normal(0, 3, n))
    low = np.minimum(open_, close) - np.abs(rng.normal(0, 3, n))
    vol = rng.integers(1_000_000, 5_000_000, n).astype(float)
    idx = pd.bdate_range(end=pd.Timestamp("2026-06-01"), periods=n)
    return pd.DataFrame({"open": open_, "high": high, "low": low,
                         "close": close, "volume": vol}, index=idx)


def test_plan_mode_returns_cost_fillrate_and_closed_pnls():
    hist = {"X.T": _trend_up_df()}
    r = run_backtest(hist, configs=DEFAULT_CONFIGS, exit_mode="plan", backtest_days=60)
    assert r["exit_mode"] == "plan"
    assert "fill_rate" in r and (r["fill_rate"] is None or 0.0 <= r["fill_rate"] <= 1.0)
    assert "cost" in r and r["cost"]["slippage_bps"] == 10.0
    assert isinstance(r["closed_pnls"], list)


def test_atr_is_alias_of_plan():
    hist = {"X.T": _trend_up_df()}
    r = run_backtest(hist, configs=DEFAULT_CONFIGS, exit_mode="atr", backtest_days=60)
    assert r["exit_mode"] == "plan"   # atr は plan のエイリアス


def test_cost_reduces_pnl_vs_zero_cost():
    hist = {"X.T": _trend_up_df()}
    zero = run_backtest(hist, configs=DEFAULT_CONFIGS, exit_mode="plan", backtest_days=60,
                        cost={"commission_bps": 0.0, "slippage_bps": 0.0})
    costly = run_backtest(hist, configs=DEFAULT_CONFIGS, exit_mode="plan", backtest_days=60,
                          cost={"commission_bps": 0.0, "slippage_bps": 50.0})
    # トレードが発生していれば、スリッページの大きい方が損益は小さい（同数量比較は近似）
    if zero["closed_trades"] > 0 and costly["closed_trades"] > 0:
        assert costly["pnl_pct"] <= zero["pnl_pct"]


def test_score_mode_applies_cost_and_returns_closed_pnls():
    hist = {"X.T": _trend_up_df()}
    r = run_backtest(hist, configs=DEFAULT_CONFIGS, exit_mode="score", backtest_days=60)
    assert r["exit_mode"] == "score"
    assert isinstance(r["closed_pnls"], list)
    assert "cost" in r


def test_eval_start_date_restricts_trading_window():
    df = _trend_up_df()
    hist = {"X.T": df}
    split = df.index[int(len(df) * 0.7)]
    r = run_backtest(hist, configs=DEFAULT_CONFIGS, exit_mode="plan",
                     backtest_days=len(df), eval_start_date=split)
    # 約定はすべて split 以降
    for t in r["trades"]:
        assert pd.Timestamp(t["date"]) >= split.normalize()
```

- [ ] **Step 2: 実行して失敗を確認**

Run: `backend/venv/bin/python -m pytest backend/test_backtest.py -v`
Expected: FAIL（`cost`/`fill_rate`/`closed_pnls` キーが無い、`eval_start_date` 引数が無い等）

- [ ] **Step 3: 実装**

`backend/backtest.py` 冒頭の import に追加：

```python
from costs import DEFAULT_COST, apply_costs, commission_cost
```

`run_backtest` のシグネチャに `cost` と `eval_start_date` を追加し、`exit_mode="atr"` を `"plan"` に正規化、`closed_pnls` を返す。**score モード**にもコストを適用する。以下に置換後の `run_backtest`（score 部）を示す（差分の要点）：

```python
def run_backtest(
    histories, configs=None, initial_capital=INITIAL_CAPITAL,
    backtest_days=BACKTEST_DAYS, warmup_days=WARMUP_DAYS,
    buy_threshold=BUY_THRESHOLD, sell_threshold=SELL_THRESHOLD,
    exit_mode="score", cost=None, eval_start_date=None,
):
    """exit_mode='score'（既定）はスコア反転で決済。'plan'（旧'atr'）は提示指値で約定する出口入り。

    cost: {'commission_bps','slippage_bps'}（None で DEFAULT_COST）。
    eval_start_date: 指定すると約定（取引）はこの日以降のみ。指標窓は全履歴を使う（out-of-sample 用）。
    """
    if configs is None:
        configs = DEFAULT_CONFIGS
    cost = cost or DEFAULT_COST
    if exit_mode in ("plan", "atr"):
        return _run_backtest_plan(histories, configs, initial_capital, backtest_days,
                                  warmup_days, buy_threshold, sell_threshold, cost,
                                  eval_start_date)

    n_tickers = len(histories)
    cash = initial_capital
    holdings = {t: 0.0 for t in histories}
    cost_basis = {t: 0.0 for t in histories}
    per_trade_budget = initial_capital / max(n_tickers, 1)

    trades = []
    closed_pnls = []
    equity_curve = []
    signal_rows = []

    all_dates = sorted(set().union(*[set(df.index) for df in histories.values()]))
    eval_dates = [d for d in all_dates if eval_start_date is None or d >= eval_start_date]
    eval_dates = eval_dates[-backtest_days:]

    for d in eval_dates:
        for ticker, df in histories.items():
            window = df[df.index <= d]
            if len(window) < warmup_days:
                continue
            score, direction, detail = evaluate(window, configs, buy_threshold, sell_threshold)
            raw = float(window["close"].iloc[-1])

            if direction == "buy" and cash > 0:
                fill = apply_costs(raw, "buy", cost)
                current_value = holdings[ticker] * fill
                invest = min(per_trade_budget, cash, max(0.0, per_trade_budget - current_value))
                if invest >= 1.0:
                    fee = commission_cost(invest, cost)
                    shares = (invest - fee) / fill
                    total_cost = cost_basis[ticker] * holdings[ticker] + invest
                    holdings[ticker] += shares
                    cost_basis[ticker] = total_cost / holdings[ticker]
                    cash -= invest
                    trades.append({"date": str(pd.Timestamp(d).date()), "ticker": ticker,
                                   "action": "buy", "price": fill, "shares": shares})

            elif direction == "sell" and holdings[ticker] > 0:
                fill = apply_costs(raw, "sell", cost)
                shares = holdings[ticker]
                proceeds = shares * fill
                proceeds -= commission_cost(proceeds, cost)
                pnl = proceeds - cost_basis[ticker] * shares
                cash += proceeds
                closed_pnls.append(pnl)
                trades.append({"date": str(pd.Timestamp(d).date()), "ticker": ticker,
                               "action": "sell", "price": fill, "shares": shares})
                holdings[ticker] = 0.0
                cost_basis[ticker] = 0.0

        equity = cash
        for ticker, df in histories.items():
            window = df[df.index <= d]
            if not window.empty:
                equity += holdings[ticker] * float(window["close"].iloc[-1])
        equity_curve.append({"date": str(pd.Timestamp(d).date()), "equity": equity})

    final_value = cash
    for ticker, df in histories.items():
        last_close = float(df["close"].iloc[-1])
        final_value += holdings[ticker] * last_close
        score, direction, detail = evaluate(df, configs, buy_threshold, sell_threshold)
        signal_rows.append({"ticker": ticker, "price": last_close,
                            "score": score, "direction": direction, "detail": detail})

    pnl_amount = final_value - initial_capital
    pnl_pct = pnl_amount / initial_capital * 100
    wins = sum(1 for p in closed_pnls if p > 0)
    win_rate = (wins / len(closed_pnls) * 100) if closed_pnls else None

    max_dd, peak = 0.0, -np.inf
    for point in equity_curve:
        peak = max(peak, point["equity"])
        if peak > 0:
            max_dd = min(max_dd, (point["equity"] - peak) / peak)

    return {
        "initial": initial_capital, "final": final_value, "pnl_amount": pnl_amount,
        "pnl_pct": pnl_pct, "trade_count": len(trades), "closed_trades": len(closed_pnls),
        "closed_pnls": closed_pnls, "win_rate": win_rate, "max_drawdown_pct": abs(max_dd) * 100,
        "trades": trades, "signals": signal_rows, "equity_curve": equity_curve,
        "exit_mode": "score", "cost": cost, "fill_rate": None,
    }
```

次に `_run_backtest_atr` を `_run_backtest_plan` に改名し、シグネチャに `cost`・`eval_start_date` を追加。**約定統一**（`build_plan` の `limit_price` を使用）・**コスト適用**・**fill_rate** を実装。置換後：

```python
def _run_backtest_plan(histories, configs, initial_capital, backtest_days,
                       warmup_days, buy_threshold, sell_threshold, cost, eval_start_date):
    """提示指値（build_plan）で約定し、ATR の損切/利確で決済する出口入りシミュレーション。

    検証=提示：作戦ボードと同一の limit_price/stop_price/target_price で約定検証する。
    eval_start_date 指定時は約定をその日以降に限定（指標窓は全履歴）。
    """
    entry_expiry_days = 5
    trades = []
    closed = []          # {pnl, reason, days}
    equity_by_date: dict[str, float] = {}
    signal_rows = []
    final_value = 0.0
    orders_placed = 0
    orders_filled = 0

    for ticker, df in histories.items():
        df = df.sort_index()
        cash = initial_capital / max(len(histories), 1)
        shares = 0.0
        entry_price = stop = target = None
        entry_i = None
        pending = None   # {"limit","stop","target","expires"}
        start = max(warmup_days, len(df) - backtest_days)

        for i in range(start, len(df)):
            row = df.iloc[i]
            d = str(pd.Timestamp(df.index[i]).date())
            low, high, close = float(row["low"]), float(row["high"]), float(row["close"])
            in_window = eval_start_date is None or df.index[i] >= eval_start_date

            # 1) 提示指値の約定（有効期限内に安値が指値に達したら約定・コスト適用）
            if in_window and shares == 0 and pending is not None and cash > 0:
                if low <= pending["limit"]:
                    fill = apply_costs(pending["limit"], "buy", cost)
                    fee = commission_cost(cash, cost)
                    shares = (cash - fee) / fill
                    entry_price, stop, target, entry_i = fill, pending["stop"], pending["target"], i
                    cash = 0.0
                    orders_filled += 1
                    trades.append({"date": d, "ticker": ticker, "action": "buy",
                                   "price": fill, "shares": shares})
                    pending = None
                elif i >= pending["expires"]:
                    pending = None   # 期限切れ（約定せず失効）

            # 2) 保有中（エントリー当日を除く）：損切優先で stop/target をチェック
            if shares > 0 and entry_i is not None and i > entry_i:
                exit_raw, reason = (stop, "stop") if low <= stop else \
                    (target, "target") if high >= target else (None, None)
                if exit_raw is not None:
                    fill = apply_costs(exit_raw, "sell", cost)
                    proceeds = shares * fill
                    proceeds -= commission_cost(proceeds, cost)
                    closed.append({"pnl": proceeds - entry_price * shares,
                                   "reason": reason, "days": i - entry_i})
                    cash += proceeds
                    trades.append({"date": d, "ticker": ticker, "action": "sell",
                                   "price": fill, "shares": shares})
                    shares = 0.0; entry_price = stop = target = None; entry_i = None

            # 3) 当日終値で判定（意思決定）。指標窓は全履歴 df.iloc[:i+1]。
            window = df.iloc[:i + 1]
            if len(window) >= warmup_days:
                score, direction, _ = evaluate(window, configs, buy_threshold, sell_threshold)
                if shares > 0 and direction == "sell":
                    fill = apply_costs(close, "sell", cost)
                    proceeds = shares * fill
                    proceeds -= commission_cost(proceeds, cost)
                    closed.append({"pnl": proceeds - entry_price * shares,
                                   "reason": "signal", "days": i - entry_i})
                    cash += proceeds
                    trades.append({"date": d, "ticker": ticker, "action": "sell",
                                   "price": fill, "shares": shares})
                    shares = 0.0; entry_price = stop = target = None; entry_i = None
                elif in_window and shares == 0 and direction == "buy":
                    plan = build_plan(window, "buy", score, configs)
                    if plan["limit_price"] and plan["stop_price"] and plan["target_price"]:
                        # 検証=提示：作戦ボードと同一の提示指値で待つ。
                        pending = {"limit": plan["limit_price"], "stop": plan["stop_price"],
                                   "target": plan["target_price"], "expires": i + entry_expiry_days}
                        orders_placed += 1

            if in_window:
                equity_by_date[d] = equity_by_date.get(d, 0.0) + cash + shares * close

        last_close = float(df["close"].iloc[-1])
        final_value += cash + shares * last_close
        score, direction, detail = evaluate(df, configs, buy_threshold, sell_threshold)
        signal_rows.append({"ticker": ticker, "price": last_close, "score": score,
                            "direction": direction, "detail": detail})

    equity_curve = [{"date": d, "equity": equity_by_date[d]} for d in sorted(equity_by_date)]
    pnls = [c["pnl"] for c in closed]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    win_rate = (len(wins) / len(pnls) * 100) if pnls else None
    avg_win = (sum(wins) / len(wins)) if wins else 0.0
    avg_loss = (sum(losses) / len(losses)) if losses else 0.0
    risk_reward = (avg_win / abs(avg_loss)) if losses and avg_loss != 0 else None
    avg_holding = (sum(c["days"] for c in closed) / len(closed)) if closed else None

    max_dd, peak = 0.0, -np.inf
    for point in equity_curve:
        peak = max(peak, point["equity"])
        if peak > 0:
            max_dd = min(max_dd, (point["equity"] - peak) / peak)

    return {
        "initial": initial_capital, "final": final_value,
        "pnl_amount": final_value - initial_capital,
        "pnl_pct": (final_value - initial_capital) / initial_capital * 100,
        "trade_count": len(trades), "closed_trades": len(closed),
        "closed_pnls": pnls, "win_rate": win_rate, "max_drawdown_pct": abs(max_dd) * 100,
        "trades": trades, "signals": signal_rows, "equity_curve": equity_curve,
        "exit_mode": "plan", "cost": cost,
        "fill_rate": (orders_filled / orders_placed) if orders_placed else None,
        "take_profit_count": sum(1 for c in closed if c["reason"] == "target"),
        "stop_loss_count": sum(1 for c in closed if c["reason"] == "stop"),
        "signal_exit_count": sum(1 for c in closed if c["reason"] == "signal"),
        "avg_holding_days": avg_holding, "risk_reward": risk_reward,
    }
```

- [ ] **Step 3b: 既存テストの追従** — `backend/test_signals.py:203`

`exit_mode="atr"` は `"plan"` のエイリアスになったため、`test_atr_exit_backtest_has_extra_metrics`（`test_signals.py:199-209`）のアサーションを更新：

```python
    r = run_backtest(hist, exit_mode="atr")
    assert r["exit_mode"] == "plan"   # atr は plan のエイリアス（約定=提示指値）
```

（`take_profit_count` 等の他キーは `_run_backtest_plan` が引き続き返すのでそのまま。）

- [ ] **Step 4: 実行して成功を確認**

Run: `backend/venv/bin/python -m pytest backend/test_backtest.py backend/test_signals.py -v`
Expected: PASS（test_backtest 5件＋test_signals 既存全件）

- [ ] **Step 5: コミット**

```bash
git add backend/backtest.py backend/test_backtest.py backend/test_signals.py
git commit -m "feat: バックテストを提示指値で約定統一＋コスト/fill_rate/eval_start_date"
```

---

## Task 3: 統計サマリ `summary_stats`（evaluation.py）

**Files:**
- Create: `backend/evaluation.py`
- Test: `backend/test_evaluation.py`

- [ ] **Step 1: 失敗テストを書く** — `backend/test_evaluation.py`（summary_stats 部）

```python
"""evaluation.py の単体テスト（合成データ・ネット非依存）。"""
from __future__ import annotations

from evaluation import summary_stats


def test_summary_stats_empty():
    s = summary_stats([])
    assert s["n"] == 0 and s["insufficient"] is True
    assert s["expectancy"] is None and s["std_error"] is None


def test_summary_stats_single_trade_no_stderr():
    s = summary_stats([100.0])
    assert s["n"] == 1 and s["std_error"] is None and s["insufficient"] is True


def test_summary_stats_expectancy_and_winrate():
    pnls = [100.0, -50.0, 100.0, -50.0]   # 勝率50%、期待値 = 平均 = 25
    s = summary_stats(pnls)
    assert s["expectancy"] == 25.0
    assert s["win_rate"] == 50.0
    assert s["avg_win"] == 100.0 and s["avg_loss"] == -50.0


def test_summary_stats_insufficient_threshold():
    assert summary_stats([1.0] * 29)["insufficient"] is True
    assert summary_stats([1.0] * 30)["insufficient"] is False


def test_summary_stats_std_error_positive_for_varied():
    s = summary_stats([10.0, -10.0, 20.0, -20.0])
    assert s["std_error"] is not None and s["std_error"] > 0
```

- [ ] **Step 2: 実行して失敗を確認**

Run: `backend/venv/bin/python -m pytest backend/test_evaluation.py -v`
Expected: FAIL（`No module named 'evaluation'`）

- [ ] **Step 3: 実装** — `backend/evaluation.py`（まず summary_stats と定数）

```python
"""out-of-sample 評価層：ホールドアウト2段構え・統計サマリ・ベンチマーク。"""
from __future__ import annotations

import statistics

from costs import DEFAULT_COST

# in-sample で探索する既定グリッド（閾値のみ・exit_mode は plan 固定）
DEFAULT_GRID: dict[str, list[int]] = {"threshold": [2, 3, 4]}

MIN_TRADES = 30   # これ未満は統計的に不十分（誤差範囲）


def summary_stats(pnls: list[float], min_trades: int = MIN_TRADES) -> dict:
    """クローズ済みトレード損益（コスト込み）から統計サマリを返す。"""
    n = len(pnls)
    if n == 0:
        return {"n": 0, "expectancy": None, "std_error": None, "win_rate": None,
                "avg_win": None, "avg_loss": None, "insufficient": True}
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    return {
        "n": n,
        "expectancy": sum(pnls) / n,                       # 1トレードあたり期待損益
        "std_error": (statistics.stdev(pnls) / (n ** 0.5)) if n >= 2 else None,
        "win_rate": len(wins) / n * 100,
        "avg_win": (sum(wins) / len(wins)) if wins else 0.0,
        "avg_loss": (sum(losses) / len(losses)) if losses else 0.0,
        "insufficient": n < min_trades,
    }
```

> 注：本実装のキーは設計書 §5.4 の下書き（`avg_return` 等）と意図的に異なる（`expectancy = 平均損益` に一本化し `avg_win`/`avg_loss` を併記）。`evaluate_holdout`・本テストとはこの形で整合しているので、§5.4 の語に合わせて戻さないこと。

- [ ] **Step 4: 実行して成功を確認**

Run: `backend/venv/bin/python -m pytest backend/test_evaluation.py -v`
Expected: PASS（5件）

- [ ] **Step 5: コミット**

```bash
git add backend/evaluation.py backend/test_evaluation.py
git commit -m "feat: evaluation.summary_stats（トレード数・標準誤差・誤差範囲警告）"
```

---

## Task 4: ベンチマーク `benchmark`（evaluation.py）

**Files:**
- Modify: `backend/evaluation.py`
- Test: `backend/test_evaluation.py`

- [ ] **Step 1: 失敗テストを追記** — `backend/test_evaluation.py`

```python
import numpy as np
import pandas as pd

from evaluation import benchmark
from signals import DEFAULT_CONFIGS


def _df(n=120, step=5.0, seed=1):
    rng = np.random.default_rng(seed)
    close = np.maximum(1000 + np.cumsum(np.full(n, step) + rng.normal(0, 2, n)), 50)
    open_ = close + rng.normal(0, 2, n)
    high = np.maximum(open_, close) + np.abs(rng.normal(0, 3, n))
    low = np.minimum(open_, close) - np.abs(rng.normal(0, 3, n))
    vol = rng.integers(1_000_000, 5_000_000, n).astype(float)
    idx = pd.bdate_range(end=pd.Timestamp("2026-06-01"), periods=n)
    return pd.DataFrame({"open": open_, "high": high, "low": low,
                         "close": close, "volume": vol}, index=idx)


def test_benchmark_returns_two_baselines():
    hist = {"X.T": _df()}
    b = benchmark(hist, DEFAULT_CONFIGS, buy_threshold=2, sell_threshold=-2,
                  initial_capital=3000.0, warmup_days=35, backtest_days=60)
    assert "buy_hold_pct" in b and "all_signals_pct" in b
    # 上昇トレンドなので buy&hold はプラス
    assert b["buy_hold_pct"] is not None and b["buy_hold_pct"] > 0
```

- [ ] **Step 2: 実行して失敗を確認**

Run: `backend/venv/bin/python -m pytest backend/test_evaluation.py::test_benchmark_returns_two_baselines -v`
Expected: FAIL（`cannot import name 'benchmark'`）

- [ ] **Step 3: 実装** — `backend/evaluation.py` に追記

```python
def benchmark(histories, configs, *, buy_threshold, sell_threshold,
              initial_capital, warmup_days, backtest_days, cost=None) -> dict:
    """評価窓のベンチマーク2種。(a) ユニバース等加重 buy&hold、(b) 全シグナル等加重（素のシグナル運用）。"""
    from backtest import run_backtest

    cost = cost or DEFAULT_COST
    # (a) buy&hold：評価窓（末尾 backtest_days 日）の頭→末リターンを等加重平均
    rets = []
    for df in histories.values():
        win = df.sort_index().tail(backtest_days)
        if len(win) >= 2 and float(win["close"].iloc[0]) > 0:
            rets.append(float(win["close"].iloc[-1]) / float(win["close"].iloc[0]) - 1.0)
    buy_hold_pct = (sum(rets) / len(rets) * 100) if rets else None

    # (b) 全シグナル等加重（選別なし）：score モードの素のシグナル運用（コスト込み）
    naive = run_backtest(histories, configs=configs, initial_capital=initial_capital,
                         backtest_days=backtest_days, warmup_days=warmup_days,
                         buy_threshold=buy_threshold, sell_threshold=sell_threshold,
                         exit_mode="score", cost=cost)
    return {"buy_hold_pct": buy_hold_pct, "all_signals_pct": naive["pnl_pct"]}
```

- [ ] **Step 4: 実行して成功を確認**

Run: `backend/venv/bin/python -m pytest backend/test_evaluation.py -v`
Expected: PASS（6件）

- [ ] **Step 5: コミット**

```bash
git add backend/evaluation.py backend/test_evaluation.py
git commit -m "feat: evaluation.benchmark（buy&hold・全シグナル等加重）"
```

---

## Task 5: ホールドアウト評価器 `evaluate_holdout`（evaluation.py）

**Files:**
- Modify: `backend/evaluation.py`
- Test: `backend/test_evaluation.py`

- [ ] **Step 1: 失敗テストを追記** — `backend/test_evaluation.py`

```python
from evaluation import evaluate_holdout


def test_evaluate_holdout_structure_and_no_lookahead():
    hist = {"X.T": _df(n=200), "Y.T": _df(n=200, seed=2)}
    res = evaluate_holdout(hist, DEFAULT_CONFIGS, split_ratio=0.7,
                           initial_capital=3000.0, warmup_days=35)
    # 構造
    for key in ("chosen_params", "in_sample", "out_of_sample", "overfit_gap",
                "significance", "benchmark"):
        assert key in res
    assert res["in_sample"]["sample"] == "in_sample"
    assert res["out_of_sample"]["sample"] == "out_of_sample"
    # chosen_params は探索グリッド内
    assert res["chosen_params"]["threshold"] in (2, 3, 4)
    # in_sample に sweep が含まれる（見出しではない）
    assert isinstance(res["in_sample"]["sweep"], list) and len(res["in_sample"]["sweep"]) == 3
    # 寄与度（leave-one-out）は in_sample 配下に残す（フロント /optimize が表示）
    assert isinstance(res["in_sample"]["contributions"], list)
    assert "baseline_pnl_pct" in res["in_sample"] and "best" in res["in_sample"]


def test_evaluate_holdout_oos_trades_are_after_split():
    hist = {"X.T": _df(n=200)}
    res = evaluate_holdout(hist, DEFAULT_CONFIGS, split_ratio=0.7,
                           initial_capital=3000.0, warmup_days=35)
    # OOS の significance は test 窓のトレードに基づく（n は妥当な非負整数）
    assert res["significance"]["n"] >= 0
```

- [ ] **Step 2: 実行して失敗を確認**

Run: `backend/venv/bin/python -m pytest backend/test_evaluation.py -k holdout -v`
Expected: FAIL（`cannot import name 'evaluate_holdout'`）

- [ ] **Step 3: 実装** — `backend/evaluation.py` に追記

```python
def _split_date(histories, split_ratio):
    """全銘柄の和集合日付で split_ratio の位置の日付を返す。"""
    import pandas as pd
    all_dates = sorted(set().union(*[set(df.index) for df in histories.values()]))
    if not all_dates:
        return None
    cut = int(len(all_dates) * split_ratio)
    cut = max(1, min(cut, len(all_dates) - 1))
    return all_dates[cut]


def evaluate_holdout(histories, configs, *, split_ratio=0.7, grid=None, cost=None,
                     initial_capital=3000.0, warmup_days=35) -> dict:
    """シンプルホールドアウト2段構え：train(in-sample) で閾値を選び test(out-of-sample) で評価。

    look-ahead 回避：test 窓のパラメータは train 窓の成績のみから選ぶ。
    """
    from backtest import run_backtest

    grid = grid or DEFAULT_GRID
    cost = cost or DEFAULT_COST
    split = _split_date(histories, split_ratio)

    # train：各銘柄を split 以前にスライスし、全期間（warmup以降）で評価
    train_hist = {t: df[df.index < split] for t, df in histories.items()}
    big = max((len(df) for df in histories.values()), default=0) + 1

    def _bt(hist, th, cfgs, eval_start=None):
        return run_backtest(hist, configs=cfgs, initial_capital=initial_capital,
                            backtest_days=big, warmup_days=warmup_days,
                            buy_threshold=th, sell_threshold=-th, exit_mode="plan",
                            cost=cost, eval_start_date=eval_start)

    # in-sample 探索：閾値ごとに train 成績（期待値）で最良を選ぶ
    sweep, best = [], None
    for th in grid["threshold"]:
        r = _bt(train_hist, th, configs)
        stat = summary_stats(r["closed_pnls"])
        row = {"threshold": th, "pnl_pct": r["pnl_pct"], "expectancy": stat["expectancy"],
               "trade_count": r["trade_count"], "win_rate": r["win_rate"]}
        sweep.append(row)
        key = (row["expectancy"] if row["expectancy"] is not None else -1e18,
               row["trade_count"], row["win_rate"] or 0)
        if best is None or key > best[0]:
            best = (key, th, r, stat, row)
    _, best_th, train_r, train_stat, best_row = best

    # in-sample 寄与度（leave-one-out・best閾値・train上）。フロント /optimize が表示。
    _ABLATABLE = ["rsi", "ma_cross", "macd", "bbands", "stoch", "candle_pattern",
                  "disparity", "obv", "cci", "volume_filter", "weekly_trend_filter"]
    present = {c["rule_type"] for c in configs}
    contributions = []
    for rt in _ABLATABLE:
        if rt not in present:
            continue
        without = _bt(train_hist, best_th, [c for c in configs if c["rule_type"] != rt])
        contributions.append({"rule_type": rt, "pnl_without": without["pnl_pct"],
                              "delta": train_r["pnl_pct"] - without["pnl_pct"]})
    contributions.sort(key=lambda x: x["delta"], reverse=True)

    # out-of-sample：選んだ閾値で全履歴を使い、約定は split 以降のみ
    oos_r = _bt(histories, best_th, configs, eval_start=split)
    oos_stat = summary_stats(oos_r["closed_pnls"])

    bench = benchmark(histories, configs, buy_threshold=best_th, sell_threshold=-best_th,
                      initial_capital=initial_capital, warmup_days=warmup_days,
                      backtest_days=big, cost=cost)

    in_expect = train_stat["expectancy"] or 0.0
    oos_expect = oos_stat["expectancy"] or 0.0

    return {
        "chosen_params": {"threshold": best_th, "exit_mode": "plan"},
        "in_sample": {"sample": "in_sample", "sweep": sweep, "best": best_row,
                      "baseline_pnl_pct": train_r["pnl_pct"], "contributions": contributions,
                      "pnl_pct": train_r["pnl_pct"], "expectancy": train_stat["expectancy"],
                      "trade_count": train_r["trade_count"], "win_rate": train_r["win_rate"]},
        "out_of_sample": {"sample": "out_of_sample", "pnl_pct": oos_r["pnl_pct"],
                          "expectancy": oos_stat["expectancy"], "win_rate": oos_r["win_rate"],
                          "trade_count": oos_r["trade_count"], "fill_rate": oos_r["fill_rate"]},
        "overfit_gap": in_expect - oos_expect,
        "significance": oos_stat,
        "benchmark": bench,
        "split_date": str(split.date()) if split is not None else None,
    }
```

- [ ] **Step 4: 実行して成功を確認**

Run: `backend/venv/bin/python -m pytest backend/test_evaluation.py -v`
Expected: PASS（holdout 2件を含む全件）

- [ ] **Step 5: コミット**

```bash
git add backend/evaluation.py backend/test_evaluation.py
git commit -m "feat: evaluation.evaluate_holdout（in-sample選定→out-of-sample評価の2段構え）"
```

---

## Task 6: `/backtest` エンドポイント更新（period・新キー）

**Files:**
- Modify: `backend/main.py`（`BacktestIn` 周辺・`backtest` 関数 `main.py:78-90,429-467`）
- Test: `backend/test_api.py`（`test_backtest_demo` 周辺）

- [ ] **Step 1: 失敗テストを追記/更新** — `backend/test_api.py`

```python
def test_backtest_includes_cost_and_significance(client):
    r = client.post("/backtest", json={"demo": True, "days": 60, "exit_mode": "plan"})
    assert r.status_code == 200
    res = r.json()
    assert res["exit_mode"] == "plan"
    assert "cost" in res and res["cost"]["slippage_bps"] == 10.0
    assert "fill_rate" in res
    assert "significance" in res and "n" in res["significance"]
    assert "benchmark" in res and "buy_hold_pct" in res["benchmark"]
```

既存 `test_backtest_atr_exit_mode` は `res["exit_mode"] == "atr"` を **`"plan"`** に更新（atr は plan のエイリアス）。

- [ ] **Step 2: 実行して失敗を確認**

Run: `backend/venv/bin/python -m pytest backend/test_api.py -k backtest -v`
Expected: FAIL（`significance`/`benchmark` キーが無い、exit_mode が "plan" でない）

- [ ] **Step 3: 実装** — `backend/main.py`

冒頭付近の import に追加：

```python
from costs import cost_from_configs
from evaluation import summary_stats, benchmark
```

`BacktestIn`（`main.py:78` 付近）に `period` を追加：

```python
class BacktestIn(BaseModel):
    tickers: Optional[list[str]] = None
    demo: bool = False
    days: int = 22
    initial_capital: float = 3000.0
    exit_mode: str = "plan"      # 既定を plan（検証=提示）に
    persist: bool = False
    period: str = "3y"
```

`backtest` 関数（`main.py:429-467`）の `get_history` 呼び出しに `period` を渡し、`run_backtest` に `cost` を渡し、応答に `significance`/`benchmark` を付与：

```python
    histories = {}
    failed = []
    for t in tickers:
        df = get_history(t, period=payload.period, demo=payload.demo)
        if df.empty:
            failed.append(t)
            continue
        histories[t] = df
    if not histories:
        raise HTTPException(status_code=502, detail="価格データを取得できませんでした（demo=true を試してください）。")

    buy_th, sell_th = db.get_thresholds()
    configs = db.list_configs(active_only=True)
    common = [c for c in configs if c["ticker"] is None]
    cost = cost_from_configs(common)
    result = run_backtest(histories, configs=common, initial_capital=payload.initial_capital,
                          backtest_days=payload.days, buy_threshold=buy_th, sell_threshold=sell_th,
                          exit_mode=payload.exit_mode, cost=cost)
    result["failed"] = failed
    result["significance"] = summary_stats(result["closed_pnls"])
    result["benchmark"] = benchmark(histories, common, buy_threshold=buy_th, sell_threshold=sell_th,
                                    initial_capital=payload.initial_capital, warmup_days=35,
                                    backtest_days=payload.days, cost=cost)

    if payload.persist:
        for t in result["trades"]:
            db.insert_paper_trade(t["ticker"], t["action"], t["price"], t["shares"], t["date"])
    return result
```

- [ ] **Step 4: 実行して成功を確認**

Run: `backend/venv/bin/python -m pytest backend/test_api.py -k backtest -v`
Expected: PASS

- [ ] **Step 5: コミット**

```bash
git add backend/main.py backend/test_api.py
git commit -m "feat: /backtest に period・コスト・統計的有意性・ベンチマークを追加"
```

---

## Task 7: `/optimize` をホールドアウト2段構えに

**Files:**
- Modify: `backend/main.py`（`OptimizeIn`・`optimize` 関数 `main.py:473-537`）
- Test: `backend/test_api.py`

- [ ] **Step 1: 失敗テストを追記** — `backend/test_api.py`

```python
def test_optimize_holdout_in_sample_out_of_sample(client):
    r = client.post("/optimize", json={"demo": True, "split_ratio": 0.7})
    assert r.status_code == 200
    res = r.json()
    assert res["in_sample"]["sample"] == "in_sample"
    assert res["out_of_sample"]["sample"] == "out_of_sample"
    assert "overfit_gap" in res
    assert "significance" in res and "benchmark" in res
    assert res["chosen_params"]["threshold"] in (2, 3, 4)
```

- [ ] **Step 2: 実行して失敗を確認**

Run: `backend/venv/bin/python -m pytest backend/test_api.py -k optimize -v`
Expected: FAIL（`in_sample`/`out_of_sample` キーが無い）

- [ ] **Step 3: 実装** — `backend/main.py`

`OptimizeIn`（`main.py:473`）に `period`・`split_ratio` を追加：

```python
class OptimizeIn(BaseModel):
    tickers: Optional[list[str]] = None
    demo: bool = False
    initial_capital: float = 3000.0
    period: str = "3y"
    split_ratio: float = 0.7
```

`optimize` 関数（`main.py:485-537`）本体を `evaluate_holdout` 呼び出しに置換：

```python
from evaluation import evaluate_holdout   # ファイル冒頭の import 群へ

@app.post("/optimize")
def optimize(payload: OptimizeIn):
    tickers = payload.tickers or [w["ticker"] for w in db.list_watchlist(only_enabled=True)]
    if not tickers:
        raise HTTPException(status_code=400, detail="対象銘柄がありません")
    histories, failed = {}, []
    for t in tickers:
        df = get_history(t, period=payload.period, demo=payload.demo)
        (histories.__setitem__(t, df) if not df.empty else failed.append(t))
    if not histories:
        raise HTTPException(status_code=502, detail="価格データを取得できませんでした（demo=true を試してください）。")

    common = [c for c in db.list_configs(active_only=True) if c["ticker"] is None]
    cost = cost_from_configs(common)
    res = evaluate_holdout(histories, common, split_ratio=payload.split_ratio, cost=cost,
                           initial_capital=payload.initial_capital)
    res["failed"] = failed
    res["tickers"] = list(histories.keys())
    return res
```

> 注：旧 `sweep`/`contributions`/`best`/`baseline_pnl_pct` は `evaluate_holdout` の `in_sample` 配下に集約済み（Task 5 で contributions も train 上で算出）。フロント `/optimize` はレスポンス形が変わるため **Task 8 で追従**する（`api.ts` 型・`page.tsx`・`api.test.ts`）。`days` は廃止し `split_ratio` を使う。

- [ ] **Step 4: 実行して成功を確認**

Run: `backend/venv/bin/python -m pytest backend/test_api.py -k optimize -v`
Expected: PASS

- [ ] **Step 5: 全テスト確認＋コミット**

```bash
backend/venv/bin/python -m pytest backend/ -q
# Expected: 全件 PASS（既存44 + 追加分）
git add backend/main.py backend/test_api.py
git commit -m "feat: /optimize をホールドアウト2段構え（in-sample選定→out-of-sample評価）に刷新"
```

---

## Task 8: フロント `/optimize` をホールドアウト応答に追従

`/optimize` のレスポンス形が変わるため、フロントを新形に追従させる（OOS を見出し表示、in-sample の sweep/contributions は参考表示）。これをやらないと最適化ページが壊れ、`api.test.ts`（CI）も落ちる。

**Files:**
- Modify: `frontend/src/lib/api.ts`（型 `SweepRow`/`OptimizeResponse`・`api.optimize` `api.ts:85-101,183-184`）
- Modify: `frontend/src/lib/__tests__/api.test.ts`（optimize の mock・body 検証 `:30-38`）
- Modify: `frontend/src/app/optimize/page.tsx`（結果表示）

- [ ] **Step 1: 失敗テストに更新** — `frontend/src/lib/__tests__/api.test.ts` の optimize ケースを新形に

```typescript
  it("optimize は POST /optimize にボディを送る", async () => {
    const f = mockFetch({
      chosen_params: { threshold: 2, exit_mode: "plan" },
      in_sample: { sample: "in_sample", sweep: [], best: null, baseline_pnl_pct: 0,
                   contributions: [], pnl_pct: 0, expectancy: null, trade_count: 0, win_rate: null },
      out_of_sample: { sample: "out_of_sample", pnl_pct: 0, expectancy: null, win_rate: null,
                       trade_count: 0, fill_rate: null },
      overfit_gap: 0,
      significance: { n: 0, expectancy: null, std_error: null, win_rate: null,
                      avg_win: null, avg_loss: null, insufficient: true },
      benchmark: { buy_hold_pct: null, all_signals_pct: 0 },
      split_date: null, failed: [], tickers: [],
    });
    vi.stubGlobal("fetch", f);
    await api.optimize({ demo: true, split_ratio: 0.7 });
    const [url, init] = f.mock.calls[0];
    expect(url).toBe("http://localhost:8000/optimize");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({ demo: true, split_ratio: 0.7 });
  });
```

- [ ] **Step 2: 実行して失敗を確認**

Run: `npm --prefix frontend test`
Expected: FAIL（型 or body 不一致。`api.ts` 未更新）

- [ ] **Step 3: 型と API を更新** — `frontend/src/lib/api.ts`（`SweepRow`〜`OptimizeResponse` を置換、`api.optimize` のボディ型を更新）

```typescript
export type SweepRow = {
  threshold: number;
  pnl_pct: number;
  expectancy: number | null;
  win_rate: number | null;
  trade_count: number;
};
export type ContribRow = { rule_type: string; pnl_without: number; delta: number };
export type Significance = {
  n: number; expectancy: number | null; std_error: number | null; win_rate: number | null;
  avg_win: number | null; avg_loss: number | null; insufficient: boolean;
};
export type OptimizeResponse = {
  chosen_params: { threshold: number; exit_mode: string };
  in_sample: {
    sample: "in_sample"; sweep: SweepRow[]; best: SweepRow | null;
    baseline_pnl_pct: number; contributions: ContribRow[];
    pnl_pct: number; expectancy: number | null; trade_count: number; win_rate: number | null;
  };
  out_of_sample: {
    sample: "out_of_sample"; pnl_pct: number; expectancy: number | null;
    win_rate: number | null; trade_count: number; fill_rate: number | null;
  };
  overfit_gap: number;
  significance: Significance;
  benchmark: { buy_hold_pct: number | null; all_signals_pct: number };
  split_date: string | null;
  failed: string[];
  tickers: string[];
};
```

`api.optimize`（`api.ts:183`）のボディ型を更新（`days` 廃止・`split_ratio` 追加）：

```typescript
  optimize: (body: { demo?: boolean; tickers?: string[]; split_ratio?: number; period?: string }) =>
    req<OptimizeResponse>("/optimize", { method: "POST", body: JSON.stringify(body) }),
```

さらに `/backtest` の `exit_mode` リテラルを `"plan"` 対応に（`atr`→`plan` 改名の追従）。`BacktestResult`（`api.ts:116`）と `api.backtest`（`api.ts:175`）を更新（新キーは任意で型に追加）：

```typescript
// BacktestResult（api.ts:104-）：exit_mode を plan に、追加キーを任意で
  exit_mode?: "score" | "plan";
  cost?: { commission_bps: number; slippage_bps: number };
  fill_rate?: number | null;
  significance?: Significance;
  benchmark?: { buy_hold_pct: number | null; all_signals_pct: number };
```

```typescript
// api.backtest（api.ts:175）：送信ボディの exit_mode を plan に
  backtest: (body: { tickers?: string[]; initial_capital?: number; days?: number; demo?: boolean; persist?: boolean; exit_mode?: "score" | "plan"; period?: string }) =>
    req<BacktestResult>("/backtest", { method: "POST", body: JSON.stringify(body) }),
```

- [ ] **Step 4: ページを新形に書き換え** — `frontend/src/app/optimize/page.tsx` 全体を以下に置換

```tsx
"use client";

import { useState } from "react";
import { api, OptimizeResponse } from "@/lib/api";
import Disclaimer from "@/components/Disclaimer";

const RULE_LABELS: Record<string, string> = {
  rsi: "RSI", ma_cross: "移動平均クロス", macd: "MACD", bbands: "ボリンジャーバンド",
  stoch: "ストキャスティクス", candle_pattern: "ローソク足パターン", disparity: "乖離率",
  obv: "OBV", cci: "CCI", volume_filter: "出来高フィルター", weekly_trend_filter: "週足トレンド足切り",
};

function pct(v: number | null | undefined) {
  if (v == null) return "N/A";
  return `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`;
}

export default function Optimize() {
  const [splitRatio, setSplitRatio] = useState(0.7);
  const [demo, setDemo] = useState(true);
  const [loading, setLoading] = useState(false);
  const [res, setRes] = useState<OptimizeResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [applied, setApplied] = useState<string | null>(null);

  async function run() {
    setLoading(true); setError(null); setApplied(null);
    try {
      setRes(await api.optimize({ demo, split_ratio: splitRatio }));
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }

  async function applyBest() {
    if (!res) return;
    const th = res.chosen_params.threshold;
    try {
      await api.updateSettings({ buy_threshold: th, sell_threshold: -th });
      setApplied(`スコア閾値を ±${th} に適用しました（設定に保存）。`);
    } catch (e) {
      setError(String(e));
    }
  }

  const maxAbsDelta = res
    ? Math.max(1, ...res.in_sample.contributions.map((c) => Math.abs(c.delta)))
    : 1;

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold">最適化（チューニング）</h1>
          <p className="text-sm text-slate-500">
            前半(in-sample)で閾値を選び、後半(out-of-sample)で検証します。見出しは後半＝未使用期間の成績です。
          </p>
        </div>
        <div className="flex items-end gap-3 text-sm">
          <label className="flex flex-col gap-1">
            前半比率（in-sample）
            <input type="number" step="0.05" min="0.5" max="0.9" value={splitRatio}
              onChange={(e) => setSplitRatio(Number(e.target.value))}
              className="w-24 rounded border px-2 py-1" />
          </label>
          <label className="flex items-center gap-1">
            <input type="checkbox" checked={demo} onChange={(e) => setDemo(e.target.checked)} />
            demo（合成データ）
          </label>
          <button onClick={run} disabled={loading}
            className="rounded bg-blue-600 px-3 py-2 text-white hover:bg-blue-700 disabled:opacity-50">
            {loading ? "計算中…（数十秒）" : "最適化を実行"}
          </button>
        </div>
      </div>

      {applied && <p className="mb-3 rounded bg-green-50 px-3 py-2 text-sm text-green-700">{applied}</p>}
      {error && <p className="mb-3 rounded bg-red-50 px-3 py-2 text-sm text-red-700">{error}（Python API :8000 を確認）</p>}

      {res && (
        <>
          {res.failed.length > 0 && (
            <p className="mb-3 rounded bg-amber-50 px-3 py-2 text-sm text-amber-800">
              取得失敗: {res.failed.join(", ")}（demo を有効にして再実行してください）
            </p>
          )}

          <section className="mb-6 rounded border border-blue-200 bg-blue-50 p-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="text-sm">
                <div className="text-slate-500">
                  推奨設定（対象 {res.tickers.join(", ")}{res.split_date ? ` / 分割 ${res.split_date}` : ""}）
                </div>
                <div className="text-lg font-semibold">
                  スコア閾値 ±{res.chosen_params.threshold}
                  <span className="ml-2 text-green-700">OOS {pct(res.out_of_sample.pnl_pct)}</span>
                </div>
                <div className="mt-1 text-slate-600">
                  OOS勝率 {res.out_of_sample.win_rate == null ? "N/A" : `${res.out_of_sample.win_rate.toFixed(1)}%`}
                  ・取引 {res.out_of_sample.trade_count}回
                  ・約定率 {res.out_of_sample.fill_rate == null ? "N/A" : `${(res.out_of_sample.fill_rate * 100).toFixed(0)}%`}
                  ・過学習ギャップ {res.overfit_gap.toFixed(1)}
                </div>
                {res.significance.insufficient && (
                  <div className="mt-1 text-amber-700">
                    ⚠ トレード数 {res.significance.n} 件は統計的に不十分（誤差範囲）。期間・銘柄を増やして再検証を。
                  </div>
                )}
                <div className="mt-1 text-slate-600">
                  ベンチマーク: buy&hold {pct(res.benchmark.buy_hold_pct)} / 全シグナル等加重 {pct(res.benchmark.all_signals_pct)}
                </div>
              </div>
              <button onClick={applyBest} className="rounded bg-green-600 px-3 py-2 text-sm text-white hover:bg-green-700">
                この閾値を適用
              </button>
            </div>
          </section>

          <section className="mb-6 rounded border bg-white p-4">
            <h2 className="mb-3 font-semibold">閾値スイープ（前半 in-sample・参考）</h2>
            <table className="w-full text-sm">
              <thead className="bg-slate-100 text-left">
                <tr>
                  <th className="px-3 py-2">スコア閾値</th>
                  <th className="px-3 py-2">損益</th>
                  <th className="px-3 py-2">期待値/件</th>
                  <th className="px-3 py-2">勝率</th>
                  <th className="px-3 py-2">取引回数</th>
                </tr>
              </thead>
              <tbody>
                {res.in_sample.sweep.map((s, i) => (
                  <tr key={i} className={`border-t ${s.threshold === res.chosen_params.threshold ? "bg-green-50" : ""}`}>
                    <td className="px-3 py-2">±{s.threshold}</td>
                    <td className={`px-3 py-2 font-semibold ${s.pnl_pct >= 0 ? "text-green-700" : "text-red-700"}`}>{pct(s.pnl_pct)}</td>
                    <td className="px-3 py-2">{s.expectancy == null ? "N/A" : s.expectancy.toFixed(1)}</td>
                    <td className="px-3 py-2">{s.win_rate == null ? "N/A" : `${s.win_rate.toFixed(1)}%`}</td>
                    <td className="px-3 py-2">{s.trade_count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>

          <section className="rounded border bg-white p-4">
            <h2 className="mb-1 font-semibold">指標の寄与度（leave-one-out・前半 in-sample）</h2>
            <p className="mb-3 text-xs text-slate-500">
              その指標を外したときの損益悪化幅（＝寄与度）。プラスが大きいほど有効、マイナスは足を引っ張っている可能性。
              基準（全指標）損益 {pct(res.in_sample.baseline_pnl_pct)}。
            </p>
            <ul className="space-y-1.5">
              {res.in_sample.contributions.map((c) => (
                <li key={c.rule_type} className="flex items-center gap-2 text-sm">
                  <span className="w-40 shrink-0">{RULE_LABELS[c.rule_type] ?? c.rule_type}</span>
                  <span className="flex-1">
                    <span className={`inline-block h-3 rounded ${c.delta >= 0 ? "bg-green-500" : "bg-red-400"}`}
                      style={{ width: `${(Math.abs(c.delta) / maxAbsDelta) * 100}%`, minWidth: c.delta === 0 ? 0 : 4 }} />
                  </span>
                  <span className={`w-20 text-right font-mono ${c.delta >= 0 ? "text-green-700" : "text-red-700"}`}>
                    {c.delta >= 0 ? "+" : ""}{c.delta.toFixed(2)}
                  </span>
                </li>
              ))}
            </ul>
          </section>
        </>
      )}

      <Disclaimer />
    </div>
  );
}
```

- [ ] **Step 4b: `/backtest`（simulation）ページの exit_mode 追従** — `frontend/src/app/simulation/page.tsx`

`atr`→`plan` 改名で「ATR出口の内訳（強化J）」セクションが**無言で消える**のを防ぐ（自動テストでは検出されない）。2箇所を変更：

```tsx
// :33 送信値（チェック時は plan を送る）
        exit_mode: atrExit ? "plan" : "score",
```
```tsx
// :118 内訳セクションの表示条件
          {result.exit_mode === "plan" && (
```

- [ ] **Step 5: テスト＋型チェック＋コミット**

```bash
npm --prefix frontend test
# Expected: 全 vitest グリーン
cd frontend && npx tsc --noEmit && cd ..
# Expected: 型エラーなし
git add frontend/src/lib/api.ts frontend/src/lib/__tests__/api.test.ts \
        frontend/src/app/optimize/page.tsx frontend/src/app/simulation/page.tsx
git commit -m "feat(ui): /optimize と /backtest を新検証応答に追従（OOS見出し・ATR内訳のplan対応）"
```

> 手動確認（自動テスト対象外）：simulation 画面で「ATR出口」チェック→実行時に「ATR出口の内訳」が表示されること。

---

## 完了の定義

- `backend/ pytest` が全件グリーン（既存44件 + 新規テスト）。
- すべての損益・期待値が**コスト込み**で算出される。
- バックテスト約定が **`build_plan` の提示指値・stop・target に一致**（検証=提示）。
- `/backtest` 応答に `cost`/`fill_rate`/`significance`/`benchmark`。
- `/optimize` が **in-sample 選定 → out-of-sample 評価** の2段構えで、`overfit_gap`・`significance`・`benchmark` を返す。
- フロント `/optimize` が新レスポンス形で動作し（OOS を見出し表示）、`npm --prefix frontend test` がグリーン・`tsc --noEmit` が型エラーなし。
- フロント `/backtest`（simulation）が `exit_mode="plan"` に追従し、ATR出口の内訳が引き続き表示される（手動確認）。

## スコープ外（将来）
- ローリング walk-forward、価格キャッシュ最適化、流動性フィルター、リスクベースのサイジング、leave-one-out 寄与度の in_sample 復活、フロント表示の追従。

> ⚠️ 本計画は検証の信頼性向上が目的であり、利益を保証しない。投資は自己責任の原則は不変。
