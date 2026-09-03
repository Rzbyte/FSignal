# Evidence checklist

`evidence/` is empty until a live run fills it. Do **not** submit rehearsal output as proof
of live delivery.

Collect, with visible timestamps:

| File | What it must show |
|---|---|
| `01-slack-early.png` | A real EARLY alert: company, founder, batch, source, excerpt, the official-check receipt, and working links |
| `02-directory-absent.png` | `ycombinator.com/companies` searched for that company at that moment, returning nothing |
| `03-ledger.png` | `/ledger` for the same run: suppressions with reasons next to the alert |
| `04-health.png` | `/health` with source states and the snapshot size each verdict was checked against |
| `05-timeline.json` | `/signals/{id}/timeline` — detection, classification, alert |
| `06-restart-silence.png` | Container restart followed by a rescan producing no new alert |
| `07-slack-confirmed.png` | A CONFIRMED alert with a genuinely measured lead time, if one occurs before submission |
| `08-pond.png` | Pond showing the agent connected and healthy |
| `raw/` | The raw source payloads behind each claim |
| `demo-recording-url.txt` | Link to the 2-minute recording |

Before recording:

```bash
python scripts/preflight.py
pytest -q
python scripts/scan_once.py
python scripts/send_test_alert.py
```

Confirm `/health` reports `production_ready: true`, or state plainly which source is not
healthy and why.

## Pond contract

Capture that anonymous `GET /manifest` returns protocol `marketplace-agent` / `1.0`; that
an authenticated `POST /runs` succeeds with `Idempotency-Key == run_id`; that repeating the
identical run returns the same persisted result; that `list_ghosts` and `get_timeline`
return real state; and that Pond shows the agent healthy. See `docs/POND.md`.
