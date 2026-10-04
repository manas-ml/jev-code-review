"""Read-only, sandboxed repo tools for agents. No writes, no shell, no network."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

MAX_OUT = 8000
MAX_FILE_BYTES = 500_000


class SandboxError(ValueError):
    pass


class RepoTools:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()

    def _resolve(self, rel: str) -> Path:
        p = (self.root / rel).resolve()
        if p != self.root and self.root not in p.parents:
            raise SandboxError(f"path escapes repo root: {rel}")
        return p

    def read_file(self, path: str, start: int = 1, end: int | None = None) -> str:
        p = self._resolve(path)
        if not p.is_file():
            raise SandboxError(f"not a file: {path}")
        if p.stat().st_size > MAX_FILE_BYTES:
            raise SandboxError("file too large")
        lines = p.read_text(errors="replace").splitlines()
        end = end or min(len(lines), start + 199)
        body = "\n".join(f"{i}: {ln}" for i, ln in enumerate(lines[start - 1 : end], start))
        return body[:MAX_OUT]

    def list_dir(self, path: str = ".") -> str:
        p = self._resolve(path)
        if not p.is_dir():
            raise SandboxError(f"not a directory: {path}")
        return "\n".join(sorted(f"{c.name}/" if c.is_dir() else c.name for c in p.iterdir())[:200])

    def grep(self, pattern: str, path: str = ".") -> str:
        base = self._resolve(path)
        rx = re.compile(pattern)
        hits: list[str] = []
        files = [base] if base.is_file() else sorted(base.rglob("*"))
        for f in files:
            if not f.is_file() or ".git" in f.parts or f.stat().st_size > MAX_FILE_BYTES:
                continue
            try:
                text = f.read_text()
            except (UnicodeDecodeError, OSError):
                continue
            for i, ln in enumerate(text.splitlines(), 1):
                if rx.search(ln):
                    hits.append(f"{f.relative_to(self.root)}:{i}: {ln.strip()}")
                    if len(hits) >= 100:
                        return "\n".join(hits)[:MAX_OUT]
        return "\n".join(hits)[:MAX_OUT]

    def call(self, name: str, args: dict[str, Any]) -> str:
        try:
            if name == "read_file":
                return self.read_file(str(args["path"]), int(args.get("start", 1)), args.get("end"))
            if name == "list_dir":
                return self.list_dir(str(args.get("path", ".")))
            if name == "grep":
                return self.grep(str(args["pattern"]), str(args.get("path", ".")))
        except (SandboxError, KeyError, re.error, ValueError) as e:
            return f"error: {e}"
        return f"error: unknown tool {name}"


def _fn(name: str, desc: str, props: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": desc,
            "parameters": {"type": "object", "properties": props, "required": required},
        },
    }


TOOL_SCHEMAS: list[dict[str, Any]] = [
    _fn(
        "read_file",
        "Read a file in the repo (numbered lines).",
        {"path": {"type": "string"}, "start": {"type": "integer"}, "end": {"type": "integer"}},
        ["path"],
    ),
    _fn(
        "grep",
        "Regex search over repo files.",
        {"pattern": {"type": "string"}, "path": {"type": "string"}},
        ["pattern"],
    ),
    _fn("list_dir", "List a directory.", {"path": {"type": "string"}}, []),
]


def parse_tool_args(raw: str | dict[str, Any]) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    try:
        out = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
    return out if isinstance(out, dict) else {}
