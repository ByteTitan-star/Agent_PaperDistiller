"""Canonical Document IR — 所有解析后端的统一中间表示。

设计约束（见 todo.md）：
- Parser（PyMuPDF / pypdf / MinerU / OCR）只负责产出 DocumentIR，
  下游（切块 / 翻译 / 摘要 / 向量入库）只消费 DocumentIR，换解析引擎不动下游。
- IR 必须可 JSON 持久化（parse_artifact.json），调整切块或 embedding 策略时无需重跑解析。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

# 节点类型集合（DocNode.type）
NODE_TYPES = frozenset({"heading", "paragraph", "equation", "table", "figure", "reference", "caption"})

# 解析失败原因枚举（DocumentIR.error_kind）
ERROR_NONE = ""
ERROR_SCANNED = "scanned_pdf"  # 扫描件：无文本层且未启用 OCR
ERROR_ENCRYPTED = "encrypted_pdf"  # 加密文档
ERROR_CORRUPT = "corrupt_pdf"  # 无法打开 / 结构损坏
ERROR_EMPTY = "empty_text"  # 打开成功但未提取到任何文本


@dataclass
class DocNode:
    """文档节点：标题 / 段落 / 公式 / 表格 / 图片 / 参考文献。

    text 为可直接进入 Markdown 的内容；
    equation 节点 latex 存 LaTeX 源码，table 节点 html 存表格 HTML（MinerU 等引擎可提供）。
    """

    node_id: str
    type: str
    text: str = ""
    page: int = 0
    level: int = 0  # heading 层级（2~4，与 Markdown # 数量一致）
    latex: str | None = None
    html: str | None = None
    caption: str | None = None
    section_path: list[str] = field(default_factory=list)
    bbox: list[float] | None = None  # [x0, y0, x1, y1]，PDF 坐标系
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class PreflightReport:
    """Preflight 预检查结果（见 preflight.py）。"""

    page_count: int = 0
    has_text_layer: bool = True
    scanned_ratio: float = 0.0  # 无有效文本页占比
    encrypted: bool = False
    suspected_two_column: bool = False
    char_count: int = 0
    parser_route: str = "native"  # native | scanned | fallback
    warnings: list[str] = field(default_factory=list)


@dataclass
class DocumentIR:
    """解析统一产物。

    - text：按阅读序拼接的全文（保留 [Page N] 标记），供翻译 / 摘要 / 领域推断使用；
    - sections：[(Markdown 标题, 内容)]，向后兼容 split_text_into_sections 的消费方；
    - nodes：结构化节点，供结构感知切块 / 向量元数据 / 图表证据提取使用；
    - ok=False 时 error/error_kind 说明原因，下游必须终止而不是把错误文案当正文。
    """

    parser: str = "unknown"
    text: str = ""
    sections: list[tuple[str, str]] = field(default_factory=list)
    nodes: list[DocNode] = field(default_factory=list)
    report: PreflightReport = field(default_factory=PreflightReport)
    metadata: dict[str, Any] = field(default_factory=dict)  # GROBID 等来源的文档级元数据
    ok: bool = True
    error: str = ""
    error_kind: str = ERROR_NONE

    @property
    def effective_chars(self) -> int:
        return len("".join(content for _, content in self.sections).strip())

    def node_stats(self) -> dict[str, int]:
        stats: dict[str, int] = {}
        for node in self.nodes:
            stats[node.type] = stats.get(node.type, 0) + 1
        return stats

    # ---------------- JSON 持久化 ----------------

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        # tuple 序列化为 JSON 数组，读取时再还原
        data["sections"] = [[title, content] for title, content in self.sections]
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DocumentIR:
        sections = [
            (str(pair[0]), str(pair[1])) for pair in data.get("sections", []) if isinstance(pair, (list, tuple))
        ]
        report_raw = data.get("report") or {}
        report = PreflightReport(
            page_count=int(report_raw.get("page_count", 0)),
            has_text_layer=bool(report_raw.get("has_text_layer", True)),
            scanned_ratio=float(report_raw.get("scanned_ratio", 0.0)),
            encrypted=bool(report_raw.get("encrypted", False)),
            suspected_two_column=bool(report_raw.get("suspected_two_column", False)),
            char_count=int(report_raw.get("char_count", 0)),
            parser_route=str(report_raw.get("parser_route", "native")),
            warnings=[str(w) for w in report_raw.get("warnings", [])],
        )
        nodes = [
            DocNode(
                node_id=str(n.get("node_id", f"n{idx:04d}")),
                type=str(n.get("type", "paragraph")),
                text=str(n.get("text", "")),
                page=int(n.get("page", 0)),
                level=int(n.get("level", 0)),
                latex=n.get("latex"),
                html=n.get("html"),
                caption=n.get("caption"),
                section_path=[str(s) for s in n.get("section_path", [])],
                bbox=[float(v) for v in n["bbox"]] if isinstance(n.get("bbox"), list) and n["bbox"] else None,
                meta=dict(n.get("meta") or {}),
            )
            for idx, n in enumerate(data.get("nodes", []))
            if isinstance(n, dict)
        ]
        return cls(
            parser=str(data.get("parser", "unknown")),
            text=str(data.get("text", "")),
            sections=sections,
            nodes=nodes,
            report=report,
            metadata=dict(data.get("metadata") or {}),
            ok=bool(data.get("ok", True)),
            error=str(data.get("error", "")),
            error_kind=str(data.get("error_kind", ERROR_NONE)),
        )

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), ensure_ascii=False), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> DocumentIR | None:
        if not path.exists():
            return None
        try:
            return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError, ValueError):
            return None


def failed_ir(
    error_kind: str,
    error: str,
    *,
    parser: str = "router",
    report: PreflightReport | None = None,
) -> DocumentIR:
    """构造解析失败的 IR（用于错误传播，而不是返回错误字符串当正文）。"""
    return DocumentIR(
        parser=parser,
        ok=False,
        error=error,
        error_kind=error_kind,
        report=report or PreflightReport(),
    )


__all__ = [
    "ERROR_CORRUPT",
    "ERROR_EMPTY",
    "ERROR_ENCRYPTED",
    "ERROR_NONE",
    "ERROR_SCANNED",
    "NODE_TYPES",
    "DocNode",
    "DocumentIR",
    "PreflightReport",
    "failed_ir",
]
