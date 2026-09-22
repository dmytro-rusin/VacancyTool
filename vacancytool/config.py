from __future__ import annotations

import json
from datetime import datetime, timedelta, time
from pathlib import Path
from zoneinfo import ZoneInfo

TIMEZONE = ZoneInfo("Europe/Kyiv")
DEFAULT_SETTINGS = {
    "timezone": "Europe/Kyiv",
    "schedule": [
        {"start": "07:00", "end": "12:00", "minutes": 10},
        {"start": "12:00", "end": "16:00", "minutes": 15},
        {"start": "16:00", "end": "20:00", "minutes": 10},
        {"start": "20:00", "end": "07:00", "minutes": 30},
    ],
    "searches": [
        {"source": "djinni", "name": "iOS", "url": "https://djinni.co/jobs/rss/?search_type=basic-search&primary_keyword=iOS", "enabled": True},
        {"source": "djinni", "name": "Mobile", "url": "https://djinni.co/jobs/rss/?all_keywords=Mobile&search_type=basic-search", "enabled": True},
        {"source": "dou", "name": "iOS/macOS", "url": "https://jobs.dou.ua/vacancies/feeds/?category=iOS/macOS", "enabled": True},
        {"source": "dou", "name": "Mobile", "url": "https://jobs.dou.ua/vacancies/feeds/?search=Mobile", "enabled": True},
    ],
}


def settings_path(root: Path) -> Path:
    return root / "settings.json"


def load_settings(root: Path) -> dict:
    path = settings_path(root)
    if not path.exists():
        path.write_text(json.dumps(DEFAULT_SETTINGS, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    data = json.loads(path.read_text(encoding="utf-8"))
    validate_schedule(data["schedule"])
    return data


def validate_schedule(windows: list[dict]) -> None:
    if not windows:
        raise ValueError("Потрібне хоча б одне часове вікно")
    intervals = []
    for window in windows:
        start = time.fromisoformat(window["start"])
        end = time.fromisoformat(window["end"])
        minutes = int(window["minutes"])
        if start == end or not 1 <= minutes <= 1440:
            raise ValueError("Некоректне часове вікно або інтервал")
        a = start.hour * 60 + start.minute
        b = end.hour * 60 + end.minute
        if b <= a:
            b += 1440
        intervals.append((a, b))
    # Exactly one window at every minute: no gaps or overlapping runs.
    coverage = [0] * 1440
    for a, b in intervals:
        for minute in range(a, b):
            coverage[minute % 1440] += 1
    if any(count != 1 for count in coverage):
        raise ValueError("Часові вікна мають покривати добу без пропусків і перетинів")


def next_due(after: datetime, windows: list[dict]) -> datetime:
    """Return the next wall-clock slot strictly after *after*."""
    now = after.astimezone(TIMEZONE)
    candidates = []
    for offset in range(-1, 3):
        day = (now + timedelta(days=offset)).date()
        for window in windows:
            hh, mm = map(int, window["start"].split(":"))
            eh, em = map(int, window["end"].split(":"))
            start = datetime.combine(day, time(hh, mm), TIMEZONE)
            end = datetime.combine(day, time(eh, em), TIMEZONE)
            if end <= start:
                end += timedelta(days=1)
            point = start
            while point < end:
                if point > now:
                    candidates.append(point)
                    break
                point += timedelta(minutes=int(window["minutes"]))
    return min(candidates)
