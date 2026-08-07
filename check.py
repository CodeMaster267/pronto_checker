#!/usr/bin/env python3
"""Watch pronto.bm for a product that is not in the catalogue yet.

pronto.bm is an Angular front end over the Eddress grocery platform. The
storefront talks to a public, unauthenticated JSON API, so this needs no
browser -- one call for the store id, then a search per query.

The thing being watched does not exist yet, so "no match" is the normal
steady state, not an error. That removes the obvious breakage alarm: a search
that silently stopped working would look exactly like a product that has not
been listed. So every query doubles as a canary -- each one is chosen to
always return *something*, and a query returning nothing fails the run.

Matching is on required words rather than an exact title, because Pronto's
titles are not consistent: "Lean Beef Stew-1lb ", "Beef Stew (Frozen) 1 lb-
Halal", "Ground Beef (Extra Lean) 1 lb - Halal". Comparing against one exact
string would miss the listing over a stray dash.

Stdlib only, by design: no pip install step in CI.
"""

import datetime
import json
import os
import re
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


def load_watchlist():
    """Load watchlist.json, rejecting configurations that cannot work."""
    watchlist = json.loads(WATCHLIST_FILE.read_text())

    for watch in watchlist:
        key = watch.get("key", "<unnamed>")
        if not watch.get("queries"):
            raise ValueError(f"{key}: needs at least one query")
        if not [word for word in watch.get("require_words", []) if word.strip()]:
            # An empty requirement matches every product in the catalogue.
            raise ValueError(f"{key}: require_words cannot be empty")

    return watchlist


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
        {"query": query, "page": 0, "storeId": store_id, "tenantUid": TENANT_UID},
    )
    return data.get("items") or []


def normalize(text):
    """Fold punctuation and case so titles compare on words alone."""
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def has_all_words(label, words):
    haystack = normalize(label)
    return all(re.search(rf"\b{re.escape(w.lower())}\b", haystack) for w in words)


def is_available(item):
    return (
        not item.get("outOfStock", True)
        and item.get("isActive", False)
        and item.get("isPublished", False)
    )


def collect(watch, store_id):
    """Run every query for a watch and return (matches, failures).

    Each query must return results. One that comes back empty means the search
    broke, not that the shelf is bare -- see the module docstring.
    """
    matches = {}
    failures = []

    for query in watch["queries"]:
        try:
            items = search(query, store_id)
        except (urllib.error.URLError, json.JSONDecodeError, OSError) as error:
            failures.append(f"{watch['key']}: query {query!r} failed ({error})")
            continue

        if not items:
            failures.append(f"{watch['key']}: query {query!r} returned nothing (canary)")
            continue

        for item in items:
            if has_all_words(item.get("label"), watch["require_words"]):
                matches[item["id"]] = item

    return matches, failures


def build_events(watch, matches, known):
    """Compare this run against stored state and describe what changed."""
    events = []

    for product_id, item in matches.items():
        previous = known.get(product_id, {})
        available = is_available(item)

        if not previous.get("listed"):
            kind = "listed"
        elif available and not previous.get("available"):
            kind = "restocked"
        else:
            kind = None

        if kind:
            events.append(
                {
                    "kind": kind,
                    "label": (item.get("label") or watch["description"]).strip(),
                    "price": item.get("price"),
                    "slug": item.get("slug"),
                    "available": available,
                }
            )

    return events


def ascii_header(text):
    """ntfy carries notification metadata in HTTP headers: ASCII, and one line.

    Collapsing whitespace first matters -- a newline in a product label would
    otherwise raise on send, turning the one alert this bot exists for into a
    failed run.
    """
    collapsed = re.sub(r"\s+", " ", text or "").strip()
    return collapsed.encode("ascii", "replace").decode("ascii")


def push(title, body, click=None):
    """Send one ntfy notification. Returns True if sent, False if unconfigured."""
    topic = os.environ.get("NTFY_TOPIC")
    if not topic:
        print("ntfy not configured (NTFY_TOPIC) - skipping notification")
        return False

    server = os.environ.get("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
    headers = {
        "Title": ascii_header(title),
        "Tags": "shopping_cart",
        "Priority": "high",
        "User-Agent": USER_AGENT,
        "Content-Type": "text/plain; charset=utf-8",
    }
    if click:
        headers["Click"] = click

    token = os.environ.get("NTFY_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    request = urllib.request.Request(
        f"{server}/{topic}", data=body.encode(), headers=headers, method="POST"
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        response.read()

    # Never log the topic: on ntfy.sh the topic name is the only access control,
    # and this repository's workflow logs are public.
    print("notification sent")
    return True


def notify(events):
    lines = []
    for event in events:
        headline = "NOW LISTED" if event["kind"] == "listed" else "BACK IN STOCK"
        stock = "in stock" if event["available"] else "listed but out of stock"
        lines.append(f"{headline}: {event['label']}")
        lines.append(f"${event['price']} - {stock}")
        if event["slug"]:
            lines.append(f"{STORE_URL}/product/{event['slug']}")

    first = events[0]
    verb = "Now on Pronto" if first["kind"] == "listed" else "Back in stock"
    click = f"{STORE_URL}/product/{first['slug']}" if first["slug"] else STORE_URL
    return push(f"{verb}: {first['label']}", "\n".join(lines), click)


def send_test():
    sent = push(
        "pronto-checker test",
        "If you can read this, notifications work.\n"
        "You will next hear from this bot when a watched product is listed.",
        STORE_URL,
    )
    return 0 if sent else 1


def main():
    if "--test" in sys.argv:
        sys.exit(send_test())

    watchlist = load_watchlist()
    state = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}
    watches = state.get("watches", {})

    store_id = get_store_id()

    events = []
    failures = []

    for watch in watchlist:
        key = watch["key"]
        known = watches.get(key, {}).get("products", {})

        matches, watch_failures = collect(watch, store_id)
        failures.extend(watch_failures)

        # A failed query means an incomplete view of the catalogue, so leave
        # this watch's state alone rather than recording a false disappearance.
        if watch_failures:
            print(f"{key}: skipped, {len(watch_failures)} query failure(s)")
            continue

        events.extend(build_events(watch, matches, known))

        print(f"{key}: {len(matches)} match(es) for {watch['require_words']}")
        for item in matches.values():
            print(f"  - {item.get('label', '').strip()!r} available={is_available(item)}")

        watches[key] = {
            "products": {
                product_id: {
                    "label": (item.get("label") or "").strip(),
                    "slug": item.get("slug"),
                    "price": item.get("price"),
                    "listed": True,
                    "available": is_available(item),
                }
                for product_id, item in matches.items()
            }
        }

    if events:
        # Record nothing unless the alert actually went out. Persisting first
        # would mark the product "already seen" and silently retire the one
        # notification this bot exists to send.
        if not notify(events):
            print(
                "FAILED - notification not sent; state left unrecorded so the "
                "next run retries",
                file=sys.stderr,
            )
            sys.exit(1)
    else:
        print("no changes")

    state["watches"] = watches
    state["checked_on"] = datetime.date.today().isoformat()
    state.pop("products", None)  # drop the old stock-checker schema
    STATE_FILE.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")

    if failures:
        for failure in failures:
            print(f"FAILED - {failure}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
