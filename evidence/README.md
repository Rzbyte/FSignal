# Evidence

## `raw/` — captured, not typed

Written by `python scripts/capture_evidence.py`. Each file carries the URL it came from and
the moment it was taken, so any claim below can be re-derived rather than trusted. See
[`../docs/EVIDENCE.md`](../docs/EVIDENCE.md) for what each file establishes.

## `live-run.json`

A real production scan: the official snapshot sizes each verdict was checked against, the
full suppression ledger, source health, and every EARLY signal with its persisted
official-check receipt and timeline.

## The claims, and how to check them yourself

### Adalat AI (YC F26) — from X

On 2026-09-03 the X source produced its first live EARLY alert. The founder account posted:

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
