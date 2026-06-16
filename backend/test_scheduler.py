"""日次スケジューラのロジックテスト（ネットワーク・時間待ち不要）。

DailyScheduler._tick に任意の「現在時刻」と app_meta を与えて挙動を検証する。

    backend/venv/bin/python -m pytest backend/test_scheduler.py -q
"""

from __future__ import annotations

from datetime import datetime

import scheduler
from scheduler import JST, DailyScheduler


def _patch_meta(monkeypatch, **kw):
    base = {"scheduler_enabled": "1", "scheduler_time": "16:00", "scheduler_demo": "0",
            "scheduler_skip_holidays": "1"}
    base.update(kw)
    monkeypatch.setattr(scheduler.db, "get_all_meta", lambda: base)


def test_disabled_does_not_run(monkeypatch):
    calls = []
    _patch_meta(monkeypatch, scheduler_enabled="0")
    sch = DailyScheduler(lambda demo=False: calls.append(demo))
    sch._tick(datetime(2026, 6, 15, 17, 0, tzinfo=JST))
    assert calls == []


def test_runs_once_after_time_with_demo_flag(monkeypatch):
    calls = []
    _patch_meta(monkeypatch, scheduler_demo="1")
    sch = DailyScheduler(lambda demo=False: calls.append(demo))
    sch._tick(datetime(2026, 6, 15, 16, 1, tzinfo=JST))   # 月曜・時刻後
    assert calls == [True]
    # 同日2回目は実行しない
    sch._tick(datetime(2026, 6, 15, 16, 30, tzinfo=JST))
    assert calls == [True]


def test_runs_again_next_business_day(monkeypatch):
    calls = []
    _patch_meta(monkeypatch)
    sch = DailyScheduler(lambda demo=False: calls.append(demo))
    sch._tick(datetime(2026, 6, 15, 16, 1, tzinfo=JST))
    sch._tick(datetime(2026, 6, 16, 16, 1, tzinfo=JST))
    assert calls == [False, False]


def test_does_not_run_before_time(monkeypatch):
    calls = []
    _patch_meta(monkeypatch)
    sch = DailyScheduler(lambda demo=False: calls.append(demo))
    sch._tick(datetime(2026, 6, 15, 15, 59, tzinfo=JST))
    assert calls == []


def test_does_not_run_on_weekend(monkeypatch):
    calls = []
    _patch_meta(monkeypatch)
    sch = DailyScheduler(lambda demo=False: calls.append(demo))
    sch._tick(datetime(2026, 6, 20, 17, 0, tzinfo=JST))   # 土曜
    sch._tick(datetime(2026, 6, 21, 17, 0, tzinfo=JST))   # 日曜
    assert calls == []


def test_skips_market_holiday(monkeypatch):
    calls = []
    _patch_meta(monkeypatch)   # scheduler_skip_holidays = "1"
    sch = DailyScheduler(lambda demo=False: calls.append(demo))
    sch._tick(datetime(2026, 5, 5, 17, 0, tzinfo=JST))    # こどもの日（平日・祝日）
    assert calls == []


def test_runs_on_holiday_when_skip_disabled(monkeypatch):
    calls = []
    _patch_meta(monkeypatch, scheduler_skip_holidays="0")
    sch = DailyScheduler(lambda demo=False: calls.append(demo))
    sch._tick(datetime(2026, 5, 5, 17, 0, tzinfo=JST))    # 祝日でもスキップ無効なら実行
    assert calls == [False]
