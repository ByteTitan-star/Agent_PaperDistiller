"""向量库版本隔离/Server 模式 + VLM 后台异步化测试（全 fake，无重依赖）。"""

from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from tests.helpers import MockStorage

from app.pipeline.state_broker import TaskBroker
from app.storage import Storage, VectorStore

# ---------------------------------------------------------------------
# 集合版本隔离（纯函数）
# ---------------------------------------------------------------------


def test_versioned_collection_name_is_deterministic_and_sanitized() -> None:
    a = VectorStore.versioned_collection_name("paper_chunks", "models/bge-m3")
    b = VectorStore.versioned_collection_name("paper_chunks", "models/bge-m3")
    assert a == b  # 确定性
    assert a.startswith("paper_chunks__models_bge_m3_") and a.endswith("_v1")

    # 模型变化 / schema 升版 -> 不同集合（维度隔离）
    other_model = VectorStore.versioned_collection_name("paper_chunks", "all-MiniLM-L6-v2")
    v2 = VectorStore.versioned_collection_name("paper_chunks", "models/bge-m3", schema_version=2)
    assert len({a, other_model, v2}) == 3

    # 特殊字符清洗
    weird = VectorStore.versioned_collection_name("chunks", "BAAI/bge-large-zh.v1.5")
    assert all(c.isalnum() or c in "_-" for c in weird.replace("chunks__", ""))


def test_vector_store_resolves_versioned_name() -> None:
    store = VectorStore(
        base_dir=Path("/tmp/unused-a"),
        db_subdir="vd",
        provider="chromadb",
        collection_name="paper_chunks",
        embedding_model_name="models/bge-m3",
        versioned_collection=True,
    )
    assert store.collection_name.startswith("paper_chunks__models_bge_m3_")
    plain = VectorStore(
        base_dir=Path("/tmp/unused-b"),
        db_subdir="vd",
        provider="chromadb",
        collection_name="paper_chunks",
        embedding_model_name="models/bge-m3",
    )
    assert plain.collection_name == "paper_chunks"  # 默认不隔离，兼容存量


# ---------------------------------------------------------------------
# 客户端模式（fake chromadb）
# ---------------------------------------------------------------------


class _FakeCollection:
    def __init__(self, name: str, metadata: dict | None) -> None:
        self.name = name
        self.metadata = metadata


class _FakeClient:
    kind = "unknown"

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs
        self.created: list[_FakeCollection] = []

    def get_or_create_collection(self, name: str, metadata: dict | None = None) -> _FakeCollection:
        collection = _FakeCollection(name, metadata)
        self.created.append(collection)
        return collection


def _fake_chromadb_module() -> types.ModuleType:
    module = types.ModuleType("chromadb")

    class _Persistent(_FakeClient):
        kind = "persistent"

        def __init__(self, path: str) -> None:
            super().__init__(path=path)

    class _Http(_FakeClient):
        kind = "http"

        def __init__(self, host: str, port: int, ssl: bool) -> None:
            super().__init__(host=host, port=port, ssl=ssl)

    module.PersistentClient = _Persistent
    module.HttpClient = _Http
    return module


def _fake_sentence_transformers_module() -> types.ModuleType:
    module = types.ModuleType("sentence_transformers")

    class _FakeST:
        def __init__(self, name: str) -> None:
            self.name = name

    module.SentenceTransformer = _FakeST
    return module


def _make_store(tmp_path: Path, **overrides: object) -> tuple[VectorStore, types.ModuleType]:
    fake = _fake_chromadb_module()
    patches = [
        patch.dict(sys.modules, {"chromadb": fake, "sentence_transformers": _fake_sentence_transformers_module()})
    ]
    for p in patches:
        p.start()
    kwargs: dict[str, object] = {
        "base_dir": tmp_path,
        "db_subdir": "vd",
        "provider": "chromadb",
        "collection_name": "paper_chunks",
        "embedding_model_name": "models/bge-m3",
    }
    kwargs.update(overrides)
    store = VectorStore(**kwargs)  # type: ignore[arg-type]
    return store, fake


@pytest.fixture(autouse=True)
def _restore_modules():
    """测试结束后把 fake chromadb 从 sys.modules 移除，避免污染其他测试。"""
    saved = {k: sys.modules.get(k) for k in ("chromadb", "sentence_transformers")}
    yield
    for key, value in saved.items():
        if key in sys.modules:
            del sys.modules[key]
        if value is not None:
            sys.modules[key] = value


def test_local_mode_uses_persistent_client(tmp_path: Path) -> None:
    store, _ = _make_store(tmp_path)
    assert store._ensure_ready() is True
    assert type(store._client).__name__ == "_Persistent"
    assert store._collection.metadata == {"embedding_model": "models/bge-m3", "hnsw:space": "cosine"}


def test_server_mode_uses_http_client_with_parsed_url(tmp_path: Path) -> None:
    store, _ = _make_store(tmp_path, store_mode="server", server_url="https://chroma.internal:8443")
    assert store._ensure_ready() is True
    client = store._client
    assert type(client).__name__ == "_Http"
    assert client.kwargs == {"host": "chroma.internal", "port": 8443, "ssl": True}


def test_server_mode_without_url_disables(tmp_path: Path) -> None:
    store, _ = _make_store(tmp_path, store_mode="server", server_url="")
    assert store._ensure_ready() is False
    assert "vector_server_url" in store.unavailable_reason


def test_versioned_store_creates_suffixed_collection(tmp_path: Path) -> None:
    store, _ = _make_store(tmp_path, versioned_collection=True)
    assert store._ensure_ready() is True
    assert store._collection.name == store.collection_name
    assert store.collection_name != "paper_chunks"
    assert store.collection_name.startswith("paper_chunks__")


def test_storage_passes_mode_through(tmp_path: Path) -> None:
    storage = Storage(
        base_dir=tmp_path / "data",
        templates_dir=tmp_path / "tpl",
        vector_store_mode="server",
        vector_server_url="http://chroma:8000",
        vector_collection_versioned=True,
    )
    assert storage.vector_store.store_mode == "server"
    assert storage.vector_store.server_url == "http://chroma:8000"
    assert storage.vector_store.collection_name.startswith("paper_chunks__")


# ---------------------------------------------------------------------
# VLM 后台异步化
# ---------------------------------------------------------------------


class _FakeVLM:
    calls = 0

    async def describe(self, png_bytes: bytes, caption: str) -> str:
        _FakeVLM.calls += 1
        await asyncio.sleep(0.05)  # 模拟 VLM 延迟
        return "后台图表描述：折线图。"


@pytest.fixture
def vlm_env(tmp_path: Path):
    import pymupdf

    storage = MockStorage(tmp_path)
    pdf_dir = tmp_path / "paper-x"
    pdf_dir.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 120, 80))
    pix.clear_with(180)
    page.insert_image(pymupdf.Rect(72, 120, 300, 220), pixmap=pix)
    doc.save(str(pdf_dir / "paper.pdf"))

    from app.pipeline.document_ir import DocNode, DocumentIR

    figure = DocNode(node_id="f1", type="figure", text="", page=1, bbox=[72, 120, 300, 220], caption="Figure 1: X")
    storage.parse_artifacts["paper-x"] = DocumentIR(
        parser="pymupdf", sections=[("## 1 引言", "正文。")], nodes=[figure]
    )
    storage.chunks["paper-x"] = ["## 1 引言\n正文。"]
    storage.chunk_metas["paper-x"] = [{"element_type": "text", "section": "1 引言", "page": 1}]

    settings = SimpleNamespace(vlm_enabled=True, vlm_max_figures=5, vlm_concurrency=2, vlm_mode="async")
    return storage, settings


async def test_async_mode_returns_immediately_and_completes_in_background(vlm_env) -> None:
    from app.harness.pipeline.orchestrator import pending_figure_tasks, schedule_figure_step_async

    storage, settings = vlm_env
    broker = TaskBroker()
    await broker.create("t-bg", "paper-x")

    with patch("app.pipeline.vlm_describer.get_vlm_describer", return_value=_FakeVLM()):
        started = schedule_figure_step_async(
            paper_id="paper-x", task_id="t-bg", storage=storage, broker=broker, settings=settings
        )
        assert started is True
        assert "paper-x" in pending_figure_tasks()  # 立即返回时任务仍在执行
        # 等待后台完成
        while pending_figure_tasks():
            await asyncio.sleep(0.02)

    assert any("图片描述" in chunk for chunk in storage.chunks["paper-x"])  # 描述块已补全
    assert storage.chunk_metas["paper-x"][-1]["element_type"] == "image_desc"
    # silent 模式：不更新任务进度（不打扰已完成的主任务状态）
    state = await broker.get("t-bg")
    assert state.status == "queued"


async def test_async_mode_dedupes_while_pending(vlm_env) -> None:
    from app.harness.pipeline.orchestrator import schedule_figure_step_async

    storage, settings = vlm_env
    broker = TaskBroker()
    await broker.create("t-dup", "paper-x")
    _FakeVLM.calls = 0

    with patch("app.pipeline.vlm_describer.get_vlm_describer", return_value=_FakeVLM()):
        first = schedule_figure_step_async(
            paper_id="paper-x", task_id="t-dup", storage=storage, broker=broker, settings=settings
        )
        second = schedule_figure_step_async(  # 未完成期间重复调度
            paper_id="paper-x", task_id="t-dup", storage=storage, broker=broker, settings=settings
        )
        while "paper-x" in {
            pid
            for pid, t in __import__(
                "app.harness.pipeline.orchestrator", fromlist=["x"]
            )._background_figure_tasks.items()
            if not t.done()
        }:
            await asyncio.sleep(0.02)

    assert first is True and second is False
    assert _FakeVLM.calls == 1  # 只描述一次（防重复计费）


async def test_async_mode_background_failure_is_swallowed(vlm_env) -> None:
    from app.harness.pipeline.orchestrator import schedule_figure_step_async

    storage, settings = vlm_env
    broker = TaskBroker()
    await broker.create("t-boom", "paper-x")

    class Exploding:
        async def describe(self, png_bytes: bytes, caption: str) -> str:
            raise RuntimeError("vlm down")

    with patch("app.pipeline.vlm_describer.get_vlm_describer", return_value=Exploding()):
        started = schedule_figure_step_async(
            paper_id="paper-x", task_id="t-boom", storage=storage, broker=broker, settings=settings
        )
        assert started is True
        await asyncio.sleep(0.15)  # 后台失败不向调度方传播

    assert storage.chunks["paper-x"] == ["## 1 引言\n正文。"]  # 无描述块写入
