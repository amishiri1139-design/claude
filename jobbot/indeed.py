"""Indeed Apply ("Easily apply") flow.

Jobs that send you to the company's own site are skipped; only applications
that stay on Indeed (smartapply.indeed.com) are automated.
"""
from __future__ import annotations

import logging
import re
from typing import Iterator
from urllib.parse import urlencode

from playwright.sync_api import Page

from .answerer import Job
from .base import Site, StopSite

log = logging.getLogger("jobbot")

SEL = {
    "card": "[data-jk]",
    "title": "h1, [data-testid='jobsearch-JobInfoHeader-title']",
    "company": "[data-testid='inlineHeader-companyName'], [data-company-name], "
               ".jobsearch-CompanyInfoContainer a",
    "location": "[data-testid='inlineHeader-companyLocation'], [data-testid='job-location'], "
                "[data-testid='jobsearch-JobInfoHeader-companyLocation']",
    "description": "#jobDescriptionText",
    "apply": "#indeedApplyButton, button.indeed-apply-button, "
             "button:has-text('Apply now'), button:has-text('Easily apply')",
}
SUBMIT_RE = re.compile(r"submit (your )?application", re.I)
NEXT_RE = re.compile(r"^(continue|next|review( your application)?|save and continue)\b", re.I)
DONE_RE = re.compile(r"application (has been )?submitted|your application has been sent", re.I)


def _blocked(page: Page) -> bool:
    try:
        return ("Just a moment" in page.title()
                or page.locator("iframe[src*='challenges.cloudflare.com'], iframe[src*='hcaptcha'], "
                                "iframe[src*='recaptcha']").count() > 0)
    except Exception:
        return False


class Indeed(Site):
    name = "indeed"

    @property
    def base(self) -> str:
        return "https://" + self.s.get("indeed", "domain", default="www.indeed.com")

    def ensure_logged_in(self) -> None:
        p = self.page
        p.goto(self.base, wait_until="domcontentloaded")
        p.wait_for_timeout(2500)
        self.wait_for_human(p, "Indeed verification", lambda: _blocked(p))
        signed_out = p.locator("a:has-text('Sign in'), a[href*='/account/login']")
        if signed_out.count() and signed_out.first.is_visible():
            raise StopSite("Not logged in to Indeed. Run:  python -m jobbot login")

    # ------------------------------------------------------------- discovery
    def iter_jobs(self, search: dict) -> Iterator[Job]:
        p = self.page
        days = self.s.get("limits", "posted_within_days", default=7)
        for page_no in range(self.s.get("limits", "pages_per_search", default=5)):
            loc = "Remote" if search.get("remote_only") else search.get("location", "")
            params = {"q": search["keywords"], "l": loc, "fromage": days, "sort": "date",
                      "start": page_no * 10}
            params.update(self.s.get("indeed", "extra_params", default={}) or {})
            p.goto(f"{self.base}/jobs?{urlencode(params)}", wait_until="domcontentloaded")
            p.wait_for_timeout(3000)
            self.wait_for_human(p, "Indeed verification", lambda: _blocked(p))
            ids: list[str] = []
            cards = p.locator(SEL["card"])
            for i in range(cards.count()):
                jk = cards.nth(i).get_attribute("data-jk")
                if jk and jk not in ids:
                    ids.append(jk)
            log.info("indeed: '%s' page %s → %s jobs", search["keywords"], page_no + 1, len(ids))
            if not ids:
                return
            for jk in ids:
                if self.db.seen(self.name, jk):
                    continue
                job = self._load_job(jk)
                if job:
                    yield job

    def _text(self, selector: str) -> str:
        loc = self.page.locator(selector)
        return " ".join(loc.first.inner_text().split()) if loc.count() else ""

    def _load_job(self, jk: str) -> Job | None:
        url = f"{self.base}/viewjob?jk={jk}"
        self.page.goto(url, wait_until="domcontentloaded")
        self.page.wait_for_timeout(2000)
        self.wait_for_human(self.page, "Indeed verification", lambda: _blocked(self.page))
        if not self.page.locator(SEL["description"]).count():
            return None
        return Job(self.name, jk, self._text(SEL["title"]), self._text(SEL["company"]),
                   self._text(SEL["location"]), url, self._text(SEL["description"]))

    # ------------------------------------------------------------------ apply
    def apply(self, job: Job) -> tuple[str, str]:
        p = self.page
        btn = p.locator(SEL["apply"])
        visible = [btn.nth(i) for i in range(btn.count()) if btn.nth(i).is_visible()]
        if not visible:
            return "skipped", "no Indeed Apply button (company-site application)"
        if re.search(r"applied", visible[0].inner_text(), re.I):
            return "skipped", "already applied"

        # Indeed Apply opens either a new tab or navigates the current one.
        pages_before = len(self.ctx.pages)
        visible[0].click()
        p.wait_for_timeout(4000)
        form = self.ctx.pages[-1] if len(self.ctx.pages) > pages_before else p
        try:
            form.wait_for_load_state("domcontentloaded", timeout=15000)
            return self._walk(form, job)
        finally:
            if form is not p:
                form.close()

    def _walk(self, form: Page, job: Job) -> tuple[str, str]:
        last_sig = ""
        stuck = 0
        for step in range(20):
            form.wait_for_timeout(2000)
            self.wait_for_human(form, "Indeed verification", lambda: _blocked(form))
            body = form.locator("body")
            if DONE_RE.search(body.inner_text()[:5000]):
                return "applied", ""

            res = self.filler.fill(body, job)
            if res.unsure:
                return "skipped", "unsure: " + "; ".join(res.unsure)[:250]

            buttons = form.get_by_role("button")
            submit = buttons.filter(has_text=SUBMIT_RE)
            if submit.count() and submit.first.is_visible():
                if self.s.dry_run:
                    return "dry_run", f"filled {step + 1} page(s), not submitted"
                submit.first.click()
                form.wait_for_timeout(5000)
                if DONE_RE.search(form.locator("body").inner_text()[:5000]) or "post-apply" in form.url:
                    return "applied", ""
                return "applied", "submitted (no confirmation text seen)"

            nxt = [b for b in (buttons.nth(i) for i in range(buttons.count()))
                   if b.is_visible() and NEXT_RE.search(b.inner_text().strip())]
            if not nxt:
                return "failed", f"no Continue/Submit button on {form.url[:120]}"
            nxt[0].click()
            self.s.pause()

            sig = form.url + str(len(form.locator("input, select, textarea").all()))
            stuck = stuck + 1 if sig == last_sig else 0
            last_sig = sig
            if stuck >= 2:
                err = form.locator("[role='alert'], [class*='error']")
                msg = err.first.inner_text().strip() if err.count() else "page did not advance"
                return "failed", f"stuck: {msg}"[:250]
        return "failed", "too many pages"
