import tracking as tk


def _bar(d, o, h, l, c):
    return {"date": d, "open": o, "high": h, "low": l, "close": c}


BUY = {"direction": "buy", "limit_price": 100.0, "stop_price": 90.0, "target_price": 140.0}
# entry=limit=100, risk=entry-stop=10, target R=(140-100)/10=4.0


def test_buy_fill_then_target():
    bars = [_bar("d0", 101, 102, 99, 101),     # bar0: low99<=100 → 約定
            _bar("d1", 120, 141, 119, 140)]     # bar1: high141>=140 → target
    r = tk.resolve_outcome(BUY, bars)
    assert r["fill_status"] == "filled" and r["outcome"] == "target"
    assert abs(r["result_r"] - 4.0) < 1e-9 and r["exit_price"] == 140.0
    assert r["days_held"] == 1 and r["resolved_date"] == "d1"


def test_buy_fill_then_stop_is_minus_1r():
    bars = [_bar("d0", 101, 102, 99, 101), _bar("d1", 98, 99, 89, 90)]  # bar1 low89<=90 stop
    r = tk.resolve_outcome(BUY, bars)
    assert r["outcome"] == "stop" and abs(r["result_r"] + 1.0) < 1e-9


def test_buy_fill_then_open_when_no_exit():
    bars = [_bar("d0", 101, 102, 99, 101), _bar("d1", 101, 105, 96, 105)]  # 約定後 stop/target未到達
    r = tk.resolve_outcome(BUY, bars)
    assert r["outcome"] == "open" and r["resolved_date"] is None       # 非終端
    assert abs(r["result_r"] - (105 - 100) / 10) < 1e-9


def test_buy_unfilled_window_elapsed_is_expired():
    bars = [_bar(f"d{i}", 105, 106, 101, 105) for i in range(5)]       # 5本とも low>100 → 未約定・窓満了
    r = tk.resolve_outcome(BUY, bars, expiry=5)
    assert r["fill_status"] == "expired" and r["resolved_date"] == "d4"


def test_buy_unfilled_window_not_elapsed_is_pending():
    bars = [_bar("d0", 105, 106, 101, 105)]    # 1本のみ・未約定・窓未経過(expiry=5)
    r = tk.resolve_outcome(BUY, bars, expiry=5)
    assert r["fill_status"] == "pending" and r["resolved_date"] is None


def test_generation_day_empty_bars_is_pending():
    assert tk.resolve_outcome(BUY, [])["fill_status"] == "pending"      # 生成当日 future=[]


def test_no_same_bar_exit_after_fill():
    # bar0 が約定(low99<=100)かつ stop(low<=90)も同足ヒットだが、決済は fill_i+1 から → 同日決済しない
    bars = [_bar("d0", 101, 102, 88, 95)]      # bar0 low88<=90 だが約定足ゆえ無視
    r = tk.resolve_outcome(BUY, bars, expiry=5)
    assert r["outcome"] == "open"              # 決済走査対象バー無し → open(非終端)


def test_sell_symmetric_target():
    sell = {"direction": "sell", "limit_price": 100.0, "stop_price": 110.0, "target_price": 60.0}
    bars = [_bar("d0", 99, 101, 98, 99),        # bar0 high101>=100 → 約定(sell)
            _bar("d1", 70, 72, 59, 60)]          # bar1 low59<=60 → target
    r = tk.resolve_outcome(sell, bars)
    assert r["outcome"] == "target" and abs(r["result_r"] - 4.0) < 1e-9  # (100-60)/(110-100)


def test_na_cases():
    assert tk.resolve_outcome({"direction": "neutral"}, [])["fill_status"] == "n/a"
    assert tk.resolve_outcome({"direction": "buy", "limit_price": 100, "stop_price": None,
                               "target_price": 140}, [])["fill_status"] == "n/a"
    bad = {"direction": "buy", "limit_price": 90.0, "stop_price": 100.0, "target_price": 140.0}  # limit<=stop
    assert tk.resolve_outcome(bad, [])["fill_status"] == "n/a"          # risk<=0


def test_plan_type():
    assert tk.plan_type("buy", "risk_on") == "risk_on:buy"
    assert tk.plan_type("buy", None) == "unknown:buy"


def test_aggregate_excludes_na_and_uses_terminal():
    rows = [
        {"plan_type": "risk_on:buy", "fill_status": "filled", "outcome": "target", "result_r": 4.0, "days_held": 3},
        {"plan_type": "risk_on:buy", "fill_status": "filled", "outcome": "stop", "result_r": -1.0, "days_held": 2},
        {"plan_type": "risk_on:buy", "fill_status": "pending", "outcome": None, "result_r": None, "days_held": None},
        {"plan_type": "neutral:none", "fill_status": "n/a", "outcome": None, "result_r": None, "days_held": None},
    ]
    agg = {a["type"]: a for a in tk.aggregate_performance(rows)}
    assert "neutral:none" not in agg                           # n/a 除外(R1)
    a = agg["risk_on:buy"]
    assert a["n_plans"] == 3 and a["n_filled"] == 2            # pending も n_plans に含む・n/a 除外
    assert abs(a["fill_rate"] - 2 / 3) < 1e-9
    assert a["n_resolved"] == 2 and abs(a["win_rate"] - 50.0) < 1e-9
    assert abs(a["avg_r"] - 1.5) < 1e-9 and abs(a["avg_days"] - 2.5) < 1e-9
