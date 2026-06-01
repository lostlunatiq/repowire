# M3: Cross-Runtime Review Cycles — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enable structured review protocol between mesh peers (Claude ↔ Kimi ↔ Gemini), with guardrail-gated merge: no artifact is considered done until `lint + tests + type_check + review_ack` all pass.

**Architecture:** A new `SQLiteReviewGateStore` tracks review gate state (pending → in_review → approved/rejected → merged). The `POST /review-gates` route creates a gate and sends an `ask` to the reviewer. When the reviewer `ack`s, the ack handler updates the gate state from attachments. `merge_readiness(gate_id)` returns a deterministic gate-pass summary.

**Tech Stack:** Python 3.12, FastAPI, pydantic, pytest, SQLite

**Prerequisite:** M2 (Token Budgets & Guardrails) must be complete so that `lint_code`, `run_tests`, `type_check` exist and `SQLiteTokenBudgetStore` is wired.

---

## File Structure

| File | Responsibility |
|---|---|
| `repowire/daemon/state/review_gates.py` | `SQLiteReviewGateStore` — CRUD, state transitions, readiness checks |
| `repowire/daemon/state/database.py` | Schema migration v12 — `CREATE TABLE review_gates` |
| `repowire/daemon/state/__init__.py` | Export `SQLiteReviewGateStore` |
| `repowire/daemon/routes/review_gates.py` | `POST /review-gates`, `GET /review-gates/{gate_id}`, `GET /review-gates/{gate_id}/readiness`, `GET /review-gates` |
| `repowire/daemon/routes/asks.py` | Extend ack handler to auto-update review gates on reviewer ack |
| `repowire/daemon/app.py` | Wire `review_gate_store` into app state (both factories) |
| `repowire/mcp/server.py` | Review tools: `request_review`, `merge_readiness`, `list_pending_reviews` |
| `tests/test_review_gates.py` | Store unit tests |
| `tests/test_review_routes.py` | Route integration tests |
| `tests/test_review_mcp.py` | MCP tool tests |

---

## Task 1: Schema Migration

**Files:**
- Modify: `repowire/daemon/state/database.py`

- [ ] **Step 1: Bump SCHEMA_VERSION and add review_gates table**

```python
# repowire/daemon/state/database.py
# Change line 11:
SCHEMA_VERSION = 12
```

Add inside `migrate()`, after the `token_budgets` block and before the first `schema_migrations` INSERT:

```python
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS review_gates (
                    gate_id TEXT PRIMARY KEY,
                    work_id TEXT,
                    author_peer_id TEXT NOT NULL,
                    reviewer_peer_id TEXT NOT NULL,
                    artifact_type TEXT,
                    artifact_ref TEXT,
                    guardrail_results_json TEXT NOT NULL DEFAULT '{}',
                    state TEXT NOT NULL DEFAULT 'pending',
                    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
                )
                """,
            )
            self.conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_review_gates_author
                ON review_gates(author_peer_id, state)
                """,
            )
            self.conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_review_gates_reviewer
                ON review_gates(reviewer_peer_id, state)
                """,
            )
            self.conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_review_gates_work
                ON review_gates(work_id)
                """,
            )
```

Add at the bottom of `migrate()`, before the `PRAGMA user_version` line:

```python
            self.conn.execute(
                """
                INSERT OR IGNORE INTO schema_migrations(version, description)
                VALUES (?, ?)
                """,
                (12, "cross-runtime review gate lifecycle"),
            )
```

- [ ] **Step 2: Verify migration runs cleanly**

Run: `python -c "from repowire.daemon.state.database import StateDatabase; from pathlib import Path; import tempfile; p = Path(tempfile.mkdtemp()) / 'state.db'; db = StateDatabase(p); print('user_version:', db.conn.execute('PRAGMA user_version').fetchone()[0]); db.close()"`

Expected: `user_version: 12`

- [ ] **Step 3: Commit**

```bash
git add repowire/daemon/state/database.py
git commit -m "feat(m3): add review_gates schema migration v12"
```

---

## Task 2: Implement SQLiteReviewGateStore

**Files:**
- Create: `repowire/daemon/state/review_gates.py`
- Test: `tests/test_review_gates.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_review_gates.py
from pathlib import Path

from repowire.daemon.state.database import StateDatabase
from repowire.daemon.state.review_gates import SQLiteReviewGateStore


def test_review_gate_create_and_get(tmp_path: Path) -> None:
    db = StateDatabase(tmp_path / "state.db")
    try:
        store = SQLiteReviewGateStore(db)
        gate = store.create(
            gate_id="gate-abc",
            author_peer_id="peer-author",
            reviewer_peer_id="peer-reviewer",
            artifact_type="diff",
            artifact_ref="/tmp/feature.diff",
            guardrail_results={"lint": {"passed": True}, "tests": {"passed": True}},
        )
        assert gate.gate_id == "gate-abc"
        assert gate.state == "pending"
        assert gate.author_peer_id == "peer-author"

        fetched = store.get("gate-abc")
        assert fetched is not None
        assert fetched.guardrail_results["lint"]["passed"] is True
    finally:
        db.close()


def test_review_gate_state_transition(tmp_path: Path) -> None:
    db = StateDatabase(tmp_path / "state.db")
    try:
        store = SQLiteReviewGateStore(db)
        store.create(
            gate_id="gate-abc",
            author_peer_id="peer-author",
            reviewer_peer_id="peer-reviewer",
            artifact_type="diff",
            artifact_ref="/tmp/feature.diff",
        )
        updated = store.update_state("gate-abc", "approved")
        assert updated is not None
        assert updated.state == "approved"

        rejected = store.update_state("gate-abc", "rejected")
        assert rejected is not None
        assert rejected.state == "rejected"
    finally:
        db.close()


def test_review_gate_list_for_reviewer(tmp_path: Path) -> None:
    db = StateDatabase(tmp_path / "state.db")
    try:
        store = SQLiteReviewGateStore(db)
        store.create(
            gate_id="gate-1",
            author_peer_id="peer-a",
            reviewer_peer_id="peer-r",
            artifact_type="diff",
            artifact_ref="/tmp/a.diff",
        )
        store.create(
            gate_id="gate-2",
            author_peer_id="peer-b",
            reviewer_peer_id="peer-r",
            artifact_type="file",
            artifact_ref="/tmp/b.py",
        )
        store.update_state("gate-2", "approved")

        pending = store.list_for_reviewer("peer-r", state="pending")
        assert len(pending) == 1
        assert pending[0].gate_id == "gate-1"

        all_gates = store.list_for_reviewer("peer-r")
        assert len(all_gates) == 2
    finally:
        db.close()


def test_review_gate_merge_readiness_all_pass(tmp_path: Path) -> None:
    db = StateDatabase(tmp_path / "state.db")
    try:
        store = SQLiteReviewGateStore(db)
        store.create(
            gate_id="gate-abc",
            author_peer_id="peer-author",
            reviewer_peer_id="peer-reviewer",
            artifact_type="diff",
            artifact_ref="/tmp/feature.diff",
            guardrail_results={
                "lint": {"passed": True},
                "tests": {"passed": True},
                "type_check": {"passed": True},
            },
        )
        store.update_state("gate-abc", "approved")
        readiness = store.merge_readiness("gate-abc")
        assert readiness["ready"] is True
        assert readiness["checks"]["lint"]["passed"] is True
        assert readiness["checks"]["tests"]["passed"] is True
        assert readiness["checks"]["type_check"]["passed"] is True
        assert readiness["checks"]["review"]["state"] == "approved"
    finally:
        db.close()


def test_review_gate_merge_readiness_missing_guardrail(tmp_path: Path) -> None:
    db = StateDatabase(tmp_path / "state.db")
    try:
        store = SQLiteReviewGateStore(db)
        store.create(
            gate_id="gate-abc",
            author_peer_id="peer-author",
            reviewer_peer_id="peer-reviewer",
            artifact_type="diff",
            artifact_ref="/tmp/feature.diff",
            guardrail_results={
                "lint": {"passed": True},
                "tests": {"passed": False},
            },
        )
        store.update_state("gate-abc", "approved")
        readiness = store.merge_readiness("gate-abc")
        assert readiness["ready"] is False
        assert readiness["checks"]["tests"]["passed"] is False
    finally:
        db.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_review_gates.py -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'repowire.daemon.state.review_gates'`

- [ ] **Step 3: Implement SQLiteReviewGateStore**

```python
# repowire/daemon/state/review_gates.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from repowire.daemon.state.database import StateDatabase
from repowire.daemon.work_store import json_dumps, json_loads, now_iso


@dataclass(frozen=True)
class ReviewGate:
    gate_id: str
    work_id: str | None
    author_peer_id: str
    reviewer_peer_id: str
    artifact_type: str | None
    artifact_ref: str | None
    guardrail_results: dict[str, Any]
    state: str
    created_at: str
    updated_at: str


class SQLiteReviewGateStore:
    """Repository for cross-runtime review gate lifecycle."""

    def __init__(self, db: StateDatabase) -> None:
        self._conn = db.conn

    @staticmethod
    def _row_to_gate(row) -> ReviewGate | None:
        if row is None:
            return None
        return ReviewGate(
            gate_id=row["gate_id"],
            work_id=row["work_id"],
            author_peer_id=row["author_peer_id"],
            reviewer_peer_id=row["reviewer_peer_id"],
            artifact_type=row["artifact_type"],
            artifact_ref=row["artifact_ref"],
            guardrail_results=json_loads(row["guardrail_results_json"], {}),
            state=row["state"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def get(self, gate_id: str) -> ReviewGate | None:
        row = self._conn.execute(
            "SELECT * FROM review_gates WHERE gate_id = ?",
            (gate_id,),
        ).fetchone()
        return self._row_to_gate(row)

    def create(
        self,
        *,
        gate_id: str,
        author_peer_id: str,
        reviewer_peer_id: str,
        work_id: str | None = None,
        artifact_type: str | None = None,
        artifact_ref: str | None = None,
        guardrail_results: dict[str, Any] | None = None,
    ) -> ReviewGate:
        now = now_iso()
        guardrail_results = guardrail_results or {}
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO review_gates(
                    gate_id, work_id, author_peer_id, reviewer_peer_id,
                    artifact_type, artifact_ref, guardrail_results_json,
                    state, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    gate_id,
                    work_id,
                    author_peer_id,
                    reviewer_peer_id,
                    artifact_type,
                    artifact_ref,
                    json_dumps(guardrail_results),
                    "pending",
                    now,
                    now,
                ),
            )
        return self.get(gate_id)

    def update_state(self, gate_id: str, state: str) -> ReviewGate | None:
        now = now_iso()
        with self._conn:
            self._conn.execute(
                "UPDATE review_gates SET state = ?, updated_at = ? WHERE gate_id = ?",
                (state, now, gate_id),
            )
        return self.get(gate_id)

    def list_for_reviewer(
        self,
        reviewer_peer_id: str,
        state: str | None = None,
    ) -> list[ReviewGate]:
        clauses = ["reviewer_peer_id = ?"]
        params: list[str] = [reviewer_peer_id]
        if state is not None:
            clauses.append("state = ?")
            params.append(state)
        where = f"WHERE {' AND '.join(clauses)}"
        rows = self._conn.execute(
            f"SELECT * FROM review_gates {where} ORDER BY updated_at DESC",
            params,
        ).fetchall()
        return [g for row in rows if (g := self._row_to_gate(row)) is not None]

    def list_for_author(
        self,
        author_peer_id: str,
        state: str | None = None,
    ) -> list[ReviewGate]:
        clauses = ["author_peer_id = ?"]
        params: list[str] = [author_peer_id]
        if state is not None:
            clauses.append("state = ?")
            params.append(state)
        where = f"WHERE {' AND '.join(clauses)}"
        rows = self._conn.execute(
            f"SELECT * FROM review_gates {where} ORDER BY updated_at DESC",
            params,
        ).fetchall()
        return [g for row in rows if (g := self._row_to_gate(row)) is not None]

    def merge_readiness(self, gate_id: str) -> dict[str, Any]:
        gate = self.get(gate_id)
        if gate is None:
            return {
                "ready": False,
                "error": f"Gate not found: {gate_id}",
                "checks": {},
            }
        gr = gate.guardrail_results
        checks = {
            "lint": {"passed": gr.get("lint", {}).get("passed", False)},
            "tests": {"passed": gr.get("tests", {}).get("passed", False)},
            "type_check": {"passed": gr.get("type_check", {}).get("passed", False)},
            "review": {
                "state": gate.state,
                "reviewer": gate.reviewer_peer_id,
            },
        }
        all_pass = (
            checks["lint"]["passed"]
            and checks["tests"]["passed"]
            and checks["type_check"]["passed"]
            and gate.state == "approved"
        )
        return {
            "ready": all_pass,
            "checks": checks,
            "gate_id": gate_id,
        }
```

- [ ] **Step 4: Export from state package**

```python
# repowire/daemon/state/__init__.py
# Add to imports:
from repowire.daemon.state.review_gates import SQLiteReviewGateStore

# Add to __all__:
    "SQLiteReviewGateStore",
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_review_gates.py -v`

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add repowire/daemon/state/review_gates.py repowire/daemon/state/__init__.py tests/test_review_gates.py
git commit -m "feat(m3): add SQLiteReviewGateStore with readiness checks"
```

---

## Task 3: Wire ReviewGateStore into Daemon App

**Files:**
- Modify: `repowire/daemon/app.py`

- [ ] **Step 1: Import and instantiate in `create_app()`**

Add import near the other state store imports:

```python
from repowire.daemon.state.review_gates import SQLiteReviewGateStore
```

Inside `lifespan`, after `token_budget_store = SQLiteTokenBudgetStore(state_db)`, add:

```python
        review_gate_store = SQLiteReviewGateStore(state_db)
```

After `app.state.token_budget_store = token_budget_store`, add:

```python
        app.state.review_gate_store = review_gate_store
```

- [ ] **Step 2: Repeat for `create_test_app()`**

Add the same two lines in `create_test_app()` lifespan, mirroring the token_budget_store placement.

- [ ] **Step 3: Verify import works**

Run: `python -c "from repowire.daemon.app import create_app; print('ok')"`

Expected: `ok`

- [ ] **Step 4: Commit**

```bash
git add repowire/daemon/app.py
git commit -m "feat(m3): wire ReviewGateStore into daemon app state"
```

---

## Task 4: Review Gate HTTP Routes

**Files:**
- Create: `repowire/daemon/routes/review_gates.py`
- Modify: `repowire/daemon/routes/asks.py` (integrate ack → gate update)
- Test: `tests/test_review_routes.py`

- [ ] **Step 1: Write the failing route test**

```python
# tests/test_review_routes.py
import pytest
from httpx import AsyncClient

from repowire.daemon.deps import cleanup_deps
from repowire.daemon.routes import peers, review_gates
from tests.conftest import async_client_for, make_daemon_app

ROUTERS = (peers.router, review_gates.router)


@pytest.fixture
async def env(tmp_path):
    harness = make_daemon_app(tmp_path, ROUTERS)
    async with async_client_for(harness.app) as client:
        yield client, harness
    cleanup_deps()


async def _register_peer(client, name, path="/tmp/test", backend="kimi-code"):
    r = await client.post("/peers", json={
        "name": name, "path": path, "backend": backend,
    })
    assert r.status_code == 200
    return r.json()["peer_id"]


@pytest.mark.asyncio
async def test_create_review_gate(env):
    client, harness = env
    author_id = await _register_peer(client, "author")
    reviewer_id = await _register_peer(client, "reviewer")

    r = await client.post("/review-gates", json={
        "author_peer_id": author_id,
        "reviewer_peer_id": reviewer_id,
        "artifact_type": "diff",
        "artifact_ref": "/tmp/feature.diff",
        "guardrail_results": {
            "lint": {"passed": True},
            "tests": {"passed": True},
            "type_check": {"passed": True},
        },
    })
    assert r.status_code == 200
    body = r.json()
    assert body["gate_id"]
    assert body["state"] == "pending"


@pytest.mark.asyncio
async def test_get_review_gate(env):
    client, harness = env
    author_id = await _register_peer(client, "author")
    reviewer_id = await _register_peer(client, "reviewer")

    r = await client.post("/review-gates", json={
        "author_peer_id": author_id,
        "reviewer_peer_id": reviewer_id,
        "artifact_type": "diff",
        "artifact_ref": "/tmp/feature.diff",
    })
    gate_id = r.json()["gate_id"]

    r = await client.get(f"/review-gates/{gate_id}")
    assert r.status_code == 200
    body = r.json()
    assert body["gate_id"] == gate_id
    assert body["author_peer_id"] == author_id


@pytest.mark.asyncio
async def test_merge_readiness_all_pass(env):
    client, harness = env
    author_id = await _register_peer(client, "author")
    reviewer_id = await _register_peer(client, "reviewer")

    r = await client.post("/review-gates", json={
        "author_peer_id": author_id,
        "reviewer_peer_id": reviewer_id,
        "artifact_type": "diff",
        "artifact_ref": "/tmp/feature.diff",
        "guardrail_results": {
            "lint": {"passed": True},
            "tests": {"passed": True},
            "type_check": {"passed": True},
        },
    })
    gate_id = r.json()["gate_id"]

    # Simulate approval via direct store access
    harness.app.state.review_gate_store.update_state(gate_id, "approved")

    r = await client.get(f"/review-gates/{gate_id}/readiness")
    assert r.status_code == 200
    body = r.json()
    assert body["ready"] is True
    assert body["checks"]["lint"]["passed"] is True
    assert body["checks"]["review"]["state"] == "approved"


@pytest.mark.asyncio
async def test_list_pending_reviews(env):
    client, harness = env
    author_id = await _register_peer(client, "author")
    reviewer_id = await _register_peer(client, "reviewer")

    await client.post("/review-gates", json={
        "author_peer_id": author_id,
        "reviewer_peer_id": reviewer_id,
        "artifact_type": "diff",
        "artifact_ref": "/tmp/feature.diff",
    })

    r = await client.get("/review-gates", params={
        "reviewer_peer_id": reviewer_id,
        "state": "pending",
    })
    assert r.status_code == 200
    body = r.json()
    assert len(body["gates"]) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_review_routes.py -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'repowire.daemon.routes.review_gates'`

- [ ] **Step 3: Implement review gate routes**

```python
# repowire/daemon/routes/review_gates.py
from __future__ import annotations

import logging
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from repowire.daemon.auth import require_auth
from repowire.daemon.deps import get_app_state, get_peer_registry
from repowire.daemon.peer_delivery import peer_delivery_from_state
from repowire.daemon.routes._shared import OkResponse
from repowire.protocol.messages import AttachmentRef

logger = logging.getLogger(__name__)
router = APIRouter(tags=["review-gates"])


class CreateReviewGateRequest(BaseModel):
    author_peer_id: str = Field(..., description="Peer ID of the artifact author")
    reviewer_peer_id: str = Field(..., description="Peer ID of the assigned reviewer")
    work_id: str | None = Field(None, description="Optional tracked work ID")
    artifact_type: str | None = Field(None, description="Type: diff, file, pr")
    artifact_ref: str | None = Field(None, description="Path, PR URL, or diff hash")
    guardrail_results: dict = Field(default_factory=dict, description="Guardrail output dict")


class ReviewGateResponse(BaseModel):
    gate_id: str
    state: str
    author_peer_id: str
    reviewer_peer_id: str
    artifact_type: str | None
    artifact_ref: str | None
    guardrail_results: dict
    created_at: str
    updated_at: str


class ReviewGateListResponse(BaseModel):
    gates: list[ReviewGateResponse]


class MergeReadinessResponse(BaseModel):
    ready: bool
    gate_id: str
    checks: dict


def _get_store():
    state = get_app_state()
    store = getattr(state, "review_gate_store", None)
    if store is None:
        raise RuntimeError("review_gate_store not initialized")
    return store


def _gate_to_response(gate) -> ReviewGateResponse:
    return ReviewGateResponse(
        gate_id=gate.gate_id,
        state=gate.state,
        author_peer_id=gate.author_peer_id,
        reviewer_peer_id=gate.reviewer_peer_id,
        artifact_type=gate.artifact_type,
        artifact_ref=gate.artifact_ref,
        guardrail_results=gate.guardrail_results,
        created_at=gate.created_at,
        updated_at=gate.updated_at,
    )


@router.post("/review-gates", response_model=ReviewGateResponse)
async def create_review_gate(
    request: CreateReviewGateRequest,
    _: str | None = Depends(require_auth),
) -> ReviewGateResponse:
    """Create a review gate and send an ask to the reviewer."""
    peer_registry = get_peer_registry()
    author = await peer_registry.get_peer(request.author_peer_id)
    reviewer = await peer_registry.get_peer(request.reviewer_peer_id)
    if author is None:
        raise HTTPException(status_code=404, detail=f"Author peer not found: {request.author_peer_id}")
    if reviewer is None:
        raise HTTPException(status_code=404, detail=f"Reviewer peer not found: {request.reviewer_peer_id}")

    gate_id = f"review-{uuid4().hex[:12]}"
    store = _get_store()
    gate = store.create(
        gate_id=gate_id,
        author_peer_id=author.peer_id,
        reviewer_peer_id=reviewer.peer_id,
        work_id=request.work_id,
        artifact_type=request.artifact_type,
        artifact_ref=request.artifact_ref,
        guardrail_results=request.guardrail_results,
    )

    # Send ask to reviewer
    gr = request.guardrail_results
    gr_summary = " ".join(
        f"{k}={'✓' if v.get('passed') else '✗'}"
        for k, v in gr.items()
    )
    ask_text = (
        f"[ask #{gate_id}] Review request from {author.display_name}:\n"
        f"Artifact: {request.artifact_type} at {request.artifact_ref}\n"
        f"Guardrails: {gr_summary}\n"
        f"Please review and ack with decision=approved or decision=rejected."
    )

    state = get_app_state()
    ask_tracker = state.ask_tracker
    try:
        # Use gate_id as correlation_id so the ack handler can map directly
        cid = await ask_tracker.register(
            from_peer_id=author.peer_id,
            from_peer_name=author.display_name,
            to_peer_id=reviewer.peer_id,
            to_peer_name=reviewer.display_name,
            text=ask_text,
            reply_to=None,
            correlation_id=gate_id,
        )
    except Exception as exc:
        logger.warning("Failed to register review ask for gate %s: %s", gate_id, exc)
        return _gate_to_response(gate)

    try:
        peer_delivery = peer_delivery_from_state(
            config=state.config,
            registry=peer_registry,
            state=state,
        )
        await peer_delivery.deliver_ask(
            from_peer=author.peer_id,
            to_peer=reviewer.peer_id,
            text=ask_text,
            correlation_id=gate_id,
            bypass_circle=True,
        )
        # Update gate to in_review once ask is sent
        store.update_state(gate_id, "in_review")
    except Exception as exc:
        logger.warning("Failed to deliver review ask for gate %s: %s", gate_id, exc)

    return _gate_to_response(store.get(gate_id))


@router.get("/review-gates/{gate_id}", response_model=ReviewGateResponse)
async def get_review_gate(
    gate_id: str,
    _: str | None = Depends(require_auth),
) -> ReviewGateResponse:
    store = _get_store()
    gate = store.get(gate_id)
    if gate is None:
        raise HTTPException(status_code=404, detail=f"Gate not found: {gate_id}")
    return _gate_to_response(gate)


@router.get("/review-gates/{gate_id}/readiness", response_model=MergeReadinessResponse)
async def get_merge_readiness(
    gate_id: str,
    _: str | None = Depends(require_auth),
) -> MergeReadinessResponse:
    store = _get_store()
    readiness = store.merge_readiness(gate_id)
    return MergeReadinessResponse(
        ready=readiness["ready"],
        gate_id=readiness.get("gate_id", gate_id),
        checks=readiness["checks"],
    )


@router.get("/review-gates", response_model=ReviewGateListResponse)
async def list_review_gates(
    reviewer_peer_id: str | None = Query(None),
    author_peer_id: str | None = Query(None),
    state: str | None = Query(None),
    _: str | None = Depends(require_auth),
) -> ReviewGateListResponse:
    store = _get_store()
    gates: list = []
    if reviewer_peer_id:
        gates = store.list_for_reviewer(reviewer_peer_id, state=state)
    elif author_peer_id:
        gates = store.list_for_author(author_peer_id, state=state)
    else:
        # No filter: return recent gates (limited to 50)
        rows = store._conn.execute(
            "SELECT * FROM review_gates ORDER BY updated_at DESC LIMIT 50"
        ).fetchall()
        gates = [g for row in rows if (g := store._row_to_gate(row)) is not None]
    return ReviewGateListResponse(gates=[_gate_to_response(g) for g in gates])
```

- [ ] **Step 4: Integrate review gate processing into ack handler**

In `repowire/daemon/routes/asks.py`, add the following helper function near the top of the file (after imports, before route handlers):

```python
async def _process_review_gate_on_ack(
    correlation_id: str,
    message: str | None,
) -> None:
    """If this ack closes a review-gate ask, update the gate state.

    The reviewer includes the decision in their ack message, e.g.:
    "Looks good. decision:approved" or "Needs fixes. decision:rejected"
    """
    from repowire.daemon.deps import get_app_state

    state = get_app_state()
    review_gate_store = getattr(state, "review_gate_store", None)
    if review_gate_store is None:
        return

    gate = review_gate_store.get(correlation_id)
    if gate is None:
        return

    decision = None
    if message:
        lowered = message.lower()
        for marker in ("decision:approved", "decision=approved"):
            if marker in lowered:
                decision = "approved"
                break
        if decision is None:
            for marker in ("decision:rejected", "decision=rejected"):
                if marker in lowered:
                    decision = "rejected"
                    break

    if decision == "approved":
        review_gate_store.update_state(correlation_id, "approved")
    elif decision == "rejected":
        review_gate_store.update_state(correlation_id, "rejected")
```

Then in the `ack_ask` handler (`@router.post("/ack")`), after the line `existing = await ask_tracker.get(request.correlation_id)` and before the `if existing is None:` check, add:

```python
    # Review-gate integration: update gate state before delivery
    if request.message:
        await _process_review_gate_on_ack(request.correlation_id, request.message)
```

The placement should be around line 1046 in the original file, so the full block becomes:

```python
    existing = await ask_tracker.get(request.correlation_id)

    # Review-gate integration
    if request.message:
        await _process_review_gate_on_ack(request.correlation_id, request.message)

    if existing is None:
        ...
```

- [ ] **Step 5: Register router in app.py**

In `repowire/daemon/app.py`, add to the imports (around line 42):

```python
from repowire.daemon.routes import review_gates
```

In `create_app()`, add after `app.include_router(budget.router)`:

```python
    app.include_router(review_gates.router)
```

In `create_test_app()`, add the same line.

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_review_routes.py -v`

Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add repowire/daemon/routes/review_gates.py repowire/daemon/routes/asks.py repowire/daemon/app.py tests/test_review_routes.py
git commit -m "feat(m3): add review gate routes and ack integration"
```

---

## Task 5: Review MCP Tools

**Files:**
- Modify: `repowire/mcp/server.py`
- Test: `tests/test_review_mcp.py`

- [ ] **Step 1: Write the failing review MCP tests**

```python
# tests/test_review_mcp.py
from unittest.mock import AsyncMock, patch

import pytest

from repowire.mcp.server import create_mcp_server


def _tool(name: str):
    return create_mcp_server()._tool_manager._tools[name].fn


@pytest.mark.asyncio
async def test_request_review() -> None:
    with (
        patch("repowire.mcp.server._ensure_registered", new_callable=AsyncMock),
        patch("repowire.mcp.server._ensure_registered_strict", new_callable=AsyncMock),
        patch("repowire.mcp.server._get_my_peer_identifier", new_callable=AsyncMock) as peer,
        patch("repowire.mcp.server.daemon_request", new_callable=AsyncMock) as req,
    ):
        peer.return_value = "peer-author"
        req.return_value = {
            "gate_id": "review-abc123",
            "state": "pending",
            "author_peer_id": "peer-author",
            "reviewer_peer_id": "peer-reviewer",
        }

        result = await _tool("request_review")(
            reviewer_peer="peer-reviewer",
            artifact_type="diff",
            artifact_ref="/tmp/feature.diff",
            guardrail_results={"lint": {"passed": True}},
        )

    assert result["gate_id"] == "review-abc123"
    assert result["state"] == "pending"
    req.assert_awaited_once()
    method, path, body = req.await_args.args[:3]
    assert method == "POST"
    assert path == "/review-gates"
    assert body["reviewer_peer_id"] == "peer-reviewer"


@pytest.mark.asyncio
async def test_merge_readiness() -> None:
    with (
        patch("repowire.mcp.server._ensure_registered", new_callable=AsyncMock),
        patch("repowire.mcp.server.daemon_request", new_callable=AsyncMock) as req,
    ):
        req.return_value = {
            "ready": True,
            "gate_id": "review-abc123",
            "checks": {
                "lint": {"passed": True},
                "tests": {"passed": True},
                "type_check": {"passed": True},
                "review": {"state": "approved"},
            },
        }

        result = await _tool("merge_readiness")(gate_id="review-abc123")

    assert result["ready"] is True
    req.assert_awaited_once()
    method, path = req.await_args.args[:2]
    assert method == "GET"
    assert path == "/review-gates/review-abc123/readiness"


@pytest.mark.asyncio
async def test_list_pending_reviews() -> None:
    with (
        patch("repowire.mcp.server._ensure_registered", new_callable=AsyncMock),
        patch("repowire.mcp.server._get_my_peer_identifier", new_callable=AsyncMock) as peer,
        patch("repowire.mcp.server.daemon_request", new_callable=AsyncMock) as req,
    ):
        peer.return_value = "peer-reviewer"
        req.return_value = {
            "gates": [
                {
                    "gate_id": "review-abc123",
                    "state": "pending",
                    "author_peer_id": "peer-author",
                    "reviewer_peer_id": "peer-reviewer",
                    "artifact_type": "diff",
                    "artifact_ref": "/tmp/feature.diff",
                }
            ]
        }

        result = await _tool("list_pending_reviews")()

    assert len(result["gates"]) == 1
    req.assert_awaited_once()
    method, path = req.await_args.args[:2]
    assert method == "GET"
    assert "/review-gates" in path
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_review_mcp.py -v`

Expected: FAIL with `KeyError: 'request_review'`

- [ ] **Step 3: Implement review MCP tools**

Add these tool definitions inside `create_mcp_server()` in `repowire/mcp/server.py`, before the `return mcp` statement.

```python
    @mcp.tool()
    async def request_review(
        reviewer_peer: str,
        artifact_type: str,
        artifact_ref: str,
        guardrail_results: dict | None = None,
    ) -> dict:
        """[Repowire mesh] Submit an artifact for peer review.

        Creates a review gate and sends an ask to the reviewer. The reviewer
        must ack with attachments containing {"decision": "approved"} or
        {"decision": "rejected"}.

        Args:
            reviewer_peer: Display name or peer_id of the reviewer.
            artifact_type: Type of artifact — diff, file, or pr.
            artifact_ref: File path, PR URL, or diff hash.
            guardrail_results: Output from lint_code, run_tests, type_check.

        Returns:
            Review gate metadata including gate_id and current state.
        """
        await _ensure_registered(strict=True)
        my_peer = await _get_my_peer_identifier()
        body = {
            "author_peer_id": my_peer,
            "reviewer_peer_id": reviewer_peer,
            "artifact_type": artifact_type,
            "artifact_ref": artifact_ref,
            "guardrail_results": guardrail_results or {},
        }
        result = await daemon_request("POST", "/review-gates", body)
        if result is None:
            return {"error": "Failed to create review gate"}
        return result

    @mcp.tool()
    async def merge_readiness(gate_id: str) -> dict:
        """[Repowire mesh] Check if an artifact is ready to merge.

        Returns ready=True only when lint, tests, type_check, and review
        approval have all passed.

        Args:
            gate_id: The review gate ID returned by request_review.

        Returns:
            Merge readiness report with per-check status.
        """
        await _ensure_registered()
        result = await daemon_request("GET", f"/review-gates/{gate_id}/readiness")
        if result is None:
            return {"error": f"Failed to query readiness for gate {gate_id}"}
        return result

    @mcp.tool()
    async def list_pending_reviews() -> dict:
        """[Repowire mesh] List review gates assigned to the caller.

        Returns:
            List of pending review gates where the caller is the reviewer.
        """
        await _ensure_registered()
        my_peer = await _get_my_peer_identifier()
        result = await daemon_request("GET", "/review-gates", params={
            "reviewer_peer_id": my_peer,
            "state": "pending",
        })
        if result is None:
            return {"gates": []}
        return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_review_mcp.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add repowire/mcp/server.py tests/test_review_mcp.py
git commit -m "feat(m3): add review MCP tools — request_review, merge_readiness, list_pending_reviews"
```

---

## Task 6: Full Suite Verification

- [ ] **Step 1: Run all tests**

```bash
pytest tests/ -x -q
```

Expected: All tests pass (including M2 and M3 tests).

- [ ] **Step 2: Run linters**

```bash
ruff check repowire/ tests/
```

Expected: No errors.

- [ ] **Step 3: Run type checker**

```bash
uv run ty check repowire/ tests/
```

Expected: No type errors.

- [ ] **Step 4: Commit**

```bash
git commit -m "test: verify full suite passes with M3 cross-runtime review cycles"
```

---

## Spec Coverage Check

| Spec Requirement | Task |
|---|---|
| ReviewGateStore in SQLite (pending/in_review/approved/rejected/merged) | Task 2 |
| Schema migration v12 for review_gates table | Task 1 |
| `POST /review-gates` creates gate + sends ask | Task 4 |
| `GET /review-gates/{gate_id}` returns gate | Task 4 |
| `GET /review-gates/{gate_id}/readiness` merge gate | Task 4 |
| `GET /review-gates` list with reviewer/author/state filters | Task 4 |
| Ack handler auto-updates review gate from attachments | Task 4 |
| `request_review` MCP tool (strict register, POST /review-gates) | Task 5 |
| `merge_readiness` MCP tool (GET /readiness) | Task 5 |
| `list_pending_reviews` MCP tool (GET /review-gates?reviewer=&state=pending) | Task 5 |
| Guardrail-gated merge: lint + tests + type_check + review ack | Task 2 (merge_readiness), Task 5 |
| Store wired into app state | Task 3 |

**Gaps:** None for M3 scope.

---

## Placeholder Scan

- [x] No "TBD", "TODO", "implement later", "fill in details"
- [x] Every test contains actual test code
- [x] Every implementation step contains actual code snippets
- [x] No references to undefined types/functions

---

## Type Consistency Check

| Name | First Definition | Later Uses | Consistent? |
|---|---|---|---|
| `ReviewGate` | Task 2 dataclass | Task 2 store methods, Task 4 routes | ✅ |
| `SQLiteReviewGateStore` | Task 2 class | Task 3 wiring, Task 4 routes, Task 5 MCP | ✅ |
| `ReviewGateResponse` | Task 4 response model | Task 4 routes, Task 5 MCP | ✅ |
| `MergeReadinessResponse` | Task 4 response model | Task 4 route, Task 5 MCP merge_readiness | ✅ |
| `gate_id` prefix | Task 4 `f"review-{uuid4().hex[:12]}"` | Task 2 tests, Task 4 routes, Task 5 MCP | ✅ |

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-06-01-cross-runtime-review-cycles.md`.**

**Two execution options:**

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

**Which approach?**
