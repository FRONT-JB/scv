# SCV 설계 결정 목록

이 디렉터리는 구현 전에 고정해야 하는 교차 구성요소 계약을 관리한다. 각 문서는
`Resolved` 상태이며, 변경하려면 해당 문서와 `GOAL.md`, 영향을 받는 schema와
테스트를 같은 변경에서 갱신한다.

| ID | 결정 | 상태 |
|---|---|---|
| D001 | [패키지 배포와 활성화](D001-package-activation.md) | Resolved |
| D002 | [호스트 호환성과 호출 계약](D002-host-contract.md) | Resolved |
| D003 | [태스크 상태와 작업 공간 신원](D003-state-workspace.md) | Resolved |
| D004 | [실행 격리와 자격증명](D004-execution-isolation.md) | Resolved |
| D005 | [품질 판정과 데이터 수명주기](D005-quality-lifecycle.md) | Resolved |

이 문서들은 소비 프로젝트에 누적되는 ADR이 아니다. 플러그인 자체의 구현
기준선이며, 소비 프로젝트의 결정은 `.scv/docs/adr/**`에 별도로 기록한다.
