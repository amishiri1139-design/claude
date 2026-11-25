"""Shared run loop for every job site."""
from __future__ import annotations

import logging
from typing import Iterator

from playwright.sync_api import BrowserContext, Page

from .answerer import Answerer, Job
from .config import Settings
from .db import Tracker
from .forms import FormFiller

log = logging.getLogger("jobbot")


class StopSite(Exception):
    """Raised to stop working on a site (daily cap, logged out, blocked...)."""


class Site:
    name = "site"

    def __init__(self, ctx: BrowserContext, settings: Settings, tracker: Tracker,
                 answerer: Answerer):
        self.ctx = ctx
        self.s = settings
        self.db = tracker
        self.a = answerer
        self.filler = FormFiller(answerer, str(settings.resume_path),
                                 settings.get("claude", "skip_when_unsure", default=True))
        self.page: Page = ctx.pages[0] if ctx.pages else ctx.new_page()

    # Implemented by subclasses ------------------------------------------------
    def ensure_logged_in(self) -> None: ...
    def iter_jobs(self, search: dict) -> Iterator[Job]: ...
    def apply(self, job: Job) -> tuple[str, str]:
        """Return (status, reason): status in applied | dry_run | skipped | failed."""
        ...

    # Shared logic ------------------------------------------------------------
    def wait_for_human(self, page: Page, what: str, check, timeout_s: int = 300) -> None:
        """Pause while a captcha / verification is on screen; YOU solve it in the window."""
        if not check():
            return
        log.warning("%s: %s detected. Solve it in the browser window (waiting up to %ss)...",
                    self.name, what, timeout_s)
        for _ in range(timeout_s // 2):
            page.wait_for_timeout(2000)
            if not check():
                log.info("%s: continuing.", self.name)
                return
        raise StopSite(f"{what} was not cleared")

    def passes_filters(self, job: Job) -> tuple[bool, str]:
        f = self.s.get("filters", default={}) or {}
        t = job.title.lower()
        for kw in f.get("exclude_title_keywords", []) or []:
            if kw.lower() in t:
                return False, f"title contains '{kw}'"
        for c in f.get("exclude_companies", []) or []:
            if c.lower() == job.company.lower().strip():
                return False, "excluded company"
        return True, ""

    def run(self, budget: int) -> int:
        """Apply to up to `budget` jobs. Returns how many were submitted."""
        self.ensure_logged_in()
        cap = self.s.get("limits", "max_per_site_per_day", default=40)
        min_fit = self.s.get("filters", "min_fit_score", default=0) or 0
        submitted = 0
        for search in self.s.get("searches", default=[]):
            for job in self.iter_jobs(search):
                if submitted >= budget:
                    return submitted
                if not self.s.dry_run and self.db.applied_today(self.name) >= cap:
                    log.info("%s: daily cap of %s reached.", self.name, cap)
                    return submitted
                if self.db.seen(self.name, job.job_id):
                    continue
                meta = dict(title=job.title, company=job.company, location=job.location,
                            url=job.url)
                ok, why = self.passes_filters(job)
                if not ok:
                    self.db.record(self.name, job.job_id, "skipped", reason=why, **meta)
                    continue
                score = None
                if min_fit:
                    score, why = self.a.fit_score(job)
                    if score < min_fit:
                        log.info("  skip (fit %s): %s @ %s — %s", score, job.title, job.company, why)
                        self.db.record(self.name, job.job_id, "skipped",
                                       reason=f"fit {score}: {why}", fit_score=score, **meta)
                        continue
                log.info("→ %s @ %s (fit %s)", job.title, job.company, score if score is not None else "-")
                try:
                    status, reason = self.apply(job)
                except StopSite:
                    raise
                except Exception as e:     # one bad form shouldn't end the run
                    log.exception("  error")
                    status, reason = "failed", f"{type(e).__name__}: {e}"[:300]
                self.db.record(self.name, job.job_id, status, reason=reason, fit_score=score, **meta)
                log.info("  %s %s", status.upper(), reason)
                if status in ("applied", "dry_run"):
                    submitted += 1
                    self.s.pause("between_applications")
        return submitted
