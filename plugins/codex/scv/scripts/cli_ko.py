"""작은 argparse 한글화 도우미."""

from __future__ import annotations

import argparse
from typing import Callable


_TRANSLATIONS = {
    "usage: ": "사용법: ",
    "positional arguments": "위치 인자",
    "optional arguments": "선택 인자",
    "options": "선택 인자",
    "show this help message and exit": "도움말을 표시하고 종료합니다",
    "the following arguments are required: %s": "다음 인자가 필요합니다: %s",
    "one of the arguments %s is required": "%s 중 하나가 필요합니다",
    "argument %(argument_name)s: %(message)s": "인자 %(argument_name)s: %(message)s",
    "invalid choice: %(value)r (choose from %(choices)s)": (
        "올바르지 않은 선택: %(value)r (가능한 값: %(choices)s)"
    ),
    "invalid %(type)s value: %(value)r": (
        "%(value)r은(는) 올바른 %(type)s 값이 아닙니다"
    ),
    "unrecognized arguments: %s": "알 수 없는 인자: %s",
    "expected one argument": "인자 하나가 필요합니다",
    "expected at least one argument": "인자가 하나 이상 필요합니다",
    "not allowed with argument %s": "%s 인자와 함께 사용할 수 없습니다",
    "%(prog)s: error: %(message)s\n": "%(prog)s: 오류: %(message)s\n",
}


def localize_argparse() -> None:
    """현재 프로세스에서 argparse의 기본 사용자 문구를 한글화합니다."""

    if getattr(argparse, "_scv_korean", False):
        return
    original: Callable[[str], str] = argparse._

    def translate(message: str) -> str:
        return _TRANSLATIONS.get(message, original(message))

    def argument_error_text(error: argparse.ArgumentError) -> str:
        if error.argument_name is None:
            return str(error.message)
        return f"인자 {error.argument_name}: {error.message}"

    argparse._ = translate
    argparse.ArgumentError.__str__ = argument_error_text
    argparse._scv_korean = True
