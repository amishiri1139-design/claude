"""LinkedIn Easy Apply.

LinkedIn changes its markup often. If something stops working, the selectors
are all at the top of this file.
"""
from __future__ import annotations

import logging
import re
from typing import Iterator
from urllib.parse import urlencode

from .answerer import Job
from .base import Site, StopSite

log = logging.getLogger("jobbot")

BASE = "https://www.linkedin.com"
SEL = {
    "card": "li[data-occludable-job-id], div[data-job-id]",
    "list": ".scaffold-layout__list, .jobs-search-results-list",
    "title": "h1",
    "company": ".job-details-jobs-unified-top-card__company-name, "
               ".jobs-unified-top-card__company-name, a[href*='/company/']",
    "location": ".job-details-jobs-unified-top-card__primary-description-container, "
                ".job-details-jobs-unified-top-card__tertiary-description-container, "
                ".jobs-unified-top-card__bullet",
    "description": "#job-details, .jobs-description__content, .jobs-box__html-content",
    "easy_apply": "button.jobs-apply-button, button[aria-label*='Easy Apply']",
    "modal": "div.jobs-easy-apply-modal, div[data-test-modal][role='dialog'], div[role='dialog']",
    "submit": "button[aria-label='Submit application'], button:has-text('Submit application')",
    "review": "button[aria-label='Review your application'], button:has-text('Review')",
    "next": "button[aria-label='Continue to next step'], button:has-text('Next')",
    "error": ".artdeco-inline-feedback--error, [data-test-form-element-error-messages]",
    "dismiss": "button[aria-label='Dismiss'], button[data-test-modal-close-btn]",
    "discard": "button[data-control-name='discard_application_confirm_btn'], "
               "button[data-test-dialog-secondary-btn], button:has-text('Discard')",
    "follow": "input#follow-company-checkbox, input[id*='follow-company']",
}


class LinkedIn(Site):
    name = "linkedin"

    def ensure_logged_in(self) -> None:
        p = self.page
        p.goto(f"{BASE}/feed/", wait_until="domcontentloaded")
        p.wait_for_timeout(2500)
        self.wait_for_human(p, "LinkedIn security check", lambda: "/checkpoint" in p.url)
        if any(x in p.url for x in ("/login", "/authwall", "/uas/", "signup")):
            raise StopSite("Not logged in to LinkedIn. Run:  python -m jobbot login")

    # ------------------------------------------------------------- discovery
    def _search_url(self, search: dict, page_no: int) -> str:
        days = self.s.get("limits", "posted_within_days", default=7)
        params = {
            "keywords": search["keywords"],
            "location": search.get("location", ""),
            "f_AL": "true",                      # Easy Apply only
            "f_TPR": f"r{int(days) * 86400}",
            "sortBy": "DD",
            "start": page_no * 25,
        }
        if search.get("remote_only"):
            params["f_WT"] = "2"
        return f"{BASE}/jobs/search/?{urlencode(params)}"

    def iter_jobs(self, search: dict) -> Iterator[Job]:
        p = self.page
        for page_no in range(self.s.get("limits", "pages_per_search", default=5)):
            p.goto(self._search_url(search, page_no), wait_until="domcontentloaded")
            p.wait_for_timeout(3000)
            # The results list lazy-loads; scroll it to render every card.
            for _ in range(8):
                p.evaluate(f"""() => {{ const l = document.querySelector("{SEL['list']}");
                                       if (l) l.scrollBy(0, 1200); else window.scrollBy(0, 1200); }}""")
                p.wait_for_timeout(600)
            ids = []
            cards = p.locator(SEL["card"])
            for i in range(cards.count()):
                jid = cards.nth(i).get_attribute("data-occludable-job-id") or \
                      cards.nth(i).get_attribute("data-job-id")
                if jid and jid not in ids:
                    ids.append(jid)
            log.info("linkedin: '%s' page %s → %s jobs", search["keywords"], page_no + 1, len(ids))
            if not ids:
                return
            for jid in ids:
                if self.db.seen(self.name, jid):
                    continue
                job = self._load_job(jid)
                if job:
                    yield job

    def _text(self, selector: str) -> str:
        loc = self.page.locator(selector)
        return " ".join(loc.first.inner_text().split()) if loc.count() else ""

    def _load_job(self, jid: str) -> Job | None:
        url = f"{BASE}/jobs/view/{jid}/"
        self.page.goto(url, wait_until="domcontentloaded")
        self.page.wait_for_timeout(2500)
        try:
            self.page.locator(SEL["description"]).first.wait_for(timeout=10000)
        except Exception:
            return None
        return Job(self.name, jid, self._text(SEL["title"]), self._text(SEL["company"]),
                   self._text(SEL["location"]), url, self._text(SEL["description"]))

    # ------------------------------------------------------------------ apply
    def apply(self, job: Job) -> tuple[str, str]:
        p = self.page
        btn = p.locator(SEL["easy_apply"]).filter(has_text=re.compile("Easy Apply", re.I))
        if not btn.count() or not btn.first.is_visible():
            if p.get_by_text(re.compile(r"Applied \d|Application submitted", re.I)).count():
                return "skipped", "already applied"
            return "skipped", "no Easy Apply button"
        btn.first.click()
        self.s.pause()

        modal = p.locator(SEL["modal"]).first
        modal.wait_for(timeout=10000)
        stuck = 0
        for step in range(15):
            res = self.filler.fill(modal, job)
            if res.unsure:
                self._discard()
                return "skipped", "unsure: " + "; ".join(res.unsure)[:250]

            submit = modal.locator(SEL["submit"])
            if submit.count() and submit.first.is_visible():
                follow = modal.locator(SEL["follow"])
                if follow.count() and follow.first.is_checked():
                    follow.first.uncheck(force=True)
                if self.s.dry_run:
                    self._discard()
                    return "dry_run", f"filled {step + 1} step(s), not submitted"
                submit.first.click()
                p.wait_for_timeout(4000)
                self._close_confirmation()
                return "applied", ""

            nxt = next((modal.locator(SEL[k]) for k in ("review", "next")
                        if modal.locator(SEL[k]).count() and modal.locator(SEL[k]).first.is_visible()),
                       None)
            if nxt is None:
                self._discard()
                return "failed", "no Next/Submit button found"
            nxt.first.click()
            self.s.pause()
            errors = modal.locator(SEL["error"])
            if errors.count() and errors.first.is_visible():
                stuck += 1
                if stuck >= 2:
                    msg = errors.first.inner_text().strip()
                    self._discard()
                    return "failed", f"form error: {msg}"[:250]
            else:
                stuck = 0
        self._discard()
        return "failed", "too many steps"

    def _discard(self) -> None:
        p = self.page
        try:
            d = p.locator(SEL["dismiss"])
            if d.count():
                d.first.click()
                p.wait_for_timeout(1000)
            c = p.locator(SEL["discard"])
            if c.count() and c.first.is_visible():
                c.first.click()
                p.wait_for_timeout(1000)
        except Exception:
            p.keyboard.press("Escape")

    def _close_confirmation(self) -> None:
        try:
            d = self.page.locator(SEL["dismiss"])
            if d.count():
                d.first.click()
        except Exception:
            pass
