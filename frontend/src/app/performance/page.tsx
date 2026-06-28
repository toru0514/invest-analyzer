"use client";

import { useEffect, useState } from "react";
import { api, type PerfRow } from "@/lib/api";
import PerfTable from "@/components/PerfTable";

export default function PerformancePage() {
  const [rows, setRows] = useState<PerfRow[] | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    api.getPerformance().then((r) => setRows(r.summary)).catch((e) => setErr(String(e)));
  }, []);

  return (
    <div className="space-y-4">
      <h1 className="text-xl font-bold">型別成績（実績トラッキング）</h1>
      <p className="text-sm text-slate-500">
        生成した作戦の実結果を、その後の実価格から自動追跡。地合い×方向の型ごとに
        約定率・勝率・平均R（建玉リスク1単位あたり損益）を集計します。
        勝率・平均Rは利確/損切が確定した分のみ（保有中は除外）。運用日数が増えるほど精度が上がります。
      </p>
      {err && <p className="text-sm text-red-600">読み込みに失敗しました: {err}</p>}
      {rows == null && !err ? (
        <p className="text-sm text-slate-500">読み込み中…</p>
      ) : (
        rows && <PerfTable rows={rows} />
      )}
    </div>
  );
}
