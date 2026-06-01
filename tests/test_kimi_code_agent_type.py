from repowire.config.models import AgentType


def test_kimi_code_agent_type() -> None:
    assert AgentType.KIMI_CODE.value == "kimi-code"
    assert AgentType("kimi-code") == AgentType.KIMI_CODE
