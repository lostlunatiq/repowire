from __future__ import annotations

from dataclasses import dataclass

from repowire.daemon.state.database import StateDatabase
from repowire.daemon.work_store import now_iso


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

    def get_or_create(
        self, budget_id: str, *, agent_type: str, parent_peer_id: str | None = None
    ) -> TokenBudget:
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
        budget = self.get(budget_id)
        assert budget is not None
        return budget

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
            return self.get_or_create(
                budget_id, agent_type=agent_type, parent_peer_id=parent_peer_id
            )
        if event == "destroyed":
            with self._conn:
                self._conn.execute(
                    "DELETE FROM token_budgets WHERE budget_id = ?",
                    (budget_id,),
                )
            return None
        if event == "usage_update":
            self.get_or_create(budget_id, agent_type=agent_type, parent_peer_id=parent_peer_id)
            return self.record_usage(
                budget_id, input_tokens=input_tokens, output_tokens=output_tokens
            )
        return self.get(budget_id)
