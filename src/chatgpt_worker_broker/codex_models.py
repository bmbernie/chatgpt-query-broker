from __future__ import annotations

from dataclasses import dataclass
from typing import Any


JsonObject = dict[str, Any]


@dataclass(frozen=True, slots=True)
class CodexNotification:
    method: str
    params: JsonObject


@dataclass(frozen=True, slots=True)
class CodexServerRequest:
    request_id: int | str
    method: str
    params: JsonObject


CodexIncoming = (
    CodexNotification
    | CodexServerRequest
)


class CodexError(RuntimeError):
    pass


class CodexProtocolError(CodexError):
    pass


class CodexProcessExited(CodexError):
    pass


class CodexRequestError(CodexError):
    def __init__(
        self,
        *,
        code: int,
        message: str,
        data: Any = None,
    ):
        self.code = code
        self.message = message
        self.data = data
        super().__init__(
            f"Codex request failed ({code}): {message}"
        )
