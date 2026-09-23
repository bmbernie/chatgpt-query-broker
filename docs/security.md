# Security

`chatgpt-query-broker` is designed as a trusted local service between qd and its configured providers.

The broker does not currently authenticate inbound HTTP requests. Its default bind address is therefore `127.0.0.1`.

Do not bind the broker directly to an untrusted network interface. A remote deployment must place the broker behind an appropriate authenticated and encrypted transport.

The Web provider API key authenticates broker requests to `chatgpt-web-provider`; it does not authenticate clients connecting to the broker.

Requests may invoke provider capabilities including model execution, tools, filesystem access permitted by the selected sandbox, and persistent conversation operations. Treat access to the broker API as privileged.

Do not commit credentials, local state databases, query data, or environment files. The repository ignores common local forms such as `.env`, `*.sqlite`, and `*.sqlite3`, but ignore rules are not a substitute for reviewing committed files and Git history before publication.
