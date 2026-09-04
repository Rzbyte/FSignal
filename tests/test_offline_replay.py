"""The replay has to work on a plane.

Its whole value is that somebody who does not trust the numbers can re-derive
them. That fails the moment it needs a key, a network, or a database left in the
right state by a previous run -- so those three are what is asserted here, plus
that it is the production pipeline doing the deciding rather than a second
implementation that happens to agree.
"""

import importlib.util
import json
import socket
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "replay_corpus.py"


def run_replay(extra=()):
    """Run the script the way the README tells a reader to run it."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--json", *extra],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,  # the assertion below reports the stderr, which `check` hides
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.fixture(scope="module")
def report():
    return run_replay()


def test_it_runs_with_no_credentials_at_all(report):
    """conftest blanks every credential, and the subprocess inherits that."""
    assert report["candidates_evaluated"] > 0


def test_it_measures_what_the_readme_claims(report):
    for field in (
        "candidates_evaluated",
        "verdicts",
        "suppression_reasons",
        "signals_persisted",
        "alerts_delivered",
        "early_signals",
    ):
        assert field in report, field

    verdicts = report["verdicts"]
    assert verdicts["alerted"] >= 1, "a replay that alerts on nothing proves nothing"
    assert verdicts["suppressed"] > verdicts["alerted"], (
        "the point of the corpus is that most of it is correctly rejected"
    )
    assert verdicts["already_official"] >= 1, (
        "companies the directory already lists must be recognised as not early"
    )
    assert all(report["suppression_reasons"].values()), (
        "every suppression carries a reason, or precision is unauditable"
    )


def test_the_second_pass_adds_nothing(report):
    """Dedup, demonstrated rather than asserted: same inputs, same pipeline."""
    assert report["second_pass_new_signals"] == 0
    assert report["second_pass_new_alerts"] == 0


def test_two_runs_agree(report):
    """Deterministic, which means it resets its own state.

    A replay that inherited the previous run's database would report how much
    was already there instead of what the pipeline decides about this corpus.
    """
    again = run_replay()
    assert again["candidates_evaluated"] == report["candidates_evaluated"]
    assert again["verdicts"] == report["verdicts"]
    assert again["suppression_reasons"] == report["suppression_reasons"]
    assert again["alerts_delivered"] == report["alerts_delivered"]
    assert (
        [g["company"] for g in again["early_signals"]]
        == [g["company"] for g in report["early_signals"]]
    )


def test_it_reaches_no_network(monkeypatch):
    """Run the replay in-process with the socket layer removed underneath it.

    Anything that tried to open a connection -- Serper, Slack, YC, Speedrun --
    raises instead, so this passes only if the run is genuinely self-contained.
    """
    spec = importlib.util.spec_from_file_location("replay_corpus_undertest", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    real_socket = socket.socket

    def refuse_internet(family=socket.AF_INET, *args, **kwargs):
        # AF_UNIX is asyncio's own self-pipe, not a network call. Blocking it
        # would only prove the event loop needs it.
        if family in (socket.AF_INET, socket.AF_INET6):
            raise AssertionError("the offline replay opened a network connection")
        return real_socket(family, *args, **kwargs)

    def refuse_connect(*args, **kwargs):
        raise AssertionError("the offline replay opened a network connection")

    monkeypatch.setattr(socket, "socket", refuse_internet)
    monkeypatch.setattr(socket, "create_connection", refuse_connect)

    import asyncio

    result = asyncio.run(module.replay())
    assert result["candidates_evaluated"] > 0
    assert result["alerts_delivered"] >= 1


def test_it_uses_the_production_pipeline_not_a_second_one():
    """The claim is that saved inputs go through the shipped code. If the script
    stopped importing the engine, the numbers would stop meaning anything."""
    source = SCRIPT.read_text()
    for imported in ("from app.engine import RadarEngine",
                     "from app.extract import enrich_signal",
                     "from app.db import Database"):
        assert imported in source, imported
