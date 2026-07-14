"""构建 RAGAS 用的 LLM 与嵌入包装器。

judge / 合成 LLM：DeepSeek（OpenAI 兼容），通过 LangChain ``ChatOpenAI`` 暴露给 RAGAS。
合成器聚类嵌入：本地 ``sentence-transformers``，通过 ``HuggingFaceEmbeddings`` 暴露给 RAGAS。
均与项目运行时 RAG 链路解耦——检索/生成仍走项目原生 ``storage`` / ``LLMClient``，这里只服务评估侧。
"""

from __future__ import annotations

import sys
import types
from functools import lru_cache

from ..config import Settings, get_settings


def _patch_langchain_community_vertexai() -> None:
    """ragas 0.4.x 仍硬导入 ``langchain_community.chat_models.vertexai.ChatVertexAI``，
    该子模块在 langchain-community>=0.4 已被移除。这里注入一个桩类让 ragas 导入通过。
    我们只用 ChatOpenAI，桩类不匹配任何 isinstance，不影响功能。"""
    try:
        import langchain_community.chat_models.vertexai  # noqa: F401
        return
    except ModuleNotFoundError:
        pass
    import langchain_community.chat_models as _cm

    mod = types.ModuleType("langchain_community.chat_models.vertexai")

    class _Stub:  # 占位类，仅满足 import 与 isinstance 遍历
        pass

    mod.ChatVertexAI = _Stub  # type: ignore[attr-defined]
    sys.modules["langchain_community.chat_models.vertexai"] = mod
    _cm.vertexai = mod  # type: ignore[attr-defined]


_patch_langchain_community_vertexai()


@lru_cache(maxsize=1)
def _langchain_openai() -> type:  # 延迟导入，避免未装 langchain 时整个模块不可用
    from langchain_openai import ChatOpenAI

    return ChatOpenAI


@lru_cache(maxsize=1)
def _huggingface_embeddings() -> type:
    from langchain_huggingface import HuggingFaceEmbeddings

    return HuggingFaceEmbeddings


def build_eval_llm(settings: Settings | None = None):
    """构造 DeepSeek 的 LangChain Chat 模型，供 RAGAS judge / 测试集合成使用。"""
    s = settings or get_settings()
    api_key = s.eval_judge_api_key or s.deepseek_api_key
    base_url = s.eval_judge_base_url or s.deepseek_base_url
    if not api_key or api_key == "your-api-key":
        raise RuntimeError(
            "评估 LLM 未配置可用 API key：请在 backend/.env.dev 设置 "
            "EVAL_JUDGE_API_KEY / DEEPSEEK_API_KEY。"
        )
    ChatOpenAI = _langchain_openai()
    return ChatOpenAI(
        model=s.eval_judge_model,
        api_key=api_key,
        base_url=base_url,
        temperature=s.eval_judge_temperature,
        timeout=getattr(s, "eval_judge_timeout_sec", 120.0),
        max_retries=5,
    )


def build_eval_embeddings(settings: Settings | None = None):
    """构造本地 sentence-transformers 嵌入，供 RAGAS 测试集合成器聚类使用。"""
    s = settings or get_settings()
    HuggingFaceEmbeddings = _huggingface_embeddings()
    return HuggingFaceEmbeddings(model_name=s.eval_embedding_model)
