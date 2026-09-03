# Roadmap

FSignal is designed as a persistent monitoring service, not a one-off scraper. **V1 is complete in this repository**: the required four-source monitoring pipeline, persistent state, Slack delivery, Ghost -> Confirmed reconciliation, explainable intelligence, signal timeline, and Pond Protocol integration are already shipped. Future releases expand coverage and precision without replacing the core pipeline.

## V1 — Current submission

**Status: shipped.**

- YC Directory monitoring: complete facet-sliced snapshot (6,199/6,199 companies)
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
