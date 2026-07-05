"""Docker-backed sandbox — isolated code and shell execution."""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

_DENIED_SHELL_PATTERNS = (
    re.compile(r"(^|\s)rm\s+-[^;&|]*r[^;&|]*f\s+/(?:\s|$)"),
    re.compile(r"(^|\s)sudo(?:\s|$)"),
    re.compile(r"(^|\s)nohup(?:\s|$)"),
    re.compile(r"(^|\s)(?:sleep)\s+(?:[3-9]\d{2,}|\d{4,})(?:\s|$)"),
)


class SandboxConfigurationError(Exception):
    """Sandbox is disabled or misconfigured."""


class SandboxManagerError(Exception):
    """Sandbox lifecycle or execution failure."""


@dataclass(frozen=True, slots=True)
class SessionMount:
    host_path: str
    container_path: str
    read_only: bool = False


@dataclass(frozen=True, slots=True)
class SandboxInfo:
    session_id: str
    container_id: str
    workspace_host: str
    workspace_container: str = "/workspace"


@dataclass(frozen=True, slots=True)
class CommandResult:
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool = False


@dataclass
class SandboxSettings:
    enabled: bool = False
    docker_image: str = "python:3.12-slim"
    timeout_sec: float = 120.0
    workspace_root: str = "data/sandbox"
    memory_limit: str = "512m"
    cpu_quota: int = 100_000
    network_enabled: bool = False
    default_working_dir: str = "/workspace"

    def is_configured(self) -> bool:
        return self.enabled and bool(shutil.which("docker"))


class SandboxManager:
    """One Docker container per agent session for governed execution."""

    def __init__(self, settings: SandboxSettings) -> None:
        self.settings = settings
        self._sessions: dict[str, SandboxInfo] = {}
        self._lock = asyncio.Lock()

    @property
    def default_working_dir(self) -> str:
        return self.settings.default_working_dir

    def _require_configured(self) -> None:
        if not self.settings.is_configured():
            raise SandboxConfigurationError(
                "Sandbox is not configured. Set SANDBOX_ENABLED=true and ensure Docker is available."
            )

    def _session_workspace(self, session_id: str) -> Path:
        root = Path(self.settings.workspace_root)
        safe = re.sub(r"[^a-zA-Z0-9_-]", "_", session_id)
        path = root / safe
        path.mkdir(parents=True, exist_ok=True)
        return path

    async def ensure_sandbox(
        self,
        session_id: str,
        *,
        session_mounts: list[SessionMount] | None = None,
    ) -> SandboxInfo:
        self._require_configured()
        async with self._lock:
            existing = self._sessions.get(session_id)
            if existing:
                return existing

            host_ws = self._session_workspace(session_id)
            container_name = f"pd-sandbox-{uuid.uuid4().hex[:12]}"
            mounts = [f"{host_ws.resolve()}:{self.settings.default_working_dir}"]
            for m in session_mounts or []:
                ro = ":ro" if m.read_only else ""
                mounts.append(f"{Path(m.host_path).resolve()}:{m.container_path}{ro}")

            network_flag = [] if self.settings.network_enabled else ["--network", "none"]
            cmd = [
                "docker",
                "run",
                "-d",
                "--name",
                container_name,
                "--memory",
                self.settings.memory_limit,
                "--cpu-quota",
                str(self.settings.cpu_quota),
                *network_flag,
            ]
            for mount in mounts:
                cmd.extend(["-v", mount])
            cmd.extend([self.settings.docker_image, "sleep", "infinity"])

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode != 0:
                raise SandboxManagerError(f"Failed to start sandbox: {stderr.decode() or stdout.decode()}")
            container_id = stdout.decode().strip()[:12]
            info = SandboxInfo(
                session_id=session_id,
                container_id=container_id,
                workspace_host=str(host_ws.resolve()),
            )
            self._sessions[session_id] = info
            logger.info("Sandbox started session=%s container=%s", session_id, container_id)
            return info

    async def run_command(
        self,
        session_id: str,
        command: str,
        *,
        timeout_s: float | None = None,
        working_directory: str | None = None,
    ) -> CommandResult:
        self._require_configured()
        for pattern in _DENIED_SHELL_PATTERNS:
            if pattern.search(command):
                return CommandResult(exit_code=126, stdout="", stderr="Command denied by policy")

        info = await self.ensure_sandbox(session_id)
        workdir = working_directory or self.settings.default_working_dir
        timeout = timeout_s or self.settings.timeout_sec

        exec_cmd = [
            "docker",
            "exec",
            "-w",
            workdir,
            info.container_id,
            "/bin/sh",
            "-lc",
            command,
        ]
        try:
            proc = await asyncio.wait_for(
                asyncio.create_subprocess_exec(
                    *exec_cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                ),
                timeout=timeout,
            )
            stdout_b, stderr_b = await proc.communicate()
            return CommandResult(
                exit_code=proc.returncode or 0,
                stdout=stdout_b.decode(errors="replace")[:5000],
                stderr=stderr_b.decode(errors="replace")[:2000],
            )
        except TimeoutError:
            return CommandResult(exit_code=124, stdout="", stderr="Command timed out", timed_out=True)

    async def write_file(self, session_id: str, rel_path: str, content: str) -> str:
        info = await self.ensure_sandbox(session_id)
        target = Path(info.workspace_host) / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return str(target)

    async def run_python_file(self, session_id: str, rel_path: str, *, timeout_s: float | None = None) -> CommandResult:
        workdir = self.settings.default_working_dir
        return await self.run_command(
            session_id,
            f"python {rel_path}",
            timeout_s=timeout_s,
            working_directory=workdir,
        )

    async def destroy_sandbox(self, session_id: str) -> None:
        async with self._lock:
            info = self._sessions.pop(session_id, None)
        if not info:
            return
        for cmd in (["docker", "rm", "-f", info.container_id],):
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.communicate()
        logger.info("Sandbox destroyed session=%s", session_id)

    async def close_session(self, session_id: str) -> None:
        await self.destroy_sandbox(session_id)
