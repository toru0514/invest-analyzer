"""FastAPI アプリ（Phase 1 / 2）。

ローカル専用の計算エンジン API。Next.js（:3000）から呼ばれる前提で CORS を開ける。
起動: backend/venv/bin/uvicorn main:app --reload --port 8000
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import db
from backtest import run_backtest
from market import get_history
from signals import BUY_THRESHOLD, SELL_THRESHOLD, evaluate

app = FastAPI(title="株価シグナル通知アプリ API", version="0.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _startup():
    db.init_db()


# ---------------------------------------------------------------------------
# モデル
# ---------------------------------------------------------------------------
class WatchlistIn(BaseModel):
    ticker: str
    name: str


class ConfigUpdate(BaseModel):
    id: int
    weight: Optional[int] = None
    enabled: Optional[bool] = None
    params: Optional[dict[str, Any]] = None


class ConfigUpdateList(BaseModel):
    updates: list[ConfigUpdate]


class BacktestIn(BaseModel):
    tickers: Optional[list[str]] = None
    initial_capital: float = 3000.0
    days: int = 22
    demo: bool = False
    persist: bool = False


# ---------------------------------------------------------------------------
# 基本
# ---------------------------------------------------------------------------
@app.get("/")
def root():
    return {
        "app": "株価シグナル通知アプリ API",
        "buy_threshold": BUY_THRESHOLD,
        "sell_threshold": SELL_THRESHOLD,
        "disclaimer": "シグナルは予測を保証しません。投資は自己責任で。",
    }


# ---------------------------------------------------------------------------
# watchlist
# ---------------------------------------------------------------------------
@app.get("/watchlist")
def get_watchlist():
    return db.list_watchlist()


@app.post("/watchlist")
def post_watchlist(item: WatchlistIn):
    new_id = db.add_watchlist(item.ticker.strip(), item.name.strip())
    return {"id": new_id, "ticker": item.ticker, "name": item.name}


@app.delete("/watchlist/{item_id}")
def remove_watchlist(item_id: int):
    db.delete_watchlist(item_id)
    return {"deleted": item_id}


# ---------------------------------------------------------------------------
# config (signal_config)
# ---------------------------------------------------------------------------
@app.get("/config")
def get_config():
    return db.list_configs()


@app.put("/config")
def put_config(payload: ConfigUpdateList):
    for u in payload.updates:
        db.update_config(u.id, weight=u.weight, enabled=u.enabled, params=u.params)
    return db.list_configs()


# ---------------------------------------------------------------------------
# signals
# ---------------------------------------------------------------------------
@app.get("/signals")
def get_signals(ticker: Optional[str] = Query(None), limit: int = 100):
    return db.list_signals(ticker=ticker, limit=limit)


@app.get("/signals/unnotified")
def get_unnotified():
    return db.list_unnotified_signals()


class MarkNotifiedIn(BaseModel):
    ids: list[int]


@app.post("/signals/mark_notified")
def mark_notified(payload: MarkNotifiedIn):
    db.mark_notified(payload.ids)
    return {"marked": payload.ids}


# ---------------------------------------------------------------------------
# prices（チャート用）
# ---------------------------------------------------------------------------
@app.get("/prices/{ticker}")
def get_prices(ticker: str):
    df = db.load_prices(ticker)
    if df.empty:
        return []
    return [
        {"date": str(idx.date()), "open": float(r["open"]), "high": float(r["high"]),
         "low": float(r["low"]), "close": float(r["close"]), "volume": int(r["volume"])}
        for idx, r in df.iterrows()
    ]


# ---------------------------------------------------------------------------
# refresh: 最新データ取得 + 再判定（全 enabled 銘柄）
# ---------------------------------------------------------------------------
def _check_price_targets(ticker: str, last_close: float, configs: list[dict]):
    """price_target ルールを評価し、(direction, detail) のアラートを返す（無ければ None）。"""
    for cfg in configs:
        if cfg["rule_type"] != "price_target" or not cfg.get("enabled", 1):
            continue
        p = cfg.get("params") or {}
        above = p.get("above")
        below = p.get("below")
        if above is not None and last_close >= above:
            return "sell", {"price_target": f">= {above}（上限到達）"}
        if below is not None and last_close <= below:
            return "buy", {"price_target": f"<= {below}（下限到達）"}
    return None


@app.post("/refresh")
def refresh(demo: bool = Query(False), period: str = Query("6mo")):
    watch = db.list_watchlist(only_enabled=True)
    all_configs = db.list_configs(active_only=True)
    # ticker 別 + 全銘柄共通(NULL) の設定を組み合わせる
    common = [c for c in all_configs if c["ticker"] is None]

    results = []
    failed = []
    for w in watch:
        ticker = w["ticker"]
        df = get_history(ticker, period=period, demo=demo)
        if df.empty:
            failed.append(ticker)
            continue
        db.upsert_prices(ticker, df)

        ticker_cfgs = common + [c for c in all_configs if c["ticker"] == ticker]
        score, direction, detail = evaluate(df, ticker_cfgs)
        last_close = float(df["close"].iloc[-1])
        date = str(df.index[-1].date())

        # price_target アラート（スコアと独立）
        pt = _check_price_targets(ticker, last_close, ticker_cfgs)
        if pt:
            pt_dir, pt_detail = pt
            db.insert_signal(ticker, date, score, pt_dir, {**detail, **pt_detail})

        sid = db.insert_signal(ticker, date, score, direction, detail)
        results.append({"id": sid, "ticker": ticker, "date": date, "price": last_close,
                        "score": score, "direction": direction, "detail": detail})

    return {"updated": results, "failed": failed,
            "note": "yfinance 取得失敗時は demo=true で合成データを使えます。" if failed else None}


# ---------------------------------------------------------------------------
# backtest
# ---------------------------------------------------------------------------
@app.post("/backtest")
def backtest(payload: BacktestIn):
    tickers = payload.tickers
    if not tickers:
        tickers = [w["ticker"] for w in db.list_watchlist(only_enabled=True)]
    if not tickers:
        raise HTTPException(status_code=400, detail="対象銘柄がありません")

    configs = db.list_configs(active_only=True)
    common = [c for c in configs if c["ticker"] is None]

    histories = {}
    failed = []
    for t in tickers:
        df = get_history(t, demo=payload.demo)
        if df.empty:
            failed.append(t)
            continue
        histories[t] = df

    if not histories:
        raise HTTPException(
            status_code=502,
            detail="価格データを取得できませんでした（ネットワーク制限時は demo=true）。")

    result = run_backtest(histories, configs=common,
                          initial_capital=payload.initial_capital,
                          backtest_days=payload.days)
    result["failed"] = failed

    if payload.persist:
        for t in result["trades"]:
            db.insert_paper_trade(t["ticker"], t["action"], t["price"],
                                  t["shares"], t["date"])

    return result
