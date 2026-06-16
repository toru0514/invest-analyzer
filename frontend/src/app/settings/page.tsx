"use client";

import { useEffect, useState } from "react";
import { api, AppSettings, SignalConfig, WatchItem } from "@/lib/api";
import Disclaimer from "@/components/Disclaimer";

const RULE_LABELS: Record<string, string> = {
  rsi: "RSI（売られすぎ/買われすぎ）",
  ma_cross: "移動平均クロス（GC/DC）",
  macd: "MACD",
  bbands: "ボリンジャーバンド",
  stoch: "ストキャスティクス",
  candle_pattern: "ローソク足パターン（赤三兵/三羽烏/包み足）",
  disparity: "乖離率（移動平均からの乖離%）",
  obv: "OBV（出来高トレンド）",
  cci: "CCI（売られすぎ/買われすぎ）",
  volume_filter: "出来高フィルター（ダマシ低減）",
  weekly_trend_filter: "週足トレンド足切り（逆張り事故低減）",
  atr_exit: "ATR出口設計（損切/利確・提案指値）",
  price_target: "指定金額アラート",
};

// 指標ごとに編集できる params 定義（RSI の 30/70、MA の期間など）
type ParamField = { key: string; label: string; step?: number; options?: { value: string; label: string }[] };
const PARAM_FIELDS: Record<string, ParamField[]> = {
  rsi: [
    { key: "length", label: "期間" },
    { key: "low", label: "売られすぎ" },
    { key: "high", label: "買われすぎ" },
  ],
  ma_cross: [
    { key: "short", label: "短期" },
    { key: "long", label: "長期" },
  ],
  macd: [
    { key: "fast", label: "Fast" },
    { key: "slow", label: "Slow" },
    { key: "signal", label: "Signal" },
  ],
  bbands: [
    { key: "length", label: "期間" },
    { key: "std", label: "σ", step: 0.1 },
  ],
  stoch: [
    { key: "k", label: "%K" },
    { key: "d", label: "%D" },
    { key: "low", label: "売られすぎ" },
    { key: "high", label: "買われすぎ" },
  ],
  candle_pattern: [],
  disparity: [
    { key: "ma", label: "MA期間" },
    { key: "low", label: "売られすぎ%" },
    { key: "high", label: "買われすぎ%" },
  ],
  obv: [{ key: "sma", label: "SMA期間" }],
  cci: [
    { key: "length", label: "期間" },
    { key: "low", label: "売られすぎ" },
    { key: "high", label: "買われすぎ" },
  ],
  volume_filter: [
    { key: "sma", label: "平均期間" },
    { key: "surge", label: "急増(倍)", step: 0.1 },
    { key: "quiet", label: "閑散(倍)", step: 0.1 },
    { key: "bonus", label: "ボーナス" },
  ],
  weekly_trend_filter: [
    { key: "sma", label: "週足SMA" },
    { key: "mode", label: "モード", options: [
      { value: "penalty", label: "減点" },
      { value: "block", label: "無効化" },
    ] },
  ],
  atr_exit: [
    { key: "length", label: "ATR期間" },
    { key: "stop_mult", label: "損切×", step: 0.1 },
    { key: "target_mult", label: "利確×", step: 0.1 },
    { key: "limit_method", label: "指値方式", options: [
      { value: "ma", label: "移動平均" },
      { value: "atr", label: "ATR押し目" },
      { value: "support", label: "サポート" },
    ] },
    { key: "limit_ma", label: "指値MA期間" },
    { key: "entry_atr_mult", label: "ATR押し目×", step: 0.1 },
  ],
};

export default function Settings() {
  const [watch, setWatch] = useState<WatchItem[]>([]);
  const [configs, setConfigs] = useState<SignalConfig[]>([]);
  const [settings, setSettings] = useState<AppSettings | null>(null);
  const [ticker, setTicker] = useState("");
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);

  // price_target 追加フォーム
  const [ptTicker, setPtTicker] = useState("");
  const [ptAbove, setPtAbove] = useState("");
  const [ptBelow, setPtBelow] = useState("");

  async function load() {
    setError(null);
    try {
      const [w, c, s] = await Promise.all([api.getWatchlist(), api.getConfig(), api.getSettings()]);
      setWatch(w);
      setConfigs(c);
      setSettings(s);
    } catch (e) {
      setError(String(e));
    }
  }

  useEffect(() => {
    load();
  }, []);

  function flash(msg: string) {
    setSaved(msg);
    setTimeout(() => setSaved(null), 2500);
  }

  async function addStock(e: React.FormEvent) {
    e.preventDefault();
    if (!ticker.trim() || !name.trim()) return;
    try {
      await api.addWatch(ticker.trim(), name.trim());
      setTicker("");
      setName("");
      await load();
    } catch (e) {
      setError(String(e));
    }
  }

  async function removeStock(id: number) {
    await api.deleteWatch(id);
    await load();
  }

  function setLocal(id: number, patch: Partial<SignalConfig>) {
    setConfigs((cs) => cs.map((c) => (c.id === id ? { ...c, ...patch } : c)));
  }

  function setParam(id: number, key: string, value: number | string) {
    setConfigs((cs) =>
      cs.map((c) => (c.id === id ? { ...c, params: { ...c.params, [key]: value } } : c)),
    );
  }

  async function saveConfigs() {
    try {
      await api.updateConfig(
        indicatorConfigs.map((c) => ({
          id: c.id,
          weight: c.weight,
          enabled: !!c.enabled,
          params: c.params,
        })),
      );
      flash("指標設定を保存しました。");
      await load();
    } catch (e) {
      setError(String(e));
    }
  }

  async function saveSettings() {
    if (!settings) return;
    try {
      await api.updateSettings(settings);
      flash("スコア閾値・スケジューラ設定を保存しました。");
      await load();
    } catch (e) {
      setError(String(e));
    }
  }

  async function addPriceTarget(e: React.FormEvent) {
    e.preventDefault();
    const above = ptAbove.trim() === "" ? undefined : Number(ptAbove);
    const below = ptBelow.trim() === "" ? undefined : Number(ptBelow);
    if (!ptTicker || (above === undefined && below === undefined)) {
      setError("ティッカーと、上限/下限の少なくとも一方を入力してください。");
      return;
    }
    try {
      const params: Record<string, number> = {};
      if (above !== undefined) params.above = above;
      if (below !== undefined) params.below = below;
      await api.addConfig({ rule_type: "price_target", ticker: ptTicker, params });
      setPtAbove("");
      setPtBelow("");
      flash("指定金額アラートを追加しました。");
      await load();
    } catch (e) {
      setError(String(e));
    }
  }

  async function removePriceTarget(id: number) {
    await api.deleteConfig(id);
    await load();
  }

  // ---- 銘柄別 ATR 出口（オーバーライド） ----
  const globalAtr = configs.find((c) => c.rule_type === "atr_exit" && c.ticker === null);
  const tickerExits = new Map(
    configs.filter((c) => c.rule_type === "atr_exit" && c.ticker !== null).map((c) => [c.ticker as string, c]),
  );

  async function addTickerExit(ticker: string) {
    const base = (globalAtr?.params ?? {
      length: 14, stop_mult: 1.5, target_mult: 1.5, limit_method: "support", support_n: 20,
    }) as Record<string, unknown>;
    try {
      await api.addConfig({ rule_type: "atr_exit", ticker, params: { ...base } });
      flash(`${ticker} の個別出口を追加しました。`);
      await load();
    } catch (e) {
      setError(String(e));
    }
  }

  async function saveTickerExit(c: SignalConfig) {
    try {
      await api.updateConfig([{ id: c.id, params: c.params }]);
      flash(`${c.ticker} の個別出口を保存しました。`);
      await load();
    } catch (e) {
      setError(String(e));
    }
  }

  async function removeTickerExit(id: number) {
    await api.deleteConfig(id);
    await load();
  }

  const indicatorConfigs = configs.filter((c) => c.rule_type !== "price_target" && c.ticker === null);
  const priceTargets = configs.filter((c) => c.rule_type === "price_target");

  return (
    <div>
      <h1 className="mb-4 text-xl font-bold">設定</h1>
      {error && <p className="mb-3 rounded bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p>}
      {saved && <p className="mb-3 rounded bg-green-50 px-3 py-2 text-sm text-green-700">{saved}</p>}

      {/* 監視銘柄 */}
      <section className="mb-8 rounded border bg-white p-4">
        <h2 className="mb-3 font-semibold">監視銘柄</h2>
        <form onSubmit={addStock} className="mb-4 flex flex-wrap gap-2 text-sm">
          <input
            value={ticker}
            onChange={(e) => setTicker(e.target.value)}
            placeholder="ティッカー（例: 8306.T）"
            className="rounded border px-2 py-1"
          />
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="銘柄名"
            className="rounded border px-2 py-1"
          />
          <button className="rounded bg-blue-600 px-3 py-1 text-white hover:bg-blue-700">追加</button>
        </form>
        <ul className="divide-y text-sm">
          {watch.map((w) => (
            <li key={w.id} className="flex items-center justify-between py-2">
              <span>
                <span className="font-mono">{w.ticker}</span> — {w.name}
              </span>
              <button onClick={() => removeStock(w.id)} className="text-red-600 hover:underline">
                削除
              </button>
            </li>
          ))}
          {watch.length === 0 && <li className="py-2 text-slate-500">銘柄がありません。</li>}
        </ul>
      </section>

      {/* スコア閾値・スケジューラ */}
      {settings && (
        <section className="mb-8 rounded border bg-white p-4">
          <div className="mb-3 flex items-center justify-between">
            <h2 className="font-semibold">スコア閾値・自動更新</h2>
            <button onClick={saveSettings} className="rounded bg-green-600 px-3 py-1 text-sm text-white hover:bg-green-700">
              保存
            </button>
          </div>
          <div className="flex flex-wrap items-end gap-4 text-sm">
            <label className="flex flex-col gap-1">
              買い判定の閾値（スコア ≥）
              <input
                type="number"
                value={settings.buy_threshold}
                onChange={(e) => setSettings({ ...settings, buy_threshold: Number(e.target.value) })}
                className="w-24 rounded border px-2 py-1"
              />
            </label>
            <label className="flex flex-col gap-1">
              売り判定の閾値（スコア ≤）
              <input
                type="number"
                value={settings.sell_threshold}
                onChange={(e) => setSettings({ ...settings, sell_threshold: Number(e.target.value) })}
                className="w-24 rounded border px-2 py-1"
              />
            </label>
          </div>
          <div className="mt-4 flex flex-wrap items-end gap-4 border-t pt-4 text-sm">
            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={settings.scheduler_enabled}
                onChange={(e) => setSettings({ ...settings, scheduler_enabled: e.target.checked })}
              />
              日次自動更新を有効化
            </label>
            <label className="flex flex-col gap-1">
              実行時刻（JST・場後）
              <input
                type="time"
                value={settings.scheduler_time}
                onChange={(e) => setSettings({ ...settings, scheduler_time: e.target.value })}
                className="w-32 rounded border px-2 py-1"
              />
            </label>
            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={settings.scheduler_demo}
                onChange={(e) => setSettings({ ...settings, scheduler_demo: e.target.checked })}
              />
              demo（合成データ）で実行
            </label>
            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={settings.scheduler_skip_holidays}
                onChange={(e) => setSettings({ ...settings, scheduler_skip_holidays: e.target.checked })}
              />
              祝日（市場休業日）はスキップ
            </label>
          </div>
          <p className="mt-3 text-xs text-slate-500">
            自動更新は API プロセス常駐中のみ動作します（毎営業日まとめて refresh→判定→通知）。自動売買は行いません。
          </p>
        </section>
      )}

      {/* シグナル指標（重み・有効・params） */}
      <section className="mb-8 rounded border bg-white p-4">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="font-semibold">シグナル指標（どの技を使うか・重み・パラメータ）</h2>
          <button onClick={saveConfigs} className="rounded bg-green-600 px-3 py-1 text-sm text-white hover:bg-green-700">
            保存
          </button>
        </div>
        <table className="w-full text-sm">
          <thead className="bg-slate-100 text-left">
            <tr>
              <th className="px-3 py-2">指標</th>
              <th className="px-3 py-2">重み</th>
              <th className="px-3 py-2">パラメータ</th>
              <th className="px-3 py-2">有効</th>
            </tr>
          </thead>
          <tbody>
            {indicatorConfigs.map((c) => (
              <tr key={c.id} className="border-t align-top">
                <td className="px-3 py-2">{RULE_LABELS[c.rule_type] ?? c.rule_type}</td>
                <td className="px-3 py-2">
                  <input
                    type="number"
                    min={0}
                    value={c.weight}
                    onChange={(e) => setLocal(c.id, { weight: Number(e.target.value) })}
                    className="w-16 rounded border px-2 py-0.5"
                  />
                </td>
                <td className="px-3 py-2">
                  <div className="flex flex-wrap gap-2">
                    {(PARAM_FIELDS[c.rule_type] ?? []).map((f) => (
                      <label key={f.key} className="flex items-center gap-1 text-xs text-slate-600">
                        {f.label}
                        {f.options ? (
                          <select
                            value={String(c.params?.[f.key] ?? f.options[0].value)}
                            onChange={(e) => setParam(c.id, f.key, e.target.value)}
                            className="rounded border px-2 py-0.5"
                          >
                            {f.options.map((o) => (
                              <option key={o.value} value={o.value}>
                                {o.label}
                              </option>
                            ))}
                          </select>
                        ) : (
                          <input
                            type="number"
                            step={f.step ?? 1}
                            value={Number(c.params?.[f.key] ?? 0)}
                            onChange={(e) => setParam(c.id, f.key, Number(e.target.value))}
                            className="w-16 rounded border px-2 py-0.5"
                          />
                        )}
                      </label>
                    ))}
                    {(PARAM_FIELDS[c.rule_type] ?? []).length === 0 && (
                      <span className="text-xs text-slate-400">—</span>
                    )}
                  </div>
                </td>
                <td className="px-3 py-2">
                  <input
                    type="checkbox"
                    checked={!!c.enabled}
                    onChange={(e) => setLocal(c.id, { enabled: e.target.checked ? 1 : 0 })}
                  />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="mt-3 text-xs text-slate-500">
          状態ベースのスコアです：トレンド系（MA/MACD）が常時 ±重み、逆張り系（RSI/ストキャス/BB）が
          売られすぎ/買われすぎで加点。合計が上の閾値を超えると買い/売り判定になります。
        </p>
      </section>

      {/* 銘柄別 ATR 出口（オーバーライド） */}
      <section className="mb-8 rounded border bg-white p-4">
        <h2 className="mb-1 font-semibold">銘柄別の出口設定（ATR・任意）</h2>
        <p className="mb-3 text-xs text-slate-500">
          スイングは「数日〜1週間」が基本。利確×を小さくすると近め（短期・高回転）、大きくすると遠め（トレンド追随）。
          銘柄ごとに動きが違うので、個別に上書きできます。未設定の銘柄は全銘柄共通（利確×{String(globalAtr?.params?.target_mult ?? 1.5)} / 損切×{String(globalAtr?.params?.stop_mult ?? 1.5)}）を使います。
        </p>
        <ul className="divide-y text-sm">
          {watch.map((w) => {
            const ov = tickerExits.get(w.ticker);
            return (
              <li key={w.id} className="flex flex-wrap items-center gap-3 py-2">
                <span className="w-44 shrink-0">
                  <span className="font-mono">{w.ticker}</span>{" "}
                  <span className="text-slate-500">{w.name}</span>
                </span>
                {ov ? (
                  <>
                    <label className="flex items-center gap-1 text-xs text-slate-600">
                      利確×
                      <input type="number" step={0.1} value={Number(ov.params?.target_mult ?? 1.5)}
                        onChange={(e) => setParam(ov.id, "target_mult", Number(e.target.value))}
                        className="w-16 rounded border px-2 py-0.5" />
                    </label>
                    <label className="flex items-center gap-1 text-xs text-slate-600">
                      損切×
                      <input type="number" step={0.1} value={Number(ov.params?.stop_mult ?? 1.5)}
                        onChange={(e) => setParam(ov.id, "stop_mult", Number(e.target.value))}
                        className="w-16 rounded border px-2 py-0.5" />
                    </label>
                    <label className="flex items-center gap-1 text-xs text-slate-600">
                      指値方式
                      <select value={String(ov.params?.limit_method ?? "support")}
                        onChange={(e) => setParam(ov.id, "limit_method", e.target.value)}
                        className="rounded border px-2 py-0.5">
                        <option value="ma">移動平均</option>
                        <option value="atr">ATR押し目</option>
                        <option value="support">サポート</option>
                      </select>
                    </label>
                    <button onClick={() => saveTickerExit(ov)} className="rounded bg-green-600 px-2 py-0.5 text-xs text-white hover:bg-green-700">保存</button>
                    <button onClick={() => removeTickerExit(ov.id)} className="text-xs text-red-600 hover:underline">共通に戻す</button>
                  </>
                ) : (
                  <>
                    <span className="text-xs text-slate-400">全銘柄共通を使用中</span>
                    <button onClick={() => addTickerExit(w.ticker)} className="rounded border px-2 py-0.5 text-xs text-blue-700 hover:bg-blue-50">個別に設定</button>
                  </>
                )}
              </li>
            );
          })}
          {watch.length === 0 && <li className="py-2 text-slate-500">銘柄がありません。</li>}
        </ul>
        <p className="mt-3 text-xs text-slate-500">
          ここの設定は作戦ボードの提案指値・利確/損切に反映されます（シミュレーションは全銘柄共通設定を使用）。
        </p>
      </section>

      {/* 指定金額アラート（price_target） */}
      <section className="rounded border bg-white p-4">
        <h2 className="mb-3 font-semibold">指定金額アラート（上限/下限）</h2>
        <form onSubmit={addPriceTarget} className="mb-4 flex flex-wrap items-end gap-2 text-sm">
          <label className="flex flex-col gap-1">
            銘柄
            <select
              value={ptTicker}
              onChange={(e) => setPtTicker(e.target.value)}
              className="rounded border px-2 py-1"
            >
              <option value="">選択…</option>
              {watch.map((w) => (
                <option key={w.id} value={w.ticker}>
                  {w.ticker} — {w.name}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1">
            上限（≥ で売り通知）
            <input
              type="number"
              value={ptAbove}
              onChange={(e) => setPtAbove(e.target.value)}
              placeholder="例: 3300"
              className="w-28 rounded border px-2 py-1"
            />
          </label>
          <label className="flex flex-col gap-1">
            下限（≤ で買い通知）
            <input
              type="number"
              value={ptBelow}
              onChange={(e) => setPtBelow(e.target.value)}
              placeholder="例: 3000"
              className="w-28 rounded border px-2 py-1"
            />
          </label>
          <button className="rounded bg-blue-600 px-3 py-1 text-white hover:bg-blue-700">追加</button>
        </form>
        <ul className="divide-y text-sm">
          {priceTargets.map((c) => (
            <li key={c.id} className="flex items-center justify-between py-2">
              <span>
                <span className="font-mono">{c.ticker}</span>
                {c.params?.above != null && <span className="ml-2">上限 ≥ {String(c.params.above)}</span>}
                {c.params?.below != null && <span className="ml-2">下限 ≤ {String(c.params.below)}</span>}
              </span>
              <button onClick={() => removePriceTarget(c.id)} className="text-red-600 hover:underline">
                削除
              </button>
            </li>
          ))}
          {priceTargets.length === 0 && (
            <li className="py-2 text-slate-500">アラートは未登録です。</li>
          )}
        </ul>
        <p className="mt-3 text-xs text-slate-500">
          指定金額アラートはスコアと独立した即時通知です。データ更新時に最新終値が上限以上/下限以下なら通知します。
        </p>
      </section>

      <Disclaimer />
    </div>
  );
}
