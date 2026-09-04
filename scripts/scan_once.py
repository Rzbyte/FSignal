"""Run all four configured sources once outside the persistent scheduler."""

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import settings
from app.db import Database
from app.scanner import Scanner


async def main():
    scanner = Scanner(Database(settings.database_path))
    print(json.dumps(await scanner.scan_all(), indent=2))


asyncio.run(main())
