"""日次自動更新スケジューラ（Phase 4）。

API プロセスに常駐するバックグラウンドスレッドで、毎営業日の指定時刻（JST・場後）に
refresh を1回だけ実行する。生成された買い/売りシグナルは NotificationWatcher が
ポーリングしてブラウザ通知する。**自動売買はしない。** 通知までが責務。

設定は app_meta（DB）から都度読むので、UI で ON/OFF・時刻を変えれば次の tick から反映される。
外部依存なし（標準ライブラリの threading のみ）。
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from typing import Callable

import db
from holidays_jp import is_market_holiday

JST = timezone(timedelta(hours=9))
TICK_SECONDS = 30


class DailyScheduler:
    def __init__(self, refresh_fn: Callable[..., dict]):
        self._refresh_fn = refresh_fn
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_run_date: str | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="daily-scheduler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    # ------------------------------------------------------------------
    def _loop(self) -> None:
        # 起動直後に1回 tick、その後 TICK_SECONDS ごと。
        while True:
            try:
                self._tick(datetime.now(JST))
            except Exception:
                # スケジューラの失敗で API を巻き込まない
                pass
            if self._stop.wait(TICK_SECONDS):
                return

    def _tick(self, now: datetime) -> None:
        m = db.get_all_meta()
        if m.get("scheduler_enabled", "0") != "1":
            return
        if now.weekday() >= 5:   # 土(5)・日(6)は実行しない
            return
        today = now.date().isoformat()
        # 祝日スキップ（既定 ON）。市場休業日には自動更新しない。
        if m.get("scheduler_skip_holidays", "1") == "1" and is_market_holiday(today):
            return

        try:
            hh, mm = (int(x) for x in m.get("scheduler_time", "16:00").split(":"))
        except (ValueError, AttributeError):
            return

        if self._last_run_date == today:
            return
        # 指定時刻を過ぎていれば、その営業日の分を1回だけ実行（起動が遅れてもキャッチアップ）。
        if (now.hour, now.minute) >= (hh, mm):
            demo = m.get("scheduler_demo", "0") == "1"
            self._refresh_fn(demo=demo)
            self._last_run_date = today
