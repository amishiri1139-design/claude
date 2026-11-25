"""Generic form filler: finds every visible field in a container and fills it."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from playwright.sync_api import Locator

from .answerer import Answerer, Job

log = logging.getLogger("jobbot")

PLACEHOLDER_OPTIONS = {"", "select an option", "select", "choose", "--", "please select",
                       "select...", "choose an option"}

# Returns the human-readable question for a form control.
LABEL_JS = r"""
el => {
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  if (el.id) {
    const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
    if (l && clean(l.innerText)) return clean(l.innerText);
  }
  const lb = el.getAttribute('aria-labelledby');
  if (lb) {
    const t = lb.split(/\s+/).map(i => document.getElementById(i)?.innerText || '').join(' ');
    if (clean(t)) return clean(t);
  }
  if (el.getAttribute('aria-label')) return clean(el.getAttribute('aria-label'));
  const wrap = el.closest('label');
  if (wrap && clean(wrap.innerText)) return clean(wrap.innerText);
  const fs = el.closest('fieldset');
  if (fs) { const lg = fs.querySelector('legend'); if (lg && clean(lg.innerText)) return clean(lg.innerText); }
  let p = el.parentElement;
  for (let i = 0; i < 4 && p; i++, p = p.parentElement) {
    const t = p.querySelector('label, legend, [class*="label"], [class*="question"]');
    if (t && clean(t.innerText)) return clean(t.innerText);
  }
  return clean(el.placeholder || el.name || '');
}
"""

# Returns {question, options[], checked} for a radio group, given one radio input.
RADIO_GROUP_JS = r"""
el => {
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const root = el.closest('fieldset, [role="radiogroup"]') || el.form || document;
  const radios = [...root.querySelectorAll(`input[type="radio"][name="${CSS.escape(el.name)}"]`)];
  const labelOf = r => {
    const l = r.id && document.querySelector(`label[for="${CSS.escape(r.id)}"]`);
    return clean((l && l.innerText) || r.getAttribute('aria-label') || r.closest('label')?.innerText || r.value);
  };
  let q = '';
  const fs = el.closest('fieldset');
  if (fs && fs.querySelector('legend')) q = clean(fs.querySelector('legend').innerText);
  if (!q) { const rg = el.closest('[role="radiogroup"]');
            if (rg) q = clean(rg.getAttribute('aria-label') ||
                        document.getElementById(rg.getAttribute('aria-labelledby'))?.innerText); }
  if (!q) { let p = root.parentElement || root;
            const t = p.querySelector('label:not([for]), legend, span, [class*="question"]');
            q = clean(t && t.innerText); }
  return { question: q, options: radios.map(labelOf), checked: radios.some(r => r.checked),
           required: radios.some(r => r.required || r.getAttribute('aria-required') === 'true') };
}
"""

REQUIRED_JS = """el => el.required || el.getAttribute('aria-required') === 'true'"""

AGREE_WORDS = ("agree", "acknowledge", "certify", "consent", "confirm", "terms", "privacy")


@dataclass
class FillResult:
    filled: int = 0
    unsure: list[str] = field(default_factory=list)   # required questions we couldn't answer


class FormFiller:
    def __init__(self, answerer: Answerer, resume_path: str, skip_when_unsure: bool = True):
        self.a = answerer
        self.resume_path = resume_path
        self.skip_when_unsure = skip_when_unsure

    def fill(self, root: Locator, job: Optional[Job]) -> FillResult:
        res = FillResult()
        self._files(root, res)
        self._text_fields(root, job, res)
        self._selects(root, job, res)
        self._radios(root, job, res)
        self._checkboxes(root, job, res)
        return res

    # ---------------------------------------------------------------- helpers
    def _visible(self, loc: Locator) -> list[Locator]:
        return [loc.nth(i) for i in range(loc.count()) if loc.nth(i).is_visible()]

    def _take(self, question: str, ftype: str, options, job, required: bool,
              res: FillResult) -> Optional[str]:
        if not question:
            return None
        value, confident = self.a.answer(question, ftype, options, job)
        log.debug("Q: %s -> %r (confident=%s)", question, value, confident)
        if value in (None, "") or (not confident and self.skip_when_unsure):
            if required:
                res.unsure.append(question)
            return None    # leave optional fields blank rather than guess
        return value

    # ----------------------------------------------------------------- fields
    def _files(self, root: Locator, res: FillResult) -> None:
        for el in [root.locator("input[type=file]").nth(i)
                   for i in range(root.locator("input[type=file]").count())]:
            label = (el.evaluate(LABEL_JS) or "").lower()
            name = (el.get_attribute("name") or el.get_attribute("id") or "").lower()
            if "cover" in label or "cover" in name:
                continue
            if any(w in label + name for w in ("resume", "cv", "upload", "file")):
                try:
                    if el.evaluate("e => e.files && e.files.length") == 0:
                        el.set_input_files(self.resume_path)
                        res.filled += 1
                except Exception as e:   # some upload widgets reject programmatic files
                    log.debug("resume upload failed: %s", e)

    def _text_fields(self, root: Locator, job, res: FillResult) -> None:
        sel = ("input:not([type=hidden]):not([type=file]):not([type=checkbox])"
               ":not([type=radio]):not([type=submit]):not([type=button]), textarea")
        for el in self._visible(root.locator(sel)):
            try:
                if el.input_value().strip() or not el.is_editable():
                    continue
                q = el.evaluate(LABEL_JS)
                ftype = "textarea" if el.evaluate("e => e.tagName") == "TEXTAREA" else (
                    el.get_attribute("type") or "text")
                value = self._take(q, ftype, None, job, el.evaluate(REQUIRED_JS), res)
                if value is None:
                    continue
                if ftype == "number":
                    value = "".join(c for c in value if c.isdigit() or c == ".") or "0"
                el.fill(value)
                if el.get_attribute("role") == "combobox" or el.get_attribute("aria-autocomplete"):
                    el.page.wait_for_timeout(1200)    # typeahead (e.g. city): pick first suggestion
                    el.press("ArrowDown")
                    el.press("Enter")
                res.filled += 1
            except Exception as e:
                log.debug("text field error: %s", e)

    def _selects(self, root: Locator, job, res: FillResult) -> None:
        for el in self._visible(root.locator("select")):
            try:
                current = el.evaluate("e => e.options[e.selectedIndex]?.text || ''").strip()
                if current.lower() not in PLACEHOLDER_OPTIONS:
                    continue
                options = [o.strip() for o in el.evaluate("e => [...e.options].map(o => o.text)")
                           if o.strip().lower() not in PLACEHOLDER_OPTIONS]
                value = self._take(el.evaluate(LABEL_JS), "select", options, job,
                                   el.evaluate(REQUIRED_JS), res)
                if value and value in options:
                    el.select_option(label=value)
                    res.filled += 1
            except Exception as e:
                log.debug("select error: %s", e)

    def _radios(self, root: Locator, job, res: FillResult) -> None:
        done: set[str] = set()
        radios = root.locator("input[type=radio]")
        for i in range(radios.count()):
            el = radios.nth(i)
            name = el.get_attribute("name") or ""
            if not name or name in done:
                continue
            done.add(name)
            try:
                info = el.evaluate(RADIO_GROUP_JS)
                if info["checked"]:
                    continue
                value = self._take(info["question"], "radio", info["options"], job,
                                   True, res)
                if value and value in info["options"]:
                    idx = info["options"].index(value)
                    target = root.locator(f'input[type=radio][name="{name}"]').nth(idx)
                    rid = target.get_attribute("id")
                    lbl = root.locator(f'label[for="{rid}"]') if rid else None
                    (lbl if lbl is not None and lbl.count() else target).first.click(force=True)
                    res.filled += 1
            except Exception as e:
                log.debug("radio error: %s", e)

    def _checkboxes(self, root: Locator, job, res: FillResult) -> None:
        boxes = root.locator("input[type=checkbox]")
        for i in range(boxes.count()):
            el = boxes.nth(i)
            try:
                if el.is_checked():
                    continue
                label = (el.evaluate(LABEL_JS) or "").lower()
                if "follow" in label:          # e.g. "Follow <company>" — leave unchecked
                    continue
                required = el.evaluate(REQUIRED_JS)
                if any(w in label for w in AGREE_WORDS) or required:
                    rid = el.get_attribute("id")
                    lbl = root.locator(f'label[for="{rid}"]') if rid else None
                    (lbl if lbl is not None and lbl.count() else el).first.click(force=True)
                    res.filled += 1
            except Exception as e:
                log.debug("checkbox error: %s", e)
