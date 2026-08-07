#!/usr/bin/env python3
"""Watch pronto.bm for products coming back in stock, and email when they do.

pronto.bm is an Angular front end over the Eddress grocery platform. The
storefront talks to a public, unauthenticated JSON API, so this only needs two
HTTP calls and no browser. Each product carries an `outOfStock` flag, and
out-of-stock products still appear in search results -- so stock is read from
that flag, never from whether the search returned a hit.

Stdlib only, by design: no pip install step in CI.
"""

import datetime
import json
import os
import smtplib
import ssl
import sys
import urllib.error
import urllib.request
from email.message import EmailMessage
from pathlib import Path

API_BASE = "https://prod-api.eddress.co/"
TENANT_UID = "WLxcLSplRaS7A9DKkDDHMQ"
OPERATION_UID = "pronto"
APP_NAME = "pronto"
STORE_URL = "https://pronto.bm"

USER_AGENT = "pronto-checker (https://github.com/CodeMaster267/pronto_checker)"
TIMEOUT = 30

ROOT = Path(__file__).parent
WATCHLIST_FILE = ROOT / "watchlist.json"
STATE_FILE = ROOT / "state.json"


def post(path, payload):
    """POST JSON to the Eddress API and return the decoded response."""
    request = urllib.request.Request(
        API_BASE + path,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        return json.loads(response.read())


def get_store_id():
    """Pronto runs a single store; look its id up rather than hardcoding it."""
    data = post(
        "api/market/app/public/services/stores",
        {"appName": APP_NAME, "operationUid": OPERATION_UID},
    )
    stores = data.get("stores") or []
    if not stores:
        raise RuntimeError("stores endpoint returned no stores")
    return stores[0]["id"]


def search(query, store_id):
    data = post(
        f"v1/api/searchengine/public/{TENANT_UID}/search",
        {
            "query": query,
            "page": 0,
            "storeId": store_id,
            "tenantUid": TENANT_UID,
        },
    )
    return data.get("items") or []


def find_product(entry, items):
    """Match on product id, falling back to the label if the id ever changes."""
    for item in items:
        if item.get("id") == entry["product_id"]:
            return item
    wanted = entry["name"].lower().replace(" ", "").replace("-", "")
    for item in items:
        label = (item.get("label") or "").lower().replace(" ", "").replace("-", "")
        if label and label == wanted:
            return item
    return None


def is_available(item):
    return (
        not item.get("outOfStock", True)
        and item.get("isActive", False)
        and item.get("isPublished", False)
    )


def build_email(newly_available):
    lines = [
        "Back in stock at Pronto:",
        "",
    ]
    for entry, item in newly_available:
        lines.append(f"  {item.get('label', entry['name']).strip()}")
        lines.append(f"  ${item.get('price')}")
        lines.append(f"  {STORE_URL}/product/{entry['slug']}")
        lines.append("")
    lines.append("-- pronto-checker")
    return "\n".join(lines)


def send_email(subject, body):
    """Send via SMTP. Returns True if sent, False if not configured."""
    host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    port = int(os.environ.get("SMTP_PORT", "465"))
    user = os.environ.get("SMTP_USER")
    password = os.environ.get("SMTP_PASS")
    recipients = [a.strip() for a in os.environ.get("MAIL_TO", "").split(",") if a.strip()]

    if not (user and password and recipients):
        print("email not configured (SMTP_USER / SMTP_PASS / MAIL_TO) - skipping send")
        return False

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = os.environ.get("MAIL_FROM", user)
    message["To"] = ", ".join(recipients)
    message.set_content(body)

    with smtplib.SMTP_SSL(host, port, context=ssl.create_default_context()) as smtp:
        smtp.login(user, password)
        smtp.send_message(message)

    # Never log the addresses themselves: workflow logs are public.
    print(f"emailed {len(recipients)} recipient(s)")
    return True


def main():
    watchlist = json.loads(WATCHLIST_FILE.read_text())
    state = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}
    products = state.get("products", {})

    store_id = get_store_id()
    today = datetime.date.today().isoformat()

    newly_available = []
    failures = []

    for entry in watchlist:
        key = entry["key"]
        previous = products.get(key, {})
        was_available = previous.get("available")

        try:
            items = search(entry["query"], store_id)
        except (urllib.error.URLError, json.JSONDecodeError, OSError) as error:
            failures.append(f"{key}: search failed ({error})")
            continue

        item = find_product(entry, items)
        if item is None:
            # Could not identify the product at all. That is a broken watcher,
            # not an out-of-stock signal -- leave the stored state untouched so
            # a later recovery does not look like a restock.
            failures.append(f"{key}: not found in {len(items)} search result(s)")
            continue

        available = is_available(item)
        print(f"{key}: available={available} price=${item.get('price')}")

        products[key] = {
            "available": available,
            "label": (item.get("label") or "").strip(),
            "price": item.get("price"),
            "changed_on": today if available != was_available else previous.get("changed_on", today),
        }

        if available and not was_available:
            newly_available.append((entry, item))

    if newly_available:
        names = ", ".join(item.get("label", e["name"]).strip() for e, item in newly_available)
        send_email(f"Back in stock at Pronto: {names}", build_email(newly_available))
    else:
        print("nothing newly available")

    state["products"] = products
    state["checked_on"] = today
    STATE_FILE.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")

    if failures:
        for failure in failures:
            print(f"LOOKUP FAILED - {failure}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
