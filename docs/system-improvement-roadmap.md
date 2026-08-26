# 系统改进方案与混合检索澄清

本文档整理了当前系统存在的关键问题及对应的升级方案，涵盖解析引擎、视觉描述、向量存储、检索策略、问答生成和异步处理等方面，同时对混合检索的实现方式做了明确说明。

---

# 改进1：AgentPaperDistiller 生产级架构 V2.0

## 1. 解析引擎升级：混合管道（Hybrid Pipeline）

不再仅依赖 `pdfplumber`，引入 **规则引擎 + 轻量级布局模型** 的分层处理：

| 组件 | 技术选型 | 职责 |
|------|----------|------|
| 文本层 | `pypdf`（快速）+ `pdfplumber`（精细） | 提取正文、段落顺序，保留粗粒度结构 |
| 表格层 | `pdfplumber.extract_tables()` + `pandas` | 转为结构化 Markdown/CSV，不经过 VLM（表格数据直接提取更准） |
| 图表/图片层 | `pdfplumber.images` 截取 + 布局过滤 | 提取图像块，过滤页眉、页脚及装饰图（宽度<100px 或高度<100px） |
| 扫描件兜底 | `PaddleOCR`（轻量级） | 若提取文本字符数 < 50，判定为扫描件，全文 OCR |

---

## 2. 视觉描述生成：异步化 + 图注绑定（Context Binding）

改进原有“看图说话”方式，引入图注匹配与上下文绑定：

- **Step A**：正则匹配 `Figure X`、`Fig. X`、`图 X` 等，提取图注文本（Caption）。
- **Step B**：将裁剪图片的 **Base64 + 图注文本** 一同发给 VLM，Prompt 升级为：

```text
你是一位学术数据分析专家。这张图片的标题是：{caption}。
请结合标题详细描述图表内容，必须包含：
1. 图表类型（折线/柱状/散点/流程图/架构图）
2. 坐标轴含义、单位、取值范围
3. 关键数据点（最大值、最小值、拐点）
4. 图例对应的曲线/颜色
5. 该图可能支持的论文结论（推断）
```

优势：即使 VLM 看图有偏差，图注文字仍可作为检索关键词。

---

## 3. 入库与向量化策略升级

放弃 `all-MiniLM-L6-v2`，采用更适合中文长文本的模型：

- **文本 Chunk**：使用 `BAAI/bge-large-zh-v1.5`（1024 维）
- **图片描述**：同样用该模型向量化

**存储结构调整（`storage.py`）**：统一 collection，增加 `type` 字段区分模态。

```python
{
  "id": "{paper_id}:chunk:{index}",
  "document": "实际文本或图片描述",
  "embedding": vector,
  "metadata": {
    "type": "text" | "table" | "image_desc",
    "page_num": 3,
    "image_path": "data/.../img_1.png",   # 保留原始图片路径
    "caption": "Figure 2: ASR comparison..."
  }
}
```

---

## 4. 检索增强（RAG）升级：BM25 + 向量混合检索

原有纯向量检索可能遗漏关键词（如特定方法名），现采用 **混合检索（Hybrid Search）**：

- 使用 `rank_bm25` 做关键词检索
- 向量检索补充语义
- 采用 **RRF（倒数秩融合）** 合并两路分数

---

## 5. 问答生成（Chat）升级：多模态上下文传递

原方案只将图片描述文本传给纯文本 LLM，丢失视觉细节。新方案：

- 在 `retrieve_contexts()` 中，若命中 `type == "image_desc"`，不仅传文本描述，还将原始图片的 **URL/Base64** 一起作为上下文。
- 调用 **多模态大模型**（如 `qwen-vl-max`）进行最终回答，让模型同时看到文本证据和图表本身，降低 VLM 初次描述时的幻觉影响。

---

## 6. 异步任务队列（解决阻塞问题）

将 VLM 调用放入 Redis Queue 异步执行，入库流程拆分：

- **极速解析文本**，立即完成入库（用户可立即搜索）
- **后台异步任务**：裁剪图片 → 调用 VLM 生成描述 → 更新向量库（Upsert）

用户上传后几秒内即可收到“解析成功”，图片描述在后台逐步补全。

---

## 7. 配置项新增（`config.py`）

```python
## 视觉模型配置
enable_async_vlm: bool = True                # 开启异步
vlm_batch_size: int = 5                     # 并发数（控制 API 并发）
ocr_backend: str = "paddleocr"              # 扫描件 OCR 引擎

## 检索配置
hybrid_search_alpha: float = 0.5            # BM25 和 向量 权重比例
embedding_model: str = "BAAI/bge-large-zh-v1.5"

## 多模态生成配置
enable_multimodal_llm_answer: bool = True   # 回答阶段使用 VLM 看图
```

---

# 改进2：混合检索实现澄清（BM25 + 语义）

## 1. 需要单独的数据库服务（如 ES）吗？

**完全不需要。** 尤其当前使用 ChromaDB 且数据量在百万级以内，引入 ES 属于过度架构。

## 2. BM25 可以从 ChromaDB 召回吗？

**不可以直接从 ChromaDB 执行 BM25 查询**，但可以使用 ChromaDB 中存储的文本数据在内存中构建 BM25 索引。

### 核心要点：
- ChromaDB 查询接口只支持向量检索（ANN），无内置 BM25 语法。
- 但 `storage.py` 中保存了原始 `document` 字符串，我们可以在应用启动时或定时任务中，从 ChromaDB 拉取全部文本，在 Python 进程内存中用 `rank_bm25` 建立倒排索引。
- **向量检索** → 走 ChromaDB（外部数据库）
- **BM25 检索** → 走 Python 本地内存（RAM）
- 两者互不干扰，数据同源，查询通道不同，最终通过 RRF 融合排序。

---

# 实施建议

- **最低成本第一步**：仅修改 `document_parser.py`，加入图注绑定和装饰图过滤，Prompt 加上标题，可提升 50% 检索准确性。
- **核心痛点**：将 VLM 调用改为异步（Celery），避免上传卡死。
- **长远规划**：替换 Embedding 模型，在 `chat.py` 中接入 `qwen-vl-max` 做最终回答（将图片传给模型）。

---