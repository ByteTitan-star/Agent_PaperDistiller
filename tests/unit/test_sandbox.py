"""SandboxManager — configuration and shell policy without Docker."""

from __future__ import annotations

from app.sandbox.manager import _DENIED_SHELL_PATTERNS, SandboxManager, SandboxSettings


def test_sandbox_not_configured_when_disabled() -> None:
    mgr = SandboxManager(SandboxSettings(enabled=False))
    assert not mgr.settings.is_configured()


def test_denied_shell_patterns_block_rm_rf_root() -> None:
    command = "rm -rf /"
    assert any(pattern.search(command) for pattern in _DENIED_SHELL_PATTERNS)
