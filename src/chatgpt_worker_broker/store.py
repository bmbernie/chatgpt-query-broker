from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import (
    Operation,
    OperationState,
    Worker,
    WorkerRole,
    WorkerState,
)


class IdempotencyConflict(RuntimeError):
    pass


class BrokerStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )
        self._initialize()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _canonical_json(value: Any) -> str:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.path,
            timeout=30.0,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                "PRAGMA journal_mode = WAL"
            )

            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS workers (
                    worker_id TEXT PRIMARY KEY,
                    role TEXT NOT NULL,
                    model TEXT NOT NULL,
                    reasoning_level TEXT NOT NULL,
                    state TEXT NOT NULL,
                    provider_session_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS operations (
                    operation_id TEXT PRIMARY KEY,
                    worker_id TEXT NOT NULL,
                    state TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    result_json TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT,
                    FOREIGN KEY(worker_id)
                        REFERENCES workers(worker_id)
                );

                CREATE INDEX IF NOT EXISTS
                    idx_operations_worker
                ON operations(worker_id);

                CREATE INDEX IF NOT EXISTS
                    idx_operations_state
                ON operations(state);
                """
            )

    @staticmethod
    def _worker_from_row(row: sqlite3.Row) -> Worker:
        return Worker(
            worker_id=row["worker_id"],
            role=WorkerRole(row["role"]),
            model=row["model"],
            reasoning_level=row["reasoning_level"],
            state=WorkerState(row["state"]),
            provider_session_id=row["provider_session_id"],
            created_at=datetime.fromisoformat(
                row["created_at"]
            ),
            updated_at=datetime.fromisoformat(
                row["updated_at"]
            ),
        )

    @staticmethod
    def _operation_from_row(
        row: sqlite3.Row,
    ) -> Operation:
        return Operation(
            operation_id=row["operation_id"],
            worker_id=row["worker_id"],
            state=OperationState(row["state"]),
            request=json.loads(row["request_json"]),
            result=(
                json.loads(row["result_json"])
                if row["result_json"] is not None
                else None
            ),
            error=row["error"],
            created_at=datetime.fromisoformat(
                row["created_at"]
            ),
            started_at=(
                datetime.fromisoformat(row["started_at"])
                if row["started_at"] is not None
                else None
            ),
            completed_at=(
                datetime.fromisoformat(
                    row["completed_at"]
                )
                if row["completed_at"] is not None
                else None
            ),
        )

    def create_worker(
        self,
        worker_id: str,
        role: WorkerRole,
        model: str,
        reasoning_level: str,
    ) -> Worker:
        now = self._now()

        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO workers (
                    worker_id,
                    role,
                    model,
                    reasoning_level,
                    state,
                    provider_session_id,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, NULL, ?, ?)
                """,
                (
                    worker_id,
                    role.value,
                    model,
                    reasoning_level,
                    WorkerState.SLEEPING.value,
                    now,
                    now,
                ),
            )

        worker = self.get_worker(worker_id)
        assert worker is not None
        return worker

    def get_worker(
        self,
        worker_id: str,
    ) -> Worker | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM workers
                WHERE worker_id = ?
                """,
                (worker_id,),
            ).fetchone()

        if row is None:
            return None

        return self._worker_from_row(row)

    def list_workers(self) -> list[Worker]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM workers
                ORDER BY worker_id
                """
            ).fetchall()

        return [
            self._worker_from_row(row)
            for row in rows
        ]

    def create_operation(
        self,
        operation_id: str,
        worker_id: str,
        request: dict[str, Any],
    ) -> tuple[Operation, bool]:
        request_json = self._canonical_json(request)
        now = self._now()

        connection = self._connect()

        try:
            connection.execute("BEGIN IMMEDIATE")

            existing = connection.execute(
                """
                SELECT *
                FROM operations
                WHERE operation_id = ?
                """,
                (operation_id,),
            ).fetchone()

            if existing is not None:
                if (
                    existing["worker_id"] != worker_id
                    or existing["request_json"]
                    != request_json
                ):
                    raise IdempotencyConflict(
                        "operation_id already exists with "
                        "different worker or request"
                    )

                operation = self._operation_from_row(
                    existing
                )
                connection.commit()
                return operation, False

            worker = connection.execute(
                """
                SELECT worker_id
                FROM workers
                WHERE worker_id = ?
                """,
                (worker_id,),
            ).fetchone()

            if worker is None:
                raise KeyError(worker_id)

            connection.execute(
                """
                INSERT INTO operations (
                    operation_id,
                    worker_id,
                    state,
                    request_json,
                    result_json,
                    error,
                    created_at,
                    started_at,
                    completed_at
                )
                VALUES (?, ?, ?, ?, NULL, NULL, ?, NULL, NULL)
                """,
                (
                    operation_id,
                    worker_id,
                    OperationState.QUEUED.value,
                    request_json,
                    now,
                ),
            )

            row = connection.execute(
                """
                SELECT *
                FROM operations
                WHERE operation_id = ?
                """,
                (operation_id,),
            ).fetchone()

            connection.commit()

            assert row is not None
            return self._operation_from_row(row), True

        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def get_operation(
        self,
        operation_id: str,
    ) -> Operation | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM operations
                WHERE operation_id = ?
                """,
                (operation_id,),
            ).fetchone()

        if row is None:
            return None

        return self._operation_from_row(row)

    def list_operations(self) -> list[Operation]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM operations
                ORDER BY created_at, operation_id
                """
            ).fetchall()

        return [
            self._operation_from_row(row)
            for row in rows
        ]

    def mark_operation_running(
        self,
        operation_id: str,
    ) -> Operation:
        now = self._now()

        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE operations
                SET
                    state = ?,
                    started_at = ?
                WHERE operation_id = ?
                """,
                (
                    OperationState.RUNNING.value,
                    now,
                    operation_id,
                ),
            )

            if cursor.rowcount != 1:
                raise KeyError(operation_id)

        operation = self.get_operation(operation_id)
        assert operation is not None
        return operation

    def complete_operation(
        self,
        operation_id: str,
        result: dict[str, Any],
    ) -> Operation:
        now = self._now()
        result_json = self._canonical_json(result)

        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE operations
                SET
                    state = ?,
                    result_json = ?,
                    error = NULL,
                    completed_at = ?
                WHERE operation_id = ?
                """,
                (
                    OperationState.COMPLETED.value,
                    result_json,
                    now,
                    operation_id,
                ),
            )

            if cursor.rowcount != 1:
                raise KeyError(operation_id)

        operation = self.get_operation(operation_id)
        assert operation is not None
        return operation

    def transition_worker(
        self,
        worker_id: str,
        state: WorkerState,
        provider_session_id: str | None = None,
    ) -> Worker:
        now = self._now()

        with self._connect() as connection:
            existing = connection.execute(
                """
                SELECT *
                FROM workers
                WHERE worker_id = ?
                """,
                (worker_id,),
            ).fetchone()

            if existing is None:
                raise KeyError(worker_id)

            # None currently means preserve the existing provider binding.
            # Sleep/delete lifecycle will add an explicit clearing path.
            session_id = (
                existing["provider_session_id"]
                if provider_session_id is None
                else provider_session_id
            )

            connection.execute(
                """
                UPDATE workers
                SET
                    state = ?,
                    provider_session_id = ?,
                    updated_at = ?
                WHERE worker_id = ?
                """,
                (
                    state.value,
                    session_id,
                    now,
                    worker_id,
                ),
            )

        worker = self.get_worker(worker_id)
        assert worker is not None
        return worker

    def recover_after_restart(
        self,
    ) -> dict[str, int]:
        """Move crash-sensitive records into explicit recovery states."""
        now = self._now()
        connection = self._connect()

        try:
            connection.execute("BEGIN IMMEDIATE")

            worker_cursor = connection.execute(
                """
                UPDATE workers
                SET
                    state = ?,
                    updated_at = ?
                WHERE state IN (?, ?, ?)
                """,
                (
                    WorkerState.RECOVERING.value,
                    now,
                    WorkerState.STARTING.value,
                    WorkerState.READY.value,
                    WorkerState.BUSY.value,
                ),
            )

            operation_cursor = connection.execute(
                """
                UPDATE operations
                SET state = ?
                WHERE state = ?
                """,
                (
                    OperationState.INDETERMINATE.value,
                    OperationState.RUNNING.value,
                ),
            )

            connection.commit()

            return {
                "workers_recovering": worker_cursor.rowcount,
                "operations_indeterminate": (
                    operation_cursor.rowcount
                ),
            }

        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
