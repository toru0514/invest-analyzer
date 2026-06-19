"""SQLite アクセス層（仕様書 §2 のスキーマ）。

data.db はプロジェクトルートに置き、Python API と（将来の）Next.js から
読める1ファイルとする。両プロセス前提なので WAL モードを使う。
"""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager

# プロジェクトルートの data.db （backend/ の1つ上）
DB_PATH = os.environ.get(
    "INVEST_DB_PATH",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data.db"),
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS watchlist (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  ticker    TEXT NOT NULL,
  name      TEXT NOT NULL,
  enabled   INTEGER DEFAULT 1,
  created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS price_data (
  ticker  TEXT NOT NULL,
  date    TEXT NOT NULL,
  open    REAL, high REAL, low REAL, close REAL,
  volume  INTEGER,
  PRIMARY KEY (ticker, date)
);

CREATE TABLE IF NOT EXISTS signal_config (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  ticker    TEXT,
  rule_type TEXT NOT NULL,
  params    TEXT,
  weight    INTEGER DEFAULT 1,
  enabled   INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS signals (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  ticker    TEXT NOT NULL,
  date      TEXT NOT NULL,
  score     INTEGER,
  direction TEXT,
  detail    TEXT,
  notified  INTEGER DEFAULT 0,
  created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS paper_trades (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  ticker    TEXT NOT NULL,
  action    TEXT NOT NULL,
  price     REAL, shares REAL,
  date      TEXT,
  signal_id INTEGER,
  created_at TEXT DEFAULT (datetime('now'))
);

-- アプリ設定の key-value（スコア閾値・スケジューラ設定など）
CREATE TABLE IF NOT EXISTS app_meta (
  key   TEXT PRIMARY KEY,
  value TEXT
);

-- 保有ポジション（取得単価・株数）。作戦ボードで含み損益を表示するため。
CREATE TABLE IF NOT EXISTS holdings (
  ticker     TEXT PRIMARY KEY,
  shares     REAL NOT NULL,
  avg_cost   REAL NOT NULL,
  updated_at TEXT DEFAULT (datetime('now'))
);

-- 作戦ボード（追補版 強化4）: 翌営業日の判定・提案指値・出口
CREATE TABLE IF NOT EXISTS daily_plan (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  ticker        TEXT NOT NULL,
  plan_date     TEXT NOT NULL,
  direction     TEXT,
  score         INTEGER,
  vol_ratio     REAL,
  weekly_trend  TEXT,
  limit_price   REAL,
  stop_price    REAL,
  target_price  REAL,
  rationale     TEXT,
  confidence    REAL,
  ai_summary    TEXT,
  ai_confidence INTEGER,
  ai_risks      TEXT,
  created_at    TEXT DEFAULT (datetime('now')),
  UNIQUE (ticker, plan_date)
);
"""

# app_meta の既定値
DEFAULT_META = {
    "buy_threshold": "2",
    "sell_threshold": "-2",
    "scheduler_enabled": "0",
    "scheduler_time": "16:00",   # JST・場後（HH:MM）
    "scheduler_demo": "0",
    "scheduler_skip_holidays": "1",   # 市場休業日（祝日）は自動更新しない
    "top_n": "3",   # 作戦ボード「今夜の推奨」の表示件数
}

# 起動時に投入する初期監視銘柄
DEFAULT_WATCHLIST = [
    ("8306.T", "三菱UFJフィナンシャル・グループ"),
    ("7203.T", "トヨタ自動車"),
    ("9984.T", "ソフトバンクグループ"),
    ("6758.T", "ソニーグループ"),
]


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _migrate_daily_plan(conn):
    """既存 data.db の daily_plan に後付けの追加列（AI解説・量的確信度）が無ければ追加（冪等）。"""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(daily_plan)").fetchall()}
    for col, decl in (("ai_summary", "TEXT"), ("ai_confidence", "INTEGER"),
                      ("ai_risks", "TEXT"), ("confidence", "REAL")):
        if col not in cols:
            conn.execute(f"ALTER TABLE daily_plan ADD COLUMN {col} {decl}")


def init_db():
    """スキーマ作成 + 初期データ（watchlist / signal_config）を投入。"""
    from signals import DEFAULT_CONFIGS

    with get_conn() as conn:
        conn.executescript(SCHEMA)
        _migrate_daily_plan(conn)

        # 監視銘柄が空なら既定を投入
        n = conn.execute("SELECT COUNT(*) AS c FROM watchlist").fetchone()["c"]
        if n == 0:
            conn.executemany(
                "INSERT INTO watchlist (ticker, name) VALUES (?, ?)",
                DEFAULT_WATCHLIST,
            )

        # シグナル設定が空なら既定（全銘柄共通・weight=1）を投入
        n = conn.execute("SELECT COUNT(*) AS c FROM signal_config").fetchone()["c"]
        if n == 0:
            conn.executemany(
                "INSERT INTO signal_config (ticker, rule_type, params, weight, enabled) "
                "VALUES (NULL, ?, ?, ?, ?)",
                [(c["rule_type"], json.dumps(c["params"]), c["weight"], c["enabled"])
                 for c in DEFAULT_CONFIGS],
            )

        # 追補版で追加した rule_type（volume_filter / weekly_trend_filter / atr_exit）を
        # 既存DBにも補完する（無いものだけ挿入）。
        existing = {r["rule_type"] for r in conn.execute(
            "SELECT rule_type FROM signal_config WHERE ticker IS NULL").fetchall()}
        for c in DEFAULT_CONFIGS:
            if c["rule_type"] not in existing:
                conn.execute(
                    "INSERT INTO signal_config (ticker, rule_type, params, weight, enabled) "
                    "VALUES (NULL, ?, ?, ?, ?)",
                    (c["rule_type"], json.dumps(c["params"]), c["weight"], c["enabled"]))

        # app_meta の既定値を未設定キーのみ投入
        conn.executemany(
            "INSERT OR IGNORE INTO app_meta (key, value) VALUES (?, ?)",
            list(DEFAULT_META.items()),
        )


# ---- app_meta（設定 key-value） ----
def get_meta(key: str, default=None):
    with get_conn() as conn:
        row = conn.execute("SELECT value FROM app_meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def get_all_meta():
    with get_conn() as conn:
        return {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM app_meta")}


def set_meta(key: str, value) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO app_meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, str(value)),
        )


def get_thresholds() -> tuple[int, int]:
    """(buy_threshold, sell_threshold) を返す。"""
    buy = int(get_meta("buy_threshold", "2"))
    sell = int(get_meta("sell_threshold", "-2"))
    return buy, sell


# ---- watchlist ----
def list_watchlist(only_enabled: bool = False):
    q = "SELECT * FROM watchlist"
    if only_enabled:
        q += " WHERE enabled = 1"
    q += " ORDER BY id"
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(q).fetchall()]


def add_watchlist(ticker: str, name: str):
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO watchlist (ticker, name) VALUES (?, ?)", (ticker, name))
        return cur.lastrowid


def delete_watchlist(item_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM watchlist WHERE id = ?", (item_id,))


# ---- signal_config ----
def list_configs(active_only: bool = False):
    q = "SELECT * FROM signal_config"
    if active_only:
        q += " WHERE enabled = 1"
    q += " ORDER BY id"
    with get_conn() as conn:
        rows = [dict(r) for r in conn.execute(q).fetchall()]
    for r in rows:
        r["params"] = json.loads(r["params"] or "{}")
    return rows


def add_config(rule_type: str, ticker=None, params=None, weight: int = 1, enabled: int = 1):
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO signal_config (ticker, rule_type, params, weight, enabled) "
            "VALUES (?, ?, ?, ?, ?)",
            (ticker, rule_type, json.dumps(params or {}), int(weight), 1 if enabled else 0))
        return cur.lastrowid


def delete_config(config_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM signal_config WHERE id = ?", (config_id,))


def update_config(config_id: int, weight=None, enabled=None, params=None):
    sets, vals = [], []
    if weight is not None:
        sets.append("weight = ?"); vals.append(int(weight))
    if enabled is not None:
        sets.append("enabled = ?"); vals.append(1 if enabled else 0)
    if params is not None:
        sets.append("params = ?"); vals.append(json.dumps(params))
    if not sets:
        return
    vals.append(config_id)
    with get_conn() as conn:
        conn.execute(f"UPDATE signal_config SET {', '.join(sets)} WHERE id = ?", vals)


# ---- price_data ----
def upsert_prices(ticker: str, df):
    """OHLCV DataFrame（小文字列・DatetimeIndex）を price_data に upsert。"""
    rows = []
    for idx, row in df.iterrows():
        rows.append((
            ticker, str(idx.date() if hasattr(idx, "date") else idx),
            float(row["open"]), float(row["high"]), float(row["low"]),
            float(row["close"]), int(row["volume"]),
        ))
    with get_conn() as conn:
        conn.executemany(
            "INSERT INTO price_data (ticker, date, open, high, low, close, volume) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(ticker, date) DO UPDATE SET "
            "open=excluded.open, high=excluded.high, low=excluded.low, "
            "close=excluded.close, volume=excluded.volume",
            rows,
        )


def latest_prices():
    """各 ticker の最新営業日の終値を {ticker: {date, close}} で返す。"""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT p.ticker, p.date, p.close FROM price_data p "
            "JOIN (SELECT ticker, MAX(date) AS d FROM price_data GROUP BY ticker) m "
            "ON p.ticker = m.ticker AND p.date = m.d"
        ).fetchall()
    return {r["ticker"]: {"date": r["date"], "close": r["close"]} for r in rows}


def load_prices(ticker: str):
    """price_data から OHLCV を DataFrame（小文字列・古い順）で返す。"""
    import pandas as pd

    with get_conn() as conn:
        rows = conn.execute(
            "SELECT date, open, high, low, close, volume FROM price_data "
            "WHERE ticker = ? ORDER BY date", (ticker,)).fetchall()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame([dict(r) for r in rows])
    df.index = pd.to_datetime(df["date"])
    return df[["open", "high", "low", "close", "volume"]]


# ---- signals ----
def insert_signal(ticker, date, score, direction, detail, notified=0):
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO signals (ticker, date, score, direction, detail, notified) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (ticker, date, score, direction, json.dumps(detail), notified))
        return cur.lastrowid


def list_signals(ticker=None, limit=100):
    q = "SELECT * FROM signals"
    params = []
    if ticker:
        q += " WHERE ticker = ?"; params.append(ticker)
    q += " ORDER BY date DESC, id DESC LIMIT ?"; params.append(limit)
    with get_conn() as conn:
        rows = [dict(r) for r in conn.execute(q, params).fetchall()]
    for r in rows:
        r["detail"] = json.loads(r["detail"] or "{}")
    return rows


def list_unnotified_signals():
    with get_conn() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM signals WHERE notified = 0 AND direction != 'neutral' "
            "ORDER BY date DESC, id DESC").fetchall()]
    for r in rows:
        r["detail"] = json.loads(r["detail"] or "{}")
    return rows


def mark_notified(signal_ids: list[int]):
    if not signal_ids:
        return
    with get_conn() as conn:
        conn.executemany("UPDATE signals SET notified = 1 WHERE id = ?",
                         [(i,) for i in signal_ids])


# ---- paper_trades ----
def insert_paper_trade(ticker, action, price, shares, date, signal_id=None):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO paper_trades (ticker, action, price, shares, date, signal_id) "
            "VALUES (?, ?, ?, ?, ?, ?)", (ticker, action, price, shares, date, signal_id))


def list_paper_trades(ticker=None):
    q = "SELECT * FROM paper_trades"
    params = []
    if ticker:
        q += " WHERE ticker = ?"; params.append(ticker)
    q += " ORDER BY date, id"
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(q, params).fetchall()]


# ---- holdings（保有ポジション） ----
def list_holdings():
    with get_conn() as conn:
        rows = conn.execute("SELECT ticker, shares, avg_cost FROM holdings ORDER BY ticker").fetchall()
    return [dict(r) for r in rows]


def upsert_holding(ticker: str, shares: float, avg_cost: float):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO holdings (ticker, shares, avg_cost, updated_at) "
            "VALUES (?, ?, ?, datetime('now')) "
            "ON CONFLICT(ticker) DO UPDATE SET "
            "shares=excluded.shares, avg_cost=excluded.avg_cost, updated_at=datetime('now')",
            (ticker, float(shares), float(avg_cost)))


def delete_holding(ticker: str):
    with get_conn() as conn:
        conn.execute("DELETE FROM holdings WHERE ticker = ?", (ticker,))


# ---- daily_plan（作戦ボード） ----
def upsert_plan(row: dict):
    """1銘柄分の作戦を (ticker, plan_date) で upsert。"""
    row = {**row}
    for k in ("ai_summary", "ai_confidence", "ai_risks", "confidence"):
        row.setdefault(k, None)
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO daily_plan "
            "(ticker, plan_date, direction, score, vol_ratio, weekly_trend, "
            " limit_price, stop_price, target_price, rationale, confidence, "
            " ai_summary, ai_confidence, ai_risks) "
            "VALUES (:ticker, :plan_date, :direction, :score, :vol_ratio, :weekly_trend, "
            " :limit_price, :stop_price, :target_price, :rationale, :confidence, "
            " :ai_summary, :ai_confidence, :ai_risks) "
            "ON CONFLICT(ticker, plan_date) DO UPDATE SET "
            "direction=excluded.direction, score=excluded.score, vol_ratio=excluded.vol_ratio, "
            "weekly_trend=excluded.weekly_trend, limit_price=excluded.limit_price, "
            "stop_price=excluded.stop_price, target_price=excluded.target_price, "
            "rationale=excluded.rationale, confidence=excluded.confidence, "
            "ai_summary=excluded.ai_summary, "
            "ai_confidence=excluded.ai_confidence, ai_risks=excluded.ai_risks, "
            "created_at=datetime('now')",
            row)


def latest_plan_date():
    with get_conn() as conn:
        row = conn.execute("SELECT MAX(plan_date) AS d FROM daily_plan").fetchone()
    return row["d"] if row else None


def list_plan(plan_date=None):
    if plan_date is None:
        plan_date = latest_plan_date()
    if plan_date is None:
        return []
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM daily_plan WHERE plan_date = ? ORDER BY "
            "CASE direction WHEN 'buy' THEN 0 WHEN 'sell' THEN 1 ELSE 2 END, ticker",
            (plan_date,)).fetchall()
    return [dict(r) for r in rows]
