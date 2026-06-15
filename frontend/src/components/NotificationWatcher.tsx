"use client";

import { useEffect } from "react";
import { api } from "@/lib/api";

// 未通知シグナルを定期ポーリングしてブラウザ通知を出す（Phase 2）。
// 自動売買はしない。通知のみ。
const POLL_MS = 60_000;

export default function NotificationWatcher() {
  useEffect(() => {
    if (typeof window === "undefined" || !("Notification" in window)) return;

    let stopped = false;

    async function poll() {
      try {
        const signals = await api.getUnnotified();
        if (stopped || signals.length === 0) return;
        if (Notification.permission === "granted") {
          for (const s of signals) {
            const verb = s.direction === "buy" ? "買いシグナル" : s.direction === "sell" ? "売りシグナル" : "アラート";
            new Notification(`${s.ticker} ${verb}`, {
              body: `スコア ${s.score} / ${s.date}\n${Object.keys(s.detail).join(", ")}`,
            });
          }
          await api.markNotified(signals.map((s) => s.id));
        }
      } catch {
        // API 未起動などは黙ってスキップ
      }
    }

    if (Notification.permission === "default") {
      Notification.requestPermission();
    }
    poll();
    const t = setInterval(poll, POLL_MS);
    return () => {
      stopped = true;
      clearInterval(t);
    };
  }, []);

  return null;
}
