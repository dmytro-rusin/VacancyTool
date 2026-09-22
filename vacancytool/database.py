from __future__ import annotations

import difflib
import re
import sqlite3
from contextlib import contextmanager, closing
from datetime import datetime, timezone
from pathlib import Path

from .scoring import assess
from .sources import FeedItem, dou_company, html_to_text, without_salary, without_salary_title

STATUSES = ("New", "Applied", "Interested", "Viewed", "Postponed", "Rejected", "Irrelevant", "Deleted")


def excluded_role(title: str) -> bool:
    """Exclude roles by job title; description may mention teammates in these roles."""
    role = re.split(r"\sв\s", title, maxsplit=1)[0]
    return bool(re.search(
        r"\b(?:QA|AQA|SDET|QC|quality assurance|quality engineer|test(?:er|ing) engineer|"
        r"project manager|project management|проєктн(?:ий|ого) менеджер|"
        r"UI\s*[/&-]\s*UX|UX\s*[/&-]\s*UI|(?:product|web|graphic|game|visual|ui|ux) designer|"
        r"designer|дизайнер|customer support|technical support|tech support|support (?:engineer|specialist|agent)|"
        r"help ?desk|підтримк[аи]|поддержк[аи])\b", role, re.I))


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path: Path) -> sqlite3.Connection:
    db = sqlite3.connect(path, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA busy_timeout=30000")
    return db


@contextmanager
def connection(path: Path):
    with closing(connect(path)) as db:
        with db:
            yield db


def initialize(path: Path) -> None:
    with connection(path) as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS vacancies (
            id INTEGER PRIMARY KEY,
            title TEXT NOT NULL,
            company TEXT,
            description_text TEXT NOT NULL DEFAULT '',
            score INTEGER NOT NULL CHECK(score BETWEEN 0 AND 100),
            level TEXT NOT NULL,
            stack TEXT NOT NULL,
            role TEXT NOT NULL,
            reason TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'New',
            published_at TEXT,
            first_seen_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS postings (
            id INTEGER PRIMARY KEY,
            vacancy_id INTEGER NOT NULL REFERENCES vacancies(id),
            source TEXT NOT NULL,
            source_key TEXT NOT NULL,
            original_url TEXT NOT NULL,
            canonical_url TEXT NOT NULL,
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            detail_checked_at TEXT,
            detail_ok INTEGER NOT NULL DEFAULT 0,
            UNIQUE(source, source_key)
        );
        CREATE TABLE IF NOT EXISTS posting_searches (
            posting_id INTEGER NOT NULL REFERENCES postings(id),
            search_key TEXT NOT NULL,
            PRIMARY KEY(posting_id, search_key)
        );
        CREATE TABLE IF NOT EXISTS feed_state (
            search_key TEXT PRIMARY KEY,
            etag TEXT,
            last_modified TEXT,
            checked_at TEXT,
            last_error TEXT
        );
        CREATE TABLE IF NOT EXISTS runs (
            id INTEGER PRIMARY KEY,
            trigger_type TEXT NOT NULL,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            found_count INTEGER NOT NULL DEFAULT 0,
            new_count INTEGER NOT NULL DEFAULT 0,
            error_count INTEGER NOT NULL DEFAULT 0,
            details TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS mail_notifications (
            vacancy_id INTEGER PRIMARY KEY REFERENCES vacancies(id),
            queued_at TEXT NOT NULL,
            sent_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_vacancies_score ON vacancies(score DESC, first_seen_at DESC);
        CREATE INDEX IF NOT EXISTS idx_postings_vacancy ON postings(vacancy_id);
        """)
        for table, column, definition in (
            ("vacancies", "excluded", "INTEGER NOT NULL DEFAULT 0"),
            ("vacancies", "work_format", "TEXT"),
            ("vacancies", "location", "TEXT"),
            ("postings", "metadata_checked_at", "TEXT"),
        ):
            if column not in {row["name"] for row in db.execute(f"PRAGMA table_info({table})")}:
                db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        for row in db.execute("""SELECT id,title,company,description_text,score,level,stack,role,reason,
                              work_format FROM vacancies""").fetchall():
            title = without_salary_title(row["title"])
            text = without_salary(row["description_text"])
            company = row["company"]
            if db.execute("SELECT 1 FROM postings WHERE vacancy_id=? AND source='dou' LIMIT 1", (row["id"],)).fetchone():
                company = dou_company(title) or company
            if company and any(sign in company for sign in ("$", "€", "£", "₴")):
                company = company.split(",", 1)[0].strip()
            result = assess(title, text, row["work_format"])
            db.execute("UPDATE vacancies SET excluded=? WHERE id=?", (int(excluded_role(title)), row["id"]))
            if (title, company, text, result.score, result.level, result.stack, result.role, result.reason) != (
                row["title"], row["company"], row["description_text"], row["score"], row["level"],
                row["stack"], row["role"], row["reason"]):
                db.execute("""UPDATE vacancies SET title=?,company=?,description_text=?,score=?,
                           level=?,stack=?,role=?,reason=?,updated_at=? WHERE id=?""",
                           (title, company, text, result.score, result.level, result.stack, result.role,
                            result.reason, utc_now(), row["id"]))


def get_posting(path: Path, source: str, source_key: str):
    with connection(path) as db:
        row = db.execute("SELECT * FROM postings WHERE source=? AND source_key=?", (source, source_key)).fetchone()
        return dict(row) if row else None


def normalized_title(title: str) -> str:
    title = re.split(r"\sв\s", title, maxsplit=1)[0]
    return re.sub(r"[^\w+]+", " ", title.casefold()).strip()


def duplicate_vacancy(db: sqlite3.Connection, item: FeedItem, text: str) -> int | None:
    if not item.company or len(text) < 250:
        return None
    candidates = db.execute("SELECT id, title, description_text FROM vacancies WHERE lower(company)=?",
                            (item.company.casefold(),)).fetchall()
    for row in candidates:
        if normalized_title(row["title"]) != normalized_title(item.title):
            continue
        other = row["description_text"]
        if len(other) >= 250 and difflib.SequenceMatcher(None, text[:3000], other[:3000]).ratio() >= 0.92:
            return row["id"]
    return None


def upsert_item(path: Path, item: FeedItem, search_key: str, detail_html: str | None,
                detail_company: str | None, detail_checked: bool,
                work_format: str | None = None, location: str | None = None) -> bool:
    """Return True only when a new canonical vacancy was inserted."""
    now = utc_now()
    description_text = without_salary(html_to_text(detail_html or item.summary_html))
    title = without_salary_title(item.title)
    company = detail_company or item.company
    with connection(path) as db:
        post = db.execute("SELECT * FROM postings WHERE source=? AND source_key=?",
                          (item.source, item.source_key)).fetchone()
        if post:
            vacancy_id = post["vacancy_id"]
            db.execute("""UPDATE postings SET original_url=?, canonical_url=?, last_seen_at=?,
                          detail_checked_at=COALESCE(?, detail_checked_at), metadata_checked_at=COALESCE(?, metadata_checked_at),
                          detail_ok=? WHERE id=?""",
                       (item.original_url, item.canonical_url, now, now if detail_checked else None,
                        now if detail_checked and detail_html else None,
                        int(bool(detail_html)) if detail_checked else post["detail_ok"], post["id"]))
            old = db.execute("SELECT * FROM vacancies WHERE id=?", (vacancy_id,)).fetchone()
            effective_work_format = work_format or old["work_format"]
            db.execute("""UPDATE vacancies SET work_format=COALESCE(?,work_format),
                       location=COALESCE(?,location), excluded=? WHERE id=?""",
                       (work_format, location, int(excluded_role(title)), vacancy_id))
            if not detail_html and old["description_text"]:
                description_text = old["description_text"]
            result = assess(title, description_text, effective_work_format)
            if (description_text != old["description_text"] or title != old["title"] or
                    result.score != old["score"] or result.reason != old["reason"]):
                db.execute("""UPDATE vacancies SET title=?, company=COALESCE(?,company),
                           description_text=?, score=?, level=?, stack=?, role=?,
                           reason=?, updated_at=? WHERE id=?""",
                           (title, company, description_text, result.score, result.level,
                            result.stack, result.role, result.reason, now, vacancy_id))
            elif company and company != old["company"]:
                db.execute("UPDATE vacancies SET company=? WHERE id=?", (company, vacancy_id))
            inserted = False
            posting_id = post["id"]
        else:
            vacancy_id = duplicate_vacancy(db, FeedItem(item.source, item.source_key, item.original_url,
                                                         item.canonical_url, title, company,
                                                         item.summary_html, item.published_at), description_text)
            inserted = vacancy_id is None
            if inserted:
                result = assess(title, description_text, work_format)
                cursor = db.execute("""INSERT INTO vacancies
                    (title, company, description_text, score, level, stack, role,
                     reason, status, published_at, first_seen_at, updated_at, excluded, work_format, location)
                    VALUES (?,?,?,?,?,?,?,?,'New',?,?,?,?,?,?)""",
                    (title, company, description_text, result.score, result.level,
                     result.stack, result.role, result.reason, item.published_at, now, now,
                     int(excluded_role(title)), work_format, location))
                vacancy_id = cursor.lastrowid
                if result.score > 25 and not excluded_role(title):
                    db.execute("INSERT INTO mail_notifications(vacancy_id,queued_at) VALUES (?,?)",
                               (vacancy_id, now))
            else:
                old = db.execute("SELECT * FROM vacancies WHERE id=?", (vacancy_id,)).fetchone()
                effective_work_format = work_format or old["work_format"]
                result = assess(old["title"], old["description_text"], effective_work_format)
                db.execute("""UPDATE vacancies SET work_format=COALESCE(?,work_format),
                           location=COALESCE(?,location), score=?, level=?, stack=?, role=?, reason=?,
                           updated_at=? WHERE id=?""",
                           (work_format, location, result.score, result.level, result.stack, result.role,
                            result.reason, now, vacancy_id))
            cursor = db.execute("""INSERT INTO postings
                (vacancy_id, source, source_key, original_url, canonical_url, first_seen_at,
                 last_seen_at, detail_checked_at, metadata_checked_at, detail_ok) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (vacancy_id, item.source, item.source_key, item.original_url, item.canonical_url,
                 now, now, now if detail_checked else None, now if detail_checked and detail_html else None,
                 int(bool(detail_html))))
            posting_id = cursor.lastrowid
        db.execute("INSERT OR IGNORE INTO posting_searches(posting_id, search_key) VALUES (?,?)",
                   (posting_id, search_key))
        return inserted


def list_vacancies(path: Path) -> list[dict]:
    with connection(path) as db:
        rows = db.execute("""SELECT v.*, p.original_url,
            (SELECT group_concat(DISTINCT source) FROM postings WHERE vacancy_id=v.id) AS sources
            FROM vacancies v JOIN postings p ON p.id=(SELECT MIN(id) FROM postings WHERE vacancy_id=v.id)
            WHERE v.excluded=0
            ORDER BY CASE v.status WHEN 'New' THEN 0 WHEN 'Interested' THEN 1 WHEN 'Applied' THEN 2
                WHEN 'Viewed' THEN 3 WHEN 'Postponed' THEN 4 WHEN 'Rejected' THEN 5
                WHEN 'Deleted' THEN 6 WHEN 'Irrelevant' THEN 7 ELSE 8 END,
              v.score DESC,
              v.published_at IS NULL, v.published_at DESC, v.first_seen_at DESC, v.id DESC""").fetchall()
        return [dict(row) for row in rows]


def counts(path: Path) -> dict[str, int]:
    with connection(path) as db:
        rows = db.execute("SELECT status, count(*) n FROM vacancies WHERE excluded=0 GROUP BY status").fetchall()
        result = {status: 0 for status in STATUSES}
        result.update({row["status"]: row["n"] for row in rows})
        return result


def pending_notifications(path: Path) -> list[dict]:
    with connection(path) as db:
        rows = db.execute("""SELECT v.id, v.title, v.score, p.original_url
            FROM mail_notifications n JOIN vacancies v ON v.id=n.vacancy_id
            JOIN postings p ON p.id=(SELECT MIN(id) FROM postings WHERE vacancy_id=v.id)
            WHERE n.sent_at IS NULL AND v.status='New' AND v.excluded=0 AND v.score>25
            ORDER BY v.score DESC, v.published_at DESC, v.id DESC""").fetchall()
        return [dict(row) for row in rows]


def mark_notifications_sent(path: Path, vacancy_ids: list[int]) -> None:
    now = utc_now()
    with connection(path) as db:
        for vacancy_id in vacancy_ids:
            db.execute("UPDATE mail_notifications SET sent_at=? WHERE vacancy_id=? AND sent_at IS NULL",
                       (now, vacancy_id))
            db.execute("UPDATE vacancies SET status='Viewed',updated_at=? WHERE id=? AND status='New'",
                       (now, vacancy_id))


def set_status(path: Path, vacancy_id: int, status: str) -> bool:
    if status not in STATUSES:
        raise ValueError("Неизвестный статус")
    with connection(path) as db:
        cursor = db.execute("UPDATE vacancies SET status=?, updated_at=? WHERE id=?",
                            (status, utc_now(), vacancy_id))
        return cursor.rowcount > 0


def feed_state(path: Path, key: str) -> dict:
    with connection(path) as db:
        row = db.execute("SELECT * FROM feed_state WHERE search_key=?", (key,)).fetchone()
        return dict(row) if row else {}


def save_feed_state(path: Path, key: str, etag: str | None, modified: str | None, error: str | None):
    with connection(path) as db:
        db.execute("""INSERT INTO feed_state(search_key,etag,last_modified,checked_at,last_error)
            VALUES (?,?,?,?,?) ON CONFLICT(search_key) DO UPDATE SET
            etag=COALESCE(excluded.etag,feed_state.etag),
            last_modified=COALESCE(excluded.last_modified,feed_state.last_modified),
            checked_at=excluded.checked_at,last_error=excluded.last_error""",
            (key, etag, modified, utc_now(), error))


def begin_run(path: Path, trigger_type: str) -> int:
    with connection(path) as db:
        return db.execute("INSERT INTO runs(trigger_type,started_at) VALUES (?,?)",
                          (trigger_type, utc_now())).lastrowid


def end_run(path: Path, run_id: int, found: int, new: int, errors: list[str]):
    with connection(path) as db:
        db.execute("""UPDATE runs SET finished_at=?, found_count=?, new_count=?, error_count=?, details=?
            WHERE id=?""", (utc_now(), found, new, len(errors), "\n".join(errors)[:10000], run_id))


def latest_run(path: Path) -> dict | None:
    with connection(path) as db:
        row = db.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()
        return dict(row) if row else None
