import agent.model_types as model_types
import agent.models as models


def test_agent_models_compatibility_exports_match_specific_modules() -> None:
    expected_names = [
        "AgentRunResult",
        "AgentState",
        "Message",
        "ModelTurn",
        "RunMetrics",
        "RuntimeConfig",
        "TokenUsage",
        "ToolCall",
        "ToolEnvelope",
        "ToolError",
        "ToolMetric",
        "ToolResult",
        "TurnMetric",
    ]

    assert models.__all__ == model_types.__all__ == expected_names
    for name in expected_names:
        assert getattr(models, name) is getattr(model_types, name)
