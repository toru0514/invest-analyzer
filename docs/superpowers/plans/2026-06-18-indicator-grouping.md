# 相関指標のグループ化（打ち手4）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `_score_indicators` を「指標→グループ別合計→グループ内クリップ(±1)→合算」に作り替え、逆張り系5指標の多重カウントを解消する。

**Architecture:** `signals.py` に `INDICATOR_GROUP`/`GROUP_CAP` 定数を追加し、`_score_indicators` の各指標ブランチを「`score += w` 直加算」から「グループ別 raw に積む」に変更。末尾で各グループを clip して合算。`evaluate` 以降・バックテスト・エンドポイントは無改修で新スコアに追従（検証=提示）。

**Tech Stack:** Python 3.13 / pandas / pandas-ta / pytest。ネット非依存（合成データ）。

**設計書:** `docs/superpowers/specs/2026-06-18-indicator-grouping-design.md`

---

## File Structure

| ファイル | 区分 | 責務 |
|---|---|---|
| `backend/signals.py` | 改修 | `INDICATOR_GROUP`/`GROUP_CAP` 追加、`_score_indicators` をグループ化＋clip に |
| `backend/test_signals.py` | 改修 | グループ化（clip・多重カウント解消・`_groups`）のテスト追加、壊れた既存テストのみ最小更新 |

単一の集中的リファクタなので1タスク。TDD（失敗テスト→失敗確認→実装→成功確認→コミット）。

---

## Task 1: `_score_indicators` をグループ化＋クリップに

**Files:**
- Modify: `backend/signals.py`（`_score_indicators` ~237-367・直前に定数追加）
- Test: `backend/test_signals.py`

- [ ] **Step 1: 失敗テストを追記** — `backend/test_signals.py`
  - import 確認：`numpy as np`・`pandas as pd`・`import signals` はトップにあるが、`evaluate`/`DEFAULT_CONFIGS` は**関数内ローカル import のみ**（モジュールトップに無い）。新テストは bare で使うので、**トップレベルに `from signals import evaluate, DEFAULT_CONFIGS` を追加**する（既存のローカル import はそのままで可）。

```python
def _declining_df(n=80):
    """単調減少（売られすぎ）で逆張り指標が一斉に買い側へ振れる合成データ。"""
    close = np.linspace(1500.0, 900.0, n)
    open_ = close + 2
    high = np.maximum(open_, close) + 3
    low = np.minimum(open_, close) - 3
    vol = np.full(n, 2_000_000.0)
    idx = pd.bdate_range(end=pd.Timestamp("2026-06-01"), periods=n)
    return pd.DataFrame({"open": open_, "high": high, "low": low,
                         "close": close, "volume": vol}, index=idx)


def test_grouping_clips_high_weight_to_cap():
    """単一指標でも重み3なら raw=3 だが、グループ clip で +1 に収まる。"""
    cfgs = [{"rule_type": "rsi", "params": {"length": 14, "low": 30, "high": 70},
             "weight": 3, "enabled": 1}]
    score, direction, detail = evaluate(_declining_df(), cfgs, 2, -2)
    assert detail.get("rsi") == 3                  # 個別寄与は重み3のまま
    assert detail["_groups"]["contrarian"] == 1    # グループは cap=1 にクリップ
    assert score == 1


def test_grouping_caps_contrarian_multicount():
    """逆張り複数指標が同時に買い側へ発火しても contrarian は +1（多重カウント解消）。"""
    score, direction, detail = evaluate(_declining_df(), DEFAULT_CONFIGS, 2, -2)
    fired = [k for k in ("rsi", "bbands", "stoch", "disparity", "cci") if detail.get(k, 0) > 0]
    assert len(fired) >= 2, f"複数の逆張り指標が買い側に発火する前提: {fired} / {detail}"
    assert detail["_groups"]["contrarian"] == 1


def test_score_detail_has_groups_within_cap():
    _, _, detail = evaluate(_declining_df(), DEFAULT_CONFIGS, 2, -2)
    assert "_groups" in detail and isinstance(detail["_groups"], dict)
    for g, v in detail["_groups"].items():
        assert -1 <= v <= 1                        # 全グループが cap 内
```

- [ ] **Step 2: 失敗確認**

Run: `backend/venv/bin/python -m pytest backend/test_signals.py -k "grouping or groups" -v`
Expected: FAIL（`detail["_groups"]` が無い・contrarian が +1 を超える）

- [ ] **Step 3: 実装** — `backend/signals.py`

`_score_indicators` の直前に定数を追加：
```python
# 相関でグルーピング（打ち手4）。グループ内は合算→±GROUP_CAP にクリップして多重カウントを止める。
INDICATOR_GROUP = {
    "ma_cross": "trend", "macd": "trend",
    "rsi": "contrarian", "bbands": "contrarian", "stoch": "contrarian",
    "disparity": "contrarian", "cci": "contrarian",
    "obv": "volume",
    "candle_pattern": "pattern",
}
GROUP_CAP = 1   # グループ内の最大寄与（±）
```

`_score_indicators` を以下に置換（各ブランチは ±w 算出は不変。`score += w; detail[key] = v` を `_add(rt, key, v)` に統一し、末尾でグループ clip→合算）：
```python
def _score_indicators(df: pd.DataFrame, configs: list[dict[str, Any]]) -> tuple[int, dict]:
    """指標列が計算済みの DataFrame の最終行をスコアリング（状態ベース・グループ化）。

    各指標は ±weight を出すが、相関グループ（順張り/逆張り/需給/パターン）ごとに合算して
    ±GROUP_CAP にクリップしてから合算する。これにより逆張り系5指標の多重カウントを止める。
    すべて当日までのデータのみ参照（look-ahead bias なし）。
    """
    group_raw: dict[str, int] = {}
    detail: dict = {}

    def _add(rt: str, key: str, v: int):
        detail[key] = v
        g = INDICATOR_GROUP.get(rt, rt)
        group_raw[g] = group_raw.get(g, 0) + v

    for cfg in configs:
        if not cfg.get("enabled", 1):
            continue
        p = cfg.get("params") or {}
        if isinstance(p, str):
            p = json.loads(p or "{}")
        rt = cfg["rule_type"]
        w = int(cfg.get("weight", 1))

        if rt == "rsi":
            rsi = _val(df, f"RSI_{p.get('length', 14)}")
            if rsi is None:
                continue
            if rsi < p.get("low", 30):
                _add(rt, "rsi", +w)
            elif rsi > p.get("high", 70):
                _add(rt, "rsi", -w)

        elif rt == "ma_cross":
            short, long = p.get("short", 5), p.get("long", 25)
            if len(df) >= long:
                cs = _sma(df["close"], short).iloc[-1]
                cl = _sma(df["close"], long).iloc[-1]
                if not (pd.isna(cs) or pd.isna(cl)):
                    if cs > cl:
                        _add(rt, "ma_cross", +w)
                    elif cs < cl:
                        _add(rt, "ma_cross", -w)

        elif rt == "macd":
            f, s, sig = p.get("fast", 12), p.get("slow", 26), p.get("signal", 9)
            hist = _val(df, f"MACDh_{f}_{s}_{sig}", -1)
            if hist is None:
                continue
            if hist > 0:
                _add(rt, "macd", +w)
            elif hist < 0:
                _add(rt, "macd", -w)

        elif rt == "bbands":
            length, std = p.get("length", 20), p.get("std", 2.0)
            lower = f"BBL_{length}_{std}_{std}"
            upper = f"BBU_{length}_{std}_{std}"
            cur_close = _val(df, "close", -1)
            cur_lower = _val(df, lower, -1)
            cur_upper = _val(df, upper, -1)
            if None in (cur_close, cur_lower, cur_upper):
                continue
            if cur_close <= cur_lower:
                _add(rt, "bbands", +w)
            elif cur_close >= cur_upper:
                _add(rt, "bbands", -w)

        elif rt == "stoch":
            k, d = p.get("k", 14), p.get("d", 3)
            ck = _val(df, f"STOCHk_{k}_{d}_3", -1)
            if ck is None:
                continue
            if ck < p.get("low", 20):
                _add(rt, "stoch", +w)
            elif ck > p.get("high", 80):
                _add(rt, "stoch", -w)

        elif rt == "candle_pattern":
            if (_val(df, "CDL_3WHITESOLDIERS") or 0) > 0:
                _add(rt, "3whitesoldiers", +w)
            if (_val(df, "CDL_3BLACKCROWS") or 0) < 0:
                _add(rt, "3blackcrows", -w)
            eng = _val(df, "CDL_ENGULFING") or 0
            if eng > 0:
                _add(rt, "engulfing", +w)
            elif eng < 0:
                _add(rt, "engulfing", -w)

        elif rt == "disparity":
            ma_len = int(p.get("ma", 25))
            ma = _sma(df["close"], ma_len).iloc[-1]
            cur_close = _val(df, "close", -1)
            if pd.isna(ma) or ma == 0 or cur_close is None:
                continue
            disp = (cur_close - ma) / ma * 100
            if disp <= p.get("low", -7):
                _add(rt, "disparity", +w)
            elif disp >= p.get("high", 7):
                _add(rt, "disparity", -w)

        elif rt == "obv":
            obv, obv_sma = obv_vs_sma(df, int(p.get("sma", 20)))
            if obv is None:
                continue
            if obv > obv_sma:
                _add(rt, "obv", +w)
            elif obv < obv_sma:
                _add(rt, "obv", -w)

        elif rt == "cci":
            cci = cci_value(df, int(p.get("length", 20)))
            if cci is None:
                continue
            if cci <= p.get("low", -100):
                _add(rt, "cci", +w)
            elif cci >= p.get("high", 100):
                _add(rt, "cci", -w)

        elif rt == "price_target":
            continue   # スコア対象外（即通知の別経路）

    groups = {g: max(-GROUP_CAP, min(GROUP_CAP, raw)) for g, raw in group_raw.items()}
    detail["_groups"] = groups
    score = sum(groups.values())
    return score, detail
```

- [ ] **Step 4: 成功確認＋既存テストの実測更新**

Run: `backend/venv/bin/python -m pytest backend/test_signals.py -q`
- まず新規グループ化テストが PASS することを確認。
- **確定的に壊れる既存テスト1件**：`test_state_based_scoring_reaches_default_threshold`（`:61`）。`_base_configs()`＋既定±2・22日窓では、多重カウント解消でスコアが ±2 に届かず `trade_count == 0` になり `assert r["trade_count"] > 0` が失敗する（設計どおりの帰結）。**意図（グループ化後もスコアリングが約定可能な信号を生む）を保ったまま** ±1 へ修正する：
  ```python
  # グループ化でスコアレンジが ±4 に圧縮されたため、±1（1グループ一致）で約定が出ることを確認。
  # 実運用の閾値は /optimize(OOS) で決定する。
  r = run_backtest(hist, configs=_base_configs(), buy_threshold=1, sell_threshold=-1)
  assert r["trade_count"] > 0
  ```
  （アサーションを単に弱める／削除するのではなく、上記のように閾値を明示し意図を残すこと。）
- 他の既存テストは設計§6どおり**実行して実際に落ちたものだけ**最小更新（単一指標の `score == 1` 系は通る見込み・先回り編集はしない）。

Run（全体）: `backend/venv/bin/python -m pytest backend/ -q`
Expected: 全件 PASS（既存82のうち上記1件を更新＋新規3＝85前後）。

- [ ] **Step 5: コミット**

```bash
git add backend/signals.py backend/test_signals.py
git commit -m "feat: 相関指標をグループ化しグループ内クリップで多重カウント解消（課題1/打ち手4）"
```

---

## 完了の定義
- 逆張り群が**最大 ±1**に収まり、多重カウントが解消（テストで担保）。
- `detail["_groups"]` に4グループ（trend/contrarian/volume/pattern）の純額が出る。
- `evaluate` 以降・バックテスト・エンドポイントは無改修で新スコアに追従（検証=提示）。
- `backend/` テスト全件グリーン。

## スコープ外（将来）
- 打ち手5（レジーム別グループ重み）、打ち手6（連続確信度・ランキング）、フロントでのグループ内訳表示。

> ⚠️ スコア設計の健全化であり利益を保証しない。投資は自己責任の原則は不変。
