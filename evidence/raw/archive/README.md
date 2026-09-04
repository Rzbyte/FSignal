# Superseded captures

Kept because they happened, moved because they no longer describe the running
service. Nothing here is wrong; each is a real capture with the timestamp it was
taken at. They are out of `raw/` so that everything left in `raw/` can be read
against the live deployment without a reader having to work out which file is
current.

| File | Captured | Why it moved |
|---|---|---|
| `timeline-9.json` | 2026-09-03 22:24 UTC | The same signal as `raw/timeline-14.json`, an earlier view of it. Two captures of one signal in the same directory invites the reader to diff them for meaning that is not there. |
| `live-run.json` | 2026-09-03 17:15 UTC | Records `x: billing_blocked`. True when it was taken and the reason the indexed fallback exists, but every other artifact and the running service now report `x: healthy` on `mode: indexed_fallback`. Left in `raw/` it read as the two disagreeing about the present rather than describing different moments. |
| `timeline-27.json` | 2026-09-03 22:53 UTC | Records the company as `@Adalat_AI`, the handle form. Corroborating posts for one company are now shown under the company's written name, so read beside the dashboard this looks like a second, separate discovery. It is not, and never was. |

Neither has been edited. To re-derive the current equivalents:

    python scripts/capture_evidence.py --base-url https://fsignal-production.up.railway.app
