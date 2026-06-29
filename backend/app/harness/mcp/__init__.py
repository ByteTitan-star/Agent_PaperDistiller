"""MCP（Model Context Protocol）集成。

- server.py：把 HarnessToolRegistry 的技能暴露为标准 MCP server（对外）。
- client.py：把外部 MCP server 加载为 LangChain 工具，供 ReAct agent 调用（对内）。

默认关闭（settings.mcp_enabled / mcp_inbound_enabled），且依赖 mcp 包（懒导入，
未安装时相关函数会优雅降级，不影响应用启动）。
"""
