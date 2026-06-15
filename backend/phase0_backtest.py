"""Phase 0: 計算コアの検証スクリプト（UIなし・単一スクリプト）

三菱UFJ(8306.T) を含む複数の日本株について、
  1. yfinance で過去データを取得
  2. pandas-ta で複数指標・ローソク足パターンを計算
  3. evaluate() で buy/sell/neutral 判定
  4. 過去約1ヶ月のペーパートレード（仮想資金3,000円・端株可）でバックテスト
  5. 成績（仕様書 §4 の必須項目）をコンソール出力
する。

使い方:
  backend/venv/bin/python backend/phase0_backtest.py             # yfinance から取得
  backend/venv/bin/python backend/phase0_backtest.py --demo      # 合成データで計算ロジックを検証
  backend/venv/bin/python backend/phase0_backtest.py --tickers 8306.T 7203.T

注意: yfinance は遅延・非公式データ。日足ベースのスイング判定が前提。
      バックテストは未来データを使わない（各日の判定はその日までのデータのみ）。
      本ツールは投資助言ではありません。シグナルは予測を保証しません（投資は自己責任）。
"""

from __future__ import annotations

import argparse
import sys

import numpy as np
import pandas as pd

from signals import DEFAULT_CONFIGS, evaluate

# デフォルト監視銘柄（数銘柄）
DEFAULT_TICKERS = ["8306.T", "7203.T", "9984.T", "6758.T"]

INITIAL_CAPITAL = 3000.0   # 仮想資金（円）
BACKTEST_DAYS = 22         # バックテスト対象の営業日数（≒1ヶ月）
WARMUP_DAYS = 35           # 指標計算に必要な助走期間（SMA25/BBands20 等）


# ---------------------------------------------------------------------------
# データ取得
# ---------------------------------------------------------------------------
def fetch_history(ticker: str, period: str = "4mo") -> pd.DataFrame:
    """yfinance で日足を取得し、小文字 OHLCV・古い順の DataFrame を返す。"""
    import yfinance as yf

    raw = yf.download(ticker, period=period, interval="1d",
                      auto_adjust=True, progress=False)
    if raw is None or raw.empty:
        return pd.DataFrame()
    # 単一銘柄でも MultiIndex 列になる場合があるので平坦化
    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = raw.columns.get_level_values(0)
    raw = raw.rename(columns=str.lower)
    df = raw[["open", "high", "low", "close", "volume"]].copy()
    df.index = pd.to_datetime(df.index)
    return df.sort_index()


def synthetic_history(ticker: str, n: int = 90, seed: int | None = None) -> pd.DataFrame:
    """ネットワーク不要の合成 OHLCV（ロジック検証用）。"""
    rng = np.random.default_rng(seed if seed is not None else abs(hash(ticker)) % (2**32))
    base = 1000 + rng.integers(0, 1500)
    # トレンド + 周期 + ノイズ。押し目/反発が出るよう緩やかな波を入れる。
    t = np.arange(n)
    drift = np.cumsum(rng.normal(0, 1, n)) * 8
    wave = np.sin(t / 6.0) * 40
    close = base + drift + wave
    close = np.maximum(close, 50)
    open_ = close + rng.normal(0, 5, n)
    high = np.maximum(open_, close) + np.abs(rng.normal(0, 6, n))
    low = np.minimum(open_, close) - np.abs(rng.normal(0, 6, n))
    vol = rng.integers(1_000_000, 8_000_000, n).astype(float)
    idx = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=n)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": vol},
        index=idx,
    )


# ---------------------------------------------------------------------------
# バックテスト（複数銘柄・仮想資金共有・端株可）
# ---------------------------------------------------------------------------
def run_backtest(histories: dict[str, pd.DataFrame], configs=None):
    if configs is None:
        configs = DEFAULT_CONFIGS

    # 各銘柄で共通の評価営業日を決める（最後の BACKTEST_DAYS 日）
    n_tickers = len(histories)
    cash = INITIAL_CAPITAL
    holdings: dict[str, float] = {t: 0.0 for t in histories}   # 保有株数（端株）
    cost_basis: dict[str, float] = {t: 0.0 for t in histories}  # 平均取得単価
    per_trade_budget = INITIAL_CAPITAL / max(n_tickers, 1)      # 1銘柄あたりの上限投入額

    trades = []          # 約定ログ
    closed_pnls = []     # 決済（売り）ごとの損益
    equity_curve = []    # (date, 評価額)
    signal_rows = []     # 各銘柄の最終日シグナル

    # 全銘柄共通の日付軸を作る
    all_dates = sorted(set().union(*[set(df.index) for df in histories.values()]))
    eval_dates = all_dates[-BACKTEST_DAYS:]

    for d in eval_dates:
        for ticker, df in histories.items():
            window = df[df.index <= d]
            if len(window) < WARMUP_DAYS:
                continue
            score, direction, detail = evaluate(window, configs)
            price = float(window["close"].iloc[-1])

            if direction == "buy" and cash > 0:
                invest = min(per_trade_budget, cash)
                # 既に同銘柄を上限まで持っていたら追加しない
                current_value = holdings[ticker] * price
                invest = min(invest, max(0.0, per_trade_budget - current_value))
                if invest >= 1.0:  # 1円未満は無視
                    shares = invest / price
                    # 平均取得単価を更新
                    total_cost = cost_basis[ticker] * holdings[ticker] + invest
                    holdings[ticker] += shares
                    cost_basis[ticker] = total_cost / holdings[ticker]
                    cash -= invest
                    trades.append((d, ticker, "buy", price, shares))

            elif direction == "sell" and holdings[ticker] > 0:
                shares = holdings[ticker]
                proceeds = shares * price
                pnl = (price - cost_basis[ticker]) * shares
                cash += proceeds
                closed_pnls.append(pnl)
                trades.append((d, ticker, "sell", price, shares))
                holdings[ticker] = 0.0
                cost_basis[ticker] = 0.0

        # 日次評価額（現金 + 保有時価）
        equity = cash
        for ticker, df in histories.items():
            window = df[df.index <= d]
            if not window.empty:
                equity += holdings[ticker] * float(window["close"].iloc[-1])
        equity_curve.append((d, equity))

    # 期間末の保有を最終時価で評価（清算はせず時価として算入）
    final_value = cash
    for ticker, df in histories.items():
        last_close = float(df["close"].iloc[-1])
        final_value += holdings[ticker] * last_close
        # 最終日シグナルも記録
        score, direction, detail = evaluate(df, configs)
        signal_rows.append((ticker, last_close, score, direction, detail))

    # ----- 成績集計（§4） -----
    pnl_amount = final_value - INITIAL_CAPITAL
    pnl_pct = pnl_amount / INITIAL_CAPITAL * 100
    trade_count = len(trades)
    wins = sum(1 for p in closed_pnls if p > 0)
    win_rate = (wins / len(closed_pnls) * 100) if closed_pnls else None

    # 最大ドローダウン
    max_dd = 0.0
    peak = -np.inf
    for _, eq in equity_curve:
        peak = max(peak, eq)
        if peak > 0:
            max_dd = min(max_dd, (eq - peak) / peak)
    max_dd_pct = abs(max_dd) * 100

    return {
        "initial": INITIAL_CAPITAL,
        "final": final_value,
        "pnl_amount": pnl_amount,
        "pnl_pct": pnl_pct,
        "trade_count": trade_count,
        "closed_trades": len(closed_pnls),
        "win_rate": win_rate,
        "max_drawdown_pct": max_dd_pct,
        "trades": trades,
        "signals": signal_rows,
        "equity_curve": equity_curve,
    }


# ---------------------------------------------------------------------------
# 出力
# ---------------------------------------------------------------------------
def print_report(result: dict, tickers: list[str]):
    print("=" * 64)
    print(" 株価シグナル バックテスト（Phase 0）")
    print("=" * 64)
    print(f" 対象銘柄        : {', '.join(tickers)}")
    print(f" 評価営業日数    : 直近 {BACKTEST_DAYS} 営業日（≒1ヶ月）")
    print()

    print("--- 最終日シグナル ---")
    print(f" {'銘柄':<10}{'終値':>10}{'スコア':>8}  判定   内訳")
    for ticker, price, score, direction, detail in result["signals"]:
        mark = {"buy": "▲買い", "sell": "▼売り", "neutral": "・中立"}[direction]
        print(f" {ticker:<10}{price:>10.1f}{score:>8}  {mark}  {detail}")
    print()

    print("--- 約定ログ（ペーパートレード） ---")
    if result["trades"]:
        for d, ticker, action, price, shares in result["trades"]:
            label = "買" if action == "buy" else "売"
            print(f" {pd.Timestamp(d).date()}  {label} {ticker:<9} @ {price:>9.1f}  {shares:.4f} 株")
    else:
        print(" （取引なし）")
    print()

    print("--- 成績（§4 必須項目） ---")
    r = result
    print(f" 開始資金        : {r['initial']:>12,.0f} 円")
    print(f" 最終評価額      : {r['final']:>12,.1f} 円")
    print(f" 損益            : {r['pnl_amount']:>+12,.1f} 円 ({r['pnl_pct']:+.2f}%)")
    print(f" 取引回数        : {r['trade_count']:>12} 回  (うち決済 {r['closed_trades']} 回)")
    wr = f"{r['win_rate']:.1f}%" if r["win_rate"] is not None else "N/A（決済なし）"
    print(f" 勝率            : {wr:>12}")
    print(f" 最大ドローダウン: {r['max_drawdown_pct']:>11.2f}%")
    print("=" * 64)
    print(" ※ シグナルは予測を保証しません。投資は自己責任で。")


def main():
    parser = argparse.ArgumentParser(description="Phase 0 バックテスト検証スクリプト")
    parser.add_argument("--tickers", nargs="+", default=DEFAULT_TICKERS,
                        help="監視銘柄（例: 8306.T 7203.T）")
    parser.add_argument("--demo", action="store_true",
                        help="yfinance を使わず合成データで計算ロジックを検証する")
    parser.add_argument("--period", default="4mo", help="yfinance の取得期間")
    args = parser.parse_args()

    histories: dict[str, pd.DataFrame] = {}
    for ticker in args.tickers:
        if args.demo:
            df = synthetic_history(ticker)
            src = "synthetic"
        else:
            print(f"[fetch] {ticker} ...", file=sys.stderr)
            df = fetch_history(ticker, period=args.period)
            src = "yfinance"
        if df.empty:
            print(f"[warn] {ticker}: データ取得失敗（スキップ）", file=sys.stderr)
            continue
        print(f"[ok] {ticker}: {len(df)} 本 ({src}, "
              f"{df.index[0].date()}〜{df.index[-1].date()})", file=sys.stderr)
        histories[ticker] = df

    if not histories:
        print("データを取得できませんでした。", file=sys.stderr)
        if not args.demo:
            print("ネットワーク制限がある環境では --demo で計算ロジックを検証できます。",
                  file=sys.stderr)
        sys.exit(1)

    result = run_backtest(histories)
    print_report(result, list(histories.keys()))


if __name__ == "__main__":
    main()
