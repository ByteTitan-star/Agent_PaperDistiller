"""追踪器（Tracer）— 为流水线每一步创建追踪跨度（TraceSpan）。

维护一份进程内的平铺 span 列表，并在启用 OpenTelemetry（settings.otel_enabled）时，
把每个 span 同步桥接到 OTel span，从而可导出到自托管后端（Jaeger/Tempo / console）。

记录每个步骤的：
- 名称和层级关系
- 开始/结束时间
- 执行状态（pending / ok / error）
- 附带元数据
"""

from __future__ import annotations

import logging
from typing import Any

from .._types import TraceSpan

logger = logging.getLogger(__name__)

# 缓存：是否启用 OTel，以及 OTel tracer（None=未启用/未安装 opentelemetry）。
_otel_tracer: Any = None
_otel_checked: bool = False


def _get_otel_tracer() -> Any:
    """惰性获取 OTel tracer。未启用或未安装 opentelemetry 时返回 None。"""
    global _otel_tracer, _otel_checked
    if _otel_checked:
        return _otel_tracer
    _otel_checked = True
    try:
        from ..config import get_harness_settings
        if not get_harness_settings().otel_enabled:
            return None
        from opentelemetry import trace as _trace
        _otel_tracer = _trace.get_tracer("paper-distiller.harness")
    except Exception as exc:
        logger.warning("OTel tracer unavailable (otel_enabled but import failed): %s", exc)
        _otel_tracer = None
    return _otel_tracer


class Tracer:
    """创建和管理流水线步骤的追踪跨度。

    使用方式：
        tracer = Tracer()
        tracer.start_span("langgraph_pipeline")     # 开始追踪
        # ... 执行流水线 ...
        tracer.end_span("ok", {"tags": [...]})       # 结束追踪

    启用 OTel 时，start/end_span 会同步开/关同名 OTel span（嵌套通过栈维护）。
    """

    def __init__(self) -> None:
        self._spans: list[TraceSpan] = []
        self._active: TraceSpan | None = None
        self._otel = _get_otel_tracer()
        # OTel span 上下文管理器栈：(cm, span_obj)
        self._otel_stack: list[tuple[Any, Any]] = []

    def start_span(self, step_name: str, parent_id: str | None = None) -> TraceSpan:
        """开始一个新的追踪跨度（若启用 OTel，同步开启同名 OTel span）。"""
        span = TraceSpan(
            span_id=f"{step_name}-{len(self._spans)}",
            parent_id=parent_id,
            step_name=step_name,
            start_time=_now_iso(),
            status="pending",
        )
        self._spans.append(span)
        self._active = span

        if self._otel is not None:
            try:
                cm = self._otel.start_as_current_span(step_name)
                span_obj = cm.__enter__()
                self._otel_stack.append((cm, span_obj))
            except Exception:
                logger.warning("OTel start_span failed for %s", step_name, exc_info=True)
        return span

    def end_span(self, status: str = "ok", metadata: dict[str, Any] | None = None) -> TraceSpan | None:
        """结束当前活跃跨度（若启用 OTel，同步关闭对应 OTel span 并写入状态/属性）。"""
        if self._active is None:
            return None
        self._active.end_time = _now_iso()
        self._active.status = status
        if metadata:
            self._active.metadata.update(metadata)
        span = self._active
        self._active = None

        if self._otel_stack:
            try:
                cm, span_obj = self._otel_stack.pop()
                if metadata:
                    for k, v in metadata.items():
                        try:
                            span_obj.set_attribute(str(k), str(v))
                        except Exception:
                            pass
                span_obj.set_attribute("status", status)
                from opentelemetry import trace as _trace
                span_obj.set_status(
                    _trace.Status(_trace.StatusCode.ERROR if status == "error" else _trace.StatusCode.OK)
                )
                cm.__exit__(None, None, None)
            except Exception:
                logger.warning("OTel end_span failed", exc_info=True)
        return span

    @property
    def spans(self) -> list[TraceSpan]:
        """获取所有已创建的追踪跨度（副本）。"""
        return list(self._spans)

    def to_dict_list(self) -> list[dict[str, Any]]:
        """将所有追踪跨度序列化为字典列表。

        用于 JSON 输出、日志记录或前端展示。

        Returns:
            list[dict]: 每个跨度的字典表示，包含所有字段。
        """
        return [
            {
                "span_id": s.span_id,
                "parent_id": s.parent_id,
                "step_name": s.step_name,
                "start_time": s.start_time,
                "end_time": s.end_time,
                "status": s.status,
                "metadata": s.metadata,
            }
            for s in self._spans
        ]


def _now_iso() -> str:
    """获取当前 UTC 时间的 ISO 格式字符串。"""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
