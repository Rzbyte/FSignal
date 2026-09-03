# Evidence

## `raw/` — captured, not typed

Written by `python scripts/capture_evidence.py`. Each file carries the URL it came from and
the moment it was taken, so any claim below can be re-derived rather than trusted. See
[`../docs/EVIDENCE.md`](../docs/EVIDENCE.md) for what each file establishes.

## `raw/pond-runs.json`

The Pond contract, exercised against the live deployment: an authenticated `POST /runs`
returning `200` with a real result, and a byte-identical resend under the same
`Idempotency-Key` returning `identical_response: true`.

That second call is the one that matters. Pond retries, and an agent that re-executed on
a retry would double-count its own work — so the run store is not a nicety, it is the
reason the integration is safe to health-check.

## `live-run.json`

A real production scan: the official snapshot sizes each verdict was checked against, the
full suppression ledger, source health, and every EARLY signal with its persisted
official-check receipt and timeline.

## The claims, and how to check them yourself

### Adalat AI (YC F26) — from X, from the deployment

On 2026-09-03 the X source produced its first live EARLY alert, first from a local run and
then — once the new build reached Railway — from the deployment itself. The artifacts in
`raw/` are the deployment's: `health.json` shows `production_ready: true` with X reporting
`mode: "indexed_fallback"`, and `alerts.sent: 2`.

The founder account posted:

> *"1/ Adalat AI is now backed by Y Combinator. We're the first nonprofit YC has backed in
> nearly five years — and the first Indian-…"*

<https://x.com/Adalat_AI/status/2090071662784086176>

The alert asserted:

> 🔎 Checked against 6,200 YC records · snapshot 21:18 UTC · batch scope F26 · no
> exact-name match · no in-batch prefix match

To verify: open <https://www.ycombinator.com/companies>, filter to **Fall 2026**, search for
*Adalat*. Nothing. A direct crawl at capture time found no company named Adalat anywhere in
the 6,200-record directory — see `raw/yc-directory-check.json`.

This alert is also the first thing the X source has ever produced. Until the indexed
fallback shipped, X answered `402 credits depleted` on every run.

### Lark (YC F26) — from LinkedIn

A founder announcing he is leaving NYU to work on Lark full time. The alert asserted:

> 🔎 Checked against 6,199 YC records · snapshot 17:14 UTC · batch scope F26 · no
> exact-name match · no in-batch prefix match

Still true at the latest capture. Note that a **different** company called `Lark` exists
under Summer 2025, and `Olark` under Summer 2009. Batch-scoped matching is what keeps them
apart — without it this signal would have been filed as already-listed and never alerted.
`raw/yc-directory-check.json` records the distinction explicitly: read
`listed_in_claimed_batch`, not `same_name_in_other_batches`.

### OnePatch (YC F26) — the other direction

During the same session the Fall 2026 roster went from 30 companies to 31. FSignal caught
the addition and raised a **NEW YC COMPANY** alert for `OnePatch` — the second alert type the
task brief asks for, from live data, in the same run that produced the EARLY signal above.

## How much lead does this actually buy? — `raw/lead-time-backtest.json`

The live monitor can only answer that after a company it flagged is later listed,
which takes as long as it takes. `scripts/backtest_lead_time.py` answers it now, from
two timestamps that both belong to somebody else: the founder's post date per the
search index, and YC's own published `launched_at`. Neither is our clock, so the
figure cannot be flattered by how often we poll.

**This is a backtest. None of it was delivered to Slack.** It measures what the
approach would have caught, using the same queries, extraction and matching the live
monitor uses.

Across Fall 2026, Summer 2026, Spring 2026 and Winter 2026:

| | |
|---|---|
| Companies resolved to the directory | 17 |
| Announced publicly **before** YC listed them | **10** |
| …by a week or more | 5 |
| …by two weeks or more | 4 |
| Median lead | 4.4 days |
| Longest lead | 50.0 days (`screenpipe`) |

The median is small because most founders announce the same day the directory
publishes. The value is the tail that does not: `screenpipe` 50 days, `RightNow`
45.8, `Agnost AI` 30.1, `Kimpton` 16.9. Each row in the JSON carries the post URL and
both dates, so any of them can be checked.

Three limits, stated in the artifact itself:

- The index reports a post's date to the day, so each lead is ±1 day.
- Only companies YC has **since** listed can be measured. A company still unlisted —
  Adalat AI, as of writing — has a lead of "at least N days and counting", and those
  are excluded from the average rather than folded in.
- Search ranking, not exhaustive retrieval: a sample of the posts that existed.

The first run of this reported a 200-day median. That was wrong: it measured LinkedIn
**company pages**, whose indexed date is when the page was created or crawled, not
when anyone announced anything. Only dated posts are measured now, and
`tests/test_backtest.py` holds that line.

The raw searches are saved inside the JSON, so
`python scripts/backtest_lead_time.py --replay evidence/raw/lead-time-backtest.json`
re-derives every figure with no API key.

## What the same runs rejected

The large majority of candidates were suppressed with a recorded reason — already-listed
companies, posts with no resolvable company, hiring and demo-day chatter — and more were
filed as `possible` because their confidence fell below the alert threshold. All of it is in
`raw/ledger.json` and served live at `/ledger`.

## Still to capture

These need a person at a screen and cannot be generated:

- `01-slack-early.png`, `02-directory-absent.png`, `03-ledger.png`, `04-health.png`
- `06-restart-silence.png`, `08-pond.png`
- `demo-recording-url.txt`

`../docs/DEMO.md` has the shot list in recording order.
