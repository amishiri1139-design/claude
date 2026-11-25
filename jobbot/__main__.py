"""jobbot — automated LinkedIn Easy Apply + Indeed Apply.

    python -m jobbot login            # sign in once; session is saved locally
    python -m jobbot run              # apply (respects dry_run in config.yaml)
    python -m jobbot run --live       # really submit, overriding dry_run
    python -m jobbot run --site linkedin --limit 5
    python -m jobbot report           # counts by site/status
    python -m jobbot export out.csv   # full log as CSV
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

from .config import ROOT


def _browser(settings_cfg: dict):
    from playwright.sync_api import sync_playwright

    pw = sync_playwright().start()
    browser_cfg = settings_cfg.get("browser", {}) or {}
    ctx = pw.chromium.launch_persistent_context(
        str(ROOT / browser_cfg.get("user_data_dir", "browser_data")),
        headless=bool(browser_cfg.get("headless", False)),
        viewport={"width": 1366, "height": 900},
        slow_mo=50,
    )
    ctx.set_default_timeout(15000)
    return pw, ctx


def cmd_login(_args) -> None:
    import yaml

    cfg_path = ROOT / "config.yaml"
    cfg = yaml.safe_load(cfg_path.read_text()) if cfg_path.exists() else {}
    domain = (cfg.get("indeed") or {}).get("domain", "www.indeed.com")
    pw, ctx = _browser(cfg)
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.goto("https://www.linkedin.com/login")
    ctx.new_page().goto(f"https://{domain}/account/login")
    input("\nSign in to LinkedIn and Indeed in the browser window (both tabs).\n"
          "Then come back here and press Enter to save the session... ")
    ctx.close()
    pw.stop()
    print("Saved. You can now run:  python -m jobbot run")


def cmd_run(args) -> None:
    from .answerer import Answerer
    from .base import StopSite
    from .config import load_settings
    from .db import Tracker
    from .indeed import Indeed
    from .linkedin import LinkedIn

    settings = load_settings(dry_run_override=False if args.live else (True if args.dry_run else None))
    log = logging.getLogger("jobbot")
    log.info("Mode: %s", "DRY RUN (nothing is submitted)" if settings.dry_run else "LIVE — submitting")
    tracker, answerer = Tracker(), Answerer(settings)
    budget = args.limit or settings.get("limits", "max_applications_per_run", default=25)

    enabled = settings.get("sites", default={}) or {}
    sites = [s for s in (LinkedIn, Indeed)
             if (args.site in (None, "all") and enabled.get(s.name, True)) or args.site == s.name]

    pw, ctx = _browser(settings.config)
    total = 0
    try:
        for SiteCls in sites:
            if total >= budget:
                break
            site = SiteCls(ctx, settings, tracker, answerer)
            try:
                total += site.run(budget - total)
            except StopSite as e:
                log.warning("%s stopped: %s", SiteCls.name, e)
    except KeyboardInterrupt:
        log.info("Interrupted.")
    finally:
        ctx.close()
        pw.stop()
    log.info("Done. %s application(s) %s. Claude calls: %s",
             total, "dry-run" if settings.dry_run else "submitted", answerer.uses_claude)
    cmd_report(args)


def cmd_report(_args) -> None:
    from .db import Tracker

    rows = Tracker().summary()
    if not rows:
        print("No jobs recorded yet.")
        return
    print(f"\n{'site':<10}{'status':<10}{'count':>6}")
    for site, status, n in rows:
        print(f"{site:<10}{status:<10}{n:>6}")


def cmd_export(args) -> None:
    from .db import Tracker

    out = Path(args.path)
    print(f"Wrote {Tracker().export_csv(out)} rows to {out}")


def main() -> None:
    ap = argparse.ArgumentParser(prog="jobbot", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-v", "--verbose", action="store_true", help="log every question/answer")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("login").set_defaults(fn=cmd_login)
    r = sub.add_parser("run")
    r.add_argument("--site", choices=["linkedin", "indeed", "all"])
    r.add_argument("--limit", type=int, help="max applications this run")
    mode = r.add_mutually_exclusive_group()
    mode.add_argument("--live", action="store_true", help="submit for real")
    mode.add_argument("--dry-run", action="store_true", help="fill but don't submit")
    r.set_defaults(fn=cmd_run)
    sub.add_parser("report").set_defaults(fn=cmd_report)
    e = sub.add_parser("export")
    e.add_argument("path", nargs="?", default=f"applications-{datetime.now():%Y%m%d}.csv")
    e.set_defaults(fn=cmd_export)
    args = ap.parse_args()

    logs = ROOT / "logs"
    logs.mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout),
                  logging.FileHandler(logs / f"jobbot-{datetime.now():%Y%m%d}.log", encoding="utf-8")],
    )
    for noisy in ("httpx", "anthropic"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    args.fn(args)


if __name__ == "__main__":
    main()
