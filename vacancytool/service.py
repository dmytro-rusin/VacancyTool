from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.date import DateTrigger

from . import database as db
from .config import TIMEZONE, load_settings, next_due, settings_path, validate_schedule
from .notifications import send_pending
from .sources import client, fetch_detail, fetch_feed, parse_feed

log = logging.getLogger(__name__)


class Collector:
    def __init__(self, root: Path):
        self.root = root
        self.database_path = root / "vacancies.sqlite3"
        self.settings = load_settings(root)
        db.initialize(self.database_path)
        self.scheduler = BackgroundScheduler(timezone=TIMEZONE, daemon=True)
        self.run_lock = threading.Lock()
        self.schedule_lock = threading.Lock()
        self.next_run: datetime | None = None
        self.running = False

    def start(self):
        self.scheduler.start()
        self._schedule_next()
        self.manual_refresh()

    def shutdown(self):
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)

    def _schedule_next(self):
        with self.schedule_lock:
            due = next_due(datetime.now(TIMEZONE), self.settings["schedule"])
            self.next_run = due
            self.scheduler.add_job(self._scheduled_run, DateTrigger(run_date=due), id="collector",
                                   replace_existing=True, max_instances=1, misfire_grace_time=None)

    def _scheduled_run(self):
        # Schedule against the clock, before the network work starts.
        self._schedule_next()
        self._run("scheduled")

    def manual_refresh(self) -> bool:
        if self.run_lock.locked() or self.running:
            return False
        self.running = True
        threading.Thread(target=self._run, args=("manual",), daemon=True, name="manual-refresh").start()
        return True

    def update_schedule(self, windows: list[dict]):
        validate_schedule(windows)
        new_settings = {**self.settings, "schedule": windows}
        path = settings_path(self.root)
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(new_settings, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temp.replace(path)
        self.settings = new_settings
        if self.scheduler.running:
            self._schedule_next()

    def state(self) -> dict:
        return {
            "running": self.running,
            "last_run": db.latest_run(self.database_path),
            "next_run": self.next_run.isoformat() if self.next_run else None,
            "counts": db.counts(self.database_path),
            "mail_pending": len(db.pending_notifications(self.database_path)),
            "mail_configured": (self.root / "mail.json").exists(),
            "schedule": self.settings["schedule"],
        }

    def _run(self, trigger_type: str):
        if not self.run_lock.acquire(blocking=False):
            return
        self.running = True
        run_id = db.begin_run(self.database_path, trigger_type)
        found = new = 0
        errors: list[str] = []
        session = client()
        try:
            for search in self.settings["searches"]:
                if not search.get("enabled", True):
                    continue
                source = search["source"]
                key = f"{source}:{search['name']}:{search['url']}"
                try:
                    state = db.feed_state(self.database_path, key)
                    response = fetch_feed(session, search["url"],
                                          state.get("etag") if trigger_type == "scheduled" else None,
                                          state.get("last_modified") if trigger_type == "scheduled" else None)
                    if response.status_code == 304:
                        db.save_feed_state(self.database_path, key, None, None, None)
                        continue
                    items = parse_feed(source, response.content)
                    found += len(items)
                    before_errors = len(errors)
                    for item in items:
                        try:
                            posting = db.get_posting(self.database_path, source, item.source_key)
                            checked = False
                            detail_html = detail_company = work_format = location = None
                            last_checked = posting.get("detail_checked_at") if posting else None
                            needs_detail = not posting or not posting.get("metadata_checked_at") or not last_checked or (datetime.now(timezone.utc) -
                                datetime.fromisoformat(last_checked)) >= timedelta(hours=24)
                            if needs_detail:
                                checked = True
                                try:
                                    detail_html, detail_company, work_format, location = fetch_detail(session, source, item.original_url)
                                except Exception as exc:
                                    errors.append(f"{source} detail {item.source_key}: {exc}")
                            if db.upsert_item(self.database_path, item, key, detail_html, detail_company, checked,
                                              work_format, location):
                                new += 1
                        except Exception as exc:
                            errors.append(f"{source} item {item.source_key}: {exc}")
                    if len(errors) == before_errors:
                        db.save_feed_state(self.database_path, key, response.headers.get("ETag"),
                                           response.headers.get("Last-Modified"), None)
                    else:
                        db.save_feed_state(self.database_path, key, None, None,
                                           "Часть вакансий не обработана")
                except Exception as exc:
                    message = f"{source} {search['name']}: {exc}"
                    errors.append(message)
                    db.save_feed_state(self.database_path, key, None, None, message)
            try:
                send_pending(self.root, self.database_path)
            except Exception as exc:
                errors.append(f"Письмо: {exc}")
                log.exception("Mail notification failed")
        except Exception as exc:
            errors.append(f"Общая ошибка: {exc}")
            log.exception("Collector failed")
        finally:
            session.close()
            db.end_run(self.database_path, run_id, found, new, errors)
            self.running = False
            self.run_lock.release()
