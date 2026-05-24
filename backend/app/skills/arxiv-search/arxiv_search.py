from typing import Any
import xml.etree.ElementTree as ET
from urllib.parse import quote_plus
from urllib.request import Request, urlopen


def run(query: str, max_results: int = 3, _context: dict[str, Any] | None = None) -> dict[str, Any]:
    safe_query = (query or "").strip()
    if not safe_query:
        return {"items": [], "error": "query is empty"}

    size = max(1, min(int(max_results), 5))
    url = (
        "https://export.arxiv.org/api/query?"
        f"search_query=all:{quote_plus(safe_query)}&start=0&max_results={size}"
    )
    req = Request(url, headers={"User-Agent": "paper-agent/1.0"})

    try:
        with urlopen(req, timeout=12) as resp:
            payload = resp.read().decode("utf-8", errors="ignore")
        root = ET.fromstring(payload)
        ns = {"atom": "http://www.w3.org/2005/Atom"}

        items: list[dict[str, str]] = []
        for entry in root.findall("atom:entry", ns):
            title = (entry.findtext("atom:title", default="", namespaces=ns) or "").strip()
            summary = (entry.findtext("atom:summary", default="", namespaces=ns) or "").strip()
            link = (entry.findtext("atom:id", default="", namespaces=ns) or "").strip()
            published = (entry.findtext("atom:published", default="", namespaces=ns) or "").strip()
            if not title:
                continue
            items.append(
                {
                    "title": title,
                    "published": published,
                    "url": link,
                    "summary": summary[:320],
                }
            )
        return {"query": safe_query, "items": items}
    except Exception as exc:
        return {"query": safe_query, "items": [], "error": str(exc)}
