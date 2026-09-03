"""Send one real Slack alert using credentials from `.env`."""

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.slack import SlackNotifier  # noqa: E402


async def main():
    result = await SlackNotifier().send_ghost(
        {
            "company_name": "Acme AI",
            "program": "yc",
            "source": "x",
            "batch": "S26",
            "confidence": 94,
            "confidence_label": "high",
            "evidence_json": "[\"Explicit founder/company acceptance or joining language\", \"No matching official-directory entry at detection time\"]",
            "gtm_score": 88,
            "gtm_priority": "high",
            "gtm_reasons_json": "[\"Pre-directory timing advantage\", \"Founder/author is directly reachable\"]",
            "author_handle": "example_founder",
            "company_domain": "acme.example",
            "url": "https://x.com/example/status/123",
            "text": "Example integration test: founder announcement detected for YC S26.",
        }
    )
    print(result)


asyncio.run(main())
