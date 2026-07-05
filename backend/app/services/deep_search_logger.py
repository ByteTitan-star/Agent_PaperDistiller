"""Deep Search 详细日志器 — 记录 ReAct 搜索的完整过程。

每日一个日志文件：logs/deep_search_YYYY-MM-DD.log
每次搜索用 ==== 分隔，记录：
- 用户请求、论文上下文摘要
- LLM 每一轮的思考过程和输出
- 工具调用的参数和返回结果
- Token 用量汇总
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_backend_root = Path(__file__).resolve().parents[2]  # backend/
_log_dir = _backend_root / "logs"
_log_dir.mkdir(exist_ok=True)

# 专用 logger，不污染全局 logging 配置
_logger = logging.getLogger("deep_search_detail")
_logger.setLevel(logging.DEBUG)
_logger.propagate = False  # 不传给父 logger


class _DailyFileHandler(logging.FileHandler):
    """按日期切换的文件 handler，每天一个文件。"""

    def __init__(self, log_dir: Path) -> None:
        self._log_dir = log_dir
        self._current_date: str = ""
        super().__init__(self._today_path(), encoding="utf-8")

    def _today_path(self) -> Path:
        today = datetime.now().strftime("%Y-%m-%d")
        self._current_date = today
        return self._log_dir / f"deep_search_{today}.log"

    def emit(self, record: logging.LogRecord) -> None:
        today = datetime.now().strftime("%Y-%m-%d")
        if today != self._current_date:
            # 日期变了，切换文件
            self.close()
            self.baseFilename = str(self._today_path())
            self.stream = self.open()
        super().emit(record)


# 初始化 handler
_daily_handler = _DailyFileHandler(_log_dir)
_daily_handler.setFormatter(logging.Formatter("%(message)s"))
_logger.addHandler(_daily_handler)


# ---------------------------------------------------------------------------
# 公共 API
# ---------------------------------------------------------------------------


def log_separator() -> None:
    """写入分隔线。"""
    _logger.info("=" * 80)
    _logger.info("  %s  |  新的深度搜索请求", _now())
    _logger.info("=" * 80)


def log_request(question: str, paper_context: list[str], clarify_hint: str | None = None) -> None:
    """记录用户请求信息。"""
    _logger.info("")
    _logger.info("【用户请求】")
    _logger.info("  问题：%s", question)
    if clarify_hint:
        _logger.info("  澄清提示：%s", clarify_hint)
    _logger.info("  论文上下文片段数：%d", len(paper_context))
    for i, ctx in enumerate(paper_context[:3]):
        _logger.info("  上下文[%d]（前200字）：%s", i, ctx[:200])
    _logger.info("")


def log_research_plan(plan: dict | None, question: str) -> None:
    """记录 LLM 生成的研究计划。"""
    _logger.info("【研究计划生成】")
    if plan:
        _logger.info("  需求理解：%s", json.dumps(plan.get("understanding", []), ensure_ascii=False))
        _logger.info("  搜索计划：%s", json.dumps(plan.get("search_plan", []), ensure_ascii=False))
        _logger.info("  重点关注：%s", json.dumps(plan.get("focus_areas", []), ensure_ascii=False))
        _logger.info("  预估深度：%s", plan.get("estimated_depth", ""))
    else:
        _logger.info("  计划生成失败（将使用默认策略），问题：%s", question)
    _logger.info("")


def log_hitl_checkpoint(checkpoint: str, decision: str, edited_state: dict | None = None) -> None:
    """记录 HITL 检查点的用户决策。"""
    _logger.info("【HITL 检查点】")
    _logger.info("  检查点：%s", checkpoint)
    _logger.info("  用户决策：%s", decision)
    if edited_state:
        _logger.info("  用户修改：%s", json.dumps(edited_state, ensure_ascii=False))
    _logger.info("")


def log_llm_round(round_num: int, messages_snapshot: list[dict]) -> None:
    """记录 LLM 某一轮的输入 messages 摘要。"""
    _logger.info("【LLM 第 %d 轮调用】", round_num)
    _logger.info("  输入 messages 数：%d", len(messages_snapshot))
    for i, msg in enumerate(messages_snapshot[-6:]):  # 只展示最近 6 条
        role = msg.get("role", "?")
        content = str(msg.get("content", ""))[:300]
        tool_calls = msg.get("tool_calls")
        if tool_calls:
            tc_summary = ", ".join(
                f"{tc.get('function', {}).get('name', '?')}({tc.get('function', {}).get('arguments', '')[:80]})"
                for tc in tool_calls
            )
            _logger.info("    [%d] role=%s | tool_calls: %s", i, role, tc_summary)
        else:
            _logger.info("    [%d] role=%s | %s", i, role, content)
    _logger.info("")


def log_agent_message(msg: Any, round_num: int) -> None:
    """记录 agent 返回的每一条 message 的详细信息。"""
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

    _logger.info("  ── Message[%d] ──", round_num)

    if isinstance(msg, SystemMessage):
        _logger.info("  类型：System")
        _logger.info("  内容（前300字）：%s", str(msg.content)[:300])

    elif isinstance(msg, HumanMessage):
        _logger.info("  类型：Human")
        _logger.info("  内容（前300字）：%s", str(msg.content)[:300])

    elif isinstance(msg, AIMessage):
        # 思考过程（DeepSeek reasoning_content）
        reasoning = getattr(msg, "reasoning_content", None)
        if reasoning:
            _logger.info("  类型：AI（含思考过程）")
            _logger.info("  💭 思考过程：%s", str(reasoning)[:500])
        else:
            _logger.info("  类型：AI")

        # 文本输出
        content = str(msg.content) if msg.content else ""
        if content:
            _logger.info("  📝 输出（前500字）：%s", content[:500])

        # 工具调用
        if msg.tool_calls:
            for tc in msg.tool_calls:
                tool_name = tc.get("name", "unknown")
                args = tc.get("args", {})
                _logger.info("  🔧 调用工具：%s", tool_name)
                _logger.info("     参数：%s", json.dumps(args, ensure_ascii=False)[:300])

        # Token 用量
        meta = getattr(msg, "usage_metadata", None)
        if meta:
            inp = meta.get("input_tokens", 0) if isinstance(meta, dict) else getattr(meta, "input_tokens", 0)
            out = meta.get("output_tokens", 0) if isinstance(meta, dict) else getattr(meta, "output_tokens", 0)
            _logger.info("  📊 Token：input=%d, output=%d", inp, out)

    elif isinstance(msg, ToolMessage):
        _logger.info("  类型：Tool Response")
        content = str(msg.content)
        _logger.info("  📥 工具返回（前500字）：%s", content[:500])

    else:
        _logger.info("  类型：%s", type(msg).__name__)
        _logger.info("  内容（前200字）：%s", str(getattr(msg, "content", ""))[:200])

    _logger.info("")


def log_final_result(
    answer: str,
    thinking_chain: list[str],
    prompt_tokens: int,
    completion_tokens: int,
    sources: list[dict],
) -> None:
    """记录最终搜索结果摘要。"""
    _logger.info("【搜索完成】")
    _logger.info("  答案长度：%d 字符", len(answer))
    _logger.info("  答案（前500字）：%s", answer[:500])
    _logger.info("  思考链步骤数：%d", len(thinking_chain))
    for i, step in enumerate(thinking_chain):
        _logger.info("    步骤[%d]：%s", i, step[:200])
    _logger.info(
        "  Token 用量：prompt=%d, completion=%d, total=%d",
        prompt_tokens,
        completion_tokens,
        prompt_tokens + completion_tokens,
    )
    _logger.info("  来源数量：%d", len(sources))
    for s in sources:
        _logger.info("    - %s | %s", s.get("title", "?")[:60], s.get("url", ""))
    _logger.info("")
    _logger.info("=" * 80)
    _logger.info("  %s  |  深度搜索结束", _now())
    _logger.info("=" * 80)
    _logger.info("")
    _logger.info("")


def log_error(stage: str, error: Exception) -> None:
    """记录错误。"""
    _logger.error("【错误】阶段=%s | %s: %s", stage, type(error).__name__, error)
    _logger.error("")


def _now() -> str:
    """当前 UTC+8 时间字符串。"""
    from datetime import timedelta

    tz = timezone(timedelta(hours=8))
    return datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")
