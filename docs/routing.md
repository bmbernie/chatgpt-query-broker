# Provider Routing and Identifiers

The broker owns provider selection and provider identity routing.

## Provider selection

For a new conversation, the provider is selected from:

1. the request's `backend` field when supplied;
2. otherwise, the configured default provider.

Although the public wire field remains named `backend`, it represents provider selection at the architectural level.

## Persistent conversations

Persistent conversation IDs are broker-scoped.

The broker records which provider owns each routed conversation identity so subsequent operations return to the same provider.

A request cannot resume a conversation through a different provider.

## Routed operations

Provider ownership is preserved for:

- conversation IDs
- execution or turn IDs
- interaction IDs
- interruption requests
- interaction responses

This allows q/qd to work with one broker-facing identity scheme without understanding provider-native identifiers.
