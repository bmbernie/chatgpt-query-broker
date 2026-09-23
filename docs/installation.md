# Installation

## Requirements

- Python 3.13+
- a configured provider
- Codex installed and authenticated for Codex-backed queries
- `chatgpt-web-provider` running and authenticated for Web-backed queries

## Install from the repository

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -e .
```

The installed command is:

```text
chatgpt-query-broker
```

## Start

Configure the required environment and start:

```sh
chatgpt-query-broker
```

By default the broker listens on:

```text
127.0.0.1:8792
```

At least one provider must be enabled for queries to be accepted.
