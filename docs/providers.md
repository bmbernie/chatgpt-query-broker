# Providers

The broker currently supports Codex and Web provider paths.

| Capability | Codex | Web |
| --- | --- | --- |
| Persistent conversations | Yes | Yes |
| Streaming provider events | Yes | No |
| Turn interruption | Yes | No |
| Interactive requests | Yes | No |
| Tool policy control | Yes | Limited |
| Sandbox selection | Yes | Limited |
| Attachments | Yes | No |

## Codex

The Codex provider communicates with Codex App Server.

It supports persistent conversations, streaming events, interruption, interactive requests, tool policy selection, sandbox selection, and attachments.

Images are passed as native image input.

Generic files are exposed to Codex through their local filesystem paths so available tools can inspect them.

The broker retains native audio input support for Codex models that support audio.

## Web

The Web provider communicates with `chatgpt-web-provider`.

It supports persistent provider sessions but currently does not expose the full Codex capability set.

The Web provider currently rejects:

- attachments
- enforceable `tools=disabled`
- writable sandbox selections
- in-flight interruption
- interactive broker requests

`read-only` is accepted as the compatibility sandbox setting for Web queries.
