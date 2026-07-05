"""WorkingSet message assembly for OpenAI API."""

from __future__ import annotations

from app.agent.context import WorkingSet
from app.agent.schemas import ToolCall


def test_to_openai_messages_with_tool_roundtrip() -> None:
    ws = WorkingSet(system_prompt="sys")
    ws.append_user("question")
    ws.append_assistant("", tool_calls=[ToolCall(id="1", name="echo", arguments={"message": "hi"})])
    ws.append_tool_result("1", "echo", "hi")

    messages = ws.to_openai_messages()
    assert messages[0] == {"role": "system", "content": "sys"}
    assert messages[1] == {"role": "user", "content": "question"}
    assert messages[2]["role"] == "assistant"
    assert messages[2]["tool_calls"][0]["function"]["name"] == "echo"
    assert messages[3] == {"role": "tool", "tool_call_id": "1", "content": "hi"}
