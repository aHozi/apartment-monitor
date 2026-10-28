#!/usr/bin/env python3
"""Watch the W|27 apartments on apartments-hn.de and send a Telegram message
when an apartment becomes "Available" or "Soon available".

How it works: a headless browser opens the booking page, and we grab the JSON
the page loads from its API (so no API key is needed in this code). Then we use
the same rule as the website to decide each apartment's status.

Usage:
  python monitor.py                # normal run (used by GitHub Actions)
  python monitor.py --dump         # print every apartment + status, send nothing
  python monitor.py --dry-run      # print Telegram messages instead of sending them
  python monitor.py --file X.json  # use a saved API response instead of the live page
  python monitor.py --today 2026-11-01   # pretend it's another day (for testing)

Environment variables: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
"""

import argparse
import datetime
import html
import json
import os
import sys
from pathlib import Path

import requests

URL = "https://www.apartments-hn.de/en/book-apartment"
API_MATCH = "/api/apartments/"  # part of the URL the page loads its apartment list from
APPLY_URL = "https://scs.onboarding.reos.software/contract/{uuid}/register"
STATE_FILE = Path(__file__).with_name("state.json")
SOON_MONTHS = 6  # the website shows "Soon available" when the date is less than 6 months away
WANTED = {"available", "soon"}
MAX_SINGLE_ALERTS = 5  # more changes than this in one run → one combined message

LABEL = {"available": "✅ AVAILABLE", "soon": "🕒 SOON AVAILABLE", "taken": "⛔ Already taken"}


# ---------------------------------------------------------------- fetching

def fetch_api_json():
    """Open the booking page in a headless browser and return the apartment JSON it loads."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(locale="en-US")
        try:
            with page.expect_response(lambda r: API_MATCH in r.url and r.status == 200, timeout=60000) as resp:
                page.goto(URL, timeout=60000)
            return resp.value.json()
        finally:
            browser.close()


def load_file(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, list):  # network.json from debug_capture.py
        call = next(c for c in data if API_MATCH in c["url"])
        data = json.loads(call["body"])
    return data


# ---------------------------------------------------------------- parsing

def get_status(next_rental_begin, today):
    """Same rule as the website's BookApartmentTable:
    no date → Available; date less than 6 months away → Soon available; else Already taken.
    """
    if next_rental_begin == "":
        return "available"
    try:
        d = datetime.date.fromisoformat(next_rental_begin)
    except (TypeError, ValueError):
        return "taken"
    months = (today.year - d.year) * 12 + today.month - d.month
    return "soon" if abs(months) < SOON_MONTHS else "taken"


def parse(api_json, today):
    locations = {
        str(x["id"]): x["attributes"].get("title", "")
        for x in api_json.get("included", [])
        if x.get("type") == "node-category"
    }
    apartments = {}
    for item in api_json["data"]:
        a = item["attributes"]
        cat = (item.get("relationships", {}).get("category", {}).get("data") or {}).get("id")
        apartments[str(item["id"])] = {
            "title": a.get("title", ""),
            "type": a.get("object_type", ""),
            "location": locations.get(str(cat), ""),
            "size": a.get("area_living_space", ""),
            "rent": a.get("price_rental_price_flatrate", ""),
            "date": a.get("next_rental_begin") or "",
            "status": get_status(a.get("next_rental_begin"), today),
            "link": APPLY_URL.format(uuid=a.get("uuid", "")),
        }
    return apartments


# ---------------------------------------------------------------- state + diff

def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")


def diff(old, new):
    """Apartments that are new with a wanted status, or whose status changed to a wanted one."""
    alerts = []
    for key, apt in new.items():
        before = old.get(key)
        if apt["status"] in WANTED and (before is None or before["status"] != apt["status"]):
            alerts.append((apt, before["status"] if before else None))
    return alerts


# ---------------------------------------------------------------- telegram

def send_telegram(text, dry_run=False):
    if dry_run:
        print("----- TELEGRAM (dry run) -----\n" + text + "\n")
        return
    r = requests.post(
        f"https://api.telegram.org/bot{os.environ['TELEGRAM_BOT_TOKEN']}/sendMessage",
        data={
            "chat_id": os.environ["TELEGRAM_CHAT_ID"],
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": "true",
        },
        timeout=30,
    )
    if not r.ok:
        raise RuntimeError(f"Telegram error {r.status_code}: {r.text}")


def send_long(lines, dry_run=False):
    """Send lines as one or more messages (Telegram's limit is 4096 characters)."""
    chunk = ""
    for line in lines:
        if len(chunk) + len(line) + 1 > 3800:
            send_telegram(chunk, dry_run)
            chunk = ""
        chunk += line + "\n"
    if chunk.strip():
        send_telegram(chunk, dry_run)


def short_line(apt):
    e = html.escape
    when = f" from {apt['date']}" if apt["status"] == "soon" else ""
    icon = LABEL[apt["status"]].split()[0]
    return (f"{icon} <b>{e(apt['title'])}</b>: {e(apt['type'])}, {e(apt['location'])}, "
            f"{e(apt['size'])} m², {e(apt['rent'])} €{e(when)} → <a href=\"{e(apt['link'])}\">Apply</a>")


def format_alert(apt, old_status):
    e = html.escape
    change = "new on the list" if old_status is None else f"was: {LABEL[old_status]}"
    lines = [
        f"🏠 <b>{LABEL[apt['status']]}</b> ({e(change)})",
        "",
        f"<b>Apartment {e(apt['title'])}</b>",
        f"Type: {e(apt['type'])}",
        f"Location: {e(apt['location'])}",
        f"Size: {e(apt['size'])} m²",
        f"Rent (all-inclusive): {e(apt['rent'])} €",
    ]
    if apt["status"] == "soon":
        lines.append(f"Available from: {e(apt['date'])}")
    lines += ["", f"👉 <a href=\"{e(apt['link'])}\">Apply now</a>", f"📋 <a href=\"{URL}\">Apartment list</a>"]
    return "\n".join(lines)


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", action="store_true", help="print all apartments and exit")
    ap.add_argument("--dry-run", action="store_true", help="print messages instead of sending")
    ap.add_argument("--file", help="use a saved API response (or debug network.json)")
    ap.add_argument("--today", help="pretend today is YYYY-MM-DD (testing)")
    args = ap.parse_args()
    today = datetime.date.fromisoformat(args.today) if args.today else datetime.date.today()

    try:
        api_json = load_file(args.file) if args.file else fetch_api_json()
        apartments = parse(api_json, today)
        problem = None if apartments else "the apartment list is empty (did the website change?)"
    except Exception as e:  # site down, page changed, ...
        apartments, problem = {}, f"{type(e).__name__}: {e}"

    if args.dump:
        for key, apt in sorted(apartments.items(), key=lambda kv: kv[1]["title"]):
            print(f"{apt['title']:>8} | {LABEL[apt['status']]:<17} | {apt['date'] or '-':<10} | "
                  f"{apt['type']:<22} | {apt['location']:<15} | {apt['size']:>6} m² | {apt['rent']:>7} €")
        counts = {s: sum(a["status"] == s for a in apartments.values()) for s in LABEL}
        print(f"\n{len(apartments)} apartments: {counts}" + (f"\nProblem: {problem}" if problem else ""))
        return 1 if problem else 0

    if not args.dry_run and not (os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_CHAT_ID")):
        print("::warning::TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID secrets are not set yet. Skipping.")
        return 0

    state = load_state()
    first_run = "apartments" not in state
    old = state.get("apartments", {})

    # Problem: warn once, keep the old state, try again next run.
    if problem:
        print("Problem:", problem)
        if not state.get("warned"):
            send_telegram(f"⚠️ Apartment monitor: {html.escape(problem)}\nI'll keep trying every run.", args.dry_run)
            state["warned"] = True
            save_state(state)
        return 0
    if state.get("warned"):
        send_telegram("✅ Apartment monitor is working again.", args.dry_run)

    wanted_now = sorted((a for a in apartments.values() if a["status"] in WANTED), key=lambda a: a["title"])
    if first_run:
        lines = [f"🤖 <b>Apartment monitor started</b>",
                 f"{len(apartments)} apartments on the list, {len(wanted_now)} available / soon available right now."]
        if wanted_now:
            lines.append("")
            lines += [short_line(a) for a in wanted_now]
        lines += ["", "I'll message you as soon as something changes. Checking every ~10 minutes."]
        send_long(lines, args.dry_run)
    else:
        alerts = sorted(diff(old, apartments), key=lambda x: x[0]["title"])
        if len(alerts) <= MAX_SINGLE_ALERTS:
            for apt, old_status in alerts:
                send_telegram(format_alert(apt, old_status), args.dry_run)
        else:
            lines = [f"🏠 <b>{len(alerts)} apartments just changed to available / soon available:</b>", ""]
            lines += [short_line(a) for a, _ in alerts]
            lines += ["", f"📋 <a href=\"{URL}\">Apartment list</a>"]
            send_long(lines, args.dry_run)

    # Only save after the messages went out, so a Telegram error means we retry next run.
    state = {"apartments": apartments, "warned": False}
    # Weekly heartbeat → one commit per week keeps GitHub from pausing the schedule after 60 days.
    year, week, _ = today.isocalendar()
    state["heartbeat"] = f"{year}-W{week:02d}"
    save_state(state)
    print(f"OK: {len(apartments)} apartments, {len(wanted_now)} available/soon.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
