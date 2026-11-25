# jobbot: automatic LinkedIn and Indeed applications

jobbot searches LinkedIn (Easy Apply) and Indeed (Indeed Apply) for the jobs you
choose. It scores each job against your resume with Claude, then fills in and
submits the application, including the screening questions. Every job it touches
is logged in `applications.db`, so it never applies to the same job twice.

> ⚠️ **Read this first.** LinkedIn and Indeed both prohibit automated activity
> in their terms of service. Using this tool can get your account restricted or
> banned. You run it at your own risk. To lower the risk:
> - Keep the daily caps low (the defaults are 25 per run and 40 per site per day).
> - Keep the random delays.
> - Use the browser window yourself whenever a captcha or security check appears.
>
> The tool never tries to get around captchas. It pauses and waits for you.

## What it does

| Step | How |
|---|---|
| Find jobs | Your saved searches (`config.yaml`). Posted in the last N days, Easy Apply / Indeed Apply only |
| Filter | Excluded title keywords and companies, plus a Claude **fit score** (0–100) against your resume |
| Fill forms | Contact details, resume upload, dropdowns, radio buttons, checkboxes and free-text questions |
| Answer questions | 1) your `custom_answers`, 2) answers it learned earlier, 3) rules from `profile.yaml`, 4) Claude |
| Be honest | If Claude can't answer a **required** question from your resume or profile (for example "Do you have a security clearance?"), the job is **skipped**, not guessed |
| Track | `applications.db`, `python -m jobbot report`, `python -m jobbot export` |

## Setup (on your own computer)

You need Python 3.10+ and an [Anthropic API key](https://console.anthropic.com/).

```bash
git clone <this repo> && cd claude
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium

cp config.example.yaml config.yaml       # searches, limits, filters
cp profile.example.yaml profile.yaml     # your details and standard answers
# put your resume at resumes/resume.pdf (or .docx) and set resume_file in profile.yaml

export ANTHROPIC_API_KEY=sk-ant-...      # Windows: set ANTHROPIC_API_KEY=...
```

Your config, profile, resume, browser session and database are all git-ignored.
They stay on your machine.

## Use it

```bash
python -m jobbot login                  # a browser opens: sign in to LinkedIn and Indeed, then press Enter
python -m jobbot run                    # first run: dry_run is on, so it fills forms but submits nothing
python -m jobbot -v run --limit 3       # verbose: shows every question and answer
python -m jobbot run --live             # submit for real
python -m jobbot run --live --site linkedin --limit 10
python -m jobbot report                 # counts: applied / skipped / failed
python -m jobbot export                 # applications-YYYYMMDD.csv
```

**Recommended first session:**
1. Run `python -m jobbot -v run --limit 3` (a dry run) and watch the browser.
2. Check the log and `learned_answers.json`. Fix any wrong answer in that file,
   or add it to `custom_answers` in `profile.yaml`.
3. Set `dry_run: false` (or pass `--live`).

To run it every day, schedule `python -m jobbot run --live` with cron (macOS or
Linux) or Task Scheduler (Windows). The computer must be on and logged in,
because the browser window opens.

## Files

```
jobbot/
  __main__.py   CLI (login / run / report / export)
  base.py       shared loop: filters, fit score, daily caps, logging to the DB
  linkedin.py   LinkedIn search + Easy Apply modal (selectors at the top)
  indeed.py     Indeed search + Indeed Apply pages (selectors at the top)
  forms.py      generic form filler (text, select, radio, checkbox, file)
  answerer.py   profile rules + Claude (claude-opus-5-5) for questions, fit score, cover letters
  config.py     loads config.yaml / profile.yaml / resume
  db.py         SQLite tracker
```

## When something breaks

Both sites change their HTML regularly. If jobs start failing with "no Easy
Apply button" or "no Continue/Submit button":
1. Run with `-v`.
2. Open the job in the browser window and inspect the button.
3. Update the matching entry in the `SEL` dictionary at the top of `linkedin.py` or `indeed.py`.

Failed and dry-run jobs are retried on the next run. Applied and skipped jobs are not.

## Cost

Each job uses about 1 Claude call for the fit score, plus 0–3 calls for new
screening questions. Repeated questions are answered for free from
`learned_answers.json`. Set `min_fit_score: 0` to skip scoring entirely.
