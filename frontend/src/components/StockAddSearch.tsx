"use client";

import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { asTicker, isCompleteCode } from "@/lib/ticker";

type Hit = { ticker: string; name: string };

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
  const [codeName, setCodeName] = useState<string | null>(null);
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

  // 完全な4桁コードが入力されたら銘柄名を解決してプレビュー表示する。
  // 部分入力（5/58/580）や名前では投げない（無駄な解決リクエストを防ぐ）。
  useEffect(() => {
    const ticker = isCompleteCode(q) ? asTicker(q) : null;
    if (!ticker) {
      setCodeName(null);
      return;
    }
    let cancelled = false;
    setCodeName(null);
    const t = setTimeout(async () => {
      try {
        const r = await api.resolveName(ticker);
        if (!cancelled) setCodeName(r.name || null);
      } catch {
        if (!cancelled) setCodeName(null);
      }
    }, 300);
    return () => {
      cancelled = true;
      clearTimeout(t);
    };
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
    if (codeTicker) return add(codeTicker, codeName ?? "");
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
                onClick={() => add(codeTicker!, codeName ?? "")}
                className="flex w-full items-center gap-2 px-3 py-2 text-left hover:bg-blue-50"
              >
                <span className="font-mono text-slate-500">{codeTicker}</span>
                {codeName ? (
                  <span>{codeName}</span>
                ) : (
                  <span className="text-slate-400">コードで追加（名前を自動取得）</span>
                )}
                <span className="ml-auto text-xs text-blue-700">追加</span>
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
