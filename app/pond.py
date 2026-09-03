"""Pond Protocol V1 models and helpers.

The implementation follows Pond's published V1 HTTP/JSON contract. Runtime
requests are validated independently from the application's launch-monitoring
logic so the integration can be reviewed and tested in isolation.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .config import settings


@dataclass
class PondProtocolError(Exception):
    status_code: int
    code: str
    message: str
    run_id: str | None = None
    details: dict | None = None


class PondUser(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    locale: str = Field(min_length=1)
    timezone: str = Field(min_length=1)


class PondTextPart(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["text"]
    text: str = Field(min_length=1)


class PondFileDescriptor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = Field(min_length=1, pattern=r"^https://")
    name: str = Field(min_length=1)
    media_type: str = Field(min_length=1)


class PondFilePart(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["file"]
    file: PondFileDescriptor


class PondMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    role: Literal["user"]
    created_at: datetime
    parts: list[PondTextPart | PondFilePart] = Field(min_length=1)

    @field_validator("parts")
    @classmethod
    def require_text_part(cls, value):
        if not any(part.type == "text" for part in value):
            raise ValueError("messages[0].parts must contain at least one text part")
        return value


class PondExecution(BaseModel):
    model_config = ConfigDict(extra="forbid")

    accepted_output_modes: list[str] = Field(min_length=1)
    deadline_ms: int = Field(gt=0)

    @field_validator("accepted_output_modes")
    @classmethod
    def unique_modes(cls, value):
        if any(not isinstance(mode, str) or not mode for mode in value):
            raise ValueError("accepted_output_modes must contain non-empty strings")
        if len(set(value)) != len(value):
            raise ValueError("accepted_output_modes must contain unique values")
        return value


class PondRunRequest(BaseModel):
    """Prepared Pond Protocol V1 request envelope."""

    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(min_length=1)
    agent_id: str = Field(min_length=1)
    conversation_id: str = Field(min_length=1)
    history_truncated: bool
    action_id: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{0,63}$")
    user: PondUser
    messages: list[PondMessage] = Field(min_length=1, max_length=1)
    parameters: dict[str, Any]
    execution: PondExecution

    @model_validator(mode="after")
    def validate_transport_contract(self):
        limits = manifest()["limits"]
        output_modes = set(manifest()["output_modes"])
        if not output_modes.intersection(self.execution.accepted_output_modes):
            raise ValueError("execution.accepted_output_modes does not overlap manifest output_modes")
        if self.execution.deadline_ms > limits["max_run_seconds"] * 1000:
            raise ValueError("execution.deadline_ms exceeds the Agent's declared max_run_seconds")
        if any(part.type == "file" for message in self.messages for part in message.parts):
            raise ValueError("file parts are not accepted because attachments are disabled")
        return self


def manifest() -> dict:
    empty_schema = {"type": "object", "properties": {}, "additionalProperties": False}
    return {
        "protocol": "marketplace-agent",
        "protocol_version": "1.0",
        "agent_version": "2026.09.03-4",
        "metadata": {
            "name": "FSignal",
            "short_description": "Detect founder-announced YC/Speedrun companies before official confirmation.",
            "description": (
                "<p>Persistent launch intelligence for GTM teams. FSignal cross-checks "
                "founder announcements against official accelerator directories, sends Slack "
                "alerts for pre-directory signals, and measures Ghost-to-Confirmed lead time.</p>"
            ),
            "category": "sales",
            "key_features": (
                "<ul><li>YC Directory and Speedrun monitoring</li>"
                "<li>X and LinkedIn founder-signal detection</li>"
                "<li>Persistent deduplication and alert retry</li>"
                "<li>Ghost-to-Confirmed reconciliation with lead-time measurement</li>"
                "<li>Explainable confidence, evidence, and GTM priority scoring</li>"
                "<li>Persistent signal timeline and source health monitoring</li></ul>"
            ),
            "use_cases": (
                "<p>Find newly accepted accelerator founders early enough for outbound, "
                "pipeline discovery, and time-sensitive GTM outreach.</p>"
            ),
            "setup_instructions": (
                "Deploy this server over HTTPS, configure POND_ACCESS_KEY and source credentials, "
                "then use the public Server Base URL when publishing the Agent in Pond."
            ),
        },
        "actions": [
            {
                "id": "scan_now",
                "name": "Scan now",
                "description": "Use when the user wants FSignal to immediately check all configured launch-monitor sources for new signals.",
                "input_schema": empty_schema,
            },
            {
                "id": "get_status",
                "name": "Get status",
                "description": "Use when the user wants current source health, monitoring status, signal counts, or alert-delivery state.",
                "input_schema": empty_schema,
            },
            {
                "id": "list_ghosts",
                "name": "List ghosts",
                "description": "Use when the user wants founder-announced YC or Speedrun signals that are not yet matched to an official directory entry.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "limit": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 50,
                            "description": "Maximum number of current ghost signals to return.",
                        }
                    },
                    "additionalProperties": False,
                },
            },
            {
                "id": "get_timeline",
                "name": "Get signal timeline",
                "description": "Use when the user wants the auditable detection, alert, and confirmation history for a specific signal.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "signal_id": {
                            "type": "integer",
                            "minimum": 1,
                            "description": "Persistent social signal ID returned by list_ghosts or the API.",
                        }
                    },
                    "required": ["signal_id"],
                    "additionalProperties": False,
                },
            },
        ],
        "capabilities": {
            "sync": True,
            "streaming": False,
            "async_tasks": False,
            "cancellation": False,
            "attachments": False,
            "feedback": False,
        },
        "input_modes": ["text/plain"],
        "output_modes": ["text/markdown"],
        "limits": {
            "max_request_bytes": 1_048_576,
            "max_attachment_bytes": 0,
            "max_run_seconds": 300,
        },
    }


def authenticate(authorization: str | None, protocol_version: str | None) -> None:
    if not settings.pond_access_key:
        raise PondProtocolError(
            503,
            "temporarily_unavailable",
            "Pond runtime authentication is not configured on this deployment.",
        )
    if authorization != f"Bearer {settings.pond_access_key}":
        raise PondProtocolError(401, "unauthorized", "The Access Key is missing or invalid.")
    if protocol_version is None or re.fullmatch(r"\d+\.\d+", protocol_version) is None:
        raise PondProtocolError(400, "invalid_request", "The protocol version must be Major.Minor.")
    if protocol_version != "1.0":
        raise PondProtocolError(
            400,
            "unsupported_protocol_version",
            f"Protocol version {protocol_version} is not supported.",
        )


def validate_parameters(action_id: str | None, parameters) -> dict:
    if action_id not in {"scan_now", "get_status", "list_ghosts", "get_timeline"}:
        raise PondProtocolError(
            400,
            "unsupported_operation",
            "The requested action is missing or unsupported.",
        )
    if not isinstance(parameters, dict):
        raise PondProtocolError(400, "invalid_request", "parameters must be a JSON object.")

    if action_id in {"scan_now", "get_status"}:
        if parameters:
            raise PondProtocolError(422, "invalid_input", f"{action_id} does not accept parameters.")
        return {}

    if action_id == "list_ghosts":
        if set(parameters) - {"limit"}:
            raise PondProtocolError(422, "invalid_input", "list_ghosts only accepts the optional limit parameter.")
        limit = parameters.get("limit", 10)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 50:
            raise PondProtocolError(
                422,
                "invalid_input",
                "limit must be an integer between 1 and 50.",
                details={"field": "parameters.limit"},
            )
        return {"limit": limit}

    if set(parameters) != {"signal_id"}:
        raise PondProtocolError(422, "invalid_input", "get_timeline requires signal_id.")
    signal_id = parameters.get("signal_id")
    if isinstance(signal_id, bool) or not isinstance(signal_id, int) or signal_id < 1:
        raise PondProtocolError(
            422,
            "invalid_input",
            "signal_id must be a positive integer.",
            details={"field": "parameters.signal_id"},
        )
    return {"signal_id": signal_id}


def request_hash(body: dict) -> str:
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def terminal(run_id: str, text: str) -> dict:
    return {
        "run_id": run_id,
        "status": "completed",
        "output": [{"type": "text", "text": text}],
        "usage": {"unit_of_measurement": "result", "quantity": 1},
    }


def failed_terminal(run_id: str, message: str) -> dict:
    """Return a safe failure after Pond has accepted execution."""
    return {
        "run_id": run_id,
        "status": "failed",
        "error": {"code": "internal_error", "message": message},
        "usage": {"unit_of_measurement": "result", "quantity": 0},
    }


def error_payload(error: PondProtocolError, run_id: str | None = None) -> dict:
    payload = {"error": {"code": error.code, "message": error.message}}
    effective_run_id = run_id or error.run_id
    if effective_run_id:
        payload["run_id"] = effective_run_id
    if error.details:
        payload["error"]["details"] = error.details
    return payload
