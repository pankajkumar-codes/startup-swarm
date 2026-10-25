"""Parse and safely write the multi-file output format engineers use.

Format (chosen over markdown fences because generated code often contains fences):

    <<<FILE src/pkg/module.py>>>
    ...file content...
    <<<END FILE>>>
"""

from __future__ import annotations

import os
import re
from typing import Dict, List

FILE_FORMAT_INSTRUCTIONS = """\
Output every file you create or change in this exact format, and nothing else:

<<<FILE relative/path/to/file.py>>>
<complete file content>
<<<END FILE>>>

Rules: complete files only (no diffs, no "rest unchanged"); paths relative to the
repository root; no absolute paths and no "..". You may add one line before the first
file starting with "NOTES:" for anything the reviewer must know."""

_BLOCK = re.compile(r"<<<FILE\s+([^\n>]+?)\s*>>>\n(.*?)\n?<<<END FILE>>>", re.DOTALL)
MAX_FILE_BYTES = 200_000
MAX_FILES = 40


class UnsafePathError(ValueError):
    pass


def safe_relpath(path: str) -> str:
    raw = path.strip().strip("`'\"")
    if not raw or raw.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", raw) or "\x00" in raw:
        raise UnsafePathError(f"absolute or empty path: {path!r}")
    norm = os.path.normpath(raw).replace("\\", "/")
    if norm == "." or norm.startswith("../") or norm == ".." or "/../" in f"/{norm}/":
        raise UnsafePathError(f"path escapes the repository: {path!r}")
    if norm.split("/")[0] in (".git",):
        raise UnsafePathError(f"refusing to write inside .git: {path!r}")
    return norm


def parse_file_blocks(text: str) -> Dict[str, str]:
    files: Dict[str, str] = {}
    for match in _BLOCK.finditer(text or ""):
        rel = safe_relpath(match.group(1))
        body = match.group(2)
        if len(body.encode("utf-8")) > MAX_FILE_BYTES:
            raise ValueError(f"{rel} exceeds {MAX_FILE_BYTES} bytes")
        files[rel] = body if body.endswith("\n") else body + "\n"
        if len(files) > MAX_FILES:
            raise ValueError(f"more than {MAX_FILES} files in one change")
    return files


def write_files(root: str, files: Dict[str, str]) -> List[str]:
    """Write files under ``root``. Re-validates every path; returns written paths."""
    root_abs = os.path.realpath(root)
    written: List[str] = []
    for rel, body in files.items():
        rel = safe_relpath(rel)
        dest = os.path.realpath(os.path.join(root_abs, rel))
        if not dest.startswith(root_abs + os.sep):
            raise UnsafePathError(f"{rel} resolves outside {root}")
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "w", encoding="utf-8") as fh:
            fh.write(body)
        written.append(rel)
    return written


def render_files_for_prompt(files: Dict[str, str]) -> str:
    return "\n".join(f"<<<FILE {p}>>>\n{b}<<<END FILE>>>" for p, b in files.items())
