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
    embedding_model_name: str = "sentence-transformers/all-MiniLM-L6-v2"
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

    # Agent tool calling
    agent_enable_tools: bool = True
    agent_max_tool_rounds: int = 4
    agent_skills_dir: str = "agents/skills"
    skills_collection_name: str = "skills_collection"
    skill_retrieval_top_k: int = 5

    # LangGraph + ToT
    langgraph_enabled: bool = True
    pipeline_translation_retry_limit: int = 1
    enable_tot: bool = True
    tot_branch_count: int = 3
    tot_generation_temperature: float = 0.8
    tot_generation_trials: int = 3
    tot_reviewer_temperature: float = 0.2
    tot_score_alpha: float = 1.0
    tot_score_beta: float = 1.0
    tot_score_gamma: float = 1.0

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    @property
    def cors_origin_list(self) -> list[str]:
        if self.cors_origins.strip() == "*":
            return ["*"]
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
