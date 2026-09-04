"""Start the web UI against the deterministic demo database."""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["DEMO_MODE"] = "true"
os.environ["DATABASE_PATH"] = "data/demo_ghost_radar.db"
os.environ["STARTUP_SCAN"] = "false"

import uvicorn

uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=False)
