import pytest

from chatgpt_query_broker.routing_ids import (
    RoutedId,
    RoutedIdError,
    decode_routed_id,
    encode_routed_id,
)


def test_routed_id_round_trip():
    encoded = encode_routed_id(
        "codex",
        "thread-123",
    )

    assert encoded.startswith(
        "qb1:codex:"
    )

    assert decode_routed_id(
        encoded
    ) == RoutedId(
        backend="codex",
        value="thread-123",
    )


def test_routed_id_preserves_opaque_provider_id():
    raw = (
        "provider/thread:α/"
        "opaque?value=1"
    )

    encoded = encode_routed_id(
        "web",
        raw,
    )

    assert decode_routed_id(
        encoded
    ) == RoutedId(
        backend="web",
        value=raw,
    )


def test_legacy_id_is_not_decoded():
    assert (
        decode_routed_id(
            "thread-existing"
        )
        is None
    )


@pytest.mark.parametrize(
    "value",
    [
        "qb1:",
        "qb1:codex:",
        "qb1::abc",
        "qb1:codex:***",
    ],
)
def test_malformed_routed_id_is_rejected(
    value,
):
    with pytest.raises(
        RoutedIdError
    ):
        decode_routed_id(value)


@pytest.mark.parametrize(
    ("backend", "value"),
    [
        ("", "thread-1"),
        ("bad:name", "thread-1"),
        ("codex", ""),
    ],
)
def test_invalid_routed_id_input_is_rejected(
    backend,
    value,
):
    with pytest.raises(
        RoutedIdError
    ):
        encode_routed_id(
            backend,
            value,
        )
