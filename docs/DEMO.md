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

**1:30–1:50 — The receipt.** A **CONFIRMED** alert with a real measured lead time, then
`/signals/{id}/timeline` showing detection → classification → alert → confirmation with
real timestamps. If no genuine confirmation has happened yet, show the ghost inventory and
pending reconciliation instead — **and say so on camera.**

**1:50–2:00 — Operable.** Pond showing the agent connected and healthy, then `/health`.

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
