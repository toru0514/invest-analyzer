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
    # look-ahead: asof 以降にバーを足しても asof 時点で切れば値は不変。
    # 注意: _idx は終端日付を 2026-06-01 に固定するため、asof は「末尾」ではなく
    # ramp 終端の位置インデックスで取る（full と truncated で同じ評価日に揃える）。
    full = _idx(np.concatenate([np.linspace(1000, 1300, 60),
                                np.linspace(1300, 2000, 10)]))   # 70点・後半は未来側の急騰
    idxf = _idx(np.full(70, 1000.0))
    asof = full.index[59]                                        # 60本目（ramp 終端）を評価日に固定
    s_asof = signals.relative_strength(full, idxf, n=20, scale=0.10, asof=asof)
    s_trunc = signals.relative_strength(full.iloc[:60], idxf.iloc[:60], n=20, scale=0.10)
    assert abs(s_asof - s_trunc) < 1e-9                          # 未来 10 本は asof で除外され不変
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
    # index_history/rs_params 未指定 = 従来結果（実戻り値キーで固定）
    explicit = backtest.run_backtest(hist, buy_threshold=2, sell_threshold=-2,
                                     index_history=None, rs_params=None)
    assert base["pnl_pct"] == explicit["pnl_pct"]
    assert base["trade_count"] == explicit["trade_count"]


def test_run_backtest_rs_supplied_runs_and_keeps_trades():
    from market import synthetic_history
    import backtest
    hist = {f"T{i}.T": synthetic_history(f"T{i}.T", n=120, seed=i) for i in range(3)}
    idx = synthetic_history("IDX.T", n=120, seed=99)
    base = backtest.run_backtest(hist, buy_threshold=2, sell_threshold=-2)
    rs = backtest.run_backtest(hist, buy_threshold=2, sell_threshold=-2,
                               index_history=idx, rs_params={"period": 20, "scale": 0.10})
    # RS は score/direction を動かさない（score モード）→ 売買・PnL は不変
    assert rs["trade_count"] == base["trade_count"]
    assert rs["pnl_pct"] == base["pnl_pct"]
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

(b) `run_backtest` シグネチャ（L28-32）に `index_history=None, rs_params=None` を追加（`regime_series=None` の後）。

(c) score モードの evaluate 呼び出し（L67-68）を変更:

```python
            score, direction, detail = evaluate(window, configs, buy_threshold, sell_threshold,
                                                regime=_regime_at(regime_series, d),
                                                rs_strength=_rs_at(index_history, rs_params, window, d))
```

(d) 末尾再判定 evaluate（L109-110）も同様に `rs_strength=_rs_at(index_history, rs_params, df, df.index[-1])` を追加。

注（重要・Task 6 で対応）: `run_backtest` は `exit_mode in ("plan","atr")` のとき L43-45 で `_run_backtest_plan` を**位置引数で**ディスパッチする。plan モードへの RS 貫通は `_run_backtest_plan` が引数を受ける Task 6 でこのディスパッチ呼び出しごと更新する。本タスクは score モードのみ（テストも score モード）なので、ここではディスパッチ呼び出しは変更しない。

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

public API（`run_backtest(exit_mode="plan")`）経由で叩く。これでディスパッチ（L43-45）への RS 貫通も同時に検証できる（直接 `_run_backtest_plan` を呼ぶと必須位置引数 `warmup_days`/`cost` を取りこぼすため避ける）:

```python
def test_run_backtest_plan_rs_invariant():
    from market import synthetic_history
    import backtest
    hist = {f"P{i}.T": synthetic_history(f"P{i}.T", n=120, seed=i) for i in range(2)}
    idx = synthetic_history("IDX.T", n=120, seed=77)
    base = backtest.run_backtest(hist, configs=None, exit_mode="plan", backtest_days=40,
                                 buy_threshold=2, sell_threshold=-2)
    rs = backtest.run_backtest(hist, configs=None, exit_mode="plan", backtest_days=40,
                               buy_threshold=2, sell_threshold=-2,
                               index_history=idx, rs_params={"period": 20, "scale": 0.10})
    # build_plan は confidence/rs を参照しない → 約定・PnL は不変（実戻り値キー）
    assert rs["closed_trades"] == base["closed_trades"]
    assert rs["pnl_amount"] == base["pnl_amount"]
```

- [ ] **Step 2: 失敗を確認**

Run: `backend/venv/bin/python -m pytest backend/test_backtest.py::test_run_backtest_plan_rs_invariant -q`
Expected: FAIL（`_run_backtest_plan` が `index_history` を受けない）

- [ ] **Step 3: 最小実装**

(a) `_run_backtest_plan` シグネチャ（L134-136）の末尾 `regime_series=None` の後に `index_history=None, rs_params=None` を追加。

(a2) **ディスパッチ貫通（Blocker・Task 5 で保留した箇所）**: `run_backtest` 内の `_run_backtest_plan` 呼び出し（L43-45）にキーワードで渡す:

```python
    if exit_mode in ("plan", "atr"):
        return _run_backtest_plan(histories, configs, initial_capital, backtest_days,
                                  warmup_days, buy_threshold, sell_threshold, cost,
                                  eval_start_date, regime_series,
                                  index_history=index_history, rs_params=rs_params)
```

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

`benchmark` は `*` 以降キーワード必須で `initial_capital`/`warmup_days`/`backtest_days` がデフォルト無し＝必須。`evaluate_holdout` は `backtest_days` を取らない（内部で `big` を算出）。戻り値キーは `all_signals_pct`（float）・`out_of_sample`（dict）。実 API に厳密に合わせる:

```python
def test_benchmark_and_holdout_accept_rs_params():
    from market import synthetic_history
    import evaluation
    hist = {f"E{i}.T": synthetic_history(f"E{i}.T", n=140, seed=i) for i in range(3)}
    idx = synthetic_history("IDX.T", n=140, seed=55)
    # benchmark: None と供給で all_signals_pct（score モード）は不変
    b_base = evaluation.benchmark(hist, None, buy_threshold=2, sell_threshold=-2,
                                  initial_capital=3000.0, warmup_days=35, backtest_days=80)
    b_rs = evaluation.benchmark(hist, None, buy_threshold=2, sell_threshold=-2,
                                initial_capital=3000.0, warmup_days=35, backtest_days=80,
                                index_history=idx, rs_params={"period": 20, "scale": 0.10})
    assert b_rs["all_signals_pct"] == b_base["all_signals_pct"]
    # evaluate_holdout: 例外なく完走（out_of_sample を含む）
    h = evaluation.evaluate_holdout(hist, None, initial_capital=3000.0, warmup_days=35,
                                    index_history=idx, rs_params={"period": 20, "scale": 0.10})
    assert "out_of_sample" in h
```

- [ ] **Step 2: 失敗を確認**

Run: `backend/venv/bin/python -m pytest backend/test_evaluation.py::test_benchmark_and_holdout_accept_rs_params -q`
Expected: FAIL（`benchmark` が `index_history` を受けない）

- [ ] **Step 3: 最小実装**

(a) `benchmark`（L39-41）の**キーワード専用引数群（`*` の後）**に `index_history=None, rs_params=None` を追加し、内部 `run_backtest`（L59-63）へ `index_history=index_history, rs_params=rs_params` を渡す。

(b) `evaluate_holdout`（L78-79）の**キーワード専用引数群（`*` の後・`regime_series=None` の隣）**に `index_history=None, rs_params=None` を追加（※ `evaluate_holdout` は `backtest_days` を取らない）。内部クロージャ `_bt`（L94-98）の `run_backtest` 呼び出しに `index_history=index_history, rs_params=rs_params` を追加。

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

既存 refresh テスト（`test_api.py:131` の `test_refresh_signals_and_prices`）と同じ `client` フィクスチャ経由で叩く（DB が seed 済み・demo の `^N225` synthetic で idx_df が入り RS 経路を通る）。RS は confidence 経由でのみ効くため「例外なく完走し plan に confidence が出る」を主眼にする:

```python
def test_refresh_runs_with_relative_strength(client):
    r = client.post("/refresh?demo=true")
    assert r.status_code == 200
    body = r.json()
    assert len(body["updated"]) == 4                 # RS 経路を通っても従来どおり完走
    for row in body["updated"]:
        assert row["direction"] in ("buy", "sell", "neutral")
    # plan に量的 confidence が露出（RS が織り込まれた値・float か None）
    plan = client.get("/plan").json()
    assert all(("confidence" in row) for row in plan)
```

注: `/plan` の戻り行に `confidence` キーがあることは打ち手6 で担保済み。RS 供給で値が変わっても直接の数値固定はしない（demo データ依存のため脆くなる）。

- [ ] **Step 2: 失敗を確認**（または既存が緑なら回帰防止として記述）

Run: `backend/venv/bin/python -m pytest backend/test_api.py -k refresh -q`
Expected: まず現状を確認

- [ ] **Step 3: 最小実装**

(a) **import 追加**: main.py 冒頭の `from signals import (...)`（L29-34 付近）に `relative_strength` を、`_find_cfg` も（未 import なら）追加。

(b) `perform_refresh`（L349 付近、common 取得後）で RS 設定を引く。`idx_df` は L352 付近で `idx_df = None` に初期化されている前提（例外時の未定義を防ぐ。未初期化なら初期化行も足す）:

```python
    rs_params = _find_cfg(common, "relative_strength")   # None なら RS 無効
```

(c) 銘柄ループ内の `evaluate` 呼び出し（L374）を変更:

```python
        rs_strength = (relative_strength(df, idx_df, int(rs_params["period"]),
                                         float(rs_params["scale"]), asof=df.index[-1])
                       if rs_params is not None and idx_df is not None and not idx_df.empty else None)
        score, direction, detail = evaluate(df, ticker_cfgs, buy_th, sell_th,
                                            regime=regime, rs_strength=rs_strength)
```

(d) **`_fetch_regime_series` を `(series, idx_df)` タプル返却に改修**（本命・Major）: 現状 `regime_series` のみ返す（`main.py:51-60`）。idx_df も返すよう変更し、既存呼び出し2箇所（`/backtest` の L499 付近・`/optimize` の L543 付近）を `rs, idx_df = _fetch_regime_series(...)` に更新。各経路で `rs_params = _find_cfg(common, "relative_strength")` を引き、`benchmark`/`evaluate_holdout`/`run_backtest` 呼び出しに `index_history=idx_df, rs_params=rs_params` を追加。idx 取得失敗時は `idx_df=None`（RS 無効・後方互換）。

注: 行番号は目安。実装時はシンボル（`perform_refresh`/`_fetch_regime_series`/`benchmark`/`evaluate_holdout` 呼び出し）で位置決めする（`/backtest`≈L476-515・`/optimize`≈L529-548）。

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
