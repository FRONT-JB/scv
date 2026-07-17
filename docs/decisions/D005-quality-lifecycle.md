# D005 — 품질 판정과 데이터 수명주기

- 상태: Resolved
- 결정일: 2026-07-17
- 적용 범위: finding, ADR, Ollama, legacy learning, retention

## Finding과 waiver

| severity | 기본 판정 | waiver |
|---|---|---|
| `critical` | `must_fix` | 금지 |
| `high` | `must_fix` | 금지 |
| `medium` | `must_fix` 또는 policy별 `waivable` | 사용자 명시 승인만 허용 |
| `low` | `advisory` | handoff acknowledgement로 충분 |

waiver에는 finding digest, 영향, 사유, 보완 조치, owner, 만료 조건과 사용자 승인
revision을 기록한다. 보안 경계 위반, 데이터 손실, 승인 범위 이탈, sandbox 실패와
결정적 acceptance 실패는 severity와 무관하게 waiver할 수 없다. Codex review와
결정적 verifier가 불일치하면 자동 완화하지 않고 `VERIFIER_DISAGREEMENT`로
`PLANNING`에 돌아간다.

## ADR

ADR ID는 `ADR-<task-id>-<task-sequence>-<slug>`이며 전역 순번을 쓰지 않는다.
파일 frontmatter의 `id`, `title`, `date`, `task_id`, `status`, `decision_hash`,
`supersedes`가 권위 metadata다. accepted ADR은 내용을 제자리에서 덮어써
supersede하지 않는다. 새 ADR이 `supersedes`로 이전 ID를 참조하며 현재 상태와
목록은 파일 집합에서 결정적으로 생성한다. 생성 index는 cache일 뿐 권위 원장이
아니다. 기존 `ADR-0001-*` 파일은 rename하지 않는다.

## Ollama qualification

Ollama는 기본 disabled다. 특정 `(operation, model digest, prompt digest, schema
version)` 조합이 고정된 최소 100개 fixture에서 다음 기준을 모두 통과해야만
저위험 operation에 opt-in할 수 있다.

- schema-valid 결과 `>= 99%`
- reference가 있는 사실 정확도 `>= 95%`, critical factual error `0건`
- retry 필요 비율 `<= 2%`
- 원격 저비용 기준선 대비 입력 token 또는 유료 호출 절감 `>= 30%`
- p95 latency가 원격 기준선의 `120%` 이하

operation당 Ollama 시도는 1회다. 실패하면 원격 허용 backend로 최대 1회 fallback하고
두 실패를 각각 기록한다. 승인, 대화 종료, 최종 계획 위험, 보안·review verdict,
정책 변경과 READY gate에는 qualification 여부와 무관하게 사용하지 않는다. setup은
설치·pull·start·delete를 실행하지 않는다.

## Legacy improve와 learning

v1에서는 기존 Git common directory의 task/learning store를 read-only로 retain한다.
새 실행에 lesson을 주입하거나 새 observation/candidate/proposal을 기록하지 않고
공개 `improve` Skill도 제공하지 않는다. 별도 ADR과 migration 검증 전에는 변환,
이동, 삭제하지 않는다.

setup은 Git common directory의 legacy signature와 Git index에 추적된 기존
`.scv/tasks/**`, `.scv/runtime/**`, legacy manifest를 읽기 전용으로 탐지한다.
후자가 존재하면 새 mutable store로 오인하지 않고 init/upgrade를 차단한다. 사용자가
직접 추적 경계를 해소하거나 별도 승인된 문서 export를 선택해야 하며 SCV는
`git rm --cached`를 실행하지 않는다.

## Retention과 정리

자동 삭제는 없다. terminal task와 evidence는 기본 무기한 보존한다. 수동
`task prune`은 `READY` 또는 `ABANDONED` 상태로 90일 이상 지난 태스크만 대상으로
하며 항상 dry-run manifest, 명시 확인과 삭제 후 tombstone을 요구한다. 승인
snapshot, handoff, ADR과 migration manifest는 prune 대상이 아니다. runtime
`tmp/cache`만 owner manifest와 active lock 부재를 확인한 뒤 7일 기준으로 repair의
명시 승인 cleanup 대상이 될 수 있다.
