# Configuration

Configuration is supplied through environment variables.

## Broker

```text
CHATGPT_QUERY_BROKER_HOST
CHATGPT_QUERY_BROKER_PORT
```

The default bind address is `127.0.0.1` and the default port is `8792`.

## Codex provider

```text
CHATGPT_QUERY_BROKER_CODEX_ENABLED
CHATGPT_QUERY_BROKER_CODEX_EXECUTABLE
CHATGPT_QUERY_BROKER_CODEX_REQUEST_TIMEOUT_SECONDS
```

`CHATGPT_QUERY_BROKER_CODEX_EXECUTABLE` defaults to `codex`.

When enabled, the broker starts Codex App Server through the configured Codex executable.

## Web provider

```text
CHATGPT_QUERY_BROKER_WEB_ENABLED
CHATGPT_QUERY_BROKER_WEB_BASE_URL
CHATGPT_QUERY_BROKER_WEB_API_KEY
CHATGPT_QUERY_BROKER_WEB_REQUEST_TIMEOUT_SECONDS
```

The default Web provider base URL is:

```text
http://127.0.0.1:8791
```

A Web API key is required when the Web provider is enabled.

## Multiple providers

Codex and Web may both be enabled.

When Codex is enabled, it is the default provider. If Codex is not enabled and Web is enabled, Web becomes the default. The broker does not automatically fail over between providers.

A query can explicitly select a provider through the public `backend` request field.
