#!/usr/bin/env python3
"""Repository-local failure learning for the macOS Codex SCV controller.

Only the deterministic controller may write this store. Nested Codex workers
receive bounded, redacted copies of selected records and never get filesystem
access to the learning directory itself.
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import stat
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

try:
    import fcntl
except ImportError:  # pragma: no cover - macOS 전용 오류를 먼저 표시합니다.
    fcntl = None  # type: ignore[assignment]

try:
    from .runtime import RuntimeRequirementError, require_macos
except ImportError:  # pragma: no cover - direct script execution.
    from runtime import RuntimeRequirementError, require_macos


SCHEMA_VERSION = 1
LOCK_NAME = ".learning.lock"
ID_PATTERN = re.compile(r"^[0-9a-f]{64}$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
LESSON_STATES = frozenset({"candidate", "active", "suspect", "retired"})
PROPOSAL_STATES = frozenset({"proposed", "handed-off", "closed"})
MAX_TEXT = 4_000
MAX_LIST_ITEMS = 8
MAX_ACTIVE_LESSONS = 3
MAX_DOCUMENT_BYTES = 1_000_000

_SECRET_PATTERNS = (
    re.compile(
        r"-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----.*?"
        r"-----END (?:[A-Z0-9 ]+ )?PRIVATE KEY-----",
        re.DOTALL,
    ),
    re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"(?i)\b(authorization\s*:\s*)(?:basic|bearer)\s+[^\s,;]+"),
    re.compile(
        r"(?i)([\"']?[A-Za-z0-9_.-]*(?:api[_-]?key|token|secret|password|"
        r"session(?:id)?|scvie|credential|private[_-]?key)"
        r"[\"']?\s*:\s*)[\"'][^\"']+[\"']"
    ),
    re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"),
    re.compile(r"\bxox(?:a|b|p|r|s)-[A-Za-z0-9-]{10,}\b"),
    re.compile(r"\b(?:sk|rk|pk)-[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^\s/@:]+:[^\s/@]+@"),
    re.compile(
        r"(?i)\b([A-Za-z0-9_.-]*(?:api[_-]?key|token|secret|password|"
        r"session(?:id)?|scvie|credential|private[_-]?key))"
        r"\s*([:=])\s*([^\s,;]+)"
    ),
)
_TIMESTAMP_PATTERN = re.compile(
    r"\b\d{4}-\d{2}-\d{2}[T ][0-9:.+-]+(?:Z|[+-]\d{2}:?\d{2})?\b"
)
_UUID_PATTERN = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
_TEMP_PATH_PATTERN = re.compile(
    r"(?:/private)?/(?:tmp|var/folders)/[^\s:'\"()]+"
)
_PID_PATTERN = re.compile(r"(?i)\b(?:pid|process)\s*[=: ]\s*\d+\b")
_LINE_NUMBER_PATTERN = re.compile(r"(?<=\S):(\d+)(?::\d+)?\b")


class LearningError(RuntimeError):
    """Learning data is missing, unsafe, or incompatible."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _digest(namespace: bytes, value: Any) -> str:
    digest = hashlib.sha256(namespace + b"\0")
    digest.update(_canonical_json(value))
    return digest.hexdigest()


def _seal_document(namespace: bytes, value: Mapping[str, Any]) -> dict[str, Any]:
    document = dict(value)
    document.pop("content_sha256", None)
    document["content_sha256"] = _digest(namespace, document)
    return document


def _verify_seal(namespace: bytes, value: Mapping[str, Any], label: str) -> None:
    expected = value.get("content_sha256")
    body = dict(value)
    body.pop("content_sha256", None)
    if not isinstance(expected, str) or _digest(namespace, body) != expected:
        raise LearningError(f"{label} 내용의 무결성 검증에 실패했습니다")


def redact_text(value: object, limit: int = MAX_TEXT) -> str:
    """Remove common credentials and control characters from untrusted text."""

    text = str(value)
    for pattern in _SECRET_PATTERNS:
        if pattern.groups >= 3:
            text = pattern.sub(lambda match: f"{match.group(1)}{match.group(2)}<redacted>", text)
        elif pattern.groups == 1:
            text = pattern.sub(lambda match: f"{match.group(1)} <redacted>", text)
        else:
            text = pattern.sub("<redacted>", text)
    text = "".join(
        "\n" if character in "\r\n" else character
        for character in text
        if character in "\r\n\t" or ord(character) >= 32
    )
    if len(text) > limit:
        text = text[:limit] + f"\n... {len(text) - limit}자 생략"
    return text


def normalize_failure_text(value: object) -> str:
    """Normalize volatile values without erasing the material error message."""

    text = redact_text(value, limit=8_000)
    text = _TIMESTAMP_PATTERN.sub("<timestamp>", text)
    text = _UUID_PATTERN.sub("<uuid>", text)
    text = _TEMP_PATH_PATTERN.sub("<temp-path>", text)
    text = _PID_PATTERN.sub("pid=<pid>", text)
    text = _LINE_NUMBER_PATTERN.sub(":<line>", text)
    return " ".join(text.lower().split())[:4_000]


def _safe_string(value: Any, label: str, *, maximum: int = MAX_TEXT) -> str:
    if not isinstance(value, str) or not value.strip():
        raise LearningError(f"{label} 값은 비어 있을 수 없습니다")
    normalized = redact_text(value.strip(), maximum)
    if not normalized:
        raise LearningError(f"{label} 값은 정제 후 비어 있을 수 없습니다")
    return normalized


def _safe_string_list(value: Any, label: str) -> list[str]:
    if not isinstance(value, list) or len(value) > MAX_LIST_ITEMS:
        raise LearningError(f"{label}은 최대 {MAX_LIST_ITEMS}개의 문자열 배열이어야 합니다")
    return [_safe_string(item, f"{label}[{position}]", maximum=1_000) for position, item in enumerate(value)]


def sanitize_analysis(value: Mapping[str, Any]) -> dict[str, Any]:
    required = {
        "classification",
        "diagnosis",
        "failed_approaches",
        "next_actions",
        "verification_checks",
        "candidate_lesson",
    }
    if set(value) != required:
        raise LearningError("실패 분석 출력 필드가 계약과 일치하지 않습니다")
    classification = value.get("classification")
    if classification not in {
        "implementation",
        "test",
        "plan",
        "environment",
        "controller",
        "unknown",
    }:
        raise LearningError("실패 분석 classification 값이 올바르지 않습니다")
    return {
        "classification": classification,
        "diagnosis": _safe_string(value.get("diagnosis"), "diagnosis"),
        "failed_approaches": _safe_string_list(
            value.get("failed_approaches"), "failed_approaches"
        ),
        "next_actions": _safe_string_list(value.get("next_actions"), "next_actions"),
        "verification_checks": _safe_string_list(
            value.get("verification_checks"), "verification_checks"
        ),
        "candidate_lesson": _safe_string(
            value.get("candidate_lesson"), "candidate_lesson"
        ),
    }


def failure_signature(
    *,
    stage: str,
    command: str | None,
    exit_code: int | None,
    message: str,
    scope: str | None = None,
) -> tuple[str, str, str | None]:
    """Return a reusable signature, normalized error, and command hash."""

    normalized_error = normalize_failure_text(message)
    normalized_command = " ".join(command.split()) if command else None
    command_hash = (
        hashlib.sha256(normalized_command.encode("utf-8")).hexdigest()
        if normalized_command
        else None
    )
    scope_hash = (
        hashlib.sha256(normalize_failure_text(scope).encode("utf-8")).hexdigest()
        if scope
        else None
    )
    signature = _digest(
        b"scv-failure-signature-v1",
        {
            "stage": stage,
            "command_sha256": command_hash,
            "exit_code": exit_code,
            "normalized_error": normalized_error,
            "scope_sha256": scope_hash,
        },
    )
    return signature, normalized_error, command_hash


def build_failure_record(
    *,
    task_id: str,
    plan_sha256: str,
    step_id: str,
    attempt_number: int,
    stage: str,
    message: str,
    evidence_sha256: str,
    acceptance_records: Sequence[Mapping[str, Any]] = (),
    context_sha256: str | None = None,
    scope: str | None = None,
) -> dict[str, Any]:
    failed_command: str | None = None
    exit_code: int | None = None
    detailed_message = message
    for record in acceptance_records:
        if record.get("status") == "passed":
            continue
        raw_command = record.get("command")
        if isinstance(raw_command, str):
            failed_command = raw_command
        raw_code = record.get("returncode")
        if type(raw_code) is int:
            exit_code = raw_code
        detail = record.get("stderr") or record.get("stdout")
        if isinstance(detail, str) and detail.strip():
            detailed_message = f"{message}\n{detail}"
        break

    signature, normalized_error, command_hash = failure_signature(
        stage=stage,
        command=failed_command,
        exit_code=exit_code,
        message=detailed_message,
        scope=scope,
    )
    scope_sha256 = (
        hashlib.sha256(normalize_failure_text(scope).encode("utf-8")).hexdigest()
        if scope
        else None
    )
    if not SHA256_PATTERN.fullmatch(evidence_sha256):
        raise LearningError("실패 증거 SHA-256이 올바르지 않습니다")
    if context_sha256 is not None and not SHA256_PATTERN.fullmatch(context_sha256):
        raise LearningError("실패 문맥 SHA-256이 올바르지 않습니다")
    return {
        "task_id": _safe_string(task_id, "task_id", maximum=64),
        "plan_sha256": _safe_string(plan_sha256, "plan_sha256", maximum=64),
        "step_id": _safe_string(step_id, "step_id", maximum=64),
        "attempt_number": attempt_number,
        "stage": _safe_string(stage, "stage", maximum=64),
        "signature": signature,
        "command_sha256": command_hash,
        "scope_sha256": scope_sha256,
        "exit_code": exit_code,
        "normalized_error": normalized_error,
        "message": redact_text(message),
        "evidence_sha256": evidence_sha256,
        "context_sha256": context_sha256,
        "recorded_at": utc_now(),
    }


def _atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8"))
            handle.write(b"\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


class LearningStore:
    """Short-lock, atomic store shared by linked worktrees of one repository."""

    def __init__(self, root: Path) -> None:
        try:
            require_macos()
        except RuntimeRequirementError as exc:
            raise LearningError(str(exc)) from exc
        self.root = root.expanduser().absolute()
        if self.root.exists() and self.root.is_symlink():
            raise LearningError("학습 저장소 루트에 심볼릭 링크를 사용할 수 없습니다")
        self.observations = self.root / "observations"
        self.lessons = self.root / "lessons"
        self.proposals = self.root / "proposals"

    def _prepare_directory(self, path: Path) -> None:
        current = self.root
        if self.root.exists() and self.root.is_symlink():
            raise LearningError("학습 저장소 루트에 심볼릭 링크를 사용할 수 없습니다")
        self.root.mkdir(parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)
        relative = path.relative_to(self.root)
        for part in relative.parts:
            current = current / part
            if current.exists() and current.is_symlink():
                raise LearningError("학습 저장소 경로에 심볼릭 링크를 사용할 수 없습니다")
            current.mkdir(exist_ok=True)
            os.chmod(current, 0o700)

    @contextmanager
    def _lock(self) -> Iterator[None]:
        if fcntl is None:  # pragma: no cover - 생성자에서 macOS를 먼저 검사합니다.
            raise LearningError("SCV 학습 잠금은 macOS에서만 사용할 수 있습니다")
        self._prepare_directory(self.root)
        flags = os.O_CREAT | os.O_RDWR
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor: int | None = None
        try:
            descriptor = os.open(self.root / LOCK_NAME, flags, 0o600)
            os.fchmod(descriptor, 0o600)
            fcntl.flock(descriptor, fcntl.LOCK_EX)
        except OSError as exc:
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            if exc.errno in {errno.ELOOP, errno.EMLINK}:
                raise LearningError("학습 잠금 파일에 심볼릭 링크를 사용할 수 없습니다") from exc
            raise LearningError(f"학습 저장소 잠금을 획득할 수 없습니다: {exc}") from exc
        assert descriptor is not None
        try:
            yield
        finally:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)

    @staticmethod
    def _validate_id(value: str, label: str) -> str:
        if not isinstance(value, str) or not ID_PATTERN.fullmatch(value):
            raise LearningError(f"{label} ID가 올바르지 않습니다")
        return value

    def _load_json(self, path: Path) -> dict[str, Any]:
        try:
            relative = path.relative_to(self.root)
        except ValueError as exc:
            raise LearningError("학습 데이터 경로가 저장소를 벗어났습니다") from exc
        current = self.root
        if current.is_symlink():
            raise LearningError("학습 저장소 루트에 심볼릭 링크를 사용할 수 없습니다")
        for part in relative.parts[:-1]:
            current = current / part
            if current.is_symlink():
                raise LearningError(
                    f"학습 데이터 상위 경로에 심볼릭 링크를 사용할 수 없습니다: {current}"
                )
        descriptor: int | None = None
        try:
            flags = os.O_RDONLY
            if hasattr(os, "O_CLOEXEC"):
                flags |= os.O_CLOEXEC
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            descriptor = os.open(path, flags)
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                raise LearningError(f"학습 데이터가 일반 파일이 아닙니다: {path}")
            if metadata.st_size > MAX_DOCUMENT_BYTES:
                raise LearningError(f"학습 데이터가 허용 크기를 초과했습니다: {path}")
            chunks: list[bytes] = []
            remaining = MAX_DOCUMENT_BYTES + 1
            while remaining > 0:
                chunk = os.read(descriptor, min(64 * 1024, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            payload = b"".join(chunks)
            if len(payload) > MAX_DOCUMENT_BYTES:
                raise LearningError(f"학습 데이터가 허용 크기를 초과했습니다: {path}")
            value = json.loads(payload.decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise LearningError(f"학습 데이터를 읽을 수 없습니다: {path}: {exc}") from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)
        if not isinstance(value, dict):
            raise LearningError(f"학습 데이터가 JSON 객체가 아닙니다: {path}")
        return value

    def record_observation(
        self,
        failure: Mapping[str, Any],
        analysis: Mapping[str, Any],
        *,
        analyst_evidence_sha256: str,
    ) -> dict[str, Any]:
        if not SHA256_PATTERN.fullmatch(analyst_evidence_sha256):
            raise LearningError("분석 증거 SHA-256이 올바르지 않습니다")
        sanitized = sanitize_analysis(analysis)
        body = {
            "schema_version": SCHEMA_VERSION,
            "failure": dict(failure),
            "analysis": sanitized,
            "analyst_evidence_sha256": analyst_evidence_sha256,
            "created_at": utc_now(),
        }
        observation_id = _digest(b"scv-observation-v1", body)
        document = {"observation_id": observation_id, **body}
        with self._lock():
            self._prepare_directory(self.observations)
            path = self.observations / f"{observation_id}.json"
            if path.exists():
                existing = self._load_json(path)
                if existing != document:
                    raise LearningError("같은 관찰 ID에 다른 내용이 이미 저장되어 있습니다")
            else:
                _atomic_write_json(path, document)
        return document

    def load_observation(self, observation_id: str) -> dict[str, Any]:
        identifier = self._validate_id(observation_id, "관찰")
        with self._lock():
            document = self._load_json(self.observations / f"{identifier}.json")
        if document.get("observation_id") != identifier:
            raise LearningError("관찰 파일 이름과 내부 ID가 일치하지 않습니다")
        body = {key: value for key, value in document.items() if key != "observation_id"}
        if _digest(b"scv-observation-v1", body) != identifier:
            raise LearningError("관찰 내용의 무결성 검증에 실패했습니다")
        if (
            type(document.get("schema_version")) is not int
            or document["schema_version"] != SCHEMA_VERSION
        ):
            raise LearningError("지원하지 않는 관찰 스키마입니다")
        return document

    def create_candidate(
        self,
        observation_id: str,
        *,
        successful_evidence_sha256: str,
    ) -> dict[str, Any]:
        observation = self.load_observation(observation_id)
        if not SHA256_PATTERN.fullmatch(successful_evidence_sha256):
            raise LearningError("성공 증거 SHA-256이 올바르지 않습니다")
        lesson_id = _digest(
            b"scv-lesson-id-v1", {"observation_id": observation_id}
        )
        failure = observation["failure"]
        analysis = observation["analysis"]
        now = utc_now()
        document = _seal_document(b"scv-lesson-content-v1", {
            "schema_version": SCHEMA_VERSION,
            "lesson_id": lesson_id,
            "status": "candidate",
            "signature": failure["signature"],
            "stage": failure["stage"],
            "command_sha256": failure.get("command_sha256"),
            "task_id": failure["task_id"],
            "source_observation_id": observation_id,
            "diagnosis": analysis["diagnosis"],
            "guidance": analysis["candidate_lesson"],
            "avoid": analysis["failed_approaches"],
            "next_actions": analysis["next_actions"],
            "verification_checks": analysis["verification_checks"],
            "validation": {
                "successful_evidence_sha256": successful_evidence_sha256,
                "successful_repair_count": 1,
                "approved_at": None,
                "suspect_at": None,
                "retired_at": None,
            },
            "created_at": now,
            "updated_at": now,
        })
        with self._lock():
            self._prepare_directory(self.lessons)
            path = self.lessons / f"{lesson_id}.json"
            if path.exists():
                return self._validate_lesson(self._load_json(path), lesson_id)
            _atomic_write_json(path, document)
        return document

    def _validate_lesson(self, value: Mapping[str, Any], expected_id: str) -> dict[str, Any]:
        if (
            type(value.get("schema_version")) is not int
            or value["schema_version"] != SCHEMA_VERSION
        ):
            raise LearningError("지원하지 않는 lesson 스키마입니다")
        if value.get("lesson_id") != expected_id:
            raise LearningError("lesson 파일 이름과 내부 ID가 일치하지 않습니다")
        if value.get("status") not in LESSON_STATES:
            raise LearningError("lesson 상태가 올바르지 않습니다")
        if not ID_PATTERN.fullmatch(str(value.get("signature", ""))):
            raise LearningError("lesson signature가 올바르지 않습니다")
        if not ID_PATTERN.fullmatch(str(value.get("source_observation_id", ""))):
            raise LearningError("lesson 관찰 참조가 올바르지 않습니다")
        _verify_seal(b"scv-lesson-content-v1", value, "lesson")
        return dict(value)

    def load_lesson(self, lesson_id: str) -> dict[str, Any]:
        identifier = self._validate_id(lesson_id, "lesson")
        with self._lock():
            value = self._load_json(self.lessons / f"{identifier}.json")
        return self._validate_lesson(value, identifier)

    def list_lessons(
        self,
        *,
        task_id: str | None = None,
        statuses: Sequence[str] | None = None,
    ) -> list[dict[str, Any]]:
        selected = set(statuses or LESSON_STATES)
        if not selected.issubset(LESSON_STATES):
            raise LearningError("조회할 lesson 상태가 올바르지 않습니다")
        with self._lock():
            self._prepare_directory(self.lessons)
            values = [
                self._validate_lesson(self._load_json(path), path.stem)
                for path in sorted(self.lessons.glob("*.json"))
                if ID_PATTERN.fullmatch(path.stem)
            ]
        return [
            value
            for value in values
            if value["status"] in selected
            and (task_id is None or value.get("task_id") == task_id)
        ]

    def active_lessons(self, signature: str, limit: int = MAX_ACTIVE_LESSONS) -> list[dict[str, Any]]:
        self._validate_id(signature, "실패 signature")
        selected: list[dict[str, Any]] = []
        with self._lock():
            self._prepare_directory(self.lessons)
            for path in sorted(self.lessons.glob("*.json")):
                if not ID_PATTERN.fullmatch(path.stem):
                    continue
                try:
                    lesson = self._validate_lesson(
                        self._load_json(path), path.stem
                    )
                except LearningError:
                    # 한 개의 오래되거나 손상된 비활성 record가 새 실패 분석을
                    # 중단하지 못하게 격리합니다. 명시적 list는 계속 오류를 냅니다.
                    continue
                if (
                    lesson.get("status") == "active"
                    and lesson.get("signature") == signature
                ):
                    selected.append(lesson)
        return selected[: max(0, min(limit, MAX_ACTIVE_LESSONS))]

    def _transition_lesson(
        self,
        lesson_id: str,
        destination: str,
        *,
        approval_evidence: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        identifier = self._validate_id(lesson_id, "lesson")
        if destination not in LESSON_STATES:
            raise LearningError("변경할 lesson 상태가 올바르지 않습니다")
        with self._lock():
            path = self.lessons / f"{identifier}.json"
            document = self._validate_lesson(self._load_json(path), identifier)
            current = document["status"]
            allowed = {
                "candidate": {"active", "retired"},
                "active": {"suspect", "retired"},
                "suspect": {"retired"},
                "retired": set(),
            }
            if destination not in allowed[current]:
                raise LearningError(f"lesson을 {current}에서 {destination}(으)로 변경할 수 없습니다")
            now = utc_now()
            document["status"] = destination
            document["updated_at"] = now
            validation = document["validation"]
            if destination == "active":
                required = {
                    "execution_index_sha256",
                    "final_evidence_sha256",
                }
                if not isinstance(approval_evidence, Mapping) or set(
                    approval_evidence
                ) != required:
                    raise LearningError(
                        "candidate 활성화에는 최종 실행 증거가 필요합니다"
                    )
                if any(
                    not isinstance(value, str)
                    or not SHA256_PATTERN.fullmatch(value)
                    for value in approval_evidence.values()
                ):
                    raise LearningError("최종 실행 증거 SHA-256이 올바르지 않습니다")
                validation["approval_evidence"] = dict(approval_evidence)
                validation["approved_at"] = now
                validation["suspect_at"] = None
            elif destination == "suspect":
                validation["suspect_at"] = now
            elif destination == "retired":
                validation["retired_at"] = now
            document = _seal_document(b"scv-lesson-content-v1", document)
            _atomic_write_json(path, document)
        return document

    def approve(
        self,
        lesson_id: str,
        *,
        approval_evidence: Mapping[str, str],
    ) -> dict[str, Any]:
        return self._transition_lesson(
            lesson_id,
            "active",
            approval_evidence=approval_evidence,
        )

    def mark_suspect(self, lesson_ids: Sequence[str]) -> list[dict[str, Any]]:
        changed: list[dict[str, Any]] = []
        for lesson_id in lesson_ids:
            try:
                lesson = self.load_lesson(lesson_id)
                if lesson["status"] == "active":
                    changed.append(self._transition_lesson(lesson_id, "suspect"))
            except LearningError:
                continue
        return changed

    def record_success(
        self,
        lesson_ids: Sequence[str],
        *,
        successful_evidence_sha256: str,
    ) -> list[dict[str, Any]]:
        if not SHA256_PATTERN.fullmatch(successful_evidence_sha256):
            raise LearningError("lesson 성공 증거 SHA-256이 올바르지 않습니다")
        changed: list[dict[str, Any]] = []
        for raw_id in lesson_ids:
            identifier = self._validate_id(raw_id, "lesson")
            with self._lock():
                path = self.lessons / f"{identifier}.json"
                document = self._validate_lesson(self._load_json(path), identifier)
                if document["status"] != "active":
                    continue
                validation = document["validation"]
                validation["successful_repair_count"] = int(
                    validation.get("successful_repair_count", 0)
                ) + 1
                validation["last_successful_evidence_sha256"] = (
                    successful_evidence_sha256
                )
                document["updated_at"] = utc_now()
                document = _seal_document(b"scv-lesson-content-v1", document)
                _atomic_write_json(path, document)
                changed.append(document)
        return changed

    def retire(self, lesson_id: str) -> dict[str, Any]:
        return self._transition_lesson(lesson_id, "retired")

    def create_proposal(self, observation_id: str, *, kind: str) -> dict[str, Any]:
        observation = self.load_observation(observation_id)
        kind = _safe_string(kind, "개선 제안 종류", maximum=64)
        body = {
            "schema_version": SCHEMA_VERSION,
            "kind": kind,
            "task_id": observation["failure"]["task_id"],
            "source_observation_id": observation_id,
            "status": "proposed",
            "summary": observation["analysis"]["candidate_lesson"],
            "required_flow": [
                "SCV 소스 저장소에서 별도 full 태스크를 시작한다.",
                "회귀 테스트가 수정 전 실패하는지 확인한다.",
                "승인된 계획을 별도 worktree에서 실행한다.",
                "전체 SCV 테스트와 독립 검증을 통과한다.",
                "사람 승인 후에만 병합·설치한다.",
            ],
            "created_at": utc_now(),
        }
        proposal_id = _digest(b"scv-improvement-proposal-v1", body)
        document = _seal_document(
            b"scv-improvement-proposal-content-v1",
            {"proposal_id": proposal_id, **body},
        )
        with self._lock():
            self._prepare_directory(self.proposals)
            path = self.proposals / f"{proposal_id}.json"
            if path.exists():
                existing = self._validate_proposal(self._load_json(path), proposal_id)
                if existing != document:
                    raise LearningError("같은 개선 제안 ID에 다른 내용이 이미 저장되어 있습니다")
                return existing
            _atomic_write_json(path, document)
        return document

    def _validate_proposal(
        self, value: Mapping[str, Any], expected_id: str
    ) -> dict[str, Any]:
        if (
            type(value.get("schema_version")) is not int
            or value["schema_version"] != SCHEMA_VERSION
        ):
            raise LearningError("지원하지 않는 개선 제안 스키마입니다")
        if value.get("proposal_id") != expected_id:
            raise LearningError("개선 제안 파일 이름과 내부 ID가 일치하지 않습니다")
        if value.get("status") not in PROPOSAL_STATES:
            raise LearningError("개선 제안 상태가 올바르지 않습니다")
        _verify_seal(
            b"scv-improvement-proposal-content-v1", value, "개선 제안"
        )
        body = {
            key: item
            for key, item in value.items()
            if key not in {"proposal_id", "content_sha256", "handoff", "closure"}
        }
        body["status"] = "proposed"
        if "source_observation_id" not in body:
            raise LearningError("SCV 개선 제안에 원본 관찰 참조가 없습니다")
        if _digest(b"scv-improvement-proposal-v1", body) != expected_id:
            raise LearningError("개선 제안 ID와 내용이 일치하지 않습니다")
        return dict(value)

    def load_proposal(self, proposal_id: str) -> dict[str, Any]:
        identifier = self._validate_id(proposal_id, "개선 제안")
        with self._lock():
            value = self._load_json(self.proposals / f"{identifier}.json")
        return self._validate_proposal(value, identifier)

    def validate_proposal_source(self, proposal_id: str) -> dict[str, Any]:
        proposal = self.load_proposal(proposal_id)
        source_observation_id = proposal.get("source_observation_id")
        if not isinstance(source_observation_id, str):
            raise LearningError("SCV 개선 제안에 원본 실패 관찰이 없습니다")
        observation = self.load_observation(source_observation_id)
        if observation.get("analysis", {}).get("classification") != "controller":
            raise LearningError("SCV 개선 제안의 원본 분석이 controller 결함이 아닙니다")
        if observation.get("failure", {}).get("task_id") != proposal.get("task_id"):
            raise LearningError("SCV 개선 제안과 원본 태스크가 일치하지 않습니다")
        return proposal

    def _transition_proposal(
        self,
        proposal_id: str,
        destination: str,
        metadata: Mapping[str, Any],
    ) -> dict[str, Any]:
        identifier = self._validate_id(proposal_id, "개선 제안")
        if destination not in {"handed-off", "closed"}:
            raise LearningError("개선 제안의 변경 상태가 올바르지 않습니다")
        with self._lock():
            path = self.proposals / f"{identifier}.json"
            document = self._validate_proposal(self._load_json(path), identifier)
            current = document["status"]
            allowed = {
                "proposed": {"handed-off", "closed"},
                "handed-off": {"closed"},
                "closed": set(),
            }
            if destination not in allowed[current]:
                raise LearningError(
                    f"개선 제안을 {current}에서 {destination}(으)로 변경할 수 없습니다"
                )
            document["status"] = destination
            if destination == "handed-off":
                document["handoff"] = dict(metadata)
            else:
                document["closure"] = dict(metadata)
            document = _seal_document(
                b"scv-improvement-proposal-content-v1", document
            )
            _atomic_write_json(path, document)
        return document

    def handoff_proposal(
        self,
        proposal_id: str,
        *,
        repair_repo: str,
        repair_plugin_root: str,
        repair_task_id: str,
    ) -> dict[str, Any]:
        self.validate_proposal_source(proposal_id)
        return self._transition_proposal(
            proposal_id,
            "handed-off",
            {
                "repair_repo": _safe_string(repair_repo, "수리 저장소", maximum=4_000),
                "repair_plugin_root": _safe_string(
                    repair_plugin_root, "SCV 수리 소스", maximum=4_000
                ),
                "repair_task_id": _safe_string(
                    repair_task_id, "수리 태스크 ID", maximum=64
                ),
                "handed_off_at": utc_now(),
            },
        )

    def close_proposal(self, proposal_id: str, *, reason: str) -> dict[str, Any]:
        return self._transition_proposal(
            proposal_id,
            "closed",
            {
                "reason": _safe_string(reason, "종료 사유", maximum=4_000),
                "closed_at": utc_now(),
            },
        )

    def list_proposals(
        self,
        *,
        task_id: str | None = None,
        statuses: Sequence[str] | None = None,
    ) -> list[dict[str, Any]]:
        selected = set(statuses or PROPOSAL_STATES)
        if not selected.issubset(PROPOSAL_STATES):
            raise LearningError("조회할 개선 제안 상태가 올바르지 않습니다")
        with self._lock():
            self._prepare_directory(self.proposals)
            values = [
                self._validate_proposal(self._load_json(path), path.stem)
                for path in sorted(self.proposals.glob("*.json"))
                if ID_PATTERN.fullmatch(path.stem)
            ]
        return [
            value
            for value in values
            if value["status"] in selected
            and (task_id is None or value.get("task_id") == task_id)
        ]
