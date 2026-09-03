# FSignal submission checklist

Every ticked box below is independently verifiable by the command or artifact named next
to it. Unticked boxes state what is actually blocking them.

## Against the eligibility requirements

The eight bullets a valid submission must satisfy, and how to check each in under a
minute. Seven are met and verifiable right now; the eighth needs a person at a screen.

| # | Requirement | Status | Check it |
|---|---|---|---|
| 1 | Working repo, runnable source, clear setup | **met** | `git clone` → `pytest -q` → 243 pass with **zero credentials configured**. `.env` appears nowhere in history. Non-technical path: [`docs/INSTALL.md`](docs/INSTALL.md) |
| 2 | All 4 sources monitored, each clearly implemented | **met** | [`/health`](https://fsignal-production.up.railway.app/health) → four sources `healthy`, `production_ready: true`. Each reports the path it used |
| 3 | Persistent stateful monitoring, no re-alerts | **met** | State lives on a mounted volume and has survived every redeploy. Three separate posts about Adalat AI produced **one** alert plus two threaded corroborations |
| 4 | Early-detection logic | **met** | `Adalat AI (YC F26)` was alerted on and is **still absent** from the 31-company Fall 2026 roster. Filter the directory to Fall 2026 and search it |
| 5 | Slack: company, source, description, link | **met** | All four in every alert, plus the founder and a `Reach out` line. Rendered payloads in [`docs/DEMO.md`](docs/DEMO.md) |
| 6 | Pond agent integration | **met** | Anonymous `GET /manifest` → `marketplace-agent` `1.0`, four actions. `POST /runs` without a token → `401`; with one → `200` and a real result, and a byte-identical resend under the same `Idempotency-Key` returns the *persisted* result rather than re-running. Captured in [`evidence/raw/pond-runs.json`](evidence/raw/pond-runs.json) |
| 7 | **Proof: screenshot or recording of a real Slack alert** | **outstanding** | Needs a person at a screen. Shot list in [`docs/DEMO.md`](docs/DEMO.md) |
| 8 | Future upgradability; runs on Pond infrastructure | **met** | A new platform needs only `name` + `async collect()`. Pond V1 is served by the same always-on process that does the monitoring |

**Not a one-shot script.** One asyncio task per source, independent intervals, adaptive
backoff, failure isolation, and a persistent outbox with retry and dead-lettering.
`/health` shows each source's last run and next run.

## Code and tests

- [x] Public repository with complete runnable source — <https://github.com/Rzbyte/FSignal>
- [x] `.env` never committed — `git log --all --full-history -- .env` is empty
- [x] `.env` and `.venv` excluded from the Docker build context — `.dockerignore`
- [x] Test suite green — `pytest -q` (238 tests), with no credentials configured
- [x] Precision enforced in CI, not just asserted — `pytest tests/test_precision.py`
- [x] Setup a non-technical operator can complete — `docs/INSTALL.md`, with prerequisites,
      the Slack click path, Windows and macOS, permanent hosting, and a troubleshooting
      table keyed to exact error text. `python scripts/preflight.py` passes on the minimum
      configuration (Slack + one search key) instead of failing on optional features.

## Sources (live)

- [x] **YC Directory** — full facet-sliced crawl returns 6,200 / 6,200 companies in ~8s,
      including the currently-filling batch. `python scripts/scan_once.py`
- [x] **Speedrun** — canonical a16z API (the one `speedrun.a16z.com` calls itself) returns
      261 companies across SR001–SR007
- [x] **LinkedIn** — live Serper queries return real public posts; adjudication verified
      against the live directory
- [x] **X** — live. Native recent search is a paid X product and this account gets
      `402 credits depleted`, so the source falls back to publicly indexed X URLs and
      keeps reporting. A single live run returned 37 candidates across YC F26, S26 and
      Speedrun SR007 and produced a real EARLY alert for **Adalat AI (YC F26)**.
      `/health` reports `mode: "indexed_fallback"`, the mode is persisted on every signal,
      and Slack renders `Source: X (indexed search)` — an indexed result is never shown as
      a native one.

## Behaviour

- [x] Persistent stateful monitoring — SQLite + WAL on a mounted volume
- [x] Runs continuously — one asyncio task per source, adaptive backoff, failure isolation
- [x] No duplicate alerts — **demonstrated live on the deployment.** Three separate X
      posts about Adalat AI, including one third-party post that named the company as
      `@Adalat_AI`, produced **one** EARLY alert and two threaded corroborations: all three
      normalise to the same `company_key` (`yc:F26:adalatai`). A redeploy in between
      re-alerted none of them. A post found through both X paths also collapses to one row,
      because the indexed collector keys on the X post id rather than a hash of the URL.
- [x] Early detection with a defensible verdict — every EARLY alert stores and displays the
      snapshot size, snapshot time, batch scope, and every match method attempted
- [x] Handles resolve to the companies they name — found by running the brief's own
      example (`x.com/beknabdik`) through the matcher: that founder builds **Speko**, which
      the directory lists under Summer 2026, but the post identifies it as `@speko_ai` and
      a name comparison called those different companies. Three live candidates
      (`@speko_ai`, `@florin_hq`, `@tryStudioai`) now correctly resolve to `Speko`,
      `Florin` and `Studio` instead of being eligible for a false EARLY alert.
- [x] **New official listings alert too** — `✅ NEW YC COMPANY`, the task brief's second
      example. Raised live for `OnePatch` when the Fall 2026 roster went from 30 to 31.
- [x] Suppression is auditable — every rejected candidate carries a reason code (`/ledger`),
      and an alerted row now carries its `signal_id` so `/ledger` links to the timeline
- [x] The landing page opens with what the bot found — company, batch, program, source,
      confidence, and links to both the founder's post and that signal's full history,
      one row per company
- [x] Slack alerts carry company, founder, source, description/excerpt, and link
- [x] **The alert ends where the outreach starts** — a `Reach out` line with the
      founder's actual profile (X handle, or the author slug LinkedIn embeds in its own
      post URLs) and the company's site. Links, not buttons: two buttons is a call to
      action, five is none. No line at all rather than a label with nothing behind it.
- [x] **Speedrun produces signals** — it never had. The gap was extraction, not queries:
      `building @infragrid` and `starting @codos_ai` are the shapes founders use on X,
      and the extractor was written against LinkedIn prose. `@codos_ai` now resolves to
      the listed `Codos (SR007)`; `@infragrid` and `MUNARI LABS` are in neither
      directory and sit at `possible` because their posts name no cohort.
- [x] Architecture supports new social platforms — a source needs only `name` + `collect()`

## Deployment and evidence

- [x] Docker Compose with persistent volume and healthcheck
- [x] Deployed at a public HTTPS URL — <https://fsignal-production.up.railway.app>
- [x] Deployment state actually persists — a volume is mounted at `/app/data` and
      `DATABASE_PATH` points inside it. It was not, until a redeploy during this work
      emptied `/ledger` and exposed it: the container's own disk is replaced on every
      deploy, so the bot was re-alerting from an empty database each time. Nothing
      failed visibly, which is why it survived a green checklist.
- [x] Pond agent published and healthy — `POND_ACCESS_KEY` and `PUBLIC_BASE_URL` set, agent
      registered
- [x] **Pond idempotency proven, not asserted** — `evidence/raw/pond-runs.json` holds one
      authenticated `POST /runs` and a byte-identical resend under the same
      `Idempotency-Key`, with `identical_response: true`. Pond retries; an agent that
      re-executed on a retry would double-count, which is why the run store exists.
- [x] Polling paced to survive review — the two metered social sources run every 4 hours
      (the brief allows 8); the free directory sources stay tight so new listings and
      Ghost -> Confirmed reconciliation are not delayed. ~70 search credits/day.
- [x] Deploys from GitHub — the service was pinned to an uploaded snapshot, so pushes
      changed nothing and **Redeploy** rebuilt the same old code. Now connected to
      `Rzbyte/FSignal` on `main`.
- [x] `/health` reports `production_ready: true` — all four sources healthy, X as
      `mode: "indexed_fallback"`
- [x] **The deployment has delivered real Slack alerts** — `alerts.sent: 3`: one EARLY
      signal for `Adalat AI (YC F26)` found on X, plus two corroborations in its thread.
      Ledger alongside them: 19 already-listed, 19 possible, 34 suppressed with reason codes.
- [x] **Lead time is measured, not asserted** — against YC's own published
      `launched_at` rather than our polling, so the number cannot drift with the scan
      interval. `scripts/backtest_lead_time.py` quantifies it across four batches:
      10 of 17 measurable companies were publicly announced before YC listed them,
      4 by two weeks or more, the longest by 50 days. Labelled as a backtest
      everywhere; the raw searches ship inside the JSON so the figures re-derive with
      `--replay` and no API key.
- [x] Machine-capturable evidence captured **from the deployment** —
      `python scripts/capture_evidence.py` wrote `evidence/raw/`: served state, per-signal
      timelines, and a directory crawl run independently of the deployment confirming
      Adalat AI is absent from all 6,200 records and from the 31-company Fall 2026 roster
- [ ] Slack screenshots and the 2-minute recording — needs a person at a screen.
      `docs/DEMO.md` has the shot list in recording order.

## Not claimed

- Live **native** X polling has never run successfully on this account. The X source is
  live through indexed search, and every layer says so.
- The offline rehearsal (`scripts/replay_corpus.py`) replays **real captured payloads**
  through production code. It is not evidence of live Slack delivery.
- No lead-time figure is reported unless it was measured from a real detection and a real
  later listing. None has occurred yet.
