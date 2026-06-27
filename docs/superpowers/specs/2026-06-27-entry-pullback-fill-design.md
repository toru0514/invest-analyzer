# 入口の取り逃し是正（押し目を浅く・約定率を上げる）— 設計

- 日付: 2026-06-27
- 対応課題: 課題5「出口とサイジングが固定的（入口より出口…だが入口の取り逃しが上限を作る）」／課題8「流動性＝提案指値が現実に約定するか」。本ブランチは**診断で確定した入口（提案指値の深さ）の是正**。
- ブランチ: `feat/entry-pullback-fill`
- 前提メモリ: [[roadmap-progress]]
- 直前の関連: 打ち手9後半（出口R:R 4:1）`docs/superpowers/specs/2026-06-27-asymmetric-rr-exit-design.md`。**本件はその上に積む**（R:R 4:1 を固定した状態で入口だけ変える）。

## 1. 背景・目的（診断の結論）

出口R:Rを 4:1 に是正した後も、plan の **fill_rate≈37%**（押し目指値が浅すぎず深すぎる「5日線への押し目」で、上げ相場で**伸びる玉を取り逃す**）が残るボトルネック。verify-before-adopt 診断（実データ3年・5銘柄 8306/7203/9984/6758/9432.T・**出口R:R 4:1 と他を全固定**・`/tmp/entry_fill_study.py`）で入口レバーをクリーンA/Bスイープ:

- 入口の深さ（`limit_method`×`entry_atr_mult`）を config だけでスイープ。加えて成行/翌寄り新規を `_run_backtest_plan` の getsource＋外科パッチ＋exec で忠実プロトタイプ（entry_mode をモジュール global で注入、**limit モードは置換が no-op ＝ orig と完全一致を sanity gate で検証**）。

| 入口 | fill% | full pnl% | DD% | OOS pnl% | OOS期待値 | OOSn | overfit_gap |
|---|---|---|---|---|---|---|---|
| **ma（現行既定・5日線深押し）** | 36.8 | 9.7 | 8.3 | 8.8 | 9.9 | 55 | 0.5 |
| atr0.5（0.5ATR押し） | 47.4 | 37.9 | 6.7 | 9.8 | 11.7 | 50 | 4.9 |
| **atr0.25（0.25ATR押し・本採用）** | 67.1 | 28.4 | 6.3 | **16.5** | **17.1** | 58 | **−3.9** |
| atr0.1（0.1ATR押し） | 76.4 | 25.3 | 6.3 | 16.1 | 16.1 | 60 | −4.0 |
| atr0.0（≈現値指値） | 80.0 | 19.4 | 6.4 | 14.4 | 15.0 | 59 | −3.1 |
| support（20日安値・最深） | 2.0 | 3.1 | 0.7 | −0.7 | −20.5 | 2 | 24.5 |
| 成行/翌寄り（100%約定） | 100.0 | 15.2 | 5.7 | 15.2 | 16.4 | 58 | −7.7 |

**確証された因果**：

1. **取り逃しが OOS の約半分を捨てていた。** fill は深さに対し単調（37→47→67→76→80→100%）、OOS は浅くするほど改善し **atr0.25 で OOS 8.8→16.5%（+88%）・OOS期待値 9.9→17.1** とほぼ倍増、その後 0.1/0.0/成行で緩むプラトー。**support（最深）は 2% しか約定せず退化**（OOS n=2 の −20.5 はノイズ）＝機構の裏取り。
2. **win率は全変種で約25-29%と不変。** 浅押しは勝率を上げるのでなく「**R:R 4:1 を取り逃さず実現**」する（avgWin 59→83）。**入口が出口R:R是正の律速だった**（4:1は玉に入って初めて効く）。
3. **DD はむしろ改善**（8.3→6.3%）。fill を上げても DD は悪化しない。
4. **overfit_gap が atr0.25 で −3.9（OOSが訓練より良い）＝過学習でない・頑健**。atr0.5 は full 最良(37.9%)だが gap +4.9 で OOS は伸びず（in-sample 寄り）＝非採用。
5. **新コード経路は不要。** 成行(100%fill・OOS15.2)は atr0.25(16.5)にわずか劣る＝**config の浅押しで利得をほぼ回収**。出口R:R fix と同型の「コード不変・config 既定変更」で採用できる。

**本件は、検証で最頑健だった `method="atr"・entry_atr_mult=0.25`（0.25·ATR の浅い押し目）を build_plan の既定にし、作戦カードとバックテスト双方に反映する。**

## 2. スコープ

**含む（本ブランチ）**
- `atr_exit` config の入口既定を **`limit_method`: `"ma"`→`"atr"`／`entry_atr_mult`: `0.5`→`0.25`** に（他キー＝length/stop_mult/target_mult/limit_ma/support_n は不変）。
- 既存DB（`data.db`）の `atr_exit` を**非クロバー・一度きり移行**（旧既定の入口＝`limit_method=="ma"` かつ `entry_atr_mult==0.5` の行だけ atr/0.25 へ）。`exit_rr_migrated` と同じく **app_meta フラグ `entry_method_migrated` で一度だけ**（毎起動の恒久強制にしない＝設定UIで ma を選び直せる）。
- 上記により build_plan の提案指値が浅くなり、**作戦カード（提示指値）とバックテスト（提示指値で約定検証）が自動的に新入口で動く＝検証=提示を維持**。

**含まない（YAGNI・後続）**
- **成行/翌寄り新規のコード経路**（`entry_mode` の本実装）。診断で atr0.25 にわずか劣り、config だけで利得を回収できるため不要。将来やるなら trail/time と同じ PAYLOAD 実験パラメータとして追加。
- **R:R動的化・分割利確・トレーリングのライブ採用**（別レバー・別ブランチ）。
- **流動性フィルター（課題8の本丸）**：薄商いでの滑り・約定可能性チェックは別途（打ち手12）。本件は「指値の深さ」だけを是正。

## 3. 設計原則

- **検証=提示の不破壊**：build_plan の提案指値でバックテスト約定する既存構造を維持。本件は build_plan が読む `limit_method`/`entry_atr_mult` の**既定値変更のみ**で、コード経路は不変。
- **加法的・最小**：build_plan / backtest / カードの**コードは無改変**（`limit_method`/`entry_atr_mult` を config から読む既存経路がそのまま新値で動く）。新 config ルールを足さない＝`DEFAULT_CONFIGS` 14個・`test_api` 件数不変。
- **非クロバー・一度きり移行**：旧既定の入口ペア（ma+0.5）だけ是正し、ユーザーが意図的に選んだ値（method=support 等、または depth!=0.5）は壊さない。一度きりゆえ以後の選択を尊重（出口R:R移行 `_migrate_exit_rr_once` と同方針）。
- **config 可変・再検証前提**：経験的基礎は5銘柄/3y・OOS n=58＝方向性。**銘柄を増やして再検証する**（params 編集で容易）。

## 4. 変更内容

### 4.1 `backend/signals.py`（既定値）
`DEFAULT_CONFIGS` の `atr_exit`（`signals.py:49`）の `params` を **`"limit_method": "atr"`／`"entry_atr_mult": 0.25`** に変更（現状 `"ma"`／`0.5`）。`limit_ma`/`support_n`/`length`/`stop_mult`/`target_mult` は不変。
→ 新規DB（`init_db` シード）と、明示 config を渡さない build_plan 経路（`build_plan(df,dir,score)` の DEFAULT_CONFIGS）が新入口になる。

build_plan の atr 方式（既存・`signals.py:651,665`）：buy `limit = close − entry_atr_mult·ATR`、sell `limit = close + entry_atr_mult·ATR`。method="atr" は ma のような現値キャップ（`min(ma,close)`）が無く、固定の浅い押し目になる。

### 4.2 `backend/db.py`（既存DB移行・非クロバー・一度きり）
`_migrate_exit_rr`/`_migrate_exit_rr_once`（`db.py:150-176`）に倣い:
```python
def _migrate_entry_method(conn):
    """既存 DB の atr_exit 入口を旧既定(ma + entry_atr_mult 0.5)→新既定(atr + 0.25) に是正（冪等・非クロバー）。
    旧既定ペアの行だけ更新。ユーザーが意図的に選んだ method/depth は変えない。
    0.5/0.25 は IEEE-754 で厳密＝JSON 往復で == が安定。"""
    rows = conn.execute("SELECT id, params FROM signal_config WHERE rule_type = 'atr_exit'").fetchall()
    for r in rows:
        params = json.loads(r["params"] or "{}")
        if params.get("limit_method") == "ma" and params.get("entry_atr_mult") == 0.5:
            params["limit_method"] = "atr"
            params["entry_atr_mult"] = 0.25
            conn.execute("UPDATE signal_config SET params = ? WHERE id = ?",
                         (json.dumps(params), r["id"]))

def _migrate_entry_method_once(conn):
    if conn.execute("SELECT value FROM app_meta WHERE key = 'entry_method_migrated'").fetchone():
        return
    _migrate_entry_method(conn)
    conn.execute("INSERT OR REPLACE INTO app_meta (key, value) VALUES ('entry_method_migrated', '1')")
```
`init_db`（`db.py:186`）の `_migrate_exit_rr_once(conn)` の直後に `_migrate_entry_method_once(conn)` を追加（同一コネクション）。

**新規DB安全性**：init_db はマイグレーション→シードの順。フレッシュDBでは migration が空テーブルに対し no-op＋フラグ set、その後シードが新既定(atr/0.25)を入れる＝二重適用なし（`_migrate_exit_rr_once` と同じ既存挙動）。

### 4.3 build_plan / backtest / カード
**コード変更なし**。build_plan は `limit_method`/`entry_atr_mult` を config から読む（`signals.py:632,635`）。バックテストは build_plan の提示で約定するため新入口で検証される。作戦カードは `limit_price`/rationale を表示するだけ。

## 5. テスト計画（TDD）

- `test_signals.py`
  - 新規 `test_build_plan_default_entry_is_shallow_atr`：明示 config 無しの `build_plan(df,"buy",3)` の `limit_price ≈ close − 0.25·ATR`、sell 側 `≈ close + 0.25·ATR` を固定（method=atr・depth0.25）。`atr = signals.atr_value(df,14)`。`synthetic_history(seed=7,n=120)`（[[signal-tests-prefer-deterministic-ohlc]]）。
  - 既存 build_plan テストは非破壊（spec-review で実コード確認済み・**no-op**）：`test_build_plan_exits_are_ordered`(110) は `limit<=close`(buy)/`>=close`(sell) を見るだけで atr 方式でも成立、`test_build_plan_limit_method_switch`(140) は明示 config ゆえ DEFAULT 非依存。**ただし `test_build_plan_exits_are_ordered` 内のコメント `test_signals.py:116`「既定の買い指値（5日線方式）…」は既定が atr になると陳腐化するため、コメントのみ「既定の買い指値（atr 浅押し方式）」へ更新**（アサーションは不変）。
- `test_api.py`
  - 新規 `test_migrate_entry_method_updates_old_default(tmp_path, monkeypatch)`：common 旧既定(ma+0.5)／per-ticker 旧既定(ma+0.5)／per-ticker ユーザー設定(method=support) を入れ、`init_db` 後に旧既定だけ atr/0.25・support は不変（非クロバー）。`monkeypatch.setattr(db,"DB_PATH",...)` 方式。
  - 新規 `test_migrate_entry_method_is_one_shot(tmp_path, monkeypatch)`：**フラグの本質（再クロバー防止）を検証**＝`test_migrate_exit_rr_is_one_shot`(test_api.py:398) と同型。migrate 後に `update_config` で ma/0.5 に戻し、再 `init_db` で**ユーザーの選択が保たれる**ことをアサート。**冪等テストだけでは不十分**（移行後 atr/0.25 は条件 ma&0.5 に当たらずフラグ有無を区別できない＝フラグを外す「簡約化」を緑のまま通してしまうため、この one-shot が必須）。
  - `DEFAULT_CONFIGS` 件数14不変（params 値変更のみ・`test_api.py:85` 系の件数アサート維持）。
- **回帰**：backtest 系は base vs explicit の相対比較ゆえ等価維持。全スイート（`backend/venv/bin/python -m pytest backend/ -q`）で DEFAULT 入口既定変更由来の破綻が無いか確認（spec-review で既存テスト破綻なしを確認済み＝新規追加のみの想定）。

## 6. 後方互換・回帰

- 整数 `score`/`direction`・`confidence`・サイジング・出口（R:R/trail/time/earnings）の経路は不変。
- 新 config ルールなし＝件数14不変。スキーマ変更なし（params 値の移行のみ・app_meta に新フラグ1件）。
- フロント無改変（カードは limit_price/rationale をそのまま表示）。既存フロントテスト不変。
- 既存DBは入口の旧既定→新既定を一度だけ是正（schema 不変）。出口R:R移行(`exit_rr_migrated`)と独立した別フラグ。

## 7. 非スコープ・将来拡張

- 成行/翌寄り新規の本実装（PAYLOAD 実験パラメータ `entry_mode`）。
- 入口の深さの動的化（レジーム/ボラ別 entry_atr_mult）。
- 流動性フィルター・約定可能性（出来高・値幅）チェック（課題8の本丸・打ち手12）。
- 入口の深さを設定画面に出す（現状は config 編集経路で可変）。

## 8. リスク・割り切り（v1）

- **経験的基礎は5銘柄/3y・OOS n=58＝方向性**。atr0.25 と atr0.1 は OOS pnl がほぼ拮抗（16.5 vs 16.1）＝「中程度に浅い(0.1–0.25)≫現行の深押し(ma/0.5)」が頑健な結論。0.25 を採るのは OOS 期待値最良・avgWin 最大（R:R 4:1 を最も実現）・取引数やや少（コスト/滑り露出小）のため。config 可変ゆえ銘柄拡大で再検証する。
- **到達点は buy&hold 未満**：OOS 16.5% は buy&hold OOS 33.8% の約半分（強い上げ相場）。本件は「入口がボトルネックだった事実の是正」で OOS をほぼ倍増させる前進。buy&hold 超えは別軸（レジーム横断・将来）。
- **移行のクロバー懸念**：method=ma かつ depth=0.5 を意図的に維持していたユーザーも新既定へ動く（旧既定と区別不能）。一度きりなので以後 config で ma に戻せる。
- **method=atr は現値キャップが無い**：浅い押し目（close−0.25·ATR）は ma（min(5MA,close)）より現値に近く、約定が増える＝意図どおり。`limit>stop` は build_plan の stop=close−1.5·ATR ＜ limit=close−0.25·ATR ゆえ常に成立（サイジングの risk_per_share>0 を保つ）。
