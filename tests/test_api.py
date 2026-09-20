from fastapi.testclient import TestClient

from chatgpt_worker_broker.app import create_app
from chatgpt_worker_broker.catalog import seed_default_workers
from chatgpt_worker_broker.models import WorkerRole
from chatgpt_worker_broker.provider import (
    ProviderCompletion,
    ProviderSession,
    ProviderSessionConflict,
    ProviderSessionNotFound,
)
from chatgpt_worker_broker.service import BrokerService
from chatgpt_worker_broker.store import BrokerStore


class FakeProvider:
    def __init__(self):
        self.sessions = {}
        self.complete_calls = 0

    async def create_session(
        self,
        session_id,
        model,
        level,
    ):
        if session_id in self.sessions:
            raise ProviderSessionConflict(session_id)

        session = ProviderSession(
            session_id=session_id,
            model=model,
            level=level,
            state="ready",
        )

        self.sessions[session_id] = session
        return session

    async def get_session(self, session_id):
        try:
            return self.sessions[session_id]
        except KeyError:
            raise ProviderSessionNotFound(session_id) from None

    async def list_sessions(self):
        return [
            self.sessions[key]
            for key in sorted(self.sessions)
        ]

    async def delete_session(self, session_id):
        if session_id not in self.sessions:
            raise ProviderSessionNotFound(session_id)

        del self.sessions[session_id]

    async def complete_session(
        self,
        session_id,
        messages,
    ):
        self.complete_calls += 1

        session = self.sessions[session_id]

        return ProviderCompletion(
            response_id=f"response-{self.complete_calls}",
            session_id=session_id,
            model=session.model,
            level=session.level,
            content=f"done:{messages[-1]['content']}",
        )


def make_client(tmp_path):
    store = BrokerStore(tmp_path / "broker.sqlite3")
    seed_default_workers(store)

    provider = FakeProvider()
    service = BrokerService(store, provider)

    app = create_app(
        store=store,
        service=service,
    )

    return TestClient(app), store, provider


def test_health(tmp_path):
    client, _, _ = make_client(tmp_path)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_default_worker_catalog(tmp_path):
    client, _, _ = make_client(tmp_path)

    response = client.get("/v1/workers")

    assert response.status_code == 200

    workers = response.json()["data"]

    assert [
        worker["worker_id"]
        for worker in workers
    ] == [
        "adjudicator-xhigh",
        "assessment-planner-xhigh",
        "crypto-high",
        "forensics-high",
        "pwn-high",
        "re-high",
        "review-xhigh",
        "web-high",
    ]

    planner = next(
        worker
        for worker in workers
        if worker["worker_id"]
        == "assessment-planner-xhigh"
    )

    assert planner["role"] == "planner"
    assert planner["reasoning_level"] == "xhigh"
    assert planner["state"] == "sleeping"


def test_create_and_get_worker(tmp_path):
    client, _, _ = make_client(tmp_path)

    created = client.post(
        "/v1/workers",
        json={
            "worker_id": "custom-high",
            "role": "specialist",
            "model": "chatgpt-5.6-sol-web",
            "reasoning_level": "high",
        },
    )

    assert created.status_code == 201
    assert created.json()["worker_id"] == "custom-high"

    fetched = client.get(
        "/v1/workers/custom-high"
    )

    assert fetched.status_code == 200
    assert fetched.json()["role"] == "specialist"


def test_wake_and_sleep_worker(tmp_path):
    client, store, provider = make_client(tmp_path)

    wake = client.post(
        "/v1/workers/re-high/wake"
    )

    assert wake.status_code == 200
    assert wake.json()["state"] == "ready"
    assert wake.json()["provider_session_id"] == "re-high"

    assert "re-high" in provider.sessions

    sleep = client.post(
        "/v1/workers/re-high/sleep"
    )

    assert sleep.status_code == 200
    assert sleep.json()["state"] == "sleeping"
    assert sleep.json()["provider_session_id"] is None

    assert "re-high" not in provider.sessions


def test_execute_operation_and_idempotent_retry(tmp_path):
    client, _, provider = make_client(tmp_path)

    payload = {
        "operation_id": "op-001",
        "messages": [
            {
                "role": "user",
                "content": "analyze",
            }
        ],
    }

    first = client.post(
        "/v1/workers/re-high/operations",
        json=payload,
    )

    second = client.post(
        "/v1/workers/re-high/operations",
        json=payload,
    )

    assert first.status_code == 200
    assert second.status_code == 200

    assert first.json()["state"] == "completed"
    assert second.json() == first.json()

    assert first.json()["result"]["content"] == "done:analyze"

    assert provider.complete_calls == 1


def test_get_operation(tmp_path):
    client, _, _ = make_client(tmp_path)

    client.post(
        "/v1/workers/re-high/operations",
        json={
            "operation_id": "op-lookup",
            "messages": [
                {
                    "role": "user",
                    "content": "lookup",
                }
            ],
        },
    )

    response = client.get(
        "/v1/operations/op-lookup"
    )

    assert response.status_code == 200
    assert response.json()["operation_id"] == "op-lookup"
    assert response.json()["state"] == "completed"


def test_duplicate_worker_conflict(tmp_path):
    client, _, _ = make_client(tmp_path)

    response = client.post(
        "/v1/workers",
        json={
            "worker_id": "re-high",
            "role": "specialist",
            "model": "chatgpt-5.6-sol-web",
            "reasoning_level": "high",
        },
    )

    assert response.status_code == 409
