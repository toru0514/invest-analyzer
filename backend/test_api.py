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


def test_config_crud_and_price_target(client):
    configs = client.get("/config").json()
    indicators = [c for c in configs if c["rule_type"] != "price_target"]
    # 既定: 状態ベース6指標 + 乖離率/OBV/CCI + 追補版3フィルター（volume/weekly/atr）= 12
    assert len(indicators) == 12

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
    # buy/sell の行は提案指値・出口が埋まっている
    for r in got["rows"]:
        if r["direction"] in ("buy", "sell"):
            assert r["limit_price"] is not None
            assert r["stop_price"] is not None and r["target_price"] is not None


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
    assert res["exit_mode"] == "atr"
    for key in ("take_profit_count", "stop_loss_count", "signal_exit_count",
                "avg_holding_days", "risk_reward"):
        assert key in res
