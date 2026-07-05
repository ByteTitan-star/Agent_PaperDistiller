"""Small repository-local secret scanner for pre-commit.

Adapted from biomap-agents/scripts/secret_scan.py.
"""

from __future__ import annotations

import argparse
import math
import re
import shutil
import subprocess  # nosec B404
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

_ALLOW_MARKER = "pragma: allow-secret"

_SECRET_ASSIGNMENT_RE = re.compile(
    r"""(?ix)
    (?:^|[\s{[,`"'])
    (?P<name>
        \b(?:BIOMAP_|APD_|PAPER_)[A-Z0-9_]*(?:API_KEY|TOKEN|SECRET|PASSWORD)\b
        |
        \b[A-Z0-9_]*(?:API_KEY|TOKEN|SECRET|PASSWORD)\b
        |
        \b(?:api[_-]?key|token|secret|password|passwd)\b
    )
    \s*(?:=|:)\s*
    (?P<value>[^#\s,}\]]+)
    """
)

_SECRET_CONTEXT_RE = re.compile(r"(?i)\b(api[_-]?key|authorization|bearer|credential|password|passwd|secret|token)\b")
_TOKEN_RE = re.compile(r"\b[A-Za-z0-9][A-Za-z0-9_+/=-]{31,}\b")

_PLACEHOLDER_VALUES = frozenset(
    {
        "",
        "''",
        '""',
        "...",
        "changeme",
        "change-me",
        "change-me-in-production-use-a-long-random-string",
        "aes-32-byte-secret-key-change-me!!",
        "dummy",
        "example",
        "fake",
        "fake-token-for-tests",
        "placeholder",
        "redacted",
        "secret",
        "service-secret",
        "sk-test",
        "test",
        "test-secret",
        "your-api-key",
    }
)

_SKIP_FILE_PATTERNS = (
    re.compile(r"(^|/)uv\.lock$"),
    re.compile(r"(^|/)package-lock\.json$"),
    re.compile(r"(^|/)yarn\.lock$"),
    re.compile(r"(^|/)pnpm-lock\.yaml$"),
    re.compile(r"(^|/)UI_figures/"),
)


@dataclass(frozen=True, slots=True)
class Finding:
    path: Path
    line_number: int
    rule: str
    message: str


def scan_text(path: Path, text: str) -> list[Finding]:
    findings: list[Finding] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if _ALLOW_MARKER in line:
            continue
        findings.extend(_scan_named_secrets(path, line_number, line))
        findings.extend(_scan_high_entropy_tokens(path, line_number, line))
    return findings


def scan_paths(paths: Iterable[Path]) -> list[Finding]:
    findings: list[Finding] = []
    for path in sorted({p for p in paths if p.exists() and p.is_file()}):
        if _should_skip_path(path):
            continue
        text = _read_text(path)
        if text is None:
            continue
        findings.extend(scan_text(path, text))
    return findings


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", type=Path)
    parser.add_argument("--all-files", action="store_true")
    parser.add_argument("--staged", action="store_true")
    args = parser.parse_args(argv)

    if args.staged:
        findings = scan_staged()
    else:
        paths = _resolve_paths(args.paths, all_files=args.all_files)
        findings = scan_paths(paths)
    if not findings:
        return 0

    for finding in findings:
        print(
            f"{finding.path}:{finding.line_number}: {finding.rule}: {finding.message}",
            file=sys.stderr,
        )
    print(
        f"secret-scan found {len(findings)} suspected secret(s). "
        f"Use '# {_ALLOW_MARKER}' only for deliberate fake test values.",
        file=sys.stderr,
    )
    return 1


def scan_staged() -> list[Finding]:
    diff = _git_stdout(["diff", "--cached", "--unified=0", "--no-ext-diff", "--diff-filter=ACMR"]).decode(
        "utf-8", errors="replace"
    )
    return _scan_unified_diff(diff)


def _scan_named_secrets(path: Path, line_number: int, line: str) -> list[Finding]:
    findings: list[Finding] = []
    for match in _SECRET_ASSIGNMENT_RE.finditer(line):
        name = match.group("name")
        raw_value = match.group("value")
        value = _clean_value(raw_value)
        if _is_placeholder(value) or _looks_like_code_declaration_value(value):
            continue
        if _is_safe_assignment_context(path, line, raw_value):
            continue
        if path.suffix == ".py" and _is_python_variable_reference(raw_value):
            continue
        if name.lower() == "token" and _looks_like_secret_token(value):
            continue
        findings.append(
            Finding(
                path=path,
                line_number=line_number,
                rule="named-secret",
                message=f"{name} appears to be populated",
            )
        )
    return findings


def _scan_high_entropy_tokens(path: Path, line_number: int, line: str) -> list[Finding]:
    if not _SECRET_CONTEXT_RE.search(line):
        return []
    findings: list[Finding] = []
    for match in _TOKEN_RE.finditer(line):
        token = _clean_value(match.group(0))
        if _is_placeholder(token) or not _looks_like_secret_token(token):
            continue
        findings.append(
            Finding(
                path=path,
                line_number=line_number,
                rule="high-entropy-token",
                message="long high-entropy token detected",
            )
        )
    return findings


def _resolve_paths(paths: Sequence[Path], *, all_files: bool) -> list[Path]:
    if all_files:
        return _git_files(["ls-files", "-z"])
    return list(paths)


def _git_files(args: list[str]) -> list[Path]:
    stdout = _git_stdout(args)
    return [Path(raw.decode()) for raw in stdout.split(b"\0") if raw]


def _git_stdout(args: list[str]) -> bytes:
    git = shutil.which("git")
    if git is None:
        raise RuntimeError("git executable not found")
    result = subprocess.run([git, *args], check=True, capture_output=True)  # nosec B603
    return result.stdout


def _scan_unified_diff(diff: str) -> list[Finding]:
    findings: list[Finding] = []
    current_path: Path | None = None
    new_line_number: int | None = None
    for raw_line in diff.splitlines():
        if raw_line.startswith("+++ "):
            current_path = _parse_new_diff_path(raw_line)
            new_line_number = None
            continue
        hunk_match = re.match(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@", raw_line)
        if hunk_match:
            new_line_number = int(hunk_match.group(1))
            continue
        if current_path is None or new_line_number is None:
            continue
        if raw_line.startswith("+") and not raw_line.startswith("+++"):
            if not _should_skip_path(current_path):
                findings.extend(_scan_diff_line(current_path, new_line_number, raw_line[1:]))
            new_line_number += 1
        elif raw_line.startswith(" "):
            new_line_number += 1
    return findings


def _scan_diff_line(path: Path, line_number: int, line: str) -> list[Finding]:
    if _ALLOW_MARKER in line:
        return []
    return [
        *_scan_named_secrets(path, line_number, line),
        *_scan_high_entropy_tokens(path, line_number, line),
    ]


def _parse_new_diff_path(line: str) -> Path | None:
    raw_path = line[4:]
    if raw_path == "/dev/null":
        return None
    if raw_path.startswith("b/"):
        raw_path = raw_path[2:]
    return Path(raw_path)


def _read_text(path: Path) -> str | None:
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    if b"\0" in raw[:4096]:
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _should_skip_path(path: Path) -> bool:
    posix = path.as_posix()
    return any(pattern.search(posix) for pattern in _SKIP_FILE_PATTERNS)


def _clean_value(value: str) -> str:
    return value.strip().strip("\"'`").strip()


def _is_placeholder(value: str) -> bool:
    normalised = value.strip().strip("\"'`").strip().lower()
    return (
        normalised in _PLACEHOLDER_VALUES
        or normalised.startswith("<")
        or normalised.startswith("${")
        or normalised.startswith("$")
    )


def _looks_like_code_declaration_value(value: str) -> bool:
    normalised = value.strip().strip("\"'`").strip().lower()
    if not normalised:
        return True
    if normalised in {"none", "true", "false"}:
        return True
    if normalised.startswith(("_", "field(", "secretstr(", "self.", "settings.", "os.environ", "os.getenv", "getenv(")):
        return True
    if re.match(r"^[a-z_][a-z0-9_]*\(", normalised):
        return True
    if re.search(r"[a-z0-9_]+\.[a-z0-9_]+\(", normalised):
        return True
    return (
        re.match(
            r"^(str|int|float|bool|bytes|path|secretstr|optional|list|dict|set|tuple|mapped)"
            r"(\b|\[|\s*\||\s*=)",
            normalised,
        )
        is not None
    )


def _is_safe_assignment_context(path: Path, line: str, raw_value: str) -> bool:
    if path.suffix == ".py" and ("mapped_column" in line or "relationship(" in line):
        return True
    value = raw_value.strip()
    if path.suffix == ".py" and value.startswith("("):
        return True
    if "=>" in value or re.search(r"\b(?:const|let|var)\s+\w+\s*=\s*\(", line):
        return True
    if re.search(r"\b(?:localStorage|sessionStorage|process\.env|getenv|os\.environ)\b", value):
        return True
    if re.search(r"\.(?:getItem|get|post|put|delete|sub|match|strip|upper|lower|split|replace)\(", value):
        return True
    if path.suffix == ".py" and re.search(r"\)[.(]", value):
        return True
    return path.suffix in {".js", ".ts", ".vue", ".jsx", ".tsx"} and re.search(r"[.(]", value) is not None


_PY_VARIABLE_REFERENCE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")


def _is_python_variable_reference(raw_value: str) -> bool:
    return _PY_VARIABLE_REFERENCE_RE.match(raw_value.strip()) is not None


def _looks_like_secret_token(token: str) -> bool:
    if len(token) < 32:
        return False
    if not any(ch.isalpha() for ch in token) or not any(ch.isdigit() for ch in token):
        return False
    return _shannon_entropy(token) >= 4.0


def _shannon_entropy(value: str) -> float:
    if not value:
        return 0.0
    counts = {char: value.count(char) for char in set(value)}
    length = len(value)
    return -sum((count / length) * math.log2(count / length) for count in counts.values())


if __name__ == "__main__":
    raise SystemExit(main())
