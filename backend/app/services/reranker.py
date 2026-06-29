"""
Cross-encoder 精排模块。

用于深度研究场景的两阶段检索：
  初排（向量 + BM25）→ 精排（CrossEncoder 重排序）

采用与 VectorStore 一致的懒加载模式：
首次调用时加载模型，加载失败则静默回退为初排结果。
"""

import logging
from typing import Any

logger = logging.getLogger("reranker")

# 模块级单例
_instance: "Reranker | None" = None


class Reranker:
    """基于 sentence-transformers CrossEncoder 的精排器。"""

    def __init__(self, model_name: str, max_length: int = 512) -> None:
        self._model_name = model_name
        self._max_length = max_length

        # 懒加载状态
        self._ready: bool = False
        self._disabled_reason: str | None = None
        self._model: Any | None = None

    def _ensure_ready(self) -> bool:
        """懒加载：首次调用时加载 CrossEncoder 模型。"""
        if self._ready:
            return True
        if self._disabled_reason:
            return False

        try:
            from sentence_transformers import CrossEncoder
        except ImportError as exc:
            self._disabled_reason = f"sentence-transformers 未安装: {exc}"
            logger.warning("[Reranker] %s", self._disabled_reason)
            return False

        try:
            self._model = CrossEncoder(
                self._model_name,
                max_length=self._max_length,
            )
            self._ready = True
            logger.info(
                "[Reranker] ✅ 模型加载成功: %s (max_length=%d)",
                self._model_name,
                self._max_length,
            )
            return True
        except Exception as exc:
            self._disabled_reason = f"模型加载失败: {exc}"
            logger.warning("[Reranker] ❌ %s", self._disabled_reason)
            return False

    @property
    def available(self) -> bool:
        """精排器是否可用。"""
        return self._ensure_ready()

    def rerank(
        self,
        query: str,
        candidates: list[dict],
        top_k: int = 10,
    ) -> list[dict]:
        """
        对初排候选结果做 CrossEncoder 精排。

        Args:
            query: 用户问题。
            candidates: 初排结果列表，每个 dict 必须包含 "text" 键。
            top_k: 精排后保留的数量。

        Returns:
            list[dict]: 按 reranker_score 降序排列的 top-k 结果。
                        每个元素新增 "reranker_score" 字段。
                        精排器不可用时直接返回初排结果前 top_k 条。
        """
        if not candidates or not query.strip():
            return candidates[:top_k]

        if not self._ensure_ready():
            logger.warning("[Reranker] 不可用，返回初排结果")
            return candidates[:top_k]

        # 构建 (query, passage) 对
        pairs = [(query, c["text"]) for c in candidates]

        try:
            scores = self._model.predict(pairs)
        except Exception as exc:
            logger.error("[Reranker] predict() 失败: %s，返回初排结果", exc)
            return candidates[:top_k]

        # 附加分数并排序
        scored: list[dict] = []
        for idx, candidate in enumerate(candidates):
            c = dict(candidate)  # 浅拷贝，不修改原始数据
            c["reranker_score"] = float(scores[idx])
            scored.append(c)

        scored.sort(key=lambda x: x["reranker_score"], reverse=True)

        logger.info(
            "[Reranker] 精排完成: %d → %d 条 (best_score=%.4f)",
            len(candidates),
            min(top_k, len(scored)),
            scored[0]["reranker_score"] if scored else 0.0,
        )

        return scored[:top_k]


def get_reranker(model_name: str, max_length: int = 512) -> Reranker:
    """
    获取模块级 Reranker 单例。

    首次调用时创建实例，后续调用返回同一实例。
    模型在首次 rerank() 时才真正加载（懒加载）。
    """
    global _instance
    if _instance is None:
        _instance = Reranker(model_name=model_name, max_length=max_length)
    return _instance
