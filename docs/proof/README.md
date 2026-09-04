# Proof assets

Screenshots that need a person at a screen, and the agent mark. Everything a
machine can capture lives in [`../../evidence/raw/`](../../evidence/raw/) instead,
written by `scripts/capture_evidence.py`.

| File | What it shows |
|---|---|
| `slack_evidence_3.png` | A delivered EARLY alert: company, founder, batch, source, and the founder's own words |
| `slack_evidence_4.png` | The same alert's receipt — 6,200 YC records checked, snapshot timestamped — and the two action buttons |
| `yc_directory_proof.png` | `ycombinator.com/companies` filtered to Fall 2026, searched for the same company: nothing. The independent half of the claim |
| `fsignal_logo.png` | The agent mark, 512×512, for the Pond listing |
| `fsignal_logo_circle.png` | The same mark under a circular crop, which is how a marketplace renders it |
| `fsignal_logo_32.png` | Favicon size, rendered rather than assumed to survive |

The three logo files are re-derivable: `python scripts/make_logo.py` regenerates
all of them and checks the small sizes it has to work at.

Screenshots are not re-derivable, so the ones these replaced are kept in
[`archive/`](archive/) with a note on what each shows that has since been fixed.
