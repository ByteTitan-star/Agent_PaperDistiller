import os
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """项目配置。通过 `.env` 覆盖默认值。"""

    app_name: str = "Personal Scholar Agent API"
    api_prefix: str = "/api"
    data_dir: str = "data"
    templates_dir: str = "templates"
    cors_origins: str = "*"

    # PDF 解析与切块
    max_chunk_chars: int = 900
    chunk_overlap: int = 120

    # 展示字段
    llm_model_name: str = "DeepSeek-Agent"
    embedding_model_name: str = "models/bge-m3"
    model_provider: str = "DeepSeek+LocalTools"
    pipeline_mode: str = "LangGraph-RAG-Agent"
    generation_model_name: str = "DeepSeek-V3"
    evaluation_model_name: str = "Qwen3"

    # DeepSeek
    deepseek_api_key: str = "your-api-key"
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-chat"
    deepseek_timeout_sec: float = 45.0

    # Qwen (通义千问)
    qwen_api_key: str = "your-api-key"
    qwen_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    qwen_model: str = "qwen3-30b-a3b-instruct-2507"
    qwen_timeout_sec: float = 45.0

    # 向量检索
    vector_store_provider: str = "chromadb"
    vector_collection_name: str = "paper_chunks"
    vector_db_subdir: str = "vectordb"
    vector_distance_metric: str = "cosine"
    rag_default_top_k: int = 4
    rag_fallback_to_lexical: bool = True

    # 跨论文检索（深度研究）
    global_retrieval_top_k: int = 50  # 初排：跨全库向量召回数量
    global_retrieval_bm25_top_k: int = 50  # 初排：每篇论文 BM25 召回数

    # 精排 Reranker
    reranker_model_name: str = "models/bge-reranker-v2-m3"
    reranker_enabled: bool = True
    reranker_top_k: int = 10  # 精排后保留数量
    reranker_max_length: int = 512

    # Agent tool calling
    agent_enable_tools: bool = True
    agent_native_chat_enabled: bool = True
    agent_max_tool_rounds: int = 4  # 工具调用最大轮数
    agent_skills_dir: str = "skills"  # relative to backend/app/
    skills_collection_name: str = "skills_collection"
    skill_retrieval_top_k: int = 5
    skill_similarity_threshold: float = 0.4

    # LangGraph + ToT (orchestrator is the only pipeline path; langgraph_enabled kept for admin UI compat)
    langgraph_enabled: bool = False
    pipeline_translation_retry_limit: int = 1
    enable_tot: bool = True
    tot_branch_count: int = 3
    tot_generation_temperature: float = 0.8
    tot_generation_trials: int = 3
    tot_reviewer_temperature: float = 0.2
    tot_score_alpha: float = 1.0
    tot_score_beta: float = 1.0
    tot_score_gamma: float = 1.0

    # Tavily WebSearch
    tavily_api_key: str = ""
    tavily_search_depth: str = "basic"
    tavily_max_results: int = 3

    # Alibaba Cloud OSS
    oss_enabled: bool = True
    oss_access_key_id: str = ""
    oss_access_key_secret: str = ""
    oss_endpoint: str = "https://oss-cn-beijing.aliyuncs.com"
    oss_bucket_name: str = "agentpaper"
    oss_prefix: str = "papers"  # OSS 对象前缀

    # ReAct Deep Search
    react_max_rounds: int = 5
    react_enable_clarification: bool = True

    # 深度搜索规划：True 时用 SupervisorPattern（主管分解子问题 + worker 并行 + 合并）
    # 生成研究计划，False 时保持单次 LLM 规划（默认，成本更低）。
    supervisor_planning_enabled: bool = False

    # MCP（Model Context Protocol）：对外把技能暴露为标准 MCP server，对内让 ReAct 调用外部 MCP server。
    # 默认关闭；启用前需 pip install mcp langchain-mcp-adapters
    mcp_enabled: bool = False  # 对外：挂载 FastMCP server 到 mcp_mount_path
    mcp_mount_path: str = "/mcp"
    mcp_inbound_enabled: bool = False  # 对内：ReAct agent 加载外部 MCP server 作为工具
    mcp_inbound_servers: str = ""  # 逗号分隔的外部 MCP server URL（SSE/HTTP），如 "http://localhost:9001/sse"

    # OpenTelemetry 自托管可观测（v3.0）。默认关闭；启用前需 pip install opentelemetry-*。
    # 选用 OTel 而非 LangSmith：自托管 exporter（Jaeger/Tempo）国内网络最稳，不依赖境外服务。
    otel_enabled: bool = False
    otel_service_name: str = "paper-distiller"
    otel_exporter_otlp_endpoint: str = (
        ""  # 空=dev 用 console exporter；非空走 OTLP（如 http://localhost:4318/v1/traces）
    )

    # 工具限流（v3.0 Phase 6）：HarnessToolRegistry 在窗口内限制每个工具的最大调用次数。
    # max_calls=0 表示不限流。
    tool_rate_limit_max_calls: int = 0
    tool_rate_limit_window: float = 60.0

    # HITL for Deep Search（深度搜索人工审批）
    hitl_deep_search_enabled: bool = False  # 启用后深度搜索会在搜索前和生成报告前暂停等待用户确认

    # Harness / Agent runtime
    harness_startup_enabled: bool = True
    agent_max_iterations: int = 12
    agent_service_role: str = "all-in-one"  # api | worker | all-in-one
    redis_url: str = ""

    # Sandbox (Docker)
    sandbox_enabled: bool = False
    sub_agent_store_memory: bool = False
    sub_agent_store_auto_fallback: bool = True
    sandbox_docker_image: str = "python:3.12-slim"
    sandbox_timeout_sec: float = 120.0
    sandbox_workspace_root: str = "data/sandbox"
    sandbox_network_enabled: bool = False

    # Multi-agent collaboration default (supervisor | round_robin | tot)
    default_collaboration_mode: str = "tot"

    # RAG 评估（RAGAS）配置。judge/合成用 DeepSeek（OpenAI 兼容端点），嵌入走本地 sentence-transformers。
    # 空 eval_judge_* 时回落到 DEEPSEEK_* 配置。
    eval_enabled: bool = True
    eval_judge_api_key: str = ""  # 空 -> 复用 deepseek_api_key
    eval_judge_base_url: str = ""  # 空 -> 复用 deepseek_base_url
    eval_judge_model: str = "deepseek-chat"  # 评估/合成用的模型 id
    eval_judge_temperature: float = 0.0
    eval_judge_timeout_sec: float = 120.0  # 评估 LLM 单次调用超时（RAGAS 多轮调用，给宽松些）
    eval_testset_size: int = 8  # 每套 RAG 合成的问题数
    eval_paper_top_k: int = 4  # 论文正文 RAG 检索 top_k
    eval_skill_top_k: int = 5  # Skill 检索 top_k
    eval_skill_min_similarity: float = 0.4
    eval_embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"  # 合成器聚类用嵌入
    eval_report_dir: str = "data/eval_reports"
    eval_paper_id: str = "eval-paper"  # 评估用论文的 paper_id
    eval_paper_path: str = ""  # 评估用 PDF 绝对路径

    # Database
    DATABASE_URL: str = "mysql+asyncmy://root:root223@localhost:3306/AgentPaperDistriller?charset=utf8mb4"

    # Security
    SECRET_KEY: str = "change-me-in-production-use-a-long-random-string"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24  # 24 hours
    AES_SECRET_KEY: str = "aes-32-byte-secret-key-change-me!!"

    # Email (SMTP for verification)
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM_EMAIL: str = ""

    model_config = SettingsConfigDict(
        env_file=f".env.{os.getenv('APP_ENV', 'dev')}",
        env_file_encoding="utf-8",
        extra="ignore",
        load_dotenv=True,
    )

    @property
    def cors_origin_list(self) -> list[str]:
        if self.cors_origins.strip() == "*":
            return ["*"]
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
