#!/usr/bin/env python3
"""Watch pronto.bm for products coming back in stock, and push to ntfy when they do.

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
import sys
import urllib.error
import urllib.request
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


def label_of(entry, item):
    return (item.get("label") or entry["name"]).strip()


def build_body(newly_available):
    lines = []
    for entry, item in newly_available:
        lines.append(f"{label_of(entry, item)} - ${item.get('price')}")
        lines.append(f"{STORE_URL}/product/{entry['slug']}")
    return "\n".join(lines)


def ascii_header(text):
    """ntfy carries notification metadata in HTTP headers, which must be ASCII."""
    return text.encode("ascii", "replace").decode("ascii")


def notify(newly_available):
    """Push to ntfy. Returns True if sent, False if not configured."""
    topic = os.environ.get("NTFY_TOPIC")
    if not topic:
        print("ntfy not configured (NTFY_TOPIC) - skipping notification")
        return False

    server = os.environ.get("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
    names = ", ".join(label_of(entry, item) for entry, item in newly_available)

    headers = {
        "Title": ascii_header(f"Back in stock: {names}"),
        "Tags": "shopping_cart",
        "Priority": "high",
        "User-Agent": USER_AGENT,
        "Content-Type": "text/plain; charset=utf-8",
        # Tapping the notification opens the product page.
        "Click": f"{STORE_URL}/product/{newly_available[0][0]['slug']}",
    }

    token = os.environ.get("NTFY_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    request = urllib.request.Request(
        f"{server}/{topic}",
        data=build_body(newly_available).encode(),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        response.read()

    # Never log the topic: on ntfy.sh the topic name is the only access control,
    # and this repository's workflow logs are public.
    print("notification sent")
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
        notify(newly_available)
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
