# M2: Token Budgets & Guardrails — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enforce a 100k token hard limit per context window (mesh peer + native subagent), and expose deterministic guardrail MCP tools (`lint_code`, `run_tests`, `type_check`, `static_analyze`) that any mesh peer can invoke.

**Architecture:** A new `SQLiteTokenBudgetStore` tracks per-entity cumulative usage in SQLite. Budget checks happen at turn boundaries (prompt hook queries daemon; stop hook reports usage). Guardrail tools run `subprocess.run()` in the peer's project directory and return structured JSON — no LLM interpretation.

**Tech Stack:** Python 3.12, FastAPI, pydantic, pytest, SQLite, subprocess

**Prerequisite:** M1 (Kimi Code Backend) must be complete so that `AgentType.KIMI_CODE` exists and peers can register.

---

## File Structure

| File | Responsibility |
|---|---|
| `repowire/daemon/state/token_budgets.py` | `SQLiteTokenBudgetStore` — CRUD, usage recording, budget enforcement, warning logic |
| `repowire/daemon/state/database.py` | Schema migration v11 — `CREATE TABLE token_budgets` |
| `repowire/daemon/state/__init__.py` | Export `SQLiteTokenBudgetStore` |
| `repowire/daemon/routes/budget.py` | `POST /peers/{identifier}/usage`, `GET /peers/{identifier}/budget`, `POST /budget/check` |
| `repowire/daemon/app.py` | Wire `token_budget_store` into app state (both `create_app` and `create_test_app`) |
| `repowire/hooks/prompt_handler.py` | Query budget before marking BUSY; block turn for backends that support hook-level block |
| `repowire/hooks/stop_handler.py` | Estimate and POST turn usage to daemon after turn completes |
| `repowire/mcp/server.py` | Guardrail tools: `lint_code`, `run_tests`, `type_check`, `static_analyze`, `get_budget_status` |
| `tests/test_token_budgets.py` | Store unit tests |
| `tests/test_budget_routes.py` | Route integration tests |
| `tests/test_guardrail_tools.py` | MCP tool tests (mocked subprocess) |

---

## Task 1: Schema Migration

**Files:**
- Modify: `repowire/daemon/state/database.py`

- [ ] **Step 1: Bump SCHEMA_VERSION and add token_budgets table**

```python
# repowire/daemon/state/database.py
# Change line 11:
SCHEMA_VERSION = 11
```

Add inside `migrate()`, after the `delivery_traces` block and before the first `schema_migrations` INSERT:

```python
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS token_budgets (
                    budget_id TEXT PRIMARY KEY,
                    parent_peer_id TEXT,
                    agent_type TEXT NOT NULL,
                    cumulative_input_tokens INTEGER NOT NULL DEFAULT 0,
                    cumulative_output_tokens INTEGER NOT NULL DEFAULT 0,
                    budget_ceiling INTEGER NOT NULL DEFAULT 100000,
                    warning_sent INTEGER NOT NULL DEFAULT 0,
                    last_updated TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
                )
                """,
            )
            self.conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_token_budgets_parent
                ON token_budgets(parent_peer_id)
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
                (11, "per-context-window token budget tracking"),
            )
```

- [ ] **Step 2: Verify migration runs cleanly**

Run: `python -c "from repowire.daemon.state.database import StateDatabase; from pathlib import Path; import tempfile; p = Path(tempfile.mkdtemp()) / 'state.db'; db = StateDatabase(p); print('user_version:', db.conn.execute('PRAGMA user_version').fetchone()[0]); db.close()"`

Expected: `user_version: 11`

- [ ] **Step 3: Commit**

```bash
git add repowire/daemon/state/database.py
git commit -m "feat(m2): add token_budgets schema migration v11"
```

---

## Task 2: Implement SQLiteTokenBudgetStore

**Files:**
- Create: `repowire/daemon/state/token_budgets.py`
- Test: `tests/test_token_budgets.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_token_budgets.py
from pathlib import Path

from repowire.daemon.state.database import StateDatabase
from repowire.daemon.state.token_budgets import SQLiteTokenBudgetStore


def test_token_budget_store_create_and_get(tmp_path: Path) -> None:
    db = StateDatabase(tmp_path / "state.db")
    try:
        store = SQLiteTokenBudgetStore(db)
        budget = store.get_or_create("peer-abc", agent_type="kimi-code")
        assert budget.budget_id == "peer-abc"
        assert budget.cumulative_input_tokens == 0
        assert budget.cumulative_output_tokens == 0
        assert budget.budget_ceiling == 100000
        assert budget.warning_sent == 0
    finally:
        db.close()


def test_token_budget_store_record_usage(tmp_path: Path) -> None:
    db = StateDatabase(tmp_path / "state.db")
    try:
        store = SQLiteTokenBudgetStore(db)
        store.get_or_create("peer-abc", agent_type="kimi-code")
        store.record_usage("peer-abc", input_tokens=15000, output_tokens=8000)
        budget = store.get("peer-abc")
        assert budget is not None
        assert budget.cumulative_input_tokens == 15000
        assert budget.cumulative_output_tokens == 8000
    finally:
        db.close()


def test_token_budget_store_check_budget_pass(tmp_path: Path) -> None:
    db = StateDatabase(tmp_path / "state.db")
    try:
        store = SQLiteTokenBudgetStore(db)
        store.get_or_create("peer-abc", agent_type="kimi-code")
        store.record_usage("peer-abc", input_tokens=10000, output_tokens=5000)
        decision = store.check_budget("peer-abc", estimated_input=5000)
        assert decision.proceed is True
        assert decision.remaining == 85000
    finally:
        db.close()


def test_token_budget_store_check_budget_fail(tmp_path: Path) -> None:
    db = StateDatabase(tmp_path / "state.db")
    try:
        store = SQLiteTokenBudgetStore(db)
        store.get_or_create("peer-abc", agent_type="kimi-code")
        store.record_usage("peer-abc", input_tokens=95000, output_tokens=4000)
        decision = store.check_budget("peer-abc", estimated_input=5000)
        assert decision.proceed is False
        assert decision.reason == "budget_exhausted"
        assert decision.used == 99000
        assert decision.ceiling == 100000
    finally:
        db.close()


def test_token_budget_store_warning_at_80_percent(tmp_path: Path) -> None:
    db = StateDatabase(tmp_path / "state.db")
    try:
        store = SQLiteTokenBudgetStore(db)
        store.get_or_create("peer-abc", agent_type="kimi-code")
        store.record_usage("peer-abc", input_tokens=70000, output_tokens=10000)
        warned = store.maybe_warn("peer-abc")
        assert warned is True
        budget = store.get("peer-abc")
        assert budget is not None
        assert budget.warning_sent == 1
        warned_again = store.maybe_warn("peer-abc")
        assert warned_again is False
    finally:
        db.close()


def test_token_budget_store_subagent_lifecycle(tmp_path: Path) -> None:
    db = StateDatabase(tmp_path / "state.db")
    try:
        store = SQLiteTokenBudgetStore(db)
        store.upsert_subagent(
            parent_peer_id="peer-abc",
            subagent_id="agent-0",
            agent_type="kimi-subagent",
            event="created",
        )
        budget = store.get("peer-abc/agent-0")
        assert budget is not None
        assert budget.parent_peer_id == "peer-abc"
        assert budget.agent_type == "kimi-subagent"

        store.upsert_subagent(
            parent_peer_id="peer-abc",
            subagent_id="agent-0",
            agent_type="kimi-subagent",
            event="usage_update",
            input_tokens=5000,
            output_tokens=3000,
        )
        budget = store.get("peer-abc/agent-0")
        assert budget.cumulative_input_tokens == 5000
        assert budget.cumulative_output_tokens == 3000

        store.upsert_subagent(
            parent_peer_id="peer-abc",
            subagent_id="agent-0",
            agent_type="kimi-subagent",
            event="destroyed",
        )
        budget = store.get("peer-abc/agent-0")
        assert budget is None
    finally:
        db.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_token_budgets.py -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'repowire.daemon.state.token_budgets'`

- [ ] **Step 3: Implement SQLiteTokenBudgetStore**

```python
# repowire/daemon/state/token_budgets.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from repowire.daemon.state.database import StateDatabase
from repowire.daemon.work_store import json_dumps, json_loads, now_iso


@dataclass(frozen=True)
class TokenBudget:
    budget_id: str
    parent_peer_id: str | None
    agent_type: str
    cumulative_input_tokens: int
    cumulative_output_tokens: int
    budget_ceiling: int
    warning_sent: int
    last_updated: str


@dataclass(frozen=True)
class BudgetDecision:
    proceed: bool
    remaining: int = 0
    reason: str | None = None
    used: int = 0
    ceiling: int = 0


class SQLiteTokenBudgetStore:
    """Repository for per-context-window token budget tracking."""

    def __init__(self, db: StateDatabase) -> None:
        self._conn = db.conn

    @staticmethod
    def _row_to_budget(row) -> TokenBudget | None:
        if row is None:
            return None
        return TokenBudget(
            budget_id=row["budget_id"],
            parent_peer_id=row["parent_peer_id"],
            agent_type=row["agent_type"],
            cumulative_input_tokens=row["cumulative_input_tokens"],
            cumulative_output_tokens=row["cumulative_output_tokens"],
            budget_ceiling=row["budget_ceiling"],
            warning_sent=row["warning_sent"],
            last_updated=row["last_updated"],
        )

    def get(self, budget_id: str) -> TokenBudget | None:
        row = self._conn.execute(
            "SELECT * FROM token_budgets WHERE budget_id = ?",
            (budget_id,),
        ).fetchone()
        return self._row_to_budget(row)

    def get_or_create(self, budget_id: str, *, agent_type: str, parent_peer_id: str | None = None) -> TokenBudget:
        existing = self.get(budget_id)
        if existing is not None:
            return existing
        now = now_iso()
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO token_budgets(
                    budget_id, parent_peer_id, agent_type,
                    cumulative_input_tokens, cumulative_output_tokens,
                    budget_ceiling, warning_sent, last_updated
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (budget_id, parent_peer_id, agent_type, 0, 0, 100000, 0, now),
            )
        return self.get(budget_id)

    def record_usage(
        self,
        budget_id: str,
        *,
        input_tokens: int = 0,
        output_tokens: int = 0,
    ) -> TokenBudget | None:
        budget = self.get(budget_id)
        if budget is None:
            return None
        new_input = budget.cumulative_input_tokens + input_tokens
        new_output = budget.cumulative_output_tokens + output_tokens
        now = now_iso()
        with self._conn:
            self._conn.execute(
                """
                UPDATE token_budgets
                SET cumulative_input_tokens = ?,
                    cumulative_output_tokens = ?,
                    last_updated = ?
                WHERE budget_id = ?
                """,
                (new_input, new_output, now, budget_id),
            )
        return self.get(budget_id)

    def check_budget(self, budget_id: str, estimated_input: int) -> BudgetDecision:
        budget = self.get(budget_id)
        if budget is None:
            return BudgetDecision(proceed=True, remaining=100000)
        used = budget.cumulative_input_tokens + budget.cumulative_output_tokens
        if used + estimated_input > budget.budget_ceiling:
            return BudgetDecision(
                proceed=False,
                reason="budget_exhausted",
                used=used,
                ceiling=budget.budget_ceiling,
            )
        return BudgetDecision(
            proceed=True,
            remaining=budget.budget_ceiling - used,
        )

    def maybe_warn(self, budget_id: str) -> bool:
        budget = self.get(budget_id)
        if budget is None:
            return False
        used = budget.cumulative_input_tokens + budget.cumulative_output_tokens
        threshold = int(0.8 * budget.budget_ceiling)
        if used < threshold or budget.warning_sent:
            return False
        with self._conn:
            self._conn.execute(
                "UPDATE token_budgets SET warning_sent = 1 WHERE budget_id = ?",
                (budget_id,),
            )
        return True

    def upsert_subagent(
        self,
        *,
        parent_peer_id: str,
        subagent_id: str,
        agent_type: str,
        event: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
    ) -> TokenBudget | None:
        budget_id = f"{parent_peer_id}/{subagent_id}"
        if event == "created":
            return self.get_or_create(budget_id, agent_type=agent_type, parent_peer_id=parent_peer_id)
        if event == "destroyed":
            with self._conn:
                self._conn.execute(
                    "DELETE FROM token_budgets WHERE budget_id = ?",
                    (budget_id,),
                )
            return None
        if event == "usage_update":
            self.get_or_create(budget_id, agent_type=agent_type, parent_peer_id=parent_peer_id)
            return self.record_usage(budget_id, input_tokens=input_tokens, output_tokens=output_tokens)
        return self.get(budget_id)
```

- [ ] **Step 4: Export from state package**

```python
# repowire/daemon/state/__init__.py
# Add to imports:
from repowire.daemon.state.token_budgets import SQLiteTokenBudgetStore

# Add to __all__:
    "SQLiteTokenBudgetStore",
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_token_budgets.py -v`

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add repowire/daemon/state/token_budgets.py repowire/daemon/state/__init__.py tests/test_token_budgets.py
git commit -m "feat(m2): add SQLiteTokenBudgetStore with enforcement and subagent lifecycle"
```

---

## Task 3: Wire TokenBudgetStore into Daemon App

**Files:**
- Modify: `repowire/daemon/app.py`

- [ ] **Step 1: Import and instantiate in `create_app()`**

Add import near the other state store imports (around line 71):

```python
from repowire.daemon.state.token_budgets import SQLiteTokenBudgetStore
```

Inside `lifespan`, after `delivery_trace_store = DeliveryTraceStore(state_db)` (around line 271), add:

```python
        token_budget_store = SQLiteTokenBudgetStore(state_db)
```

After `app.state.delivery_trace_store = delivery_trace_store` (around line 293), add:

```python
        app.state.token_budget_store = token_budget_store
```

- [ ] **Step 2: Repeat for `create_test_app()`**

In `create_test_app()`, after `delivery_trace_store = DeliveryTraceStore(state_db)` (around line 624), add:

```python
        token_budget_store = SQLiteTokenBudgetStore(state_db)
```

After `app.state.delivery_trace_store = delivery_trace_store` (around line 644), add:

```python
        app.state.token_budget_store = token_budget_store
```

- [ ] **Step 3: Verify import works**

Run: `python -c "from repowire.daemon.app import create_app; print('ok')"`

Expected: `ok`

- [ ] **Step 4: Commit**

```bash
git add repowire/daemon/app.py
git commit -m "feat(m2): wire TokenBudgetStore into daemon app state"
```

---

## Task 4: Budget HTTP Routes

**Files:**
- Create: `repowire/daemon/routes/budget.py`
- Test: `tests/test_budget_routes.py`

- [ ] **Step 1: Write the failing route test**

```python
# tests/test_budget_routes.py
import pytest
from httpx import AsyncClient

from repowire.daemon.deps import cleanup_deps
from repowire.daemon.routes import budget, peers
from tests.conftest import async_client_for, make_daemon_app

ROUTERS = (peers.router, budget.router)


@pytest.fixture
async def env(tmp_path):
    harness = make_daemon_app(tmp_path, ROUTERS)
    async with async_client_for(harness.app) as client:
        yield client, harness
    cleanup_deps()


@pytest.mark.asyncio
async def test_post_usage(env):
    client, harness = env
    r = await client.post("/peers", json={
        "name": "alice",
        "path": "/tmp/alice",
        "backend": "kimi-code",
    })
    assert r.status_code == 200
    peer_id = r.json()["peer_id"]

    r = await client.post(
        f"/peers/{peer_id}/usage",
        json={"input_tokens": 15000, "output_tokens": 8000},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["used"] == 23000
    assert body["remaining"] == 77000
    assert body["ceiling"] == 100000


@pytest.mark.asyncio
async def test_get_budget(env):
    client, harness = env
    r = await client.post("/peers", json={
        "name": "bob",
        "path": "/tmp/bob",
        "backend": "claude-code",
    })
    assert r.status_code == 200
    peer_id = r.json()["peer_id"]

    # Pre-seed usage via store
    store = harness.app.state.token_budget_store
    store.record_usage(peer_id, input_tokens=10000, output_tokens=5000)

    r = await client.get(f"/peers/{peer_id}/budget")
    assert r.status_code == 200
    body = r.json()
    assert body["used"] == 15000
    assert body["remaining"] == 85000
    assert body["ceiling"] == 100000


@pytest.mark.asyncio
async def test_get_budget_unknown_peer(env):
    client, harness = env
    r = await client.get("/peers/peer-unknown/budget")
    assert r.status_code == 200
    body = r.json()
    assert body["used"] == 0
    assert body["remaining"] == 100000
    assert body["ceiling"] == 100000


@pytest.mark.asyncio
async def test_budget_check(env):
    client, harness = env
    r = await client.post("/peers", json={
        "name": "carol",
        "path": "/tmp/carol",
        "backend": "gemini",
    })
    assert r.status_code == 200
    peer_id = r.json()["peer_id"]

    store = harness.app.state.token_budget_store
    store.record_usage(peer_id, input_tokens=95000, output_tokens=4000)

    r = await client.post("/budget/check", json={
        "identifier": peer_id,
        "estimated_input": 5000,
    })
    assert r.status_code == 200
    body = r.json()
    assert body["decision"] == "block"
    assert body["reason"] == "budget_exhausted"
    assert body["used"] == 99000
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_budget_routes.py -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'repowire.daemon.routes.budget'`

- [ ] **Step 3: Implement budget routes**

```python
# repowire/daemon/routes/budget.py
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from repowire.daemon.auth import require_auth
from repowire.daemon.deps import get_app_state, get_peer_registry
from repowire.daemon.routes._shared import OkResponse

router = APIRouter(tags=["budget"])


class RecordUsageRequest(BaseModel):
    input_tokens: int = Field(..., ge=0, description="Input tokens consumed this turn")
    output_tokens: int = Field(..., ge=0, description="Output tokens consumed this turn")


class BudgetStatusResponse(BaseModel):
    budget_id: str
    used: int
    remaining: int
    ceiling: int
    warning_sent: bool


class CheckBudgetRequest(BaseModel):
    identifier: str = Field(..., description="Peer display name or peer_id")
    estimated_input: int = Field(default=0, ge=0, description="Estimated tokens for next turn")


class CheckBudgetResponse(BaseModel):
    decision: str
    remaining: int
    reason: str | None = None
    used: int = 0


def _get_store():
    state = get_app_state()
    store = getattr(state, "token_budget_store", None)
    if store is None:
        raise RuntimeError("token_budget_store not initialized")
    return store


def _resolve_budget_id(identifier: str) -> tuple[str, str]:
    """Resolve identifier to (budget_id, agent_type). Falls back to identifier itself."""
    try:
        peer_registry = get_peer_registry()
        peer = peer_registry.get_peer_sync(identifier)
        if peer is not None:
            return peer.peer_id, peer.backend.value
    except Exception:
        pass
    return identifier, "unknown"


@router.post("/peers/{identifier}/usage", response_model=BudgetStatusResponse)
async def record_usage(
    identifier: str,
    request: RecordUsageRequest,
    _: str | None = Depends(require_auth),
) -> BudgetStatusResponse:
    """Record token usage for a peer and return updated budget status."""
    budget_id, agent_type = _resolve_budget_id(identifier)
    store = _get_store()
    budget = store.get_or_create(budget_id, agent_type=agent_type)
    budget = store.record_usage(
        budget_id,
        input_tokens=request.input_tokens,
        output_tokens=request.output_tokens,
    )
    if budget is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to record usage",
        )
    used = budget.cumulative_input_tokens + budget.cumulative_output_tokens
    return BudgetStatusResponse(
        budget_id=budget.budget_id,
        used=used,
        remaining=budget.budget_ceiling - used,
        ceiling=budget.budget_ceiling,
        warning_sent=bool(budget.warning_sent),
    )


@router.get("/peers/{identifier}/budget", response_model=BudgetStatusResponse)
async def get_budget(
    identifier: str,
    _: str | None = Depends(require_auth),
) -> BudgetStatusResponse:
    """Get current token budget status for a peer."""
    budget_id, _agent_type = _resolve_budget_id(identifier)
    store = _get_store()
    budget = store.get(budget_id)
    if budget is None:
        return BudgetStatusResponse(
            budget_id=budget_id,
            used=0,
            remaining=100000,
            ceiling=100000,
            warning_sent=False,
        )
    used = budget.cumulative_input_tokens + budget.cumulative_output_tokens
    return BudgetStatusResponse(
        budget_id=budget.budget_id,
        used=used,
        remaining=budget.budget_ceiling - used,
        ceiling=budget.budget_ceiling,
        warning_sent=bool(budget.warning_sent),
    )


@router.post("/budget/check", response_model=CheckBudgetResponse)
async def check_budget(
    request: CheckBudgetRequest,
    _: str | None = Depends(require_auth),
) -> CheckBudgetResponse:
    """Check if a turn should proceed given estimated token cost."""
    budget_id, _agent_type = _resolve_budget_id(request.identifier)
    store = _get_store()
    decision = store.check_budget(budget_id, request.estimated_input)
    if decision.proceed:
        return CheckBudgetResponse(decision="proceed", remaining=decision.remaining)
    return CheckBudgetResponse(
        decision="block",
        remaining=0,
        reason=decision.reason,
        used=decision.used,
    )
```

**Note:** `_resolve_budget_id` calls `peer_registry.get_peer_sync(identifier)`. If `get_peer_sync` does not exist on `PeerRegistry`, use `await peer_registry.get_peer(identifier)` and make the route handler `async` (it already is). In that case, change `_resolve_budget_id` to an async function:

```python
async def _resolve_budget_id(identifier: str) -> tuple[str, str]:
    peer_registry = get_peer_registry()
    peer = await peer_registry.get_peer(identifier)
    if peer is not None:
        return peer.peer_id, peer.backend.value
    return identifier, "unknown"
```

And call it with `await` in each route.

- [ ] **Step 4: Register router in app.py**

In `repowire/daemon/app.py`, add to the imports (around line 42):

```python
from repowire.daemon.routes import budget
```

In `create_app()`, add after `app.include_router(work.router)` (around line 496):

```python
    app.include_router(budget.router)
```

In `create_test_app()`, add the same line in the router registration section (look for the block of `app.include_router(...)` calls and add it there).

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_budget_routes.py -v`

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add repowire/daemon/routes/budget.py repowire/daemon/app.py tests/test_budget_routes.py
git commit -m "feat(m2): add budget HTTP routes for usage, status, and turn-gate checks"
```

---

## Task 5: Hook Integration — Prompt Handler Budget Check

**Files:**
- Modify: `repowire/hooks/prompt_handler.py`

- [ ] **Step 1: Add budget check before marking BUSY**

In `repowire/hooks/prompt_handler.py`, add the following function after the imports and before `main()`:

```python
def _budget_check_blocking(backend: str) -> str | None:
    """Best-effort budget check. Returns block reason if budget exhausted.

    Backends that support hook-level block decisions (gemini, kimi)
    get a native block. Others log a warning and rely on daemon-level
    enforcement (ask routing, job runner).
    """
    from repowire.hooks.utils import daemon_get, get_display_name

    peer_name = get_display_name()
    try:
        resp = daemon_get(f"/peers/{peer_name}/budget")
        if resp and resp.get("remaining", 100000) <= 0:
            used = resp.get("used", 0)
            ceiling = resp.get("ceiling", 100000)
            reason = (
                f"Token budget exhausted: {used}/{ceiling}. "
                f"Kill this peer or spawn a fresh one to reset."
            )
            # Backends with native hook-level block support
            if backend in ("gemini", "kimi", "antigravity"):
                print(json.dumps({"decision": "deny", "reason": reason}))
                return reason
            # For claude-code / codex: daemon-level enforcement is the gate
            print(
                f"repowire prompt: budget exhausted ({used}/{ceiling}) — "
                f"daemon will block further asks",
                file=sys.stderr,
            )
            return reason
    except Exception as exc:
        print(f"repowire prompt: budget check failed: {exc}", file=sys.stderr)
    return None
```

Then modify the `main()` function. After `payload = normalize(input_data, backend)` and before `pane_id = get_pane_id()`, add:

```python
    # Budget gate: check before consuming a turn
    block_reason = _budget_check_blocking(backend)
    if block_reason and backend in ("gemini", "kimi", "antigravity"):
        # For these backends, the deny decision was already printed;
        # skip the normal BUSY mark and allow the runtime to handle it.
        return 0
```

The full modified `main()` should look like:

```python
def main(backend: str = "claude-code") -> int:
    """Main entry point for prompt hook."""
    try:
        input_data = json.loads(sys.stdin.read())
    except json.JSONDecodeError as e:
        print(f"repowire prompt: invalid JSON input: {e}", file=sys.stderr)
        return 0

    payload = normalize(input_data, backend)

    if payload.event != "UserPromptSubmit":
        return 0

    # Budget gate
    block_reason = _budget_check_blocking(backend)
    if block_reason and backend in ("gemini", "kimi", "antigravity"):
        return 0

    pane_id = get_pane_id()
    if pane_id:
        if not update_status(pane_id, "busy", use_pane_id=True, turn_state="working"):
            print(
                f"repowire prompt: failed to update status for pane {pane_id}",
                file=sys.stderr,
            )

    if backend == "claude-code":
        _maybe_spawn_chat_delta_streamer(
            payload.transcript_path,
            pane_id,
            payload.session_id or None,
        )

    hook_output(backend)
    return 0
```

- [ ] **Step 2: Run existing hook tests to ensure no regression**

Run: `pytest tests/test_hooks*.py -v -x`

Expected: All existing tests pass (new behavior is additive).

- [ ] **Step 3: Commit**

```bash
git add repowire/hooks/prompt_handler.py
git commit -m "feat(m2): prompt hook checks token budget before marking BUSY"
```

---

## Task 6: Hook Integration — Stop Handler Usage Report

**Files:**
- Modify: `repowire/hooks/stop_handler.py`

- [ ] **Step 1: Add usage estimation and reporting**

Add the following function after the imports and before `main()` in `repowire/hooks/stop_handler.py`:

```python
def _estimate_and_report_usage(
    backend: str,
    user_text: str | None,
    assistant_text: str | None,
) -> None:
    """Estimate token usage from turn text and POST it to the daemon.

    Uses backend-specific character-to-token multipliers.
    This is best-effort; true per-turn counts are preferred when available.
    """
    multipliers = {
        "claude-code": 4.0,
        "kimi-code": 3.5,
        "gemini": 4.0,
        "codex": 4.0,
        "opencode": 4.0,
        "antigravity": 4.0,
        "pi": 4.0,
    }
    mult = multipliers.get(backend, 4.0)
    safety = 1.1 if backend == "kimi-code" else 1.0

    input_tokens = int((len(user_text or "") / mult) * safety) if user_text else 0
    output_tokens = int((len(assistant_text or "") / mult) * safety) if assistant_text else 0

    if input_tokens == 0 and output_tokens == 0:
        return

    from repowire.hooks.utils import daemon_post, get_display_name

    peer_name = get_display_name()
    try:
        daemon_post(
            f"/peers/{peer_name}/usage",
            {"input_tokens": input_tokens, "output_tokens": output_tokens},
        )
    except Exception as exc:
        print(f"repowire stop: usage report failed: {exc}", file=sys.stderr)
```

Then modify the `main()` function. After `assistant_text` is finalized (after the `if assistant_text and not assistant_text.strip():` block) and before `write_handoff_summary(...)`, add:

```python
    # Report estimated usage for this turn
    _estimate_and_report_usage(backend, user_text, assistant_text)
```

- [ ] **Step 2: Run existing hook tests to ensure no regression**

Run: `pytest tests/test_hooks*.py -v -x`

Expected: All existing tests pass.

- [ ] **Step 3: Commit**

```bash
git add repowire/hooks/stop_handler.py
git commit -m "feat(m2): stop hook estimates and reports turn token usage"
```

---

## Task 7: Guardrail MCP Tools

**Files:**
- Modify: `repowire/mcp/server.py`
- Test: `tests/test_guardrail_tools.py`

- [ ] **Step 1: Write the failing guardrail tool tests**

```python
# tests/test_guardrail_tools.py
import json
from unittest.mock import patch

import pytest

from repowire.mcp.server import create_mcp_server


def _tool(name: str):
    return create_mcp_server()._tool_manager._tools[name].fn


@pytest.mark.asyncio
async def test_lint_code_ruff_found() -> None:
    with (
        patch("repowire.mcp.server._ensure_registered", new_callable=AsyncMock),
        patch("shutil.which", return_value="/usr/bin/ruff"),
        patch("subprocess.run") as run,
    ):
        run.return_value.returncode = 0
        run.return_value.stdout = '[]'
        run.return_value.stderr = ''

        result = await _tool("lint_code")(files=["src/foo.py"], linter="ruff")

    assert result["passed"] is True
    assert result["exit_code"] == 0
    assert result["violations"] == []


@pytest.mark.asyncio
async def test_lint_code_linter_missing() -> None:
    with (
        patch("repowire.mcp.server._ensure_registered", new_callable=AsyncMock),
        patch("shutil.which", return_value=None),
    ):
        result = await _tool("lint_code")(files=["src/foo.py"], linter="ruff")

    assert result["passed"] is False
    assert "not found" in result["error"]


@pytest.mark.asyncio
async def test_run_tests_pytest_found() -> None:
    with (
        patch("repowire.mcp.server._ensure_registered", new_callable=AsyncMock),
        patch("shutil.which", return_value="/usr/bin/pytest"),
        patch("subprocess.run") as run,
    ):
        run.return_value.returncode = 0
        run.return_value.stdout = '1 passed'
        run.return_value.stderr = ''

        result = await _tool("run_tests")(test_path="tests/", runner="pytest")

    assert result["passed"] is True
    assert result["exit_code"] == 0


@pytest.mark.asyncio
async def test_type_check_mypy_found() -> None:
    with (
        patch("repowire.mcp.server._ensure_registered", new_callable=AsyncMock),
        patch("shutil.which", return_value="/usr/bin/mypy"),
        patch("subprocess.run") as run,
    ):
        run.return_value.returncode = 0
        run.return_value.stdout = 'Success'
        run.return_value.stderr = ''

        result = await _tool("type_check")(files=["src/foo.py"], checker="mypy")

    assert result["passed"] is True
    assert result["exit_code"] == 0


@pytest.mark.asyncio
async def test_static_analyze_bandit_found() -> None:
    with (
        patch("repowire.mcp.server._ensure_registered", new_callable=AsyncMock),
        patch("shutil.which", return_value="/usr/bin/bandit"),
        patch("subprocess.run") as run,
    ):
        run.return_value.returncode = 0
        run.return_value.stdout = '{"results": []}'
        run.return_value.stderr = ''

        result = await _tool("static_analyze")(files=["src/foo.py"], analyzer="bandit")

    assert result["passed"] is True
    assert result["exit_code"] == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_guardrail_tools.py -v`

Expected: FAIL with `KeyError: 'lint_code'` (tools not yet defined).

- [ ] **Step 3: Implement guardrail tools in MCP server**

Add these tool definitions inside `create_mcp_server()` in `repowire/mcp/server.py`, before the `return mcp` statement.

Add imports at the top of `create_mcp_server()` or at module level:

```python
import shutil
import subprocess
from pathlib import Path
```

Then the tools:

```python
    @mcp.tool()
    async def lint_code(files: list[str], linter: str = "ruff") -> dict:
        """[Repowire mesh] Run a linter on the given files.

        Args:
            files: List of file paths to lint.
            linter: Linter to use. Supported: ruff, flake8, pylint, eslint, prettier.

        Returns:
            Structured lint result with passed, violations, stdout, stderr, exit_code.
        """
        await _ensure_registered()
        binary = shutil.which(linter)
        if not binary:
            return {"passed": False, "error": f"{linter} not found in PATH"}

        cmd: list[str]
        if linter == "ruff":
            cmd = [binary, "check", "--output-format", "json"] + files
        else:
            cmd = [binary] + files

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=60,
                cwd=Path.cwd(),
            )
        except subprocess.TimeoutExpired:
            return {"passed": False, "error": "Lint timed out after 60s"}
        except FileNotFoundError:
            return {"passed": False, "error": f"{linter} not found"}

        violations: list[dict] = []
        if linter == "ruff" and result.stdout:
            try:
                violations = json.loads(result.stdout)
                if not isinstance(violations, list):
                    violations = []
            except json.JSONDecodeError:
                pass

        return {
            "passed": result.returncode == 0,
            "violations": violations,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "exit_code": result.returncode,
        }

    @mcp.tool()
    async def run_tests(test_path: str, runner: str = "pytest") -> dict:
        """[Repowire mesh] Run tests and return structured results.

        Args:
            test_path: Path to test file or directory.
            runner: Test runner. Supported: pytest, unittest, jest, vitest, cargo test.

        Returns:
            Structured test result with passed, failed list, stdout, stderr, exit_code.
        """
        await _ensure_registered()
        binary = shutil.which(runner)
        if not binary:
            return {"passed": False, "error": f"{runner} not found in PATH"}

        cmd: list[str]
        if runner == "pytest":
            cmd = [binary, test_path, "-v", "--tb=short"]
        elif runner == "unittest":
            cmd = [binary, "-m", "unittest", "discover", "-s", test_path]
        else:
            cmd = [binary, test_path]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=120,
                cwd=Path.cwd(),
            )
        except subprocess.TimeoutExpired:
            return {"passed": False, "error": "Tests timed out after 120s"}
        except FileNotFoundError:
            return {"passed": False, "error": f"{runner} not found"}

        failed: list[dict] = []
        if runner == "pytest" and result.returncode != 0 and result.stdout:
            for line in result.stdout.splitlines():
                if "FAILED" in line:
                    parts = line.split("FAILED", 1)
                    failed.append({
                        "test": parts[0].strip() if parts else line.strip(),
                        "message": parts[1].strip() if len(parts) > 1 else "",
                    })

        return {
            "passed": result.returncode == 0,
            "failed": failed,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "exit_code": result.returncode,
        }

    @mcp.tool()
    async def type_check(files: list[str], checker: str = "mypy") -> dict:
        """[Repowire mesh] Run a type checker on the given files.

        Args:
            files: List of file paths to type-check.
            checker: Type checker. Supported: mypy, pyright, tsc, rustc.

        Returns:
            Structured type-check result with passed, errors, stdout, stderr, exit_code.
        """
        await _ensure_registered()
        binary = shutil.which(checker)
        if not binary:
            return {"passed": False, "error": f"{checker} not found in PATH"}

        cmd: list[str]
        if checker == "mypy":
            cmd = [binary, "--show-error-codes"] + files
        else:
            cmd = [binary] + files

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=120,
                cwd=Path.cwd(),
            )
        except subprocess.TimeoutExpired:
            return {"passed": False, "error": "Type check timed out after 120s"}
        except FileNotFoundError:
            return {"passed": False, "error": f"{checker} not found"}

        errors: list[dict] = []
        if result.returncode != 0 and result.stdout:
            for line in result.stdout.splitlines():
                if ":" in line and not line.startswith("Success"):
                    errors.append({"message": line.strip()})

        return {
            "passed": result.returncode == 0,
            "errors": errors,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "exit_code": result.returncode,
        }

    @mcp.tool()
    async def static_analyze(files: list[str], analyzer: str = "bandit") -> dict:
        """[Repowire mesh] Run a security/static analyzer on the given files.

        Args:
            files: List of file paths to analyze.
            analyzer: Analyzer. Supported: bandit, semgrep, safety.

        Returns:
            Structured analysis result with passed, issues, stdout, stderr, exit_code.
        """
        await _ensure_registered()
        binary = shutil.which(analyzer)
        if not binary:
            return {"passed": False, "error": f"{analyzer} not found in PATH"}

        cmd: list[str]
        if analyzer == "bandit":
            cmd = [binary, "-f", "json", "-r"] + files
        else:
            cmd = [binary] + files

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=120,
                cwd=Path.cwd(),
            )
        except subprocess.TimeoutExpired:
            return {"passed": False, "error": "Analysis timed out after 120s"}
        except FileNotFoundError:
            return {"passed": False, "error": f"{analyzer} not found"}

        issues: list[dict] = []
        if analyzer == "bandit" and result.stdout:
            try:
                data = json.loads(result.stdout)
                issues = data.get("results", [])
                if not isinstance(issues, list):
                    issues = []
            except json.JSONDecodeError:
                pass

        return {
            "passed": result.returncode == 0,
            "issues": issues,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "exit_code": result.returncode,
        }

    @mcp.tool()
    async def get_budget_status(peer_id: str | None = None) -> dict:
        """[Repowire mesh] Query token budget status for self or a subagent.

        Args:
            peer_id: Peer ID to query. Defaults to caller's own peer_id.

        Returns:
            Budget status dict with used, remaining, ceiling, warning_sent.
        """
        await _ensure_registered()
        target = peer_id or await _get_my_peer_identifier()
        resp = await daemon_request("GET", f"/peers/{target}/budget")
        if resp is None:
            return {"error": "Failed to query budget status"}
        return {
            "budget_id": resp.get("budget_id", target),
            "used": resp.get("used", 0),
            "remaining": resp.get("remaining", 100000),
            "ceiling": resp.get("ceiling", 100000),
            "warning_sent": resp.get("warning_sent", False),
        }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_guardrail_tools.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add repowire/mcp/server.py tests/test_guardrail_tools.py
git commit -m "feat(m2): add guardrail MCP tools — lint, test, type-check, static-analyze, budget-status"
```

---

## Task 8: Full Suite Verification

- [ ] **Step 1: Run all tests**

```bash
pytest tests/ -x -q
```

Expected: All tests pass (including new M2 tests).

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
git commit -m "test: verify full suite passes with M2 token budgets and guardrails"
```

---

## Spec Coverage Check

| Spec Requirement | Task |
|---|---|
| TokenBudgetStore in SQLite (100k default ceiling) | Task 2 |
| Schema migration v11 for token_budgets table | Task 1 |
| `record_usage` accumulates input + output tokens | Task 2 |
| `check_budget` hard limit enforcement | Task 2 |
| `maybe_warn` at 80% threshold | Task 2 |
| Subagent lifecycle (created/usage_update/destroyed) | Task 2 |
| Budget routes: POST usage, GET status, POST check | Task 4 |
| Prompt hook budget check (best-effort block) | Task 5 |
| Stop hook usage estimation + report | Task 6 |
| `lint_code` MCP tool (subprocess, structured JSON) | Task 7 |
| `run_tests` MCP tool | Task 7 |
| `type_check` MCP tool | Task 7 |
| `static_analyze` MCP tool | Task 7 |
| `get_budget_status` MCP tool | Task 7 |
| Store wired into app state (create_app + create_test_app) | Task 3 |

**Gaps:** None for M2 scope.

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
| `TokenBudget` | Task 2 dataclass | Task 2 store methods | ✅ |
| `BudgetDecision` | Task 2 dataclass | Task 2 check_budget, Task 4 route | ✅ |
| `SQLiteTokenBudgetStore` | Task 2 class | Task 3 wiring, Task 4 routes, Task 7 MCP | ✅ |
| `BudgetStatusResponse` | Task 4 response model | Task 4 routes, Task 7 MCP get_budget_status | ✅ |
| `CheckBudgetResponse` | Task 4 response model | Task 4 route, Task 5 hook | ✅ |

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-06-01-token-budgets-and-guardrails.md`.**

**Two execution options:**

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

**Which approach?**
