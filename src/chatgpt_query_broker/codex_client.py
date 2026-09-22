from __future__ import annotations

import asyncio
import json
from collections import deque
from contextlib import suppress
from typing import Any

from .codex_models import (
    CodexIncoming,
    CodexNotification,
    CodexProcessExited,
    CodexProtocolError,
    CodexRequestError,
    CodexServerRequest,
    JsonObject,
)


class CodexAppServerClient:
    def __init__(
        self,
        command: tuple[str, ...],
        *,
        request_timeout_seconds: float = 60.0,
    ):
        if not command:
            raise ValueError(
                "Codex command must not be empty"
            )

        if request_timeout_seconds <= 0:
            raise ValueError(
                "Codex request timeout must be greater than zero"
            )

        self._command = command
        self._request_timeout_seconds = (
            request_timeout_seconds
        )

        self._process: asyncio.subprocess.Process | None = (
            None
        )
        self._reader_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None

        self._pending: dict[
            int,
            asyncio.Future[Any],
        ] = {}

        self._incoming: asyncio.Queue[
            CodexIncoming | BaseException
        ] = asyncio.Queue()

        self._thread_incoming: dict[
            str,
            asyncio.Queue[
                CodexIncoming | BaseException
            ],
        ] = {}

        self._write_lock = asyncio.Lock()
        self._next_request_id = 1
        self._stderr_tail: deque[str] = deque(
            maxlen=50
        )

        self._started = False
        self._closing = False

    @property
    def stderr_tail(self) -> tuple[str, ...]:
        return tuple(self._stderr_tail)

    @property
    def running(self) -> bool:
        process = self._process

        return (
            self._started
            and process is not None
            and process.returncode is None
        )

    async def start(self) -> JsonObject:
        if self.running:
            raise RuntimeError(
                "Codex app-server is already running"
            )

        self._closing = False
        self._incoming = asyncio.Queue()
        self._thread_incoming.clear()

        self._process = (
            await asyncio.create_subprocess_exec(
                *self._command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        )

        if (
            self._process.stdin is None
            or self._process.stdout is None
            or self._process.stderr is None
        ):
            await self.aclose()

            raise CodexProcessExited(
                "Codex app-server did not expose stdio pipes"
            )

        self._started = True

        self._reader_task = asyncio.create_task(
            self._reader_loop(),
            name="codex-app-server-stdout",
        )

        self._stderr_task = asyncio.create_task(
            self._stderr_loop(),
            name="codex-app-server-stderr",
        )

        try:
            result = await self.request(
                "initialize",
                {
                    "clientInfo": {
                        "name": "chatgpt-query-broker",
                        "title": "ChatGPT Query Broker",
                        "version": "0.1.0",
                    },
                    "capabilities": {
                        "experimentalApi": False,
                    },
                },
            )

            await self.notify(
                "notifications/initialized"
            )

        except BaseException:
            await self.aclose()
            raise

        if not isinstance(result, dict):
            await self.aclose()

            raise CodexProtocolError(
                "initialize result must be an object"
            )

        return result

    async def aclose(self) -> None:
        if self._closing:
            return

        self._closing = True
        process = self._process

        if process is None:
            self._started = False
            self._closing = False
            return

        if process.stdin is not None:
            process.stdin.close()

            with suppress(Exception):
                await process.stdin.wait_closed()

        if process.returncode is None:
            try:
                await asyncio.wait_for(
                    process.wait(),
                    timeout=2.0,
                )

            except asyncio.TimeoutError:
                process.terminate()

                try:
                    await asyncio.wait_for(
                        process.wait(),
                        timeout=2.0,
                    )

                except asyncio.TimeoutError:
                    process.kill()
                    await process.wait()

        for task in (
            self._reader_task,
            self._stderr_task,
        ):
            if task is not None and not task.done():
                task.cancel()

        for task in (
            self._reader_task,
            self._stderr_task,
        ):
            if task is not None:
                with suppress(
                    asyncio.CancelledError
                ):
                    await task

        self._fail_pending(
            CodexProcessExited(
                "Codex app-server closed"
            )
        )

        self._process = None
        self._reader_task = None
        self._stderr_task = None
        self._started = False
        self._closing = False

    async def request(
        self,
        method: str,
        params: JsonObject | None = None,
        *,
        timeout: float | None = None,
    ) -> Any:
        self._require_running()

        request_id = self._next_request_id
        self._next_request_id += 1

        loop = asyncio.get_running_loop()

        future: asyncio.Future[Any] = (
            loop.create_future()
        )

        self._pending[request_id] = future

        message: JsonObject = {
            "id": request_id,
            "method": method,
        }

        if params is not None:
            message["params"] = params

        try:
            await self._send(message)

            return await asyncio.wait_for(
                future,
                timeout=(
                    self._request_timeout_seconds
                    if timeout is None
                    else timeout
                ),
            )

        except asyncio.TimeoutError:
            self._pending.pop(
                request_id,
                None,
            )

            if not future.done():
                future.cancel()

            raise

        except BaseException:
            self._pending.pop(
                request_id,
                None,
            )

            if not future.done():
                future.cancel()

            raise

    async def notify(
        self,
        method: str,
        params: JsonObject | None = None,
    ) -> None:
        self._require_running()

        message: JsonObject = {
            "method": method,
        }

        if params is not None:
            message["params"] = params

        await self._send(message)

    async def respond(
        self,
        request_id: int | str,
        *,
        result: Any = None,
        error: JsonObject | None = None,
    ) -> None:
        if (
            error is not None
            and result is not None
        ):
            raise ValueError(
                "response cannot contain both result and error"
            )

        message: JsonObject = {
            "id": request_id,
        }

        if error is not None:
            message["error"] = error
        else:
            message["result"] = result

        await self._send(message)

    async def next_message(
        self,
        *,
        timeout: float | None = None,
    ) -> CodexIncoming:
        item = await self._queue_get(
            self._incoming,
            timeout=timeout,
        )

        if isinstance(item, BaseException):
            raise item

        return item

    def subscribe_thread(
        self,
        thread_id: str,
    ) -> None:
        self._require_running()

        if not thread_id:
            raise ValueError(
                "thread_id must not be empty"
            )

        if thread_id in self._thread_incoming:
            raise RuntimeError(
                f"thread {thread_id!r} is already subscribed"
            )

        self._thread_incoming[thread_id] = (
            asyncio.Queue()
        )

    def unsubscribe_thread(
        self,
        thread_id: str,
    ) -> None:
        self._thread_incoming.pop(
            thread_id,
            None,
        )

    async def next_thread_message(
        self,
        thread_id: str,
        *,
        timeout: float | None = None,
    ) -> CodexIncoming:
        try:
            queue = self._thread_incoming[
                thread_id
            ]
        except KeyError:
            raise KeyError(
                f"thread {thread_id!r} is not subscribed"
            ) from None

        item = await self._queue_get(
            queue,
            timeout=timeout,
        )

        if isinstance(item, BaseException):
            raise item

        return item

    @staticmethod
    async def _queue_get(
        queue: asyncio.Queue,
        *,
        timeout: float | None,
    ):
        if timeout is None:
            return await queue.get()

        return await asyncio.wait_for(
            queue.get(),
            timeout=timeout,
        )

    def _require_running(self) -> None:
        if not self.running:
            raise CodexProcessExited(
                "Codex app-server is not running"
            )

    async def _send(
        self,
        message: JsonObject,
    ) -> None:
        self._require_running()

        process = self._process

        assert process is not None
        assert process.stdin is not None

        payload = (
            json.dumps(
                message,
                separators=(",", ":"),
            )
            + "\n"
        ).encode()

        try:
            async with self._write_lock:
                process.stdin.write(payload)
                await process.stdin.drain()

        except (
            BrokenPipeError,
            ConnectionResetError,
        ) as exc:
            raise CodexProcessExited(
                "Codex app-server stdin closed"
            ) from exc

    async def _reader_loop(self) -> None:
        process = self._process

        assert process is not None
        assert process.stdout is not None

        failure: BaseException | None = None

        try:
            while True:
                line = await process.stdout.readline()

                if not line:
                    break

                try:
                    message = json.loads(line)

                except json.JSONDecodeError as exc:
                    failure = CodexProtocolError(
                        "Codex app-server emitted invalid JSON"
                    )
                    failure.__cause__ = exc
                    break

                if not isinstance(message, dict):
                    failure = CodexProtocolError(
                        "Codex app-server message must be an object"
                    )
                    break

                self._dispatch(message)

        except asyncio.CancelledError:
            raise

        except BaseException as exc:
            failure = exc

        finally:
            if (
                failure is None
                and not self._closing
            ):
                failure = CodexProcessExited(
                    "Codex app-server stdout closed"
                )

            if failure is not None:
                self._fail_pending(failure)

    async def _stderr_loop(self) -> None:
        process = self._process

        assert process is not None
        assert process.stderr is not None

        while True:
            line = await process.stderr.readline()

            if not line:
                return

            self._stderr_tail.append(
                line.decode(
                    errors="replace"
                ).rstrip()
            )

    def _dispatch(
        self,
        message: JsonObject,
    ) -> None:
        if (
            "id" in message
            and "method" not in message
        ):
            request_id = message["id"]

            if not isinstance(
                request_id,
                int,
            ):
                return

            future = self._pending.pop(
                request_id,
                None,
            )

            if (
                future is None
                or future.done()
            ):
                return

            if "error" in message:
                error = message["error"]

                if not isinstance(error, dict):
                    future.set_exception(
                        CodexProtocolError(
                            "Codex error response must be an object"
                        )
                    )
                    return

                future.set_exception(
                    CodexRequestError(
                        code=int(
                            error.get(
                                "code",
                                -32603,
                            )
                        ),
                        message=str(
                            error.get(
                                "message",
                                "unknown error",
                            )
                        ),
                        data=error.get("data"),
                    )
                )

                return

            future.set_result(
                message.get("result")
            )

            return

        method = message.get("method")

        if not isinstance(method, str):
            return

        params = message.get("params")

        if params is None:
            params = {}

        if not isinstance(params, dict):
            return

        if "id" in message:
            request_id = message["id"]

            if isinstance(
                request_id,
                (int, str),
            ):
                self._route_incoming(
                    CodexServerRequest(
                        request_id=request_id,
                        method=method,
                        params=params,
                    )
                )

            return

        self._route_incoming(
            CodexNotification(
                method=method,
                params=params,
            )
        )

    def _route_incoming(
        self,
        incoming: CodexIncoming,
    ) -> None:
        thread_id = incoming.params.get(
            "threadId"
        )

        if isinstance(thread_id, str):
            queue = self._thread_incoming.get(
                thread_id
            )

            if queue is not None:
                queue.put_nowait(incoming)
                return

        self._incoming.put_nowait(incoming)

    def _fail_pending(
        self,
        exc: BaseException,
    ) -> None:
        pending = tuple(
            self._pending.values()
        )

        self._pending.clear()

        for future in pending:
            if not future.done():
                future.set_exception(exc)

        self._incoming.put_nowait(exc)

        for queue in self._thread_incoming.values():
            queue.put_nowait(exc)
