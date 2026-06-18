# 相関指標のグループ化（打ち手4）設計書

- 日付: 2026-06-18
- 対象ブランチ: `feature/indicator-grouping`
- 対応課題: `課題.md` 課題1（スコアが相関指標の多重カウント・順張りと逆張りの綱引き）／打ち手4
- 前提: フェーズA（検証インフラ）・打ち手3（地合いレジームゲート）完了済み。検証=提示を維持する。
- ステータス: 設計（実装前）

---

## 1. 背景と問題

`_score_indicators`（`signals.py:237-367`）は9指標が各々 ±weight を**単純加算**して1つの `score` を作る。逆張り・オシレーター系は **RSI・ボリンジャー・ストキャス・乖離率・CCI の5指標**あり、いずれも「売られすぎ/買われすぎ」という**ほぼ同じ情報**を測る。これらが別々に ±1 を出すと、実質1つの見方が**5票**として数えられ（多重カウント）、スコアは構造的に逆張りへ偏る。順張り系（MA・MACD）と需給（OBV）は計3票しかなく、「強いから買い」と「伸びきったから売り」が1つの数字で綱引きになる。

`課題.md` の打ち手：「相関でグルーピングし、グループ内は代表1票に正規化（逆張り群は合成して最大±1）。当面は『順張り群』『逆張り群』『出来高・需給群』の3カテゴリに集約し、カテゴリ単位で重み付けする」。

## 2. ゴール / 非ゴール

### ゴール（本設計＝打ち手4のみ）
1. 9つのスコア指標を相関に基づき**グループ化**する。
2. **グループ内で合算→クリップ（上限 ±cap）**してから合算し、逆張り群の多重カウントを止める。
3. 各指標の個別寄与（`detail`）は維持し、グループ純額も `detail` に記録（解釈性）。
4. 検証=提示を維持：`_score_indicators` の変更は `evaluate` 経由でバックテスト（`/optimize` OOS）にも自動反映。

### 非ゴール（次の増分）
- レジーム別のグループ重み切替（打ち手5）。本設計はグループ重み=1固定。
- 連続確信度スコア・Top N ランキング（打ち手6）。
- 指標の追加・閾値の自動再決定（`/optimize` が別途チューニング）。

## 3. 確定した設計判断

| 項目 | 決定 |
|---|---|
| グループ | 順張り(`ma_cross`,`macd`) / 逆張り(`rsi`,`bbands`,`stoch`,`disparity`,`cci`) / 需給(`obv`) / パターン(`candle_pattern`) |
| 正規化 | グループ内 ±w を合算 → `clip(-cap, +cap)`（cap 既定 **1**） |
| 合算 | `score = Σ_group clip(group_raw, -cap, +cap)`（v1 はグループ重み=1） |
| 個別寄与 | 既存の per-indicator `detail` は維持。`detail["_groups"]` にグループ純額を追加 |
| 閾値 | 既定 ±2 のまま。`/optimize`(OOS) で再チューニング・検証 |

## 4. コンポーネント詳細（`signals.py`）

### 4.1 グループ定義（モジュール定数）
```python
INDICATOR_GROUP = {
    "ma_cross": "trend", "macd": "trend",
    "rsi": "contrarian", "bbands": "contrarian", "stoch": "contrarian",
    "disparity": "contrarian", "cci": "contrarian",
    "obv": "volume",
    "candle_pattern": "pattern",
}
GROUP_CAP = 1   # グループ内の最大寄与（±）
```

### 4.2 `_score_indicators` の改修
- 各指標ブランチは現状どおり ±w を算出し `detail[key] = ±w` を記録するが、`score` へ直接加算するのをやめ、**グループ別の raw 合計**に積む。
- ローソク足の複数サブ寄与（`3whitesoldiers`/`3blackcrows`/`engulfing`）は `pattern` グループの raw に合算。
- 末尾で各グループを `clip(-GROUP_CAP, +GROUP_CAP)` し、その総和を `score` とする。
- 実装イメージ：
```python
def _score_indicators(df, configs):
    group_raw: dict[str, int] = {}
    detail: dict = {}

    def _add(rt, key, v):
        detail[key] = v
        g = INDICATOR_GROUP.get(rt, rt)
        group_raw[g] = group_raw.get(g, 0) + v

    for cfg in configs:
        ...
        # 例: rsi → _add("rsi", "rsi", +w) / ma_cross → _add("ma_cross", "ma_cross", +w)
        # candle → _add("candle_pattern", "3whitesoldiers", +w) など
        # price_target は continue（スコア対象外）

    groups = {g: max(-GROUP_CAP, min(GROUP_CAP, raw)) for g, raw in group_raw.items()}
    detail["_groups"] = groups
    score = sum(groups.values())
    return score, detail
```
- `score` は int を維持（`clip` の入力は int、総和も int）。`evaluate` 以降（direction 判定・各フィルター・レジームゲート）は不変。

### 4.3 影響範囲
- `evaluate`（`signals.py:370`）は `_score_indicators` の戻り値をそのまま使うため変更不要。出来高フィルター・週足足切り・地合いゲートは従来どおり後段で適用。
- バックテスト（`backtest.py`）・評価（`evaluation.py`）・エンドポイントは `evaluate` を呼ぶだけなので変更不要（スコア意味の改善が自動で反映）。
- `/optimize` の leave-one-out 寄与度：指標ルールを1つ外すとそのグループの raw 合計→clip が変わるため、**グループ化後の寄与**として引き続き機能する。

## 5. データフロー（不変・スコア計算のみ差し替え）
```
evaluate(df, configs, ..., regime)
  → _score_indicators: 指標→グループ別raw→clip→Σ = score（多重カウント解消）
  → 出来高フィルター → direction判定 → 週足足切り → 地合いレジームゲート → return
```

## 6. 後方互換・既存テストへの影響
- これは**スコア意味の意図的変更**（多重カウント解消）であり、完全な後方互換ではない。
- `detail` の per-indicator キーは維持（`detail["_groups"]` を追加）。`evaluate` の戻り値の型・direction の値域は不変。
- `signal_config` は不変（指標の追加・削除なし）→ `test_api.py` の config 件数（13）は**不変**。
- `test_signals.py` のうち**スコアの絶対値に依存するアサーション**は更新が必要（例：複数オシレーター同時発火で従来 ±N だったものが ±1 になる）。direction の妥当性のみ見るテストは不変。更新は「グループ化後の期待値」に合わせる。
- バックテスト系テストは demo/合成データで値が変わり得るが、キー存在・範囲・不変条件を見るものは維持。約定挙動に依存する数値アサーションがあれば最小更新。

## 7. テスト戦略（決定論・ネット非依存）
- **多重カウント解消**：逆張り5指標が全部「売られすぎ」を示す合成データで、グループ化前の素朴和（+5想定）に対し `_groups["contrarian"] == 1`・`score` 寄与が +1 に収まることを検証。
- **グループ別 clip**：あるグループの raw を意図的に大きく/逆向きにし、`clip` が ±cap に収まること、向きが正しいこと。
- **detail 維持**：per-indicator キー（rsi/macd/…）が従来どおり入り、`detail["_groups"]` が4グループの純額を持つこと。
- **direction 不変性**：明確な上昇/下降データで direction が妥当（buy/sell/neutral のいずれか・破綻しない）。
- 既存 `test_signals.py` のスコア依存テストを新仕様に更新。
- `backend/` 全体（82 件）を緑に保つ（更新が必要なものは最小更新）。

## 8. エラー処理・境界
- 指標が算出不能（データ不足で `continue`）→ そのグループの raw に寄与しない（0 のまま）。空グループは clip 対象外。
- 未マッピングの rule_type は `INDICATOR_GROUP.get(rt, rt)` で自分自身名の単独グループ＝従来同等（実質 cap=1 の単独）。`price_target` はスコア対象外（従来どおり `continue`）。

## 9. 成功基準
- 逆張り群が**最大 ±1**に収まり、多重カウントが解消される（テストで担保）。
- `score` が「順張り・逆張り・需給・パターンが各々1票」の合成になり、`detail["_groups"]` で内訳が見える。
- `evaluate` 以降・バックテスト・エンドポイントは無改修で新スコアに追従（検証=提示維持）。
- `/optimize`(OOS) でグループ化後の期待値が算出でき、閾値の再チューニングが可能。
- `backend/` テスト全件グリーン。

## 10. スコープ外（将来）
- 打ち手5（レジーム別グループ重み：本設計の `groups` 合算に地合い依存の重みを掛ける）、打ち手6（連続確信度・ランキング）、フロントでのグループ内訳表示。

> ⚠️ 本設計はスコア設計の健全化であり、利益を保証しない。投資は自己責任の原則は不変。
