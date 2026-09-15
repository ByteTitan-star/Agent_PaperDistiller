# TODO — 文档解析管线重构（Document Ingestion Pipeline）

> 目标架构：`Parser Layer → Canonical Document IR → Structure-aware Chunker → Embedding → ChromaDB`
>
> 核心原则（来自架构评审，已采纳）：
> 1. **不要让 Parser 输出直接绑死 ChromaDB**，中间必须有统一 Document IR，换 parser 下游不动；
> 2. **PDF 不能无脑全文 OCR**，有文本层的 PDF 直接取字，OCR 只兜底"无可靠文字层"的场景；
> 3. **解析结果必须持久化**，调 chunk 策略 / embedding 模型时不需要重跑解析；
> 4. **Chunk 不允许固定长度硬切**，结构优先（标题/段落/公式/表格原子），token 上限只是约束。

## 背景问题（现状诊断）

| # | 问题 | 位置 |
|---|------|------|
| 1 | pypdf 纯文本抽取：公式乱码、表格散架、双栏顺序错乱、扫描件直接失败 | `app/pipeline/document_parser.py` |
| 2 | "未提取到可读文本" 错误字符串被当正文流入翻译/摘要 | `document_parser.py:102` |
| 3 | 章节识别靠 20 词硬编码词表 + 正则，标题识别不到就并段 | `split_text_into_sections` |
| 4 | 固定 900 字符窗口切块，空白全部压平，公式/表格被腰斩 | `chunk_text` |
| 5 | 4 个步骤各自重新解析一遍 PDF，翻译跑了 3 遍，无产物复用 | `harness/pipeline/orchestrator.py` |
| 6 | Google 非官方翻译接口，术语/公式必毁 | `pipeline/translator.py` |
| 7 | Chroma chunk 只有 paper_id/chunk_index 元数据，无法按类型过滤（参考文献污染检索） | `storage.py:VectorStore` |
| 8 | `token.md` 硬编码 Windows 路径 `D:\Z-Desktop\...` | `pipeline/common_utils.py:12` |

---

## Phase 0 — 管线工程修复（P0，无新重依赖，纯收益）

- [x] **0.1 DocumentIR 数据模型**：`document_ir.py`
      `DocNode`（type=heading/paragraph/equation/table/figure/reference，含 page/level/latex/html/caption/section_path/bbox）+ `PreflightReport` + `DocumentIR`（含阅读序全文、兼容用 sections、节点列表、解析报告），支持 JSON 往返持久化。
- [x] **0.2 结构感知分块**：`chunk_sections()`
      按章节切块（不再压平全文）；`$$...$$` 公式块与 Markdown 表格为原子单元不可切断；块内保留标题路径上下文；保留旧 `chunk_text` 兼容。
- [x] **0.3 解析一次 + 产物持久化**：orchestrator 改造
      parse 步骤产出 `parse_artifact.json`（DocumentIR），translate/summarize/critique 只加载产物（缺失时才兜底重解析）；翻译产物 `translated_sections.json` 同样持久化复用，全文翻译从 3 次降为 1 次。
- [x] **0.4 错误传播**：解析失败（扫描件/加密/损坏）不再把错误字符串当正文，parse 步骤返回 `ok=False`，worker 走失败路径并给出明确原因。
- [x] **0.5 token.md 路径修复**：`TOKEN_MD_PATH` 环境变量可覆盖，默认落到 `data/token.md`，删除硬编码 Windows 路径。

## Phase 1 — 解析引擎层（P1，ParserRouter + Preflight + PyMuPDF）

- [x] **1.1 Preflight 预检查**：`preflight.py`
      用 PyMuPDF 快速判定：页数 / 是否有文本层 / 无文本页占比（scanned_ratio）/ 是否加密 / 疑似双栏 / 图片覆盖，输出 `parser_route` 建议（native / scanned / fallback）。
- [x] **1.2 PyMuPDF 解析后端**：`parser_backend.PyMuPDFBackend`
      替代 pypdf 成为原生 PDF 主通道：块级双栏阅读顺序（先左栏后右栏）、字号+正则双通道标题识别、断词连字符修复（`soft-\nware` → `software`）、`find_tables` 表格转 Markdown（避免正文重复）、图片节点登记。
- [x] **1.3 PypdfBackend 兜底**：现有抽取逻辑封装为 backend 接口实现，PyMuPDF 不可用/失败时自动降级，零新依赖也能跑。
- [x] **1.4 ParserRouter 自动路由**：`parser_backend.parser_backend` 配置（auto/pymupdf/pypdf/mineru）；auto = Preflight 判定 → 原生 PDF 走 PyMuPDF、扫描件走 OCR/MinerU（若启用）→ 全部失败降级 pypdf。
- [x] **1.5 Chroma chunk 元数据增强**：chunk 携带 `element_type / page / section` 元数据入向量库；BM25/向量检索默认排除 `reference` 类型块；`chunks_meta.json` 随 chunks 持久化。

## Phase 2 — 模型能力层（P2，可选依赖 + 配置开关 + 优雅降级）

- [x] **2.1 MinerU 后端适配器**：`MinerUBackend`
      面向复杂论文 PDF（双栏+公式+表格），CLI 方式调用（`mineru` 可执行文件探测），解析其 `content_list.json`/Markdown 输出为 DocumentIR（公式→LaTeX、表格→HTML）。未安装/未启用时自动跳过，`parser_mineru_enabled` 控制。
- [x] **2.2 PaddleOCR 扫描件后端**：`PaddleOCRBackend`
      PyMuPDF 渲染页面 → PaddleOCR 识别 → 按 "第 N 页" 组装文本。守卫导入，`parser_ocr_enabled` 控制，未启用时扫描件明确报错而非静默空文本。
- [x] **2.3 公式转 LaTeX 适配器**：`formula_recognizer.py`
      统一接口 `FormulaRecognizer.recognize(png_bytes) -> latex`；实现 MathpixAdapter（HTTP API，`formula_backend=mathpix`）与 Pix2TextAdapter（本地模型，守卫导入）；默认 off，逐块失败不影响主流程。
- [x] **2.4 Quality Gate**：解析结果质量评分（有效字符数/页、标题命中数、空页率），低分自动降级重试下一引擎并在 report.warnings 记录。

## Phase 3 — 下游升级（P3）

- [x] **3.1 LLM 翻译通道**：`llm_translator.py`
      `translation_provider = auto | llm | google`；LLM 通道复用 DeepSeek/OpenAI 兼容配置，prompt 强约束：保留 `$...$`/`$$...$$`/Markdown 表格/术语不译；并发受限（`translation_llm_concurrency`）；Google 接口降级为 fallback，翻译失败仍回退原文。
- [x] **3.2 检索过滤**：参考文献块默认排除出 RAG 上下文（向量 + BM25 两路）。
- [x] **3.3 依赖与文档**：requirements.txt / pyproject.toml 增加 `PyMuPDF`；README 增补解析引擎配置说明。

## Phase 4 — Review 验收补齐

Review 发现的缺口逐项补齐（公式链路闭环为其中最大一项）：

- [x] **4.1 uv.lock 同步**：pyproject 新增 PyMuPDF 后重新 `uv lock`，`uv lock --check` 通过。
- [x] **4.2 chunk 页码元数据**：标题节点页码贯通到块元数据（`section_pages` 映射），Chroma 元数据含 `page`，支持块定位回原始页。
- [x] **4.3 公式链路闭环（PyMuPDF 主通道）**：数学字形密集行检测（数学 Unicode 区块 + TeX 字体名启发式）→ 连续区域合并 → 页面裁剪渲染 PNG → `FormulaRecognizer`（Mathpix/Pix2Text，配置驱动）转 LaTeX → `$$...$$` 回填正文 + equation 节点（含页码/bbox）；识别失败保留原始字形优雅降级；回填后的公式行在切块时保持原子；公式行永不参与标题识别。
- [x] **4.4 全局检索参考文献过滤**：深度搜索的两路（跨论文向量 `query_global` + 逐论文 BM25）默认排除 reference 块，与单论文 RAG 行为一致。
- [x] **4.5 PaddleOCR 单测**：fake 引擎模块注入测试（扫描件 → OCR 文本组装 / 模块缺失时不可用）。
- [x] **4.6 回归**：127 个单测全绿（新增 14 个：公式链路 7 + 检索过滤 5 + 页码元数据 2）；ruff/format 通过；mypy 维持存量基线（26 个旧问题，无新增）。

## Phase 5 — 后续迭代三项落地（VLM 图表描述 / SHA256 去重 / Mathpix 缓存）

- [x] **5.1 VLM 图表描述 + 图注绑定入库**（roadmap 改进 2，`vlm_describer.py` + `run_figure_step`）
      `vlm_enabled=True` 且配置 `qwen_api_key` 时：figure 节点按 bbox 裁剪渲染 PNG（2x）→ qwen-vl-max 等 OpenAI 兼容多模态接口描述（Prompt 约束图表类型/坐标轴/关键数据/图例/结论，结合已绑定图注）→ 描述回写产物 + `[图片描述 Page N] 图注+描述` 以 `element_type="image_desc"` 入检索库（RAG 与图表证据技能均可命中）。单图失败只降级该图，步骤整体异常不阻塞管线；复用产物时已描述节点不重复调用；`vlm_max_figures`/`vlm_concurrency` 控制成本。
- [x] **5.2 文件 SHA256 去重**（`storage.py` + `_load_ir`）
      解析成功后记录 `sha256_index.json`（内容哈希 -> paper_id）；同文件重复上传时 `_load_ir` 命中索引直接复用既有解析产物（落盘到新 paper_id），不重跑解析引擎。
- [x] **5.3 Mathpix 识别缓存**：进程级内容哈希缓存（sha1(png) -> latex，容量 512），重跑解析/失败重试不重复计费。
- [x] **5.4 回归**：136 个单测全绿（新增 9 个：VLM 5 + 去重 2 + 缓存 1 + 裁剪边界 1）；ruff/format 通过；mypy 24 个（TYPE_CHECKING 修复顺带消除 2 个存量错误，低于基线 26）。
- [x] **5.5 端到端冒烟**：真实 PDF → 解析（图注自动绑定）→ VLM 描述（真实裁剪 PNG）→ 描述回写产物 + image_desc 块入库 → SHA256 索引记录。

## 验证（贯穿）

- [x] 单元测试：DocumentIR 序列化往返 / 结构感知分块（公式表格原子性）/ Preflight 扫描件判定 / ParserRouter 降级链 / LLM 翻译（mock）/ orchestrator 解析一次断言 / 公式链路（回填+降级+原子分块）/ 检索双路参考文献过滤 / PaddleOCR fake 引擎 / chunk 页码元数据 / VLM 图表描述（裁剪+回填+入库+降级）/ SHA256 去重 / Mathpix 缓存。
- [x] 回归：`PYTHONPATH=backend pytest tests/unit -q` 全绿（136 passed）；ruff 通过。
- [x] 端到端冒烟：`formula_backend=mathpix` 配置 → ParserRouter 注入识别器 → 公式区域检测/裁剪/识别 → `$$Attention(Q,K,V)=...$$` 回填正文与 equation 节点 → 分块原子、元数据带页码；`vlm_enabled` → 图注绑定 → VLM 描述 → image_desc 入库。

## Phase 6 — FileRouter 扩展 + GROBID 元数据 + 确定性 ID

- [x] **6.1 Markdown / DOCX 文件路由**（`MarkdownBackend` / `DocxBackend` + `parse_any_document` 统一分发）
      上传支持 PDF / Markdown / DOCX（`save_upload` 保留真实扩展名、`source_path` 通用寻址）；Markdown 标题层级保留原文、`$$` 公式与表格切块原子；DOCX 走 Heading 样式映射章节、表格转 Markdown（python-docx 守卫导入）；VLM 裁剪与 GROBID 对非 PDF 自动跳过。
- [x] **6.2 GROBID 学术元数据增强**（`grobid.py`，可选）
      `grobid_enabled=true` 且服务可达时：解析成功后调用 `/api/processFulltextDocument` 抽取 title/authors/abstract/DOI/references 合入 `DocumentIR.metadata`；服务不可用/非 PDF/解析失败一律告警跳过，不阻塞主管线。
- [x] **6.3 确定性 chunk ID（内容哈希）**：`{paper}:{index}:{sha1(content)}`，同内容重复入库生成相同 ID（幂等，重跑解析不产生重复向量）。
- [x] **6.4 回归**：152 个单测全绿（新增 16 个：MD/DOCX/FileRouter/存储后缀 7 + GROBID 7 + 确定性 ID 2）；ruff/format 通过；mypy 24（持平）；uv.lock 重新生成（+python-docx/lxml）。
- [x] **6.5 端到端冒烟**：Markdown 全管线（解析→章节→公式/表格原子块→元数据入库）+ VLM/GROBID 非 PDF 守卫 + SHA256 去重对 MD 生效。

## Phase 7 — 前端接入：上传组件扩展 + SettingsView 管线配置

- [x] **7.1 上传组件支持 .md/.docx**：HomeView 文件选择器 `accept=".pdf,.md,.markdown,.docx"` + 前端扩展名守卫（与后端 `SUPPORTED_UPLOAD_SUFFIXES` 一致的友好提示），文案更新。
- [x] **7.2 用户级管线偏好（后端）**：`UserApiConfig.pipeline_prefs`（JSON，白名单 10 键：解析引擎/MinerU/OCR/公式识别/翻译通道/VLM×3/GROBID×2，Literal 枚举校验）+ `GET/PUT /api/settings/pipeline`（GET 返回用户覆盖与服务端默认合并后的生效值 + `is_user_set` 标记；PUT 全空 = 恢复默认）；`load_user_settings` 经 `apply_pipeline_prefs` 把偏好应用到管线 settings（白名单 + hasattr 守卫 + 坏 JSON 静默回退）。init.sql 同步加列。
- [x] **7.3 SettingsView「解析与生成管线」卡片**：解析引擎/翻译通道/公式识别下拉、MinerU/OCR/VLM/GROBID 开关、VLM 模型与上限、GROBID 地址输入；已自定义项打标；「恢复默认」一键清空覆盖；client.js 新增 `getPipelinePrefs/updatePipelinePrefs`。
- [x] **7.4 验证**：后端 162 个单测全绿（+10：偏好应用/枚举校验/端点逻辑 fake-db）；ruff/mypy 干净；前端 `npm run build` 通过（Vue SFC 编译 + 打包）。

## Phase 8 — document_jobs 状态机 + GROBID references 合并去重

- [x] **8.1 document_jobs 管线阶段状态机**（`models.DocumentJob` + `services/document_jobs.py`）
      阶段流转 `UPLOADED → PARSING → CHUNKING → EMBEDDING → INDEXED`，任一阶段可转 `FAILED`（终态）；严格相邻流转（禁回退/禁跳阶段，同阶段幂等）；`stage_history` JSON 记录每次流转（时间/引擎/错误）；与 TaskRecord（用户可见粗粒度进度）互补，回答"处理到哪一步/卡在哪/用哪个引擎"。orchestrator 解析步骤全接线（解析失败转 FAILED 并带 error）；所有 DB 操作 best-effort（异常只告警，绝不阻塞管线）；init.sql 建表。
- [x] **8.2 GROBID references 合并去重**（`grobid.merge_reference_nodes`）
      规范化指纹匹配（去前导 `[12]` 编号、小写、剔除非字母数字，截断保护 60 字符，指纹 >= 8 才参与防短串误配，包含判定容忍 PDF 抽取截断/页码噪声）：命中节点回填 `meta.source=grobid` + 规范标题；未命中追加 `ref-g*` 节点；重放幂等（无重复节点）。已接入 `enrich_ir_metadata`。
- [x] **8.3 验证**：175 个单测全绿（+13：流转规则 2 + fake-session 持久化生命周期/回退拒绝/FAILED 带错误/DB 异常吞掉 4 + orchestrator 阶段顺序与失败终态 2 + 指纹/合并/幂等/短串/enrich 集成 5）；ruff/mypy 干净。

## Phase 9 — 公式区域检测升级（MFD：真实模型权重落地）

- [x] **9.1 调研（生产级做法 + 网络现实）**：HF/GitHub 不可达、hf-mirror LFS 走 xet CDN 实测 ~1KB/s（44MB 的 pix2text MFD onnx 不可行）；ModelScope/Gitee AI 无镜像；**百度 BOS（PaddleX 官方模型仓库）完全可达且快**。路线定为 paddlepaddle CPU + PaddleX 官方推理包。
- [x] **9.2 模型落地**（`backend/models/paddle/`，`scripts/download_models.sh` 一键下载，.gitignore 不入库）：
      PP-DocLayoutV2（DETR，203MB，23 类，区分 display_formula/inline_formula）+ PP-DocLayout-S（PicoDet，**4MB 轻量档**，含 formula 类）+ PP-FormulaNet-S（公式→LaTeX，本地替代 Mathpix，留作识别通道扩展）+ 官方公式示例图（测试素材）。
- [x] **9.3 检测器实现**（`layout_detector.py`）：paddle.inference 守卫加载（PIR inference.json 格式）；PaddleX 检测导出解码 `[class_id, score, x1..y2]`；**实测修正**——graph 不应用 scale_factor，输出为 resize 后坐标，自行按 原始/目标 比例映射回原图（真实模型冒烟定位并修复该 bug）；display 级公式框整行标记、inline 交给启发式（防误吞正文）。
- [x] **9.4 接线**：PyMuPDFBackend 新增 `layout_detector` 参数，`_mark_formula_lines_with_detector` 检测框内文本行标记公式行（下游裁剪→识别→$$回填链路复用）；检测失败回退字形启发式；Router 按配置注入（`layout_detector: off | doclayout`，默认 off）。
- [x] **9.5 验证**：184 个单测全绿（+9：解码纯函数 4 / 配置门控 2 / **真实模型集成 2**——公式图 PDF 检出 ≥3 个公式框且落位正确、后端全链路 $$LaTeX$$ 回填 / yml 配置解析 1）；坐标映射回归测试防 scale_factor 方向 bug 复发；ruff/mypy 干净。

## Phase 10 — PP-FormulaNet 本地公式识别通道（公式链路完全本地化）

- [x] **10.1 逆向官方管线**（从 paddlex wheel 源码）：UniMERNet 预处理（裁边→短边 384 等比→居中填充→(x/255-0.7931)/0.1738→加权灰度单通道）；实测导出图**内置自回归生成**（单输入 (1,1,384,384)，输出 token id 序列）；tokenizer（5 万词表 BPE）内嵌于 inference.yml，导出临时 json 交 tokenizers 库加载。
- [x] **10.2 `PaddleFormulaRecognizer`**（`formula_backend=paddle`，本地免费替代 Mathpix）：cv2-free 纯 numpy/PIL 预处理复刻 + paddle inference + 内嵌 tokenizer 解码；失败返回 None 优雅降级。`get_formula_recognizer` 增加 paddle 分支（paddle/tokenizers 未装或模型目录缺失时回退 Null）。
- [x] **10.3 图片型公式识别**：版面检测的公式区域内无文本行时（扫描图/图片公式），直接裁剪区域识别，产出 `source=image_formula` 的 equation 节点并按页回填 `$$LaTeX$$` 到全文——补齐了此前"检测框只标记文本行"的能力缺口。
- [x] **10.4 验证**：190 个单测全绿（+6：预处理纯函数 3 / 门控 1 / **真实模型识别 1**（交叉熵公式 → 正确 LaTeX）/ **全链路 1**（公式图片 PDF → 检测 → 裁剪 → 识别 → `$$H_c(\mathbf{x})=-\int c\log c\,d\mathbf{u}$$` 回填））；ruff/mypy 干净；download_models.sh 默认下载 FormulaNet。

## Phase 11 — 向量库版本隔离/Server 模式 + VLM 后台异步化

- [x] **11.1 Chroma Server 模式客户端**：`vector_store_mode=local|server` + `vector_server_url`；server 模式走 `chromadb.HttpClient`（URL 解析 host/port/ssl），local 保持 PersistentClient 兼容；未配 URL 时明确禁用并给原因。部署拆分（独立 chroma 容器）由运维执行，客户端切换纯配置。
- [x] **11.2 embedding collection 版本隔离**：`vector_collection_versioned=true` 时集合名为 `{base}__{模型标签}_{短哈希}_v{schema}`（确定性、特殊字符清洗），换 embedding 模型或 schema 升版自动切新集合，杜绝不同维度向量混库；默认关闭兼容存量数据。集合 metadata 记录 `embedding_model` 审计标记。
- [x] **11.3 VLM 后台异步化（`vlm_mode=sync|async`）**：async 时 `schedule_figure_step_async` 转进程内后台任务——上传秒级完成解析入库，描述异步补全（回写产物 + image_desc 检索块）；silent 模式不打扰已完成的主任务状态；同 paper 未完成任务期间重复调度去重（防重复计费）；后台失败只告警不传播。调度器接口与 Redis 队列版（roadmap 改进 6）对齐，后续可平滑换实现。
- [x] **11.4 验证**：200 个单测全绿（+10：版本化命名 2 / fake-chromadb 客户端模式 5 / VLM 异步立即返回+补全+去重+失败吞掉 3）；ruff 干净；mypy 无新增（存量 7 个 Optional 收窄问题）。

## 后续迭代

- 存量数据库迁移：`ALTER TABLE user_api_configs ADD COLUMN pipeline_prefs TEXT` + `CREATE TABLE document_jobs`（init.sql 已覆盖新装环境）
- VLM 后台任务升级为 Redis 队列（多进程/多副本部署时替换进程内调度器，接口已对齐）
- 版本隔离集合的存量数据迁移脚本（按需：重 embed 或复制）
