#!/usr/bin/env python3
"""Debug helper: open the booking page in a real browser and save
- rendered.html : the page after JavaScript has loaded the apartment list
- network.json  : every data (XHR/fetch) request the page made, with its response
- scripts.json  : the JavaScript files that handle the apartment list
"""

import json

from playwright.sync_api import sync_playwright

URL = "https://www.apartments-hn.de/en/book-apartment"

calls = []


scripts = {}


def on_response(resp):
    req = resp.request
    if req.resource_type == "script":
        try:
            text = resp.text()
        except Exception:
            return
        if "next_rental_begin" in text or "available_for_rent" in text:
            scripts[resp.url] = text
        return
    if req.resource_type not in ("xhr", "fetch"):
        return
    try:
        body = resp.text()
    except Exception as e:
        body = f"<could not read: {e}>"
    calls.append({
        "method": req.method,
        "url": resp.url,
        "status": resp.status,
        "request_headers": {k: v for k, v in req.headers.items() if k.lower() not in ("authorization", "cookie")},
        "post_data": req.post_data,
        "content_type": resp.headers.get("content-type"),
        "body": body[:200000],
    })


with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(locale="en-US")
    page.on("response", on_response)
    page.goto(URL, wait_until="networkidle", timeout=60000)
    try:
        page.wait_for_function("!document.body.innerText.includes('Loading data')", timeout=30000)
    except Exception:
        print("Still 'Loading data...' after 30s")
    page.wait_for_timeout(3000)
    open("rendered.html", "w", encoding="utf-8").write(page.content())
    browser.close()

json.dump(scripts, open("scripts.json", "w", encoding="utf-8"), indent=2, ensure_ascii=False)
json.dump(calls, open("network.json", "w", encoding="utf-8"), indent=2, ensure_ascii=False)
for c in calls:
    print(c["status"], c["method"], c["url"], c["content_type"], len(c["body"]))
