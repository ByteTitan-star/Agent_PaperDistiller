"""Tavily web search skill for the PaperDistiller agent."""

from __future__ import annotations

import os
from typing import Any


def run(query: str, max_results: int = 3, search_depth: str = "basic") -> dict[str, Any]:
    """Search the web using Tavily API.

    Args:
        query: Search query string.
        max_results: Number of results to return (1-5).
        search_depth: "basic" for quick results, "advanced" for deeper analysis.

    Returns:
        Dict with "results" list or "error" string.
    """
    api_key = os.getenv("TAVILY_API_KEY", "").strip()
    if not api_key:
        return {"error": "TAVILY_API_KEY not configured. Set it in .env or environment."}

    try:
        from tavily import TavilyClient

        client = TavilyClient(api_key=api_key)
        response = client.search(
            query=query,
            max_results=max(1, min(max_results, 5)),
            search_depth=search_depth,
        )

        results = []
        for item in response.get("results", []):
            results.append({
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "content": item.get("content", "")[:500],
                "score": item.get("score", 0),
            })

        return {"results": results, "query": query, "result_count": len(results)}
    except ImportError:
        return {"error": "tavily-python not installed. Run: pip install tavily-python"}
    except Exception as exc:
        return {"error": f"Web search failed: {exc}"}
