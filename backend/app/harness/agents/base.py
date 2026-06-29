"""Agent 基类 — 所有 Agent 的模板方法（Template Method）生命周期管理。

本模块定义了 BaseAgent 抽象基类，为所有 Agent 提供统一的：
- 生命周期钩子：on_init → on_pre_run → _do_run → on_post_run（异常走 on_error）
- 事件广播：每个生命周期阶段自动向 EventBus 发射事件
- 错误兜底：异常时自动返回包含错误信息的 AgentResult

子类只需实现 _do_run() 方法编写具体业务逻辑即可。
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from datetime import datetime, timezone

from .._types import AgentResult, AgentRole, HarnessEvent, LifecyclePhase, TokenUsage
from ..config import HarnessSettings
from ..events import EventBus


class BaseAgent(ABC):
    """所有 harness 托管 Agent 的抽象基类。

    使用模板方法模式（Template Method Pattern）：
    - 基类定义了 execute() 的执行骨架（固定的生命周期流程）
    - 子类通过 _do_run() 填充具体业务逻辑
    - 子类可通过重写 on_init / on_pre_run / on_post_run / on_error 自定义行为

    生命周期执行顺序：
        on_init() → on_pre_run() → _do_run() → on_post_run()
    任何阶段抛异常 → on_error()，并返回 AgentResult(error=...)

    Attributes:
        name: Agent 名称标识，如 "deepseek-chat" / "qwen-plus"。
        role: Agent 角色（AgentRole 枚举），如 GENERATOR / EVALUATOR / CRITIC。
        event_bus: 进程内事件总线，用于发射生命周期事件。
        settings: Harness 框架配置。
    """

    name: str
    role: AgentRole
    event_bus: EventBus
    settings: HarnessSettings

    def __init__(
        self,
        name: str,          # Agent 名称标识
        role: AgentRole,     # Agent 角色（枚举）
        event_bus: EventBus, # 事件总线
        settings: HarnessSettings,  # 框架配置
    ) -> None:
        self.name = name
        self.role = role
        self.event_bus = event_bus
        self.settings = settings

    async def execute(self, prompt: str, **kwargs: object) -> AgentResult:
        """模板方法 — 执行 Agent 的完整生命周期。

        流程：
        1. on_init() — 初始化钩子，发射 "init" 事件
        2. on_pre_run() — 预处理钩子（可修改 prompt），发射 "pre_run" 事件
        3. _do_run() — 子类实现的实际业务逻辑
        4. on_post_run() — 后处理钩子，发射 "post_run" 事件
        5. 任何阶段异常 → on_error()，发射 "error" 事件，返回错误结果

        Args:
            prompt: 输入提示词（用户问题或上游输出）。
            **kwargs: 附加参数，传递给 on_pre_run 和 _do_run，
                常见的有：system_prompt、temperature、max_tokens 等。

        Returns:
            AgentResult: 包含 content（输出）、token_usage（用量）、error（错误）的结果对象。
        """
        # 阶段 1：初始化
        self.on_init()
        self.event_bus.emit(
            HarnessEvent(layer="agent", component=self.name, action="init"),
        )
        try:
            # 阶段 2：预处理（可修改 prompt）
            prepared = self.on_pre_run(prompt, **kwargs)
            self.event_bus.emit(
                HarnessEvent(layer="agent", component=self.name, action="pre_run"),
            )
            # 阶段 3：执行具体逻辑（由子类实现，带瞬态错误重试）
            result = await self._run_with_retry(prepared, **kwargs)
            # 阶段 4：后处理（集中记录 token 用量）
            self.on_post_run(result, **kwargs)
            self.event_bus.emit(
                HarnessEvent(layer="agent", component=self.name, action="post_run",
                            payload={"has_error": result.error is not None}),
            )
            return result
        except Exception as exc:
            # 异常处理：调用错误钩子，返回包含错误信息的 AgentResult
            self.on_error(exc)
            self.event_bus.emit(
                HarnessEvent(layer="agent", component=self.name, action="error",
                            payload={"error": str(exc)}),
            )
            return AgentResult(error=str(exc))

    @abstractmethod
    async def _do_run(self, prompt: str, **kwargs: object) -> AgentResult:
        """子类必须实现的抽象方法 — Agent 的实际业务逻辑。

        这是模板方法模式中的"变化点"，每个 Agent 在这里写自己具体的处理逻辑。
        例如：调用 LLM API、调用工具、协调多个子 Agent 等。

        Args:
            prompt: 经过 on_pre_run 预处理后的提示词。
            **kwargs: 附加参数（system_prompt、temperature、max_tokens 等）。

        Returns:
            AgentResult: Agent 执行结果。
        """
        ...

    # ---- 健壮性：瞬态错误重试 + 资源关闭 ----

    @staticmethod
    def _transient_exceptions() -> tuple[type[Exception], ...]:
        """返回应触发重试的瞬态异常类型（OpenAI 限流/超时/连接错误）。

        openai 未安装或版本不含这些类型时返回空 tuple（此时退化为不重试）。
        """
        try:
            from openai import APIConnectionError, APITimeoutError, RateLimitError
            return (RateLimitError, APITimeoutError, APIConnectionError)
        except Exception:
            return ()

    async def _run_with_retry(self, prompt: str, **kwargs: object) -> AgentResult:
        """带瞬态错误重试地执行 _do_run（消费 settings.agent_retry_count / agent_retry_delay）。

        - retry_count <= 1 或 tenacity 未安装：直接执行一次。
        - 否则用 tenacity.AsyncRetrying，仅对 _transient_exceptions() 重试，
          鉴权/参数等错误不重试；耗尽后 reraise（由 execute 的 except 兜底转 AgentResult）。
        """
        retry_count = max(1, int(getattr(self.settings, "agent_retry_count", 1)))
        if retry_count <= 1:
            return await self._do_run(prompt, **kwargs)
        try:
            from tenacity import (
                AsyncRetrying,
                retry_if_exception_type,
                stop_after_attempt,
                wait_fixed,
            )
        except ImportError:
            return await self._do_run(prompt, **kwargs)

        transient = self._transient_exceptions()
        if not transient:
            return await self._do_run(prompt, **kwargs)

        retry_delay = float(getattr(self.settings, "agent_retry_delay", 1.0))
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(retry_count),
            wait=wait_fixed(retry_delay),
            retry=retry_if_exception_type(transient),
            reraise=True,
        ):
            with attempt:
                return await self._do_run(prompt, **kwargs)
        # 理论不可达（AsyncRetrying 会 reraise 或 return）
        return await self._do_run(prompt, **kwargs)

    async def aclose(self) -> None:
        """释放底层资源（如 OpenAI HTTP 连接池）。默认无操作；子类按需重写。

        在 AppHarness.shutdown / 任务结束时调用，避免连接泄漏。
        """
        client = getattr(self, "_client", None)
        if client is None:
            return
        try:
            close = getattr(client, "close", None)
            if close is None:
                return
            res = close()
            if hasattr(res, "__await__"):
                await res
        except Exception:
            pass

    # ---- 可重写的生命周期钩子 ----

    def on_init(self) -> None:
        """初始化钩子。Agent 开始执行前调用，可用于重置内部状态。"""
        pass

    def on_pre_run(self, prompt: str, **kwargs: object) -> str:
        """预处理钩子。在 _do_run 之前调用，可用于修改 prompt。

        Args:
            prompt: 原始输入提示词。
            **kwargs: 附加参数。

        Returns:
            str: 处理后的提示词（默认直接返回原始 prompt）。
        """
        return prompt

    def on_post_run(self, result: AgentResult, **kwargs: object) -> None:
        """后处理钩子。在 _do_run 之后调用。

        默认实现：集中记录 token 用量（单条路径，带 user_id / action_type）。
        所有 leaf agent（DeepSeek/Qwen）的 LLM 调用都在这里统一落库，
        避免每个调用点重复记账或漏记。

        Args:
            result: _do_run 的执行结果。
            **kwargs: execute 透传的附加参数；可包含 user_id（int）、action_type（str）。
        """
        usage = result.token_usage
        if usage is None:
            return
        try:
            from ...pipeline.common_utils import log_token_usage
            user_id = kwargs.get("user_id")
            action_type = kwargs.get("action_type", "agent")
            log_token_usage(
                project_name=self.settings.app_name,
                model_name=usage.model_name,
                prompt_tokens=usage.prompt_tokens,
                completion_tokens=usage.completion_tokens,
                user_id=user_id if isinstance(user_id, int) else None,
                action_type=str(action_type),
            )
        except Exception:
            # token 记账失败不影响主流程
            pass

    def on_error(self, error: Exception) -> None:
        """错误处理钩子。执行过程中抛异常时调用。

        Args:
            error: 捕获到的异常对象。
        """
        pass
