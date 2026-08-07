# pronto_checker

Watches [pronto.bm](https://pronto.bm) for products coming back in stock and
sends a phone notification via [ntfy](https://ntfy.sh) when they do. Runs three
times a day on GitHub Actions.

## How it works

pronto.bm is an Angular front end over the [Eddress](https://eddress.co) grocery
platform, and its storefront talks to a public, unauthenticated JSON API. So
there is no scraping and no headless browser -- two HTTP calls per run:

1. `POST api/market/app/public/services/stores` -> the Bermuda store id
2. `POST v1/api/searchengine/public/{tenantUid}/search` -> matching products

Each product carries an `outOfStock` boolean. Out-of-stock products **still
appear in search results**, so stock is read from that flag, never from whether
the search returned a hit.

`state.json` records the last known state of each product. A notification goes
out only on the transition from unavailable to available, so a product that
stays in stock does not buzz three phones three times a day.

If a watched product cannot be found at all, the run fails loudly (non-zero
exit) rather than silently reporting "not available" -- a broken watcher and an
out-of-stock product should not look the same. The stored state is left
untouched in that case, so a later recovery is not mistaken for a restock.

## Configuration

Products live in `watchlist.json`:

| field | meaning |
| --- | --- |
| `key` | stable identifier used in `state.json` |
| `name` | human-readable fallback name, used if the API label is missing |
| `query` | search text sent to the API |
| `product_id` | Eddress product id; the primary match |
| `slug` | used to build the product URL |

Matching is by `product_id` first, falling back to the label. To add a product,
search for it and copy the `id` and `slug` out of the response:

```sh
curl -s -X POST -H 'Content-Type: application/json' \
  -d '{"query":"beef stew","page":0,
       "storeId":"62bc5c3f5e1f9a0685630dbf",
       "tenantUid":"WLxcLSplRaS7A9DKkDDHMQ"}' \
  https://prod-api.eddress.co/v1/api/searchengine/public/WLxcLSplRaS7A9DKkDDHMQ/search \
  | python3 -m json.tool | grep -E '"(id|label|slug|outOfStock)"'
```

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
