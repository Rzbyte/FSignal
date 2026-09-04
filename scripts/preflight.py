"""Check the configuration before running FSignal for real.

Two tiers, because they answer different questions.

REQUIRED is "can this bot work at all": somewhere to deliver alerts, and at
least one way to find them. Anything missing here is a stop.

OPTIONAL is "which capabilities are switched on". A missing X token or Pond key
is a smaller product, not a broken one -- so those report SKIP and the script
still exits 0. This used to fail the run, which meant the very first command in
"Verifying it works" told a new operator their setup was broken because they had
declined a feature the README calls optional.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import settings

# (label, ok, hint)
required = [
    ("Slack bot token", bool(settings.slack_bot_token), "SLACK_BOT_TOKEN=xoxb-..."),
    ("Slack destination", bool(settings.slack_channel_id), "SLACK_CHANNEL_ID=C... or D..."),
    (
        "A way to find signals",
        bool(settings.serper_api_key or settings.x_bearer_token),
        "SERPER_API_KEY (recommended) or X_BEARER_TOKEN",
    ),
    ("Production mode", not settings.demo_mode, "DEMO_MODE=false"),
    ("YC Directory URL", bool(settings.yc_directory_url), "YC_DIRECTORY_URL"),
    ("Speedrun URL", bool(settings.speedrun_url), "SPEEDRUN_URL"),
]

optional = [
    (
        "LinkedIn + X discovery",
        bool(settings.serper_api_key),
        "SERPER_API_KEY",
        "both social sources are off without it",
    ),
    (
        "X native API",
        bool(settings.x_bearer_token),
        "X_BEARER_TOKEN",
        "X falls back to indexed search, which is fine",
    ),
    (
        "Startup scan",
        settings.startup_scan,
        "STARTUP_SCAN=true",
        "first scan waits one full interval instead",
    ),
    (
        "Pond publishing",
        bool(settings.pond_access_key),
        "POND_ACCESS_KEY",
        "the bot runs; it just is not published on Pond",
    ),
    (
        "Public deployment URL",
        settings.public_base_url.startswith("https://")
        and "your-deployment" not in settings.public_base_url,
        "PUBLIC_BASE_URL=https://...",
        "only needed to publish on Pond",
    ),
]

print("FSignal preflight\n")

print("REQUIRED")
failed = 0
for label, ok, hint in required:
    print(f"  {'PASS' if ok else 'FAIL':4}  {label:<24} {hint}")
    failed += int(not ok)

print("\nOPTIONAL")
for label, ok, hint, consequence in optional:
    mark = "PASS" if ok else "SKIP"
    note = hint if ok else f"{hint} -- {consequence}"
    print(f"  {mark:4}  {label:<24} {note}")

if failed:
    print(f"\n{failed} required setting(s) missing. See docs/INSTALL.md.")
    raise SystemExit(1)

print("\nReady. Next: pytest -q, then python scripts/scan_once.py")
