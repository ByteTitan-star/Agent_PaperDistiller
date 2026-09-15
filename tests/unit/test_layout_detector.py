"""版面公式检测（MFD 升级）测试：纯解码函数 + 真实模型集成（权重缺失自动跳过）。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

np = pytest.importorskip("numpy", reason="numpy 未安装时跳过")
pymupdf = pytest.importorskip("pymupdf", reason="PyMuPDF 未安装时跳过")

from app.pipeline.layout_detector import (
    FORMULA_REGION_LABELS,
    PaddleLayoutDetector,
    RegionDetection,
    decode_paddlex_detections,
    formula_regions,
    get_layout_detector,
    load_inference_config,
)

MODEL_DIR = Path(__file__).resolve().parents[2] / "backend" / "models" / "paddle" / "PP-DocLayoutV2_infer"
DEMO_FORMULA_PNG = MODEL_DIR.parent / "demo_formula.png"

LABELS = ["text", "paragraph_title", "display_formula", "inline_formula", "formula", "table"]


# ---------------------------------------------------------------------
# 纯函数：解码
# ---------------------------------------------------------------------


def test_decode_filters_by_score_and_maps_labels() -> None:
    output = np.array(
        [
            [2, 0.95, 10, 20, 110, 60],  # display_formula，高分保留
            [0, 0.98, 1, 2, 3, 4],  # text，高分保留
            [3, 0.10, 5, 5, 50, 50],  # 低分过滤
            [99, 0.99, 0, 0, 1, 1],  # 非法类 id 过滤
        ],
        dtype=np.float32,
    )
    dets = decode_paddlex_detections(output, LABELS, score_threshold=0.3)
    labels = [d.label for d in dets]
    assert set(labels) == {"text", "display_formula"}
    assert dets[0].label == "text" and dets[0].score == pytest.approx(0.98)  # 按 score 降序


def test_decode_normalizes_swapped_corners() -> None:
    output = np.array([[2, 0.9, 110, 60, 10, 20]], dtype=np.float32)  # x1>x2 / y1>y2
    dets = decode_paddlex_detections(output, LABELS, 0.3)
    assert dets[0].bbox == (10.0, 20.0, 110.0, 60.0)


def test_decode_empty_and_malformed() -> None:
    assert decode_paddlex_detections(None, LABELS, 0.3) == []
    assert decode_paddlex_detections(np.zeros((0, 8), dtype=np.float32), LABELS, 0.3) == []
    short = np.array([[2, 0.9, 1, 2]], dtype=np.float32)  # 列数不足
    assert decode_paddlex_detections(short, LABELS, 0.3) == []


def test_formula_regions_filter() -> None:
    dets = [
        RegionDetection((0, 0, 10, 10), "text", 0.9),
        RegionDetection((0, 0, 10, 10), "display_formula", 0.9),
        RegionDetection((0, 0, 10, 10), "formula", 0.9),
        RegionDetection((0, 0, 10, 10), "inline_formula", 0.9),
    ]
    labels = {d.label for d in formula_regions(dets)}
    assert labels == {"display_formula", "formula"}  # inline 不整行标记
    assert "inline_formula" not in FORMULA_REGION_LABELS


def test_load_inference_config_real_yml() -> None:
    if not MODEL_DIR.exists():
        pytest.skip("PP-DocLayoutV2 模型未下载")
    config = load_inference_config(MODEL_DIR)
    assert config["target_size"] == (800, 800)
    assert "display_formula" in config["label_list"]
    assert "inline_formula" in config["label_list"]


# ---------------------------------------------------------------------
# 配置门控
# ---------------------------------------------------------------------


def test_get_layout_detector_off_by_default() -> None:
    assert get_layout_detector(SimpleNamespace()) is None
    assert get_layout_detector(SimpleNamespace(layout_detector="off")) is None


def test_get_layout_detector_guards() -> None:
    # paddle 缺失 -> None
    with patch("app.pipeline.layout_detector._paddle_available", return_value=False):
        assert get_layout_detector(SimpleNamespace(layout_detector="doclayout")) is None
    # 模型目录缺失 -> None
    assert (
        get_layout_detector(
            SimpleNamespace(
                layout_detector="doclayout", layout_detector_model="/no/such/dir", layout_detector_score=0.3
            )
        )
        is None
    )


# ---------------------------------------------------------------------
# 真实模型集成（权重存在时运行）
# ---------------------------------------------------------------------


@pytest.mark.skipif(not MODEL_DIR.exists(), reason="PP-DocLayoutV2 模型未下载")
@pytest.mark.skipif(not DEMO_FORMULA_PNG.exists(), reason="公式示例图未下载")
def test_real_detector_finds_formula_regions(tmp_path: Path) -> None:
    """左半页公式示例图 + 右半页正文 -> 公式框落在图像区，标题落在右栏。"""
    doc = pymupdf.open()
    page = doc.new_page(width=842, height=595)
    image_rect = pymupdf.Rect(40, 80, 400, 540)
    page.insert_image(image_rect, filename=str(DEMO_FORMULA_PNG))
    page.insert_text((440, 120), "1. Introduction", fontsize=12, fontname="hebo")
    page.insert_text((440, 150), "We study attention mechanisms in this paper.", fontsize=10)
    pdf_path = tmp_path / "mfd.pdf"
    doc.save(str(pdf_path))

    zoom = 2.0
    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
    rgb = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)

    detector = PaddleLayoutDetector(MODEL_DIR, score_threshold=0.3)
    detections = detector.detect(rgb)
    frs = formula_regions(detections)

    assert len(frs) >= 3, "公式示例图内应检出多个公式区域"
    # 公式框落在图像区域内（像素坐标，含容差）
    img_px = (v * zoom for v in image_rect)
    ix0, iy0, ix1, iy1 = image_rect.x0 * zoom, image_rect.y0 * zoom, image_rect.x1 * zoom, image_rect.y1 * zoom
    del img_px
    for det in frs:
        assert det.bbox[0] >= ix0 - 30 and det.bbox[2] <= ix1 + 30
        assert det.bbox[1] >= iy0 - 30 and det.bbox[3] <= iy1 + 30

    # 右栏标题定位正确（坐标映射回归：此前 scale_factor 方向 bug 会让标题落到左栏）
    titles = [d for d in detections if d.label == "paragraph_title"]
    assert titles and titles[0].bbox[0] > 440 * zoom * 0.9


@pytest.mark.skipif(not MODEL_DIR.exists(), reason="PP-DocLayoutV2 模型未下载")
def test_real_detector_backend_integration(tmp_path: Path) -> None:
    """PyMuPDFBackend + 假识别器：检测框内的文本行被标记为公式行并回填 $$LaTeX$$。"""
    from app.pipeline.parser_backend import PyMuPDFBackend

    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 90), "1. Introduction", fontsize=12, fontname="hebo")
    page.insert_text((72, 130), "The attention function is defined below.", fontsize=10)
    page.insert_text((72, 160), "y equals f of x times g of x plus b", fontsize=10)
    pdf_path = tmp_path / "backend_mfd.pdf"
    doc.save(str(pdf_path))

    zoom = 2.0
    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
    page_size = (pix.height, pix.width)

    class FakeDetector:
        name = "fake"

        def detect(self, image_rgb):
            # 把第三行（y≈160pt -> 320px）整行报为 display_formula（返回原图像素坐标）
            return [
                RegionDetection((60 * zoom, 310, 480 * zoom, 335), "display_formula", 0.97),
            ]

    class FakeRecognizer:
        name = "fake"

        def recognize(self, png_bytes):
            return "y = f(x) \\\\cdot g(x) + b"

    backend = PyMuPDFBackend(formula_recognizer=FakeRecognizer(), layout_detector=FakeDetector())
    ir = backend.parse(pdf_path)

    assert ir.ok is True
    assert "$$y = f(x)" in ir.text  # 公式行被检测框标记并回填 LaTeX
    equations = [n for n in ir.nodes if n.type == "equation"]
    assert len(equations) == 1
    del page_size


# ---------------------------------------------------------------------
# 轻量档（S）+ 滑窗：公式落位验收
# ---------------------------------------------------------------------


def test_tiled_detect_offsets_and_block_sizes() -> None:
    """滑窗纯函数：2x2 切块尺寸正确，块内坐标加偏移映射回原图。"""
    from app.pipeline.layout_detector import RegionDetection, tiled_detect

    image = np.zeros((100, 200, 3), dtype=np.uint8)
    calls: list[tuple[int, int]] = []

    def fake_detect_single(tile):
        h, w = tile.shape[:2]
        calls.append((h, w))
        return [RegionDetection((10, 10, 40, 20), "formula", 0.9)]

    merged = tiled_detect(2, image, fake_detect_single)
    # 块尺寸 = stride + 两侧 20% 重叠：宽 200/2*1.2=120，高 100/2*1.2=60
    assert len(calls) == 4 and all((h, w) == (60, 120) for h, w in calls)
    # 4 个块各一框，坐标 = 块内 local + 块偏移；右列块偏移 >= 80
    xs = sorted(round(d.bbox[0]) for d in merged)
    assert xs[0] == 10 and xs[-1] >= 90  # 左列块偏移 0，右列块偏移 80+
    assert all(d.bbox[2] <= 200 and d.bbox[3] <= 100 for d in merged)  # 全部落在原图内


@pytest.mark.skipif(
    not (Path(__file__).resolve().parents[2] / "backend/models/paddle/PP-DocLayout-S_infer").exists(),
    reason="PP-DocLayout-S 模型未下载",
)
@pytest.mark.skipif(not DEMO_FORMULA_PNG.exists(), reason="公式示例图未下载")
def test_light_model_tiled_finds_formula_regions(tmp_path: Path) -> None:
    """S 档（4MB）+ 默认 2×2 滑窗：整页 fixture 的公式全部检出且落位正确。

    S 档（1M 参数/480 输入）整图直检会把小公式压没（0 检出），滑窗后等效
    分辨率翻倍即可检出——这是它作为入库默认档的依据。右栏小字正文可能漏检，
    但正文提取不依赖版面模型（走 PyMuPDF 文本层），不构成验收项。
    """
    doc = pymupdf.open()
    page = doc.new_page(width=842, height=595)
    image_rect = pymupdf.Rect(40, 80, 400, 540)
    page.insert_image(image_rect, filename=str(DEMO_FORMULA_PNG))
    page.insert_text((440, 120), "1. Introduction", fontsize=12, fontname="hebo")
    pdf_path = tmp_path / "mfd_s.pdf"
    doc.save(str(pdf_path))

    from types import SimpleNamespace

    from app.pipeline.layout_detector import get_layout_detector

    detector = get_layout_detector(
        SimpleNamespace(
            layout_detector="doclayout",
            layout_detector_model=str(MODEL_DIR.parent / "PP-DocLayout-S_infer"),
            layout_detector_score=0.3,
        )
    )
    assert detector._tiles == 2  # 轻量档默认滑窗

    zoom = 2.0
    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
    rgb = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    frs = formula_regions(detector.detect(rgb))

    assert len(frs) >= 3, "滑窗后应检出多个公式区域（直检为 0）"
    ix0, iy0, ix1, iy1 = image_rect.x0 * zoom, image_rect.y0 * zoom, image_rect.x1 * zoom, image_rect.y1 * zoom
    for det in frs:
        assert det.bbox[0] >= ix0 - 30 and det.bbox[2] <= ix1 + 30
        assert det.bbox[1] >= iy0 - 30 and det.bbox[3] <= iy1 + 30
