"""Typed agent runtime errors."""

from __future__ import annotations


class AgentRuntimeError(Exception):
    """Base class for agent runtime failures."""


class MaxIterationsError(AgentRuntimeError):
    """ReAct loop exceeded the configured iteration budget."""


class SessionCancelledError(AgentRuntimeError):
    """User or operator cancelled the session mid-turn."""


class LLMGatewayError(AgentRuntimeError):
    """Upstream LLM provider failed."""


class LLMToolCallParseError(AgentRuntimeError):
    """Model returned malformed tool-call arguments."""


class ToolNotFoundError(AgentRuntimeError):
    """Requested tool is not registered."""


class ToolArgumentError(AgentRuntimeError):
    """Tool arguments failed schema validation."""


class ToolExecutionError(AgentRuntimeError):
    """Tool raised during execution."""


class ToolRepeatedCallError(AgentRuntimeError):
    """Identical tool call repeated beyond throttle threshold."""


class SandboxNotConfiguredError(AgentRuntimeError):
    """Sandbox tools requested but sandbox is disabled."""


class SubAgentError(AgentRuntimeError):
    """Sub-agent spawn or collection failed."""
