"""构建 RAGAS 用的 LLM 与嵌入包装器。

背景：RAGAS 评估需要两样"引擎"——
  1. 一个 LLM：当"裁判"(judge)给答案打分，也用来合成测试集的参考答案；
  2. 一个嵌入模型：RAGAS 合成测试集时，需要把文档向量化后聚类，挑出有代表性的主题。

本文件就是把项目里已有的 DeepSeek（OpenAI 兼容端点）和本地 sentence-transformers
分别包成 RAGAS / LangChain 能识别的对象。注意：这里只服务"评估侧"，不碰项目运行时
真正的检索/生成链路（那些仍走 storage / LLMClient）。
"""

from __future__ import annotations

import sys
import types
from functools import lru_cache

from ..config import Settings, get_settings


# ---------------------------------------------------------------------------
# 兼容桩：让 ragas 0.4.x 在新版 langchain-community 下能正常 import
# ---------------------------------------------------------------------------
# 现象：ragas 0.4.x 在 ragas/llms/base.py 顶部硬写了
#   from langchain_community.chat_models.vertexai import ChatVertexAI
# 但 langchain-community>=0.4 已经把这个子模块删了（迁移到 langchain-google-vertexai），
# 导致 `import ragas` 直接报 ModuleNotFoundError。
# 解决：在 ragas 被 import 之前，往 sys.modules 里塞一个假的 vertexai 模块，
#       里面的 ChatVertexAI 是个空桩类。ragas 只在 MULTIPLE_COMPLETION_SUPPORTED
#       列表里用它做 isinstance 判断，我们用的是 ChatOpenAI，桩类永远匹配不上，
#       所以不影响实际功能。
def _patch_langchain_community_vertexai() -> None:
    """注入一个 vertexai 桩模块，绕过 ragas 0.4.x 对已删除子模块的硬导入。"""
    # 能正常导入就直接返回，不做任何事（旧版 langchain-community 还保留该子模块时）
    try:
        import langchain_community.chat_models.vertexai  # noqa: F401
        return
    except ModuleNotFoundError:
        pass

    import langchain_community.chat_models as _cm

    # 手工造一个模块对象，注册到 sys.modules，这样 `from ...vertexai import ChatVertexAI` 就能成功
    mod = types.ModuleType("langchain_community.chat_models.vertexai")

    class _Stub:  # 占位类：只为了让 import 和 isinstance 遍历不报错
        pass

    mod.ChatVertexAI = _Stub  # type: ignore[attr-defined]
    sys.modules["langchain_community.chat_models.vertexai"] = mod
    _cm.vertexai = mod  # type: ignore[attr-defined]  同时挂到父模块属性上，便于 `import ... .vertexai`


# 模块被 import 时立刻执行一次，确保后续任何 `import ragas` 都不会因为缺 vertexai 而炸
_patch_langchain_community_vertexai()


# ---------------------------------------------------------------------------
# 延迟导入 + 缓存：langchain 类的加载比较重，且只在实际评估时才需要
# ---------------------------------------------------------------------------
@lru_cache(maxsize=1)
def _langchain_openai() -> type:
    """延迟导入 LangChain 的 ChatOpenAI，避免未装 langchain 时整个模块不可用。"""
    from langchain_openai import ChatOpenAI

    return ChatOpenAI


@lru_cache(maxsize=1)
def _huggingface_embeddings() -> type:
    """延迟导入 LangChain 的 HuggingFaceEmbeddings 包装。"""
    from langchain_huggingface import HuggingFaceEmbeddings

    return HuggingFaceEmbeddings


# ---------------------------------------------------------------------------
# 对外暴露的两个构造函数
# ---------------------------------------------------------------------------
def build_eval_llm(settings: Settings | None = None):
    """构造 DeepSeek 的 LangChain Chat 模型，供 RAGAS judge / 测试集合成使用。

    DeepSeek 官方提供 OpenAI 兼容的 /chat/completions 端点，所以可以直接用
    LangChain 的 ChatOpenAI，把 base_url 指向 DeepSeek 即可，RAGAS 不用改任何代码。

    配置来源（见 backend/.env.dev）：
      - EVAL_JUDGE_API_KEY：为空时回落到 DEEPSEEK_API_KEY
      - EVAL_JUDGE_BASE_URL：为空时回落到 DEEPSEEK_BASE_URL
      - EVAL_JUDGE_MODEL：模型 id，例如 deepseek-chat / deepseek-v4-flash
    """
    s = settings or get_settings()
    api_key = s.eval_judge_api_key or s.deepseek_api_key
    base_url = s.eval_judge_base_url or s.deepseek_base_url
    # 仍是默认占位符说明用户没填 key，直接报错提示去配 .env.dev
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
        temperature=s.eval_judge_temperature,  # judge 打分要稳定，温度配 0
        timeout=getattr(s, "eval_judge_timeout_sec", 120.0),  # RAGAS 多轮调用，超时给宽松些
        max_retries=5,  # DeepSeek 偶发超时/限流，自动重试 5 次
    )


def build_eval_embeddings(settings: Settings | None = None):
    """构造本地 sentence-transformers 嵌入，供 RAGAS 测试集合成器聚类使用。

    只在"合成测试集"这一步用到（把文档向量化后聚类挑主题）；真正的检索评估
    走的是项目原生 storage 里的向量库，跟这里无关。默认用 all-MiniLM-L6-v2
    （小模型，首次自动从 HuggingFace 下载），不依赖生产里的 bge-m3。
    """
    s = settings or get_settings()
    HuggingFaceEmbeddings = _huggingface_embeddings()
    return HuggingFaceEmbeddings(model_name=s.eval_embedding_model)
