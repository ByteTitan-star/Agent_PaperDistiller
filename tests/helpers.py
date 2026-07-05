"""Shared test doubles for agent runtime tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from app.agent.models import LLMResponse
from app.agent.schemas import ToolResult, ToolResultStatus
from app.tools.base import Tool, ToolContext


class EchoTool(Tool):
    name: ClassVar[str] = "echo"
    description: ClassVar[str] = "Echo back a message (test helper)."
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {"message": {"type": "string"}},
        "required": ["message"],
    }
    concurrency_safe: ClassVar[bool] = True

    async def execute(self, args: dict[str, Any], context: ToolContext) -> ToolResult:
        return ToolResult(ToolResultStatus.SUCCESS, args.get("message", ""))


class MockLLMGateway:
    """Scripted LLM responses for AgentLoop integration tests."""

    def __init__(self, script: list[LLMResponse]) -> None:
        self._script = list(script)
        self.calls: list[list[dict[str, Any]]] = []

    async def chat_completion(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        self.calls.append(messages)
        if not self._script:
            return LLMResponse(content="fallback answer")
        return self._script.pop(0)


class MockSkillRegistry:
    def __init__(self, responses: dict[str, dict[str, Any]] | None = None) -> None:
        self.responses = responses or {}
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def execute(self, name: str, args: dict[str, Any], context: dict[str, Any] | None = None) -> dict[str, Any]:
        self.calls.append((name, args))
        if name in self.responses:
            return self.responses[name]
        return {"error": f"unknown skill: {name}"}


class MockStorage:
    """Minimal Storage stand-in for pipeline step tests."""

    def __init__(self, base_dir: Path, *, pdf_text: str = "Sample paper body.") -> None:
        self.base_dir = base_dir
        self.pdf_text = pdf_text
        self.chunks: dict[str, list[str]] = {}
        self.results: dict[str, dict[str, str]] = {}
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def pdf_path(self, paper_id: str) -> Path:
        path = self.base_dir / paper_id / "paper.pdf"
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text(self.pdf_text, encoding="utf-8")
        return path

    def save_chunks(self, paper_id: str, chunks: list[str]) -> None:
        self.chunks[paper_id] = chunks

    def load_chunks(self, paper_id: str) -> list[str]:
        return self.chunks.get(paper_id, [])

    def write_result(self, paper_id: str, kind: str, content: str, template_name: str = "") -> None:
        self.results.setdefault(paper_id, {})[kind] = content

    def read_template(self, template_name: str) -> str:
        return "# Template\n\n- **Title**: {{title}}\n"

    def paper_output_dir(self, paper_id: str) -> Path:
        path = self.base_dir / paper_id / "output"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _upload_to_oss(self, *_args: Any, **_kwargs: Any) -> None:
        return None


class MockAgentFactory:
    def create_deepseek(self) -> Any:
        return object()

    def create_qwen(self) -> Any:
        return object()

    def create_tot(self) -> Any:
        return object()
