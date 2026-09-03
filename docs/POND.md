# Pond Protocol V1 integration

FSignal exposes Pond Protocol V1 on the same persistent service that runs the monitor.

Official documentation used for this implementation:

- Documentation index: `https://docs.joinpond.ai/llms.txt`
- Pond Protocol Quick Start: `https://docs.joinpond.ai/docs/build-and-publish-an-agent-on-pond`
- Full publishing guide: `https://docs.joinpond.ai/docs/build-and-publish-an-agent-on-pond-full`

## Endpoints

### `GET /manifest`

Public. No bearer token or protocol-version header is required.

The manifest declares:

- protocol `marketplace-agent`
- protocol version `1.0`
- synchronous JSON results
- no streaming, async tasks, attachments, cancellation, or feedback
- `text/plain` inputs and `text/markdown` outputs
- four actions: `scan_now`, `get_status`, `list_ghosts`, and `get_timeline`

### `POST /runs`

Requires:

```http
Authorization: Bearer <POND_ACCESS_KEY>
X-Agent-Protocol-Version: 1.0
Idempotency-Key: <same value as body run_id>
Content-Type: application/json
```

The server validates the complete Pond V1 prepared-run envelope, including:

- required run, agent, conversation, user, message, parameter, and execution fields;
- exactly one synthesized user-role message;
- at least one text part;
- no file parts because this Agent advertises `attachments: false`;
- exact declared `action_id`;
- action parameter schema;
- accepted output modes overlapping `text/markdown`;
- execution deadline not exceeding the manifest limit;
- request body size not exceeding the manifest limit.

## Idempotency

Completed results are persisted in SQLite by `run_id` and request hash. An identical duplicate receives the saved terminal result. Reusing a `run_id` with a materially different body returns HTTP 409 `idempotency_conflict`.

The production image runs one Uvicorn worker. Concurrent duplicate requests in that process are coalesced with a per-run lock before execution.

## Failure behavior

Requests rejected before execution use Pond's stable HTTP error codes. Once execution has been accepted, unexpected application failures return a terminal `status: failed` result with cumulative usage quantity `0`; infrastructure details and stack traces are not exposed.

## Publish checklist

1. Deploy the container at a stable public HTTPS base URL.
2. Set `POND_ACCESS_KEY` on the deployment.
3. Enter the same Access Key when publishing the Agent in Pond.
4. Give Pond the base URL, not `/manifest` or `/runs`.
5. Confirm anonymous `GET /manifest` succeeds.
6. Trigger representative actions through Pond, including `get_timeline`, and verify action routing and parameters.
7. Capture Pond's connected/healthy evidence for the task submission.
