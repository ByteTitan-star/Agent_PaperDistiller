"""eval 测试子目录的 conftest：注册 --run-eval 开关，默认跳过会消耗 LLM 的端到端用例。"""

from __future__ import annotations

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--run-eval",
        action="store_true",
        default=False,
        help="运行 RAGAS RAG 评估端到端用例（会调用 DeepSeek API，产生费用）",
    )


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "eval: RAGAS RAG evaluation (LLM cost)")


@pytest.fixture
def run_eval_opt(request: pytest.FixtureRequest) -> bool:
    return bool(request.config.getoption("--run-eval"))
