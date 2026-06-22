"""API 結合テスト（FastAPI TestClient）。

隔離した一時 DB（INVEST_DB_PATH）に対して全エンドポイントを demo データで検証する。
ネットワーク不要。

    backend/venv/bin/python -m pytest backend/test_api.py -q
"""

from __future__ import annotations

import os
import tempfile

# db / main を import する前に、隔離した一時 DB を環境変数で指定する。
os.environ["INVEST_DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="invest-test-"), "test.db")

import pytest
from fastapi.testclient import TestClient

import main


@pytest.fixture(scope="module")
def client():
    # context manager で lifespan（init_db / スケジューラ起動）を実行する。
    with TestClient(main.app) as c:
        yield c


@pytest.fixture(autouse=True)
def _no_gemini(monkeypatch):
    """テストでは Gemini を呼ばない（ネットワーク非依存を維持・無料枠保護）。

    backend/.env に GEMINI_API_KEY があると ai_commentary が読み込むため、
    各テストでキーを無効化し generate_commentary を None にする。
    """
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)


def test_root_exposes_thresholds(client):
    r = client.get("/")
    assert r.status_code == 200
    body = r.json()
    assert body["buy_threshold"] == 2
    assert body["sell_threshold"] == -2


def test_watchlist_crud(client):
    r = client.get("/watchlist")
    assert r.status_code == 200
    initial = r.json()
    assert len(initial) == 4  # 既定の4銘柄

    created = client.post("/watchlist", json={"ticker": "TEST.T", "name": "テスト銘柄"}).json()
    assert client.get("/watchlist").json().__len__() == 5

    client.delete(f"/watchlist/{created['id']}")
    assert client.get("/watchlist").json().__len__() == 4


def test_stock_search_and_name_resolution(client):
    # 名前で検索
    hits = client.get("/stocks/search?q=トヨタ").json()
    assert any(h["ticker"] == "7203.T" for h in hits)
    # コードで検索
    hits2 = client.get("/stocks/search?q=6501").json()
    assert any(h["ticker"] == "6501.T" and "日立" in h["name"] for h in hits2)
    # 内蔵マスタからの名前解決（コード '6501' → '6501.T'）
    nm = client.get("/stocks/name?ticker=6501").json()
    assert nm["ticker"] == "6501.T" and nm["name"] == "日立製作所" and nm["source"] == "master"


def test_add_watch_resolves_name_when_blank(client):
    # name 省略・コードだけ → サーバが内蔵マスタから名前を補完し .T も付与
    created = client.post("/watchlist", json={"ticker": "6501"}).json()
    assert created["ticker"] == "6501.T"
    assert created["name"] == "日立製作所"
    client.delete(f"/watchlist/{created['id']}")


def test_config_crud_and_price_target(client):
    configs = client.get("/config").json()
    indicators = [c for c in configs if c["rule_type"] != "price_target"]
    # 既定: 状態ベース6指標 + 乖離率/OBV/CCI + 追補版3フィルター（volume/weekly/atr）+ 地合い（market_regime）+ 相対力（relative_strength）= 14
    assert len(indicators) == 14

    # 重み更新
    target = indicators[0]
    client.put("/config", json={"updates": [{"id": target["id"], "weight": 3}]})
    updated = next(c for c in client.get("/config").json() if c["id"] == target["id"])
    assert updated["weight"] == 3

    # price_target の作成 → 一覧に出る → 削除
    new_id = client.post("/config", json={
        "rule_type": "price_target", "ticker": "8306.T",
        "params": {"above": 9999, "below": 1},
    }).json()["id"]
    pts = [c for c in client.get("/config").json() if c["rule_type"] == "price_target"]
    assert any(c["id"] == new_id for c in pts)
    client.delete(f"/config/{new_id}")
    assert all(c["id"] != new_id for c in client.get("/config").json())

    # 後続テストに影響しないよう重みを戻す
    client.put("/config", json={"updates": [{"id": target["id"], "weight": 1}]})


def test_settings_get_and_update(client):
    s = client.get("/settings").json()
    assert s["buy_threshold"] == 2 and s["sell_threshold"] == -2
    assert s["scheduler_enabled"] is False

    client.put("/settings", json={"buy_threshold": 3, "scheduler_enabled": True,
                                  "scheduler_time": "15:30"})
    s2 = client.get("/settings").json()
    assert s2["buy_threshold"] == 3
    assert s2["scheduler_enabled"] is True
    assert s2["scheduler_time"] == "15:30"

    # 打ち手6: top_n（既定3）
    assert s["top_n"] == 3
    client.put("/settings", json={"top_n": 5})
    assert client.get("/settings").json()["top_n"] == 5
    client.put("/settings", json={"top_n": -2})            # 負値はフォールバック（既定3）
    assert client.get("/settings").json()["top_n"] == 3
    client.put("/settings", json={"top_n": 3})             # 後始末

    # 打ち手8: account_size / risk_pct（既定 100万 / 1.0%）
    assert s["account_size"] == 1_000_000.0
    assert s["risk_pct"] == 1.0
    client.put("/settings", json={"account_size": 2_000_000, "risk_pct": 0.5})
    s3 = client.get("/settings").json()
    assert s3["account_size"] == 2_000_000.0 and s3["risk_pct"] == 0.5
    client.put("/settings", json={"risk_pct": 0})       # 範囲外（0）→既定へ
    assert client.get("/settings").json()["risk_pct"] == 1.0
    client.put("/settings", json={"risk_pct": 150})     # 範囲外（>100）→既定へ
    assert client.get("/settings").json()["risk_pct"] == 1.0
    client.put("/settings", json={"account_size": -5})  # 範囲外（負）→既定へ
    assert client.get("/settings").json()["account_size"] == 1_000_000.0
    client.put("/settings", json={"account_size": 1_000_000, "risk_pct": 1.0})  # 後始末

    # 既定に戻す（他テストへ影響しないように）
    client.put("/settings", json={"buy_threshold": 2, "scheduler_enabled": False})


def test_refresh_signals_and_prices(client):
    r = client.post("/refresh?demo=true")
    assert r.status_code == 200
    body = r.json()
    assert len(body["updated"]) == 4
    assert body["failed"] == []
    for row in body["updated"]:
        assert row["direction"] in ("buy", "sell", "neutral")

    # シグナルが保存されている
    signals = client.get("/signals?limit=10").json()
    assert len(signals) >= 4

    # 最新価格 / ローソク足
    latest = client.get("/prices_latest").json()
    assert "8306.T" in latest and latest["8306.T"]["close"] > 0
    candles = client.get("/prices/8306.T").json()
    assert len(candles) > 0 and {"open", "high", "low", "close"} <= candles[0].keys()


def test_refresh_runs_with_relative_strength(client):
    r = client.post("/refresh?demo=true")
    assert r.status_code == 200
    body = r.json()
    assert len(body["updated"]) == 4                 # RS 経路を通っても従来どおり完走
    for row in body["updated"]:
        assert row["direction"] in ("buy", "sell", "neutral")
    # plan に量的 confidence が露出（RS が織り込まれた値・float か None）
    plan = client.get("/plan").json()["rows"]
    assert all(("confidence" in row) for row in plan)


def test_unnotified_and_mark_notified(client):
    # price_target（下限）を入れて refresh → 必ず通知シグナルが出る状況を作る
    pt_id = client.post("/config", json={
        "rule_type": "price_target", "ticker": "8306.T",
        "params": {"below": 10_000_000},   # demo 価格は必ず下回る → buy 通知
    }).json()["id"]
    client.post("/refresh?demo=true")

    unn = client.get("/signals/unnotified").json()
    assert len(unn) >= 1
    assert all(s["direction"] != "neutral" for s in unn)

    ids = [s["id"] for s in unn]
    client.post("/signals/mark_notified", json={"ids": ids})
    after = client.get("/signals/unnotified").json()
    assert all(s["id"] not in ids for s in after)

    client.delete(f"/config/{pt_id}")


def test_plan_generate_and_get(client):
    gen = client.post("/plan/generate?demo=true").json()
    assert gen["failed"] == []
    assert gen["plan_date"]
    assert len(gen["rows"]) == 4   # 監視4銘柄ぶん

    got = client.get("/plan").json()
    assert got["plan_date"] == gen["plan_date"]
    tickers = {r["ticker"] for r in got["rows"]}
    assert "8306.T" in tickers
    # AI解説の列は存在する（GEMINI_API_KEY 未設定なら値は None で従来どおり動作）
    for r in got["rows"]:
        assert "ai_summary" in r and "ai_confidence" in r and "ai_risks" in r
    # buy/sell の行は提案指値・出口が埋まっている
    for r in got["rows"]:
        if r["direction"] in ("buy", "sell"):
            assert r["limit_price"] is not None
            assert r["stop_price"] is not None and r["target_price"] is not None
    # 打ち手6: 量的確信度カラムが存在し、buy/sell 行は 0..100 の値を持つ（ai_confidence とは別物）
    for r in got["rows"]:
        assert "confidence" in r
        if r["direction"] in ("buy", "sell") and r["confidence"] is not None:
            assert 0 <= r["confidence"] <= 100


def test_holdings_crud(client):
    assert client.get("/holdings").json() == []
    client.put("/holdings", json={"ticker": "8306.T", "shares": 100, "avg_cost": 3210})
    hs = client.get("/holdings").json()
    assert len(hs) == 1 and hs[0]["ticker"] == "8306.T" and hs[0]["shares"] == 100

    # 上書き
    client.put("/holdings", json={"ticker": "8306.T", "shares": 150, "avg_cost": 3180})
    assert client.get("/holdings").json()[0]["shares"] == 150

    # shares<=0 は保有解除
    client.put("/holdings", json={"ticker": "8306.T", "shares": 0, "avg_cost": 3180})
    assert client.get("/holdings").json() == []

    # DELETE エンドポイント
    client.put("/holdings", json={"ticker": "7203.T", "shares": 50, "avg_cost": 2800})
    client.delete("/holdings/7203.T")
    assert client.get("/holdings").json() == []


def test_backtest_demo(client):
    r = client.post("/backtest", json={"demo": True, "days": 60, "initial_capital": 3000})
    assert r.status_code == 200
    res = r.json()
    for key in ("initial", "final", "pnl_amount", "pnl_pct", "trade_count",
                "win_rate", "max_drawdown_pct", "equity_curve", "trades"):
        assert key in res
    assert res["initial"] == 3000
    assert res["trade_count"] >= 0
    assert len(res["equity_curve"]) > 0


def test_backtest_atr_exit_mode(client):
    r = client.post("/backtest", json={"demo": True, "days": 60, "exit_mode": "atr"})
    assert r.status_code == 200
    res = r.json()
    assert res["exit_mode"] == "plan"   # atr は plan のエイリアス（約定=提示指値）
    for key in ("take_profit_count", "stop_loss_count", "signal_exit_count",
                "avg_holding_days", "risk_reward"):
        assert key in res


def test_backtest_includes_cost_and_significance(client):
    r = client.post("/backtest", json={"demo": True, "days": 60, "exit_mode": "plan"})
    assert r.status_code == 200
    res = r.json()
    assert res["exit_mode"] == "plan"
    assert "cost" in res and res["cost"]["slippage_bps"] == 10.0
    assert "fill_rate" in res
    assert "significance" in res and "n" in res["significance"]
    assert "benchmark" in res and "buy_hold_pct" in res["benchmark"]


def test_optimize_holdout_in_sample_out_of_sample(client):
    r = client.post("/optimize", json={"demo": True, "split_ratio": 0.7})
    assert r.status_code == 200
    res = r.json()
    assert res["in_sample"]["sample"] == "in_sample"
    assert res["out_of_sample"]["sample"] == "out_of_sample"
    assert "overfit_gap" in res
    assert "significance" in res and "benchmark" in res
    assert res["chosen_params"]["threshold"] in (1, 2, 3)


def test_backtest_signals_include_regime(client):
    r = client.post("/backtest", json={"demo": True, "days": 60, "exit_mode": "plan"})
    assert r.status_code == 200
    res = r.json()
    assert res["signals"] and "regime" in res["signals"][0]["detail"]


def test_optimize_still_ok_with_regime(client):
    r = client.post("/optimize", json={"demo": True, "split_ratio": 0.7})
    assert r.status_code == 200
    assert r.json()["out_of_sample"]["sample"] == "out_of_sample"


def test_daily_plan_sizing_columns_and_upsert(tmp_path, monkeypatch):
    import sqlite3
    import db as dbmod
    dbfile = tmp_path / "old.db"
    # 打ち手8 以前の現実的な daily_plan（base 列＋ai/confidence はあるが shares/risk_amount だけ無い）。
    conn = sqlite3.connect(dbfile)
    conn.execute(
        "CREATE TABLE daily_plan (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "ticker TEXT NOT NULL, plan_date TEXT NOT NULL, direction TEXT, score INTEGER, "
        "vol_ratio REAL, weekly_trend TEXT, limit_price REAL, stop_price REAL, "
        "target_price REAL, rationale TEXT, confidence REAL, ai_summary TEXT, "
        "ai_confidence INTEGER, ai_risks TEXT, created_at TEXT, UNIQUE(ticker, plan_date))")
    conn.execute("INSERT INTO daily_plan (ticker, plan_date, direction, score) "
                 "VALUES ('OLD.T','2026-06-01','buy',3)")
    conn.commit(); conn.close()

    # get_conn() は呼び出し時にモジュール global db.DB_PATH を読む（@contextmanager）。
    # monkeypatch.setattr なら teardown で自動復元＝他テストを汚染しない（reload は使わない）。
    monkeypatch.setattr(dbmod, "DB_PATH", str(dbfile))
    dbmod.init_db()                                   # 冪等マイグレーションで shares/risk_amount を追加
    with dbmod.get_conn() as conn:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(daily_plan)")}
    assert {"shares", "risk_amount"} <= cols
    # 旧行は NULL のまま読める
    old = [r for r in dbmod.list_plan("2026-06-01") if r["ticker"] == "OLD.T"][0]
    assert old["shares"] is None and old["risk_amount"] is None
    # 新規 upsert で値が入る（perform_refresh と同様に vol_ratio/weekly_trend も渡す）
    dbmod.upsert_plan({"ticker": "NEW.T", "plan_date": "2026-06-01", "direction": "buy",
                       "score": 3, "vol_ratio": None, "weekly_trend": None,
                       "limit_price": 1000.0, "stop_price": 950.0,
                       "target_price": 1100.0, "rationale": "x",
                       "confidence": 70.0, "shares": 200.0, "risk_amount": 10000.0})
    new = [r for r in dbmod.list_plan("2026-06-01") if r["ticker"] == "NEW.T"][0]
    assert new["shares"] == 200.0 and new["risk_amount"] == 10000.0


def test_plan_has_risk_sizing_for_buy(client):
    """null 経路の不変条件: 全 plan 行に shares/risk_amount キーが存在し、非 buy 行は None。
    （buy 経路の実サイジングは test_perform_refresh_sizes_buy_end_to_end が担保）"""
    # 7203.T は DEFAULT_WATCHLIST に既存。watchlist に ticker UNIQUE が無いため
    # 無条件 POST は重複行を残す（共有 module-scoped DB を汚す）。未登録時のみ追加する。
    if not any(w["ticker"] == "7203.T" for w in client.get("/watchlist").json()):
        client.post("/watchlist", json={"ticker": "7203.T", "name": "トヨタ"})
    client.post("/refresh?demo=true")
    rows = client.get("/plan").json()["rows"]
    assert rows, "プラン行が無い"
    for r in rows:
        if r["direction"] == "buy" and r["limit_price"] and r["stop_price"]:
            assert isinstance(r["shares"], (int, float)) and r["shares"] >= 0
            assert isinstance(r["risk_amount"], (int, float)) and r["risk_amount"] >= 0
        else:
            assert r["shares"] is None and r["risk_amount"] is None


def test_perform_refresh_sizes_buy_end_to_end(client, monkeypatch):
    """buy 経路を実際に通す統合テスト: 上昇トレンドを get_history に流し、
    perform_refresh が position_size と同じ推奨株数を daily_plan に永続化することを確認する。
    （既存の null 経路テストと対で buy/非buy 両分岐をカバー）"""
    import numpy as np
    import pandas as pd
    import main
    from signals import position_size
    # 決定論的な上昇トレンド OHLC（test_signals の _idx 流儀）→ buy を出す
    # high/low に固定スプレッドを付与してATRを確保する（high==low==close だと
    # ATR≈step_size≈2.52 になり stop_price が limit_price を上回り shares=0 になる）
    # 末尾を today にすることで _next_business_day が他テストと同じ plan_date を生成し、
    # latest_plan_date() が返す日付に UPTREND.T 行が含まれる（過去日付だと埋もれる）
    closes = np.linspace(1000.0, 1300.0, 120)
    spread = 10.0  # 固定スプレッドで完全決定論的
    idx = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=len(closes))
    up = pd.DataFrame({"open": closes - spread * 0.3,
                       "high": closes + spread,
                       "low":  closes - spread,
                       "close": closes,
                       "volume": np.full(len(closes), 1e6)}, index=idx)
    monkeypatch.setattr(main, "get_history", lambda *a, **k: up.copy())

    client.post("/watchlist", json={"ticker": "UPTREND.T", "name": "上昇"})
    # 既定閾値2では score=1 で neutral になるため、買い閾値を1に下げる（test_signals と同方針）
    client.put("/settings", json={"buy_threshold": 1, "sell_threshold": -1})
    try:
        client.post("/refresh?demo=true")
        rows = client.get("/plan").json()["rows"]
        row = [r for r in rows if r["ticker"] == "UPTREND.T"][0]
        assert row["direction"] == "buy"
        assert isinstance(row["shares"], (int, float)) and row["shares"] > 0
        assert isinstance(row["risk_amount"], (int, float)) and row["risk_amount"] > 0
        # perform_refresh が position_size と同じ株数を永続化していること
        # （account_size 既定100万・risk_pct 既定1.0・永続化された confidence を使用）
        expected = position_size(row["limit_price"], row["stop_price"],
                                 1_000_000.0, 1.0, confidence=row["confidence"])
        assert abs(row["shares"] - expected["shares"]) < 1e-6
        assert abs(row["risk_amount"] - expected["risk_amount"]) < 1e-6
    finally:
        # 後始末: 閾値を既定に戻し、テスト銘柄を削除（共有DBを汚さない）
        client.put("/settings", json={"buy_threshold": 2, "sell_threshold": -2})
        wl = client.get("/watchlist").json()
        for w in wl:
            if w["ticker"] == "UPTREND.T":
                client.delete(f"/watchlist/{w['id']}")
