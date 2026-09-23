# Security Policy

## Supported versions

Security reports should be tested against the current `main` branch when
possible. Older releases and tags may not receive security backports.

## Reporting a vulnerability

Do not open a public issue for a suspected vulnerability.

Use GitHub's private vulnerability reporting feature from the repository's
**Security** tab and select **Report a vulnerability**.

Please include, where applicable:

- the affected version or commit
- operating system and Python version
- the affected backend or routing path
- reproduction steps or a minimal proof of concept
- the security impact
- any relevant configuration
- any known mitigations or prerequisites

Do not include real provider credentials, API keys, tokens, or unrelated
private data in a report.

## Security boundaries

The broker is designed for a trusted local deployment and normally listens on
loopback. It does not provide an inbound authentication boundary for untrusted
remote clients.

Deployments that expose the broker to other hosts should place it behind an
appropriate authenticated and encrypted transport.

Security reports are particularly useful for issues such as:

- unintended exposure beyond the configured local network boundary
- credential or provider-secret disclosure
- injection that crosses an intended process or provider boundary
- unauthorized routing or control of another backend or conversation
- backend provenance or scoped-identifier failures that create a security
  boundary violation

The absence of inbound authentication on the intended trusted-loopback
deployment is part of the documented design and is not itself a vulnerability.

## Disclosure

There is currently no fixed response or remediation SLA. Public disclosure of
an unresolved vulnerability should be coordinated through the private report.
