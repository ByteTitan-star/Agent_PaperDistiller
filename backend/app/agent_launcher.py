#!/usr/bin/env python3
"""Standalone agent worker entrypoint.

Usage:
    uv run python -m app.agent_launcher worker
"""

from __future__ import annotations

import asyncio
import logging
import sys

logging.basicConfig(level=logging.INFO)


async def _main() -> None:
    role = sys.argv[1] if len(sys.argv) > 1 else "worker"
    if role != "worker":
        print(f"Unknown role: {role}")
        sys.exit(1)

    from app.config import get_settings
    from app.dependencies import get_app_harness

    settings = get_settings()
    settings.agent_service_role = "worker"
    harness = get_app_harness()
    await harness.startup()

    from app.agent.worker import AgentWorker

    worker = AgentWorker()
    await worker.run_forever()


if __name__ == "__main__":
    asyncio.run(_main())
