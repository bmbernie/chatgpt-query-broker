# Operations

## Health

Check broker HTTP liveness:

```sh
curl -s http://127.0.0.1:8792/health
```

`/health` confirms that the broker HTTP service is responding. It does not verify provider authentication, provider availability, or model execution.

## Provider startup

Configured providers are initialized when the broker starts.

Codex starts its App Server transport.

The Web provider performs a health check against its configured provider endpoint.

If no provider is configured, the HTTP service can still expose `/health`, but query operations have no provider available.

## Shutdown

Provider resources are closed when the broker shuts down.

## systemd readiness

When launched under a service manager that supplies `NOTIFY_SOCKET`, the broker sends `READY=1` after application startup has completed and the HTTP listener has been bound.
