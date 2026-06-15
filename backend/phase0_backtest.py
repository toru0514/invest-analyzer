"""Phase 0: 計算コアの検証スクリプト（UIなし・コンソール出力）

三菱UFJ(8306.T) を含む複数の日本株について、
  1. yfinance で過去データを取得（--demo で合成データ）
  2. pandas-ta で複数指標・ローソク足パターンを計算
  3. evaluate() で buy/sell/neutral 判定
  4. 直近約1ヶ月のペーパートレード（仮想資金3,000円・端株可）でバックテスト
  5. 成績（仕様書 §4 の必須項目）をコンソール出力

使い方:
  backend/venv/bin/python backend/phase0_backtest.py
  backend/venv/bin/python backend/phase0_backtest.py --demo
  backend/venv/bin/python backend/phase0_backtest.py --tickers 8306.T 7203.T

注意: yfinance は遅延・非公式データ。日足ベースのスイング判定が前提。
      バックテストは未来データを使わない。投資は自己責任。
"""

from __future__ import annotations

import argparse
import sys

import pandas as pd

from backtest import BACKTEST_DAYS, run_backtest
from market import get_history

DEFAULT_TICKERS = ["8306.T", "7203.T", "9984.T", "6758.T"]


def print_report(result: dict, tickers: list[str]):
    print("=" * 64)
    print(" 株価シグナル バックテスト（Phase 0）")
    print("=" * 64)
    print(f" 対象銘柄        : {', '.join(tickers)}")
    print(f" 評価営業日数    : 直近 {BACKTEST_DAYS} 営業日（≒1ヶ月）")
    print()

    print("--- 最終日シグナル ---")
    print(f" {'銘柄':<10}{'終値':>10}{'スコア':>8}  判定   内訳")
    for s in result["signals"]:
        mark = {"buy": "▲買い", "sell": "▼売り", "neutral": "・中立"}[s["direction"]]
        print(f" {s['ticker']:<10}{s['price']:>10.1f}{s['score']:>8}  {mark}  {s['detail']}")
    print()

    print("--- 約定ログ（ペーパートレード） ---")
    if result["trades"]:
        for t in result["trades"]:
            label = "買" if t["action"] == "buy" else "売"
            print(f" {t['date']}  {label} {t['ticker']:<9} @ {t['price']:>9.1f}  {t['shares']:.4f} 株")
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
    parser.add_argument("--tickers", nargs="+", default=DEFAULT_TICKERS)
    parser.add_argument("--demo", action="store_true",
                        help="yfinance を使わず合成データで検証")
    parser.add_argument("--period", default="6mo")
    args = parser.parse_args()

    histories: dict[str, pd.DataFrame] = {}
    for ticker in args.tickers:
        if not args.demo:
            print(f"[fetch] {ticker} ...", file=sys.stderr)
        df = get_history(ticker, period=args.period, demo=args.demo)
        if df.empty:
            print(f"[warn] {ticker}: データ取得失敗（スキップ）", file=sys.stderr)
            continue
        src = "synthetic" if args.demo else "yfinance"
        print(f"[ok] {ticker}: {len(df)} 本 ({src}, "
              f"{df.index[0].date()}〜{df.index[-1].date()})", file=sys.stderr)
        histories[ticker] = df

    if not histories:
        print("データを取得できませんでした。", file=sys.stderr)
        if not args.demo:
            print("ネットワーク制限がある環境では --demo で検証できます。", file=sys.stderr)
        sys.exit(1)

    result = run_backtest(histories)
    print_report(result, list(histories.keys()))


if __name__ == "__main__":
    main()
