# 地合いレジームゲート（打ち手3）設計書

- 日付: 2026-06-18
- 対象ブランチ: `feature/market-regime-gate`
- 対応課題: `課題.md` 課題2（相場環境＝市況を見ていない）／打ち手3
- 前提: フェーズA（検証インフラ）完了済み。本設計は「検証=提示」を維持する。
- ステータス: 設計（実装前）

---

## 1. 背景と問題

各銘柄を完全に独立評価しており、日経平均など**地合い**を一切入力していない。全面安の日のBUYと地合い良好な日のBUYは別物で、暴落局面で逆張りを連発して大やられするリスクがある。`課題.md` は「指数のトレンド/ボラ/ドローダウンでレジーム判定し、リスクオフ時は新規BUYを抑制」「既存の `weekly_trend_filter`（個別銘柄の週足）を**指数版に拡張**するのが最短」と指摘する。

現状、`perform_refresh` は `^N225` の `weekly_trend` を計算しているが、**AI解説の文章に渡すだけでスコア判定（`evaluate`）には一切効いていない**（`main.py:308-353`）。

## 2. ゴール / 非ゴール

### ゴール（本設計＝打ち手3のみ）
1. 指数から**地合いレジーム（risk_on / neutral / risk_off）**を判定する。
2. レジームを `evaluate()` の**一次ゲート**にする：**リスクオフ×新規BUYはペナルティ減点**（閾値割れで neutral 化）。
3. フェーズAの**検証=提示**を維持：バックテストにも同ゲートを反映し、look-ahead を回避する。
4. レジームは設定（`signal_config`）で調整可能（mode・penalty・閾値）。

### 非ゴール（次の増分・本設計では扱わない）
- レジーム別の順張り/逆張り重み切替（打ち手5）。
- 連続確信度スコア・Top N ランキング（打ち手6）。
- フロントエンドでの地合い表示（データ上は `detail["regime"]` に出るが、専用UIは次の増分）。
- SELL 側の対称ゲート（リスクオンで売り抑制）。v1 は risk_off×BUY のみ。

## 3. 確定した設計判断（ユーザー合意済み）

| 項目 | 決定 |
|---|---|
| ゲート方式 | **ペナルティ減点**（mode で block も選択可・既定 penalty）。既存 `weekly_trend_filter` と同方式 |
| レジーム段階 | 3段階（risk_on / neutral / risk_off）。指数の週足トレンド＋直近高値からのドローダウンで判定 |
| 配線 | レジームは `evaluate()` 内で適用（検証=提示）。バックテストには**日次レジーム系列を前計算し asof 参照** |
| 後方互換 | `regime`/`regime_series` は任意（None で従来挙動）。`market_regime` 設定が無ければ無効 |

## 4. アーキテクチャ（コンポーネント）

| 関数/箇所 | 区分 | 責務 |
|---|---|---|
| `signals.market_regime(index_df, *, sma, dd_lookback, dd_threshold) -> str` | 新規 | 指数 OHLCV の**最終行時点**のレジームを返す（risk_on/neutral/risk_off） |
| `signals.regime_series(index_df, **params) -> pd.Series` | 新規 | 各営業日について「その日までの指数」でのレジームを前計算（look-ahead安全・バックテスト用） |
| `signals.evaluate(df, configs, buy_th, sell_th, regime=None)` | 改修 | `market_regime` ルールに従いレジームゲートを適用。`regime=None` で従来どおり |
| `signals.DEFAULT_CONFIGS` | 改修 | `market_regime` ルールを追加 |
| `main.perform_refresh` | 改修 | 現在のレジームを算出し `evaluate` へ。AI解説の market_ctx にもレジームを渡す |
| `backtest.run_backtest(..., regime_series=None)` | 改修 | 各営業日 `regime_series.asof(d)` を `evaluate` に渡す（両モード） |
| `evaluation.benchmark / evaluate_holdout` | 改修 | `regime_series` を受け取り `run_backtest` へ透過 |
| `main` `/backtest` `/optimize` | 改修 | 指数を取得→`regime_series` を1回算出→評価層へ渡す |

依存：`market_regime` は既存 `weekly_trend` を再利用。`regime_series` は `market_regime` を日次で呼ぶ。

## 5. コンポーネント詳細

### 5.1 `market_regime`
```
def market_regime(index_df, *, sma=13, dd_lookback=60, dd_threshold=0.10) -> str:
    if index_df is None or len(index_df) < 5: return "neutral"
    trend = weekly_trend(index_df, sma)                      # 既存（up/down/flat）
    closes = index_df["close"].tail(dd_lookback)
    peak = float(closes.max()); last = float(closes.iloc[-1])
    dd = (peak - last) / peak if peak > 0 else 0.0           # 直近高値からの下落率
    if trend == "down" or dd >= dd_threshold:   return "risk_off"
    if trend == "up" and dd < dd_threshold / 2: return "risk_on"
    return "neutral"
```
- 最終行時点で算出。呼び出し側が `index_df` を日付で切ることで look-ahead を回避。

### 5.2 `regime_series`（バックテスト用・前計算）
```
def regime_series(index_df, **params) -> pd.Series:
    # 各日 i について、その日までの指数 index_df.iloc[:i+1] でレジームを算出
    idx = index_df.sort_index()
    return pd.Series({idx.index[i]: market_regime(idx.iloc[:i + 1], **params)
                      for i in range(len(idx))})
```
- 1回だけ前計算し、バックテストの各営業日で `asof` 参照する（指数の数百日に対し O(N) 回。stock×threshold×ablation のループ内で再計算しない）。

### 5.3 `evaluate` のレジームゲート
`weekly_trend_filter` の直後に、同じ足切りパターンで追加：
```
    # --- 地合いレジームの一次ゲート（指数版の足切り） ---
    rf = _find_cfg(configs, "market_regime")
    if regime is not None:
        detail["regime"] = regime
        if rf is not None and regime == "risk_off" and direction == "buy":
            mode = rf.get("mode", "penalty"); penalty = int(rf.get("penalty", 2))
            if mode == "block":
                detail["regime_filter"] = "blocked"; direction = "neutral"
            else:
                score -= penalty; detail["regime_filter"] = -penalty
                direction = _direction(score)
    return score, direction, detail
```
- `regime is None`（バックテストで指数未指定／既存呼び出し）なら**完全に従来どおり**。
- risk_on / neutral は記録のみ（v1 はブーストしない・YAGNI）。

### 5.4 設定（DEFAULT_CONFIGS に追加）
```
{"rule_type": "market_regime",
 "params": {"mode": "penalty", "penalty": 2, "sma": 13, "dd_lookback": 60, "dd_threshold": 0.10},
 "weight": 1, "enabled": 1}
```
`signal_config` は任意 rule_type を JSON で保存できるため、スキーマ変更不要。`/config` で調整可能。

### 5.5 ライブ配線（`perform_refresh`）
- 既取得の指数 `idx_df`（`^N225`、env `GEMINI_INDEX_TICKER`）から `regime = market_regime(idx_df)` を算出。
- `evaluate(df, ticker_cfgs, buy_th, sell_th, regime=regime)` に渡す。
- AI解説 `market_ctx` に `regime` を追加（地合いラベルを自然文に反映。既存 `index_trend` は維持）。

### 5.6 バックテスト配線
- `run_backtest(..., regime_series=None)`：score・plan 両モードの各意思決定日 `d` で
  `regime = _regime_at(regime_series, d)`（`asof`、NaN は None 扱い）を `evaluate(window, ..., regime=regime)` に渡す。
- `benchmark(..., regime_series=None)` と `evaluate_holdout(..., regime_series=None)` は `run_backtest` へ透過。
  - ホールドアウトでは train でも test でも**同一の `regime_series`**（全期間の前計算）を使い、各日 `asof` で当日以前のレジームを参照する（look-ahead 安全。train は split 以前の日しか評価しないので自然に train 範囲のレジームのみ使う）。
- `_regime_at(series, d)`: `series is None` → None。`v = series.asof(d)`、`pd.isna(v)` → None、else `v`。

### 5.7 エンドポイント配線
- `/backtest`・`/optimize`：対象銘柄取得と同様に指数 `get_history(INDEX_TICKER, period, demo)` を取得し、空でなければ `rs = regime_series(idx_df, **regime_params)` を1回算出して評価層に渡す。指数取得失敗時は `regime_series=None`（ゲート無効・従来挙動）。
- `regime_params` は `market_regime` 設定（common configs）から読む。無ければ既定。

## 6. データフロー（/optimize 例）
```
/optimize → 銘柄histories + 指数idx_df を取得
          → rs = regime_series(idx_df)            # 日次レジーム（前計算・1回）
          → evaluate_holdout(histories, configs, regime_series=rs, ...)
               ├ train/test とも run_backtest(..., regime_series=rs)
               │    各営業日 d: regime = rs.asof(d) → evaluate(window, regime=regime)
               └ benchmark(..., regime_series=rs)
```

## 7. 後方互換
- `evaluate` の `regime`、`run_backtest`/`benchmark`/`evaluate_holdout` の `regime_series` はすべて**任意（既定 None）**。
- 既存テスト（指数を渡さない）は `regime=None` で**従来と同一挙動**＝全件維持。
- `market_regime` 設定を追加しても、`regime` が渡らなければゲートは発火しない。
- 指数取得失敗（ネット制限・demo以外）は握りつぶして `regime_series=None`（ゲート無効）。

## 8. テスト戦略（決定論・ネット非依存・合成データ）
- `market_regime`：上昇・低DD→risk_on／下降→risk_off／高DD（直近高値から閾値超下落）→risk_off／横ばい低DD→neutral。`len<5`→neutral。
- `regime_series`：長さ一致・各値が当日以前データの `market_regime` と一致（look-ahead無し）。
- `evaluate`：`regime="risk_off"`＋`market_regime`設定で BUY がペナルティ/ブロックされ neutral 化。`regime=None` で不変。`regime="risk_on"` は不変（記録のみ）。
- `run_backtest`：`regime_series` 指定でリスクオフ日のエントリーが抑制される（指定なしとの差）。look-ahead 安全（`asof` が当日以前のみ参照）。
- 結合（`test_api.py`）：`/backtest`・`/optimize` が demo（合成指数）でレジーム込みでも正常応答（既存キー＋`detail.regime`）。
- 既存 70 件は維持（指数未指定の経路は不変）。

## 9. エラー処理・境界
- 指数 `len < 5` や None → `market_regime` は "neutral"、`regime_series` は空/None 相当でゲート無効。
- `regime_series.asof(d)` が NaN（d が系列開始前）→ None。
- 設定 `market_regime` 未登録 → ゲート無効（`_find_cfg` が None）。

## 10. 成功基準
- 指数から3段階レジームが算出され、`evaluate` のゲートとして**リスクオフ時に新規BUYが抑制**される。
- バックテスト（`/optimize` の OOS）でレジーム込みの成績が出る＝**検証=提示**が保たれる。
- `regime` 未指定の既存経路は挙動不変（既存テスト緑）。
- 設定でゲートの mode・penalty・閾値を調整できる。

> ⚠️ 本設計は地合いを判断材料に加えるものであり、利益を保証しない。投資は自己責任の原則は不変。
