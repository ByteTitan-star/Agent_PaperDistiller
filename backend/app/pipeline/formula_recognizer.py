"""公式识别适配器：裁剪的公式图片 -> LaTeX。

实现（由 settings.formula_backend 选择）：
- "mathpix"：Mathpix v3 HTTP API（效果最好，需申请 app_id/app_key）；
- "pix2text"：本地 Pix2Text 模型（守卫导入，需 pip install pix2text）；
- "paddle"：本地 PP-FormulaNet（paddle inference + 内嵌 tokenizer，免费离线）；
- "off"（默认）：不识别，返回 None（调用方保留原文或图片占位）。

所有适配器 recognize() 失败返回 None，绝不抛异常拖垮解析主流程。
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
from pathlib import Path
from typing import Any, ClassVar, Protocol
from urllib.error import URLError
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

MATHPIX_ENDPOINT = "https://api.mathpix.com/v3/text"


class FormulaRecognizer(Protocol):
    """公式识别统一接口：png 图片字节 -> LaTeX 字符串（失败返回 None）。"""

    name: str

    def recognize(self, png_bytes: bytes) -> str | None: ...


class NullRecognizer:
    """关闭公式识别时的空实现。"""

    name = "off"

    def recognize(self, png_bytes: bytes) -> str | None:
        return None


class MathpixAdapter:
    """Mathpix API 适配器（HTTP，无本地依赖）。

    内置内容哈希缓存：同一张裁剪图重复识别（重跑解析/失败重试）不重复计费。
    """

    name = "mathpix"

    # 进程级缓存：sha1(png) -> latex；容量上限防止长驻进程内存膨胀
    _CACHE_MAX = 512
    _latex_cache: ClassVar[dict[str, str]] = {}

    def __init__(self, app_id: str, app_key: str, timeout: int = 30) -> None:
        if not app_id or not app_key:
            raise ValueError("Mathpix 需要 mathpix_app_id / mathpix_app_key")
        self.app_id = app_id
        self.app_key = app_key
        self.timeout = timeout

    def recognize(self, png_bytes: bytes) -> str | None:
        cache_key = hashlib.sha1(png_bytes).hexdigest()
        cached = MathpixAdapter._latex_cache.get(cache_key)
        if cached is not None:
            return cached

        payload = json.dumps(
            {
                "src": f"data:image/png;base64,{base64.b64encode(png_bytes).decode('ascii')}",
                "formats": ["text"],
                "data_options": {"include_latex": True},
            }
        ).encode("utf-8")
        request = Request(
            MATHPIX_ENDPOINT,
            data=payload,
            headers={
                "app_id": self.app_id,
                "app_key": self.app_key,
                "Content-type": "application/json",
            },
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
        except (URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            logger.warning("[公式识别] Mathpix 请求失败: %s", exc)
            return None
        latex = str(data.get("text", "")).strip()
        if not latex or "error" in data:
            logger.warning("[公式识别] Mathpix 返回异常: %s", str(data)[:200])
            return None
        result = _strip_math_delimiters(latex)
        if len(MathpixAdapter._latex_cache) < MathpixAdapter._CACHE_MAX:
            MathpixAdapter._latex_cache[cache_key] = result
        return result


class Pix2TextAdapter:
    """本地 Pix2Text 模型适配器（守卫导入，模型首次调用时加载）。"""

    name = "pix2text"

    def __init__(self, languages: str = "en") -> None:
        try:
            from pix2text import Pix2Text
        except Exception as exc:
            raise ValueError(f"pix2text 未安装或导入失败: {exc}") from exc
        self._engine = Pix2Text(languages=languages)

    def recognize(self, png_bytes: bytes) -> str | None:
        import io

        try:
            from PIL import Image

            image = Image.open(io.BytesIO(png_bytes))
            result = self._engine.recognize(image)
        except Exception as exc:
            logger.warning("[公式识别] Pix2Text 失败: %s", exc)
            return None
        latex = str(result).strip()
        return latex or None


def _strip_math_delimiters(latex: str) -> str:
    """去掉 Mathpix 输出外层的 \\[...\\] / \\(...\\) 定界符。"""
    text = latex.strip()
    if text.startswith("\\[") and text.endswith("\\]"):
        return text[2:-2].strip()
    if text.startswith("\\(") and text.endswith("\\)"):
        return text[2:-2].strip()
    if text.startswith("$$") and text.endswith("$$"):
        return text[2:-2].strip()
    return text


def _paddle_available() -> bool:
    try:
        import paddle
        import paddle.inference  # noqa: F401
        from tokenizers import Tokenizer  # noqa: F401
    except Exception:
        return False
    return True


def _cfg(settings: Any, name: str, default: Any) -> Any:
    value = getattr(settings, name, None)
    return default if value is None else value


class PaddleFormulaRecognizer:
    """本地 PP-FormulaNet 识别器（paddle inference + 内嵌 tokenizer，无外部 API）。

    模型包为 PaddleX 官方推理包（inference.json/pdiparams/yml），导出图内置自回归生成：
    输入 (1,1,384,384) 归一化灰度图，输出 token id 序列；tokenizer（5 万词表 BPE）
    内嵌于 inference.yml 的 fast_tokenizer_file，加载时导出为临时 json 供 tokenizers 使用。

    预处理复刻 PaddleX UniMERNet 管线（cv2-free 实现）：
    裁边（灰度阈值<200）-> 短边 384 等比缩放 -> 384×384 居中填充
    -> (x/255-0.7931)/0.1738 -> 加权灰度 -> 单通道。
    """

    name = "paddle-formulanet"
    INPUT_SIZE = 384
    NORM_MEAN = 0.7931
    NORM_STD = 0.1738

    def __init__(self, model_dir: str | Path, timeout: int = 60) -> None:
        import paddle.inference as paddle_inference

        self.model_dir = Path(model_dir)
        program = self.model_dir / "inference.json"
        params = self.model_dir / "inference.pdiparams"
        if not (program.exists() and params.exists()):
            raise ValueError(f"PP-FormulaNet 模型文件缺失: {self.model_dir}")

        config = paddle_inference.Config(str(program), str(params))
        config.disable_gpu()
        config.disable_glog_info()
        self._predictor = paddle_inference.create_predictor(config)
        self._tokenizer = self._load_tokenizer()

    def _load_tokenizer(self):
        import json
        import tempfile

        import yaml
        from tokenizers import Tokenizer

        with (self.model_dir / "inference.yml").open(encoding="utf-8") as handle:
            yml = yaml.safe_load(handle)
        fast_tokenizer = yml["PostProcess"]["character_dict"]["fast_tokenizer_file"]
        # tokenizers 库只支持从文件加载，导出到临时文件（每次初始化一次）
        tmp_path = Path(tempfile.mkstemp(suffix=".json")[1])
        try:
            with tmp_path.open("w", encoding="utf-8") as handle:
                json.dump(fast_tokenizer, handle, ensure_ascii=False)
            return Tokenizer.from_file(str(tmp_path))
        finally:
            try:
                tmp_path.unlink()
            except OSError:
                pass

    def recognize(self, png_bytes: bytes) -> str | None:
        try:
            import io

            import numpy as np
            from PIL import Image

            image = Image.open(io.BytesIO(png_bytes)).convert("RGB")
            tensor = preprocess_formula_image(np.asarray(image), self.INPUT_SIZE, self.NORM_MEAN, self.NORM_STD)
            input_name = self._predictor.get_input_names()[0]
            self._predictor.get_input_handle(input_name).copy_from_cpu(tensor)
            self._predictor.run()
            ids = self._predictor.get_output_handle(self._predictor.get_output_names()[0]).copy_to_cpu()
            latex = self._tokenizer.decode(ids[0].tolist(), skip_special_tokens=True)
            latex = _strip_math_delimiters(latex).strip()
            if not latex or len(latex) > 2000:
                return None
            return latex
        except Exception as exc:
            logger.warning("[公式识别] PP-FormulaNet 失败: %s", exc)
            return None


def preprocess_formula_image(
    image_rgb: Any,
    input_size: int = 384,
    norm_mean: float = 0.7931,
    norm_std: float = 0.1738,
) -> Any:
    """RGB uint8 (H,W,3) -> (1,1,S,S) float32（PaddleX UniMERNet 预处理，cv2-free）。

    纯函数：可直接单测（形状/数值范围/裁边行为）。
    """
    import numpy as np
    from PIL import Image, ImageOps

    # 1) 裁边：灰度 <200 的最小包围盒（复刻 UniMERNetImgDecode.crop_margin）
    gray = np.asarray(Image.fromarray(image_rgb).convert("L"), dtype=np.uint8)
    if int(gray.max()) != int(gray.min()):
        stretched = (gray.astype(np.float32) - float(gray.min())) / max(1e-6, float(gray.max()) - gray.min()) * 255
        mask = stretched < 200
        rows, cols = np.nonzero(mask)
        if len(rows) > 0 and len(cols) > 0:
            image_rgb = image_rgb[rows.min() : rows.max() + 1, cols.min() : cols.max() + 1]

    # 2) 短边缩放到 input_size（等比）
    image = Image.fromarray(image_rgb)
    width, height = image.size
    if width <= height:
        new_w, new_h = input_size, max(1, round(input_size * height / width))
    else:
        new_w, new_h = max(1, round(input_size * width / height)), input_size
    resample = getattr(Image, "Resampling", Image).BILINEAR
    image = image.resize((new_w, new_h), resample=resample)
    image.thumbnail((input_size, input_size), resample=resample)

    # 3) 居中填充到 input_size×input_size
    delta_w, delta_h = input_size - image.width, input_size - image.height
    image = ImageOps.expand(image, (delta_w // 2, delta_h // 2, delta_w - delta_w // 2, delta_h - delta_h // 2))

    # 4) 归一化 + 加权灰度（位置权重复刻 cv2.COLOR_BGR2GRAY 作用于 RGB 数组的行为）
    arr = np.asarray(image, dtype=np.float32) / 255.0
    arr = (arr - norm_mean) / norm_std
    gray_norm = 0.114 * arr[:, :, 0] + 0.587 * arr[:, :, 1] + 0.299 * arr[:, :, 2]
    tensor = gray_norm[np.newaxis, np.newaxis, :, :].astype(np.float32)
    return np.ascontiguousarray(tensor)


__all__ = [
    "FormulaRecognizer",
    "MathpixAdapter",
    "NullRecognizer",
    "Pix2TextAdapter",
    "get_formula_recognizer",
]


def get_formula_recognizer(settings: Any | None = None) -> FormulaRecognizer:
    """按 settings.formula_backend 构造识别器；不可用时降级为 NullRecognizer。

    paddle：本地 PP-FormulaNet（需 paddlepaddle + tokenizers + 模型目录），
    是 Mathpix 的免费本地替代。
    """
    backend = str(_cfg(settings, "formula_backend", "off") or "off").lower()
    if backend == "mathpix":
        try:
            return MathpixAdapter(
                app_id=str(_cfg(settings, "mathpix_app_id", "") or ""),
                app_key=str(_cfg(settings, "mathpix_app_key", "") or ""),
            )
        except ValueError as exc:
            logger.warning("[公式识别] Mathpix 配置缺失，公式识别关闭: %s", exc)
    elif backend == "pix2text":
        try:
            return Pix2TextAdapter()
        except ValueError as exc:
            logger.warning("[公式识别] Pix2Text 不可用，公式识别关闭: %s", exc)
    elif backend == "paddle":
        if not _paddle_available():
            logger.warning("[公式识别] paddlepaddle/tokenizers 未安装，公式识别关闭")
        else:
            model_dir = Path(str(_cfg(settings, "paddle_formula_model", "backend/models/paddle/PP-FormulaNet-S_infer")))
            if not model_dir.exists():
                logger.warning("[公式识别] PP-FormulaNet 模型目录不存在（%s），公式识别关闭", model_dir)
            else:
                try:
                    return PaddleFormulaRecognizer(model_dir=model_dir)
                except Exception as exc:
                    logger.warning("[公式识别] PP-FormulaNet 加载失败，公式识别关闭: %s", exc)
    return NullRecognizer()


__all__ = [
    "FormulaRecognizer",
    "MathpixAdapter",
    "NullRecognizer",
    "PaddleFormulaRecognizer",
    "Pix2TextAdapter",
    "get_formula_recognizer",
    "preprocess_formula_image",
]
