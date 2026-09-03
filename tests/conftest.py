"""Test bootstrap.

Credentials are neutralised *before* `app.config` is imported. Two tests once
passed only because the developer's `.env` happened to supply a Serper key, and
failed on any clean checkout -- which is every CI run and every reviewer who
clones the repo. `load_dotenv(..., override=False)` means an env var set here
wins over the file, so the suite sees the same empty configuration a fresh clone
does. A test that needs a credential must inject it explicitly.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

for _credential in (
    "X_BEARER_TOKEN",
    "SERPER_API_KEY",
    "SLACK_BOT_TOKEN",
    "SLACK_CHANNEL_ID",
    "POND_ACCESS_KEY",
):
    os.environ[_credential] = ""

# Never touch a real database or the live Slack workspace from a test run.
os.environ["DATABASE_PATH"] = str(Path(__file__).parent / ".pytest-scratch" / "test.db")
os.environ["DEMO_MODE"] = "false"
os.environ["STARTUP_SCAN"] = "false"
