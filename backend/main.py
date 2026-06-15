"""FastAPI アプリ（Phase 1 / 2）。

ローカル専用の計算エンジン API。Next.js（:3000）から呼ばれる前提で CORS を開ける。
起動: backend/venv/bin/uvicorn main:app --reload --port 8000
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import db
from backtest import run_backtest
from market import get_history
from scheduler import DailyScheduler
from signals import evaluate

_scheduler: DailyScheduler | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _scheduler
    db.init_db()
    _scheduler = DailyScheduler(perform_refresh)
    _scheduler.start()
    yield
    if _scheduler:
        _scheduler.stop()


app = FastAPI(title="株価シグナル通知アプリ API", version="0.3.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    # ローカル専用。Next.js は :3000 が埋まっていると :3001 等に逃げるため、
    # localhost / 127.0.0.1 の任意ポートを許可する。
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1):\d+",
    allow_methods=["*"],
    allow_headers=["*"],
)


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
    buy, sell = db.get_thresholds()
    return {
        "app": "株価シグナル通知アプリ API",
        "buy_threshold": buy,
        "sell_threshold": sell,
        "disclaimer": "シグナルは予測を保証しません。投資は自己責任で。",
    }


# ---------------------------------------------------------------------------
# settings（スコア閾値・スケジューラ設定）
# ---------------------------------------------------------------------------
class SettingsUpdate(BaseModel):
    buy_threshold: Optional[int] = None
    sell_threshold: Optional[int] = None
    scheduler_enabled: Optional[bool] = None
    scheduler_time: Optional[str] = None
    scheduler_demo: Optional[bool] = None


@app.get("/settings")
def get_settings():
    m = db.get_all_meta()
    return {
        "buy_threshold": int(m.get("buy_threshold", 2)),
        "sell_threshold": int(m.get("sell_threshold", -2)),
        "scheduler_enabled": m.get("scheduler_enabled", "0") == "1",
        "scheduler_time": m.get("scheduler_time", "16:00"),
        "scheduler_demo": m.get("scheduler_demo", "0") == "1",
    }


@app.put("/settings")
def put_settings(payload: SettingsUpdate):
    if payload.buy_threshold is not None:
        db.set_meta("buy_threshold", payload.buy_threshold)
    if payload.sell_threshold is not None:
        db.set_meta("sell_threshold", payload.sell_threshold)
    if payload.scheduler_enabled is not None:
        db.set_meta("scheduler_enabled", "1" if payload.scheduler_enabled else "0")
    if payload.scheduler_time is not None:
        db.set_meta("scheduler_time", payload.scheduler_time)
    if payload.scheduler_demo is not None:
        db.set_meta("scheduler_demo", "1" if payload.scheduler_demo else "0")
    return get_settings()


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


class ConfigCreate(BaseModel):
    rule_type: str
    ticker: Optional[str] = None
    params: Optional[dict[str, Any]] = None
    weight: int = 1
    enabled: bool = True


@app.post("/config")
def post_config(payload: ConfigCreate):
    new_id = db.add_config(payload.rule_type, ticker=payload.ticker,
                           params=payload.params, weight=payload.weight,
                           enabled=payload.enabled)
    return {"id": new_id}


@app.delete("/config/{config_id}")
def remove_config(config_id: int):
    db.delete_config(config_id)
    return {"deleted": config_id}


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
@app.get("/prices_latest")
def get_latest_prices():
    """監視一覧の最新終値（{ticker: {date, close}}）。ダッシュボードの現在値用。"""
    return db.latest_prices()


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


def perform_refresh(demo: bool = False, period: str = "6mo") -> dict:
    """最新データ取得 + 再判定（全 enabled 銘柄）の中核。

    HTTP エンドポイントとスケジューラの両方から呼ぶ。
    """
    watch = db.list_watchlist(only_enabled=True)
    all_configs = db.list_configs(active_only=True)
    buy_th, sell_th = db.get_thresholds()
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
        score, direction, detail = evaluate(df, ticker_cfgs, buy_th, sell_th)
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


@app.post("/refresh")
def refresh(demo: bool = Query(False), period: str = Query("6mo")):
    return perform_refresh(demo=demo, period=period)


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

    buy_th, sell_th = db.get_thresholds()
    result = run_backtest(histories, configs=common,
                          initial_capital=payload.initial_capital,
                          backtest_days=payload.days,
                          buy_threshold=buy_th, sell_threshold=sell_th)
    result["failed"] = failed

    if payload.persist:
        for t in result["trades"]:
            db.insert_paper_trade(t["ticker"], t["action"], t["price"],
                                  t["shares"], t["date"])

    return result
