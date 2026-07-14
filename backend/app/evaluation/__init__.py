"""RAG 评估模块（基于 RAGAS）。

评估两套 RAG：
- 论文正文 RAG（chat Q&A 的向量+BM25 混合检索 + DeepSeek 生成）
- Agent Skill 检索 RAG（SkillRegistry.select_tools 的语义召回）

judge/合成 LLM 走 DeepSeek（OpenAI 兼容端点），合成器聚类嵌入走本地 sentence-transformers。
"""

from .runner import run_rag_evaluation

__all__ = ["run_rag_evaluation"]
