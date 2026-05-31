# AgentPaperDistiller - 待完善功能清单

## 1. PDF 解析升级：从 PyPDF 到 pdfplumber + 视觉模型

### 现状
当前使用 `pypdf` 做 PDF 文本提取，存在以下不足：
- 只能提取纯文本，无法识别图片和表格
- 对复杂排版（多栏、公式、图表）的提取质量一般
- 扫描版 PDF 完全无法处理（所有页面返回空）

### 目标方案
用 `pdfplumber` 替换 `pypdf`，增加图片/表格提取能力，并用视觉模型生成图片描述。

### 具体步骤

#### Step 1: 替换 PDF 解析引擎
- 依赖：`pip install pdfplumber Pillow`
- 文件：`backend/app/pipeline/document_parser.py`
- 将 `extract_text_from_pdf()` 改用 `pdfplumber.open()` 实现
- pdfplumber 对复杂排版的文本提取质量显著优于 pypdf
- 可用 `page.extract_tables()` 提取表格数据为结构化格式

#### Step 2: 提取图片块和表格块
- 使用 `pdfplumber` 的 `page.images` 获取图片坐标 (x0, y0, x1, y1)
- 使用 `page.crop((x0, y0, x1, y1)).to_image()` 按坐标裁剪页面区域为 PNG
- 保存到 `data/processed/{paper_id}/images/` 目录
- 提取表格：`page.extract_tables()` -> 转为 Markdown 表格文本
- 记录每张图片/表格的页码和周边文本上下文

#### Step 3: 视觉模型生成图片描述
- 模型：`qwen-vl-max` 或 `qwen-vl-plus`（DashScope API，与现有 Qwen 共用 base_url 和 api_key）
- 将裁剪后的 PNG 图片转 base64，通过 OpenAI 兼容接口发送给视觉模型
- Prompt：`"请用中文详细描述这张学术论文中的图片/图表内容，包括图表类型、坐标轴含义、关键数据趋势、重要结论"`
- 生成描述文本，格式示例：`[图片 - Page 3] Figure 2: 不同攻击方法在 CIFAR-10 上的 ASR 对比折线图。横轴为 poison rate (1%-10%)，纵轴为 ASR (%)。方法A 在 5% poison rate 时达到 95% ASR...`

#### Step 4: 图片描述存入向量数据库
- 文件：`backend/app/storage.py`
- 在 VectorStore 中新增 `image_chunks` collection（独立于 `paper_chunks`）
- 每条记录：
  - `id`: `{paper_id}:img:{index}`
  - `document`: 图片描述文本
  - `embedding`: sentence-transformers 对描述文本的向量化
  - `metadata`: `{paper_id, page_num, image_path, type: "image"}`
- 使用同一个 sentence-transformers 模型（all-MiniLM-L6-v2），与文本 chunk 共享向量空间

#### Step 5: 检索增强
- 文件：`backend/app/services/chat.py`
- `retrieve_contexts()` 增加从 `image_chunks` collection 检索
- 将命中的图片描述拼入上下文，LLM 回答时可引用图片内容
- 格式：`[Context N - 图片描述 (Page X)] {描述文本}`

### 配置项（config.py）
```python
enable_image_extraction: bool = False      # 开关，默认关闭
qwen_vl_model: str = "qwen-vl-max"        # 视觉模型 ID
image_max_size: int = 1024                 # 图片最大边长
image_min_size: int = 50                   # 过滤过小的装饰图片
image_context_window: int = 200            # 图片周边文本上下文字符数
```

### 注意事项
- 需要额外安装 `pdfplumber` 和 `Pillow`，`requirements.txt` 中添加
- 视觉模型调用有成本，建议加开关控制（`enable_image_extraction`）
- 裁剪图片时过滤过小的图片（如 icon、装饰性图形），只保留有意义的图表
- 图片描述向量化复用现有 sentence-transformers，不引入额外的 CLIP 模型以控制部署成本

---

## 2. 其他待完善功能

### 2.1 TaskRecord 状态持久化
- 当前 TaskRecord 仅在 upload 时创建初始记录，pipeline 执行过程中状态仅存于内存（TaskBroker）
- 待改进：在 pipeline 各阶段切换时同步更新 TaskRecord 表，保证服务重启后状态不丢失

### 2.2 翻译引擎可配置化
- 当前硬编码使用 Google Translate 非官方 API
- 待改进：支持选择翻译引擎（Google / DeepL / LLM 翻译），通过配置切换

### 2.3 多模态聊天
- 当前聊天仅支持文本问答
- 待改进：支持在聊天中发送图片截图，结合视觉模型进行图文问答

### 2.4 导出功能
- 待改进：支持将翻译、摘要、创新建议导出为 PDF/Word 格式

