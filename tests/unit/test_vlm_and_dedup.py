"""VLM 图表描述 / SHA256 去重 / Mathpix 缓存 测试。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

pymupdf = pytest.importorskip("pymupdf", reason="PyMuPDF 未安装时跳过")

import io
import json

from tests.helpers import MockStorage

from app.harness.pipeline.orchestrator import _load_ir, run_figure_step
from app.pipeline.document_ir import DocNode, DocumentIR
from app.pipeline.formula_recognizer import MathpixAdapter
from app.pipeline.state_broker import TaskBroker
from app.pipeline.vlm_describer import crop_figure_region, get_vlm_describer
from app.storage import Storage


def _make_real_pdf(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 300, 200))
    pix.clear_with(200)
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 90), "1. Introduction", fontsize=12, fontname="hebo")
    page.insert_text((72, 120), "Body text of the paper for testing.", fontsize=10)
    page.insert_image(pymupdf.Rect(72, 150, 300, 320), pixmap=pix)
    doc.save(str(path))
    return path


# ---------------------------------------------------------------------
# VLM 图表描述
# ---------------------------------------------------------------------


def test_crop_figure_region_returns_png(tmp_path: Path) -> None:
    pdf = _make_real_pdf(tmp_path / "fig.pdf")
    png = crop_figure_region(pdf, 1, [80, 160, 290, 310])
    assert png is not None
    assert png[:4] == b"\x89PNG"


def test_crop_figure_region_invalid_inputs(tmp_path: Path) -> None:
    pdf = _make_real_pdf(tmp_path / "fig.pdf")
    assert crop_figure_region(pdf, 1, None) is None
    assert crop_figure_region(pdf, 1, [1, 2, 3]) is None
    assert crop_figure_region(pdf, 99, [80, 160, 290, 310]) is None  # 页码越界
    assert crop_figure_region(tmp_path / "ghost.pdf", 1, [0, 0, 10, 10]) is None


def test_get_vlm_describer_gating() -> None:
    assert get_vlm_describer(SimpleNamespace(vlm_enabled=False)) is None
    assert (
        get_vlm_describer(SimpleNamespace(vlm_enabled=True, qwen_api_key="your-api-key")) is None
    )  # 占位 key 视为未配置
    describer = get_vlm_describer(
        SimpleNamespace(
            vlm_enabled=True,
            qwen_api_key="sk-test",
            qwen_base_url="https://dashscope.example/compatible-mode/v1",
            vlm_model="qwen-vl-max",
            vlm_timeout_sec=5.0,
        )
    )
    assert describer is not None
    assert describer._model == "qwen-vl-max"


class FakeVLMDescriber:
    async def describe(self, png_bytes: bytes, caption: str) -> str:
        return f"VLM对[{caption}]的图表描述：折线图，横轴为epochs。"


@pytest.mark.asyncio
async def test_run_figure_step_describes_and_ingests(tmp_path: Path) -> None:
    storage = MockStorage(tmp_path)
    _make_real_pdf(tmp_path / "paper-1" / "paper.pdf")  # 真实 PDF 供裁剪
    broker = TaskBroker()
    await broker.create("task-v", "paper-1")

    figure = DocNode(
        node_id="figure-p1-1",
        type="figure",
        text="",
        page=1,
        bbox=[72, 150, 300, 320],
        caption="Figure 1: Training curve",
    )
    ir = DocumentIR(parser="pymupdf", sections=[("## 1 引言", "正文。")], nodes=[figure])
    storage.parse_artifacts["paper-1"] = ir
    storage.chunks["paper-1"] = ["## 1 引言\n正文。"]
    storage.chunk_metas["paper-1"] = [{"element_type": "text", "section": "1 引言", "page": 1}]

    settings = SimpleNamespace(vlm_enabled=True, vlm_max_figures=5, vlm_concurrency=2)

    with patch("app.pipeline.vlm_describer.get_vlm_describer", return_value=FakeVLMDescriber()):
        result = await run_figure_step(
            paper_id="paper-1",
            task_id="task-v",
            storage=storage,
            broker=broker,
            settings=settings,
        )

    assert result["ok"] is True
    assert result["described"] == 1
    # 节点描述回写产物
    updated = storage.parse_artifacts["paper-1"]
    figure_node = next(n for n in updated.nodes if n.type == "figure")
    assert "折线图" in figure_node.text
    # 描述块入库（图注 + 描述），带 image_desc 元数据
    assert any("图片描述" in chunk and "Figure 1" in chunk for chunk in storage.chunks["paper-1"])
    assert storage.chunk_metas["paper-1"][-1]["element_type"] == "image_desc"
    assert len(storage.chunks["paper-1"]) == 2  # 原正文块 + 描述块


@pytest.mark.asyncio
async def test_run_figure_step_disabled_skips(tmp_path: Path) -> None:
    storage = MockStorage(tmp_path)
    broker = TaskBroker()
    await broker.create("task-d", "paper-d")
    result = await run_figure_step(
        paper_id="paper-d",
        task_id="task-d",
        storage=storage,
        broker=broker,
        settings=SimpleNamespace(),  # vlm_enabled 缺省 = False
    )
    assert result["skipped"] == "vlm_disabled"
    assert storage.chunks == {}


@pytest.mark.asyncio
async def test_run_figure_step_vlm_error_does_not_block(tmp_path: Path) -> None:
    storage = MockStorage(tmp_path)
    _make_real_pdf(tmp_path / "paper-e" / "paper.pdf")
    broker = TaskBroker()
    await broker.create("task-e", "paper-e")
    figure = DocNode(node_id="f1", type="figure", text="", page=1, bbox=[72, 150, 300, 320])
    storage.parse_artifacts["paper-e"] = DocumentIR(
        parser="pymupdf", sections=[("## 1 引言", "正文。")], nodes=[figure]
    )

    class ExplodingDescriber:
        async def describe(self, png_bytes: bytes, caption: str) -> str:
            raise RuntimeError("vlm down")

    with patch("app.pipeline.vlm_describer.get_vlm_describer", return_value=ExplodingDescriber()):
        result = await run_figure_step(
            paper_id="paper-e",
            task_id="task-e",
            storage=storage,
            broker=broker,
            settings=SimpleNamespace(vlm_enabled=True, vlm_max_figures=5, vlm_concurrency=2),
        )

    assert result["ok"] is True  # 步骤失败不阻塞主管线


# ---------------------------------------------------------------------
# SHA256 去重
# ---------------------------------------------------------------------


def _storage(tmp_path: Path) -> Storage:
    return Storage(base_dir=tmp_path / "data", templates_dir=tmp_path / "templates")


def test_sha256_dedup_reuses_artifact(tmp_path: Path) -> None:
    storage = _storage(tmp_path)
    pdf_bytes = _make_real_pdf(tmp_path / "seed.pdf").read_bytes()

    dir_a = storage.paper_output_dir("paper-a")
    (dir_a / "source.pdf").write_bytes(pdf_bytes)
    dir_b = storage.paper_output_dir("paper-b")
    (dir_b / "source.pdf").write_bytes(pdf_bytes)  # 同内容重复上传
    dir_c = storage.paper_output_dir("paper-c")
    (dir_c / "source.pdf").write_bytes(pdf_bytes + b"different")  # 不同文件

    ir_a = DocumentIR(parser="pymupdf", sections=[("## 1 引言", "内容 A")])
    calls: list[str] = []

    def fake_parse(path, settings=None):
        calls.append(str(path))
        return ir_a

    # 第一次：真实解析并记录索引
    with patch("app.harness.pipeline.orchestrator.parse_document", side_effect=fake_parse):
        loaded_a = _load_ir("paper-a", storage, None)
    assert loaded_a.parser == "pymupdf"
    assert len(calls) == 1

    # 第二次（同文件）：命中去重，不重跑解析
    with patch("app.harness.pipeline.orchestrator.parse_document", side_effect=fake_parse):
        loaded_b = _load_ir("paper-b", storage, None)
    assert len(calls) == 1, "同内容文件应复用产物，不再解析"
    assert loaded_b.parser == "pymupdf"
    assert storage.load_parse_artifact("paper-b") is not None  # 产物已落到新 paper_id

    # 不同内容：正常解析
    with patch("app.harness.pipeline.orchestrator.parse_document", side_effect=fake_parse):
        _load_ir("paper-c", storage, None)
    assert len(calls) == 2


def test_sha_index_persists_across_instances(tmp_path: Path) -> None:
    storage1 = _storage(tmp_path)
    pdf = _make_real_pdf(tmp_path / "seed2.pdf")
    digest = storage1.record_sha256("paper-x", pdf)
    storage2 = _storage(tmp_path)  # 新实例读同一 data 目录
    assert storage2._load_sha_index().get(digest) == "paper-x"


# ---------------------------------------------------------------------
# Mathpix 识别缓存
# ---------------------------------------------------------------------


def _mathpix_http(text: str):
    return io.BytesIO(json.dumps({"text": text}).encode("utf-8"))


def test_mathpix_cache_hits_same_image() -> None:
    MathpixAdapter._latex_cache.clear()
    adapter = MathpixAdapter(app_id="id", app_key="key")
    png = b"\x89PNG fake-formula-image-bytes"

    http_calls: list[int] = []

    def urlopen_side_effect(request, timeout=None):
        http_calls.append(1)
        return _mathpix_http("x^2")

    with patch("app.pipeline.formula_recognizer.urlopen", side_effect=urlopen_side_effect):
        first = adapter.recognize(png)
        second = adapter.recognize(png)  # 同图第二次走缓存
        calls_after_same_image = len(http_calls)
        other = adapter.recognize(png + b"-other")  # 不同图片不走缓存

    assert first == "x^2"
    assert second == "x^2"
    assert calls_after_same_image == 1  # 同图第二次命中缓存，HTTP 只调用一次

    assert other is not None
    assert len(http_calls) == 2
