"""SQLite log of every job seen, so nothing is applied to twice."""
from __future__ import annotations

import csv
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .config import ROOT

DB_PATH = ROOT / "applications.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    site        TEXT NOT NULL,
    job_id      TEXT NOT NULL,
    title       TEXT,
    company     TEXT,
    location    TEXT,
    url         TEXT,
    status      TEXT NOT NULL,   -- applied | dry_run | skipped | failed
    reason      TEXT,
    fit_score   INTEGER,
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (site, job_id)
);
"""


class Tracker:
    def __init__(self, path: Path = DB_PATH):
        self.conn = sqlite3.connect(path)
        self.conn.execute(SCHEMA)
        self.conn.commit()

    def seen(self, site: str, job_id: str) -> bool:
        """True if we already applied, or decided to skip, this job."""
        row = self.conn.execute(
            "SELECT status FROM jobs WHERE site=? AND job_id=?", (site, job_id)
        ).fetchone()
        # Failed attempts and dry runs may be retried on a later run.
        return row is not None and row[0] in ("applied", "skipped")

    def record(self, site: str, job_id: str, status: str, *, title: str = "",
               company: str = "", location: str = "", url: str = "",
               reason: str = "", fit_score: int | None = None) -> None:
        self.conn.execute(
            """INSERT INTO jobs (site, job_id, title, company, location, url, status,
                                 reason, fit_score, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(site, job_id) DO UPDATE SET
                 status=excluded.status, reason=excluded.reason,
                 fit_score=COALESCE(excluded.fit_score, jobs.fit_score),
                 updated_at=excluded.updated_at""",
            (site, job_id, title, company, location, url, status, reason, fit_score,
             datetime.now(timezone.utc).isoformat(timespec="seconds")),
        )
        self.conn.commit()

    def applied_today(self, site: str) -> int:
        today = datetime.now(timezone.utc).date().isoformat()
        return self.conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE site=? AND status='applied' AND updated_at LIKE ?",
            (site, f"{today}%"),
        ).fetchone()[0]

    def summary(self) -> list[tuple]:
        return self.conn.execute(
            "SELECT site, status, COUNT(*) FROM jobs GROUP BY site, status ORDER BY site, status"
        ).fetchall()

    def export_csv(self, out: Path) -> int:
        cur = self.conn.execute("SELECT * FROM jobs ORDER BY updated_at DESC")
        cols = [d[0] for d in cur.description]
        rows = cur.fetchall()
        with out.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(cols)
            w.writerows(rows)
        return len(rows)
