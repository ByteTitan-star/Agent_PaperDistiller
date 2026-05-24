from typing import Any


FIGURE_MARKERS = ("figure", "fig.", "table", "图", "表")


def run(keyword: str, top_k: int = 5, _context: dict[str, Any] | None = None) -> dict[str, Any]:
    token = (keyword or "").strip().lower()
    if not token:
        return {"matches": [], "error": "keyword is empty"}

    context = _context or {}
    chunks = context.get("chunks", []) or []
    vector_search = context.get("vector_search")
    limit = max(1, min(int(top_k), 8))

    matches: list[str] = []
    for chunk in chunks:
        if not isinstance(chunk, str):
            continue
        lowered = chunk.lower()
        if token not in lowered:
            continue
        if any(marker in lowered for marker in FIGURE_MARKERS):
            matches.append(chunk[:350])
            if len(matches) >= limit:
                return {"keyword": keyword, "matches": matches[:limit]}

    if callable(vector_search):
        try:
            vector_hits = vector_search(keyword, max(3, limit))
            for hit in vector_hits:
                if not isinstance(hit, str):
                    continue
                lowered = hit.lower()
                if any(marker in lowered for marker in FIGURE_MARKERS):
                    matches.append(hit[:350])
                    if len(matches) >= limit:
                        break
        except Exception as exc:
            return {"keyword": keyword, "matches": matches[:limit], "error": str(exc)}

    return {"keyword": keyword, "matches": matches[:limit]}
