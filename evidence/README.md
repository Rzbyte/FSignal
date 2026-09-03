# Evidence

## `live-run.json`

A real production scan. Contains the official snapshot sizes each verdict was checked
against, the full suppression ledger, source health, and every EARLY signal with its
persisted official-check receipt and timeline.

### The claim, and how to check it yourself

The run alerted on **Lark (YC F26)** — a founder announcing on LinkedIn that he is leaving
NYU to work on it full time. At detection time the alert asserted:

> 🔎 Checked against 6,199 YC records · snapshot 17:14 UTC · batch scope F26 · no
> exact-name match · no in-batch prefix match

To verify: open <https://www.ycombinator.com/companies>, filter to **Fall 2026**, and search
for *Lark*. It is not there. The batch listed 30 companies at capture time:

> Antropi Robotics, Asakana, Ascii, Capveon, Collar, Covera, Degla Inc, Forward, GodHands,
> Hemlock, Lambda Robotics, Lantern AI, Lightfield, Maritime, Nodus Compute, Orca Aerospace,
> Qokedas, Quippy, Redoubt Insurance, RightNow, Sentient OS, Simantic, Simulithic, Talos,
> The Subvocal Company, Veeza AI, Vorelios, Workers IO, antimattr, herdr

Note that a **different** company called `Lark` exists in the directory under Summer 2025.
Batch-scoped matching is what keeps those apart — without it this signal would have been
wrongly filed as already-listed and never alerted.

### What the same run rejected

Of the candidates evaluated, the large majority were suppressed with a recorded reason —
already-listed companies, posts with no resolvable company, application and demo-day
chatter — and several more were filed as `possible` because their confidence fell below the
alert threshold. All of it is in `live-run.json` under `ledger` and `suppressed_sample`,
and served live at `/ledger`.

## Still to capture

These need a public deployment and are not yet done:

- `01-slack-early.png` — screenshot of the delivered Slack alert
- `02-directory-absent.png` — the YC directory search showing the company absent
- `03-ledger.png`, `04-health.png` — the served pages
- `08-pond.png` — Pond showing the agent connected
- `demo-recording-url.txt` — the 2-minute recording

See `docs/EVIDENCE.md`.
