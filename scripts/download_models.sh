#!/usr/bin/env bash
# 下载本地解析增强模型（公式区域检测 / 公式识别）到 backend/models/paddle/。
# 模型源：PaddleX 官方 BOS（国内直连）；SHA 校验可按需追加。
set -euo pipefail
cd "$(dirname "$0")/.."

BASE="https://paddle-model-ecology.bj.bcebos.com/paddlex/official_inference_model/paddle3.0.0"
DIR="backend/models/paddle"
mkdir -p "$DIR"

fetch() {
  local name="$1"
  if [ -d "$DIR/${name}_infer" ]; then
    echo "[skip] ${name} 已存在"
    return
  fi
  echo "[download] ${name} ..."
  curl -fL --retry 3 -o "$DIR/${name}_infer.tar" "${BASE}/${name}_infer.tar"
  tar xf "$DIR/${name}_infer.tar" -C "$DIR"
  rm -f "$DIR/${name}_infer.tar"
}

# 公式区域检测（MFD）：默认用 V2（203MB，区分 display/inline 公式）
fetch "PP-DocLayoutV2"
# 轻量备选（4MB，含 formula 类）：内存受限环境可改用
# fetch "PP-DocLayout-S"
# 公式识别（图像 -> LaTeX，223MB，本地替代 Mathpix）：formula_backend=paddle 时使用
fetch "PP-FormulaNet-S"

# 集成测试用的公式示例图
if [ ! -f "$DIR/demo_formula.png" ]; then
  curl -fL --retry 3 -o "$DIR/demo_formula.png" \
    "https://paddle-model-ecology.bj.bcebos.com/paddlex/demo_image/general_formula_recognition.png"
fi

echo "完成。启用：.env 配置 layout_detector=doclayout + formula_backend=paddle（另需 pip install paddlepaddle tokenizers pillow）"
