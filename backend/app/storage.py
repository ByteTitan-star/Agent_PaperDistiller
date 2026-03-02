"""
存储层：
1) 管理论文文件与处理结果；
2) 维护 papers.json 元数据；
3) 管理模板；
4) 维护向量索引（ChromaDB + sentence-transformers）。
"""

import hashlib
import json
import re
import unicodedata
from pathlib import Path
from typing import Any

from fastapi import UploadFile

from .schemas import PaperMeta, ResultKind

DEFAULT_SUMMARY_TEMPLATE = "tinghua.md"

RESULT_FILE_MAP: dict[ResultKind, str] = {
    "translation": "translated_full.md",
    "summary": "summary_tinghua.md",
    "improvement": "improvements.md",
}

PAPER_ID_PREFIX_RE = re.compile(r"^(\d+)")
TEMPLATE_DOMAIN_MAP: dict[str, str] = {
    "tinghua": "General",
    "backdoor_attacks": "Backdoor Attack",
    "backdoor_attack": "Backdoor Attack",
    "backdoor_defense": "Backdoor Defense",
}


def make_utf8_safe(text: str) -> str:
    return text.encode("utf-8", errors="replace").decode("utf-8")


def domain_tag_from_template(template_name: str) -> str:
    stem = Path(template_name).stem.lower()
    if stem in TEMPLATE_DOMAIN_MAP:
        return TEMPLATE_DOMAIN_MAP[stem]

    if "backdoor" in stem and "defense" in stem:
        return "Backdoor Defense"
    if "backdoor" in stem:
        return "Backdoor Attack"

    normalized = stem.replace("_", " ").replace("-", " ").strip()
    if not normalized:
        return "General"
    return " ".join(word.capitalize() for word in normalized.split())


def slugify_title(title: str) -> str:
    ascii_text = (
        unicodedata.normalize("NFKD", title)
        .encode("ascii", errors="ignore")
        .decode("ascii")
    )
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", ascii_text).strip("_").lower()
    if not slug:
        return "paper"
    return slug[:48]


def unique_keep_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if not value:
            continue
        if value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered


class VectorStore:
    """基于 ChromaDB 的本地向量索引。"""

    def __init__(
        self,
        base_dir: Path,
        db_subdir: str,
        provider: str,
        collection_name: str,
        embedding_model_name: str,
        distance_metric: str = "cosine",
    ) -> None:
        self.provider = provider.lower().strip()
        self.collection_name = collection_name
        self.embedding_model_name = embedding_model_name
        self.distance_metric = distance_metric
        self.db_dir = base_dir / db_subdir

        self._ready = False
        self._disabled_reason: str | None = None
        self._client: Any | None = None
        self._collection: Any | None = None
        self._embedder: Any | None = None

    @property
    def available(self) -> bool:
        return self._ensure_ready()

    @property
    def unavailable_reason(self) -> str | None:
        if self._ready:
            return None
        self._ensure_ready()
        return self._disabled_reason

    def _ensure_ready(self) -> bool:
        if self._ready:
            return True
        if self._disabled_reason:
            return False

        if self.provider not in {"chromadb"}:
            self._disabled_reason = f"不支持的向量库 provider: {self.provider}"
            return False

        try:
            import chromadb
        except Exception as exc:
            self._disabled_reason = f"缺少 chromadb 依赖: {exc}"
            return False

        try:
            from sentence_transformers import SentenceTransformer
        except Exception as exc:
            self._disabled_reason = f"缺少 sentence-transformers 依赖: {exc}"
            return False

        try:
            self.db_dir.mkdir(parents=True, exist_ok=True)
            self._client = chromadb.PersistentClient(path=str(self.db_dir))
            metadata = None
            if self.distance_metric in {"cosine", "l2", "ip"}:
                metadata = {"hnsw:space": self.distance_metric}
            self._collection = self._client.get_or_create_collection(
                name=self.collection_name,
                metadata=metadata,
            )
            self._embedder = SentenceTransformer(self.embedding_model_name)
            self._ready = True
            return True
        except Exception as exc:
            self._disabled_reason = f"初始化向量库失败: {exc}"
            return False

    @staticmethod
    def _chunk_id(paper_id: str, index: int) -> str:
        digest = hashlib.sha1(f"{paper_id}:{index}".encode("utf-8")).hexdigest()[:16]
        return f"{paper_id}:{index}:{digest}"

    def upsert_chunks(self, paper_id: str, chunks: list[str]) -> None:
        if not chunks:
            return
        if not self._ensure_ready():
            return

        docs = [make_utf8_safe(chunk).strip() for chunk in chunks if chunk and chunk.strip()]
        if not docs:
            return

        ids = [self._chunk_id(paper_id, idx) for idx in range(len(docs))]
        metadatas = [{"paper_id": paper_id, "chunk_index": idx} for idx in range(len(docs))]

        try:
            self._collection.delete(where={"paper_id": paper_id})
        except Exception:
            # 旧版本 Chroma 可能在 where 为空时抛异常，这里忽略即可。
            pass

        embeddings = self._embedder.encode(docs, normalize_embeddings=True)
        if hasattr(embeddings, "tolist"):
            embeddings = embeddings.tolist()

        self._collection.add(
            ids=ids,
            documents=docs,
            metadatas=metadatas,
            embeddings=embeddings,
        )

    def query(self, paper_id: str, question: str, top_k: int) -> list[str]:
        if not question.strip():
            return []
        if not self._ensure_ready():
            return []

        query_embeddings = self._embedder.encode([question], normalize_embeddings=True)
        if hasattr(query_embeddings, "tolist"):
            query_embeddings = query_embeddings.tolist()

        result = self._collection.query(
            query_embeddings=query_embeddings,
            n_results=max(1, top_k),
            where={"paper_id": paper_id},
            include=["documents", "distances", "metadatas"],
        )

        documents = result.get("documents", [])
        if not documents:
            return []

        first_batch = documents[0] or []
        return [make_utf8_safe(doc) for doc in first_batch if isinstance(doc, str) and doc.strip()]


class Storage:
    """文件与元数据存储层。"""

    def __init__(
        self,
        base_dir: Path,
        templates_dir: Path,
        vector_provider: str = "chromadb",
        vector_collection_name: str = "paper_chunks",
        vector_db_subdir: str = "vectordb",
        embedding_model_name: str = "sentence-transformers/all-MiniLM-L6-v2",
        vector_distance_metric: str = "cosine",
    ) -> None:
        self.base_dir = base_dir
        self.raw_dir = self.base_dir / "raw"
        self.processed_dir = self.base_dir / "processed"
        self.meta_file = self.base_dir / "papers.json"
        self.templates_dir = templates_dir
        self._ensure_structure()

        self.vector_store = VectorStore(
            base_dir=self.base_dir,
            db_subdir=vector_db_subdir,
            provider=vector_provider,
            collection_name=vector_collection_name,
            embedding_model_name=embedding_model_name,
            distance_metric=vector_distance_metric,
        )

    def _ensure_structure(self) -> None:
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.processed_dir.mkdir(parents=True, exist_ok=True)
        self.templates_dir.mkdir(parents=True, exist_ok=True)

        if not self.meta_file.exists():
            self.meta_file.write_text("[]", encoding="utf-8")

        default_template_path = self.templates_dir / DEFAULT_SUMMARY_TEMPLATE
        if not default_template_path.exists():
            default_template_path.write_text(
                (
                    "# Core Ideas (Tinghua)\n\n"
                    "## 1. Problem Statement\n"
                    "- What problem does this paper solve?\n\n"
                    "## 2. Method\n"
                    "- Main idea and technical route.\n\n"
                    "## 3. Experiments\n"
                    "- Dataset / metrics / key results.\n\n"
                    "## 4. Strengths and Limits\n"
                    "- Strong points and known limitations.\n\n"
                    "## 5. Takeaways\n"
                    "- Reusable insights and practical notes.\n"
                ),
                encoding="utf-8",
            )

    def vector_status(self) -> tuple[bool, str | None]:
        available = self.vector_store.available
        return available, self.vector_store.unavailable_reason

    def _load_papers(self) -> list[dict]:
        return json.loads(self.meta_file.read_text(encoding="utf-8"))

    def _save_papers(self, papers: list[dict]) -> None:
        payload = json.dumps(papers, ensure_ascii=False, indent=2)
        self.meta_file.write_text(make_utf8_safe(payload), encoding="utf-8")

    def _next_paper_sequence(self) -> int:
        papers = self._load_papers()
        max_seq = 0
        pattern = re.compile(r"^(\d+)\.")

        for paper in papers:
            paper_id = paper.get("paper_id", "")
            match = pattern.match(paper_id)
            if not match:
                continue
            seq = int(match.group(1))
            if seq > max_seq:
                max_seq = seq
        return max_seq + 1

    def allocate_paper_id(self, title: str) -> str:
        seq = self._next_paper_sequence()
        safe_title = re.sub(r'[\\/*?:"<>|]', "", title).strip()
        return f"{seq}.{safe_title}"

    def list_papers(self) -> list[PaperMeta]:
        papers: list[PaperMeta] = []
        for item in self._load_papers():
            paper = PaperMeta.model_validate(item)
            template_tag = domain_tag_from_template(paper.summary_template)
            paper.domain_tags = unique_keep_order([template_tag, *paper.domain_tags])
            papers.append(paper)
        return sorted(papers, key=lambda p: p.created_at, reverse=True)

    def get_paper(self, paper_id: str) -> PaperMeta | None:
        for paper in self.list_papers():
            if paper.paper_id == paper_id:
                return paper
        return None

    def upsert_paper(self, payload: PaperMeta) -> None:
        papers = self._load_papers()
        serialized = payload.model_dump()
        for idx, item in enumerate(papers):
            if item["paper_id"] == payload.paper_id:
                papers[idx] = serialized
                self._save_papers(papers)
                return
        papers.append(serialized)
        self._save_papers(papers)

    def update_paper_status(self, paper_id: str, status: str, domain_tags: list[str] | None = None) -> None:
        papers = self._load_papers()
        for item in papers:
            if item["paper_id"] != paper_id:
                continue
            item["status"] = status
            if domain_tags is None:
                continue
            template_name = str(item.get("summary_template", DEFAULT_SUMMARY_TEMPLATE))
            template_tag = domain_tag_from_template(template_name)
            item["domain_tags"] = unique_keep_order([template_tag, *domain_tags])
        self._save_papers(papers)

    def save_upload(self, paper_id: str, upload: UploadFile, source_filename: str | None = None) -> Path:
        output_pdf = self.paper_output_dir(paper_id) / "source.pdf"
        legacy_pdf = self.raw_dir / f"{paper_id}.pdf"

        upload.file.seek(0)
        with output_pdf.open("wb") as output, legacy_pdf.open("wb") as backup:
            while True:
                chunk = upload.file.read(1024 * 1024)
                if not chunk:
                    break
                output.write(chunk)
                backup.write(chunk)
        upload.file.seek(0)

        if source_filename:
            source_name_file = self.paper_output_dir(paper_id) / "source_filename.txt"
            source_name_file.write_text(make_utf8_safe(source_filename), encoding="utf-8")
        return output_pdf

    def pdf_path(self, paper_id: str) -> Path:
        preferred = self.processed_dir / paper_id / "source.pdf"
        if preferred.exists():
            return preferred
        return self.raw_dir / f"{paper_id}.pdf"

    def paper_output_dir(self, paper_id: str) -> Path:
        output_dir = self.processed_dir / paper_id
        output_dir.mkdir(parents=True, exist_ok=True)
        return output_dir

    def _summary_output_name(self, summary_template: str | None = None) -> str:
        template_name = Path(summary_template or DEFAULT_SUMMARY_TEMPLATE).name
        template_stem = Path(template_name).stem or Path(DEFAULT_SUMMARY_TEMPLATE).stem
        return f"summary_{template_stem}.md"

    def _result_output_name(self, kind: ResultKind, summary_template: str | None = None) -> str:
        if kind == "summary":
            return self._summary_output_name(summary_template)
        return RESULT_FILE_MAP[kind]

    def write_result(
        self,
        paper_id: str,
        kind: ResultKind,
        content: str,
        summary_template: str | None = None,
    ) -> None:
        output_file = self.paper_output_dir(paper_id) / self._result_output_name(kind, summary_template)
        output_file.write_text(make_utf8_safe(content), encoding="utf-8")

    def read_result(self, paper_id: str, kind: ResultKind, summary_template: str | None = None) -> str:
        output_file = self.paper_output_dir(paper_id) / self._result_output_name(kind, summary_template)
        if not output_file.exists():
            return ""
        return output_file.read_text(encoding="utf-8")

    def save_chunks(self, paper_id: str, chunks: list[str]) -> None:
        path = self.paper_output_dir(paper_id) / "chunks.json"
        safe_chunks = [make_utf8_safe(chunk) for chunk in chunks]
        path.write_text(
            json.dumps(safe_chunks, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        try:
            self.vector_store.upsert_chunks(paper_id, safe_chunks)
        except Exception:
            # 向量索引失败时不阻塞主流程，问答阶段自动回退词法检索。
            pass

    def load_chunks(self, paper_id: str) -> list[str]:
        path = self.paper_output_dir(paper_id) / "chunks.json"
        if not path.exists():
            return []
        return json.loads(path.read_text(encoding="utf-8"))

    def search_similar_chunks(self, paper_id: str, question: str, top_k: int) -> list[str]:
        try:
            return self.vector_store.query(paper_id=paper_id, question=question, top_k=top_k)
        except Exception:
            return []

    def list_templates(self) -> list[str]:
        templates: list[str] = []
        for path in self.templates_dir.glob("*.md"):
            templates.append(path.name)
        return sorted(templates)

    def read_template(self, template_name: str) -> str:
        safe_template_name = Path(template_name).name
        path = self.templates_dir / safe_template_name
        if not path.exists():
            path = self.templates_dir / DEFAULT_SUMMARY_TEMPLATE
        return path.read_text(encoding="utf-8")
