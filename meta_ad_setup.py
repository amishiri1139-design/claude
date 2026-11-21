#!/usr/bin/env python3
"""Build Meta (Facebook/Instagram) campaigns, ad sets and ads from a Google Sheet.

One row in the sheet = one ad. Campaign and ad set columns repeat on every row;
rows sharing a campaign_name / adset_name are grouped together.

Default is a DRY RUN: the sheet is validated and the plan printed, nothing is sent
to Meta. Pass --live to create objects. Everything is created PAUSED.

Environment variables:
  META_ACCESS_TOKEN   system user access token (required for --live)
  META_AD_ACCOUNT_ID  ad account id, with or without the "act_" prefix
  META_PAGE_ID        Facebook Page the ads run from
  META_IG_USER_ID     optional Instagram account id
  META_PIXEL_ID       required for SALES / LEADS objectives
  META_API_VERSION    Graph API version (default v23.0)
"""
from __future__ import annotations

import argparse
import base64
import csv
import io
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import requests

DEFAULT_API_VERSION = "v23.0"

# Friendly objective name -> (Meta campaign objective, ad set optimization goal, pixel event)
OBJECTIVES = {
    "TRAFFIC": ("OUTCOME_TRAFFIC", "LINK_CLICKS", None),
    "AWARENESS": ("OUTCOME_AWARENESS", "REACH", None),
    "ENGAGEMENT": ("OUTCOME_ENGAGEMENT", "POST_ENGAGEMENT", None),
    "SALES": ("OUTCOME_SALES", "OFFSITE_CONVERSIONS", "PURCHASE"),
    "LEADS": ("OUTCOME_LEADS", "OFFSITE_CONVERSIONS", "LEAD"),
}

CALL_TO_ACTIONS = {
    "LEARN_MORE", "SHOP_NOW", "SIGN_UP", "BOOK_TRAVEL", "CONTACT_US", "DOWNLOAD",
    "GET_OFFER", "GET_QUOTE", "SUBSCRIBE", "APPLY_NOW", "ORDER_NOW", "BUY_NOW",
}

GENDERS = {"ALL": None, "MALE": [1], "FEMALE": [2]}

REQUIRED_COLUMNS = [
    "campaign_name", "objective", "adset_name", "daily_budget", "countries",
    "ad_name", "image", "primary_text", "headline", "link_url",
]
OPTIONAL_COLUMNS = ["age_min", "age_max", "gender", "description", "call_to_action", "start_time"]


class SheetError(ValueError):
    """Raised when the sheet has invalid or inconsistent data."""


# --------------------------------------------------------------------------- plan model

@dataclass
class Ad:
    name: str
    image: str
    primary_text: str
    headline: str
    link_url: str
    description: str = ""
    call_to_action: str = "LEARN_MORE"


@dataclass
class AdSet:
    name: str
    daily_budget_cents: int
    countries: list[str]
    age_min: int = 18
    age_max: int = 65
    gender: str = "ALL"
    start_time: str = ""
    ads: list[Ad] = field(default_factory=list)


@dataclass
class Campaign:
    name: str
    objective: str  # friendly name, key of OBJECTIVES
    adsets: dict[str, AdSet] = field(default_factory=dict)


# --------------------------------------------------------------------------- sheet loading

def sheet_csv_url(url: str) -> str:
    """Turn a normal Google Sheets link into its CSV export link."""
    m = re.search(r"docs\.google\.com/spreadsheets/d/([A-Za-z0-9_-]+)", url)
    if not m:
        return url
    gid = re.search(r"[#&?]gid=(\d+)", url)
    out = f"https://docs.google.com/spreadsheets/d/{m.group(1)}/export?format=csv"
    return out + (f"&gid={gid.group(1)}" if gid else "")


def load_rows(source: str) -> list[dict[str, str]]:
    """Load rows from a local CSV path or a Google Sheet link."""
    if source.startswith(("http://", "https://")):
        resp = requests.get(sheet_csv_url(source), timeout=30)
        resp.raise_for_status()
        if "text/html" in resp.headers.get("Content-Type", ""):
            raise SheetError(
                "Got a web page instead of CSV. Set the sheet's sharing to "
                "'Anyone with the link can view', or download it as CSV."
            )
        text = resp.content.decode("utf-8-sig")
    else:
        text = Path(source).read_text(encoding="utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    rows = []
    for row in reader:
        clean = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
        if any(clean.values()):
            rows.append(clean)
    return rows


# --------------------------------------------------------------------------- validation

def _int(value: str, column: str, line: int, lo: int, hi: int) -> int:
    try:
        n = int(float(value))
    except ValueError:
        raise SheetError(f"Row {line}: {column} must be a number, got {value!r}") from None
    if not lo <= n <= hi:
        raise SheetError(f"Row {line}: {column} must be between {lo} and {hi}, got {n}")
    return n


def build_plan(rows: list[dict[str, str]]) -> list[Campaign]:
    """Validate rows and group them into campaigns -> ad sets -> ads."""
    if not rows:
        raise SheetError("The sheet has no rows.")
    missing = [c for c in REQUIRED_COLUMNS if c not in rows[0]]
    if missing:
        raise SheetError(f"Missing columns: {', '.join(missing)}")

    campaigns: dict[str, Campaign] = {}
    ad_names: set[tuple[str, str, str]] = set()
    errors: list[str] = []

    for i, row in enumerate(rows, start=2):  # line 1 is the header
        try:
            for col in REQUIRED_COLUMNS:
                if not row.get(col):
                    raise SheetError(f"Row {i}: {col} is empty")

            objective = row["objective"].upper()
            if objective not in OBJECTIVES:
                raise SheetError(
                    f"Row {i}: objective must be one of {', '.join(OBJECTIVES)}, got {row['objective']!r}"
                )

            budget_text = row["daily_budget"].replace("$", "").replace(",", "")
            try:
                budget = float(budget_text)
            except ValueError:
                raise SheetError(f"Row {i}: daily_budget must be a number, got {row['daily_budget']!r}") from None
            if budget < 1:
                raise SheetError(f"Row {i}: daily_budget must be at least 1")

            countries = [c.strip().upper() for c in re.split(r"[,;\s]+", row["countries"]) if c.strip()]
            bad = [c for c in countries if not re.fullmatch(r"[A-Z]{2}", c)]
            if bad:
                raise SheetError(f"Row {i}: countries must be 2-letter codes like US, GB; got {', '.join(bad)}")

            age_min = _int(row.get("age_min") or "18", "age_min", i, 18, 65)
            age_max = _int(row.get("age_max") or "65", "age_max", i, 18, 65)
            if age_min > age_max:
                raise SheetError(f"Row {i}: age_min is greater than age_max")

            gender = (row.get("gender") or "ALL").upper()
            if gender not in GENDERS:
                raise SheetError(f"Row {i}: gender must be ALL, MALE or FEMALE")

            cta = (row.get("call_to_action") or "LEARN_MORE").upper().replace(" ", "_")
            if cta not in CALL_TO_ACTIONS:
                raise SheetError(f"Row {i}: call_to_action must be one of {', '.join(sorted(CALL_TO_ACTIONS))}")

            if not row["link_url"].startswith(("http://", "https://")):
                raise SheetError(f"Row {i}: link_url must start with http:// or https://")

            camp = campaigns.setdefault(row["campaign_name"], Campaign(row["campaign_name"], objective))
            if camp.objective != objective:
                raise SheetError(
                    f"Row {i}: campaign {camp.name!r} has objective {objective} here "
                    f"but {camp.objective} on an earlier row"
                )

            new_adset = AdSet(
                name=row["adset_name"],
                daily_budget_cents=round(budget * 100),
                countries=countries,
                age_min=age_min,
                age_max=age_max,
                gender=gender,
                start_time=row.get("start_time", ""),
            )
            adset = camp.adsets.setdefault(new_adset.name, new_adset)
            if adset is not new_adset:
                for attr in ("daily_budget_cents", "countries", "age_min", "age_max", "gender", "start_time"):
                    if getattr(adset, attr) != getattr(new_adset, attr):
                        raise SheetError(
                            f"Row {i}: ad set {adset.name!r} has a different {attr} than on an earlier row"
                        )

            key = (camp.name, adset.name, row["ad_name"])
            if key in ad_names:
                raise SheetError(f"Row {i}: duplicate ad_name {row['ad_name']!r} in ad set {adset.name!r}")
            ad_names.add(key)

            adset.ads.append(Ad(
                name=row["ad_name"],
                image=row["image"],
                primary_text=row["primary_text"],
                headline=row["headline"],
                link_url=row["link_url"],
                description=row.get("description", ""),
                call_to_action=cta,
            ))
        except SheetError as e:
            errors.append(str(e))

    if errors:
        raise SheetError("\n".join(errors))
    return list(campaigns.values())


def print_plan(plan: list[Campaign]) -> None:
    for camp in plan:
        print(f"Campaign: {camp.name}  [{camp.objective}]")
        for adset in camp.adsets.values():
            who = f"{adset.gender.lower()} {adset.age_min}-{adset.age_max} in {', '.join(adset.countries)}"
            print(f"  Ad set: {adset.name}  ${adset.daily_budget_cents / 100:.2f}/day  {who}")
            for ad in adset.ads:
                print(f"    Ad: {ad.name}  \"{ad.headline}\" -> {ad.link_url}  [{ad.call_to_action}]")
    n_sets = sum(len(c.adsets) for c in plan)
    n_ads = sum(len(s.ads) for c in plan for s in c.adsets.values())
    print(f"\n{len(plan)} campaign(s), {n_sets} ad set(s), {n_ads} ad(s)")


# --------------------------------------------------------------------------- payloads

def campaign_payload(camp: Campaign) -> dict:
    return {
        "name": camp.name,
        "objective": OBJECTIVES[camp.objective][0],
        "status": "PAUSED",
        "special_ad_categories": json.dumps([]),
        "is_adset_budget_sharing_enabled": "false",
    }


def adset_payload(adset: AdSet, campaign_id: str, objective: str, pixel_id: str | None) -> dict:
    _, goal, event = OBJECTIVES[objective]
    targeting: dict = {
        "geo_locations": {"countries": adset.countries},
        "age_min": adset.age_min,
        "age_max": adset.age_max,
        "targeting_automation": {"advantage_audience": 0},
    }
    if GENDERS[adset.gender]:
        targeting["genders"] = GENDERS[adset.gender]
    payload = {
        "name": adset.name,
        "campaign_id": campaign_id,
        "daily_budget": adset.daily_budget_cents,
        "billing_event": "IMPRESSIONS",
        "optimization_goal": goal,
        "bid_strategy": "LOWEST_COST_WITHOUT_CAP",
        "targeting": json.dumps(targeting),
        "status": "PAUSED",
    }
    if event:
        if not pixel_id:
            raise SheetError(f"Ad set {adset.name!r}: {objective} campaigns need META_PIXEL_ID set")
        payload["promoted_object"] = json.dumps({"pixel_id": pixel_id, "custom_event_type": event})
    if adset.start_time:
        payload["start_time"] = adset.start_time
    return payload


def creative_payload(ad: Ad, image_hash: str, page_id: str, ig_user_id: str | None) -> dict:
    link_data = {
        "image_hash": image_hash,
        "link": ad.link_url,
        "message": ad.primary_text,
        "name": ad.headline,
        "call_to_action": {"type": ad.call_to_action, "value": {"link": ad.link_url}},
    }
    if ad.description:
        link_data["description"] = ad.description
    spec = {"page_id": page_id, "link_data": link_data}
    if ig_user_id:
        spec["instagram_user_id"] = ig_user_id
    return {"name": f"{ad.name} creative", "object_story_spec": json.dumps(spec)}


# --------------------------------------------------------------------------- Meta API

class MetaAPIError(RuntimeError):
    pass


class MetaClient:
    def __init__(self, token: str, account_id: str, version: str = DEFAULT_API_VERSION, session=None):
        self.token = token
        self.account = account_id if account_id.startswith("act_") else f"act_{account_id}"
        self.base = f"https://graph.facebook.com/{version}"
        self.http = session or requests.Session()

    def post(self, edge: str, data: dict) -> dict:
        resp = self.http.post(
            f"{self.base}/{self.account}/{edge}",
            data={**data, "access_token": self.token},
            timeout=60,
        )
        body = resp.json()
        if resp.status_code >= 400 or "error" in body:
            err = body.get("error", {})
            detail = err.get("error_user_msg") or err.get("message") or resp.text
            raise MetaAPIError(f"{edge}: {detail}")
        return body

    def upload_image(self, image: str) -> str:
        if image.startswith(("http://", "https://")):
            resp = requests.get(image, timeout=60)
            resp.raise_for_status()
            data = resp.content
        else:
            data = Path(image).read_bytes()
        body = self.post("adimages", {"bytes": base64.b64encode(data).decode()})
        return next(iter(body["images"].values()))["hash"]


# --------------------------------------------------------------------------- run

def load_state(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text())
    return {"campaigns": {}, "adsets": {}, "images": {}, "ads": {}}


def save_state(path: Path, state: dict) -> None:
    path.write_text(json.dumps(state, indent=2))


def execute(plan: list[Campaign], client: MetaClient, page_id: str, ig_user_id: str | None,
            pixel_id: str | None, state: dict, on_change=lambda: None) -> None:
    """Create everything in the plan, skipping objects already recorded in state."""
    for camp in plan:
        cid = state["campaigns"].get(camp.name)
        if cid:
            print(f"= campaign {camp.name} already exists ({cid})")
        else:
            cid = client.post("campaigns", campaign_payload(camp))["id"]
            state["campaigns"][camp.name] = cid
            on_change()
            print(f"+ campaign {camp.name} ({cid})")

        for adset in camp.adsets.values():
            skey = f"{camp.name}/{adset.name}"
            sid = state["adsets"].get(skey)
            if sid:
                print(f"  = ad set {adset.name} already exists ({sid})")
            else:
                sid = client.post("adsets", adset_payload(adset, cid, camp.objective, pixel_id))["id"]
                state["adsets"][skey] = sid
                on_change()
                print(f"  + ad set {adset.name} ({sid})")

            for ad in adset.ads:
                akey = f"{skey}/{ad.name}"
                if akey in state["ads"]:
                    print(f"    = ad {ad.name} already exists ({state['ads'][akey]})")
                    continue
                image_hash = state["images"].get(ad.image)
                if not image_hash:
                    image_hash = client.upload_image(ad.image)
                    state["images"][ad.image] = image_hash
                    on_change()
                creative_id = client.post("adcreatives", creative_payload(ad, image_hash, page_id, ig_user_id))["id"]
                ad_id = client.post("ads", {
                    "name": ad.name,
                    "adset_id": sid,
                    "creative": json.dumps({"creative_id": creative_id}),
                    "status": "PAUSED",
                })["id"]
                state["ads"][akey] = ad_id
                on_change()
                print(f"    + ad {ad.name} ({ad_id})")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("sheet", help="Google Sheet link or path to a CSV file")
    p.add_argument("--live", action="store_true", help="actually create the ads (default: dry run)")
    p.add_argument("--state", default="meta_ad_state.json", help="file that remembers created IDs")
    args = p.parse_args(argv)

    try:
        plan = build_plan(load_rows(args.sheet))
    except (SheetError, OSError, requests.RequestException) as e:
        print(f"Sheet problem:\n{e}", file=sys.stderr)
        return 1

    print_plan(plan)
    if not args.live:
        print("\nDry run only. Re-run with --live to create these (they will be PAUSED).")
        return 0

    env = os.environ
    needed = ["META_ACCESS_TOKEN", "META_AD_ACCOUNT_ID", "META_PAGE_ID"]
    missing = [k for k in needed if not env.get(k)]
    if missing:
        print(f"Missing environment variables: {', '.join(missing)}", file=sys.stderr)
        return 1

    client = MetaClient(env["META_ACCESS_TOKEN"], env["META_AD_ACCOUNT_ID"],
                        env.get("META_API_VERSION", DEFAULT_API_VERSION))
    state_path = Path(args.state)
    state = load_state(state_path)
    print()
    try:
        execute(plan, client, env["META_PAGE_ID"], env.get("META_IG_USER_ID"), env.get("META_PIXEL_ID"),
                state, on_change=lambda: save_state(state_path, state))
    except (MetaAPIError, SheetError, OSError, requests.RequestException) as e:
        print(f"\nStopped: {e}\nProgress is saved; fix the problem and re-run to continue.", file=sys.stderr)
        return 1
    print("\nDone. Everything is PAUSED. Review in Ads Manager, then switch on what you want to run.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
