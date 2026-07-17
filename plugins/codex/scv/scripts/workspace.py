#!/usr/bin/env python3
"""Shared, controller-owned workspace identity for SCV verification gates."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import subprocess


def _git_bytes(repo: Path, *arguments: str) -> bytes:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), *arguments],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except OSError as exc:
        raise OSError(f"git 명령을 시작할 수 없습니다: {exc}") from exc
    if result.returncode != 0:
        detail = result.stderr.decode(errors="replace").strip()
        raise OSError(f"git {' '.join(arguments)} 실행 실패: {detail}")
    return result.stdout


def workspace_fingerprint(root: Path) -> str:
    """Hash HEAD plus tracked, staged, and non-ignored untracked content."""

    root = root.resolve()
    digest = hashlib.sha256()
    head = _git_bytes(root, "rev-parse", "HEAD")
    status = _git_bytes(
        root, "status", "--porcelain=v1", "-z", "--untracked-files=all"
    )
    diff = _git_bytes(root, "diff", "HEAD", "--no-ext-diff", "--binary", "--")
    untracked = _git_bytes(root, "ls-files", "--others", "--exclude-standard", "-z")
    for label, payload in (
        (b"head", head),
        (b"status", status),
        (b"diff", diff),
        (b"untracked", untracked),
    ):
        digest.update(label + b"\0" + payload + b"\0")
    for raw_path in sorted(item for item in untracked.split(b"\0") if item):
        relative = Path(os.fsdecode(raw_path))
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"안전하지 않은 untracked 경로를 감지했습니다: {relative}")
        path = root / relative
        digest.update(raw_path + b"\0")
        if path.is_symlink():
            digest.update(b"symlink\0" + os.fsencode(os.readlink(path)))
        elif path.is_file():
            digest.update(b"file\0")
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
        else:
            digest.update(b"other\0")
    return digest.hexdigest()
