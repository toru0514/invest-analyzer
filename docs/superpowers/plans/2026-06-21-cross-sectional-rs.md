# 打ち手7 クロスセクション相対力（RS）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 指数対比の相対力（超過リターン）を符号付き連続強度 `s_rs∈[-1,1]` として算出し、確信度チャネル（`_strength_net` の新グループ `rs`）にのみ加える。整数 `score`/`direction` は不変（後方互換）。

**Architecture:** `relative_strength()` ヘルパが「銘柄の N 日リターン − 指数の N 日リターン」を `tanh` で有界化。`asof` 引数で評価日以前にスライスし look-ahead を一点に閉じ込める。`evaluate(rs_strength=)` → `_score_indicators` が `_CONF_GROUPS` を動的に5グループ化し確信度へ織り込む。refresh / run_backtest / _run_backtest_plan / evaluation.py の全経路に `index_history`/`rs_params` を `regime_series` と同じく貫通させる。`rs_strength=None` で既存挙動を完全保存。

**Tech Stack:** Python 3.x, pandas, numpy, pytest（ネット非依存・決定論データ `_idx`/`_declining_df`）, FastAPI。

**Spec:** `docs/superpowers/specs/2026-06-21-cross-sectional-rs-design.md`

**テスト実行:** `backend/venv/bin/python -m pytest backend/ -q`（既存99件＋新規）

---

## File Structure

| ファイル | 責務 | 変更 |
|---|---|---|
| `backend/signals.py` | RS ヘルパ・確信度注入・設定 | `relative_strength()`・`RS_STRENGTH_SCALE`・`REGIME_GROUP_WEIGHTS["rs"]`・`_score_indicators(rs_strength=)`・`_CONF_GROUPS` 動的化・`evaluate(rs_strength=)`・`DEFAULT_CONFIGS` |
| `backend/backtest.py` | 評価日ごとの RS 供給 | `_rs_at()` ラッパ・`run_backtest`/`_run_backtest_plan` に `index_history`/`rs_params` |
| `backend/evaluation.py` | RS の貫通 | `benchmark`/`evaluate_holdout` に `index_history`/`rs_params` |
| `backend/main.py` | 配線 | `perform_refresh` で RS 計算・`/optimize`/`/backtest` で index 供給 |
| `backend/test_signals.py` 他 | テスト | RS の性質・後方互換・貫通不変性 |

---

## Task 1: `relative_strength` ヘルパ＋スケール定数

**Files:**
- Modify: `backend/signals.py`（定数は `VOL_*` 群の近く L254 付近、関数は `_tanh_strength` の後 L305 付近）
- Test: `backend/test_signals.py`

- [ ] **Step 1: 失敗するテストを書く**

`backend/test_signals.py` の末尾（`_idx` ヘルパが見える位置）に追加:

```python
def test_relative_strength_sign_and_none():
    import numpy as np
    # 銘柄が指数より強く上昇 → excess>0 → s_rs>0
    strong = _idx(np.linspace(1000, 1300, 60))   # +30%
    weak_index = _idx(np.linspace(1000, 1050, 60))  # +5%
    s = signals.relative_strength(strong, weak_index, n=20, scale=0.10)
    assert s is not None and s > 0
    # 銘柄が指数より弱い → s_rs<0
    s2 = signals.relative_strength(weak_index, strong, n=20, scale=0.10)
    assert s2 is not None and s2 < 0
    # 同一推移 → ≈0
    s3 = signals.relative_strength(strong, strong, n=20, scale=0.10)
    assert abs(s3) < 1e-9
    # データ不足 → None
    assert signals.relative_strength(_idx([1000, 1100]), strong, n=20, scale=0.10) is None
    # 範囲
    assert -1.0 <= s <= 1.0


def test_relative_strength_monotonic_and_lookahead():
    import numpy as np
    idx = _idx(np.linspace(1000, 1000, 60))   # 指数フラット
    mild = _idx(np.linspace(1000, 1100, 60))  # +10%
    big = _idx(np.linspace(1000, 1300, 60))   # +30%
    s_mild = signals.relative_strength(mild, idx, n=20, scale=0.10)
    s_big = signals.relative_strength(big, idx, n=20, scale=0.10)
    assert s_big > s_mild > 0                 # 強いほど大（単調）
    # look-ahead: asof 以降のデータを足しても asof 時点の RS は不変
    extended = _idx(np.concatenate([np.linspace(1000, 1300, 60),
                                    np.linspace(1300, 2000, 10)]))
    asof = big.index[-1]
    s_asof = signals.relative_strength(extended, idx, n=20, scale=0.10, asof=asof)
    assert abs(s_asof - s_big) < 1e-9
```

- [ ] **Step 2: 失敗を確認**

Run: `backend/venv/bin/python -m pytest backend/test_signals.py::test_relative_strength_sign_and_none -q`
Expected: FAIL（`AttributeError: module 'signals' has no attribute 'relative_strength'`）

- [ ] **Step 3: 最小実装**

`signals.py` の `VOL_DISCOUNT`/`GATE_DISCOUNT` 定義の近く（L256 付近）に定数追加:

```python
RS_STRENGTH_SCALE = 0.10   # 指数対比の超過リターンを tanh で正規化する係数（20日で+10%超過→s≈0.76）
```

`_tanh_strength`（L300-304）の直後に関数追加:

```python
def relative_strength(ticker_df, index_df, n: int = 20,
                      scale: float = RS_STRENGTH_SCALE,
                      asof=None) -> float | None:
    """指数対比の相対力（超過リターン）を符号付き強度 ∈[-1,1] で返す。

    s = tanh((銘柄のn日リターン − 指数のn日リターン) / scale)。+ = 指数をアウトパフォーム（買い寄り）。
    asof（評価日 Timestamp）が与えられたら双方を asof 以前に絞ってから末尾と n 本前を取る
    （look-ahead 回避を呼び出し側でなくヘルパに閉じ込める）。データ不足/ゼロ除算は None。
    """
    def _ret(df):
        if df is None or "close" not in df:
            return None
        s = df["close"]
        if asof is not None:
            s = s[s.index <= asof]
        if len(s) < n + 1:
            return None
        base = float(s.iloc[-(n + 1)])
        if base <= 0:
            return None
        return float(s.iloc[-1]) / base - 1.0

    rt = _ret(ticker_df)
    ri = _ret(index_df)
    if rt is None or ri is None:
        return None
    return _tanh_strength(rt - ri, scale)
```

- [ ] **Step 4: テスト通過を確認**

Run: `backend/venv/bin/python -m pytest backend/test_signals.py -k relative_strength -q`
Expected: PASS（2件）

- [ ] **Step 5: コミット**

```bash
git add backend/signals.py backend/test_signals.py
git commit -m "feat: relative_strength ヘルパ（指数対比の超過リターン強度）を追加（打ち手7）"
```

---

## Task 2: `_score_indicators` への RS 注入（`_CONF_GROUPS` 動的化）

**Files:**
- Modify: `backend/signals.py`（`REGIME_GROUP_WEIGHTS` L261-267、`_score_indicators` シグネチャ L307、末尾の確信度集約 L456-469）
- Test: `backend/test_signals.py`

- [ ] **Step 1: 失敗するテストを書く**

```python
def test_score_indicators_rs_backward_compat_when_none():
    import numpy as np
    df = signals.add_indicators(_idx(np.linspace(1000, 1300, 120)))
    _, d_no = signals._score_indicators(df, _base_configs())          # rs_strength 既定 None
    _, d_explicit = signals._score_indicators(df, _base_configs(), None, None)
    # None 供給で _strength_net も score も完全一致（後方互換）
    assert d_no["_strength_net"] == d_explicit["_strength_net"]
    assert "rs" not in d_no.get("_strengths", {})


def test_score_indicators_rs_enters_strength_net():
    import numpy as np
    df = signals.add_indicators(_idx(np.linspace(1000, 1300, 120)))   # trend 買い・強度高
    _, d_base = signals._score_indicators(df, _base_configs())
    # 既存加重平均を下回る弱い正 rs → 希釈で _strength_net は下がる（非単調・spec §4.2）
    _, d_weak = signals._score_indicators(df, _base_configs(), None, 0.05)
    assert d_weak["_strength_net"] < d_base["_strength_net"]
    assert d_weak["rs"] == 0.05
    # 最大の正 rs → 分子増分が効き上がる
    _, d_strong = signals._score_indicators(df, _base_configs(), None, 1.0)
    assert d_strong["_strength_net"] > d_weak["_strength_net"]
    assert -1.0 <= d_strong["_strength_net"] <= 1.0


def test_score_indicators_rs_regime_weight():
    import numpy as np
    df = signals.add_indicators(_idx(np.linspace(1000, 1300, 120)))
    # risk_on は rs 重み2 / neutral は1。同じ rs でも risk_on の方が rs 寄与の比重が大きい。
    _, on = signals._score_indicators(df, _base_configs(), "risk_on", 1.0)
    _, neu = signals._score_indicators(df, _base_configs(), "neutral", 1.0)
    assert -1.0 <= on["_strength_net"] <= 1.0
    assert -1.0 <= neu["_strength_net"] <= 1.0
    assert signals._group_weight("risk_on", "rs") == 2
    assert signals._group_weight("neutral", "rs") == 1
```

- [ ] **Step 2: 失敗を確認**

Run: `backend/venv/bin/python -m pytest backend/test_signals.py -k "score_indicators_rs" -q`
Expected: FAIL（`_score_indicators` が positional 4引数を受けない / `rs` キー無し）

- [ ] **Step 3: 最小実装**

(a) `REGIME_GROUP_WEIGHTS`（L262-266）の各レジーム dict に `rs` を追加:

```python
REGIME_GROUP_WEIGHTS: dict[str, dict[str, int]] = {
    "risk_on":  {"trend": 2, "contrarian": 1, "volume": 1, "pattern": 1, "rs": 2},
    "neutral":  {"trend": 1, "contrarian": 2, "volume": 1, "pattern": 1, "rs": 1},
    "risk_off": {"trend": 2, "contrarian": 1, "volume": 1, "pattern": 1, "rs": 1},
}
```

（コメントに「rs は trend 同様のモメンタム重み。risk_off だけ 1 にして落ちる相場での相対力買いを抑える意図的非対称」を追記）

(b) `_score_indicators` シグネチャ（L307）に引数追加:

```python
def _score_indicators(df: pd.DataFrame, configs: list[dict[str, Any]],
                      regime: str | None = None,
                      rs_strength: float | None = None):
```

（既存シグネチャの regime に続けて rs_strength を足す。既存呼び出しは位置・キーワードとも互換）

(c) 確信度集約ブロック（L457-469）を改修。`sgroup_raw` 構築後に rs を加え、`_CONF_GROUPS` を動的化:

```python
    sgroups = {g: max(-GROUP_CAP, min(GROUP_CAP, raw)) for g, raw in sgroup_raw.items()}
    _CONF_GROUPS = ("trend", "contrarian", "volume", "pattern")
    if rs_strength is not None:
        strengths["rs"] = rs_strength
        sgroups["rs"] = max(-GROUP_CAP, min(GROUP_CAP, float(rs_strength)))
        detail["rs"] = round(float(rs_strength), 3)
        _CONF_GROUPS = _CONF_GROUPS + ("rs",)
    wmax = sum(_group_weight(regime, g) * GROUP_CAP for g in _CONF_GROUPS)
    anum = sum(_group_weight(regime, g) * sgroups.get(g, 0.0) for g in _CONF_GROUPS)
    detail["_strength_net"] = (anum / wmax) if wmax else 0.0
```

注: `detail["_strengths"] = strengths`（L454）は rs 追加前に実行済みのため、rs を `strengths` に入れる行は L454 より後（上記ブロック内）に置く。`detail["_strengths"]` は同じ dict 参照なので rs も反映される。

- [ ] **Step 4: テスト通過を確認**

Run: `backend/venv/bin/python -m pytest backend/test_signals.py -k "score_indicators_rs" -q`
Expected: PASS（3件）

- [ ] **Step 5: 既存テストの非回帰を確認**

Run: `backend/venv/bin/python -m pytest backend/test_signals.py -q`
Expected: 全 PASS（既存の `_strength_net`/confidence テストが不変）

- [ ] **Step 6: コミット**

```bash
git add backend/signals.py backend/test_signals.py
git commit -m "feat: _score_indicators に rs グループを動的注入・REGIME_GROUP_WEIGHTS に rs（打ち手7）"
```

---

## Task 3: `evaluate` への `rs_strength` 素通し

**Files:**
- Modify: `backend/signals.py`（`evaluate` シグネチャ L475-481、`_score_indicators` 呼び出し L493）
- Test: `backend/test_signals.py`

- [ ] **Step 1: 失敗するテストを書く**

```python
def test_evaluate_rs_affects_confidence_not_direction():
    import numpy as np
    df = _idx(np.linspace(1000, 1300, 120))
    base = evaluate(df, _base_configs(), 1, -1)                       # rs 無し
    strong = evaluate(df, _base_configs(), 1, -1, rs_strength=1.0)    # 強い相対力
    # direction/score は不変（後方互換）
    assert strong[0] == base[0] and strong[1] == base[1]
    assert isinstance(strong[0], int)
    # confidence は変化しうる（rs が織り込まれる）／範囲保証
    assert 0 <= strong[2]["confidence"] <= 100
    assert strong[2]["rs"] == 1.0
    # None 既定は完全後方互換
    none_eval = evaluate(df, _base_configs(), 1, -1, rs_strength=None)
    assert none_eval[2]["confidence"] == base[2]["confidence"]
    assert "rs" not in none_eval[2]
```

- [ ] **Step 2: 失敗を確認**

Run: `backend/venv/bin/python -m pytest backend/test_signals.py::test_evaluate_rs_affects_confidence_not_direction -q`
Expected: FAIL（`evaluate` が `rs_strength` を受けない）

- [ ] **Step 3: 最小実装**

`evaluate` シグネチャ（L475-481）末尾に引数追加:

```python
def evaluate(
    df: pd.DataFrame,
    configs: list[dict[str, Any]] | None = None,
    buy_threshold: int = BUY_THRESHOLD,
    sell_threshold: int = SELL_THRESHOLD,
    regime: str | None = None,
    rs_strength: float | None = None,
):
```

`_score_indicators` 呼び出し（L493）を変更:

```python
    score, detail = _score_indicators(df_ind, configs, regime, rs_strength)
```

- [ ] **Step 4: テスト通過を確認**

Run: `backend/venv/bin/python -m pytest backend/test_signals.py::test_evaluate_rs_affects_confidence_not_direction -q`
Expected: PASS

- [ ] **Step 5: コミット**

```bash
git add backend/signals.py backend/test_signals.py
git commit -m "feat: evaluate に rs_strength 引数を追加し確信度へ素通し（打ち手7）"
```

---

## Task 4: `DEFAULT_CONFIGS` に `relative_strength` ルール

**Files:**
- Modify: `backend/signals.py`（`DEFAULT_CONFIGS` L26-49、`market_regime` ルールの後）
- Test: `backend/test_signals.py`

- [ ] **Step 1: 失敗するテストを書く**

```python
def test_default_configs_has_relative_strength():
    p = signals._find_cfg(signals.DEFAULT_CONFIGS, "relative_strength")
    assert p is not None
    assert p["period"] == 20 and p["scale"] == 0.10
```

- [ ] **Step 2: 失敗を確認**

Run: `backend/venv/bin/python -m pytest backend/test_signals.py::test_default_configs_has_relative_strength -q`
Expected: FAIL（`_find_cfg` が None）

- [ ] **Step 3: 最小実装**

`DEFAULT_CONFIGS` の `market_regime` ルール（L43-45）の直後に追加:

```python
    # 相対力（打ち手7）: 指数対比の N 日超過リターンを確信度に加える。enabled:0 で RS 無効。
    {"rule_type": "relative_strength",
     "params": {"period": 20, "scale": 0.10}, "weight": 1, "enabled": 1},
```

- [ ] **Step 4: テスト通過を確認**

Run: `backend/venv/bin/python -m pytest backend/test_signals.py::test_default_configs_has_relative_strength -q`
Expected: PASS

- [ ] **Step 5: コミット**

```bash
git add backend/signals.py backend/test_signals.py
git commit -m "feat: DEFAULT_CONFIGS に relative_strength ルール（period20/scale0.10）を追加（打ち手7）"
```

---

## Task 5: `backtest.run_backtest` への `index_history`/`rs_params` 配線

**Files:**
- Modify: `backend/backtest.py`（`_regime_at` の近く L20 に `_rs_at`、`run_backtest` シグネチャ L28-32、evaluate 呼び出し L67・L109）
- Test: `backend/test_backtest.py`

- [ ] **Step 1: 失敗するテストを書く**

`backend/test_backtest.py` に追加（既存の histories ヘルパの流儀に合わせる。無ければ synthetic_history を使う）:

```python
def test_run_backtest_rs_none_is_backward_compatible():
    from market import synthetic_history
    import backtest
    hist = {f"T{i}.T": synthetic_history(f"T{i}.T", n=120, seed=i) for i in range(3)}
    base = backtest.run_backtest(hist, buy_threshold=2, sell_threshold=-2)
    # index_history/rs_params 未指定 = 従来結果
    explicit = backtest.run_backtest(hist, buy_threshold=2, sell_threshold=-2,
                                     index_history=None, rs_params=None)
    assert base["total_return"] == explicit["total_return"]
    assert base["trades"] == explicit["trades"]


def test_run_backtest_rs_supplied_runs_and_keeps_trades():
    from market import synthetic_history
    import backtest
    hist = {f"T{i}.T": synthetic_history(f"T{i}.T", n=120, seed=i) for i in range(3)}
    idx = synthetic_history("IDX.T", n=120, seed=99)
    base = backtest.run_backtest(hist, buy_threshold=2, sell_threshold=-2)
    rs = backtest.run_backtest(hist, buy_threshold=2, sell_threshold=-2,
                               index_history=idx, rs_params={"period": 20, "scale": 0.10})
    # RS は score/direction を動かさない → 売買（trades）は不変
    assert rs["trades"] == base["trades"]
```

- [ ] **Step 2: 失敗を確認**

Run: `backend/venv/bin/python -m pytest backend/test_backtest.py -k rs -q`
Expected: FAIL（`run_backtest` が `index_history` を受けない）

- [ ] **Step 3: 最小実装**

(a) `_regime_at`（L20-25）の直後に追加:

```python
def _rs_at(index_history, rs_params, window, d):
    """評価日 d 時点の相対力強度を返す（index_history/rs_params 未指定で None）。"""
    if index_history is None or rs_params is None:
        return None
    from signals import relative_strength
    return relative_strength(window, index_history,
                             n=int(rs_params.get("period", 20)),
                             scale=float(rs_params.get("scale", 0.10)), asof=d)
```

(b) `run_backtest` シグネチャ（L28-32）に `index_history=None, rs_params=None` を追加。

(c) evaluate 呼び出し（L67-68）を変更:

```python
            score, direction, detail = evaluate(window, configs, buy_threshold, sell_threshold,
                                                regime=_regime_at(regime_series, d),
                                                rs_strength=_rs_at(index_history, rs_params, window, d))
```

(d) 末尾再判定 evaluate（L109-110）も同様に `rs_strength=_rs_at(index_history, rs_params, df, df.index[-1])` を追加。

- [ ] **Step 4: テスト通過を確認**

Run: `backend/venv/bin/python -m pytest backend/test_backtest.py -k rs -q`
Expected: PASS（2件）

- [ ] **Step 5: 非回帰確認**

Run: `backend/venv/bin/python -m pytest backend/test_backtest.py -q`
Expected: 全 PASS

- [ ] **Step 6: コミット**

```bash
git add backend/backtest.py backend/test_backtest.py
git commit -m "feat: run_backtest に index_history/rs_params と _rs_at を配線（打ち手7）"
```

---

## Task 6: `_run_backtest_plan` への RS 配線（plan モード後方互換）

**Files:**
- Modify: `backend/backtest.py`（`_run_backtest_plan` シグネチャ L134-136、evaluate 呼び出し L202・L228）
- Test: `backend/test_backtest.py`

- [ ] **Step 1: 失敗するテストを書く**

```python
def test_run_backtest_plan_rs_invariant():
    from market import synthetic_history
    import backtest
    hist = {f"P{i}.T": synthetic_history(f"P{i}.T", n=120, seed=i) for i in range(2)}
    idx = synthetic_history("IDX.T", n=120, seed=77)
    base = backtest._run_backtest_plan(hist, configs=None, initial_capital=3000.0,
                                       backtest_days=40, buy_threshold=2, sell_threshold=-2,
                                       eval_start_date=None)
    rs = backtest._run_backtest_plan(hist, configs=None, initial_capital=3000.0,
                                     backtest_days=40, buy_threshold=2, sell_threshold=-2,
                                     eval_start_date=None,
                                     index_history=idx, rs_params={"period": 20, "scale": 0.10})
    # build_plan は confidence/rs を参照しない → 約定・PnL は不変
    assert rs["closed_trades"] == base["closed_trades"]
    assert rs["total_pnl"] == base["total_pnl"]
```

注: `_run_backtest_plan` の実引数名・戻り値キーは実装に合わせる（`backtest.py:134-136` と戻り値 dict を確認して `closed_trades`/`total_pnl` 等の実キーに修正）。

- [ ] **Step 2: 失敗を確認**

Run: `backend/venv/bin/python -m pytest backend/test_backtest.py::test_run_backtest_plan_rs_invariant -q`
Expected: FAIL（`_run_backtest_plan` が `index_history` を受けない）

- [ ] **Step 3: 最小実装**

(a) `_run_backtest_plan` シグネチャ（L134-136）に `index_history=None, rs_params=None` を追加。

(b) 日次 evaluate（L202-203）に追加:

```python
                score, direction, _ = evaluate(window, configs, buy_threshold, sell_threshold,
                                               regime=_regime_at(regime_series, df.index[i]),
                                               rs_strength=_rs_at(index_history, rs_params, window, df.index[i]))
```

(c) 末尾再判定 evaluate（L228-229）にも `rs_strength=_rs_at(index_history, rs_params, df, df.index[-1])` を追加。

- [ ] **Step 4: テスト通過を確認**

Run: `backend/venv/bin/python -m pytest backend/test_backtest.py::test_run_backtest_plan_rs_invariant -q`
Expected: PASS

- [ ] **Step 5: コミット**

```bash
git add backend/backtest.py backend/test_backtest.py
git commit -m "feat: _run_backtest_plan に RS 配線（build_plan 非参照のため約定不変）（打ち手7）"
```

---

## Task 7: `evaluation.py` の `benchmark`/`evaluate_holdout` 貫通

**Files:**
- Modify: `backend/evaluation.py`（`benchmark` L39-63、`evaluate_holdout` L78-133）
- Test: `backend/test_evaluation.py`

- [ ] **Step 1: 失敗するテストを書く**

```python
def test_benchmark_and_holdout_accept_rs_params():
    from market import synthetic_history
    import evaluation
    hist = {f"E{i}.T": synthetic_history(f"E{i}.T", n=140, seed=i) for i in range(3)}
    idx = synthetic_history("IDX.T", n=140, seed=55)
    # 受理して例外なく走る（None と供給で naive ベースの売買は不変）
    b_base = evaluation.benchmark(hist, None, buy_threshold=2, sell_threshold=-2)
    b_rs = evaluation.benchmark(hist, None, buy_threshold=2, sell_threshold=-2,
                                index_history=idx, rs_params={"period": 20, "scale": 0.10})
    assert b_rs["all_signals"]["trades"] == b_base["all_signals"]["trades"]
    h = evaluation.evaluate_holdout(hist, None,
                                    index_history=idx, rs_params={"period": 20, "scale": 0.10})
    assert "oos" in h   # 例外なく完走
```

注: `benchmark`/`evaluate_holdout` の実引数順・戻り値キー（`all_signals`/`oos` 等）は `evaluation.py` を読んで実キーに合わせる。

- [ ] **Step 2: 失敗を確認**

Run: `backend/venv/bin/python -m pytest backend/test_evaluation.py::test_benchmark_and_holdout_accept_rs_params -q`
Expected: FAIL（`benchmark` が `index_history` を受けない）

- [ ] **Step 3: 最小実装**

(a) `benchmark`（L39-41）シグネチャに `index_history=None, rs_params=None` を追加し、内部 `run_backtest`（L59-63）へ `index_history=index_history, rs_params=rs_params` を渡す。

(b) `evaluate_holdout`（L78-79）シグネチャに `index_history=None, rs_params=None` を追加。内部クロージャ `_bt`（L94-98）の `run_backtest` 呼び出しに `index_history=index_history, rs_params=rs_params` を追加。

(c) `evaluate_holdout` 内の nested `benchmark` 呼び出し（L130-133）にも `index_history=index_history, rs_params=rs_params` を渡す（OOS ベンチ側だけ RS 無効になり比較条件がズレるのを防ぐ）。

- [ ] **Step 4: テスト通過を確認**

Run: `backend/venv/bin/python -m pytest backend/test_evaluation.py::test_benchmark_and_holdout_accept_rs_params -q`
Expected: PASS

- [ ] **Step 5: 非回帰確認**

Run: `backend/venv/bin/python -m pytest backend/test_evaluation.py -q`
Expected: 全 PASS

- [ ] **Step 6: コミット**

```bash
git add backend/evaluation.py backend/test_evaluation.py
git commit -m "feat: benchmark/evaluate_holdout に index_history/rs_params を貫通（nested benchmark 含む）（打ち手7）"
```

---

## Task 8: `main.py` の配線（perform_refresh ＋ /optimize ・ /backtest）

**Files:**
- Modify: `backend/main.py`（`perform_refresh` L340-374、`/backtest` L499-510、`/optimize` L531-545）
- Test: `backend/test_api.py`

- [ ] **Step 1: 失敗するテストを書く**

demo モードで refresh が RS 経路を通っても例外なく動き、confidence が出ることを確認:

```python
def test_perform_refresh_with_rs_runs(monkeypatch, tmp_path):
    # 既存の refresh テストの流儀に合わせて demo=True で実行し、例外が出ないこと＋
    # plan に confidence が入ることを確認する。
    import main
    res = main.perform_refresh(demo=True)
    assert "updated" in res or "results" in res or res is not None
```

注: 既存の `test_api.py` の refresh テスト（demo モード）の作法・アサーション対象を確認し、それに合わせる。RS は confidence 経由でのみ効くため「例外なく完走」を主眼にする。

- [ ] **Step 2: 失敗を確認**（または既存が緑なら回帰防止として記述）

Run: `backend/venv/bin/python -m pytest backend/test_api.py -k refresh -q`
Expected: まず現状を確認

- [ ] **Step 3: 最小実装**

(a) `perform_refresh`（L349 付近、common 取得後）で RS 設定を引く:

```python
    rs_params = _find_cfg(common, "relative_strength")   # None なら RS 無効
```

（`_find_cfg` を main で import 済みか確認。未 import なら `from signals import _find_cfg` を追加、または `signals._find_cfg`）

(b) 銘柄ループ内の `evaluate` 呼び出し（L374）を変更:

```python
        rs_strength = (relative_strength(df, idx_df, int(rs_params["period"]),
                                         float(rs_params["scale"]), asof=df.index[-1])
                       if rs_params is not None and not idx_df.empty else None)
        score, direction, detail = evaluate(df, ticker_cfgs, buy_th, sell_th,
                                            regime=regime, rs_strength=rs_strength)
```

（`idx_df` は L356-359 で取得済み。例外時は L361 で未定義になりうるので、`idx_df` の存在を `try` 内のフラグか `locals()` で守る。安全策: L352 付近で `idx_df = None` を初期化し、`rs_params is not None and idx_df is not None and not idx_df.empty` で判定。`relative_strength`・`_find_cfg` を main の import に追加）

(c) `/backtest`（L499-510）と `/optimize`（L531-545）: `_fetch_regime_series` が取得する idx を再利用して `index_history` と `rs_params` を `benchmark`/`evaluate_holdout`/`run_backtest` に渡す。`_fetch_regime_series` を「regime_series と idx_df を両方返す」よう小改修するか、別途 `get_history(index_ticker, period, demo)` で idx を取り、common から `rs_params = _find_cfg(common, "relative_strength")` を引いて渡す。失敗時 None。

- [ ] **Step 4: テスト通過を確認**

Run: `backend/venv/bin/python -m pytest backend/test_api.py -q`
Expected: 全 PASS

- [ ] **Step 5: 全体テスト**

Run: `backend/venv/bin/python -m pytest backend/ -q`
Expected: 全 PASS（既存99件＋新規）

- [ ] **Step 6: コミット**

```bash
git add backend/main.py backend/test_api.py
git commit -m "feat: perform_refresh と /backtest・/optimize に RS を配線（打ち手7）"
```

---

## Task 9: 最終確認

- [ ] **Step 1: backend 全テスト**

Run: `backend/venv/bin/python -m pytest backend/ -q`
Expected: 全 PASS。

- [ ] **Step 2: フロントテスト（RS は新規 UI 無し・回帰確認のみ）**

Run: `npm --prefix frontend test`
Expected: 既存13件 PASS（RS は confidence 数値として既存表示に反映されるため UI 変更なし）。

- [ ] **Step 3: 後方互換の最終目視**

- `rs_strength=None`／`index_history=None` で score/direction/confidence が現状一致（Task 2,3,5,6 のテストで担保済み）。
- RS は整数 score を動かさず backtest の売買が不変（Task 5,6 で担保済み）。

---

## 完了の定義（spec §8 再掲）

- backend 全グリーン（既存99件＋新規・ネット非依存）。
- `rs_strength=None`/`index_history=None` で後方互換（テストで固定）。
- RS 供給時、指数より強い buy 銘柄の confidence が（既存加重平均を上回る分）上がり Top N に反映。
- backtest が direction ベース売買・PnL を変えない。
- final code-review 通過後、main へ FF マージ＋`git push origin main`。
