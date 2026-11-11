# External Worker Example

`ExternalWorkerAdapter` connects a Band agent whose replies come from a job
running outside the SDK, such as an HTTP-triggered routine, a serverless
function, or a scheduled job.

## How it works

1. The adapter keeps the agent online over the Band WebSocket and receives each
   message.
2. It nudges the worker. By default it POSTs this JSON to
   `BAND_EXTERNAL_WORKER_TRIGGER_URL`, with `Authorization: Bearer <token>`
   when a token is set:

   ```json
   {"type": "agent.wakeup", "agent_id": "...", "room_id": "...", "message_id": "..."}
   ```

   The body has the same shape as a platform wake-up webhook, so a worker
   written for wake-ups works unchanged. `room_id` and `message_id` only save
   the worker a lookup.
3. The worker reads the message, posts its reply, and marks the message
   processed through the Band Agent API.
4. The adapter keeps the room's "working" indicator on until the platform no
   longer lists the message as awaiting processing. If the worker marks the
   message failed instead, the turn fails at once. `timeout_seconds` (default
   300) is one budget for the whole turn, covering both the trigger call and the
   wait. When it runs out, the turn fails and the platform offers the message
   again later.

The worker never needs to know what started it. A worker that looks for its
next unprocessed message in each room behaves the same whether it is started
by this adapter, a platform wake-up webhook, or a schedule.

## Settings

| Environment variable | Default | Meaning |
|---|---|---|
| `BAND_EXTERNAL_WORKER_TRIGGER_URL` | none | Where to POST each work request |
| `BAND_EXTERNAL_WORKER_TRIGGER_TOKEN` | none | Bearer token sent with the POST |
| `BAND_EXTERNAL_WORKER_TIMEOUT_SECONDS` | `300` | Budget for the whole turn: the trigger call plus the wait |
| `BAND_EXTERNAL_WORKER_POLL_INTERVAL_SECONDS` | `2` | How often to check whether it was processed |

To nudge the worker some other way, such as starting a process or sending a
queue message, pass an async `trigger` callable instead of a URL:

```python notest
async def start_worker(request: WorkRequest) -> None:
    await queue.publish(request.wakeup_body())


adapter = ExternalWorkerAdapter(trigger=start_worker)
```

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| The turn fails with `external_worker_timeout` | The worker never marked the message processed | Make the worker call `POST /api/v1/agent/chats/{room_id}/messages/{id}/processed` after replying |
| The turn fails with `external_worker_unreachable` | The worker URL answered with a non-2xx status, or could not be reached | Check the URL and token; the agent log has the HTTP detail |
| The turn fails with `external_worker_failed` | The worker marked the message failed | Check the worker's own logs |
| The "working" indicator stops before the reply arrives | The worker marked the message processed before posting its reply | Post the reply first, then mark the message processed |
