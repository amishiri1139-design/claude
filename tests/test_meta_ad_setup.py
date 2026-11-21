import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import meta_ad_setup as m  # noqa: E402

TEMPLATE = Path(__file__).resolve().parents[1] / "template.csv"


def row(**over):
    base = {
        "campaign_name": "C1", "objective": "traffic", "adset_name": "S1", "daily_budget": "$20",
        "countries": "US", "ad_name": "A1", "image": "img.jpg", "primary_text": "Hi",
        "headline": "Head", "link_url": "https://x.com",
    }
    base.update(over)
    return base


def test_template_parses_and_groups():
    plan = m.build_plan(m.load_rows(str(TEMPLATE)))
    assert len(plan) == 1
    camp = plan[0]
    assert list(camp.adsets) == ["US Women 25-44", "UK + CA All 18-65"]
    assert len(camp.adsets["US Women 25-44"].ads) == 2
    assert camp.adsets["UK + CA All 18-65"].countries == ["GB", "CA"]
    assert camp.adsets["US Women 25-44"].daily_budget_cents == 2000


def test_missing_column():
    r = row()
    del r["headline"]
    with pytest.raises(m.SheetError, match="Missing columns: headline"):
        m.build_plan([r])


def test_collects_all_errors():
    with pytest.raises(m.SheetError) as e:
        m.build_plan([row(objective="nope"), row(ad_name="A2", countries="USA")])
    msg = str(e.value)
    assert "Row 2: objective" in msg and "Row 3: countries" in msg


def test_conflicting_adset_settings():
    with pytest.raises(m.SheetError, match="different daily_budget_cents"):
        m.build_plan([row(), row(ad_name="A2", daily_budget="30")])


def test_duplicate_ad_name():
    with pytest.raises(m.SheetError, match="duplicate ad_name"):
        m.build_plan([row(), row()])


def test_sheet_url_conversion():
    url = "https://docs.google.com/spreadsheets/d/abc_123/edit#gid=42"
    assert m.sheet_csv_url(url) == "https://docs.google.com/spreadsheets/d/abc_123/export?format=csv&gid=42"


def test_adset_payload_targeting_and_pixel():
    plan = m.build_plan([row(objective="SALES", gender="female", age_min="25", age_max="44")])
    adset = plan[0].adsets["S1"]
    with pytest.raises(m.SheetError, match="META_PIXEL_ID"):
        m.adset_payload(adset, "c1", "SALES", None)
    p = m.adset_payload(adset, "c1", "SALES", "px")
    t = json.loads(p["targeting"])
    assert t["genders"] == [2] and t["age_min"] == 25 and t["geo_locations"] == {"countries": ["US"]}
    assert json.loads(p["promoted_object"]) == {"pixel_id": "px", "custom_event_type": "PURCHASE"}
    assert p["status"] == "PAUSED" and p["optimization_goal"] == "OFFSITE_CONVERSIONS"


class FakeClient:
    def __init__(self):
        self.calls = []

    def post(self, edge, data):
        self.calls.append((edge, data))
        return {"id": f"{edge}-{len(self.calls)}"}

    def upload_image(self, image):
        self.calls.append(("adimages", image))
        return f"hash-{image}"


def test_execute_creates_paused_and_is_idempotent():
    plan = m.build_plan([row(), row(ad_name="A2"), row(adset_name="S2", ad_name="A3")])
    client, state = FakeClient(), m.load_state(Path("/nonexistent"))
    m.execute(plan, client, "page", None, None, state)
    edges = [c[0] for c in client.calls]
    assert edges.count("campaigns") == 1
    assert edges.count("adsets") == 2
    assert edges.count("ads") == 3
    assert edges.count("adimages") == 1  # same image reused
    assert all(d.get("status") == "PAUSED" for e, d in client.calls if e in {"campaigns", "adsets", "ads"})

    client2 = FakeClient()
    m.execute(plan, client2, "page", None, None, state)
    assert client2.calls == []


def test_dry_run_makes_no_requests(capsys, monkeypatch):
    monkeypatch.setattr(m.requests, "post", lambda *a, **k: pytest.fail("network call"))
    assert m.main([str(TEMPLATE)]) == 0
    assert "Dry run only" in capsys.readouterr().out
