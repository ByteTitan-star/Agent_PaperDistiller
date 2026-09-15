"""版面公式检测（MFD 升级）：PaddleX PP-DocLayout 系列 + paddle inference（CPU）。

背景（见 todo.md Phase 9）：
- 旧方案是"数学字形密度启发式"（math_glyph_ratio），召回有限；
- 本模块用官方轻量版面检测模型做公式区域检测，替代启发式：
  * PP-DocLayoutV2（DETR，约 203MB）：23 类，区分 display_formula / inline_formula；
  * PP-DocLayout-S（PicoDet/GFL，约 4MB）：24 类，含 formula（轻量首选，几百万参数量级）。
- 模型目录为 BOS 官方推理包（inference.json + inference.pdiparams + inference.yml），
  输出为 PaddleX 检测导出标准格式：每行 [class_id, score, x1, y1, x2, y2, ...]，
  坐标为输入图像像素坐标（scale_factor 已在图内消化）。

默认关闭（layout_detector=off），paddle/模型目录缺失时优雅降级为启发式。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

logger = logging.getLogger(__name__)

# 视为"公式区域"的类别（display 级别才整行送识别；inline 交给字形启发式，避免误吞正文）
FORMULA_REGION_LABELS = frozenset({"formula", "display_formula"})
# 全部公式相关类别（inline_formula 用于统计/未来细粒度处理）
ALL_FORMULA_LABELS = frozenset({"formula", "display_formula", "inline_formula"})


@dataclass
class RegionDetection:
    """单个版面检测框（输入图像像素坐标 xyxy）。"""

    bbox: tuple[float, float, float, float]
    label: str
    score: float


class LayoutDetector(Protocol):
    """版面检测器接口：RGB 图像 -> 检测框列表。"""

    name: str

    def detect(self, image_rgb: Any) -> list[RegionDetection]: ...


def _cfg(settings: Any, name: str, default: Any) -> Any:
    value = getattr(settings, name, None)
    return default if value is None else value


def load_inference_config(model_dir: Path) -> dict:
    """读取 PaddleX 推理包的 inference.yml（目标尺寸/归一化参数/标签表）。"""
    import yaml

    yml_path = Path(model_dir) / "inference.yml"
    with yml_path.open(encoding="utf-8") as handle:
        config = yaml.safe_load(handle) or {}

    target_size = (800, 800)
    mean = (0.0, 0.0, 0.0)
    std = (1.0, 1.0, 1.0)
    is_scale = True
    for step in config.get("Preprocess", []) or []:
        if step.get("type") == "Resize":
            target_size = tuple(step.get("target_size") or target_size)  # type: ignore[assignment]
        elif step.get("type") == "NormalizeImage":
            mean = tuple(step.get("mean") or mean)  # type: ignore[assignment]
            std = tuple(step.get("std") or std)  # type: ignore[assignment]
            is_scale = bool(step.get("is_scale", True))
    return {
        "target_size": target_size,
        "mean": mean,
        "std": std,
        "is_scale": is_scale,
        "label_list": list(config.get("label_list") or []),
    }


def preprocess_image(image_rgb: Any, target_size: tuple[int, int], mean: tuple, std: tuple, is_scale: bool) -> Any:
    """RGB uint8 图像 -> 模型输入 (1,3,H,W) float32（Resize + /255 + Normalize + CHW）。"""
    import numpy as np
    from PIL import Image

    image = Image.fromarray(image_rgb).resize((target_size[1], target_size[0]))
    arr = np.asarray(image, dtype=np.float32)
    if is_scale:
        arr = (arr / np.float32(255.0)).astype(np.float32)
    mean_arr = np.asarray(mean, dtype=np.float32)
    std_arr = np.asarray(std, dtype=np.float32)
    arr = ((arr - mean_arr) / std_arr).astype(np.float32)
    arr = arr.transpose(2, 0, 1)  # HWC -> CHW
    return np.ascontiguousarray(arr[None, ...])  # type: ignore[no-any-return]


def decode_paddlex_detections(
    output: Any,
    label_list: list[str],
    score_threshold: float,
) -> list[RegionDetection]:
    """解码 PaddleX 检测导出输出 [N, >=6]（class_id, score, x1, y1, x2, y2）。

    纯函数：无模型依赖，直接单测。
    """

    detections: list[RegionDetection] = []
    if output is None or len(output) == 0:
        return detections
    for row in output:
        if len(row) < 6:
            continue
        cls_id = round(float(row[0]))
        score = float(row[1])
        if score < score_threshold:
            continue
        if not (0 <= cls_id < len(label_list)):
            continue
        x1, y1, x2, y2 = (float(row[2]), float(row[3]), float(row[4]), float(row[5]))
        detections.append(
            RegionDetection(
                bbox=(min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)),
                label=label_list[cls_id],
                score=score,
            )
        )
    detections.sort(key=lambda d: d.score, reverse=True)
    return detections


class PaddleLayoutDetector:
    """PP-DocLayout 系列版面检测器（paddle inference，CPU，守卫导入）。"""

    name = "paddle-doclayout"

    def __init__(self, model_dir: Path | str, score_threshold: float = 0.3) -> None:
        import numpy as np  # noqa: F401
        import paddle.inference as paddle_inference

        self.model_dir = Path(model_dir)
        program = self.model_dir / "inference.json"
        params = self.model_dir / "inference.pdiparams"
        if not program.exists():
            program = self.model_dir / "inference.pdmodel"  # 旧版格式兼容
        if not (program.exists() and params.exists()):
            raise ValueError(f"版面检测模型文件缺失: {self.model_dir}")

        self._score_threshold = float(score_threshold)
        self._config = load_inference_config(self.model_dir)
        self._label_list = self._config["label_list"]

        config = paddle_inference.Config(str(program), str(params))
        config.disable_gpu()
        config.disable_glog_info()
        self._predictor = paddle_inference.create_predictor(config)
        self._input_names = self._predictor.get_input_names()
        logger.info(
            "[版面检测] ✅ 模型加载 | dir=%s | labels=%d | target=%s",
            self.model_dir.name,
            len(self._label_list),
            self._config["target_size"],
        )

    def detect(self, image_rgb: Any) -> list[RegionDetection]:
        """RGB uint8 (H,W,3) -> 检测框列表（输入图像像素坐标）。"""
        import numpy as np

        height, width = image_rgb.shape[:2]
        tensor = preprocess_image(
            image_rgb,
            self._config["target_size"],
            self._config["mean"],
            self._config["std"],
            self._config["is_scale"],
        )
        im_shape = np.array([[height, width]], dtype=np.float32)
        # PaddleDetection 约定：scale_factor = 原始尺寸/推理尺寸（模型坐标 / scale_factor = 原图坐标）
        scale_y = height / max(1, self._config["target_size"][0])
        scale_x = width / max(1, self._config["target_size"][1])
        scale_factor = np.array([[scale_y, scale_x]], dtype=np.float32)

        feeds = {"image": tensor, "im_shape": im_shape, "scale_factor": scale_factor}
        for name in self._input_names:
            self._predictor.get_input_handle(name).copy_from_cpu(feeds[name])
        self._predictor.run()
        output = self._predictor.get_output_handle(self._predictor.get_output_names()[0]).copy_to_cpu()
        detections = decode_paddlex_detections(output, self._label_list, self._score_threshold)

        # 实测（PP-DocLayoutV2 导出图）：输出为 resize 后的模型坐标，需自行映射回原图
        target_h, target_w = self._config["target_size"]
        scale_x = width / max(1, target_w)
        scale_y = height / max(1, target_h)
        for det in detections:
            x1, y1, x2, y2 = det.bbox
            det.bbox = (x1 * scale_x, y1 * scale_y, x2 * scale_x, y2 * scale_y)
        return detections


def _paddle_available() -> bool:
    try:
        import paddle
        import paddle.inference  # noqa: F401
    except Exception:
        return False
    return True


def get_layout_detector(settings: Any | None = None) -> LayoutDetector | None:
    """按配置构造版面检测器；off / paddle 缺失 / 模型目录缺失时返回 None（回退启发式）。"""
    backend = str(_cfg(settings, "layout_detector", "off") or "off").lower()
    if backend != "doclayout":
        return None
    if not _paddle_available():
        logger.info("[版面检测] paddle 未安装，公式区域检测回退字形启发式")
        return None
    model_dir = Path(str(_cfg(settings, "layout_detector_model", "backend/models/paddle/PP-DocLayoutV2_infer")))
    if not model_dir.exists():
        logger.info("[版面检测] 模型目录不存在（%s），回退字形启发式", model_dir)
        return None
    try:
        return PaddleLayoutDetector(
            model_dir=model_dir,
            score_threshold=float(_cfg(settings, "layout_detector_score", 0.3)),
        )
    except Exception as exc:
        logger.warning("[版面检测] 模型加载失败，回退字形启发式: %s", exc)
        return None


def formula_regions(detections: list[RegionDetection]) -> list[RegionDetection]:
    """过滤出 display 级公式区域（用于整行标记送识别）。"""
    return [d for d in detections if d.label in FORMULA_REGION_LABELS]


__all__ = [
    "ALL_FORMULA_LABELS",
    "FORMULA_REGION_LABELS",
    "LayoutDetector",
    "PaddleLayoutDetector",
    "RegionDetection",
    "decode_paddlex_detections",
    "formula_regions",
    "get_layout_detector",
    "load_inference_config",
    "preprocess_image",
]
