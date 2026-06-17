"""costs.py の単体テスト（純粋関数・ネット非依存）。"""
from __future__ import annotations

import pytest

from costs import DEFAULT_COST, apply_costs, commission_cost, cost_from_configs


def test_default_cost_values():
    assert DEFAULT_COST["commission_bps"] == 0.0
    assert DEFAULT_COST["slippage_bps"] == 10.0


def test_apply_costs_buy_fills_higher():
    # 10bps スリッページ → 買いは0.1%高く約定（不利）
    assert apply_costs(1000.0, "buy", {"slippage_bps": 10.0}) == pytest.approx(1001.0)


def test_apply_costs_sell_fills_lower():
    assert apply_costs(1000.0, "sell", {"slippage_bps": 10.0}) == pytest.approx(999.0)


def test_apply_costs_zero_slippage_identity():
    assert apply_costs(1234.5, "buy", {"slippage_bps": 0.0}) == pytest.approx(1234.5)
    assert apply_costs(1234.5, "sell", {"slippage_bps": 0.0}) == pytest.approx(1234.5)


def test_apply_costs_none_uses_default():
    # 既定10bps が使われる
    assert apply_costs(1000.0, "buy") == pytest.approx(1001.0)


def test_apply_costs_unknown_side_raises():
    with pytest.raises(ValueError):
        apply_costs(100.0, "hold")


def test_commission_cost():
    assert commission_cost(100_000.0, {"commission_bps": 5.0}) == pytest.approx(50.0)
    assert commission_cost(100_000.0, {"commission_bps": 0.0}) == pytest.approx(0.0)


def test_cost_from_configs_reads_rule():
    cfgs = [{"rule_type": "cost_model", "params": {"commission_bps": 3, "slippage_bps": 7}}]
    assert cost_from_configs(cfgs) == {"commission_bps": 3.0, "slippage_bps": 7.0}


def test_cost_from_configs_defaults_when_absent():
    assert cost_from_configs([]) == DEFAULT_COST
    assert cost_from_configs(None) == DEFAULT_COST
