# FSignal submission checklist

Every ticked box below is independently verifiable by the command or artifact named next
to it. Unticked boxes state what is actually blocking them.

## Code and tests

- [x] Public repository with complete runnable source — `git log`, `pip install -r requirements.txt`
- [x] `.env` never committed — `git log --all --full-history -- .env` is empty
- [x] `.env` and `.venv` excluded from the Docker build context — `.dockerignore`
- [x] Test suite green — `pytest -q` (151 tests), with no credentials configured
- [x] CI-equivalent run verified — the workflow's exact steps (`pip install -r requirements.txt`, `pytest -q`) pass on Python 3.12 in a
      clean container with no `.env` present. GitHub Actions itself has not run until this is pushed.
- [x] Precision enforced in CI, not just asserted — `pytest tests/test_precision.py`

## Sources (live)

- [x] **YC Directory** — full facet-sliced crawl returns 6,199 / 6,199 companies in ~8s,
      including the currently-filling batch. `python scripts/scan_once.py`
- [x] **Speedrun** — canonical a16z API (the one `speedrun.a16z.com` calls itself) returns
      261 companies across SR001–SR007
- [x] **LinkedIn** — live Serper queries return real public posts; adjudication verified
      against the live directory
- [ ] **X** — implementation complete and covered by tests, but the account returns
      `402 credits depleted`; recent search requires a paid X plan. `/health` reports this
      source as `billing_blocked` rather than as a generic failure.

## Behaviour

- [x] Persistent stateful monitoring — SQLite + WAL on a mounted volume
- [x] Runs continuously — one asyncio task per source, adaptive backoff, failure isolation
- [x] No duplicate alerts — one alert per company per stage; restart + rescan is silent
- [x] Early detection with a defensible verdict — every EARLY alert stores and displays the
      snapshot size, snapshot time, batch scope, and every match method attempted
- [x] Suppression is auditable — every rejected candidate carries a reason code (`/ledger`)
- [x] Slack alerts carry company, source, description/excerpt, and link
- [x] Architecture supports new social platforms — a source needs only `name` + `collect()`

## Deployment and evidence

- [x] Docker Compose with persistent volume and healthcheck
- [ ] Deployed at a public HTTPS URL — not yet deployed
- [ ] Pond agent published and healthy — needs the deployment above; `POND_ACCESS_KEY` and
      `PUBLIC_BASE_URL` are still unset. The protocol implementation itself is complete and
      tested (`pytest -k pond`).
- [ ] Real Slack screenshots in `evidence/` — pending a live run against the deployment
- [ ] 2-minute demo recording — pending the above

## Not claimed

- Live X polling has never run successfully on this account.
- The offline rehearsal (`scripts/replay_corpus.py`) replays **real captured payloads**
  through production code. It is not evidence of live Slack delivery.
- No lead-time figure is reported unless it was measured from a real detection and a real
  later listing.
