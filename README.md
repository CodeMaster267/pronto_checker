# pronto_checker

Watches [pronto.bm](https://pronto.bm) for a product that **is not in the
catalogue yet**, and sends a phone notification via [ntfy](https://ntfy.sh) the
moment it gets listed. Runs three times a day on GitHub Actions.

Currently watching for: **Lean Beef Stew - 1lb - Halal**

## How it works

pronto.bm is an Angular front end over the [Eddress](https://eddress.co) grocery
platform, and its storefront talks to a public, unauthenticated JSON API. So
there is no scraping and no headless browser:

1. `POST api/market/app/public/services/stores` -> the Bermuda store id
2. `POST v1/api/searchengine/public/{tenantUid}/search` -> one call per query

### Matching on words, not on an exact title

Pronto's product titles are not internally consistent:

```
Lean Beef Stew-1lb            <- trailing space, no spaces around the dash
Beef Stew (Frozen) 1 lb- Halal
Ground Beef (Extra Lean) 1 lb - Halal
Lamb Leg Steak Boneless 1lb- Halal
```

Comparing against one exact string would miss the listing over a stray dash or
a doubled space. So a watch declares the words a title **must** contain, in any
order, with punctuation and case ignored. Size is deliberately not a required
word, because `1lb` and `1 lb` are both plausible.

### Why every query is also a canary

The watched product does not exist yet, so "no match" is the normal steady
state -- which means a search that quietly stopped working would look exactly
like a product that has not been listed. Nothing would ever alert.

So each query is chosen to always return *something*, and **a query that
returns nothing fails the run**. A red run means the watcher is broken, not
that the product is missing. GitHub emails you about failed runs, which makes
that the alarm.

If any query fails, that watch's stored state is left untouched, so an
incomplete view of the catalogue is never recorded as a disappearance.

## Configuration

Watches live in `watchlist.json`:

| field | meaning |
| --- | --- |
| `key` | stable identifier used in `state.json` |
| `description` | what you are actually waiting for, for humans |
| `queries` | search terms; each must always return results (canary) |
| `require_words` | every one of these must appear in a product title to match |

`state.json` records which matching products have been seen. A notification
fires when a matching product is **listed for the first time**, and again if a
known match goes from out of stock to in stock. A product that simply stays
listed does not buzz anyone.

## Notifications

One secret, under Settings -> Secrets and variables -> Actions:

| secret | value |
| --- | --- |
| `NTFY_TOPIC` | the ntfy topic name to publish to |

**On ntfy.sh the topic name is the only access control.** Anyone who knows it
can subscribe to your notifications or publish to them, so use a long random
name -- not `pronto` -- and keep it in the secret rather than in this file.
This repository is public, which means **workflow logs are public too**; the
script deliberately never prints the topic. Keep it that way.

Optional environment overrides: `NTFY_SERVER` (default `https://ntfy.sh`) and
`NTFY_TOKEN` for a self-hosted or access-controlled server.

### Testing notifications

Since the real event may be months away, there is a test path that pushes a
notification without checking anything:

Actions -> Pronto stock check -> Run workflow -> tick **Send a test
notification** -> Run workflow.

Or locally:

```sh
NTFY_TOPIC=your-topic python3 check.py --test
```

## Running locally

```sh
python3 check.py                       # check only, no notification
NTFY_TOPIC=your-topic python3 check.py # check and notify
```

## Schedule

`45 11,17,22 * * *` (UTC). Bermuda is UTC-4 in winter and UTC-3 in summer, so
these land at 07:45 / 13:45 / 18:45 local in winter, an hour later in summer --
always after 07:30 without a timezone lookup. GitHub can delay scheduled runs,
but only ever later, never earlier.

Note that GitHub disables scheduled workflows after 60 days of repository
inactivity. `state.json` records the date of the last check, so a commit lands
at least once a day and keeps the repo active.
