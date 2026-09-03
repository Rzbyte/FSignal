"""Capture everything about a live deployment that a machine can capture.

`docs/EVIDENCE.md` lists ten artifacts. Six of them are screenshots or a
recording, which need a person at a screen. The other four -- served state, the
Pond contract, and the independent directory check that makes the EARLY claim
verifiable -- are just HTTP, and a person copying them by hand introduces the
one thing this repository refuses to allow: a claim nobody can re-derive.

So this script fetches them, stamps each with the moment it was taken, and
writes them into `evidence/raw/`. It reads the deployment; it never writes to it
beyond the Pond runs it is explicitly proving, and it invents nothing.

    python scripts/capture_evidence.py
    python scripts/capture_evidence.py --base-url http://localhost:8000

Pond capture is skipped with a printed reason when POND_ACCESS_KEY is unset.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import settings  # noqa: E402
from app.sources.official import YCDirectorySource  # noqa: E402

OUT = ROOT / "evidence" / "raw"


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write(name: str, payload: dict) -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / name
    path.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n")
    print(f"  wrote {path.relative_to(ROOT)}")
    return path


def envelope(captured_from: str, note: str, body) -> dict:
    """Every artifact says where it came from and when, so it can be re-taken."""
    return {
        "captured_at": now(),
        "captured_from": captured_from,
        "note": note,
        "body": body,
    }


async def get_json(client: httpx.AsyncClient, base: str, path: str):
    response = await client.get(f"{base}{path}", headers={"Accept": "application/json"})
    response.raise_for_status()
    return response.json()


async def capture_served_state(client, base) -> dict:
    print("Serving state")
    health = await get_json(client, base, "/health")
    write(
        "health.json",
        envelope(
            f"{base}/health",
            "Source health and mode, the snapshot each verdict was checked "
            "against, ledger counts, and alert-delivery state.",
            health,
        ),
    )

    ledger = await get_json(client, base, "/ledger")
    write(
        "ledger.json",
        envelope(
            f"{base}/ledger",
            "Every candidate the bot evaluated and what it decided, including "
            "each suppression's reason code. This is the precision claim.",
            ledger,
        ),
    )

    write(
        "manifest.json",
        envelope(
            f"{base}/manifest",
            "Anonymous GET. Pond Protocol V1 declaration -- no bearer token or "
            "protocol-version header required.",
            await get_json(client, base, "/manifest"),
        ),
    )
    return health


async def capture_timelines(client, base) -> list[dict]:
    """One timeline per alerted signal: detection, classification, delivery."""
    print("Signal timelines")
    ledger = await get_json(client, base, "/ledger")
    alerted = [
        candidate
        for candidate in ledger.get("candidates", [])
        if candidate.get("verdict") == "alerted"
    ]
    if not alerted:
        print("  none yet -- no signal on this deployment has been alerted on")
        return []

    captured = []
    for candidate in alerted:
        signal_id = candidate.get("signal_id")
        if signal_id is None:
            continue
        payload = await get_json(client, base, f"/signals/{signal_id}/timeline")
        name = f"timeline-{signal_id}.json"
        write(
            name,
            envelope(
                f"{base}/signals/{signal_id}/timeline",
                f"Full history of the alert for "
                f"{candidate.get('company_name') or 'an unnamed company'}.",
                payload,
            ),
        )
        captured.append(payload)
    return captured


def pond_run(action_id: str, run_id: str) -> dict:
    """A complete prepared-run envelope, exactly as Pond sends one."""
    return {
        "run_id": run_id,
        "agent_id": "fsignal",
        "conversation_id": f"evidence-{run_id}",
        "history_truncated": False,
        "action_id": action_id,
        "user": {"id": "evidence-capture", "locale": "en-US", "timezone": "UTC"},
        "messages": [
            {
                "id": f"msg-{run_id}",
                "role": "user",
                "created_at": now(),
                "parts": [{"type": "text", "text": f"Run {action_id}."}],
            }
        ],
        "parameters": {},
        "execution": {"accepted_output_modes": ["text/markdown"], "deadline_ms": 60000},
    }


async def capture_pond(client, base) -> None:
    """Prove the contract that matters: the same run_id returns the same result.

    Pond retries. An agent that re-executes on a retry would double-count, so
    idempotency is not a nicety here -- it is the reason the run store exists.
    """
    print("Pond contract")
    if not settings.pond_access_key:
        print("  skipped -- POND_ACCESS_KEY is not set")
        return

    run_id = f"evidence-{uuid.uuid4()}"
    body = pond_run("get_status", run_id)
    headers = {
        "Authorization": f"Bearer {settings.pond_access_key}",
        "X-Agent-Protocol-Version": "1.0",
        "Idempotency-Key": run_id,
        "Content-Type": "application/json",
    }

    first = await client.post(f"{base}/runs", json=body, headers=headers)
    # Byte-identical resend, which is what a Pond retry looks like.
    second = await client.post(f"{base}/runs", json=body, headers=headers)

    write(
        "pond-runs.json",
        envelope(
            f"{base}/runs",
            "One authenticated run and a byte-identical resend under the same "
            "Idempotency-Key. identical_response must be true: a retry returns "
            "the persisted result rather than executing again.",
            {
                "request": body,
                "first": {"status": first.status_code, "body": first.json()},
                "second": {"status": second.status_code, "body": second.json()},
                "identical_response": first.json() == second.json(),
            },
        ),
    )


async def capture_directory_check(health: dict) -> None:
    """The independent half of the EARLY claim.

    An alert asserting "not in the directory" is only evidence if somebody can
    check the directory. This crawls it directly -- not through the deployment --
    and records the full batch roster, so the absence is a fact on the page
    rather than something FSignal says about itself.
    """
    print("Independent directory check")
    ledger_path = OUT / "ledger.json"
    if not ledger_path.exists():
        print("  skipped -- no ledger captured")
        return

    ledger = json.loads(ledger_path.read_text())["body"]
    alerted = [
        candidate
        for candidate in ledger.get("candidates", [])
        if candidate.get("verdict") == "alerted" and candidate.get("company_name")
    ]
    if not alerted:
        print("  skipped -- nothing has been alerted on")
        return

    companies = await YCDirectorySource().collect()
    rosters: dict[str, list[str]] = {}
    findings = []
    for candidate in alerted:
        name = candidate["company_name"]
        batch = candidate.get("batch")
        label = _batch_label(batch) if batch else None

        if label and label not in rosters:
            rosters[label] = sorted(
                company.name
                for company in companies
                if (company.batch or "").strip().lower() == label.lower()
            )
        roster = rosters.get(label, [])

        # The claim is batch-scoped, so the check has to be too. A company of
        # the same name in an older batch does not falsify "not listed in F26" --
        # a real Lark exists under Summer 2025, and batch scoping is exactly what
        # keeps it from suppressing the Fall 2026 signal.
        findings.append(
            {
                "company_name": name,
                "claimed_batch": batch,
                "claimed_batch_label": label,
                "listed_in_claimed_batch": any(
                    entry.lower() == name.lower() for entry in roster
                ),
                "same_name_in_other_batches": [
                    {"name": company.name, "batch": company.batch, "url": company.url}
                    for company in companies
                    if name.lower() in (company.name or "").lower()
                    and (company.batch or "").strip().lower() != (label or "").lower()
                ],
            }
        )

    write(
        "yc-directory-check.json",
        envelope(
            "https://www.ycombinator.com/companies (crawled directly)",
            "Crawled independently of the deployment. For each alerted company: "
            "whether it is listed in the batch it claimed -- which is what the "
            "alert actually asserts -- the full roster of that batch so the "
            "absence is checkable by eye, and separately any same-name company "
            "in a different batch, which does not contradict the claim.",
            {
                "directory_size": len(companies),
                "alerted_companies": findings,
                "batch_rosters": rosters,
            },
        ),
    )


def _batch_label(code: str) -> str | None:
    """``F26`` -> ``Fall 2026``, for reading the roster out of the directory."""
    seasons = {"W": "Winter", "X": "Spring", "P": "Spring", "S": "Summer", "F": "Fall"}
    code = (code or "").strip().upper()
    if len(code) == 3 and code[0] in seasons and code[1:].isdigit():
        return f"{seasons[code[0]]} 20{code[1:]}"
    return None


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-url",
        default=settings.public_base_url.rstrip("/"),
        help="Deployment to capture (default: PUBLIC_BASE_URL)",
    )
    args = parser.parse_args()
    base = args.base_url.rstrip("/")

    if not base or "your-deployment" in base:
        print("Set PUBLIC_BASE_URL or pass --base-url.")
        return 1

    print(f"Capturing evidence from {base}\n")
    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            health = await capture_served_state(client, base)
        except httpx.HTTPError as exc:
            print(f"\nCould not reach {base}: {exc}")
            return 1
        await capture_timelines(client, base)
        await capture_pond(client, base)

    await capture_directory_check(health)

    print("\nCaptured. Still needs a person at a screen, per docs/EVIDENCE.md:")
    for name, shot in (
        ("01-slack-early.png", "the delivered EARLY alert in Slack"),
        ("02-directory-absent.png", "ycombinator.com/companies searched for it, empty"),
        ("03-ledger.png", f"{base}/ledger"),
        ("04-health.png", f"{base}/health"),
        ("06-restart-silence.png", "restart, rescan, no new alert"),
        ("08-pond.png", "Pond showing the agent connected"),
        ("demo-recording-url.txt", "the 2-minute recording"),
    ):
        print(f"  {name:26s} {shot}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
