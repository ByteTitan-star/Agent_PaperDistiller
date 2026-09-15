"""GROBID 学术元数据增强（可选）：title / authors / abstract / DOI / references。

GROBID 是学术 PDF 结构化服务（TEI XML 输出）。本模块只做轻量 HTTP 客户端：
- `grobid_enabled=True` 且 `grobid_base_url` 可达时，解析成功后调用
  `/api/processFulltextDocument` 抽取元数据，合入 DocumentIR.metadata；
- 服务不可用/解析失败一律告警跳过，绝不阻塞主管线；
- 不用 GROBID 替代解析主链路（版面/公式/表格仍由 ParserRouter 负责）。
"""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

from .document_ir import DocNode, DocumentIR

logger = logging.getLogger(__name__)

TEI_NAMESPACE = {"tei": "http://www.tei-c.org/ns/1.0"}


def _cfg(settings: Any, name: str, default: Any) -> Any:
    value = getattr(settings, name, None)
    return default if value is None else value


class GrobidClient:
    """GROBID REST 客户端（同步，管线内经 asyncio.to_thread 调用）。"""

    def __init__(self, base_url: str, timeout: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def available(self) -> bool:
        """健康检查（/api/isalive）。"""
        try:
            with urlopen(f"{self.base_url}/api/isalive", timeout=min(self.timeout, 5.0)) as response:
                return response.status == 200
        except (URLError, TimeoutError, OSError):
            return False

    def extract_tei(self, pdf_path: Path) -> str | None:
        """调用 processFulltextDocument 返回 TEI XML 文本；失败返回 None。"""
        try:
            pdf_bytes = Path(pdf_path).read_bytes()
        except OSError as exc:
            logger.warning("[GROBID] 读取 PDF 失败: %s", exc)
            return None
        request = Request(
            f"{self.base_url}/api/processFulltextDocument",
            data=pdf_bytes,
            headers={"Content-Type": "application/pdf"},
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                return response.read().decode("utf-8", errors="replace")
        except (URLError, TimeoutError, OSError) as exc:
            logger.warning("[GROBID] processFulltextDocument 失败: %s", exc)
            return None


def parse_tei_metadata(tei_xml: str) -> dict[str, Any]:
    """TEI XML -> 文档级元数据 dict（title/authors/abstract/doi/references）。"""
    try:
        root = ET.fromstring(tei_xml)
    except ET.ParseError as exc:
        logger.warning("[GROBID] TEI XML 解析失败: %s", exc)
        return {}

    title_el = root.find(".//tei:fileDesc/tei:titleStmt/tei:title", TEI_NAMESPACE)
    title = (title_el.text or "").strip() if title_el is not None and title_el.text else ""

    authors: list[str] = []
    for author in root.findall(".//tei:fileDesc/tei:titleStmt/tei:author/tei:persName", TEI_NAMESPACE):
        forenames = [el.text.strip() for el in author.findall("tei:forename", TEI_NAMESPACE) if el.text]
        surnames = [el.text.strip() for el in author.findall("tei:surname", TEI_NAMESPACE) if el.text]
        full = " ".join(filter(None, [" ".join(forenames), " ".join(surnames)])).strip()
        if full:
            authors.append(full)

    abstract_el = root.find(".//tei:profileDesc/tei:abstract", TEI_NAMESPACE)
    abstract = " ".join(" ".join(abstract_el.itertext()).split()) if abstract_el is not None else ""

    doi = ""
    for idno in root.findall(".//tei:sourceDesc//tei:idno[@type='DOI']", TEI_NAMESPACE):
        if idno.text and idno.text.strip():
            doi = idno.text.strip()
            break

    references: list[str] = []
    for bibl in root.findall(".//tei:listBibl/tei:biblStruct", TEI_NAMESPACE):
        ref_title_el = bibl.find(".//tei:title", TEI_NAMESPACE)
        if ref_title_el is not None and ref_title_el.text and ref_title_el.text.strip():
            references.append(ref_title_el.text.strip())

    metadata: dict[str, Any] = {}
    if title:
        metadata["title"] = title
    if authors:
        metadata["authors"] = authors
    if abstract:
        metadata["abstract"] = abstract
    if doi:
        metadata["doi"] = doi
    if references:
        metadata["references"] = references
    return metadata


# 前导编号标记："[12]"、"12."、"12)" 等（PDF 抽取的参考文献行常见）
REF_NUMBER_PREFIX_RE = re.compile(r"^\s*(\[\d+\]|\d+[.)])\s*")


def _ref_fingerprint(text: str) -> str:
    """参考文献指纹：去前导编号、小写、剔除非字母数字/中日文字符，截取前 60 字符。"""
    stripped = REF_NUMBER_PREFIX_RE.sub("", text)
    normalized = re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", stripped.lower())
    return normalized[:60]


def merge_reference_nodes(ir: DocumentIR, grobid_references: list[str]) -> tuple[int, int]:
    """GROBID 结构化 references 与解析层 reference 节点合并去重。

    匹配策略：规范化指纹包含判定（解析层节点常被截断/混入页码噪声，GROBID 标题
    为其子串即视为同一条；指纹长度 >= 8 才参与匹配，避免短串误配）。
    - 命中：节点 meta 标记 source=grobid 并回填规范标题（保留原节点文本）；
    - 未命中：追加 source=grobid 的新 reference 节点（不与已有条目重复）；
    返回 (合并数, 新增数)。
    """
    existing_fps = [(_ref_fingerprint(n.text), n) for n in ir.nodes if n.type == "reference"]

    merged = 0
    added = 0
    for entry in grobid_references:
        entry_fp = _ref_fingerprint(entry)
        if len(entry_fp) < 8:
            continue
        hit = next(
            (node for fp, node in existing_fps if len(fp) >= 8 and (entry_fp in fp or fp in entry_fp)),
            None,
        )
        if hit is not None:
            meta = dict(hit.meta or {})
            meta["source"] = "grobid"
            meta["grobid_title"] = entry
            hit.meta = meta
            merged += 1
            continue
        new_node = DocNode(
            node_id=f"ref-g{added + 1:04d}",
            type="reference",
            text=entry,
            section_path=["参考文献"],
            meta={"source": "grobid"},
        )
        ir.nodes.append(new_node)
        existing_fps.append((_ref_fingerprint(entry), new_node))
        added += 1
    return merged, added


def get_grobid_client(settings: Any | None = None) -> GrobidClient | None:
    """按配置构造客户端；未启用返回 None。"""
    if not bool(_cfg(settings, "grobid_enabled", False)):
        return None
    base_url = str(_cfg(settings, "grobid_base_url", "http://localhost:8070") or "")
    if not base_url:
        return None
    return GrobidClient(base_url=base_url, timeout=float(_cfg(settings, "grobid_timeout_sec", 30.0)))


def enrich_ir_metadata(ir: DocumentIR, pdf_path: Path, settings: Any | None = None) -> tuple[bool, str]:
    """就地增强 DocumentIR.metadata；返回 (是否增强, 说明)。

    任何失败只记录 warning，不抛异常（不阻塞主管线）。
    """
    client = get_grobid_client(settings)
    if client is None:
        return False, "grobid_disabled"
    if Path(pdf_path).suffix.lower() != ".pdf":
        return False, "not_pdf"  # GROBID 仅支持 PDF
    try:
        if not client.available():
            return False, "grobid_unreachable"
        tei = client.extract_tei(pdf_path)
        if not tei:
            return False, "grobid_no_response"
        metadata = parse_tei_metadata(tei)
        if not metadata:
            return False, "grobid_empty_metadata"
        ir.metadata.update(metadata)
        # references 与解析层 reference 节点合并去重（命中补 meta，未命中追加节点）
        grobid_refs = metadata.get("references") or []
        merged, added = merge_reference_nodes(ir, grobid_refs)
        logger.info(
            "[GROBID] ✅ 元数据增强 | keys=%s | references 合并 %d / 新增 %d",
            sorted(metadata.keys()),
            merged,
            added,
        )
        return True, "ok"
    except Exception as exc:  # 保险丝：GROBID 异常绝不拖垮解析
        logger.warning("[GROBID] 增强异常（忽略）: %s", exc)
        return False, f"grobid_error:{exc}"


__all__ = [
    "GrobidClient",
    "enrich_ir_metadata",
    "get_grobid_client",
    "merge_reference_nodes",
    "parse_tei_metadata",
]
