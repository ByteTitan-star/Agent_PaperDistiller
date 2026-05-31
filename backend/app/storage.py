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

# 默认使用的摘要模板文件名
DEFAULT_SUMMARY_TEMPLATE = "tinghua.md"

# 结果类型到文件名的映射
RESULT_FILE_MAP: dict[ResultKind, str] = {
    "translation": "translated_full.md",      # 翻译结果
    "summary": "summary_tinghua.md",          # 摘要结果
    "improvement": "improvements.md",         # 改进建议
}

# 模板文件名到领域标签的映射
TEMPLATE_DOMAIN_MAP: dict[str, str] = {
    "tinghua": "General",
    "backdoor_attacks": "Backdoor Attack",
    "backdoor_attack": "Backdoor Attack",
    "backdoor_defense": "Backdoor Defense",
}


def make_utf8_safe(text: str) -> str:
    """
    将文本转为UTF-8安全格式，无法编码的字符用替换符代替。
    防止写入文件时出现编码错误。
    """
    return text.encode("utf-8", errors="replace").decode("utf-8")


def domain_tag_from_template(template_name: str) -> str:
    """
    根据模板文件名推断论文领域标签。
    
    处理逻辑：
    - 先查映射表，有则直接返回
    - 包含"backdoor"和"defense" -> Backdoor Defense
    - 包含"backdoor" -> Backdoor Attack
    - 其他情况将文件名转为首字母大写的标签
    """
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
    """
    将论文标题转为URL安全的短字符串（slug）。
    
    处理步骤：
    1. 规范化Unicode（NFKD分解）
    2. 移除非ASCII字符
    3. 将非字母数字字符转为下划线
    4. 截取前48字符
    """
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
    """
    去重同时保持原有顺序。
    用于领域标签列表，确保模板标签在前，推断标签在后且不重复。
    """
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


class OSSClient:
    """阿里云 OSS 封装，支持上传、下载、签名 URL、删除。"""

    def __init__(
        self,
        access_key_id: str,
        access_key_secret: str,
        endpoint: str,
        bucket_name: str,
        prefix: str = "papers",
    ) -> None:
        self._bucket_name = bucket_name
        self._prefix = prefix.strip("/")
        self._bucket = None
        if access_key_id and access_key_secret:
            try:
                import oss2
                auth = oss2.Auth(access_key_id, access_key_secret)
                self._bucket = oss2.Bucket(auth, endpoint, bucket_name)
            except ImportError:
                pass

    @property
    def available(self) -> bool:
        return self._bucket is not None

    def _key(self, *parts: str) -> str:
        return "/".join([self._prefix, *parts])

    def upload_file(self, local_path: Path, *key_parts: str) -> str:
        """上传本地文件到 OSS，返回对象 key。"""
        if not self._bucket:
            return ""
        key = self._key(*key_parts)
        self._bucket.put_object_from_file(key, str(local_path))
        return key

    def upload_bytes(self, data: bytes, content_type: str, *key_parts: str) -> str:
        """上传字节数据到 OSS。"""
        if not self._bucket:
            return ""
        key = self._key(*key_parts)
        headers = {"Content-Type": content_type}
        self._bucket.put_object(key, data, headers=headers)
        return key

    def get_signed_url(self, *key_parts: str, expires: int = 3600) -> str:
        """生成签名下载 URL，默认 1 小时有效。"""
        if not self._bucket:
            return ""
        key = self._key(*key_parts)
        return self._bucket.sign_url("GET", key, expires)

    def delete_prefix(self, *key_parts: str) -> None:
        """删除指定前缀下的所有对象。"""
        if not self._bucket:
            return
        prefix = self._key(*key_parts) + "/"
        for obj in oss2.ObjectIterator(self._bucket, prefix=prefix):
            self._bucket.delete_object(obj.key)

    def exists(self, *key_parts: str) -> bool:
        if not self._bucket:
            return False
        key = self._key(*key_parts)
        return self._bucket.object_exists(key)




class VectorStore:
    """
    基于 ChromaDB 的本地向量索引封装类。
    
    核心功能：
    - 延迟初始化（第一次使用时才加载模型和数据库）
    - 文本向量化（使用 sentence-transformers）
    - 向量存储与检索（使用 ChromaDB）
    """

    def __init__(
        self,
        base_dir: Path,                    # 基础数据目录
        db_subdir: str,                    # 向量数据库子目录名
        provider: str,                     # 向量库提供商（目前仅支持chromadb）
        collection_name: str,              # 集合名称（类似数据库表名）
        embedding_model_name: str,         # 嵌入模型名称
        distance_metric: str = "cosine",   # 距离度量方式：cosine/l2/ip
    ) -> None:
        self.provider = provider.lower().strip()
        self.collection_name = collection_name
        self.embedding_model_name = embedding_model_name
        self.distance_metric = distance_metric
        self.db_dir = base_dir / db_subdir  # 向量数据库实际存储路径

        # 延迟初始化相关状态
        self._ready = False                 # 是否已完成初始化
        self._disabled_reason: str | None = None  # 初始化失败原因
        self._client: Any | None = None     # ChromaDB客户端
        self._collection: Any | None = None # ChromaDB集合
        self._embedder: Any | None = None   # 句子嵌入模型

    @property
    def available(self) -> bool:
        """向量库是否可用（已初始化成功）"""
        return self._ensure_ready()

    @property
    def unavailable_reason(self) -> str | None:
        """获取向量库不可用的原因（如果不可用）"""
        if self._ready:
            return None
        self._ensure_ready()
        return self._disabled_reason

    def _ensure_ready(self) -> bool:
        """
        延迟初始化：检查并完成向量库初始化。
        
        初始化流程：
        1. 检查provider是否支持
        2. 导入chromadb依赖
        3. 导入sentence-transformers依赖
        4. 创建持久化客户端
        5. 获取或创建集合
        6. 加载嵌入模型
        
        任一环节失败都会记录原因并返回False，但不抛出异常。
        """
        if self._ready:
            return True
        if self._disabled_reason:
            return False

        # 检查提供商支持
        if self.provider not in {"chromadb"}:
            self._disabled_reason = f"不支持的向量库 provider: {self.provider}"
            return False

        # 检查依赖安装
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

        # 初始化客户端和模型
        try:
            self.db_dir.mkdir(parents=True, exist_ok=True)
            # 创建持久化客户端（数据存放到本地目录）
            self._client = chromadb.PersistentClient(path=str(self.db_dir))
            
            # 设置HNSW索引的空间度量方式
            metadata = None
            if self.distance_metric in {"cosine", "l2", "ip"}:
                metadata = {"hnsw:space": self.distance_metric}
            
            # 获取或创建集合（如果不存在则自动创建）
            self._collection = self._client.get_or_create_collection(
                name=self.collection_name,
                metadata=metadata,
            )
            
            # 加载句子嵌入模型（本地缓存，首次下载）
            self._embedder = SentenceTransformer(self.embedding_model_name)
            self._ready = True
            return True
        except Exception as exc:
            self._disabled_reason = f"初始化向量库失败: {exc}"
            return False

    @staticmethod
    def _chunk_id(paper_id: str, index: int) -> str:
        """
        生成文本块的唯一标识符。
        
        格式：{paper_id}:{index}:{hash}
        使用SHA1哈希确保ID的唯一性和稳定性。
        """
        digest = hashlib.sha1(f"{paper_id}:{index}".encode("utf-8")).hexdigest()[:16]
        return f"{paper_id}:{index}:{digest}"


    def upsert_chunks(self, paper_id: str, chunks: list[str]) -> None:
        """
        将文本块存入向量数据库（插入或更新）。
        
        流程：
        1. 清理文本（UTF-8安全）
        2. 生成唯一ID列表
        3. 生成元数据（包含paper_id用于过滤）
        4. 删除该论文旧数据（避免重复）
        5. 文本向量化（embedding）
        6. 批量存入ChromaDB
        
        注意：如果向量库未初始化，静默跳过不报错。
        """
        if not chunks:
            return
        if not self._ensure_ready():
            return

        # 清理文本，去除空块
        docs = [make_utf8_safe(chunk).strip() for chunk in chunks if chunk and chunk.strip()]
        if not docs:
            return

        # 生成ID和元数据
        ids = [self._chunk_id(paper_id, idx) for idx in range(len(docs))]
        # 元数据每个文本块的附加信息，不是向量本身，一起存储
        # 存的是paper_id论文唯一标识（ 和 chunk_index 块序号
        metadatas = [{"paper_id": paper_id, "chunk_index": idx} for idx in range(len(docs))]

        # 删除该论文已有数据（全量更新策略）
        try:
            self._collection.delete(where={"paper_id": paper_id})
        except Exception:
            # 旧版本 Chroma 可能在 where 为空时抛异常，这里忽略即可。
            pass

        # 文本 -> 向量（归一化，便于余弦相似度计算）
        # 完成所有块的向量化
        embeddings = self._embedder.encode(docs, normalize_embeddings=True)



        if hasattr(embeddings, "tolist"):
            embeddings = embeddings.tolist()

        # 批量存入ChromaDB
        self._collection.add(
            ids=ids,
            documents=docs,        # 原始文本（可选，用于结果返回）
            metadatas=metadatas,   # 元数据（用于过滤）
            embeddings=embeddings, # 向量（用于相似度搜索）
        )

    def query(self, paper_id: str, question: str, top_k: int) -> list[str]:
        """
        向量相似度检索：根据问题查找最相关的文本块。
        
        流程：
        1. 问题文本向量化（使用相同的嵌入模型）
        2. 在指定论文的块中搜索最相似的top_k个
        3. 返回原始文本列表
        
        过滤条件：where={"paper_id": paper_id} 确保只查当前论文
        """
        if not question.strip():
            return []
        if not self._ensure_ready():
            return []

        # 问题向量化
        query_embeddings = self._embedder.encode([question], normalize_embeddings=True)


        if hasattr(query_embeddings, "tolist"):
            query_embeddings = query_embeddings.tolist()

        # 执行相似度查询
        result = self._collection.query(
            query_embeddings=query_embeddings,     # 查询向量
            n_results=max(1, top_k),              # 至少返回1个
            where={"paper_id": paper_id},          # 仅搜索指定论文的块   找论文对应的块进行快速检索
            include=["documents", "distances", "metadatas"],  # 返回文档内容和距离
        )

        # 提取结果文本
        documents = result.get("documents", [])
        if not documents:
            return []

        first_batch = documents[0] or []
        # 过滤掉异常数据，确保返回干净的文本列表
        return [make_utf8_safe(doc) for doc in first_batch if isinstance(doc, str) and doc.strip()]


class Storage:
    """
    文件与元数据存储层。管理所有数据的持久化
    生成目录的结果；调用模块的位置
    
    职责：
    1. 管理目录结构（raw/processed/templates）
    2. 维护papers.json元数据
    3. 管理论文文件上传和结果保存
    4. 封装VectorStore提供向量检索能力
    """

    def __init__(
        self,
        base_dir: Path,                                    # 基础数据目录
        templates_dir: Path,                               # 模板目录
        vector_provider: str = "chromadb",                 # 向量库提供商
        vector_collection_name: str = "paper_chunks",      # 向量集合名
        vector_db_subdir: str = "vectordb",                # 向量库子目录
        embedding_model_name: str = "sentence-transformers/all-MiniLM-L6-v2",  # 嵌入模型
        vector_distance_metric: str = "cosine",            # 距离度量
        oss_client: OSSClient | None = None,               # OSS 客户端（可选）
    ) -> None:
        self.base_dir = base_dir
        self.raw_dir = self.base_dir / "raw"               # 原始上传PDF
        self.processed_dir = self.base_dir / "processed"   # 处理结果
        self.meta_file = self.base_dir / "papers.json"     # 论文元数据
        self.templates_dir = templates_dir                 # 模板文件
        self.default_template: str = DEFAULT_SUMMARY_TEMPLATE  # 可在启动时从 DB 覆盖
        self.oss = oss_client                              # OSS 客户端
        
        # 确保目录结构存在
        self._ensure_structure()

        # 初始化向量存储（延迟加载）
        self.vector_store = VectorStore(
            base_dir=self.base_dir,
            db_subdir=vector_db_subdir,
            provider=vector_provider,
            collection_name=vector_collection_name,
            embedding_model_name=embedding_model_name,
            distance_metric=vector_distance_metric,
        )

    def _ensure_structure(self) -> None:
        """
        确保必要的目录和默认文件存在。
        
        创建：
        - base_dir/          根目录
        - base_dir/raw/      原始PDF存储
        - base_dir/processed/ 处理结果存储
        - templates_dir/     模板目录
        
        如果不存在默认模板，创建tinghua.md模板。
        """
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.processed_dir.mkdir(parents=True, exist_ok=True)
        self.templates_dir.mkdir(parents=True, exist_ok=True)

        # 初始化空的论文列表
        if not self.meta_file.exists():
            self.meta_file.write_text("[]", encoding="utf-8")

        # 创建默认模板（如果不存在）
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
        """获取向量库状态（是否可用，不可用原因）"""
        available = self.vector_store.available
        return available, self.vector_store.unavailable_reason

    def _load_papers(self) -> list[dict]:
        """从papers.json加载论文元数据列表"""
        return json.loads(self.meta_file.read_text(encoding="utf-8"))

    def _save_papers(self, papers: list[dict]) -> None:
        """保存论文元数据列表到papers.json"""
        payload = json.dumps(papers, ensure_ascii=False, indent=2)
        self.meta_file.write_text(make_utf8_safe(payload), encoding="utf-8")

    def allocate_paper_id(self, title: str) -> str:
        """
        为新论文分配唯一ID。

        格式：{短UUID}  (8位hex，无重复风险)
        例："a3f1b2c4"
        """
        import uuid

        return uuid.uuid4().hex[:8]

    def list_papers(self) -> list[PaperMeta]:
        """
        获取所有论文列表，按创建时间倒序。
        
        同时根据模板推断并补充领域标签。
        """
        papers: list[PaperMeta] = []
        for item in self._load_papers():
            paper = PaperMeta.model_validate(item)
            template_tag = domain_tag_from_template(paper.summary_template)
            paper.domain_tags = unique_keep_order([template_tag, *paper.domain_tags])
            papers.append(paper)
        return sorted(papers, key=lambda p: p.created_at, reverse=True)

    def get_paper(self, paper_id: str) -> PaperMeta | None:
        """根据ID获取单篇论文元数据"""
        for paper in self.list_papers():
            if paper.paper_id == paper_id:
                return paper
        return None

    def upsert_paper(self, payload: PaperMeta) -> None:
        """
        插入或更新论文元数据。
        
        如果paper_id已存在则更新，否则追加。
        """
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
        """
        更新论文处理状态和领域标签。
        
        领域标签会去重，并确保模板标签在前。
        """
        papers = self._load_papers()
        for item in papers:
            if item["paper_id"] != paper_id:
                continue
            item["status"] = status
            if domain_tags is None:
                continue
            template_name = str(item.get("summary_template", self.default_template))
            template_tag = domain_tag_from_template(template_name)
            item["domain_tags"] = unique_keep_order([template_tag, *domain_tags])
        self._save_papers(papers)

    def save_upload(self, paper_id: str, upload: UploadFile, source_filename: str | None = None) -> Path:
        """
        保存用户上传的PDF文件。
        
        双写策略：
        1. processed/{paper_id}/source.pdf（主存储）
        2. raw/{paper_id}.pdf（备份/兼容）
        
        可选保存原始文件名。
        """
        output_pdf = self.paper_output_dir(paper_id) / "source.pdf"
        legacy_pdf = self.raw_dir / f"{paper_id}.pdf"

        # 分块读取并双写（1MB缓冲区）
        upload.file.seek(0)
        with output_pdf.open("wb") as output, legacy_pdf.open("wb") as backup:
            while True:
                chunk = upload.file.read(1024 * 1024)
                if not chunk:
                    break
                output.write(chunk)
                backup.write(chunk)
        upload.file.seek(0)

        # 保存原始文件名（用于显示）
        if source_filename:
            source_name_file = self.paper_output_dir(paper_id) / "source_filename.txt"
            source_name_file.write_text(make_utf8_safe(source_filename), encoding="utf-8")

        # 上传到 OSS
        self._upload_to_oss(output_pdf, paper_id, "source.pdf")
        return output_pdf

    def pdf_path(self, paper_id: str) -> Path:
        """
        获取论文PDF路径。

        优先返回processed目录，如果不存在则回退到raw目录。
        """
        preferred = self.processed_dir / paper_id / "source.pdf"
        if preferred.exists():
            return preferred
        return self.raw_dir / f"{paper_id}.pdf"

    def oss_pdf_signed_url(self, paper_id: str, expires: int = 3600) -> str | None:
        """获取 PDF 的 OSS 签名 URL，不可用时返回 None。"""
        if not self.oss or not self.oss.available:
            return None
        return self.oss.get_signed_url(paper_id, "source.pdf", expires=expires)

    def _upload_to_oss(self, local_path: Path, *key_parts: str) -> None:
        """异步安全地将本地文件上传到 OSS（失败不阻塞）。"""
        if not self.oss or not self.oss.available:
            return
        try:
            self.oss.upload_file(local_path, *key_parts)
        except Exception:
            pass

    def paper_output_dir(self, paper_id: str) -> Path:
        """
        获取论文的输出目录，如果不存在则创建。
        
        路径：processed/{paper_id}/
        """
        output_dir = self.processed_dir / paper_id
        output_dir.mkdir(parents=True, exist_ok=True)
        return output_dir

    def _summary_output_name(self, summary_template: str | None = None) -> str:
        """根据模板名生成摘要结果文件名"""
        template_name = Path(summary_template or self.default_template).name
        template_stem = Path(template_name).stem or Path(self.default_template).stem
        return f"summary_{template_stem}.md"

    def _result_output_name(self, kind: ResultKind, summary_template: str | None = None) -> str:
        """根据结果类型生成文件名"""
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
        """将处理结果写入文件"""
        output_file = self.paper_output_dir(paper_id) / self._result_output_name(kind, summary_template)
        output_file.write_text(make_utf8_safe(content), encoding="utf-8")
        self._upload_to_oss(output_file, paper_id, output_file.name)

    def read_result(self, paper_id: str, kind: ResultKind, summary_template: str | None = None) -> str:
        """读取处理结果，如果不存在返回空字符串"""
        output_file = self.paper_output_dir(paper_id) / self._result_output_name(kind, summary_template)
        if not output_file.exists():
            return ""
        return output_file.read_text(encoding="utf-8")

    def save_chunks(self, paper_id: str, chunks: list[str]) -> None:
        """
        保存文本块到本地JSON，并同步到向量数据库。
        
        双保险策略：
        1. 总是保存chunks.json（本地备份）
        2. 尝试存入向量库（失败不阻塞）
        
        向量存储失败时静默跳过，问答阶段会自动回退到词法检索。
        """
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
        """从本地JSON加载文本块"""
        path = self.paper_output_dir(paper_id) / "chunks.json"
        if not path.exists():
            return []
        return json.loads(path.read_text(encoding="utf-8"))

    def search_similar_chunks(self, paper_id: str, question: str, top_k: int) -> list[str]:
        """
        搜索与问题最相似的文本块。
        
        如果向量检索失败，返回空列表（上层应回退到词法检索）。
        """
        try:
            return self.vector_store.query(paper_id=paper_id, question=question, top_k=top_k)
        except Exception:
            return []

    def list_templates(self) -> list[str]:
        """列出所有可用的模板文件（*.md）"""
        templates: list[str] = []
        for path in self.templates_dir.glob("*.md"):
            templates.append(path.name)
        return sorted(templates)


    def read_template(self, template_name: str) -> str:
        """
        读取模板内容（文件系统），如果不存在返回默认模板。

        安全措施：使用Path.name防止路径遍历攻击。
        """
        safe_template_name = Path(template_name).name
        path = self.templates_dir / safe_template_name
        if not path.exists():
            path = self.templates_dir / self.default_template
        return path.read_text(encoding="utf-8")


async def resolve_template_content(template_name: str, user_id: int | None = None) -> str | None:
    """从数据库解析模板内容，优先用户私有，其次公开模板。"""
    from .database import async_session_factory
    from .models import Template
    from sqlalchemy import select, or_

    async with async_session_factory() as session:
        # 1. 用户私有模板
        if user_id is not None:
            result = await session.execute(
                select(Template).where(
                    Template.name == template_name, Template.user_id == user_id
                )
            )
            t = result.scalar_one_or_none()
            if t:
                return t.content

        # 2. 公开模板（系统 + 管理员创建）
        result = await session.execute(
            select(Template).where(Template.name == template_name, Template.user_id.is_(None))
        )
        t = result.scalar_one_or_none()
        if t:
            return t.content

    return None
