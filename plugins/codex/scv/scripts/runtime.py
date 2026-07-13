#!/usr/bin/env python3
"""Runtime requirements shared by the macOS-only Codex SCV commands."""

from __future__ import annotations

import re
import sys
from typing import Sequence


MINIMUM_PYTHON = (3, 9)
MINIMUM_CODEX = (0, 144, 1)
CODEX_VERSION_PATTERN = re.compile(r"\bcodex-cli\s+(\d+)\.(\d+)\.(\d+)\b")
REQUIRED_EXEC_FLAGS = (
    "--config",
    "--cd",
    "--color",
    "--ephemeral",
    "--ignore-user-config",
    "--json",
    "--output-schema",
    "--output-last-message",
    "--sandbox",
)
REQUIRED_SANDBOX_FLAGS = ("-P", "--sandbox-state-disable-network", "-C")


class RuntimeRequirementError(RuntimeError):
    """The host cannot safely run the supported SCV workflow."""


def require_macos(platform: str | None = None) -> None:
    detected = platform or sys.platform
    if detected != "darwin":
        raise RuntimeRequirementError(
            f"SCV는 macOS 전용입니다. 현재 플랫폼: {detected}"
        )
    if sys.version_info[:2] < MINIMUM_PYTHON:
        required = ".".join(str(item) for item in MINIMUM_PYTHON)
        current = ".".join(str(item) for item in sys.version_info[:3])
        raise RuntimeRequirementError(
            f"SCV에는 Python {required} 이상이 필요합니다. 현재 버전: {current}"
        )


def parse_codex_version(value: str) -> tuple[int, int, int]:
    match = CODEX_VERSION_PATTERN.search(value)
    if match is None:
        raise RuntimeRequirementError(
            "Codex CLI 버전을 확인할 수 없습니다. codex-cli 0.144.1 이상이 필요합니다"
        )
    return tuple(int(item) for item in match.groups())  # type: ignore[return-value]


def validate_codex_capabilities(
    version_output: str,
    exec_help: str,
    sandbox_help: str,
) -> None:
    version = parse_codex_version(version_output)
    if version < MINIMUM_CODEX:
        required = ".".join(str(item) for item in MINIMUM_CODEX)
        current = ".".join(str(item) for item in version)
        raise RuntimeRequirementError(
            f"Codex CLI {required} 이상이 필요합니다. 현재 버전: {current}"
        )
    _require_flags(exec_help, REQUIRED_EXEC_FLAGS, "codex exec")
    _require_flags(sandbox_help, REQUIRED_SANDBOX_FLAGS, "codex sandbox")


def _require_flags(output: str, flags: Sequence[str], command: str) -> None:
    missing = [flag for flag in flags if flag not in output]
    if missing:
        raise RuntimeRequirementError(
            f"{command}에 필요한 옵션이 없습니다: {', '.join(missing)}"
        )
