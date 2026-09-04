# Roadmap

FSignal is designed as a persistent monitoring service, not a one-off scraper. **V1 is complete in this repository**: the required four-source monitoring pipeline, persistent state, Slack delivery, Ghost -> Confirmed reconciliation, explainable intelligence, signal timeline, and Pond Protocol integration are already shipped. Future releases expand coverage and precision without replacing the core pipeline.

## V1 — Current submission

**Status: shipped.**

- YC Directory monitoring: complete facet-sliced snapshot, every company the
  directory lists (6,200 of 6,200 at the time of writing)
- Speedrun monitoring through a16z's own first-party API, with a labelled fallback
- X founder-announcement monitoring, native-first with an indexed-search fallback so
  the source keeps reporting on an account without a paid X plan
- Public indexed LinkedIn signal monitoring
- Batch targeting derived from the directory rather than hardcoded
- Identity gate that refuses to alert without a defensible company name
- Suppression ledger recording a reason code for every rejected candidate
- Conservative official matching: domain, exact name, then batch-scoped prefix
- EARLY/GHOST lifecycle
- Ghost -> Confirmed reconciliation
- Measured early-detection lead time
- **A single explainable confidence scale** with persisted evidence
- **A persisted official-check receipt** on every EARLY verdict
- **GTM priority score** for outbound triage
- **Persistent signal timeline** for detection/alert/confirmation auditability
- Persistent SQLite state, company-level deduplication and threaded corroboration
- Persistent Slack outbox with retry and dead-lettering
- Startup scan + recurring scheduler
- Per-source health history, including which collection path answered
- Rich Slack Block Kit alerts with source/company links
- Pond Protocol V1 `/manifest` and `/runs`
- Pond actions: `scan_now`, `get_status`, `list_ghosts`, `get_timeline`
- Pond idempotency persistence
- Docker deployment and CI tests

## Getting to real time

The brief asks for "real-time alerts" and then allows a cadence of up to eight hours.
The shipped configuration sits between them, and this is exactly what it would take to
close the gap — no redesign in any of it.

### Where the latency actually is today

| Source | Interval | What sets it |
|---|---|---|
| YC Directory | 60 min | Nothing. It is free to poll. A newly listed company is caught within the hour. |
| Speedrun | 120 min | Same. |
| X | 240 min | Search credits, and the refresh rate of the index behind them. |
| LinkedIn | 240 min | Same. |

So half the system is already near real time. The founder-post half is not, and the
reason is worth being precise about, because it decides which upgrades are worth buying.

### Tier 1 — Lower the interval. Free, and largely pointless.

`X_SCAN_INTERVAL_MINUTES` and `LINKEDIN_SCAN_INTERVAL_MINUTES` are environment
variables. No code changes.

But against a search index this buys very little. **The slow component is the index, not
the polling.** Adalat AI's post went up on 19 August and was still surfacing as a fresh
result more than two weeks later; two scans twelve minutes apart returned zero new items
between them. Polling every fifteen minutes would spend sixteen times the credits to
re-read the same ten results.

Worth doing only alongside Tier 2.

### Tier 2 — The native X API. ~$200/month, and the code is already written.

This is the real path to near real time, and nothing needs building for it.

`XSource.collect_native` already implements `since_id` watermarking: it asks X only for
posts newer than the last one it processed, and persists that cursor per query. That
makes a tight interval *cheap* rather than wasteful — the opposite of the indexed path.

Set `X_BEARER_TOKEN` to a key on a paid plan and lower `X_SCAN_INTERVAL_MINUTES`. The
source switches from `indexed_fallback` to `native` on its own, `/health` reports the
change, and the Slack badge stops saying "indexed search". A minute or two of latency on
X becomes affordable.

### Tier 3 — Streaming. Sub-second on X; impossible on LinkedIn.

X's filtered stream would push posts as they are written. The source-adapter boundary
already supports it: a source needs only a `name` and an `async collect()`, and a
streaming adapter can buffer into the same shape. Ghost → Confirmed semantics, dedup,
Slack delivery and Pond handling would not change.

**LinkedIn has no public equivalent and will not get one.** There is no post-firehose
outside approved partner access, so indexed public search is the ceiling for that source
short of a LinkedIn partnership. An honest roadmap says so rather than implying parity.

### What this means for the claim

The product's lead is measured in days — a median of 4.4 and a tail past 50 in the
backtest. A four-hour detection interval is 0.17 days against that. Real time is worth
buying when the competition is other monitors; it is close to irrelevant when the
competition is the directory.

## V1.1 — Cross-source signal quality

Goal: improve precision and corroboration while preserving the same external behavior.

- cross-source founder/company identity graph
- source trust/reliability weighting
- operator-tunable suppression rules on top of the existing ledger
- richer domain/company enrichment
- better retry/backoff and source-rate-limit handling
- alert-quality analytics

## V1.2 — Additional discovery sources

Goal: expand early-signal coverage for recurring Pond tasks.

Candidate adapters:

- Product Hunt
- Bluesky
- Reddit
- Hacker News
- Threads
- additional accelerator/program directories

Each new platform plugs into the source-adapter boundary and reuses extraction, intelligence, matching, persistence, Slack, and Pond layers.

## V1.3 — GTM enrichment

Goal: add context without delaying the core alert.

- company/industry enrichment
- founder/contact enrichment
- funding and hiring signals
- configurable watchlists by sector or cohort
- optional outreach context/suggestions
- CRM export/webhook adapters

These features remain downstream of discovery so they cannot block or delay a launch alert.

## V2 — Team and production scale

- PostgreSQL persistence
- distributed scheduler / worker queue
- multi-workspace Slack support
- configurable alert routing and filters
- historical lead-time analytics
- source-level operations dashboard
- admin controls and audit history
- horizontal worker scaling

## Upgrade contract

```text
new source adapter
      ↓
normalized source record
      ↓
existing extraction + intelligence
      ↓
existing matching + persistent lifecycle
      ↓
existing Slack + Pond interfaces
```

Adding a social platform must not require changes to Ghost -> Confirmed semantics, alert delivery, Pond Protocol handling, or existing source implementations.
