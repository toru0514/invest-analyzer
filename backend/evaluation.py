"""out-of-sample 評価層：ホールドアウト2段構え・統計サマリ・ベンチマーク。"""
from __future__ import annotations

import statistics

from costs import DEFAULT_COST

# in-sample で探索する既定グリッド（閾値のみ・exit_mode は plan 固定）。
# 打ち手4のグループ化でスコアは最大±4に圧縮され、順張り群と逆張り群は構造的に逆を向くため
# 3〜4（3グループ以上一致）はほぼ到達不能。探索は 1〜3（1〜3グループ一致）に合わせる。
DEFAULT_GRID: dict[str, list[int]] = {"threshold": [1, 2, 3]}

MIN_TRADES = 30   # これ未満は統計的に不十分（誤差範囲）

# leave-one-out 寄与度の対象指標（in-sample で各指標を外した時の損益差を測る）
_ABLATABLE = ["rsi", "ma_cross", "macd", "bbands", "stoch", "candle_pattern",
              "disparity", "obv", "cci", "volume_filter", "weekly_trend_filter"]


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


def benchmark(histories, configs, *, buy_threshold, sell_threshold,
              initial_capital, warmup_days, backtest_days, cost=None,
              eval_start_date=None, regime_series=None,
              index_history=None, rs_params=None) -> dict:
    """評価窓のベンチマーク2種。(a) ユニバース等加重 buy&hold、(b) 全シグナル等加重（素のシグナル運用）。

    eval_start_date 指定時は評価窓をその日以降（out-of-sample）に限定する。戦略の評価窓と揃える。
    """
    from backtest import run_backtest

    cost = cost or DEFAULT_COST
    # (a) buy&hold：評価窓の頭→末リターンを等加重平均（OOS指定時は split 以降）
    rets = []
    for df in histories.values():
        df = df.sort_index()
        win = df[df.index >= eval_start_date] if eval_start_date is not None else df.tail(backtest_days)
        if len(win) >= 2 and float(win["close"].iloc[0]) > 0:
            rets.append(float(win["close"].iloc[-1]) / float(win["close"].iloc[0]) - 1.0)
    buy_hold_pct = (sum(rets) / len(rets) * 100) if rets else None

    # (b) 全シグナル等加重（選別なし）：score モードの素のシグナル運用（コスト込み）
    naive = run_backtest(histories, configs=configs, initial_capital=initial_capital,
                         backtest_days=backtest_days, warmup_days=warmup_days,
                         buy_threshold=buy_threshold, sell_threshold=sell_threshold,
                         exit_mode="score", cost=cost, eval_start_date=eval_start_date,
                         regime_series=regime_series,
                         index_history=index_history, rs_params=rs_params)
    return {"buy_hold_pct": buy_hold_pct, "all_signals_pct": naive["pnl_pct"]}


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
                     initial_capital=3000.0, warmup_days=35, regime_series=None,
                     index_history=None, rs_params=None, risk_pct=None) -> dict:
    """シンプルホールドアウト2段構え：train(in-sample) で閾値を選び test(out-of-sample) で評価。

    look-ahead 回避：test 窓のパラメータは train 窓の成績のみから選ぶ。
    """
    from backtest import run_backtest
    from signals import DEFAULT_CONFIGS as _DEFAULT_CONFIGS, DEFAULT_RISK_PCT
    risk_pct = DEFAULT_RISK_PCT if risk_pct is None else risk_pct

    grid = grid or DEFAULT_GRID
    cost = cost or DEFAULT_COST
    configs = configs if configs is not None else _DEFAULT_CONFIGS
    split = _split_date(histories, split_ratio)

    # train：各銘柄を split 以前にスライスし、全期間（warmup以降）で評価
    train_hist = {t: df[df.index < split] for t, df in histories.items()}
    big = max((len(df) for df in histories.values()), default=0) + 1

    def _bt(hist, th, cfgs, eval_start=None):
        return run_backtest(hist, configs=cfgs, initial_capital=initial_capital,
                            backtest_days=big, warmup_days=warmup_days,
                            buy_threshold=th, sell_threshold=-th, exit_mode="plan",
                            cost=cost, eval_start_date=eval_start, regime_series=regime_series,
                            index_history=index_history, rs_params=rs_params,
                            risk_pct=risk_pct)

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

    # ベンチマークも OOS 窓（split 以降）で計算し、戦略の OOS 成績と公平に比較する。
    bench = benchmark(histories, configs, buy_threshold=best_th, sell_threshold=-best_th,
                      initial_capital=initial_capital, warmup_days=warmup_days,
                      backtest_days=big, cost=cost, eval_start_date=split,
                      regime_series=regime_series,
                      index_history=index_history, rs_params=rs_params)

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
