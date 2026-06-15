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
"""

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


def init_db():
    """スキーマ作成 + 初期データ（watchlist / signal_config）を投入。"""
    from signals import DEFAULT_CONFIGS

    with get_conn() as conn:
        conn.executescript(SCHEMA)

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
