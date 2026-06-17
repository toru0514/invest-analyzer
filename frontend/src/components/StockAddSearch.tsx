"use client";

import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";

type Hit = { ticker: string; name: string };

// コードらしい入力（数字4桁まで・末尾 .T 任意）を東証ティッカーに正規化
function asTicker(q: string): string | null {
  const m = q.trim().toUpperCase().match(/^(\d{1,4})(\.T)?$/);
  return m ? `${m[1]}.T` : null;
}

/**
 * 名前/コードで検索して選択、もしくはコードを直接入力して追加する。
 * onAdded: 追加成功後に呼ばれる（一覧の再読込など）。
 */
export default function StockAddSearch({ onAdded }: { onAdded: () => Promise<void> | void }) {
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<Hit[]>([]);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const boxRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const query = q.trim();
    if (!query) {
      setHits([]);
      return;
    }
    const t = setTimeout(async () => {
      try {
        setHits(await api.searchStocks(query));
      } catch {
        setHits([]);
      }
    }, 250);
    return () => clearTimeout(t);
  }, [q]);

  useEffect(() => {
    function onDocClick(e: MouseEvent) {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, []);

  const codeTicker = asTicker(q);
  const codeNotInHits = codeTicker && !hits.some((h) => h.ticker === codeTicker);

  async function add(ticker: string, name = "") {
    setBusy(true);
    setError(null);
    setMsg(null);
    try {
      const res = await api.addWatch(ticker, name);
      setQ("");
      setHits([]);
      setOpen(false);
      setMsg(`${res.ticker}（${res.name}）を追加しました。`);
      await onAdded();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (hits.length > 0) return add(hits[0].ticker, hits[0].name);
    if (codeTicker) return add(codeTicker);
    // デバウンス未完了でも、押した時点で確定検索してから判断する
    const query = q.trim();
    if (query) {
      try {
        const found = await api.searchStocks(query);
        if (found.length > 0) return add(found[0].ticker, found[0].name);
      } catch {
        /* 検索失敗時は下のエラーへ */
      }
    }
    setError("一致する銘柄がありません。コード（例: 8306.T）で入力してください。");
  }

  return (
    <div ref={boxRef} className="relative">
      <form onSubmit={onSubmit} className="flex flex-wrap items-center gap-2 rounded border bg-white px-3 py-2 text-sm">
        <span className="text-xs font-semibold text-slate-500">銘柄を追加</span>
        <input
          value={q}
          onChange={(e) => { setQ(e.target.value); setOpen(true); }}
          onFocus={() => setOpen(true)}
          placeholder="名前またはコードで検索（例: トヨタ / 7203）"
          className="w-72 rounded border px-2 py-1"
        />
        <button disabled={busy} className="rounded bg-blue-600 px-3 py-1 text-white hover:bg-blue-700 disabled:opacity-50">
          追加
        </button>
        <span className="text-xs text-slate-400">追加後は「作戦を生成」で判定・価格を取得</span>
      </form>

      {open && q.trim() && (hits.length > 0 || codeNotInHits) && (
        <ul className="absolute z-10 mt-1 max-h-72 w-96 overflow-auto rounded border bg-white text-sm shadow-lg">
          {hits.map((h) => (
            <li key={h.ticker}>
              <button
                type="button"
                onClick={() => add(h.ticker, h.name)}
                className="flex w-full items-center gap-2 px-3 py-2 text-left hover:bg-slate-50"
              >
                <span className="font-mono text-slate-500">{h.ticker}</span>
                <span>{h.name}</span>
              </button>
            </li>
          ))}
          {codeNotInHits && (
            <li className="border-t">
              <button
                type="button"
                onClick={() => add(codeTicker!)}
                className="w-full px-3 py-2 text-left text-blue-700 hover:bg-blue-50"
              >
                「{codeTicker}」をコードで追加（名前を自動取得）
              </button>
            </li>
          )}
        </ul>
      )}

      {msg && <p className="mt-2 text-xs text-green-700">{msg}</p>}
      {error && <p className="mt-2 text-xs text-red-600">{error}</p>}
    </div>
  );
}
