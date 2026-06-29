"""MCP server — 把 HarnessToolRegistry 的技能暴露为标准 MCP 工具服务（对外）。

通过 FastMCP 把每个非 local_only 技能注册为一个 MCP 工具，外部 MCP 客户端
（Claude Desktop、其它 Agent 等）即可标准化调用本平台的 web_search / arxiv_search 等技能。

设计要点：
- 懒导入 mcp：未安装时 build_mcp_http_app() 返回 None，main.py 跳过挂载，不影响启动。
- 跳过 local_only 技能（依赖进程内 _context 注入，无法跨进程暴露，如 figure/code）。
- 动态构造 runner 函数的签名（来自技能 JSON schema 的 properties），
  让 FastMCP 生成准确的 inputSchema，而非空 schema。
"""
from __future__ import annotations

import inspect
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


def _is_local_only(skill: Any) -> bool:
    """技能是否仅本地可用（不应跨进程暴露给外部 MCP 客户端）。

    判定（任一命中即视为 local_only）：
    1. openai.yaml 里 function.local_only == true（显式标记，如安全敏感工具）；
    2. callable 签名含 _context 参数（依赖进程内上下文注入，外部客户端无法提供）。
    """
    # 1. 显式标记
    schema = getattr(skill, "tool_schema", None) or {}
    if isinstance(schema, dict):
        fn = schema.get("function", schema)
        if isinstance(fn, dict) and fn.get("local_only"):
            return True
    if bool(getattr(skill, "local_only", False)):
        return True
    # 2. 依赖 _context 注入
    fn = getattr(skill, "callable_fn", None)
    if fn is not None:
        try:
            if "_context" in inspect.signature(fn).parameters:
                return True
        except (ValueError, TypeError):
            pass
    return False


def _skill_meta(skill: Any) -> dict | None:
    """从一个 LoadedSkill 提取 MCP 工具元信息：name / description / inputSchema。"""
    schema = getattr(skill, "tool_schema", None) or {}
    fn = schema.get("function", schema) if isinstance(schema, dict) else {}
    name = fn.get("name") or getattr(skill, "tool_name", None)
    if not name:
        return None
    description = fn.get("description") or getattr(skill, "description", "") or name
    params = fn.get("parameters") or {"type": "object", "properties": {}}
    return {"name": name, "description": description, "inputSchema": params}


_JSON_TYPE_MAP = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
    "array": list,
    "object": dict,
}


def _json_type_to_python(schema: Any) -> type:
    """JSON Schema 类型 → Python 类型（用于构造 runner 签名）。嵌套/未知 → Any。"""
    if isinstance(schema, dict):
        return _JSON_TYPE_MAP.get(str(schema.get("type", "")).lower(), Any)
    return Any


def _make_runner(tool_executor: Any, name: str, input_schema: dict) -> Any:
    """为单个技能构造一个带正确签名的 async runner，供 FastMCP 注册。

    FastMCP 根据函数签名生成 inputSchema，因此把技能 schema 的 properties
    映射为函数的 keyword-only 参数 + 类型注解。
    """
    properties = (input_schema or {}).get("properties", {}) if isinstance(input_schema, dict) else {}
    required = set((input_schema or {}).get("required", []) if isinstance(input_schema, dict) else [])

    async def runner(**kwargs: Any) -> str:
        result = tool_executor.execute(name, kwargs or {})
        return json.dumps(result, ensure_ascii=False)

    # 构造签名：每个 property 一个 keyword-only 参数
    params: list[inspect.Parameter] = []
    for pname, pschema in properties.items():
        default = inspect.Parameter.empty if pname in required else None
        params.append(
            inspect.Parameter(pname, inspect.Parameter.KEYWORD_ONLY, default=default,
                              annotation=_json_type_to_python(pschema))
        )
    # 没有显式参数时，保留 **kwargs 以兼容任意输入
    if not params:
        params.append(inspect.Parameter("arguments", inspect.Parameter.VAR_KEYWORD))
    runner.__signature__ = inspect.Signature(params)  # type: ignore[attr-defined]
    runner.__name__ = name
    runner.__doc__ = f"MCP tool '{name}' exposed from HarnessToolRegistry."
    return runner


def build_mcp_http_app(tool_executor: Any):
    """构建并返回 FastMCP 的 streamable-http ASGI app；mcp 未安装或无技能时返回 None。

    Args:
        tool_executor: HarnessToolRegistry（或回退的 SkillRegistry），提供 all_tools()/execute()。

    Returns:
        Starlette/ASGI app，或 None（应跳过挂载）。
    """
    try:
        from mcp.server.fastmcp import FastMCP
    except Exception as exc:  # mcp 未安装
        logger.warning("MCP server disabled: mcp package not installed (%s)", exc)
        return None

    mcp = FastMCP("paper-distiller")
    registered: list[str] = []

    for skill in tool_executor.all_tools():
        if _is_local_only(skill):
            continue
        meta = _skill_meta(skill)
        if not meta:
            continue
        runner = _make_runner(tool_executor, meta["name"], meta["inputSchema"])
        try:
            mcp.add_tool(runner, name=meta["name"], description=meta["description"])
            registered.append(meta["name"])
        except Exception as exc:
            logger.warning("MCP: failed to register tool %s: %s", meta["name"], exc)

    if not registered:
        logger.info("MCP server: no tools registered, skipping mount")
        return None

    logger.info("MCP server exposing %d tools: %s", len(registered), registered)
    try:
        return mcp.streamable_http_app()
    except AttributeError:
        # 老版本 mcp SDK 没有 streamable_http_app，尝试 sse_app
        try:
            return mcp.sse_app()
        except AttributeError as exc:
            logger.warning("MCP server: no supported ASGI app factory in installed mcp version: %s", exc)
            return None
