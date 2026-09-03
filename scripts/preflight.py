"""Credential/configuration preflight before recording the real submission demo."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import settings  # noqa: E402


checks = [
    ("Production mode", not settings.demo_mode, "DEMO_MODE=false"),
    ("Startup monitoring", settings.startup_scan, "STARTUP_SCAN=true"),
    ("YC Directory URL", bool(settings.yc_directory_url), "YC_DIRECTORY_URL"),
    ("Speedrun URL", bool(settings.speedrun_url), "SPEEDRUN_URL"),
    ("X API", bool(settings.x_bearer_token), "X_BEARER_TOKEN"),
    ("LinkedIn discovery", bool(settings.serper_api_key), "SERPER_API_KEY"),
    ("Slack bot token", bool(settings.slack_bot_token), "SLACK_BOT_TOKEN"),
    ("Slack destination", bool(settings.slack_channel_id), "SLACK_CHANNEL_ID"),
    ("Pond access key", bool(settings.pond_access_key), "POND_ACCESS_KEY"),
    (
        "Public deployment URL",
        settings.public_base_url.startswith("https://")
        and "your-deployment" not in settings.public_base_url,
        "PUBLIC_BASE_URL=https://...",
    ),
]

failed = 0
print("FSignal production preflight\n")
for label, ok, hint in checks:
    print(f"{'PASS' if ok else 'FAIL':4}  {label:<24} {hint}")
    failed += int(not ok)

if failed:
    print(f"\n{failed} check(s) still need configuration.")
    raise SystemExit(1)

print("\nConfiguration is ready for live source/Slack/Pond smoke tests.")
