"""Tool abstract base and per-invocation context."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field

from ..agent.schemas import AuthContext, EventType, StreamEvent, ToolResult

INTENT_SUMMARY_KEY = "_intent_summary"

ProgressEmitter = Callable[[dict[str, Any]], Awaitable[None]]
EventEmitter = Callable[[EventType, dict[str, Any]], Awaitable[None]]


class ToolContext(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    session_id: str
    auth: AuthContext
    call_id: str | None = None
    paper_id: str | None = None
    progress_emitter: ProgressEmitter | None = None
    event_emitter: EventEmitter | None = None
    runtime: Any | None = Field(default=None, description="RuntimeBundle reference for sub-agent spawn")


class Tool(ABC):
    name: ClassVar[str]
    description: ClassVar[str]
    parameters: ClassVar[dict[str, Any]]
    concurrency_safe: ClassVar[bool] = False
    deduplicate_repeated_calls: ClassVar[bool] = False

    @abstractmethod
    async def execute(self, args: dict[str, Any], context: ToolContext) -> ToolResult: ...

    async def stream(
        self,
        args: dict[str, Any],
        context: ToolContext,
    ) -> AsyncIterator[StreamEvent]:
        result = await self.execute(args, context)
        yield StreamEvent(
            event_type=EventType.TOOL_RESULT,
            session_id=context.session_id,
            data={"name": self.name, "result": result.content, "status": result.status.value},
        )
