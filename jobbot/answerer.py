"""Answers application questions: profile rules first, Claude as fallback.

Every answer Claude gives is saved to learned_answers.json. You can open that
file and correct any answer; corrected answers are reused verbatim next time.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Optional

import anthropic
import yaml
from pydantic import BaseModel, Field

from .config import ROOT, Settings

LEARNED_PATH = ROOT / "learned_answers.json"
FALLBACK_BETA = "server-side-fallback-2026-07-01"


@dataclass
class Job:
    site: str
    job_id: str
    title: str
    company: str
    location: str
    url: str
    description: str


class _Answer(BaseModel):
    answer: str = Field(description="The exact value to enter. For choice questions, "
                                    "one of the provided options copied exactly.")
    confident: bool = Field(description="False if the profile/resume doesn't support "
                                        "an answer and you had to guess.")


class _Fit(BaseModel):
    score: int = Field(description="0-100 match between candidate and job")
    reason: str = Field(description="One sentence")


class _Letter(BaseModel):
    text: str


def _yes_no(value: bool) -> str:
    return "Yes" if value else "No"


class Answerer:
    def __init__(self, settings: Settings):
        self.s = settings
        self.p = settings.profile
        self.model = settings.get("claude", "model", default="claude-opus-5-5")
        self.client = anthropic.Anthropic()
        self.learned: dict[str, str] = (
            json.loads(LEARNED_PATH.read_text(encoding="utf-8")) if LEARNED_PATH.exists() else {}
        )
        self.uses_claude = 0
        # Stable system prompt (profile + resume) so prompt caching kicks in.
        self.system = [{
            "type": "text",
            "text": (
                "You fill in job applications on behalf of the candidate below. Answer "
                "truthfully from the profile and resume only; never invent degrees, "
                "employers, certifications or clearances. Be concise: numeric fields get a "
                "bare number, short fields a few words.\n\n"
                f"<profile>\n{yaml.safe_dump(self.p, sort_keys=False)}</profile>\n\n"
                f"<resume>\n{settings.resume_text}\n</resume>"
            ),
            "cache_control": {"type": "ephemeral"},
        }]

    # ------------------------------------------------------------------ Claude
    def _ask(self, prompt: str, schema: type[BaseModel], effort: str = "low",
             max_tokens: int = 4000):
        self.uses_claude += 1
        resp = self.client.beta.messages.parse(
            model=self.model,
            max_tokens=max_tokens,
            system=self.system,
            messages=[{"role": "user", "content": prompt}],
            output_format=schema,
            output_config={"effort": effort},
            betas=[FALLBACK_BETA],
            fallbacks="default",   # re-run on a fallback model if a request is declined
        )
        if resp.stop_reason == "refusal" or resp.parsed_output is None:
            return None
        return resp.parsed_output

    def fit_score(self, job: Job) -> tuple[int, str]:
        out = self._ask(
            f"Rate how well the candidate fits this job (0-100). Consider title, seniority, "
            f"required skills, location/remote and work authorization.\n\n"
            f"Title: {job.title}\nCompany: {job.company}\nLocation: {job.location}\n\n"
            f"{job.description[:12000]}",
            _Fit,
        )
        return (out.score, out.reason) if out else (0, "no score")

    def cover_letter(self, job: Job) -> str:
        out = self._ask(
            f"Write a cover letter (under 250 words, plain text, no placeholders, signed "
            f"with the candidate's name) for:\nTitle: {job.title}\nCompany: {job.company}\n\n"
            f"{job.description[:12000]}",
            _Letter, effort="medium",
        )
        return out.text if out else ""

    # ---------------------------------------------------------------- answers
    def answer(self, question: str, field_type: str, options: Optional[list[str]] = None,
               job: Optional[Job] = None) -> tuple[Optional[str], bool]:
        """Return (answer, confident). answer=None means 'leave it'."""
        q = " ".join(question.split())
        key = f"{q} || {' | '.join(options or [])}"

        for needle, value in (self.p.get("custom_answers") or {}).items():
            if needle.lower() in q.lower():
                return self._match_option(str(value), options), True
        if key in self.learned:
            return self.learned[key], True

        rule = self._rule(q.lower(), field_type)
        if rule is not None:
            return self._match_option(rule, options), True

        if "cover letter" in q.lower() and field_type == "textarea" and job:
            return self.cover_letter(job), True

        prompt = f"Application question ({field_type}): {q}\n"
        if options:
            prompt += "Options:\n" + "\n".join(f"- {o}" for o in options) + "\n"
        if job:
            prompt += f"\nJob: {job.title} at {job.company}\n{job.description[:4000]}"
        out = self._ask(prompt, _Answer, effort="medium" if field_type == "textarea" else "low")
        if out is None:
            return None, False
        value = self._match_option(out.answer, options)
        if out.confident:
            self.learned[key] = value
            LEARNED_PATH.write_text(json.dumps(self.learned, indent=2, ensure_ascii=False),
                                    encoding="utf-8")
        return value, out.confident

    @staticmethod
    def _match_option(value: str, options: Optional[list[str]]) -> str:
        if not options:
            return value
        v = value.strip().lower()
        for o in options:
            if o.strip().lower() == v:
                return o
        for o in options:
            if v and (v in o.lower() or o.lower() in v):
                return o
        return value

    def _rule(self, q: str, field_type: str) -> Optional[str]:
        """Deterministic answers from profile.yaml for the common questions."""
        per, work = self.p.get("personal", {}), self.p.get("work", {})
        elig, edu = self.p.get("eligibility", {}), self.p.get("education", {})
        demo = self.p.get("demographics", {})

        simple = [
            (r"\bfirst name", per.get("first_name")),
            (r"\blast name|surname|family name", per.get("last_name")),
            (r"^(full )?name$|your name", f"{per.get('first_name','')} {per.get('last_name','')}".strip()),
            (r"e-?mail", per.get("email")),
            (r"country code", per.get("phone_country_code")),
            (r"phone|mobile", per.get("phone")),
            (r"\bcity\b|location \(city\)", per.get("city")),
            (r"\bstate\b|province", per.get("state")),
            (r"zip|postal", per.get("zip")),
            (r"linkedin", per.get("linkedin_url")),
            (r"github", per.get("github_url")),
            (r"portfolio|website|personal site", per.get("portfolio_url")),
            (r"salary|compensation|pay expectation", work.get("desired_salary")),
            (r"notice period", work.get("notice_period")),
            (r"start date|when can you start|available to start", work.get("earliest_start_date")),
            (r"current (job )?title", work.get("current_title")),
            (r"current (company|employer)", work.get("current_company")),
            (r"highest (level of )?(education|degree)", edu.get("highest_degree")),
            (r"\bgender\b", demo.get("gender")),
            (r"race|ethnicity|hispanic", demo.get("race_ethnicity")),
            (r"veteran", demo.get("veteran_status")),
            (r"disabilit", demo.get("disability_status")),
        ]
        for pattern, value in simple:
            if re.search(pattern, q) and value not in (None, ""):
                return str(value)

        if re.search(r"sponsor", q):
            return _yes_no(bool(elig.get("requires_sponsorship")))
        if re.search(r"authori[sz]ed|legally (able|eligible|permitted)|right to work", q):
            return _yes_no(bool(elig.get("authorized_to_work", True)))
        if "relocat" in q:
            return _yes_no(bool(work.get("willing_to_relocate")))
        if "background check" in q:
            return _yes_no(bool(elig.get("willing_background_check", True)))
        if "drug" in q:
            return _yes_no(bool(elig.get("willing_drug_test", True)))
        if "driver" in q and "licen" in q:
            return _yes_no(bool(elig.get("has_drivers_license", True)))
        if re.search(r"\bremote\b", q) and "?" in q:
            return _yes_no(bool(work.get("open_to_remote", True)))
        if re.search(r"commut|on-?site|in (the )?office|hybrid", q):
            return _yes_no(bool(work.get("open_to_onsite", True) or work.get("open_to_hybrid", True)))

        if re.search(r"how many years|years of (work )?experience", q):
            for skill, years in (self.p.get("skill_years") or {}).items():
                if re.search(rf"\b{re.escape(skill.lower())}\b", q):
                    return str(years)
            if re.search(r"(total|overall|professional|work) experience|years of experience\??$", q):
                return str(work.get("years_of_experience", 0))
        return None
