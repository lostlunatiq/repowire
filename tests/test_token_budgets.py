
from repowire.daemon.state.database import StateDatabase
from repowire.daemon.state.token_budgets import SQLiteTokenBudgetStore


def test_token_budget_store_create_and_get(tmp_path):
    db = StateDatabase(tmp_path / "state.db")
    try:
        store = SQLiteTokenBudgetStore(db)
        budget = store.get_or_create("budget-1", agent_type="claude")
        assert budget.budget_id == "budget-1"
        assert budget.agent_type == "claude"
        assert budget.cumulative_input_tokens == 0
        assert budget.cumulative_output_tokens == 0
        assert budget.budget_ceiling == 100000
        assert budget.warning_sent == 0
        assert budget.parent_peer_id is None

        fetched = store.get("budget-1")
        assert fetched is not None
        assert fetched.budget_id == "budget-1"

        # get_or_create is idempotent
        again = store.get_or_create("budget-1", agent_type="codex")
        assert again.agent_type == "claude"  # original value preserved
    finally:
        db.close()


def test_token_budget_store_record_usage(tmp_path):
    db = StateDatabase(tmp_path / "state.db")
    try:
        store = SQLiteTokenBudgetStore(db)
        store.get_or_create("budget-1", agent_type="claude")
        updated = store.record_usage("budget-1", input_tokens=50, output_tokens=25)
        assert updated is not None
        assert updated.cumulative_input_tokens == 50
        assert updated.cumulative_output_tokens == 25

        updated2 = store.record_usage("budget-1", input_tokens=10, output_tokens=5)
        assert updated2 is not None
        assert updated2.cumulative_input_tokens == 60
        assert updated2.cumulative_output_tokens == 30

        assert store.record_usage("missing", input_tokens=1) is None
    finally:
        db.close()


def test_token_budget_store_check_budget_pass(tmp_path):
    db = StateDatabase(tmp_path / "state.db")
    try:
        store = SQLiteTokenBudgetStore(db)
        store.get_or_create("budget-1", agent_type="claude")
        store.record_usage("budget-1", input_tokens=1000, output_tokens=500)
        decision = store.check_budget("budget-1", estimated_input=1000)
        assert decision.proceed is True
        assert decision.remaining == 100000 - 1500
        assert decision.reason is None
    finally:
        db.close()


def test_token_budget_store_check_budget_fail(tmp_path):
    db = StateDatabase(tmp_path / "state.db")
    try:
        store = SQLiteTokenBudgetStore(db)
        store.get_or_create("budget-1", agent_type="claude")
        store.record_usage("budget-1", input_tokens=99000, output_tokens=0)
        decision = store.check_budget("budget-1", estimated_input=2000)
        assert decision.proceed is False
        assert decision.reason == "budget_exhausted"
        assert decision.used == 99000
        assert decision.ceiling == 100000
    finally:
        db.close()


def test_token_budget_store_warning_at_80_percent(tmp_path):
    db = StateDatabase(tmp_path / "state.db")
    try:
        store = SQLiteTokenBudgetStore(db)
        store.get_or_create("budget-1", agent_type="claude")
        # 79% usage — no warning
        store.record_usage("budget-1", input_tokens=79000, output_tokens=0)
        assert store.maybe_warn("budget-1") is False

        # cross 80% — warning fires
        store.record_usage("budget-1", input_tokens=1000, output_tokens=1000)
        assert store.maybe_warn("budget-1") is True

        # second call — already warned
        assert store.maybe_warn("budget-1") is False

        # missing budget — no warning
        assert store.maybe_warn("missing") is False
    finally:
        db.close()


def test_token_budget_store_subagent_lifecycle(tmp_path):
    db = StateDatabase(tmp_path / "state.db")
    try:
        store = SQLiteTokenBudgetStore(db)
        # created
        budget = store.upsert_subagent(
            parent_peer_id="peer-a",
            subagent_id="sub-1",
            agent_type="claude",
            event="created",
        )
        assert budget is not None
        assert budget.budget_id == "peer-a/sub-1"
        assert budget.parent_peer_id == "peer-a"
        assert budget.agent_type == "claude"

        # usage_update
        updated = store.upsert_subagent(
            parent_peer_id="peer-a",
            subagent_id="sub-1",
            agent_type="claude",
            event="usage_update",
            input_tokens=100,
            output_tokens=50,
        )
        assert updated is not None
        assert updated.cumulative_input_tokens == 100
        assert updated.cumulative_output_tokens == 50

        # destroyed
        deleted = store.upsert_subagent(
            parent_peer_id="peer-a",
            subagent_id="sub-1",
            agent_type="claude",
            event="destroyed",
        )
        assert deleted is None
        assert store.get("peer-a/sub-1") is None

        # unknown event falls back to get
        assert store.upsert_subagent(
            parent_peer_id="peer-a",
            subagent_id="sub-1",
            agent_type="claude",
            event="unknown",
        ) is None
    finally:
        db.close()
