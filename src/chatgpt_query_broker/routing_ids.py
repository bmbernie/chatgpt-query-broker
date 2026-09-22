from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass


_PREFIX = "qb1"


class RoutedIdError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class RoutedId:
    backend: str
    value: str


def encode_routed_id(
    backend: str,
    value: str,
) -> str:
    if not backend or ":" in backend:
        raise RoutedIdError(
            "backend name is invalid"
        )

    if not value:
        raise RoutedIdError(
            "backend id must not be empty"
        )

    payload = (
        base64.urlsafe_b64encode(
            value.encode("utf-8")
        )
        .decode("ascii")
        .rstrip("=")
    )

    return (
        f"{_PREFIX}:{backend}:{payload}"
    )


def decode_routed_id(
    value: str,
) -> RoutedId | None:
    prefix = f"{_PREFIX}:"

    # Existing raw provider IDs are legacy IDs.
    if not value.startswith(prefix):
        return None

    parts = value.split(":", 2)

    if len(parts) != 3:
        raise RoutedIdError(
            "malformed routed id"
        )

    _, backend, payload = parts

    if not backend or ":" in backend:
        raise RoutedIdError(
            "malformed routed backend"
        )

    if not payload:
        raise RoutedIdError(
            "malformed routed payload"
        )

    padding = "=" * (
        (-len(payload)) % 4
    )

    try:
        decoded = base64.b64decode(
            payload + padding,
            altchars=b"-_",
            validate=True,
        ).decode("utf-8")
    except (
        binascii.Error,
        UnicodeDecodeError,
        ValueError,
    ) as exc:
        raise RoutedIdError(
            "malformed routed payload"
        ) from exc

    if not decoded:
        raise RoutedIdError(
            "decoded backend id is empty"
        )

    return RoutedId(
        backend=backend,
        value=decoded,
    )
