# レジーム別グループ重み（打ち手5）設計書

- 日付: 2026-06-19
- 対象ブランチ: `feature/regime-group-weights`
- 対応課題: `課題.md` 課題1・課題2／打ち手5（レジーム別に順張り/逆張りの重みを切替）
- 前提: 打ち手3（地合いレジーム）・打ち手4（相関グループ化）完了済み。検証=提示を維持する。
- ステータス: 設計（実装前）

---

## 1. 背景と問題

打ち手4で指標を4グループ（順張り/逆張り/需給/パターン）に集約し、各グループは ±1 にクリップして**等重み**で合算している（`score = Σ clip(group, ±1)`）。しかし `課題.md` の指摘どおり、**相場つきによって効く指標群は異なる**：

> トレンド相場では押し目を待ち、オシレーターの「買われすぎ」は無視する。レンジ相場では逆張りに従う。今の設計はそれが原理的にできない。

打ち手3で地合いレジーム（risk_on/neutral/risk_off）は算出され `evaluate` に渡っているが、**スコア合成は地合いに依らず一定**。これを地合い依存にする。

## 2. ゴール / 非ゴール

### ゴール（本設計＝打ち手5のみ）
1. グループ合算を**レジーム依存の重み**付きにする：`score = Σ weight(regime, group) × clip(group, ±1)`。
2. トレンド時は順張り群を主、レンジ時は逆張り群を主に切り替える。
3. `evaluate` が持つ `regime` を `_score_indicators` に渡す（**新規配線は最小**：バックテスト/エンドポイントは打ち手3で既に regime を `evaluate` へ流済み）。
4. 検証=提示を維持：重み付きスコアはバックテスト（`/optimize` OOS）にも自動反映。

### 非ゴール（将来）
- 重み値そのものの自動チューニング（`/optimize` の探索軸に重みを加える）。v1 は固定の妥当値。
- 連続確信度スコア・Top N ランキング（打ち手6）。
- フロントでの地合い別重みの表示。

## 3. 確定した設計判断

| 項目 | 決定 |
|---|---|
| 重み | レジーム×グループの整数重み表（スコアは int 維持） |
| 既定値 | risk_on/risk_off は trend=2、neutral は contrarian=2、他は 1。`regime=None` は全1（＝打ち手4の挙動） |
| 配線 | `evaluate` → `_score_indicators(df, configs, regime)`。バックテスト/エンドポイントは無改修 |
| 後方互換 | `regime=None` で全重み1＝打ち手4と同一。既存テスト不変 |

### 既定重み表
```
REGIME_GROUP_WEIGHTS = {
    "risk_on":  {"trend": 2, "contrarian": 1, "volume": 1, "pattern": 1},
    "neutral":  {"trend": 1, "contrarian": 2, "volume": 1, "pattern": 1},
    "risk_off": {"trend": 2, "contrarian": 1, "volume": 1, "pattern": 1},
}
# regime が None / 未知 → 全グループ 1（重みなし＝打ち手4）
```

## 4. コンポーネント詳細（`signals.py`）

### 4.1 重み定数
上記 `REGIME_GROUP_WEIGHTS` をモジュール定数として追加。

### 4.2 `_score_indicators(df, configs, regime=None)`
- グループ別 raw 合計→`clip(±GROUP_CAP)` までは打ち手4のまま。
- 末尾の合算のみ変更：
```python
def _group_weight(regime, group: str) -> int:
    return REGIME_GROUP_WEIGHTS.get(regime, {}).get(group, 1)

# 旧: score = sum(groups.values())
groups = {g: max(-GROUP_CAP, min(GROUP_CAP, raw)) for g, raw in group_raw.items()}
detail["_groups"] = groups                       # クリップ後（重み前）の純額（透明性）
score = sum(_group_weight(regime, g) * v for g, v in groups.items())
```
- `detail["_groups"]` は**重み適用前**のグループ純額を保持（解釈性）。`detail["_regime"] = regime` を記録（どのレジームで重み付けたか）。
- `regime=None`／未知レジーム → 全グループ重み1＝打ち手4と完全一致（後方互換）。

### 4.3 `evaluate` からの配線
- `evaluate`（`signals.py:392`）の `_score_indicators(df_ind, configs)` を `_score_indicators(df_ind, configs, regime)` に変更（`regime` は `evaluate` の引数・打ち手3で導入済み）。
- 打ち手3のレジームゲート（risk_off×BUY のペナルティ）は**従来どおり後段で適用**。重み付け（本設計）とゲート（打ち手3）は別軸で共存：
  - 重み付け＝「どの指標群を重視するか」（スコア合成）。
  - ゲート＝「リスクオフでの新規BUYの方向ペナルティ」。

### 4.4 影響範囲（無改修で追従）
- バックテスト（`backtest.py`）・評価（`evaluation.py`）・エンドポイントは既に各営業日のレジームを `evaluate` に渡している（打ち手3）。`_score_indicators` がそれを使うようになるだけで、**バックテストにも重み付けが自動反映**＝検証=提示。

## 5. データフロー（不変・スコア合成のみ地合い依存に）
```
evaluate(df, configs, ..., regime)
  → _score_indicators(df, configs, regime): 指標→グループclip→Σ weight(regime,g)×group = score
  → 出来高フィルター → direction判定 → 週足足切り → 地合いレジームゲート(打ち手3) → return
```

## 6. 期待される挙動（既定重みでの定性）
- **risk_on（上昇トレンド）**：trend×2。順張り群が単独で閾値±2に到達可（順張り主体）。強い上昇で逆張りが「買われすぎ(-1)」を出しても trend(+2)−contrarian(1)=+1 で待ち、押し目で逆張りが買い(+1)に転じると +3 の強い買い（＝トレンドの押し目買い）。
- **neutral（レンジ）**：contrarian×2。逆張り群が主役（レンジでの逆張り）。
- **risk_off（下降/急落）**：trend×2 で下降（売り）が勝り、逆張りの「落ちるナイフ」買いを抑制。残るBUYは打ち手3ゲートが減点。

## 7. 後方互換・既存テストへの影響
- `regime=None` で全重み1＝打ち手4と完全一致。`regime` を渡さない既存テスト（`_score_indicators`/`evaluate` 直呼びでレジーム未指定）は**不変**。
- `signal_config` 不変（重み表は定数）→ config 件数テスト不変。
- レジーム付きでスコア値が変わるため、**レジームを渡してスコア値を断定する既存テストがあれば**更新（現状そういうテストは無い見込み。実行で確認）。
- `/optimize` のグリッド `[1,2,3]`（打ち手4で調整済み）：重み付きで最大は risk_on/neutral で ±5 に拡大するが、閾値到達は重み付き合成で評価される。グリッドは現状維持（必要なら計画で再検討）。

## 8. テスト戦略（決定論・ネット非依存）
- **risk_on で trend が増幅**：順張りが買い側・逆張りが中立な合成データで、`evaluate(regime="risk_on")` のスコアが `regime=None` より大きい（trend×2 が効く）こと。
- **neutral で contrarian が増幅**：逆張りが買い側に振れる（売られすぎ）データで、`evaluate(regime="neutral")` のスコアが `regime=None` より大きいこと。
- **後方互換**：`regime=None` のスコア・direction が打ち手4と同一（重み1）。
- **detail**：`detail["_groups"]` は重み前のクリップ値・`detail["_regime"]` にレジームが入る。
- **int 維持**：整数重み×int で score が int。
- 既存 `backend/`（85件）を緑に保つ（レジームを渡さない経路は不変）。

## 9. エラー処理・境界
- 未知レジーム文字列／`None` → `REGIME_GROUP_WEIGHTS.get(regime, {})` が空 → 各グループ `.get(group, 1)` で重み1（安全フォールバック）。
- 空グループ（全指標 continue）→ groups 空 → score 0（打ち手4と同じ）。

## 10. 成功基準
- レジームによって順張り/逆張りの寄与が切り替わる（テストで担保）。
- `regime=None` は打ち手4と完全一致（後方互換）。
- バックテスト（`/optimize` OOS）が重み付けを自動反映＝検証=提示。
- `detail["_groups"]`（重み前）＋`detail["_regime"]` で内訳が見える。
- `backend/` 全件グリーン。

## 11. スコープ外（将来）
- 重み値の `/optimize` 自動チューニング、打ち手6（連続確信度・ランキング）、フロント表示、SELL側の非対称重み。

> ⚠️ 本設計はスコア合成を相場つきに適応させるもので利益を保証しない。投資は自己責任の原則は不変。
