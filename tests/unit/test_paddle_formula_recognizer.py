"""PP-FormulaNet 本地公式识别测试：预处理纯函数 + 真实模型 + 检测→识别→回填全链路。

模型权重缺失时自动跳过真实模型测试（CI 无权重也可跑纯函数部分）。
"""

from __future__ import annotations

import io
from pathlib import Path
from types import SimpleNamespace

import pytest

np = pytest.importorskip("numpy", reason="numpy 未安装时跳过")
pymupdf = pytest.importorskip("pymupdf", reason="PyMuPDF 未安装时跳过")

from app.pipeline.formula_recognizer import (
    NullRecognizer,
    PaddleFormulaRecognizer,
    get_formula_recognizer,
    preprocess_formula_image,
)
from app.pipeline.layout_detector import PaddleLayoutDetector

PADDLE_DIR = Path(__file__).resolve().parents[2] / "backend" / "models" / "paddle"
FORMULANET_DIR = PADDLE_DIR / "PP-FormulaNet-S_infer"
DOCLAYOUT_DIR = PADDLE_DIR / "PP-DocLayoutV2_infer"
DEMO_FORMULA_PNG = PADDLE_DIR / "demo_formula.png"


# ---------------------------------------------------------------------
# 预处理纯函数
# ---------------------------------------------------------------------


def _rgb_of(image) -> np.ndarray:
    return np.asarray(image.convert("RGB"))


def test_preprocess_shape_and_dtype() -> None:
    from PIL import Image

    image = Image.new("RGB", (640, 120), "white")
    tensor = preprocess_formula_image(_rgb_of(image))
    assert tensor.shape == (1, 1, 384, 384)
    assert tensor.dtype == np.float32
    # 中心白底归一化后为 (1-0.7931)/0.1738 ≈ 1.19；边角为 pad 黑（官方实现 fill=0）≈ -4.56
    assert abs(float(tensor[0, 0, 192, 192]) - (1.0 - 0.7931) / 0.1738) < 0.05
    assert abs(float(tensor[0, 0, 0, 0]) - (0.0 - 0.7931) / 0.1738) < 0.1


def test_preprocess_crops_margin() -> None:
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (400, 400), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((150, 150, 250, 250), fill="black")  # 内容居中，四周大片白边
    tensor = preprocess_formula_image(_rgb_of(image), input_size=384)
    # 裁边后内容为 100x100 -> 短边 384 等比 -> 384x384
    assert tensor.shape == (1, 1, 384, 384)
    # 中心应为内容（黑色 -> 归一化后约 (0-0.7931)/0.1738 ≈ -4.56）
    assert float(tensor[0, 0, 192, 192]) < -3.0


def test_preprocess_wide_formula_keeps_ratio() -> None:
    from PIL import Image

    image = Image.new("RGB", (800, 100), "white")
    tensor = preprocess_formula_image(_rgb_of(image))
    assert tensor.shape == (1, 1, 384, 384)  # 统一填充到 384²


# ---------------------------------------------------------------------
# 配置门控
# ---------------------------------------------------------------------


def test_get_recognizer_off_and_missing_model() -> None:
    assert isinstance(get_formula_recognizer(SimpleNamespace()), NullRecognizer)
    # paddle 档但模型目录缺失 -> Null
    recognizer = get_formula_recognizer(SimpleNamespace(formula_backend="paddle", paddle_formula_model="/no/such/dir"))
    assert isinstance(recognizer, NullRecognizer)


# ---------------------------------------------------------------------
# 真实模型（权重存在时运行）
# ---------------------------------------------------------------------


@pytest.fixture(scope="module")
def formula_crop_png() -> bytes | None:
    """从公式示例图上检测并裁剪一个真实公式，供识别测试复用。"""
    if not (DOCLAYOUT_DIR.exists() and DEMO_FORMULA_PNG.exists()):
        pytest.skip("版面模型/示例图未下载")
    from PIL import Image

    from app.pipeline.layout_detector import formula_regions

    image = Image.open(DEMO_FORMULA_PNG).convert("RGB")
    detector = PaddleLayoutDetector(DOCLAYOUT_DIR, score_threshold=0.3)
    regions = formula_regions(detector.detect(np.asarray(image)))
    if not regions:
        pytest.skip("示例图上未检出公式区域")
    x0, y0, x2, y2 = regions[0].bbox
    crop = image.crop((int(x0), int(y0), int(x2), int(y2)))
    buffer = io.BytesIO()
    crop.save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.mark.skipif(not FORMULANET_DIR.exists(), reason="PP-FormulaNet-S 模型未下载")
def test_real_recognizer_produces_latex(formula_crop_png) -> None:
    recognizer = PaddleFormulaRecognizer(FORMULANET_DIR)
    latex = recognizer.recognize(formula_crop_png)
    assert latex, "真实公式裁剪图应识别出非空 LaTeX"
    # LaTeX 形态 sanity：包含字母/数字，且不含特殊 token 残留
    assert any(ch.isalnum() for ch in latex)
    assert "<unk>" not in latex and "<s>" not in latex
    print("识别结果:", latex[:120])


@pytest.mark.skipif(not (FORMULANET_DIR.exists() and DOCLAYOUT_DIR.exists()), reason="模型未下载")
@pytest.mark.skipif(not DEMO_FORMULA_PNG.exists(), reason="示例图未下载")
def test_full_chain_detection_to_latex_backfill(tmp_path: Path) -> None:
    """全链路：公式图片 PDF -> 版面检测（无文本行 -> 图片型公式区域）-> 裁剪识别 -> $$LaTeX$$ 回填。"""
    from PIL import Image

    # 取一个真实公式裁剪作为 PDF 内嵌图片
    from app.pipeline.layout_detector import formula_regions
    from app.pipeline.parser_backend import PyMuPDFBackend

    image = Image.open(DEMO_FORMULA_PNG).convert("RGB")
    detector = PaddleLayoutDetector(DOCLAYOUT_DIR, score_threshold=0.3)
    regions = formula_regions(detector.detect(np.asarray(image)))
    assert regions, "示例图上应检出公式区域"
    x0, y0, x2, y2 = regions[0].bbox
    crop = image.crop((int(x0), int(y0), int(x2), int(y2)))
    crop_path = tmp_path / "formula_crop.png"
    crop.save(crop_path)

    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 90), "1. Introduction", fontsize=12, fontname="hebo")
    page.insert_text((72, 120), "The key equation is shown below.", fontsize=10)
    page.insert_image(pymupdf.Rect(72, 150, 500, 320), filename=str(crop_path))
    pdf_path = tmp_path / "formula_pdf.pdf"
    doc.save(str(pdf_path))

    backend = PyMuPDFBackend(
        formula_recognizer=PaddleFormulaRecognizer(FORMULANET_DIR),
        layout_detector=PaddleLayoutDetector(DOCLAYOUT_DIR, score_threshold=0.3),
    )
    ir = backend.parse(pdf_path)

    assert ir.ok is True
    # 图片型公式被识别并回填 $$...$$ 到全文
    assert "$$" in ir.text
    image_equations = [n for n in ir.nodes if n.type == "equation" and n.meta.get("source") == "image_formula"]
    assert image_equations, "应产出 source=image_formula 的 equation 节点"
    assert image_equations[0].latex
    assert image_equations[0].page == 1
    print("全链路 LaTeX:", image_equations[0].latex[:120])
