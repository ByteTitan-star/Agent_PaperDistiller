"""公式识别适配器测试（mock HTTP，不依赖真实服务）。"""

from __future__ import annotations

import io
import json
from types import SimpleNamespace
from unittest.mock import patch

from app.pipeline.formula_recognizer import (
    MathpixAdapter,
    NullRecognizer,
    _strip_math_delimiters,
    get_formula_recognizer,
)


def _mathpix_response(text: str) -> io.BytesIO:
    # io.BytesIO 支持上下文管理器协议，可替代 urlopen 的返回值
    return io.BytesIO(json.dumps({"text": text}).encode("utf-8"))


def test_mathpix_recognize_success() -> None:
    adapter = MathpixAdapter(app_id="id", app_key="key")
    with patch(
        "app.pipeline.formula_recognizer.urlopen",
        return_value=_mathpix_response("\\[E=mc^2\\]"),
    ):
        latex = adapter.recognize(b"fake-png")
    assert latex == "E=mc^2"


def test_mathpix_recognize_error_returns_none() -> None:
    adapter = MathpixAdapter(app_id="id", app_key="key")
    payload = io.BytesIO(json.dumps({"error": "bad image"}).encode("utf-8"))
    with patch("app.pipeline.formula_recognizer.urlopen", return_value=payload):
        assert adapter.recognize(b"bad") is None


def test_mathpix_network_error_returns_none() -> None:
    adapter = MathpixAdapter(app_id="id", app_key="key")
    with patch("app.pipeline.formula_recognizer.urlopen", side_effect=OSError("timeout")):
        assert adapter.recognize(b"x") is None


def test_mathpix_requires_credentials() -> None:
    try:
        MathpixAdapter(app_id="", app_key="")
    except ValueError:
        pass
    else:
        raise AssertionError("空凭据应抛 ValueError")


def test_get_recognizer_off_by_default() -> None:
    recognizer = get_formula_recognizer(SimpleNamespace(formula_backend="off"))
    assert isinstance(recognizer, NullRecognizer)
    assert recognizer.recognize(b"png") is None


def test_get_recognizer_mathpix_missing_config_downgrades() -> None:
    settings = SimpleNamespace(formula_backend="mathpix", mathpix_app_id="", mathpix_app_key="")
    assert isinstance(get_formula_recognizer(settings), NullRecognizer)


def test_strip_delimiters_variants() -> None:
    assert _strip_math_delimiters("\\[x^2\\]") == "x^2"
    assert _strip_math_delimiters("\\(x^2\\)") == "x^2"
    assert _strip_math_delimiters("$$x^2$$") == "x^2"
    assert _strip_math_delimiters("x^2") == "x^2"
