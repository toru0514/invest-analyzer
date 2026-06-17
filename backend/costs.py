"""売買コストモデル（手数料・スリッページ）。純粋関数・依存なし。

bps = ベーシスポイント = 0.01%。すべて片道（往復はエントリー＋エグジットで2回適用）。
キー未設定・None は DEFAULT_COST にフォールバックする。
"""
from __future__ import annotations

# 既定：ネット証券ゼロコース（手数料0）＋スリッページ片道10bps(0.1%)
DEFAULT_COST: dict[str, float] = {"commission_bps": 0.0, "slippage_bps": 10.0}


def apply_costs(price: float, side: str, cost: dict | None = None) -> float:
    """スリッページを反映した約定価格を返す。買いは高く・売りは安く（ともに不利方向）。"""
    c = cost or DEFAULT_COST
    slip = float(c.get("slippage_bps", 0.0)) / 1e4
    if side == "buy":
        return price * (1.0 + slip)
    if side == "sell":
        return price * (1.0 - slip)
    raise ValueError(f"unknown side: {side}")


def commission_cost(notional: float, cost: dict | None = None) -> float:
    """約定代金に対する片道手数料額（円・絶対値）。"""
    c = cost or DEFAULT_COST
    return abs(notional) * float(c.get("commission_bps", 0.0)) / 1e4


def cost_from_configs(configs: list[dict] | None) -> dict:
    """signal_config の cost_model ルールからコストを読む。無ければ DEFAULT_COST。"""
    for c in (configs or []):
        if c.get("rule_type") == "cost_model":
            p = c.get("params") or {}
            return {
                "commission_bps": float(p.get("commission_bps", DEFAULT_COST["commission_bps"])),
                "slippage_bps": float(p.get("slippage_bps", DEFAULT_COST["slippage_bps"])),
            }
    return dict(DEFAULT_COST)
