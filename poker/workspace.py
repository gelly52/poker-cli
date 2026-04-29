"""Workspace traversal helpers."""

from collections.abc import Iterator
from pathlib import Path

SKIP_DIRS = {
    ".git",
    ".idea",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "venv",
}

TEXT_SUFFIXES = {
    "",
    ".env",
    ".ini",
    ".json",
    ".md",
    ".py",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
}

MAX_FILE_SIZE = 10_000_000


def iter_text_files(root: Path) -> Iterator[Path]:
    """Yield likely text files under root while skipping noisy folders."""

    root = root.resolve()
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if any(part in SKIP_DIRS for part in relative.parts):
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        if path.stat().st_size > MAX_FILE_SIZE:
            continue
        yield path


def read_text(path: Path) -> str:
    """Read a file as UTF-8 text, replacing invalid bytes."""

    return path.read_text(encoding="utf-8", errors="replace")
