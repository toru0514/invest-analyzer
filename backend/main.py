"""FastAPI アプリ（Phase 1 / 2）。

ローカル専用の計算エンジン API。Next.js（:3000）から呼ばれる前提で CORS を開ける。
起動: backend/venv/bin/uvicorn main:app --reload --port 8000
"""

from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import db
import stocks_jp
from ai_commentary import generate_commentary
from backtest import run_backtest
from costs import cost_from_configs
from evaluation import benchmark, evaluate_holdout, summary_stats
from market import fetch_earnings_days, fetch_name, get_history
from scheduler import DailyScheduler
from signals import (
    _find_cfg,
    build_plan,
    evaluate,
    market_regime,
    regime_series,
    relative_strength,
    resolve_configs,
    weekly_trend,
)


def _normalize_ticker(ticker: str) -> str:
    """'6501' → '6501.T' のように東証コードを正規化する。"""
    t = ticker.strip().upper()
    if t.isdigit():        # 数字だけなら東証とみなして .T を付与
        return f"{t}.T"
    return t


def _regime_params(common: list[dict]) -> dict:
    """共通設定の market_regime ルールからレジーム計算パラメータを抜き出す。"""
    rp = _find_cfg(common, "market_regime")   # _find_cfg は params dict を返す
    return {k: rp[k] for k in ("sma", "dd_lookback", "dd_threshold") if rp and k in rp}


def _fetch_regime_series(period: str, demo: bool, common: list[dict]):
    """指数を取得し (日次レジーム系列, 指数OHLCV) を返す。取得失敗・空は (None, None)。"""
    try:
        idx = get_history(os.environ.get("GEMINI_INDEX_TICKER", "^N225"),
                          period=period, demo=demo)
        if idx.empty:
            return None, None
        return regime_series(idx, **_regime_params(common)), idx
    except Exception:
        return None, None

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
    name: str = ""    # 空なら内蔵マスタ／yfinance から自動解決


class ConfigUpdate(BaseModel):
    id: int
    weight: Optional[int] = None
    enabled: Optional[bool] = None
    params: Optional[dict[str, Any]] = None


class ConfigUpdateList(BaseModel):
    updates: list[ConfigUpdate]


class BacktestIn(BaseModel):
    tickers: Optional[list[str]] = None
    demo: bool = False
    days: Optional[int] = None   # 未指定なら取得期間全体（warmup以降）を評価
    initial_capital: float = 3000.0
    exit_mode: str = "plan"      # 既定を plan（検証=提示）に
    persist: bool = False
    period: str = "3y"


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
    scheduler_skip_holidays: Optional[bool] = None
    top_n: Optional[int] = None


def _safe_top_n(raw) -> int:
    # 負値・非整数は既定3にフォールバック（0は有効＝「今夜の推奨」セクション非表示）。
    # clamp-to-0（max(0,...)）にしないこと: 負値は無効入力として既定へ戻す契約（put_settings と一致）。
    try:
        v = int(raw)
        return v if v >= 0 else 3
    except (TypeError, ValueError):
        return 3


@app.get("/settings")
def get_settings():
    m = db.get_all_meta()
    return {
        "buy_threshold": int(m.get("buy_threshold", 2)),
        "sell_threshold": int(m.get("sell_threshold", -2)),
        "scheduler_enabled": m.get("scheduler_enabled", "0") == "1",
        "scheduler_time": m.get("scheduler_time", "16:00"),
        "scheduler_demo": m.get("scheduler_demo", "0") == "1",
        "scheduler_skip_holidays": m.get("scheduler_skip_holidays", "1") == "1",
        "top_n": _safe_top_n(m.get("top_n", "3")),
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
    if payload.scheduler_skip_holidays is not None:
        db.set_meta("scheduler_skip_holidays", "1" if payload.scheduler_skip_holidays else "0")
    if payload.top_n is not None:
        db.set_meta("top_n", str(payload.top_n) if payload.top_n >= 0 else "3")
    return get_settings()


# ---------------------------------------------------------------------------
# watchlist
# ---------------------------------------------------------------------------
@app.get("/watchlist")
def get_watchlist():
    return db.list_watchlist()


def _resolve_name(ticker: str) -> str:
    """銘柄名を内蔵マスタ→yfinance の順で解決。取れなければティッカーを返す。"""
    return stocks_jp.lookup_name(ticker) or fetch_name(ticker) or ticker


@app.post("/watchlist")
def post_watchlist(item: WatchlistIn):
    ticker = _normalize_ticker(item.ticker)
    name = item.name.strip() or _resolve_name(ticker)
    new_id = db.add_watchlist(ticker, name)
    return {"id": new_id, "ticker": ticker, "name": name}


@app.delete("/watchlist/{item_id}")
def remove_watchlist(item_id: int):
    db.delete_watchlist(item_id)
    return {"deleted": item_id}


@app.get("/stocks/search")
def stocks_search(q: str = Query("")):
    """内蔵マスタを名前/コードで検索（追加候補の自動補完用）。"""
    return stocks_jp.search(q)


@app.get("/stocks/name")
def stocks_name(ticker: str = Query(...)):
    """ティッカーから銘柄名を解決（内蔵マスタ→yfinance）。追加前のプレビュー用。"""
    t = _normalize_ticker(ticker)
    name = stocks_jp.lookup_name(t)
    if name:
        return {"ticker": t, "name": name, "source": "master"}
    fetched = fetch_name(t)
    return {"ticker": t, "name": fetched or "", "source": "yfinance" if fetched else "none"}


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


def _next_business_day(date_str: str) -> str:
    """YYYY-MM-DD の翌営業日（土日スキップ）を返す。"""
    import datetime as _dt
    d = _dt.date.fromisoformat(date_str)
    d += _dt.timedelta(days=1)
    while d.weekday() >= 5:   # 土(5)/日(6)
        d += _dt.timedelta(days=1)
    return d.isoformat()


def perform_refresh(demo: bool = False, period: str = "6mo") -> dict:
    """最新データ取得 + 再判定（全 enabled 銘柄）の中核。

    HTTP エンドポイントとスケジューラの両方から呼ぶ。
    """
    watch = db.list_watchlist(only_enabled=True)
    all_configs = db.list_configs(active_only=True)
    buy_th, sell_th = db.get_thresholds()
    # ticker 別 + 全銘柄共通(NULL) の設定を組み合わせる
    common = [c for c in all_configs if c["ticker"] is None]
    rs_params = _find_cfg(common, "relative_strength")   # None なら RS 無効

    # 地合い（指数トレンド・レジーム）を1回だけ取得し全銘柄で使い回す（best-effort）
    index_trend = None
    regime = None
    idx_df = None
    try:
        index_ticker = os.environ.get("GEMINI_INDEX_TICKER", "^N225")
        idx_df = get_history(index_ticker, period=period, demo=demo)
        if not idx_df.empty:
            index_trend = weekly_trend(idx_df)
            regime = market_regime(idx_df, **_regime_params(common))
    except Exception:
        index_trend = None

    results = []
    failed = []
    for w in watch:
        ticker = w["ticker"]
        df = get_history(ticker, period=period, demo=demo)
        if df.empty:
            failed.append(ticker)
            continue
        db.upsert_prices(ticker, df)

        ticker_cfgs = resolve_configs(common, [c for c in all_configs if c["ticker"] == ticker])
        rs_strength = (relative_strength(df, idx_df, int(rs_params.get("period", 20)),
                                         float(rs_params.get("scale", 0.10)), asof=df.index[-1])
                       if rs_params is not None and idx_df is not None and not idx_df.empty else None)
        score, direction, detail = evaluate(df, ticker_cfgs, buy_th, sell_th,
                                            regime=regime, rs_strength=rs_strength)
        last_close = float(df["close"].iloc[-1])
        date = str(df.index[-1].date())

        # price_target アラート（スコアと独立）
        pt = _check_price_targets(ticker, last_close, ticker_cfgs)
        if pt:
            pt_dir, pt_detail = pt
            db.insert_signal(ticker, date, score, pt_dir, {**detail, **pt_detail})

        sid = db.insert_signal(ticker, date, score, direction, detail)

        # 作戦ボード（強化4）: 翌営業日の提案指値・出口を生成して保存
        plan = build_plan(df, direction, score, ticker_cfgs)
        plan_date = _next_business_day(date)

        # AI解説（Gemini・無料枠・best-effort）。キー無し/失敗は None で従来どおり。
        days_to_earnings = None if demo else fetch_earnings_days(ticker)
        commentary = generate_commentary(
            {"ticker": ticker, "name": w["name"], "direction": direction,
             "score": score, "detail": detail, "vol_ratio": detail.get("vol_ratio"),
             "weekly_trend": detail.get("weekly_trend"), "close": last_close,
             "limit_price": plan["limit_price"], "stop_price": plan["stop_price"],
             "target_price": plan["target_price"]},
            {"index_trend": index_trend, "days_to_earnings": days_to_earnings,
             "regime": regime},
        )

        db.upsert_plan({
            "ticker": ticker, "plan_date": plan_date, "direction": direction, "score": score,
            "vol_ratio": detail.get("vol_ratio"), "weekly_trend": detail.get("weekly_trend"),
            "limit_price": plan["limit_price"], "stop_price": plan["stop_price"],
            "target_price": plan["target_price"], "rationale": plan["rationale"],
            "confidence": detail.get("confidence"),
            "ai_summary": commentary["summary"] if commentary else None,
            "ai_confidence": commentary["confidence"] if commentary else None,
            "ai_risks": json.dumps(commentary["risks"], ensure_ascii=False) if commentary else None,
        })

        results.append({"id": sid, "ticker": ticker, "date": date, "price": last_close,
                        "score": score, "direction": direction, "detail": detail})

    plan_date = _next_business_day(results[-1]["date"]) if results else None
    return {"updated": results, "failed": failed, "plan_date": plan_date,
            "note": "yfinance 取得失敗時は demo=true で合成データを使えます。" if failed else None}


@app.post("/refresh")
def refresh(demo: bool = Query(False), period: str = Query("6mo")):
    return perform_refresh(demo=demo, period=period)


# ---------------------------------------------------------------------------
# holdings（保有ポジション）
# ---------------------------------------------------------------------------
class HoldingIn(BaseModel):
    ticker: str
    shares: float
    avg_cost: float


@app.get("/holdings")
def get_holdings():
    return db.list_holdings()


@app.put("/holdings")
def put_holding(payload: HoldingIn):
    if payload.shares <= 0 or payload.avg_cost <= 0:
        # 0 以下は保有解除とみなす
        db.delete_holding(payload.ticker)
        return {"deleted": payload.ticker}
    db.upsert_holding(payload.ticker, payload.shares, payload.avg_cost)
    return {"ticker": payload.ticker, "shares": payload.shares, "avg_cost": payload.avg_cost}


@app.delete("/holdings/{ticker}")
def remove_holding(ticker: str):
    db.delete_holding(ticker)
    return {"deleted": ticker}


# ---------------------------------------------------------------------------
# 作戦ボード（強化4）
# ---------------------------------------------------------------------------
@app.get("/plan")
def get_plan(date: Optional[str] = Query(None)):
    """指定日（省略時は最新）の作戦ボードを返す。"""
    return {"plan_date": date or db.latest_plan_date(), "rows": db.list_plan(date)}


@app.post("/plan/generate")
def post_plan_generate(demo: bool = Query(False)):
    """全 enabled 銘柄の作戦ボードを生成・保存（refresh と同じ処理を走らせる）。"""
    res = perform_refresh(demo=demo)
    return {"plan_date": res["plan_date"], "rows": db.list_plan(res["plan_date"]),
            "failed": res["failed"]}


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
    rs, idx_df = _fetch_regime_series(payload.period, payload.demo, common)
    rs_params = _find_cfg(common, "relative_strength")
    # days 未指定なら取得期間全体（warmup以降）を評価する
    bdays = payload.days if payload.days is not None else max(
        (len(df) for df in histories.values()), default=0) + 1
    result = run_backtest(histories, configs=common, initial_capital=payload.initial_capital,
                          backtest_days=bdays, buy_threshold=buy_th, sell_threshold=sell_th,
                          exit_mode=payload.exit_mode, cost=cost, regime_series=rs,
                          index_history=idx_df, rs_params=rs_params)
    result["failed"] = failed
    result["significance"] = summary_stats(result["closed_pnls"])
    result["benchmark"] = benchmark(histories, common, buy_threshold=buy_th, sell_threshold=sell_th,
                                    initial_capital=payload.initial_capital, warmup_days=35,
                                    backtest_days=bdays, cost=cost, regime_series=rs,
                                    index_history=idx_df, rs_params=rs_params)

    if payload.persist:
        for t in result["trades"]:
            db.insert_paper_trade(t["ticker"], t["action"], t["price"], t["shares"], t["date"])
    return result


# ---------------------------------------------------------------------------
# optimize: チューニング自動化（閾値スイープ + 指標の寄与度）
# ---------------------------------------------------------------------------
class OptimizeIn(BaseModel):
    tickers: Optional[list[str]] = None
    demo: bool = False
    initial_capital: float = 3000.0
    period: str = "3y"
    split_ratio: float = 0.7


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
    rs, idx_df = _fetch_regime_series(payload.period, payload.demo, common)
    rs_params = _find_cfg(common, "relative_strength")
    res = evaluate_holdout(histories, common, split_ratio=payload.split_ratio, cost=cost,
                           initial_capital=payload.initial_capital, regime_series=rs,
                           index_history=idx_df, rs_params=rs_params)
    res["failed"] = failed
    res["tickers"] = list(histories.keys())
    return res
