# D003 — 태스크 상태와 작업 공간 신원

- 상태: Resolved
- 결정일: 2026-07-17
- 적용 범위: state machine, binding, checkpoint, BLOCKED resume

## 고정 binding과 가변 checkpoint

태스크 생성 시 다음 immutable binding을 기록한다.

- setup이 발급한 repository UUID
- canonical Git common directory identity digest
- project root와 worktree root의 canonical path
- 생성 시 branch name

태스크는 결속된 worktree에서만 재개한다. Git common directory는 identity probe에만
사용하고 태스크나 lock 저장소로 사용하지 않는다. 서로 다른 worktree는 서로 다른
`.scv/tasks`와 `.scv/runtime`을 가지므로 독립 태스크만 병렬 실행할 수 있다.

HEAD와 콘텐츠는 immutable binding이 아니라 mutable checkpoint다. checkpoint에는
HEAD, index, tracked/staged/non-ignored-untracked fingerprint, plan/policy hash,
operation ID와 state revision을 기록한다. `.scv/tasks/**`, `.scv/runtime/**`와 로컬
setup/link manifest는 fingerprint에서 제외한다. 승인된 operation을 kernel이
검증한 뒤에만 checkpoint를 갱신한다.

재개 시 초기 상태가 아니라 마지막 checkpoint와 비교한다. 사용자 commit 또는
승인 밖 파일 변경도 자동 수용하지 않는다. 같은 repository/worktree/branch 안의
drift는 정확 복구 시 원래 state로 복귀할 수 있고, 복구 불가 시 명시적
rebaseline 후 `PLANNING`에서 전제·acceptance와 계획을 다시 승인한다. repository,
worktree 또는 branch binding이 달라졌으면 rebaseline할 수 없고 원래 위치로
복귀하거나 새 태스크를 시작해야 한다.

## BLOCKED safe-resume map

`blocked_from`, `reason_code`, `resume_to`, 검증 predicate를 상태에 저장한다. 다음
표 밖의 전이는 거부한다.

| reason class | 정확 복구 후 | 대체 경로 |
|---|---|---|
| `BACKEND_UNAVAILABLE`, `HOST_INCOMPATIBLE` | `blocked_from` | 없음 |
| `SANDBOX_UNAVAILABLE`, `POLICY_DENIED`, `CREDENTIAL_UNAVAILABLE` | `blocked_from` | 허용 backend를 다시 선택한 뒤 같은 state |
| `WORKSPACE_DRIFT` | `blocked_from` | 같은 binding의 사용자 rebaseline 후 `PLANNING` |
| `BINDING_MISMATCH` | 원래 binding 복구 시 `blocked_from` | 새 태스크 생성 |
| `EVIDENCE_CORRUPT`, `STATE_CORRUPT` | 검증된 backup 복구 시 `blocked_from` | 사용자 export 후 `ABANDONED`만 선택 가능 |

`ABANDONED`는 사용자 명시 확인으로만 진입하고 자료를 삭제하지 않는다. `READY`와
`ABANDONED`는 종료 상태다. stale lock은 OS lock 획득과 이전 attempt evidence
audit를 모두 통과한 경우에만 복구하며 PID 문자열만 보고 강제 해제하지 않는다.
lock을 얻지 못하면 상태를 쓸 수 없으므로 `BLOCKED`로 전이하지 않고 invocation
수준의 `busy`를 반환한다. 승인 무효화, 수렴 종료와 finding 수정은 각각 합법
전이표를 따라 `PLANNING` 또는 `EXECUTING`으로 직접 이동한다.

## Git lifecycle 경계

SCV는 branch/worktree create, switch, adopt, delete와 `commit`, `merge`, `rebase`,
`stash`, `checkout`, `reset`, `tag`, `push`를 실행하지 않는다. `git status`, `diff`,
`rev-parse`, `ls-files` 같은 allowlisted read-only probe만 kernel이 실행한다. backend
prompt뿐 아니라 argv/tool policy와 사후 Git ref/index fingerprint로 이를 검증한다.
