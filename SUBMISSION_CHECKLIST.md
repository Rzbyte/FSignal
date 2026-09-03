# FSignal submission checklist

Every ticked box below is independently verifiable by the command or artifact named next
to it. Unticked boxes state what is actually blocking them.

## Code and tests

- [x] Public repository with complete runnable source — <https://github.com/Rzbyte/FSignal>
- [x] `.env` never committed — `git log --all --full-history -- .env` is empty
- [x] `.env` and `.venv` excluded from the Docker build context — `.dockerignore`
- [x] Test suite green — `pytest -q` (185 tests), with no credentials configured
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
- [x] No duplicate alerts — one alert per company per stage; restart + rescan is silent.
      A post found through both X paths collapses to one row: the indexed collector keys on
      the X post id, not a hash of the URL.
- [x] Early detection with a defensible verdict — every EARLY alert stores and displays the
      snapshot size, snapshot time, batch scope, and every match method attempted
- [x] **New official listings alert too** — `✅ NEW YC COMPANY`, the task brief's second
      example. Raised live for `OnePatch` when the Fall 2026 roster went from 30 to 31.
- [x] Suppression is auditable — every rejected candidate carries a reason code (`/ledger`),
      and an alerted row now carries its `signal_id` so `/ledger` links to the timeline
- [x] Slack alerts carry company, founder, source, description/excerpt, and link
- [x] Architecture supports new social platforms — a source needs only `name` + `collect()`

## Deployment and evidence

- [x] Docker Compose with persistent volume and healthcheck
- [x] Deployed at a public HTTPS URL — <https://fsignal-production.up.railway.app>
- [x] Pond agent published and healthy — `POND_ACCESS_KEY` and `PUBLIC_BASE_URL` set, agent
      registered
- [x] Polling paced to survive review — the two metered social sources run every 4 hours
      (the brief allows 8); the free directory sources stay tight so new listings and
      Ghost -> Confirmed reconciliation are not delayed. ~70 search credits/day.
- [x] Machine-capturable evidence captured — `python scripts/capture_evidence.py` writes
      served state, the Pond idempotency proof, per-signal timelines, and an independent
      directory crawl into `evidence/raw/`
- [ ] Slack screenshots and the 2-minute recording — needs a person at a screen.
      `docs/DEMO.md` has the shot list in recording order.

## Not claimed

- Live **native** X polling has never run successfully on this account. The X source is
  live through indexed search, and every layer says so.
- The offline rehearsal (`scripts/replay_corpus.py`) replays **real captured payloads**
  through production code. It is not evidence of live Slack delivery.
- No lead-time figure is reported unless it was measured from a real detection and a real
  later listing. None has occurred yet.
