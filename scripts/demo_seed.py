"""Reset deterministic demo state without touching a production database."""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["DEMO_MODE"] = "true"
os.environ["DATABASE_PATH"] = "data/demo_ghost_radar.db"
os.environ["STARTUP_SCAN"] = "false"

from app.config import settings
from app.db import Database

path = Path(settings.database_path)
if path.exists():
    path.unlink()
Path("data/demo_slack.jsonl").unlink(missing_ok=True)
Database(settings.database_path)
print(f"Demo state reset: {settings.database_path}")
