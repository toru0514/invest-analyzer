// 東証ティッカーの正規化・コード判定（純関数・テスト対象）。
// 旧4桁数字コード（例 7203）と新形式の英数字コード（例 285A）の両方に対応する。

/** コードらしい入力を東証ティッカー（XXXX.T）に正規化。コードでなければ null を返す。
 *  「数字始まりで 1〜4 文字の英数字（.T 任意）」を受け付ける（部分入力 58 等も従来どおり許容）。 */
export function asTicker(q: string): string | null {
  const m = q.trim().toUpperCase().match(/^(\d[0-9A-Z]{0,3})(\.T)?$/);
  return m ? `${m[1]}.T` : null;
}

/** 名前解決（/stocks/name）を投げてよい「完全な4桁コード」か。
 *  部分入力（5/58/580）や名前では false にして、未確定入力での無駄な解決リクエストを防ぐ。 */
export function isCompleteCode(q: string): boolean {
  return /^\d[0-9A-Z]{3}(\.T)?$/.test(q.trim().toUpperCase());
}
