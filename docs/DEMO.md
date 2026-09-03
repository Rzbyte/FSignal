# Demo

## The 2-minute submission recording

The story is: **find earlier → prove it → don't repeat yourself → confirm.** Do not spend
demo time on a code walkthrough.

**0:00–0:15 — The claim.** Deployed `/health`: four sources, last-success times, and the
snapshot line showing 6,199 YC records. One sentence: *"FSignal finds YC founders who have
announced before YC has listed them — and proves it."*

**0:15–0:45 — The proof.** Trigger a live scan. Slack receives an **EARLY** alert. Read the
receipt line aloud, then switch to `ycombinator.com/companies`, search that company, and
show zero results. This is the moment the submission is won: the reviewer has independently
verified the central claim in about fifteen seconds.

**0:45–1:10 — The precision.** Open `/ledger` for the same run. Show the already-listed
companies the same query returned, each suppressed with a reason, alongside the
identity-less rejections. *"Same query, same minute. One alert, N suppressions, every
decision recorded."*

**1:10–1:30 — No duplicates, persistent.** Rescan: no new alert. Restart the container.
Rescan again: still no new alert. If a second source has corroborated a company, show that
it landed in the original thread rather than as a new alert.

**1:30–1:50 — How much lead this buys.** If no genuine Ghost -> Confirmed has
happened yet, this is the beat that answers the buyer's real question. Open
`evidence/raw/lead-time-backtest.json` and **say the word backtest on camera**: *"Not
alerts we sent — this measures the founder's post date against YC's own published
listing time. Across four batches, 10 of 17 companies were announced publicly before
YC listed them, 4 of them by more than two weeks, the longest by 50 days."* Then show
one row and its post URL so the viewer knows it is checkable.

**1:30–1:50 (alternative) — The receipt.** A **CONFIRMED** alert with a real measured lead time, then
`/signals/{id}/timeline` showing detection → classification → alert → confirmation with
real timestamps. If no genuine confirmation has happened yet, show the ghost inventory and
pending reconciliation instead — **and say so on camera.**

**1:50–2:00 — Operable.** Pond showing the agent connected and healthy, then `/health`.

## Shot list, in recording order

Run `python scripts/capture_evidence.py` first: the JSON it writes into `evidence/raw/` is
the same state these screenshots show, so the two corroborate each other.

| # | File | Open this | Make sure this is visible |
|---|---|---|---|
| 1 | `04-health.png` | `<base>/health` | Four sources; the X source's `mode`; `snapshots[].size` around 6,200 |
| 2 | `01-slack-early.png` | Slack, the alert channel | Header, Company, Founder, Batch, Source, Detected, the OFFICIAL CHECK block, both buttons |
| 3 | `02-directory-absent.png` | `ycombinator.com/companies` | The batch filter set to the alert's batch, the company name typed in the search box, zero results — **in the same frame** |
| 4 | `03-ledger.png` | `<base>/ledger` | The alerted row and suppressed rows together, reason codes readable |
| 5 | `06-restart-silence.png` | terminal | `docker compose restart`, then a rescan, then `alerts.sent` unchanged |
| 6 | `08-pond.png` | Pond | The agent listed, connected, healthy |
| 7 | `07-slack-confirmed.png` | Slack | Only if a real Ghost -> Confirmed has happened. Skip it otherwise and say so. |

Shot 3 is the one that wins the submission. The filter, the query and the empty result must
be in one uncropped frame — a screenshot of an empty search box proves nothing.

### Not allowed

No fake timestamps. No back-dated detection presented as real. No hand-constructed signal
presented as a discovery. No mock presented as live. Nothing that bypasses normalization,
identity extraction, official matching or dedup. If the X path is replay-based, say so on
screen.

## Offline rehearsal

```bash
python scripts/demo_seed.py
python scripts/replay_corpus.py
python scripts/demo_server.py     # http://localhost:8000/ledger
```

This replays 139 **real captured** Serper results through the unmodified production
pipeline. Nothing is hand-fed and no timestamp is invented — but it is still a rehearsal,
not evidence of live Slack delivery.
