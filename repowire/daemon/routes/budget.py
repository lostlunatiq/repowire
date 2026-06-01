from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from repowire.daemon.auth import require_auth
from repowire.daemon.deps import get_app_state, get_peer_registry

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


async def _resolve_budget_id(identifier: str) -> tuple[str, str]:
    """Resolve identifier to (budget_id, agent_type). Falls back to identifier itself."""
    peer_registry = get_peer_registry()
    peer = await peer_registry.get_peer(identifier)
    if peer is not None:
        return peer.peer_id, peer.backend.value
    return identifier, "unknown"


@router.post("/peers/{identifier}/usage", response_model=BudgetStatusResponse)
async def record_usage(
    identifier: str,
    request: RecordUsageRequest,
    _: str | None = Depends(require_auth),
) -> BudgetStatusResponse:
    """Record token usage for a peer and return updated budget status."""
    budget_id, agent_type = await _resolve_budget_id(identifier)
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
    budget_id, _agent_type = await _resolve_budget_id(identifier)
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
    budget_id, _agent_type = await _resolve_budget_id(request.identifier)
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
