#!/usr/bin/env bash
# 下载本地解析增强模型到 backend/models/paddle/。
#
# 权重分发策略：
# - PP-DocLayout-S（4MB 轻量版面检测，公式区域定位）已随仓库分发，克隆即用；
# - PP-DocLayoutV2（203MB，更高精度，区分独立/行内公式）与 PP-FormulaNet-S
#   （223MB，本地公式->LaTeX 识别，替代 Mathpix）为可选增强，经本脚本从
#   PaddleX 官方 BOS（国内直连）下载；模型目录不依赖 git。
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

# 可选增强 1：高精度版面检测（layout_detector_model 指向 PP-DocLayoutV2_infer）
fetch "PP-DocLayoutV2"
# 可选增强 2：本地公式识别（.env 设 formula_backend=paddle）
fetch "PP-FormulaNet-S"

# 集成测试用的公式示例图（若仓库内缺失）
if [ ! -f "$DIR/demo_formula.png" ]; then
  curl -fL --retry 3 -o "$DIR/demo_formula.png" \
    "https://paddle-model-ecology.bj.bcebos.com/paddlex/demo_image/general_formula_recognition.png"
fi

echo "完成。默认启用：.env 配置 layout_detector=doclayout（另需 pip install paddlepaddle tokenizers pillow）"
echo "增强档：layout_detector_model=backend/models/paddle/PP-DocLayoutV2_infer / formula_backend=paddle"
