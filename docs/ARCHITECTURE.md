# Architecture

```text
                    ┌── YC Directory (Algolia, facet-sliced) ──┐
per-source          ├── Speedrun (a16z first-party API) ───────┴──→ official snapshot
scheduler,          │                                                 (size + timestamp)
one asyncio task    ├── X (recent search, since_id watermark) ─┐            │
each                └── LinkedIn (indexed public search) ──────┴──→ identity gate
                                                                     │        │
                                                          suppression ledger  │
                                                                              ↓
                                                            scoring → official check
                                                                              ↓
                                              EARLY (+ receipt) / already-listed / possible
                                                                              ↓
                                                    company-level dedup → outbox → Slack
                                                                              ↓
                                        later official listing → CONFIRMED + measured lead
```

## Source adapters

Everything source-specific lives in `app/sources/`. A source needs a `name` and an async
`collect()`; the scanner injects `targets` (which batches are currently filling) before
each social run. Adding Product Hunt, Bluesky, Reddit or approved LinkedIn access requires
no change to extraction, scoring, matching, persistence, dedup, Slack or Pond.

### YC Directory

`ycombinator.com/companies` is a client-side Algolia application. The page ships a public,
index-restricted search key in `window.AlgoliaOpts`; FSignal uses the same client-facing
path rather than scraping rendered HTML.

A single Algolia query cannot return more than 1,000 hits (`paginationLimitedTo`), so a
**full** crawl enumerates the `batch` facet and issues one filtered query per batch — 50
queries, 6,199 companies, about 8 seconds. Between full crawls a **hot** refresh runs one
recent-window query against the launch-date replica, which is enough to catch new
listings. If the facet totals stop matching the index, the crawl raises rather than
persisting a partial snapshot, because a partial snapshot silently poisons every EARLY
verdict.

### Speedrun

`speedrun.a16z.com/companies` is a Next.js application that fetches
`speedrun-api.a16z.com/api/companies/companies/` itself, so that endpoint is the canonical
client-facing data path. The talent-network URL is resilience only; runs that use it are
labelled `fallback` so no evidence taken from it can be mistaken for canonical operation.

### X and LinkedIn

Both build their queries from the active batches the directory reports, and refuse to run
(status `waiting`, not an error) when no snapshot exists — without an adjudicator, "not
listed" would be unsupportable. X persists a `since_id` watermark per query to bound cost;
the `UNIQUE(source, external_id)` constraint remains the correctness guarantee for dedup.

## Scheduler

One asyncio task per monitored entity, each on its own interval.

| Task | Default | Env |
|---|---|---|
| `x` | 10 min | `X_SCAN_INTERVAL_MINUTES` |
| `linkedin` | 15 min | `LINKEDIN_SCAN_INTERVAL_MINUTES` |
| `yc_directory` | 20 min | `YC_SCAN_INTERVAL_MINUTES` |
| `speedrun` | 30 min | `SPEEDRUN_SCAN_INTERVAL_MINUTES` |
| `ghost_reconciliation` | 10 min | `GHOST_RECHECK_INTERVAL_MINUTES` |

Failures back off exponentially, capped at one hour, and never disturb other tasks.
`not_configured`, `waiting` and `billing_blocked` are reported as themselves rather than
folded into a generic failure, because an operator responds to each differently.

## Identity and scoring

`app/extract.py` resolves identity only. It anchors on a program tag, an acceptance claim,
or a possessive phrase, then walks outward to a phrase boundary — a single wide regex
reliably swallows page chrome instead ("Jane Doe's Post - LinkedIn EVO HQ (YC F26)").
Candidates are validated against fragment tokens, generic name components and page
furniture, and where several spans are plausible the one that recurs in the post wins.

`app/intelligence.py` holds the only confidence scale. An earlier version scored the same
post twice and merged the results with `max()`, producing a number on no scale at all.
Confidence measures evidence that this is a genuine announcement; GTM priority measures
how actionable it is now. Both are deterministic and both persist their evidence.

## Official check

`app/matcher.py` resolves domain first, then exact normalized name, then a batch-scoped
strict token-prefix. Never fuzzy similarity. Batch identity is alias-aware: Spring 2026
appears in live posts as both `X26` and `P26`. The resulting `OfficialCheck` — methods
tried, batch scope, records compared, snapshot size and age — is persisted with the signal
and rendered in Slack.

## State

| Table | Purpose |
|---|---|
| `official_companies` | Official records and first-seen timestamps |
| `official_snapshots` | Snapshot size, mode, index and active batches per source |
| `social_signals` | Deduplicated discoveries, scores, evidence, official-check receipt |
| `candidate_ledger` | Every candidate evaluated and why it was or was not alerted |
| `company_alerts` | Per-company alert state and the Slack thread to reply into |
| `timeline_events` | Auditable detection → classification → alert → confirmation |
| `alert_outbox` | Durable Slack queue with retry and dead-letter |
| `watermarks` | Source cursors (X `since_id`) |
| `source_runs` | Health history for every collector |
| `pond_runs` | Persisted Pond idempotency responses |

```text
ghost → ghost_classified → slack_alert_sent → official_confirmed → confirmation sent
possible          (weak evidence or stale snapshot: recorded, never alerted)
already_official  (stored for audit and dedup, no early alert)
```

## Delivery

Persistence precedes notification. The outbox distinguishes per-alert failures — which
dead-letter after a capped number of attempts so one undeliverable message cannot mute the
queue behind it — from global failures like an expired token, which pause the flush and
retry in order. `flush_alerts()` is guarded by a lock so concurrent source tasks cannot
double-deliver.

SQLite must live on persistent storage and the service should run one uvicorn worker,
because the scheduler is process-local.
