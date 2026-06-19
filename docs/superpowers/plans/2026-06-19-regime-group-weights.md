# レジーム別グループ重み（打ち手5）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** グループ合算を地合い依存の重み付きにする：`score = Σ weight(regime, group) × clip(group, ±1)`。トレンド時は順張り群、レンジ時は逆張り群を主役に切り替える。

**Architecture:** `signals.py` の `_score_indicators` 末尾（クリップ後グループの合算）だけをレジーム重み付き合算に変更し、`evaluate` から `regime` を1引数渡すだけで配線完了（backtest/endpoint は打ち手3で既に `evaluate(regime=)` 済み＝無改修で追従）。重み（本設計）とゲート（打ち手3の risk_off×BUY ペナルティ）は別軸で共存。後方互換は `regime=None` で全重み1（＝打ち手4と完全一致）。

**Tech Stack:** Python 3.13 / pandas(3.x) / pandas_ta / pytest。テストは決定論・ネット非依存。

**設計書:** `docs/superpowers/specs/2026-06-19-regime-group-weights-design.md`

---

## File Structure

- `backend/signals.py`（変更）
  - `REGIME_GROUP_WEIGHTS` 定数を追加（`GROUP_CAP` の直後）。
  - `_group_weight(regime, group)` ヘルパーを追加（`_score_indicators` の直前）。
  - `_score_indicators(df, configs, regime=None)`：シグネチャに `regime` を追加し、末尾の合算のみ重み付きに。`detail["_regime"]` を記録。docstring 更新。
  - `evaluate`：`_score_indicators(df_ind, configs)` → `_score_indicators(df_ind, configs, regime)`。docstring 更新。
- `backend/test_signals.py`（変更）
  - 既存2件を更新（理由は各タスク参照）：
    - `test_evaluate_regime_off_penalizes_a_buy_signal`（決定論化＝既存flake解消＋打ち手5堅牢化）
    - `test_evaluate_regime_records_and_no_change_when_not_risk_off`（「risk_on で不変」は打ち手5と矛盾 → ゲート意味論へ置換）
  - 新規テストを追加（`_score_indicators` 直呼びのユニット＋`evaluate` 経由の統合）。

**配線の波及（無改修で追従・確認済み）:** `_score_indicators` の呼び出し元は `evaluate` のみ（＋テスト3件は2引数なので `regime=None` 既定で不変）。`evaluate` は `backtest.py`(4箇所)・`main.py:360` から既に `regime=` 付きで呼ばれている → `evaluate` を配線すればバックテスト/エンドポイントに自動反映。`detail["_regime"]` は新規キーで既存consumerなし（フロント/ai_commentary は別dict）。

---

## ⚠️ 着手前の前提（重要）

**ベースラインは現状 84/85 で、`test_evaluate_regime_off_penalizes_a_buy_signal` が既に失敗している。**
- 原因：このテストは「`synthetic_history` の seed 0〜19 のどれかが buy シグナルを出す」を前提に seed 走査するが、現環境（pandas 3.x 等）では1つも buy が出ず `AssertionError("buy シグナルの合成データが見つからない")`。
- ブランチ `feature/regime-group-weights` は backend コードが main と同一（docsのみ追加）なので、これは**打ち手5 とは独立した既存の環境依存failであり main 由来**。
- Task 1 でこのテストを決定論的な統制データへ書き換えることで、**既存flakeの解消**と**打ち手5（risk_off の trend×2 重み付けと `os_==bs-2` 前提の矛盾）への堅牢化**を同時に達成する。

---

## Task 1: 既存の脆弱テストを決定論化してベースラインを緑に

**Files:**
- Modify: `backend/test_signals.py:258-269`（`test_evaluate_regime_off_penalizes_a_buy_signal`）

**意図:** seed 走査（環境依存）を廃し、統制データ＋単一の逆張り指標でゲート減点(-2)を**厳密・決定論的**に検証する。rsi は `contrarian` グループで risk_off の重みは 1（＝重み付けの影響を受けない）ため、現コード（重みなし）でも打ち手5（重みあり）でも同一に通る。

- [ ] **Step 1: 既存テストを決定論版へ置換**

`backend/test_signals.py` の以下を置換する。

置換前（258-269行）:
```python
def test_evaluate_regime_off_penalizes_a_buy_signal():
    from signals import evaluate, DEFAULT_CONFIGS
    from market import synthetic_history
    for seed in range(20):
        df = synthetic_history("X.T", seed=seed)
        bs, bd, _ = evaluate(df, DEFAULT_CONFIGS, 2, -2)
        if bd == "buy":
            os_, od, odt = evaluate(df, DEFAULT_CONFIGS, 2, -2, regime="risk_off")
            assert os_ == bs - 2
            assert odt["regime_filter"] == -2
            return
    raise AssertionError("buy シグナルの合成データが見つからない（前提を見直す）")
```

置換後:
```python
def test_evaluate_regime_off_penalizes_a_buy_signal():
    """risk_off は買い判定にゲート減点(-2)を課す（打ち手3）。打ち手5の重み付け後に適用される。

    単一の逆張り指標(rsi)＋売られすぎの統制データで決定論化。rsi は contrarian グループで
    risk_off の重みは 1（重み付けの影響を受けない）ため、ゲート減点だけを厳密に検証できる。
    ゲートは config-gated（`evaluate` 内 `_find_cfg(configs, "market_regime")` が None だと
    発火しない）ため、cfg に market_regime ルールを必ず含める（剥がすとゲートが効かない）。
    market_regime は `_score_indicators` ではスコア対象外なので base スコアには影響しない。
    """
    cfg = [
        {"rule_type": "rsi", "params": {"length": 14, "low": 30, "high": 70},
         "weight": 1, "enabled": 1},
        {"rule_type": "market_regime",
         "params": {"mode": "penalty", "penalty": 2, "sma": 13,
                    "dd_lookback": 60, "dd_threshold": 0.10},
         "weight": 1, "enabled": 1},
    ]
    df = _declining_df()                                  # 売られすぎ → rsi が買い(+1)
    base = evaluate(df, cfg, 1, -1)                       # regime=None → score=1, buy
    assert (base[0], base[1]) == (1, "buy")
    off = evaluate(df, cfg, 1, -1, regime="risk_off")
    assert off[2]["regime_filter"] == -2                 # ゲートが発火
    assert off[0] == base[0] - 2                          # 重み付け不変(contrarian×1)＋ゲートで-2
    assert off[1] != "buy"                                # 買いが抑制される
```

- [ ] **Step 2: 当該テストが通ることを確認**

Run: `backend/venv/bin/python -m pytest backend/test_signals.py::test_evaluate_regime_off_penalizes_a_buy_signal -v`
Expected: PASS（`_declining_df()` の急落で RSI<30 → rsi買い+1。既存 `test_grouping_clips_high_weight_to_cap` が同データで rsi 発火を実証済み）

- [ ] **Step 3: 全 backend スイートが緑（85/85）になることを確認**

Run: `backend/venv/bin/python -m pytest backend/ -q`
Expected: `85 passed`（既存flake解消でベースラインが緑に戻る）

- [ ] **Step 4: コミット**

```bash
git add backend/test_signals.py
git commit -m "test: ゲート減点テストを決定論化（合成データのbuy非発火による環境依存failを解消）"
```

---

## Task 2: `_score_indicators` にレジーム別グループ重みを追加（capability・配線前）

**Files:**
- Modify: `backend/signals.py:245`（`GROUP_CAP` 直後に定数）/ `:248`（ヘルパー＋シグネチャ）/ `:370-373`（合算）
- Test: `backend/test_signals.py`（新規ユニットテストを `_declining_df` 定義の後ろ、grouping テスト群の近くに追加）

この段階では `evaluate` は無改修（`_score_indicators` を2引数で呼び続ける）。よって `evaluate` 経由の既存テストは全て不変のまま、`_score_indicators` を直呼びする新ユニットテストで重み付けを駆動する。

- [ ] **Step 1: 失敗するユニットテストを書く（RED）**

`backend/test_signals.py` に追加（`_declining_df()` 定義より後、例: `test_score_detail_has_groups_within_cap` の後）:
```python
def test_score_indicators_risk_on_doubles_trend():
    """risk_on は trend グループを ×2 にする（順張り主体）。"""
    df_ind = signals.add_indicators(_idx(np.linspace(1000, 1300, 120)))  # 上昇 → trend買い
    cfg = _base_configs()
    none_score, none_detail = signals._score_indicators(df_ind, cfg)            # regime=None
    on_score, on_detail = signals._score_indicators(df_ind, cfg, "risk_on")
    assert none_detail["_groups"]["trend"] == 1                                  # 順張りが買い側
    assert on_score == none_score + none_detail["_groups"]["trend"]             # trend のみ ×2
    assert on_detail["_regime"] == "risk_on"
    assert isinstance(on_score, int)                                            # 整数重み×int → int


def test_score_indicators_neutral_doubles_contrarian():
    """neutral は contrarian グループを ×2 にする（レンジでの逆張り）。"""
    df_ind = signals.add_indicators(_declining_df())                            # 売られすぎ → 逆張り買い
    cfg = _base_configs()
    none_score, none_detail = signals._score_indicators(df_ind, cfg)
    neu_score, neu_detail = signals._score_indicators(df_ind, cfg, "neutral")
    assert none_detail["_groups"]["contrarian"] == 1
    assert neu_score == none_score + none_detail["_groups"]["contrarian"]       # contrarian のみ ×2
    assert neu_detail["_regime"] == "neutral"


def test_score_indicators_regime_none_equals_unweighted():
    """regime=None → 全重み1 → 打ち手4と完全一致（クリップ後グループの単純合計）。"""
    df_ind = signals.add_indicators(_declining_df())
    cfg = _base_configs()
    score, detail = signals._score_indicators(df_ind, cfg)
    assert score == sum(detail["_groups"].values())
    assert detail["_regime"] is None
```

- [ ] **Step 2: RED を確認**

Run: `backend/venv/bin/python -m pytest backend/test_signals.py -k "score_indicators_risk_on or score_indicators_neutral or regime_none_equals" -v`
Expected: FAIL
- `..._risk_on_doubles_trend` / `..._neutral_doubles_contrarian`：`TypeError: _score_indicators() takes 2 positional arguments but 3 were given`
- `..._regime_none_equals_unweighted`：`KeyError: '_regime'`（`detail["_regime"]` 未実装）

- [ ] **Step 3: 重み定数を追加**

`backend/signals.py` の `GROUP_CAP = 1   # グループ内の最大寄与（±）`（245行）の直後に追加:
```python

# レジーム別グループ重み（打ち手5）。グループ純額(±GROUP_CAP)に乗じてから合算する。
# トレンド時は順張り(trend)、レンジ時は逆張り(contrarian)を主役にする。
# regime が None / 未知 → 全グループ 1（重みなし＝打ち手4の挙動）。
REGIME_GROUP_WEIGHTS: dict[str, dict[str, int]] = {
    "risk_on":  {"trend": 2, "contrarian": 1, "volume": 1, "pattern": 1},
    "neutral":  {"trend": 1, "contrarian": 2, "volume": 1, "pattern": 1},
    "risk_off": {"trend": 2, "contrarian": 1, "volume": 1, "pattern": 1},
}


def _group_weight(regime: str | None, group: str) -> int:
    """レジーム×グループの整数重み。未知レジーム/未知グループは 1（安全フォールバック）。"""
    return REGIME_GROUP_WEIGHTS.get(regime, {}).get(group, 1)
```

- [ ] **Step 4: `_score_indicators` のシグネチャと合算を変更**

`backend/signals.py:248` のシグネチャを変更:
```python
def _score_indicators(df: pd.DataFrame, configs: list[dict[str, Any]],
                      regime: str | None = None) -> tuple[int, dict]:
```

docstring（249-254行）の末尾に1文追記:
```python
    最後にグループ純額を ±GROUP_CAP にクリップし、レジーム別重み（打ち手5）を乗じて合算する：
    score = Σ weight(regime, group) × clip(group, ±GROUP_CAP)。regime=None は全重み1（＝打ち手4）。
```

合算部（370-373行）を変更:

変更前:
```python
    groups = {g: max(-GROUP_CAP, min(GROUP_CAP, raw)) for g, raw in group_raw.items()}
    detail["_groups"] = groups
    score = sum(groups.values())
    return score, detail
```

変更後:
```python
    groups = {g: max(-GROUP_CAP, min(GROUP_CAP, raw)) for g, raw in group_raw.items()}
    detail["_groups"] = groups            # クリップ後・重み適用前の純額（解釈性）
    detail["_regime"] = regime            # どのレジームで重み付けたか
    score = sum(_group_weight(regime, g) * v for g, v in groups.items())
    return score, detail
```

- [ ] **Step 5: GREEN を確認（ユニット）**

Run: `backend/venv/bin/python -m pytest backend/test_signals.py -k "score_indicators_risk_on or score_indicators_neutral or regime_none_equals" -v`
Expected: 3 passed

- [ ] **Step 6: 全 backend スイートが緑（evaluate 無改修なので既存は不変）**

Run: `backend/venv/bin/python -m pytest backend/ -q`
Expected: `88 passed`（85 + 新規3）。`evaluate` を2引数で呼ぶ既存テスト・直呼び3件（2引数→`regime=None`）はすべて不変。

- [ ] **Step 7: コミット**

```bash
git add backend/signals.py backend/test_signals.py
git commit -m "feat: _score_indicators にレジーム別グループ重みを追加（配線は次段・打ち手5）"
```

---

## Task 3: `evaluate` をレジーム重みに配線＋統合テスト＋矛盾テスト更新

**Files:**
- Modify: `backend/signals.py:392`（`evaluate` の `_score_indicators` 呼び出し）/ docstring（383-388行）
- Modify: `backend/test_signals.py:248-256`（`test_evaluate_regime_records_and_no_change_when_not_risk_off` を置換）
- Test: `backend/test_signals.py`（`evaluate` 経由の統合テストを追加）

- [ ] **Step 1: 矛盾する既存テストを置換**

打ち手5 では risk_on が trend を増幅するため「risk_on でスコア・direction 不変」は**偽**になる（設計書 §7）。`backend/test_signals.py:248-256` を置換する。

置換前（248-256行）:
```python
def test_evaluate_regime_records_and_no_change_when_not_risk_off():
    from signals import evaluate, DEFAULT_CONFIGS
    from market import synthetic_history
    df = synthetic_history("X.T", seed=3)
    base = evaluate(df, DEFAULT_CONFIGS, 2, -2)
    on = evaluate(df, DEFAULT_CONFIGS, 2, -2, regime="risk_on")
    assert on[2]["regime"] == "risk_on"
    assert (on[0], on[1]) == (base[0], base[1])
```

置換後:
```python
def test_evaluate_risk_on_records_regime_no_off_gate():
    """risk_on は regime を記録するが、risk_off ゲート（打ち手3）は発火しない。

    打ち手5 で risk_on はスコア重みを変える（別テストで担保）が、方向ペナルティ（ゲート）は
    risk_off 限定のまま＝重み付けとゲートは別軸で共存する。
    """
    from signals import evaluate, DEFAULT_CONFIGS
    from market import synthetic_history
    df = synthetic_history("X.T", seed=3)
    on = evaluate(df, DEFAULT_CONFIGS, 2, -2, regime="risk_on")
    assert on[2]["regime"] == "risk_on"        # ゲート側の記録（evaluate）
    assert on[2]["_regime"] == "risk_on"        # 重み側の記録（_score_indicators）
    assert "regime_filter" not in on[2]         # risk_off ゲートは発火しない
```

- [ ] **Step 2: 失敗する統合テストを書く（RED）**

`backend/test_signals.py` に追加（grouping テスト群の後ろ）:
```python
def test_evaluate_risk_on_amplifies_trend():
    """evaluate 経由で risk_on の trend×2 が反映される（バックテストにも自動反映される配線）。"""
    df = _idx(np.linspace(1000, 1300, 120))                     # 上昇 → trend買い
    base = evaluate(df, _base_configs(), 2, -2)                  # regime=None
    on = evaluate(df, _base_configs(), 2, -2, regime="risk_on")
    assert base[2]["_groups"]["trend"] == 1
    assert on[0] == base[0] + 1                                  # trend×2 で +1
    assert on[2]["_regime"] == "risk_on"


def test_evaluate_neutral_amplifies_contrarian():
    """evaluate 経由で neutral の contrarian×2 が反映される。"""
    df = _declining_df()                                         # 売られすぎ → 逆張り買い
    base = evaluate(df, _base_configs(), 2, -2)
    neu = evaluate(df, _base_configs(), 2, -2, regime="neutral")
    assert base[2]["_groups"]["contrarian"] == 1
    assert neu[0] == base[0] + 1                                 # contrarian×2 で +1
    assert neu[2]["_regime"] == "neutral"
```

注: `_base_configs()` は volume_filter/weekly_trend_filter/atr_exit を除くため、`evaluate` のスコアは `_score_indicators` の出力そのもの（後段フィルタなし）。risk_on/neutral はゲート対象外（ゲートは risk_off×buy のみ）なので、差分は重みのみ＝決定論。

- [ ] **Step 3: RED を確認**

Run: `backend/venv/bin/python -m pytest backend/test_signals.py -k "evaluate_risk_on_amplifies or evaluate_neutral_amplifies" -v`
Expected: FAIL（`evaluate` 未配線のため `on[0] == base[0]` で `base[0] + 1` に届かず AssertionError）

- [ ] **Step 4: `evaluate` を配線**

`backend/signals.py:392` を変更:

変更前:
```python
    score, detail = _score_indicators(df_ind, configs)
```
変更後:
```python
    score, detail = _score_indicators(df_ind, configs, regime)
```

`evaluate` docstring（383-388行）の `detail["_groups"]` 説明文の直後に1文追記:
```python
    打ち手5: グループ純額はレジーム別重み（risk_on/off=trend重視、neutral=contrarian重視）で
    合算され、detail["_regime"] に適用レジームが入る（detail["_groups"] は重み適用前の純額）。
```

- [ ] **Step 5: GREEN を確認（統合＋置換テスト）**

Run: `backend/venv/bin/python -m pytest backend/test_signals.py -k "evaluate_risk_on or evaluate_neutral or regime_off_penalizes" -v`
Expected: 全 PASS（amplify 2件＋`test_evaluate_risk_on_records_regime_no_off_gate`＋`test_evaluate_regime_off_penalizes_a_buy_signal`）

- [ ] **Step 6: 全 backend スイートが緑**

Run: `backend/venv/bin/python -m pytest backend/ -q`
Expected: `90 passed`（88 − 置換で消えた1件 + 統合2件 + 置換後1件 = 90）。配線で `backtest.py`/`main.py` 経路も重み付きになるが、それらのテスト（`test_backtest.py` の regime 配線spy等）は `evaluate` シグネチャ不変のため通る。

- [ ] **Step 7: コミット**

```bash
git add backend/signals.py backend/test_signals.py
git commit -m "feat: evaluate をレジーム別グループ重みに配線（バックテスト/板に自動反映・打ち手5）"
```

---

## 完了後（このプラン外・別スキルで実施）

- **最終レビュー:** superpowers:requesting-code-review で差分をレビュー（設計書 §10 成功基準との突合：レジームで順張り/逆張りが切替・`regime=None` は打ち手4一致・`detail["_groups"]`＋`_regime` で内訳可視・backend 全件緑）。
- **マージ:** superpowers:finishing-a-development-branch で main へ fast-forward マージ＋`git push origin main`。
- **メモリ更新:** `roadmap-progress.md` を「打ち手5 完了・main マージ済み」に更新し、Task 1 で解消した既存flake（環境ドリフト）も記録。次候補は打ち手6（連続確信度＋Top N）。

## スコープ外（将来）

重み値の `/optimize` 自動チューニング、打ち手6、フロントでの地合い別重み表示、SELL側の非対称重み（設計書 §11）。
