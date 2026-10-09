"""表格提取升级链：矢量抽取质量门禁 -> 区域裁剪 -> 结构识别（VLM）-> Markdown。

生产级做法（见 todo.md Phase 13）：表格不逐页切换技术，而是逐元素置信度门控——
- 有框线表格：find_tables() 矢量抽取（快、无损），网格健全性检查通过即接受；
- 门禁失败（无框线/复杂合并格/图片型表格）：只把该区域升级到视觉模型，
  裁剪区域 -> VLM 转 Markdown 表格，绝不影响同页其余元素的文本层抽取；
- 表格节点带 source 标记（vector | model | vector_low_quality）供可观测。

所有识别器 recognize() 失败返回 None，绝不抛异常拖垮解析主流程。
"""

from __future__ import annotations

import asyncio
import base64
import logging
from typing import Any, Protocol

logger = logging.getLogger(__name__)

TABLE_PROMPT = """You are a table extraction engine. The image contains a table from an academic document.
Convert it into a GitHub-flavored Markdown table. Rules (MUST follow):
1. Output ONLY the Markdown table, starting with the header row and a |---| separator row.
2. Preserve every row and column; do not merge, drop, or invent cells.
3. Keep numbers, units and symbols exactly as they appear.
4. If a cell is empty, leave it blank between pipes.
5. Multi-line cell content should be joined with a space."""

# 质量门禁默认阈值
MIN_ROWS = 2
MIN_COLS = 2
MIN_FILL_RATIO = 0.35  # 非空单元格占比下限
MIN_COL_CONSISTENCY = 0.6  # 行列数一致率下限（乱网格判定）


def _cfg(settings: Any, name: str, default: Any) -> Any:
    value = getattr(settings, name, None)
    return default if value is None else value


def table_quality_report(
    rows: list[list[Any]] | None,
    *,
    min_rows: int = MIN_ROWS,
    min_cols: int = MIN_COLS,
    min_fill: float = MIN_FILL_RATIO,
    min_consistency: float = MIN_COL_CONSISTENCY,
) -> dict[str, Any]:
    """矢量表格网格健全性检查（纯函数，可单测）。

    判定垃圾抽取的信号：行列不足、单元格大面积空置、行列数参差
    （find_tables 对无框线表格常抽出一维碎片或错位网格）。
    返回 {sane, reason, rows, cols, fill_ratio, col_consistency}。
    """
    report: dict[str, Any] = {
        "sane": False,
        "reason": "",
        "rows": 0,
        "cols": 0,
        "fill_ratio": 0.0,
        "col_consistency": 0.0,
    }
    cleaned = [r for r in (rows or []) if r and any(c is not None and str(c).strip() for c in r)]
    if not cleaned:
        report["reason"] = "empty"
        return report

    widths = [len(r) for r in cleaned]
    ncols = max(widths)
    nrows = len(cleaned)
    report["rows"], report["cols"] = nrows, ncols

    total_cells = sum(widths)
    filled = sum(1 for r in cleaned for c in r if c is not None and str(c).strip())
    report["fill_ratio"] = round(filled / max(1, total_cells), 3)

    mode_width = max(set(widths), key=widths.count)
    report["col_consistency"] = round(widths.count(mode_width) / nrows, 3)

    if nrows < min_rows:
        report["reason"] = f"rows<{min_rows}"
    elif ncols < min_cols:
        report["reason"] = f"cols<{min_cols}"
    elif report["fill_ratio"] < min_fill:
        report["reason"] = f"fill<{min_fill}"
    elif report["col_consistency"] < min_consistency:
        report["reason"] = f"ragged_cols<{min_consistency}"
    else:
        report["sane"] = True
    return report


class TableStructureRecognizer(Protocol):
    """表格结构识别接口：区域裁剪 PNG -> Markdown 表格（失败返回 None）。"""

    name: str

    def recognize(self, png_bytes: bytes) -> str | None: ...


class NullTableRecognizer:
    """未启用时的空实现。"""

    name = "off"

    def recognize(self, png_bytes: bytes) -> str | None:
        return None


class VlmTableRecognizer:
    """VLM 表格识别（OpenAI 兼容多模态接口，复用 qwen key；同步接口，线程安全）。"""

    name = "vlm-table"

    def __init__(self, api_key: str, base_url: str, model: str, timeout: float = 60.0) -> None:
        self._api_key = api_key
        self._base_url = base_url
        self._model = model
        self._timeout = timeout

    async def _describe(self, png_bytes: bytes) -> str:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=self._api_key, base_url=self._base_url, timeout=self._timeout)
        data_uri = f"data:image/png;base64,{base64.b64encode(png_bytes).decode('ascii')}"
        response = await client.chat.completions.create(
            model=self._model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": TABLE_PROMPT},
                        {"type": "image_url", "image_url": {"url": data_uri}},
                    ],
                }
            ],
        )
        content = (response.choices[0].message.content or "").strip() if response.choices else ""
        # 只保留 Markdown 表格行（模型偶尔加说明文字）
        lines = [ln for ln in content.splitlines() if ln.strip().startswith("|")]
        return "\n".join(lines)

    def recognize(self, png_bytes: bytes) -> str | None:
        """同步入口：解析主流程在 worker 线程运行（无事件循环），asyncio.run 安全；
        若被事件循环线程同步调用，则转入独立线程执行，避免嵌套 run 报错。"""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self._describe(png_bytes)) or None

        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(lambda: asyncio.run(self._describe(png_bytes)))
            try:
                return future.result(timeout=self._timeout + 15.0) or None
            except concurrent.futures.TimeoutError:
                logger.warning("[表格识别] VLM 超时")
                return None


def get_table_recognizer(settings: Any | None = None) -> TableStructureRecognizer:
    """按配置构造表格识别器；table_recognition=off 或无 key 时为 Null（门禁仍生效，仅标记低质量）。"""
    backend = str(_cfg(settings, "table_recognition", "off") or "off").lower()
    if backend != "vlm":
        return NullTableRecognizer()
    api_key = str(_cfg(settings, "qwen_api_key", "") or "")
    if not api_key or api_key == "your-api-key":
        logger.info("[表格识别] table_recognition=vlm 但未配置 qwen_api_key，升级通道关闭")
        return NullTableRecognizer()
    return VlmTableRecognizer(
        api_key=api_key,
        base_url=str(_cfg(settings, "qwen_base_url", "https://dashscope.aliyuncs.com/compatible-mode/v1")),
        model=str(_cfg(settings, "vlm_model", "qwen-vl-max")),
        timeout=float(_cfg(settings, "vlm_timeout_sec", 60.0)),
    )


def bbox_iou(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    """两框 IoU（判断检测区域是否已被矢量表格覆盖，避免重复升级）。"""
    ix1, iy1 = max(a[0], b[0]), max(a[1], b[1])
    ix2, iy2 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    if inter <= 0:
        return 0.0
    area_a = (a[2] - a[0]) * (a[3] - a[1])
    area_b = (b[2] - b[0]) * (b[3] - b[1])
    return inter / max(1e-6, area_a + area_b - inter)


__all__ = [
    "NullTableRecognizer",
    "TableStructureRecognizer",
    "VlmTableRecognizer",
    "bbox_iou",
    "get_table_recognizer",
    "table_quality_report",
]
