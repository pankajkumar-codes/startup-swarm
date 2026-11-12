"""Parse and safely write the multi-file output format engineers use.

Format (chosen over markdown fences because generated code often contains fences):

    <<<FILE src/pkg/module.py>>>
    ...file content...
    <<<END FILE>>>
"""

from __future__ import annotations

import os
import re
from typing import Dict, Iterable, List, Optional, Tuple

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


def write_files(root: str, files: Dict[str, str], overwrite: Optional[Iterable[str]] = None) -> List[str]:
    """Write files under ``root``. Re-validates every path; returns written paths.

    With ``overwrite=None`` (a fresh output folder) existing files are replaced. With a collection
    (writing into an existing repo) only the listed paths may replace existing files, nothing under
    ``.github/`` may be touched, and every path is checked before the first file is written.
    """
    root_abs = os.path.realpath(root)
    allowed = None if overwrite is None else {safe_relpath(p) for p in overwrite}
    targets: List[Tuple[str, str, str]] = []
    for rel, body in files.items():
        rel = safe_relpath(rel)
        dest = os.path.realpath(os.path.join(root_abs, rel))
        if not dest.startswith(root_abs + os.sep):
            raise UnsafePathError(f"{rel} resolves outside {root}")
        if allowed is not None:
            if rel.startswith(".github/"):
                raise UnsafePathError(f"refusing to modify CI configuration in an existing repo: {rel}")
            if os.path.exists(dest) and rel not in allowed:
                raise UnsafePathError(
                    f"{rel} already exists and was not passed with --context-file; refusing to overwrite it"
                )
        targets.append((rel, dest, body))
    for _, dest, body in targets:
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "w", encoding="utf-8") as fh:
            fh.write(body)
    return [rel for rel, _, _ in targets]


def render_files_for_prompt(files: Dict[str, str]) -> str:
    return "\n".join(f"<<<FILE {p}>>>\n{b}<<<END FILE>>>" for p, b in files.items())
