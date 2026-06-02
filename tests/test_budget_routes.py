import pytest

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


async def _register_peer(client, name, path="/tmp/test", backend="kimi-code"):
    r = await client.post("/peers", json={
        "name": name, "path": path, "backend": backend,
    })
    assert r.status_code == 200
    return r.json()["peer_id"]


@pytest.mark.asyncio
async def test_post_usage(env):
    client, harness = env
    peer_id = await _register_peer(client, "alice")
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
    peer_id = await _register_peer(client, "bob")
    store = harness.app.state.token_budget_store
    store.get_or_create(peer_id, agent_type="kimi-code")
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
    peer_id = await _register_peer(client, "carol")
    store = harness.app.state.token_budget_store
    store.get_or_create(peer_id, agent_type="kimi-code")
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
