#!/usr/bin/env python3
"""Watch apartments-hn.de and send a Telegram message when an apartment
becomes "available" or "soon available".

Usage:
  python monitor.py            # normal run (used by GitHub Actions)
  python monitor.py --dump     # just print what the parser finds, send nothing
  python monitor.py --dry-run  # print Telegram messages instead of sending them
  python monitor.py --file page.html   # parse a saved HTML file instead of the live page

Environment variables:
  TELEGRAM_BOT_TOKEN  bot token from @BotFather
  TELEGRAM_CHAT_ID    your chat ID
  CARD_SELECTOR       optional CSS selector for one apartment card (e.g. ".apartment-item")
  USE_BROWSER         "1" to render the page with Playwright (only if listings load via JavaScript)
"""

import argparse
import datetime
import html
import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

URL = "https://www.apartments-hn.de/en/book-apartment"
STATE_FILE = Path(__file__).with_name("state.json")
WANTED = {"available", "soon"}
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Accept-Language": "en,de;q=0.8",
}

# Status words in English and German. Checked in this order: booked → available now → soon → available.
BOOKED_RE = re.compile(
    r"not\s+available|unavailable|no\s+longer\s+available|booked|reserved|occupied|rented|sold\s*out|"
    r"nicht\s+(mehr\s+)?verf[üu]gbar|ausgebucht|belegt|vermietet|reserviert",
    re.I,
)
NOW_RE = re.compile(r"available\s+(now|immediately)|immediately\s+available|ab\s+sofort|sofort\s+verf[üu]gbar", re.I)
SOON_RE = re.compile(
    r"soon|coming|upcoming|available\s+(from|as\s+of|starting)|bald|demn[äa]chst|in\s+K[üu]rze|"
    r"verf[üu]gbar\s+ab|frei\s+ab",
    re.I,
)
AVAILABLE_RE = re.compile(r"available|verf[üu]gbar|\bfrei\b|\bvacant\b", re.I)
ANY_STATUS_RE = re.compile(
    r"available|verf[üu]gbar|\bfrei\b|vacant|booked|reserved|reserviert|occupied|rented|ausgebucht|belegt|vermietet",
    re.I,
)
SKIP_TAGS = {"script", "style", "noscript", "title", "head", "meta", "option", "select", "nav", "footer"}
TOP_TAGS = {"body", "html", "[document]", "main", "form"}

STATUS_LABEL = {
    "available": "✅ AVAILABLE",
    "soon": "🕒 SOON AVAILABLE",
    "booked": "⛔ booked",
    None: "?",
}


# ---------------------------------------------------------------- fetching

def fetch(url=URL):
    if os.getenv("USE_BROWSER") == "1":
        return fetch_with_browser(url)
    last_error = None
    for attempt in range(3):
        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
            r.raise_for_status()
            return r.text
        except requests.RequestException as e:
            last_error = e
            time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"Could not load {url}: {last_error}")


def fetch_with_browser(url):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(extra_http_headers={"Accept-Language": HEADERS["Accept-Language"]})
        page.goto(url, wait_until="networkidle", timeout=60000)
        content = page.content()
        browser.close()
        return content


# ---------------------------------------------------------------- parsing

def clean(text):
    return re.sub(r"\s+", " ", text or "").strip()


def classify(text):
    if BOOKED_RE.search(text):
        return "booked"
    if NOW_RE.search(text):
        return "available"
    if SOON_RE.search(text) and AVAILABLE_RE.search(text):
        return "soon"
    if AVAILABLE_RE.search(text):
        return "available"
    return None


def status_count(node):
    return len(ANY_STATUS_RE.findall(node.get_text(" ")))


def find_cards(soup):
    """Find apartment cards.

    With CARD_SELECTOR set, use it. Otherwise find every status word on the page
    and climb up to the biggest element that still holds only that one status.
    """
    selector = os.getenv("CARD_SELECTOR")
    if selector:
        return soup.select(selector)

    cards, seen = [], set()
    for text_node in soup.find_all(string=ANY_STATUS_RE):
        if any(p.name in SKIP_TAGS for p in text_node.parents):
            continue
        node = text_node.parent
        while node.parent is not None and node.parent.name not in TOP_TAGS and status_count(node.parent) <= 1:
            node = node.parent
        if id(node) not in seen:
            seen.add(id(node))
            cards.append(node)
    return cards


def card_name(card):
    for tag in ("h1", "h2", "h3", "h4", "h5", "h6", "strong", "b"):
        el = card.find(tag)
        if el and clean(el.get_text()):
            name = clean(el.get_text())
            if not ANY_STATUS_RE.fullmatch(name):
                return name[:120]
    # Fallback: the text before the status words (so the name doesn't change when the status does)
    text = clean(card.get_text(" "))
    starts = [m.start() for rx in (BOOKED_RE, NOW_RE, SOON_RE, ANY_STATUS_RE) if (m := rx.search(text))]
    before = clean(text[: min(starts)]) if starts else text
    return (before or clean(ANY_STATUS_RE.sub("", text)))[:80] or "Apartment"


def card_link(card, base_url):
    for a in card.find_all("a", href=True):
        href = a["href"].strip()
        if href and not href.startswith(("#", "javascript:", "mailto:", "tel:")):
            return urljoin(base_url, href)
    return base_url


def parse(page_html, base_url=URL):
    soup = BeautifulSoup(page_html, "html.parser")
    listings = {}
    for card in find_cards(soup):
        text = clean(card.get_text(" "))
        status = classify(text)
        if status is None:
            continue
        name = card_name(card)
        link = card_link(card, base_url)
        key = name if link == base_url else f"{name} | {link}"
        n = 2
        while key in listings:  # two cards with the same name/link
            key = f"{name} #{n}"
            n += 1
        listings[key] = {"name": name, "status": status, "link": link, "text": text[:400]}
    return listings


# ---------------------------------------------------------------- state + diff

def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")


def diff(old, new):
    """Return listings that are new-and-wanted, or that changed into a wanted status."""
    alerts = []
    for key, item in new.items():
        if item["status"] not in WANTED:
            continue
        before = old.get(key)
        if before is None or before["status"] != item["status"]:
            alerts.append((item, before["status"] if before else None))
    return alerts


# ---------------------------------------------------------------- telegram

def send_telegram(text, dry_run=False):
    if dry_run:
        print("----- TELEGRAM (dry run) -----\n" + text + "\n")
        return
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    r = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data={"chat_id": chat_id, "text": text[:4000], "parse_mode": "HTML", "disable_web_page_preview": "true"},
        timeout=30,
    )
    if not r.ok:
        raise RuntimeError(f"Telegram error {r.status_code}: {r.text}")


def format_alert(item, old_status):
    e = html.escape
    change = "NEW" if old_status is None else f"{STATUS_LABEL[old_status]} → now"
    return (
        f"🏠 <b>{STATUS_LABEL[item['status']]}</b> ({e(change)})\n"
        f"<b>{e(item['name'])}</b>\n\n"
        f"{e(item['text'][:300])}\n\n"
        f"👉 <a href=\"{e(item['link'])}\">Book now</a>\n"
        f"{e(URL)}"
    )


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", action="store_true", help="print parsed listings and exit")
    ap.add_argument("--dry-run", action="store_true", help="print messages instead of sending")
    ap.add_argument("--file", help="parse this HTML file instead of the live page")
    args = ap.parse_args()

    try:
        page_html = Path(args.file).read_text(encoding="utf-8") if args.file else fetch()
        listings = parse(page_html)
        problem = None if listings else "the page loaded, but no apartments were found (layout changed?)"
    except Exception as e:  # site down, network error, ...
        listings, problem = {}, str(e)

    if args.dump:
        print(json.dumps(listings, indent=2, ensure_ascii=False))
        print(f"\n{len(listings)} listing(s) found." + (f" Problem: {problem}" if problem else ""))
        return

    state = load_state()
    first_run = "listings" not in state
    old = state.get("listings", {})

    # Problem: warn once, keep the old state, try again next run.
    if problem:
        print("Problem:", problem)
        if not state.get("warned"):
            send_telegram(f"⚠️ Apartment monitor: {html.escape(problem)}\nI'll keep trying.", args.dry_run)
            state["warned"] = True
            save_state(state)
        return
    if state.get("warned"):
        send_telegram("✅ Apartment monitor is working again.", args.dry_run)

    wanted_now = [i for i in listings.values() if i["status"] in WANTED]
    if first_run:
        lines = [f"🤖 Apartment monitor started. {len(listings)} listing(s) on the page, "
                 f"{len(wanted_now)} available / soon available right now:"]
        lines += [f"• {STATUS_LABEL[i['status']]}: {html.escape(i['name'])}" for i in wanted_now[:30]]
        lines.append("\nFrom now on I'll message you when something new shows up.")
        send_telegram("\n".join(lines), args.dry_run)
    else:
        for item, old_status in diff(old, listings):
            send_telegram(format_alert(item, old_status), args.dry_run)

    # Only save after messages were sent, so a Telegram error means we retry next run.
    state["listings"] = listings
    state["warned"] = False
    # Weekly heartbeat → one commit per week keeps the GitHub schedule from being paused after 60 days.
    year, week, _ = datetime.date.today().isocalendar()
    state["heartbeat"] = f"{year}-W{week:02d}"
    save_state(state)
    print(f"OK: {len(listings)} listing(s), {len(wanted_now)} available/soon.")


if __name__ == "__main__":
    sys.exit(main())
