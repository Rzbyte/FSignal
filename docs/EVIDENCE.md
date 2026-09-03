# Evidence checklist

Two kinds of artifact, split by what can produce them honestly.

**Machine-captured.** Served state, the Pond contract, and the independent directory check
are HTTP. A person copying them by hand introduces the one thing this repository refuses to
allow — a claim nobody can re-derive — so a script fetches them and stamps each with the
moment it was taken:

```bash
python scripts/capture_evidence.py
python scripts/capture_evidence.py --base-url http://localhost:8000   # or a local run
python scripts/backtest_lead_time.py --batches "Fall 2026,Summer 2026,Spring 2026,Winter 2026"
```

| File in `evidence/raw/` | What it establishes |
|---|---|
| `health.json` | Source health **and mode**, the snapshot each verdict was checked against, ledger counts, alert-delivery state |
| `ledger.json` | Every candidate evaluated and its verdict, each suppression with a reason code — the precision claim |
| `manifest.json` | Anonymous `GET /manifest` returns protocol `marketplace-agent` / `1.0` |
| `pond-runs.json` | An authenticated `POST /runs` and a byte-identical resend under the same `Idempotency-Key`; `identical_response` must be `true` |
| `timeline-<id>.json` | One alerted signal's full history: detection, classification, delivery |
| `yc-directory-check.json` | The directory crawled **independently of the deployment**: whether each alerted company is listed in the batch it claimed, plus that batch's full roster |
| `lead-time-backtest.json` | How much earlier than the directory this finds companies, measured from the founder's post date against YC's own `launched_at` — **a backtest, not delivered alerts** |

`pond-runs.json` needs `POND_ACCESS_KEY` in the environment you run the script from; without
it the script says so and continues.

`yc-directory-check.json` is the artifact that makes an EARLY alert evidence rather than an
assertion. Read `listed_in_claimed_batch`, not `same_name_in_other_batches` — a company of
the same name in an older batch does not contradict "not listed in F26". A real `Lark`
exists under Summer 2025, and batch-scoped matching is precisely what stops it suppressing
the Fall 2026 signal.

**Needs a person at a screen.** Nothing can generate these, and nothing should try:

| File | What it must show |
|---|---|
| `01-slack-early.png` | A real EARLY alert: company, founder, batch, source, excerpt, the official-check receipt, working links |
| `02-directory-absent.png` | `ycombinator.com/companies` filtered to that batch and searched for that company, returning nothing |
| `03-ledger.png` | `/ledger` for the same run: suppressions with reasons beside the alert |
| `04-health.png` | `/health` with source states, modes, and the snapshot size each verdict was checked against |
| `06-restart-silence.png` | Container restart followed by a rescan producing no new alert |
| `07-slack-confirmed.png` | A CONFIRMED alert with a genuinely measured lead time, **if one occurs before submission** |
| `08-pond.png` | Pond showing the agent connected and healthy |
| `demo-recording-url.txt` | Link to the 2-minute recording |

`docs/DEMO.md` has the shot list in recording order.

## Before recording

```bash
python scripts/preflight.py       # required checks must PASS; SKIP is fine
pytest -q
python scripts/scan_once.py
python scripts/capture_evidence.py
```

`/health` should report `production_ready: true`. If a source is not healthy, say which and
why on camera rather than working around it.

Note the `mode` field on the X source. `native` means X's own recent search answered;
`indexed_fallback` means the account has no paid X plan and the same hunt ran against
publicly indexed X URLs. Both are real monitoring and the alert labels which it was — but
say which one is running rather than letting a viewer assume.

## Pond contract

Capture that anonymous `GET /manifest` returns protocol `marketplace-agent` / `1.0`; that an
authenticated `POST /runs` succeeds with `Idempotency-Key == run_id`; that repeating the
identical run returns the same persisted result; that `list_ghosts` and `get_timeline` return
real state; and that Pond shows the agent healthy. `capture_evidence.py` does the first three.
See `docs/POND.md`.

## Reporting the backtest

`lead-time-backtest.json` is the one artifact here that is not a record of something
that happened. Say "backtest" out loud whenever it is on screen. It answers "how much
lead does this approach buy", never "this is what we sent" — the delivered alerts are
in `ledger.json` and the Slack screenshots, and those are a different claim.

Quote the shape, not just the median: most founders announce the same day the directory
publishes, and the value is in the tail that does not. `10 of 17 measurable companies
were announced before YC listed them, 4 of them by two weeks or more, the longest by 50
days` is both truthful and stronger than any single number.

## Not allowed

Rehearsal output presented as live delivery. Back-dated detection. Hand-constructed signals.
A lead-time figure that was not measured from a real detection and a real later listing.
