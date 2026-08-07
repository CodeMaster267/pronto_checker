# pronto_checker

Watches [pronto.bm](https://pronto.bm) for products coming back in stock and
emails when they do. Runs three times a day on GitHub Actions.

## How it works

pronto.bm is an Angular front end over the [Eddress](https://eddress.co) grocery
platform, and its storefront talks to a public, unauthenticated JSON API. So
there is no scraping and no headless browser -- two HTTP calls per run:

1. `POST api/market/app/public/services/stores` -> the Bermuda store id
2. `POST v1/api/searchengine/public/{tenantUid}/search` -> matching products

Each product carries an `outOfStock` boolean. Out-of-stock products **still
appear in search results**, so stock is read from that flag, never from whether
the search returned a hit.

`state.json` records the last known state of each product. Email goes out only
on the transition from unavailable to available, so a product that stays in
stock does not generate three emails a day.

If a watched product cannot be found at all, the run fails loudly (non-zero
exit) rather than silently reporting "not available" -- a broken watcher and an
out-of-stock product should not look the same. The stored state is left
untouched in that case, so a later recovery is not mistaken for a restock.

## Configuration

Products live in `watchlist.json`:

| field | meaning |
| --- | --- |
| `key` | stable identifier used in `state.json` |
| `name` | human-readable name, used in the email |
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

## Secrets

Set these under Settings -> Secrets and variables -> Actions:

| secret | value |
| --- | --- |
| `SMTP_USER` | Gmail address used to send |
| `SMTP_PASS` | Gmail **app password**, not the account password |
| `MAIL_TO` | comma-separated recipients |

Optional environment overrides: `SMTP_HOST` (default `smtp.gmail.com`),
`SMTP_PORT` (default `465`), `MAIL_FROM` (default `SMTP_USER`).

This repository is public, which means **workflow logs are public too**. The
script logs recipient counts, never addresses. Keep it that way.

## Running locally

```sh
python3 check.py                      # check only, no email
MAIL_TO=you@example.com SMTP_USER=... SMTP_PASS=... python3 check.py
```

## Schedule

`45 11,17,22 * * *` (UTC). Bermuda is UTC-4 in winter and UTC-3 in summer, so
these land at 07:45 / 13:45 / 18:45 local in winter, an hour later in summer --
always after 07:30 without a timezone lookup. GitHub can delay scheduled runs,
but only ever later, never earlier.

Note that GitHub disables scheduled workflows after 60 days of repository
inactivity. `state.json` records the date of the last check, so a commit lands
at least once a day and keeps the repo active.
