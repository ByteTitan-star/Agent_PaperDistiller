"""VLM 图表描述：figure 节点裁剪 -> 多模态模型描述 -> 图注绑定入检索库。

对应 docs/system-improvement-roadmap.md 改进 2：
- Step A：图注已在解析阶段绑定（figure 节点 caption 字段）；
- Step B：裁剪图片 + 图注一同发给 VLM，Prompt 约束输出图表类型/坐标轴/关键数据/图例/结论；
- 描述文本作为 element_type="image_desc" 的块入库，RAG 与图表证据技能即可命中。

默认关闭（vlm_enabled=False），复用 qwen_api_key / qwen_base_url；无 key 或调用失败
一律优雅降级（描述为空，不影响主管线）。
"""

from __future__ import annotations

import asyncio
import base64
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DESCRIBE_PROMPT = """你是一位学术数据分析专家。这张图片来自一篇学术论文，图片标题（图注）是：{caption}。
请结合标题用中文详细描述图表内容，必须包含：
1. 图表类型（折线/柱状/散点/流程图/架构图等）
2. 坐标轴含义、单位、取值范围（如适用）
3. 关键数据点（最大值、最小值、拐点等）
4. 图例对应的曲线/颜色（如适用）
5. 该图可能支持的论文结论（推断）
直接输出描述文本，不要额外格式。"""

# 裁剪渲染倍率（2x 保证小图可辨识）
CROP_ZOOM = 2.0


def _cfg(settings: Any, name: str, default: Any) -> Any:
    value = getattr(settings, name, None)
    return default if value is None else value


def crop_figure_region(pdf_path: Path, page_no: int, bbox: list[float] | None, zoom: float = CROP_ZOOM) -> bytes | None:
    """按节点 bbox 裁剪 PDF 页面区域为 PNG 字节；失败返回 None。"""
    if not bbox or len(bbox) != 4:
        return None
    try:
        import pymupdf

        doc = pymupdf.open(str(pdf_path))
    except Exception as exc:
        logger.warning("[VLM] 打开 PDF 裁剪失败: %s", exc)
        return None
    try:
        if page_no < 1 or page_no > doc.page_count:
            return None
        page = doc[page_no - 1]
        clip = pymupdf.Rect(*bbox)
        clip = clip & page.rect  # 交集，防越界
        if clip.is_empty or clip.width < 4 or clip.height < 4:
            return None
        pixmap = page.get_pixmap(clip=clip, matrix=pymupdf.Matrix(zoom, zoom))
        return pixmap.tobytes("png")
    except Exception as exc:
        logger.warning("[VLM] 裁剪页 %s 区域 %s 失败: %s", page_no, bbox, exc)
        return None
    finally:
        doc.close()


class QwenVLDescriber:
    """OpenAI 兼容多模态接口（qwen-vl-max 等）的图表描述器。"""

    name = "qwen-vl"

    def __init__(self, api_key: str, base_url: str, model: str, timeout: float = 60.0) -> None:
        self._api_key = api_key
        self._base_url = base_url
        self._model = model
        self._timeout = timeout

    async def describe(self, png_bytes: bytes, caption: str) -> str:
        """描述单张图片；失败抛异常（调用方按图降级）。"""
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=self._api_key, base_url=self._base_url, timeout=self._timeout)
        data_uri = f"data:image/png;base64,{base64.b64encode(png_bytes).decode('ascii')}"
        response = await client.chat.completions.create(
            model=self._model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": DESCRIBE_PROMPT.format(caption=caption or "（无图注）")},
                        {"type": "image_url", "image_url": {"url": data_uri}},
                    ],
                }
            ],
        )
        content = (response.choices[0].message.content or "").strip() if response.choices else ""
        return content


def get_vlm_describer(settings: Any | None = None) -> QwenVLDescriber | None:
    """按配置构造描述器；未启用或无 key 返回 None（管线跳过图表描述步骤）。"""
    if not bool(_cfg(settings, "vlm_enabled", False)):
        return None
    api_key = str(_cfg(settings, "qwen_api_key", "") or "")
    if not api_key or api_key == "your-api-key":
        logger.info("[VLM] vlm_enabled 但未配置 qwen_api_key，图表描述跳过")
        return None
    return QwenVLDescriber(
        api_key=api_key,
        base_url=str(_cfg(settings, "qwen_base_url", "https://dashscope.aliyuncs.com/compatible-mode/v1")),
        model=str(_cfg(settings, "vlm_model", "qwen-vl-max")),
        timeout=float(_cfg(settings, "vlm_timeout_sec", 60.0)),
    )


async def describe_figure_nodes(
    pdf_path: Path,
    figure_nodes: list[Any],
    describer: QwenVLDescriber,
    *,
    concurrency: int = 3,
) -> tuple[int, int]:
    """并发描述 figure 节点：成功者回填 node.text，返回 (成功数, 失败数)。

    单图失败只降级该图（text 保持为空），不中断整批。
    """
    semaphore = asyncio.Semaphore(max(1, concurrency))
    successes = 0
    failures = 0

    async def describe_one(node: Any) -> None:
        nonlocal successes, failures
        png_bytes = await asyncio.to_thread(crop_figure_region, pdf_path, node.page, node.bbox)
        if not png_bytes:
            failures += 1
            return
        async with semaphore:
            try:
                description = await describer.describe(png_bytes, node.caption or "")
            except Exception as exc:
                failures += 1
                logger.warning("[VLM] 图表描述失败（node=%s page=%s）: %s", node.node_id, node.page, exc)
                return
        if description:
            node.text = description
            successes += 1
        else:
            failures += 1

    await asyncio.gather(*(describe_one(node) for node in figure_nodes))
    return successes, failures


__all__ = [
    "CROP_ZOOM",
    "DESCRIBE_PROMPT",
    "QwenVLDescriber",
    "crop_figure_region",
    "describe_figure_nodes",
    "get_vlm_describer",
]
