"""检索参考文献过滤测试：单论文 RAG / 全局向量检索两路默认排除 reference 块。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.services.chat import _exclude_reference_chunks, retrieve_contexts


class FakeFilterStorage:
    """最小 storage 替身：只提供 load_chunks / load_chunk_metas / search_similar_chunks。"""

    def __init__(
        self,
        chunks: list[str],
        metas: list[dict[str, Any]] | None = None,
        vector_hits: list[str] | None = None,
    ) -> None:
        self._chunks = chunks
        self._metas = metas
        self._vector_hits = vector_hits or []

    def load_chunks(self, paper_id: str) -> list[str]:
        return self._chunks

    def load_chunk_metas(self, paper_id: str) -> list[dict[str, Any]]:
        return self._metas or []

    def search_similar_chunks(self, paper_id: str, question: str, top_k: int) -> list[str]:
        return self._vector_hits[:top_k]


def test_exclude_reference_chunks_filters_refs() -> None:
    chunks = ["注意力机制正文。", "[1] Vaswani et al. 2017.", "[2] Devlin et al. 2019.", "实验结果正文。"]
    metas = [
        {"element_type": "text", "is_reference": False},
        {"element_type": "reference", "is_reference": True},
        {"element_type": "reference", "is_reference": True},
        {"element_type": "text", "is_reference": False},
    ]
    storage = FakeFilterStorage(chunks, metas)
    kept, kept_metas = _exclude_reference_chunks(chunks, "p1", storage)
    assert kept == ["注意力机制正文。", "实验结果正文。"]
    assert all(m["element_type"] == "text" for m in kept_metas)


def test_exclude_reference_chunks_passthrough_without_metas() -> None:
    chunks = ["正文一。", "正文二。"]
    storage = FakeFilterStorage(chunks, metas=[])  # 旧数据无元数据
    kept, _ = _exclude_reference_chunks(chunks, "p1", storage)
    assert kept == chunks


def test_exclude_reference_chunks_all_refs_keeps_original() -> None:
    """全部都是参考文献时不过滤（用户可能明确想问参考文献）。"""
    chunks = ["[1] ref one", "[2] ref two"]
    metas = [{"element_type": "reference", "is_reference": True}] * 2
    storage = FakeFilterStorage(chunks, metas)
    kept, _ = _exclude_reference_chunks(chunks, "p1", storage)
    assert kept == chunks


def test_retrieve_contexts_excludes_references_in_fusion() -> None:
    """BM25 命中参考文献块时，融合结果中不应出现。"""
    chunks = [
        "Attention mechanism improves accuracy significantly.",
        "softmax attention beats RNN baselines on WMT14.",
        "[1] Vaswani et al. Attention is all you need. 2017.",
    ]
    metas = [
        {"element_type": "text", "is_reference": False},
        {"element_type": "text", "is_reference": False},
        {"element_type": "reference", "is_reference": True},
    ]
    storage = FakeFilterStorage(chunks, metas, vector_hits=[])
    results = retrieve_contexts("attention", "p1", top_k=3, storage=storage)
    assert results
    assert all("[1] Vaswani" not in ctx for ctx in results)


def test_vector_store_query_global_filters_references(tmp_path: Path) -> None:
    """全局向量检索：reference 块被过滤、top_k 生效、多取一倍候选。"""

    class FakeCollection:
        def __init__(self) -> None:
            self.last_n_results = 0

        def query(self, **kwargs: Any) -> dict:
            self.last_n_results = kwargs["n_results"]
            docs = ["正文块 A", "参考文献 [1]", "正文块 B", "参考文献 [2]", "正文块 C"]
            metas = [
                {"paper_id": "p1", "chunk_index": 0, "element_type": "text"},
                {"paper_id": "p1", "chunk_index": 1, "element_type": "reference"},
                {"paper_id": "p2", "chunk_index": 0, "element_type": "text"},
                {"paper_id": "p2", "chunk_index": 1, "element_type": "reference"},
                {"paper_id": "p3", "chunk_index": 0, "element_type": "text"},
            ]
            dists = [0.1, 0.2, 0.3, 0.4, 0.5]
            return {"documents": [docs], "metadatas": [metas], "distances": [dists]}

    class FakeEmbedder:
        def encode(self, texts: list[str], normalize_embeddings: bool = True) -> list[list[float]]:
            return [[0.0, 1.0] for _ in texts]

    from app.storage import VectorStore

    store = VectorStore(
        base_dir=tmp_path,
        db_subdir="vectordb",
        provider="chromadb",
        collection_name="paper_chunks",
        embedding_model_name="fake",
    )
    store._ready = True
    store._collection = FakeCollection()
    store._embedder = FakeEmbedder()

    results = store.query_global("attention", top_k=3)
    assert store._collection.last_n_results == 6  # 多取一倍候选
    assert len(results) == 3
    assert all("参考文献" not in r["text"] for r in results)
    assert all(r["element_type"] == "text" for r in results)

    include_all = store.query_global("attention", top_k=5, include_references=True)
    assert len(include_all) == 5  # 不过滤时全部返回
