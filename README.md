# Meta Ad Setup Automation

Fill in a Google Sheet, run one command, and get your Meta (Facebook + Instagram)
campaigns, ad sets and ads created for you.

- **Dry run by default.** It checks your sheet and shows what it would build. Nothing is sent to Meta.
- **Everything is created PAUSED.** Nothing spends money until you switch it on in Ads Manager.
- **Safe to re-run.** It records what it already made in `meta_ad_state.json` and skips those.
  If it stops partway, fix the problem and run it again to continue.

---

## Part 1: One-time Meta setup (about 20 minutes)

You need four things: an **access token**, your **ad account ID**, your **Page ID**, and
(for sales/leads campaigns) your **Pixel ID**.

### 1. Business Manager
Go to <https://business.facebook.com>. If you don't have a Business portfolio, create one.
Make sure your **ad account** and **Facebook Page** are added to it
(Settings → Accounts → Ad accounts / Pages).

### 2. Create a developer app
1. Go to <https://developers.facebook.com/apps> → **Create App**.
2. Choose the **"Other"** use case, then the **Business** app type, and connect it to your Business portfolio.
3. In the app dashboard, add the **Marketing API** product.

### 3. Create a system user and token
1. In Business Settings → **Users → System users** → **Add**. Name it something like `ad-automation` and give it the **Admin** role.
2. Click **Assign assets**. Give it **Full control** of your ad account and your Page.
3. Click **Generate new token**. Pick your app and tick these permissions:
   `ads_management`, `ads_read`, `business_management`, `pages_show_list`,
   `pages_read_engagement`, `pages_manage_ads`.
4. Copy the token and keep it somewhere safe, like a password manager. **Never share it or paste it into chat.**

### 4. Find your IDs
| What | Where to find it |
|---|---|
| Ad account ID | Ads Manager URL: `act=1234567890`, or Business Settings → Ad accounts |
| Page ID | Your Page → About → Page transparency, or Business Settings → Pages |
| Instagram account ID (optional) | Business Settings → Instagram accounts |
| Pixel ID (sales/leads only) | Events Manager → Data sources |

---

## Part 2: Your Google Sheet

1. Create a new Google Sheet. Then go to **File → Import → Upload** and choose [`template.csv`](template.csv), which gives you the right columns.
2. Click **Share** and set link access to **"Anyone with the link" → Viewer**, so the script can read it.
   (If you'd rather not, use **File → Download → CSV** and point the script at that file instead.)
3. Add **one row per ad**. Repeat the campaign and ad set columns on every row. Rows with the
   same `campaign_name` / `adset_name` get grouped together.

| Column | Required | Example | Notes |
|---|---|---|---|
| campaign_name | ✓ | Spring Sale | |
| objective | ✓ | TRAFFIC | `TRAFFIC`, `AWARENESS`, `ENGAGEMENT`, `SALES`, `LEADS` (the last two need a Pixel) |
| adset_name | ✓ | US Women 25-44 | |
| daily_budget | ✓ | 20 | In your ad account's currency |
| countries | ✓ | US, CA | 2-letter country codes |
| age_min / age_max | | 25 / 44 | Default 18 / 65 |
| gender | | FEMALE | `ALL` (default), `MALE`, `FEMALE` |
| start_time | | 2026-12-01T09:00:00-0500 | Blank = start when switched on |
| ad_name | ✓ | Spring - Image A | Must be unique within the ad set |
| image | ✓ | https://…/a.jpg | Public image link or local file path. 1080×1080 works well |
| primary_text | ✓ | Our spring collection… | The main text above the image |
| headline | ✓ | Spring Sale: 20% Off | |
| description | | Free shipping over $50 | |
| call_to_action | | SHOP_NOW | `LEARN_MORE` (default), `SHOP_NOW`, `SIGN_UP`, `BUY_NOW`, `ORDER_NOW`, `GET_OFFER`, `CONTACT_US`, `SUBSCRIBE`, `APPLY_NOW`, `DOWNLOAD`, `GET_QUOTE`, `BOOK_TRAVEL` |
| link_url | ✓ | https://yoursite.com/spring | |

---

## Part 3: Run it

Requires Python 3.10+ and the `requests` library (`pip install requests`).

```bash
# 1. Preview: checks the sheet and prints the plan. Sends nothing to Meta.
python3 meta_ad_setup.py "https://docs.google.com/spreadsheets/d/YOUR_SHEET_ID/edit"

# 2. Set your credentials (in your terminal, never in the sheet)
export META_ACCESS_TOKEN="..."        # from step 3
export META_AD_ACCOUNT_ID="1234567890"
export META_PAGE_ID="1234567890"
export META_IG_USER_ID="..."          # optional
export META_PIXEL_ID="..."            # only for SALES / LEADS

# 3. Create everything (PAUSED)
python3 meta_ad_setup.py "https://docs.google.com/spreadsheets/d/YOUR_SHEET_ID/edit" --live
```

Then open Ads Manager, review the new campaigns, and switch on the ones you want.

**Notes**
- If you add new rows later and re-run, only the new ads get created.
- Editing a row that was already created does **not** change the live ad. Make those changes in Ads Manager.
- The Graph API version defaults to `v23.0`. If Meta reports that version is deprecated, set `META_API_VERSION`, for example `export META_API_VERSION=v24.0`.
- Keep `meta_ad_state.json` around. Deleting it makes the script forget what it already created.

## Tests
```bash
python3 -m pytest tests
```
