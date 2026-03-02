"""Skill registry with optional semantic retrieval and safe execution."""

from __future__ import annotations

import hashlib
import importlib.util
import inspect
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


@dataclass
class LoadedSkill:
    tool_name: str
    tool_schema: dict[str, Any]
    description: str
    skill_prompt: str
    callable_fn: Callable[..., Any]
    source_path: Path


class SkillRegistry:
    """Load skills from disk, retrieve relevant tools, and execute tool functions."""

    def __init__(
        self,
        skills_root: Path,
        vector_db_dir: Path,
        embedding_model_name: str,
        provider: str = "chromadb",
        collection_name: str = "skills_collection",
    ) -> None:
        self.skills_root = skills_root
        self.vector_db_dir = vector_db_dir
        self.embedding_model_name = embedding_model_name
        self.provider = provider
        self.collection_name = collection_name

        self._skills: dict[str, LoadedSkill] = {}
        self._messages: list[str] = []

        self._semantic_ready = False
        self._collection: Any | None = None
        self._embedder: Any | None = None

    def load(self) -> int:
        """Scan `skills_root` and load all valid skills."""
        self._skills = {}
        self._messages = []
        self._semantic_ready = False
        self._collection = None
        self._embedder = None

        if not self.skills_root.exists():
            self._messages.append(f"skills directory not found: {self.skills_root}")
            return 0

        for skill_dir in sorted(self.skills_root.iterdir(), key=lambda p: p.name.lower()):
            if not skill_dir.is_dir():
                continue
            loaded = self._load_single_skill(skill_dir)
            if loaded is None:
                continue
            self._skills[loaded.tool_name] = loaded

        self._index_skill_descriptions()
        return len(self._skills)

    def status(self) -> dict[str, Any]:
        return {
            "skill_count": len(self._skills),
            "semantic_ready": self._semantic_ready,
            "messages": self._messages[:],
            "skills": sorted(self._skills.keys()),
        }

    def all_tools(self) -> list[LoadedSkill]:
        return list(self._skills.values())

    def select_tools(self, query: str, top_k: int, min_similarity: float = 0.8) -> list[LoadedSkill]:
        """Return only high-match tools. If no high match, return [] explicitly."""
        if not self._skills:
            return []

        limit = max(1, min(top_k, len(self._skills)))
        if not query.strip():
            return []

        if not (self._semantic_ready and self._collection is not None and self._embedder is not None):
            return []

        try:
            query_embedding = self._embedder.encode([query], normalize_embeddings=True)
            if hasattr(query_embedding, "tolist"):
                query_embedding = query_embedding.tolist()

            result = self._collection.query(
                query_embeddings=query_embedding,
                n_results=limit,
                include=["metadatas", "distances", "documents"],
            )

            metadata_batches = result.get("metadatas", [])
            first_batch = metadata_batches[0] if metadata_batches else []
            distance_batches = result.get("distances", [])
            first_distances = distance_batches[0] if distance_batches else []

            selected: list[LoadedSkill] = []
            for idx, metadata in enumerate(first_batch):
                if not isinstance(metadata, dict):
                    continue
                tool_name = str(metadata.get("tool_name", "")).strip()
                if not tool_name or tool_name not in self._skills:
                    continue

                distance = first_distances[idx] if idx < len(first_distances) else None
                try:
                    # Chroma cosine distance -> similarity.
                    similarity = 1.0 - float(distance)
                except (TypeError, ValueError):
                    similarity = 0.0

                if similarity < min_similarity:
                    continue
                if any(skill.tool_name == tool_name for skill in selected):
                    continue
                selected.append(self._skills[tool_name])

            return selected[:limit]
        except Exception as exc:
            self._messages.append(f"skills semantic retrieval failed: {exc}")
            return []

    def build_openai_tools(self, selected_skills: list[LoadedSkill]) -> list[dict[str, Any]]:
        return [skill.tool_schema for skill in selected_skills]

    def build_skill_hint(self, selected_skills: list[LoadedSkill]) -> str:
        if not selected_skills:
            return ""
        lines = ["Matched skills for this query:"]
        for skill in selected_skills:
            lines.append(f"- {skill.tool_name}: {skill.description}")
        return "\n".join(lines)

    def execute(self, tool_name: str, arguments: dict[str, Any], context: dict[str, Any] | None = None) -> dict[str, Any]:
        skill = self._skills.get(tool_name)
        if not skill:
            return {"error": f"tool not registered: {tool_name}"}

        safe_context = context or {}
        safe_args = arguments or {}
        try:
            signature = inspect.signature(skill.callable_fn)
            kwargs: dict[str, Any] = {}
            accepts_var_kwargs = any(
                parameter.kind == inspect.Parameter.VAR_KEYWORD
                for parameter in signature.parameters.values()
            )

            for name, parameter in signature.parameters.items():
                if name == "_context":
                    kwargs[name] = safe_context
                    continue
                if name in safe_args:
                    kwargs[name] = safe_args[name]
                    continue
                if parameter.default is inspect.Parameter.empty and parameter.kind not in {
                    inspect.Parameter.VAR_POSITIONAL,
                    inspect.Parameter.VAR_KEYWORD,
                }:
                    return {"error": f"missing required argument: {name}"}

            if accepts_var_kwargs:
                for key, value in safe_args.items():
                    kwargs.setdefault(key, value)

            result = skill.callable_fn(**kwargs)
            if isinstance(result, dict):
                return result
            return {"result": result}
        except Exception as exc:
            return {"error": f"tool execution failed: {exc}"}

    def _load_single_skill(self, skill_dir: Path) -> LoadedSkill | None:
        skill_md_path = skill_dir / "SKILL.md"
        schema_path = skill_dir / "openai.yaml"
        if not skill_md_path.exists() or not schema_path.exists():
            self._messages.append(f"skip {skill_dir.name}: missing SKILL.md or openai.yaml")
            return None

        config = self._read_yaml(schema_path)
        if not isinstance(config, dict):
            self._messages.append(f"skip {skill_dir.name}: failed to parse openai.yaml")
            return None

        tool_schema = self._extract_tool_schema(config)
        if not tool_schema:
            self._messages.append(f"skip {skill_dir.name}: invalid tool schema")
            return None

        function_block = tool_schema.get("function", {})
        tool_name = str(function_block.get("name", "")).strip()
        if not tool_name:
            self._messages.append(f"skip {skill_dir.name}: missing function.name")
            return None

        runtime = config.get("runtime", {}) if isinstance(config.get("runtime"), dict) else {}
        entrypoint = str(runtime.get("entrypoint", "")).strip()
        callable_name = str(runtime.get("callable", "run")).strip() or "run"
        if not entrypoint:
            self._messages.append(f"skip {skill_dir.name}: runtime.entrypoint is empty")
            return None

        module_path = skill_dir / entrypoint
        callable_fn = self._load_callable(module_path, callable_name)
        if callable_fn is None:
            self._messages.append(f"skip {skill_dir.name}: cannot load {entrypoint}:{callable_name}")
            return None

        skill_prompt = skill_md_path.read_text(encoding="utf-8")
        description = str(function_block.get("description", "")).strip()
        if not description:
            description = self._first_line(skill_prompt) or f"{tool_name} skill"
            function_block["description"] = description

        return LoadedSkill(
            tool_name=tool_name,
            tool_schema=tool_schema,
            description=description,
            skill_prompt=skill_prompt,
            callable_fn=callable_fn,
            source_path=module_path,
        )

    def _extract_tool_schema(self, config: dict[str, Any]) -> dict[str, Any] | None:
        tool_block = config.get("tool")
        if not isinstance(tool_block, dict):
            tool_block = config

        if tool_block.get("type") == "function" and isinstance(tool_block.get("function"), dict):
            return {"type": "function", "function": tool_block["function"]}

        if isinstance(tool_block.get("function"), dict):
            return {"type": "function", "function": tool_block["function"]}

        if all(key in tool_block for key in ["name", "description", "parameters"]):
            return {
                "type": "function",
                "function": {
                    "name": tool_block["name"],
                    "description": tool_block["description"],
                    "parameters": tool_block["parameters"],
                },
            }
        return None

    def _load_callable(self, module_path: Path, callable_name: str) -> Callable[..., Any] | None:
        if not module_path.exists():
            return None

        module_hash = hashlib.sha1(str(module_path).encode("utf-8")).hexdigest()[:16]
        module_name = f"agent_skill_{module_hash}"
        spec = importlib.util.spec_from_file_location(module_name, module_path)
        if spec is None or spec.loader is None:
            return None

        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        fn = getattr(module, callable_name, None)
        if not callable(fn):
            return None
        return fn

    def _read_yaml(self, path: Path) -> dict[str, Any] | None:
        text = path.read_text(encoding="utf-8")
        try:
            import yaml
        except Exception:
            try:
                maybe_json = json.loads(text)
                return maybe_json if isinstance(maybe_json, dict) else None
            except Exception:
                self._messages.append("PyYAML missing and openai.yaml is not JSON")
                return None

        try:
            payload = yaml.safe_load(text)
            return payload if isinstance(payload, dict) else None
        except Exception as exc:
            self._messages.append(f"YAML parse failed: {exc}")
            return None

    @staticmethod
    def _first_line(text: str) -> str:
        for line in text.splitlines():
            stripped = line.strip()
            if stripped:
                return stripped[:200]
        return ""

    def _index_skill_descriptions(self) -> None:
        if not self._skills:
            return

        if self.provider.strip().lower() != "chromadb":
            self._messages.append(f"skills semantic index supports only chromadb, got: {self.provider}")
            return

        try:
            import chromadb
            from sentence_transformers import SentenceTransformer
        except Exception as exc:
            self._messages.append(f"skills semantic index dependency missing: {exc}")
            return

        try:
            self.vector_db_dir.mkdir(parents=True, exist_ok=True)
            client = chromadb.PersistentClient(path=str(self.vector_db_dir))
            self._collection = client.get_or_create_collection(
                name=self.collection_name,
                metadata={"hnsw:space": "cosine"},
            )
            self._embedder = SentenceTransformer(self.embedding_model_name)

            ids: list[str] = []
            docs: list[str] = []
            metadatas: list[dict[str, Any]] = []
            for skill in self._skills.values():
                ids.append(skill.tool_name)
                docs.append(f"{skill.description}\n\n{skill.skill_prompt}")
                metadatas.append(
                    {
                        "tool_name": skill.tool_name,
                        "source_path": str(skill.source_path),
                    }
                )

            embeddings = self._embedder.encode(docs, normalize_embeddings=True)
            if hasattr(embeddings, "tolist"):
                embeddings = embeddings.tolist()

            self._collection.upsert(
                ids=ids,
                documents=docs,
                metadatas=metadatas,
                embeddings=embeddings,
            )
            self._semantic_ready = True
        except Exception as exc:
            self._messages.append(f"skills semantic index failed: {exc}")
            self._semantic_ready = False
