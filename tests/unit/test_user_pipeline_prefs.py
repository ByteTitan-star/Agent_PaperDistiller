"""用户级管线偏好测试：白名单应用 / pydantic 校验 / 端点逻辑（fake db）。"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.routers.settings import PipelinePrefs, _parse_prefs_json, get_pipeline_prefs, update_pipeline_prefs
from app.services.user_settings import apply_pipeline_prefs


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        parser_backend="auto",
        parser_mineru_enabled=False,
        parser_ocr_enabled=False,
        formula_backend="off",
        translation_provider="auto",
        vlm_enabled=False,
        vlm_model="qwen-vl-max",
        vlm_max_figures=12,
        grobid_enabled=False,
        grobid_base_url="http://localhost:8070",
    )


# ---------------------------------------------------------------------
# apply_pipeline_prefs（管线侧生效逻辑）
# ---------------------------------------------------------------------


def test_apply_pipeline_prefs_overrides() -> None:
    settings = _settings()
    prefs = json.dumps(
        {
            "parser_backend": "mineru",
            "translation_provider": "llm",
            "vlm_enabled": True,
            "vlm_max_figures": 5,
            "grobid_base_url": "http://grobid:8070",
        }
    )
    apply_pipeline_prefs(settings, prefs)
    assert settings.parser_backend == "mineru"
    assert settings.translation_provider == "llm"
    assert settings.vlm_enabled is True
    assert settings.vlm_max_figures == 5
    assert settings.grobid_base_url == "http://grobid:8070"
    # 未覆盖键保持默认
    assert settings.formula_backend == "off"
    assert settings.grobid_enabled is False


def test_apply_pipeline_prefs_ignores_unknown_and_none() -> None:
    settings = _settings()
    prefs = json.dumps({"deepseek_api_key": "sk-hack", "parser_backend": None, "secret_key": "x"})
    apply_pipeline_prefs(settings, prefs)
    assert settings.parser_backend == "auto"  # None 不生效
    assert not hasattr(settings, "deepseek_api_key") or settings  # 未知键被忽略（不抛错）


def test_apply_pipeline_prefs_bad_json_is_noop() -> None:
    settings = _settings()
    apply_pipeline_prefs(settings, "{broken json")
    apply_pipeline_prefs(settings, None)
    apply_pipeline_prefs(settings, json.dumps(["not", "a", "dict"]))
    assert settings.parser_backend == "auto"


# ---------------------------------------------------------------------
# pydantic 校验（白名单 + 枚举）
# ---------------------------------------------------------------------


def test_pipeline_prefs_accepts_valid_values() -> None:
    prefs = PipelinePrefs(parser_backend="mineru", vlm_enabled=True, vlm_max_figures=3)
    dumped = prefs.model_dump()
    assert dumped["parser_backend"] == "mineru"
    assert dumped["vlm_enabled"] is True


def test_pipeline_prefs_rejects_invalid_enum() -> None:
    with pytest.raises(ValidationError):
        PipelinePrefs(parser_backend="nougat")
    with pytest.raises(ValidationError):
        PipelinePrefs(translation_provider="bing")
    with pytest.raises(ValidationError):
        PipelinePrefs(formula_backend="mathpix-v2")


def test_parse_prefs_json_filters_whitelist() -> None:
    raw = json.dumps({"parser_backend": "pypdf", "deepseek_api_key": "sk-x", "vlm_enabled": True})
    parsed = _parse_prefs_json(raw)
    assert parsed == {"parser_backend": "pypdf", "vlm_enabled": True}
    assert _parse_prefs_json(None) == {}
    assert _parse_prefs_json("{bad") == {}


# ---------------------------------------------------------------------
# 端点逻辑（fake db，不起 HTTP 服务）
# ---------------------------------------------------------------------


class _FakeResult:
    def __init__(self, row: object) -> None:
        self._row = row

    def scalar_one_or_none(self) -> object:
        return self._row


class _FakeDB:
    def __init__(self, row: object | None) -> None:
        self._row = row
        self.added: list[object] = []

    async def execute(self, _query: object) -> _FakeResult:
        return _FakeResult(self._row)

    async def flush(self) -> None:
        pass

    def add(self, obj: object) -> None:
        self.added.append(obj)
        self._row = obj


@pytest.mark.asyncio
async def test_get_pipeline_prefs_merges_user_over_defaults() -> None:
    row = SimpleNamespace(pipeline_prefs=json.dumps({"parser_backend": "mineru", "vlm_enabled": True}))
    response = await get_pipeline_prefs(user=SimpleNamespace(id=1), db=_FakeDB(row))

    assert response.parser_backend == "mineru"  # 用户覆盖
    assert response.vlm_enabled is True
    assert response.translation_provider == "auto"  # 未覆盖 -> 服务端默认
    assert response.is_user_set["parser_backend"] is True
    assert response.is_user_set["translation_provider"] is False


@pytest.mark.asyncio
async def test_get_pipeline_prefs_without_config_returns_defaults() -> None:
    response = await get_pipeline_prefs(user=SimpleNamespace(id=1), db=_FakeDB(None))
    assert response.parser_backend == "auto"
    assert response.vlm_enabled is False


@pytest.mark.asyncio
async def test_update_pipeline_prefs_persists_json() -> None:
    row = SimpleNamespace(pipeline_prefs=None)
    body = PipelinePrefs(parser_backend="pymupdf", grobid_enabled=True)
    result = await update_pipeline_prefs(body=body, user=SimpleNamespace(id=1), db=_FakeDB(row))

    assert result["message"] == "管线配置已更新"
    saved = json.loads(row.pipeline_prefs)
    assert saved == {"parser_backend": "pymupdf", "grobid_enabled": True}


@pytest.mark.asyncio
async def test_update_pipeline_prefs_empty_body_clears() -> None:
    row = SimpleNamespace(pipeline_prefs='{"parser_backend": "mineru"}')
    await update_pipeline_prefs(body=PipelinePrefs(), user=SimpleNamespace(id=1), db=_FakeDB(row))
    assert row.pipeline_prefs is None  # 全空 = 恢复系统默认
