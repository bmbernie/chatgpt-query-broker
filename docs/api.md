# API

## Health

```http
GET /health
```

A healthy broker returns:

```json
{
  "ok": true
}
```

## Query

```http
POST /v1/query
```

The request accepts:

```json
{
  "input": "query text",
  "cwd": "/workspace",
  "model": "gpt-6-luna",
  "reasoning_effort": "high",
  "attachments": [],
  "thread_id": null,
  "backend": "codex",
  "tools": "enabled",
  "ephemeral": true,
  "sandbox": "read-only"
}
```

`backend` is optional. When omitted, the configured default provider is selected.

The broker streams newline-delimited JSON events back to the caller.

The compatibility API exposes provider-neutral conversation and execution identities as:

```text
thread_id
turn_id
```

## Interrupt a turn

```http
POST /v1/threads/{thread_id}/turns/{turn_id}/interrupt
```

Interruption is routed to the provider that owns the conversation.

Provider capability restrictions still apply.

## Respond to an interaction

```http
POST /v1/interactions/{interaction_id}/respond
```

Example body:

```json
{
  "result": {}
}
```

Interactive requests are currently supported by providers that expose that capability.

## Attachments

Attachment entries use:

```json
{
  "kind": "file",
  "path": "/absolute/path"
}
```

Supported broker attachment kinds are:

```text
image
audio
file
```

Provider support differs. See [providers.md](providers.md).
