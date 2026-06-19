# 連続確信度スコア＋Top N ランキング（打ち手6）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 各指標の「強さ」を連続量で捕捉して銘柄ごとに 0–100 の量的確信度を算出し、作戦ボード上部に確信度降順の Top N（既定3）を切り出す。

**Architecture:** 案B（spec §3）。整数 `score`＝方向決定はそのまま維持し、連続確信度を**別チャネル**で加える。`_score_indicators` に連続強度の算出を**加法的に**足し（既存の votes/groups/score は不変）、`evaluate` の最終段で direction 整合の 0–100 確信度を `detail["confidence"]` に出す。DB `daily_plan.confidence` に永続化、`/plan` で露出、フロントの純関数 `selectTopN` で Top N を切り出す。検証インフラ（backtest/holdout/閾値/ゲート）は無傷。

**Tech Stack:** Python 3 / FastAPI / SQLite（backend）、Next.js / TypeScript / Vitest（frontend）。テスト: `backend/venv/bin/python -m pytest backend/ -q`（ネット非依存・現状90件グリーン）／`npm --prefix frontend test`（vitest）。

**Spec:** `docs/superpowers/specs/2026-06-19-continuous-confidence-topn-design.md`

---

## File Structure（決定の固定）

- `backend/signals.py` — 連続強度ヘルパ＋スケール定数、`_score_indicators` を拡張（`detail["_strengths"]`＝指標別連続強度、`detail["_strength_net"]`＝レジーム加重・正規化済みの符号付き集約 ∈[-1,1]）、`evaluate` に確信度合成（`detail["confidence"]`）。
- `backend/db.py` — `daily_plan.confidence REAL` ＋冪等マイグレーション、`upsert_plan` の列を追加、`DEFAULT_META["top_n"]="3"`。
- `backend/main.py` — `perform_refresh` で `confidence` を保存、`SettingsUpdate`/`get_settings`/`put_settings` に `top_n`。
- `frontend/src/lib/api.ts` — `PlanRow.confidence`、`AppSettings.top_n` 型追加。
- `frontend/src/lib/rows.ts` — `rankByConfidence` / `selectTopN`（純関数・テスト対象）。
- `frontend/src/app/plan/page.tsx` — Top N セクション＋確信度バッジ。
- テスト: `backend/test_signals.py`、`backend/test_api.py`、`frontend/src/lib/__tests__/rows.test.ts`。

## 設計上の不変条件（テストで守る性質）

1. **後方互換**: `score` は int のまま。`detail["_groups"]`・既存 direction・backtest/holdout の数値は不変。追加は detail のキーのみ。
2. **連続強度の単調性**: 同じ向きでより極端なほど |strength| が大きい（RSI 15 の強度 > RSI 29）。
3. **確信度の範囲と整合**: 常に `0 ≤ confidence ≤ 100`。buy は正の集約で高、sell は対称、neutral は 0。
4. **ゲート連動**: block で direction=neutral のとき confidence=0、penalty 発火で割引（GATE_DISCOUNT）。
5. **Top N**: confidence 降順、同点は |score| 降順→ticker 昇順の決定論的順序。`n<=0` は空、`n>件数` は全件。

---

## Task 1: 連続指標強度の算出（signals.py）

各指標の符号付き連続強度 `s ∈ [-1,1]`（+ = 買い寄り）を、純粋なマッピングヘルパで定義し、`_score_indicators` の既存ループ内（メトリクスが手元にある箇所）で `detail["_strengths"]` に加法的に格納する。既存の votes/groups/score は一切変更しない。

**Files:**
- Modify: `backend/signals.py`（定数ブロック ~245、`_score_indicators` ~264-393）
- Test: `backend/test_signals.py`

- [ ] **Step 1: 失敗するテストを書く（ヘルパの単調性・境界）**

`backend/test_signals.py` の末尾（`_declining_df` などの近く）に追加:

```python
# --- 打ち手6: 連続強度ヘルパ ---
def test_ramp_strength_monotonic_and_bounded():
    # RSI 型（floor=0, ceil=100, 中立帯[30,70]）。低いほど買い側に強い。
    s15 = signals._ramp_strength(15, 30, 70)
    s29 = signals._ramp_strength(29, 30, 70)
    assert 0 < s29 < s15 <= 1.0            # 売られすぎが深いほど強い・単調
    assert signals._ramp_strength(50, 30, 70) == 0.0   # 中立帯は 0
    assert -1.0 <= signals._ramp_strength(85, 30, 70) < 0   # 買われすぎは負
    assert signals._ramp_strength(0, 30, 70) == 1.0    # 下限で +1
    assert signals._ramp_strength(100, 30, 70) == -1.0  # 上限で -1


def test_beyond_strength_monotonic_and_clipped():
    # CCI/乖離率 型（無界・閾値超過分を span 正規化）
    assert signals._beyond_strength(-150, -100, 100, 100) == 0.5
    assert signals._beyond_strength(-300, -100, 100, 100) == 1.0   # span 超は 1 にクリップ
    assert signals._beyond_strength(0, -100, 100, 100) == 0.0
    assert signals._beyond_strength(150, -100, 100, 100) == -0.5


def test_tanh_strength_sign_and_bounded():
    assert signals._tanh_strength(5.0, 2.0) > 0
    assert signals._tanh_strength(-5.0, 2.0) < 0
    assert abs(signals._tanh_strength(1e9, 2.0)) <= 1.0
    assert signals._tanh_strength(0.0, 2.0) == 0.0
    assert signals._tanh_strength(1.0, 0.0) == 0.0    # scale=0 はゼロ（ゼロ除算回避）
```

- [ ] **Step 2: テスト失敗を確認**

Run: `backend/venv/bin/python -m pytest backend/test_signals.py -q -k "strength"`
Expected: FAIL（`AttributeError: module 'signals' has no attribute '_ramp_strength'`）

- [ ] **Step 3: 定数とヘルパを実装**

`backend/signals.py` の `GROUP_CAP = 1` の直後（~246 行目）に定数ブロックを追加:

```python
# --- 連続確信度（打ち手6）のスケール定数 ---
MACD_STRENGTH_ATR_K = 1.0      # MACDヒストを ATR 比で正規化する係数
MA_CROSS_STRENGTH_ATR_K = 2.0  # 短長MA乖離を ATR 比で正規化
OBV_STRENGTH_K = 1.0           # OBV-OBVSMA を |OBVSMA| 比で正規化
CCI_STRENGTH_SPAN = 100.0      # CCI 閾値超過分の正規化幅
DISPARITY_STRENGTH_SPAN = 7.0  # 乖離率 閾値超過分の正規化幅（%）
```

同じく `signals.py` のモジュール関数として（`_group_weight` の直後 ~262 行目あたり）ヘルパを追加:

```python
def _ramp_strength(value: float, low: float, high: float,
                   floor: float = 0.0, ceil: float = 100.0) -> float:
    """[floor,ceil] に収まるオシレーター（RSI/STOCH）用の符号付き強度 ∈[-1,1]。

    value<=low: 買い側 +、floor で +1（深いほど大）。value>=high: 売り側 −、ceil で −1。
    中立帯 [low,high] は 0。閾値と向きは既存 ±1 投票に整合させる。
    """
    if value <= low:
        return 0.0 if low <= floor else max(0.0, min(1.0, (low - value) / (low - floor)))
    if value >= high:
        return 0.0 if ceil <= high else -max(0.0, min(1.0, (value - high) / (ceil - high)))
    return 0.0


def _beyond_strength(value: float, low: float, high: float, span: float) -> float:
    """無界オシレーター（CCI/乖離率）用。閾値超過分を span で正規化して ∈[-1,1]。"""
    if span <= 0:
        return 0.0
    if value <= low:
        return min(1.0, (low - value) / span)
    if value >= high:
        return -min(1.0, (value - high) / span)
    return 0.0


def _tanh_strength(x: float, scale: float) -> float:
    """モメンタム系（MACD/MA乖離/OBV）用。x/scale を tanh で有界化（符号 = x の符号）。"""
    if scale <= 0:
        return 0.0
    return math.tanh(x / scale)
```

`signals.py` 冒頭に `import math` が無ければ追加（既存の import 群に合わせる）。

- [ ] **Step 4: テスト成功を確認**

Run: `backend/venv/bin/python -m pytest backend/test_signals.py -q -k "strength"`
Expected: PASS（3件）

- [ ] **Step 5: `_score_indicators` を拡張して `detail["_strengths"]` を埋める失敗テストを書く**

`backend/test_signals.py` に追加:

```python
def test_score_indicators_emits_signed_strengths():
    # 売られすぎ（_declining_df）→ 逆張り指標の強度は買い側（正）。トレンドは下降（負）。
    df_ind = signals.add_indicators(_declining_df())
    score, detail = signals._score_indicators(df_ind, _base_configs())
    st = detail["_strengths"]
    assert isinstance(st, dict) and st                      # 何か発火している
    assert st.get("rsi", 0) > 0                             # 売られすぎ → rsi 買い側
    assert all(-1.0 <= v <= 1.0 for v in st.values())       # 全て有界


def test_score_indicators_strengths_are_additive_only():
    # 後方互換: _strengths を足しても従来の score / _groups は不変。
    df_ind = signals.add_indicators(_declining_df())
    score, detail = signals._score_indicators(df_ind, _base_configs())
    assert score == sum(detail["_groups"].values())        # 既存の不変条件（打ち手4）
    assert isinstance(score, int)
```

- [ ] **Step 6: テスト失敗を確認**

Run: `backend/venv/bin/python -m pytest backend/test_signals.py -q -k "strengths"`
Expected: FAIL（`KeyError: '_strengths'`）

- [ ] **Step 7: `_score_indicators` のループ内で強度を算出して格納**

`backend/signals.py` の `_score_indicators` を次のように拡張する。`_add` の隣に強度格納ヘルパを足し、各 rule ブランチで（メトリクスが手元にある所で）強度を 1 行追加する。**既存の `_add(...)` 呼び出しと votes/groups ロジックは変更しない**。

`detail` 初期化の直後に:

```python
    strengths: dict[str, float] = {}

    def _str(key: str, s: float):
        strengths[key] = float(s)
```

各ブランチに以下を追加（メトリクス計算の直後・既存 `_add` の近く）:

- rsi（`if rsi is None: continue` の後）: `_str("rsi", _ramp_strength(rsi, p.get("low", 30), p.get("high", 70)))`
- stoch（`if ck is None: continue` の後）: `_str("stoch", _ramp_strength(ck, p.get("low", 20), p.get("high", 80)))`
- cci（`if cci is None: continue` の後）: `_str("cci", _beyond_strength(cci, p.get("low", -100), p.get("high", 100), CCI_STRENGTH_SPAN))`
- disparity（`disp = ...` の後）: `_str("disparity", _beyond_strength(disp, p.get("low", -7), p.get("high", 7), DISPARITY_STRENGTH_SPAN))`
- macd（`if hist is None: continue` の後）: 
  ```python
  _atr = atr_value(df)
  _str("macd", _tanh_strength(hist, MACD_STRENGTH_ATR_K * _atr) if _atr else 0.0)
  ```
- ma_cross（`cs`, `cl` 計算済みの `if not (pd.isna(cs) or pd.isna(cl)):` 内）:
  ```python
  _atr = atr_value(df)
  _str("ma_cross", _tanh_strength(cs - cl, MA_CROSS_STRENGTH_ATR_K * _atr) if _atr else 0.0)
  ```
- obv（`if obv is None: continue` の後）: `_str("obv", _tanh_strength(obv - obv_sma, OBV_STRENGTH_K * abs(obv_sma)) if obv_sma else 0.0)`
- bbands（`if None in (...)：continue` の後）: 
  ```python
  _band = (cur_upper - cur_lower)
  _str("bbands", _beyond_strength(cur_close, cur_lower, cur_upper, _band / 2) if _band > 0 else 0.0)
  ```
- candle_pattern（離散・各 `_add` の隣に符号で）:
  ```python
  # 3whitesoldiers→+1, 3blackcrows→-1, engulfing→±1（vote と同符号）
  ```
  具体的には各 `_add(rt, "3whitesoldiers", +w)` の隣に `_str("3whitesoldiers", 1.0)`、`_add(rt, "3blackcrows", -w)` の隣に `_str("3blackcrows", -1.0)`、engulfing は `_str("engulfing", 1.0 if eng > 0 else -1.0)`。

関数末尾、`detail["_regime"] = regime` の直後に:

```python
    detail["_strengths"] = strengths
```

- [ ] **Step 8: テスト成功＋全 backend グリーンを確認**

Run: `backend/venv/bin/python -m pytest backend/test_signals.py -q`
Expected: PASS（既存＋新規。`_strengths`/`strength` 系が緑、既存の grouping/regime テストも緑）

- [ ] **Step 9: Commit**

```bash
git add backend/signals.py backend/test_signals.py
git commit -m "feat: 連続指標強度を算出して detail[_strengths] に格納（打ち手6・確信度の素地）

各指標の符号付き連続強度 s∈[-1,1] を _ramp/_beyond/_tanh ヘルパで定義し、
_score_indicators の既存ループ内に加法的に格納。votes/groups/score は不変（後方互換）。

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: 確信度の合成と `evaluate` 配線（signals.py）

`_score_indicators` でレジーム加重・正規化した符号付き集約 `detail["_strength_net"] ∈[-1,1]` を作り、`evaluate` の最終段（direction とゲート確定後）で 0–100 の `detail["confidence"]` を算出する。倍率は `detail` のキーを後から検査して決める（spec §4.3：整数経路と独立＝二重計算回避）。

**Files:**
- Modify: `backend/signals.py`（`_score_indicators` 末尾・`evaluate` 末尾）
- Test: `backend/test_signals.py`

- [ ] **Step 1: 失敗するテスト（集約 _strength_net）を書く**

```python
def test_strength_net_normalized_and_signed():
    df_ind = signals.add_indicators(_declining_df())          # 売られすぎ → 買い側
    _, detail = signals._score_indicators(df_ind, _base_configs())
    a = detail["_strength_net"]
    assert -1.0 <= a <= 1.0
    assert a > 0                                              # 買い側に符号

    df_up = signals.add_indicators(_idx(np.linspace(1000, 1300, 120)))  # 上昇 → trend 買い
    _, d_up = signals._score_indicators(df_up, _base_configs())
    assert d_up["_strength_net"] > 0
```

- [ ] **Step 2: 失敗を確認**

Run: `backend/venv/bin/python -m pytest backend/test_signals.py -q -k "strength_net"`
Expected: FAIL（`KeyError: '_strength_net'`）

- [ ] **Step 3: `_score_indicators` 末尾に集約を実装**

`detail["_strengths"] = strengths` の直後に追加:

```python
    # 連続強度をグループ集約 → ±GROUP_CAP クリップ → レジーム加重 → 最大重みで正規化（∈[-1,1]）
    sgroup_raw: dict[str, float] = {}
    for key, s in strengths.items():
        g = INDICATOR_GROUP.get(key, key)
        sgroup_raw[g] = sgroup_raw.get(g, 0.0) + s
    sgroups = {g: max(-GROUP_CAP, min(GROUP_CAP, raw)) for g, raw in sgroup_raw.items()}
    _CONF_GROUPS = ("trend", "contrarian", "volume", "pattern")
    wmax = sum(_group_weight(regime, g) * GROUP_CAP for g in _CONF_GROUPS)
    anum = sum(_group_weight(regime, g) * sgroups.get(g, 0.0) for g in _CONF_GROUPS)
    detail["_strength_net"] = (anum / wmax) if wmax else 0.0
```

注: candle のサブキー（3whitesoldiers 等）は `INDICATOR_GROUP` に無いため `g=key` になり 4 グループ集約から漏れる。これを避けるため、強度格納時のグループ解決を vote と揃える。`INDICATOR_GROUP` は `candle_pattern→pattern` のみ持つので、`_str` のキーを **rule_type 基準のグループに寄せる**: candle は各サブキーを足さず、`pattern` グループ強度として 1 本化する。具体的には Task 1 Step 7 の candle 追加を「`strengths` には個別キー、ただし集約では pattern に寄せる」ため、ここで明示的に pattern を補正する:

```python
    # candle 系サブキーは pattern グループへ寄せる（vote と同じ扱い）
    for _ck in ("3whitesoldiers", "3blackcrows", "engulfing"):
        if _ck in strengths:
            sgroups["pattern"] = max(-GROUP_CAP, min(GROUP_CAP,
                                     sgroups.get("pattern", 0.0) + 0.0))  # 既に加算済みなら何もしない
```

（実装メモ: シンプルさのため、`g = INDICATOR_GROUP.get(key, "pattern" if key in ("3whitesoldiers","3blackcrows","engulfing") else key)` と集約ループ側で解決すれば上の補正は不要。集約ループの `g = ...` 行をこの式に置き換えること。）

- [ ] **Step 4: 成功を確認**

Run: `backend/venv/bin/python -m pytest backend/test_signals.py -q -k "strength_net"`
Expected: PASS

- [ ] **Step 5: 失敗するテスト（confidence 範囲・方向整合・後方互換）を書く**

```python
def test_evaluate_confidence_range_and_alignment():
    # 売られすぎ → buy 側、confidence は (0,100]
    df = _declining_df()
    score, direction, detail = evaluate(df, _base_configs(), 1, -1)
    assert direction == "buy"
    assert 0 < detail["confidence"] <= 100
    assert isinstance(score, int)                 # 後方互換: score は int のまま


def test_evaluate_confidence_zero_when_neutral():
    # 閾値を高くして neutral にすると confidence=0
    df = _declining_df()
    _, direction, detail = evaluate(df, _base_configs(), 99, -99)
    assert direction == "neutral"
    assert detail["confidence"] == 0


def test_evaluate_confidence_discounted_by_regime_gate():
    cfg = [c for c in DEFAULT_CONFIGS if c["rule_type"] in ("rsi", "market_regime")]
    base = evaluate(_declining_df(), cfg, 1, -1)                 # regime=None
    off = evaluate(_declining_df(), cfg, 1, -1, regime="risk_off")
    assert off[2].get("regime_filter") == -2                    # ゲート penalty 発火
    # ゲートで割り引かれる（同条件で confidence が下がる、または neutral 化で 0）
    assert off[2]["confidence"] <= base[2]["confidence"]
```

- [ ] **Step 6: 失敗を確認**

Run: `backend/venv/bin/python -m pytest backend/test_signals.py -q -k "confidence"`
Expected: FAIL（`KeyError: 'confidence'`）

- [ ] **Step 7: `evaluate` の最終段に confidence 合成を実装**

`backend/signals.py` の `evaluate`、`return score, direction, detail` の**直前**に追加:

```python
    # --- 連続確信度（打ち手6）: direction とゲート確定後に算出（detail を後検査して二重計算回避） ---
    a = float(detail.get("_strength_net", 0.0))
    if direction == "buy":
        conf = max(0.0, a) * 100.0
    elif direction == "sell":
        conf = max(0.0, -a) * 100.0
    else:
        conf = 0.0
    vol = detail.get("volume")
    if vol == "quiet":
        conf *= VOL_DISCOUNT
    elif isinstance(vol, (int, float)) and not isinstance(vol, bool) and vol != 0:
        conf = min(100.0, conf * VOL_BOOST)
    if isinstance(detail.get("regime_filter"), int):   # penalty 発火（block は direction=neutral 済み）
        conf *= GATE_DISCOUNT
    if isinstance(detail.get("weekly_filter"), int):
        conf *= GATE_DISCOUNT
    detail["confidence"] = round(max(0.0, min(100.0, conf)), 1)
```

`signals.py` の定数ブロック（Task 1 で追加した箇所）に倍率定数を追記:

```python
VOL_BOOST = 1.15       # 出来高サージ時の確信度ブースト
VOL_DISCOUNT = 0.7     # 出来高細り時（direction 生存時）の確信度ディスカウント
GATE_DISCOUNT = 0.6    # レジーム/週足ゲート penalty 時の確信度ディスカウント
```

- [ ] **Step 8: 成功＋全 backend グリーンを確認**

Run: `backend/venv/bin/python -m pytest backend/ -q`
Expected: PASS（既存90件＋新規。confidence/strength 系すべて緑、backtest/holdout/api 不変）

- [ ] **Step 9: Commit**

```bash
git add backend/signals.py backend/test_signals.py
git commit -m "feat: evaluate に 0-100 連続確信度を合成（detail[confidence]・打ち手6中核）

_score_indicators がレジーム加重・正規化した符号付き集約 _strength_net を作り、
evaluate 最終段で direction 整合の confidence を算出（vol/gate 倍率は detail 後検査）。
score(int)/direction/backtest は不変。

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: 確信度の永続化（db.py / main.py）

`daily_plan.confidence REAL` を追加し、`upsert_plan`/`perform_refresh` で保存、`/plan` 応答に露出する。

**Files:**
- Modify: `backend/db.py`（`_migrate_daily_plan` ~134、`upsert_plan` ~407、`daily_plan` スキーマ ~82）
- Modify: `backend/main.py`（`perform_refresh` の `upsert_plan` 呼び出し ~388）
- Test: `backend/test_api.py`

- [ ] **Step 1: 失敗するテストを書く**

`backend/test_api.py` の `test_plan_generate_and_get` の末尾に確信度のアサートを追加（既存テストを拡張）:

```python
    # 打ち手6: 量的確信度カラムが存在し、buy/sell 行は 0..100 の値を持つ（ai_confidence とは別物）
    for r in got["rows"]:
        assert "confidence" in r
        if r["direction"] in ("buy", "sell") and r["confidence"] is not None:
            assert 0 <= r["confidence"] <= 100
```

- [ ] **Step 2: 失敗を確認**

Run: `backend/venv/bin/python -m pytest backend/test_api.py -q -k "plan_generate"`
Expected: FAIL（`assert "confidence" in r` が False）

- [ ] **Step 3: スキーマ・マイグレーション・upsert を実装**

`backend/db.py` の `daily_plan` スキーマ（~93、`rationale TEXT` の後）に列を追加:

```sql
  confidence    REAL,
```

`_migrate_daily_plan`（~137）のタプルに `confidence` を追加（冪等 ALTER）:

```python
    for col, decl in (("ai_summary", "TEXT"), ("ai_confidence", "INTEGER"),
                      ("ai_risks", "TEXT"), ("confidence", "REAL")):
```

`upsert_plan`（~410）の `setdefault` ループに `confidence` を追加:

```python
    for k in ("ai_summary", "ai_confidence", "ai_risks", "confidence"):
        row.setdefault(k, None)
```

`upsert_plan` の INSERT 文に `confidence` を追加（列・VALUES・ON CONFLICT の3か所）:
- 列リスト: `... rationale, confidence, ai_summary, ai_confidence, ai_risks) `
- VALUES: `... :rationale, :confidence, :ai_summary, :ai_confidence, :ai_risks) `
- ON CONFLICT: `rationale=excluded.rationale, confidence=excluded.confidence, ai_summary=excluded.ai_summary, ...`

- [ ] **Step 4: `perform_refresh` で confidence を渡す**

`backend/main.py` の `db.upsert_plan({...})`（~388）の dict に追加:

```python
            "target_price": plan["target_price"], "rationale": plan["rationale"],
            "confidence": detail.get("confidence"),
```

- [ ] **Step 5: 成功を確認**

Run: `backend/venv/bin/python -m pytest backend/test_api.py -q -k "plan_generate"`
Expected: PASS

- [ ] **Step 6: 全 backend グリーンを確認**

Run: `backend/venv/bin/python -m pytest backend/ -q`
Expected: PASS（既存＋新規）

- [ ] **Step 7: Commit**

```bash
git add backend/db.py backend/main.py backend/test_api.py
git commit -m "feat: daily_plan.confidence を永続化し /plan で露出（打ち手6）

冪等マイグレーションで confidence REAL を追加、upsert_plan/perform_refresh で保存。
既存 ai_confidence（Gemini解説）とは別物の量的確信度。

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: `top_n` 設定（db.py / main.py）

Top N の N を `app_meta` の `top_n`（既定3）で持ち、`/settings` で取得・更新する。負値・非整数はフォールバック、0 は「今夜の推奨」非表示。

**Files:**
- Modify: `backend/db.py`（`DEFAULT_META` ~103）
- Modify: `backend/main.py`（`SettingsUpdate` ~134、`get_settings` ~143、`put_settings` ~156）
- Test: `backend/test_api.py`

- [ ] **Step 1: 失敗するテストを書く**

`backend/test_api.py` の `test_settings_get_and_update` に追加（既定値の確認と更新・クランプ）:

```python
    # 打ち手6: top_n（既定3）
    assert s["top_n"] == 3
    client.put("/settings", json={"top_n": 5})
    assert client.get("/settings").json()["top_n"] == 5
    client.put("/settings", json={"top_n": -2})            # 負値はフォールバック（既定3）
    assert client.get("/settings").json()["top_n"] == 3
    client.put("/settings", json={"top_n": 3})             # 後始末
```

- [ ] **Step 2: 失敗を確認**

Run: `backend/venv/bin/python -m pytest backend/test_api.py -q -k "settings"`
Expected: FAIL（`KeyError: 'top_n'`）

- [ ] **Step 3: 実装**

`backend/db.py` の `DEFAULT_META`（~110、末尾）に追加:

```python
    "top_n": "3",   # 作戦ボード「今夜の推奨」の表示件数
```

`backend/main.py` の `SettingsUpdate`（~140 末尾）に追加:

```python
    top_n: Optional[int] = None
```

`get_settings`（~152、return dict 末尾）に追加。負値・非整数は既定3にフォールバック:

```python
        "scheduler_skip_holidays": m.get("scheduler_skip_holidays", "1") == "1",
        "top_n": _safe_top_n(m.get("top_n", "3")),
```

`get_settings` の直前にヘルパを追加:

```python
def _safe_top_n(raw) -> int:
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return 3
```

`put_settings`（~169、`scheduler_skip_holidays` の後）に追加。保存時も負値をクランプ:

```python
    if payload.top_n is not None:
        db.set_meta("top_n", max(0, payload.top_n))
```

- [ ] **Step 4: 成功＋全 backend グリーンを確認**

Run: `backend/venv/bin/python -m pytest backend/ -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/db.py backend/main.py backend/test_api.py
git commit -m "feat: top_n 設定を追加（作戦ボード Top N の件数・既定3・打ち手6）

app_meta top_n を /settings で取得/更新。負値・非整数はフォールバック、0で非表示。

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: フロント Top N 切り出し＋確信度バッジ（api.ts / rows.ts / plan/page.tsx）

純関数 `selectTopN` で確信度降順 Top N を切り出し、作戦ボード上部に「今夜の推奨」セクションを出す。各カードに確信度バッジを足す。既存の全ウォッチ一覧は下に残す。

**Files:**
- Modify: `frontend/src/lib/api.ts`（`PlanRow` ~63、`AppSettings` ~45）
- Modify: `frontend/src/lib/rows.ts`（純関数追加）
- Modify: `frontend/src/app/plan/page.tsx`（Top N セクション・バッジ）
- Test: `frontend/src/lib/__tests__/rows.test.ts`

- [ ] **Step 1: 型を追加（先に型だけ・テスト基盤）**

`frontend/src/lib/api.ts` の `PlanRow`（~74、`rationale` の後）に追加:

```typescript
  confidence: number | null;
```

`AppSettings`（~51、末尾）に追加:

```typescript
  top_n: number;
```

- [ ] **Step 2: 失敗するテスト（selectTopN）を書く**

`frontend/src/lib/__tests__/rows.test.ts` に追加（既存 import に `selectTopN` を足す）:

```typescript
import { selectTopN } from "@/lib/rows";

const mk = (ticker: string, direction: any, score: number, confidence: number | null) =>
  ({ ticker, direction, score, confidence } as any);

describe("selectTopN", () => {
  it("confidence 降順で上位 N を返し neutral を除外する", () => {
    const rows = [
      mk("A.T", "buy", 2, 40),
      mk("B.T", "buy", 3, 80),
      mk("C.T", "neutral", 0, 99),  // neutral は対象外
      mk("D.T", "sell", -2, 60),
    ];
    const top = selectTopN(rows, 2);
    expect(top.map((r) => r.ticker)).toEqual(["B.T", "D.T"]);
  });

  it("同点は |score| 降順 → ticker 昇順で決定論的", () => {
    const rows = [
      mk("Z.T", "buy", 1, 70),
      mk("Y.T", "buy", 3, 70),
      mk("X.T", "buy", 3, 70),
    ];
    expect(selectTopN(rows, 3).map((r) => r.ticker)).toEqual(["X.T", "Y.T", "Z.T"]);
  });

  it("n<=0 は空、n>件数 は全件", () => {
    const rows = [mk("A.T", "buy", 2, 40), mk("B.T", "buy", 3, 80)];
    expect(selectTopN(rows, 0)).toEqual([]);
    expect(selectTopN(rows, 99).map((r) => r.ticker)).toEqual(["B.T", "A.T"]);
  });

  it("confidence=null は最下位扱い", () => {
    const rows = [mk("A.T", "buy", 2, null), mk("B.T", "buy", 1, 10)];
    expect(selectTopN(rows, 2).map((r) => r.ticker)).toEqual(["B.T", "A.T"]);
  });
});
```

- [ ] **Step 3: 失敗を確認**

Run: `npm --prefix frontend test -- rows`
Expected: FAIL（`selectTopN` 未 export）

- [ ] **Step 4: `rows.ts` に純関数を実装**

`frontend/src/lib/rows.ts` の末尾に追加（`Direction` は既存 import に含まれる）:

```typescript
// 作戦ボード Top N（打ち手6）。confidence 降順 → |score| 降順 → ticker 昇順の決定論順。
type Rankable = { ticker: string; direction: Direction; score: number; confidence: number | null };

export function rankByConfidence<T extends Rankable>(rows: T[]): T[] {
  return [...rows]
    .filter((r) => r.direction !== "neutral")
    .sort(
      (a, b) =>
        (b.confidence ?? -1) - (a.confidence ?? -1) ||
        Math.abs(b.score) - Math.abs(a.score) ||
        a.ticker.localeCompare(b.ticker),
    );
}

export function selectTopN<T extends Rankable>(rows: T[], n: number): T[] {
  return n <= 0 ? [] : rankByConfidence(rows).slice(0, n);
}
```

- [ ] **Step 5: 成功を確認**

Run: `npm --prefix frontend test -- rows`
Expected: PASS

- [ ] **Step 6: `plan/page.tsx` に Top N セクションと確信度バッジを配線**

`frontend/src/app/plan/page.tsx`:

1. import に追加: `import { selectTopN } from "@/lib/rows";`、`api` から `getSettings`。
2. state 追加: `const [topN, setTopN] = useState(3);`
3. `load()` の `Promise.all` に `api.getSettings()` を足し、`setTopN(settings.top_n)`。
4. `ordered` の算出後に Top N を計算:
   ```typescript
   const topPicks = selectTopN(rows, topN);
   ```
5. ウォッチ一覧 (`ordered.map(...)`) の**上**に「今夜の推奨」セクションを追加（topN>0 かつ topPicks.length>0 のとき）。各 `topPicks` を既存 `PlanCard` で描画（`watch` から name を引く）。見出し「今夜の推奨 Top {topPicks.length}」。
6. `PlanCard` の見出し行（~179、`スコア {row.score}` の隣）に確信度バッジを追加:
   ```tsx
   {row?.confidence != null && (
     <span className="rounded bg-blue-600 px-1.5 py-0.5 text-xs font-semibold text-white">
       確信度 {Math.round(row.confidence)}
     </span>
   )}
   ```

- [ ] **Step 7: フロント全テスト＋型チェック**

Run: `npm --prefix frontend test` および `npm --prefix frontend run build`（型エラーが無いこと）
Expected: PASS（rows/api/DirectionBadge 既存緑＋新規 selectTopN 緑、build 成功）

- [ ] **Step 8: Commit**

```bash
git add frontend/src/lib/api.ts frontend/src/lib/rows.ts frontend/src/app/plan/page.tsx frontend/src/lib/__tests__/rows.test.ts
git commit -m "feat: 作戦ボードに今夜の推奨 Top N と確信度バッジを追加（打ち手6・仕上げ）

selectTopN（confidence降順・決定論タイブレーク）で上位Nを上部に切り出し、
各カードに量的確信度バッジ。既存の全ウォッチ一覧は下に維持。

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## 最終確認（全タスク後）

- [ ] `backend/venv/bin/python -m pytest backend/ -q` 全グリーン（既存90件＋新規）
- [ ] `npm --prefix frontend test` 全グリーン、`npm --prefix frontend run build` 成功
- [ ] 既存 direction / backtest / holdout の数値が不変（後方互換テストで保証）
- [ ] `requesting-code-review` で最終レビュー → 指摘反映
- [ ] `finishing-a-development-branch` で main へ FF マージ＋`git push origin main`

## 留意点

- **score は int のまま**（`test_evaluate_returns_valid_direction` の `isinstance(score,int)` を壊さない）。確信度は `detail["confidence"]`（float）と DB `confidence REAL` の別チャネル。
- **名前衝突**: 量的確信度=`confidence`、Gemini解説=`ai_confidence`。フロント表示でも別バッジ。
- **RS（相対力）は打ち手7**。本実装は確信度構成に RS を含めない（`_strength_net` に後から volume/RS グループを足せる拡張点を残す）。
- 決定論データ（`_idx`/`_declining_df`）のみ使用しネット非依存を維持（[[signal-tests-prefer-deterministic-ohlc]]）。
