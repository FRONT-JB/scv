# SCV 재구성 목표

## 문서 목적

이 문서는 SCV의 목표 구조와 동작 경계를 정의한다. 구현 계획이나 현재 동작
계약을 대신하지 않으며, 재구성 과정에서 설계와 구현을 판단하는 기준으로 사용한다.

현재 `workflow-contract.md`와 `improvement-contract.md`는 기존 구현의 계약이다.
새 계약과 마이그레이션 계획이 승인되고 검증되기 전까지는 기존 계약이 계속
유효하다.

## 목표

SCV를 Claude Code와 Codex에서 동일하게 사용할 수 있는 macOS용 프로젝트 로컬
워크플로 시스템으로 재구성한다.

- 플러그인과 Skill의 권위 있는 원본은 프로젝트의 `.scv/plugin/**`에 둔다.
- Claude와 Codex는 심볼릭 링크를 통해 같은 Skill 원본을 사용한다.
- setup 이후에는 `scv-1-plan` 하나로 계획, 승인, 구현, 검증, 리뷰와 핸드오프를
  진행한다.
- 계획은 저장소 조사 후 사용자와 한 번에 하나의 쟁점을 충분히 협의하는 대화형
  과정으로 작성한다.
- 단계별 모델 지시문은 Skill의 `references/*.md`에 정적으로 관리한다.
- 에이전트가 실행하는 Skill 지침은 영문으로 작성하고, 각 Skill의 `README.md`에
  동일한 동작을 설명하는 한글판을 함께 제공한다.
- 모델 결과는 구조화된 데이터로 검증한 뒤 고정된 문서 템플릿으로 렌더링한다.
- 대화와 판단은 Skill이 맡고, 상태·승인·경로·검증·증거는 결정적 Python 커널이
  맡는다.
- 태스크 상태와 잠금은 현재 worktree의 `.scv/**`에 한정하고, 같은 태스크를 다른
  worktree에서 암묵적으로 재개하지 않는다.
- 사용자가 준비한 현재 브랜치 또는 worktree에서 작업하며 SCV가 Git 작업 공간을
  생성하거나 전환하지 않는다.
- 구현과 검증은 승인된 시도 예산과 fail-closed 샌드박스 경계 안에서 수행한다.
- 작업의 난이도와 위험도에 맞춰 Codex, Claude와 선택적 Ollama 백엔드를 사용한다.
- 프로젝트 문서와 ADR을 태스크 실행 과정에 연결하고 지속적으로 누적한다.

## 사용자 경험

### 최초 구성

사용자는 프로젝트 루트에서 setup을 한 번 실행한다.

```text
$scv:setup 이 프로젝트를 구성해줘
```

setup은 변경 계획을 먼저 보여주고 승인을 받은 뒤 `.scv`, `.agents`와 `.claude`
연결 구조를 만든다. 이미 구성된 프로젝트에서는 상태 확인, 복구와 업그레이드를
지원한다.

### 태스크 실행

setup 이후의 기본 진입점은 `scv-1-plan`이다.

```text
$scv:scv-1-plan 이 변경을 진행해줘
```

정상 흐름은 다음과 같다.

```text
요청 조사
  → 범위와 전제 확인
  → 질문과 답변
  → 대안 비교
  → 설계 구간별 확인
  → 계획 작성과 자체 검토
  → 사용자 최종 승인
  → PLAN_READY
      ├─ 계획까지만 요청 → 보류
      └─ 실행 요청
          → 구현
          → 테스트와 검증
          → Codex 리뷰
          → 핸드오프
          → READY
```

사용자는 구현, 검증, 리뷰와 핸드오프를 각각 다른 Skill로 호출하지 않는다.
`scv-1-plan`은 승인 또는 사용자 결정이 필요하거나 작업이 차단된 경우에만 멈춘다.
중단된 태스크에 다시 `scv-1-plan`을 호출하면 완료된 단계를 반복하지 않고 저장된
상태에서 재개한다.

사용자가 계획까지만 요청한 경우에는 승인된 계획을 저장하고 `PLAN_READY`에서
구현을 시작하지 않는다.

### 계획 대화 경험

`scv-1-plan`은 기본적으로 충분한 협의를 우선한다. 질문 개수를 미리 제한하지
않고, 구현 결과를 바꿀 수 있는 미해결 불확실성이 없어질 때까지 사용자와
대화를 이어간다. 많은 질문 자체가 목적은 아니며 이미 확인된 내용을 다시 묻지
않는다.

계획 대화는 다음 순서로 진행한다.

1. 프로젝트 문서, 관련 코드, 테스트, 현재 변경과 이전 태스크 결정을 먼저
   조사한다.
2. 요청이 한 태스크로 다룰 수 있는지 판단하고, 너무 크면 독립적으로 검증 가능한
   단위와 진행 순서를 사용자와 정한다.
3. 조사로 확인할 수 없는 목적, 제약, 성공 기준과 선호를 한 번에 하나씩 질문한다.
4. 중요한 범위 또는 설계 결정마다 2~3개 대안을 비교하고 추천안과 이유를
   제시한다.
5. 합의한 설계를 복잡도에 맞는 작은 구간으로 보여주고 구간마다 이해가 맞는지
   확인한다.
6. 합의 내용을 계획으로 렌더링한 뒤 누락, 모순, 모호성, 과도한 범위와 검증
   가능성을 자체 검토한다.
7. 사용자가 작성된 계획과 남은 가정을 검토하고 정확한 계획 버전을 승인해야
   구현으로 이동한다.

각 질문은 다음 계약을 따른다.

- 한 메시지에는 하나의 주된 결정만 담는다. 서로 종속된 후속 질문은 다음
  메시지로 나눈다.
- 질문이 필요한 이유와 결과에 미치는 영향을 짧게 설명한다.
- 선택형이 적합하면 상호 배타적인 2~4개 선택지, 추천안, 선택지별 장단점을
  제공하고 자유 입력도 허용한다.
- 저장소에서 확인할 수 있는 사실을 사용자에게 묻지 않는다. 조사 결과와 사용자
  설명이 충돌할 때만 근거와 함께 확인한다.
- 모호한 답변은 임의로 확정하지 않는다. 이해한 내용을 다시 말하고 구체적 사례,
  경계 조건 또는 관찰 가능한 성공 기준을 후속 질문으로 확인한다.
- 이미 요청에 답이 있거나 이전에 확정된 결정은 건너뛴다. 새 근거 없이 같은
  결정을 반복해서 설득하지 않는다.
- 되돌리기 어렵거나 파괴적인 결정은 정확한 영향과 복구 가능성을 밝히고 명시적
  확인을 받는다.
- 호스트의 구조화 질문 기능을 사용할 수 없으면 같은 내용을 일반 메시지로 한
  번만 질문하고 응답을 기다린다. 질문 실패를 승인이나 기본 선택으로 간주하지
  않는다.

질문은 최소한 다음 영역을 점검하되, 해당하지 않는 항목은 이유와 함께 제외한다.

- 해결할 문제, 대상 사용자와 기대 효과
- 관찰 가능한 완료·수용 기준
- 포함 범위, 비범위와 최소 유효 변경
- 현재 동작, 호환성, 운영 및 기술 제약
- 구성요소 경계, 데이터 흐름과 외부 인터페이스
- 오류, 복구, 보안, 개인정보와 성능 위험
- 마이그레이션, 배포, 되돌리기와 기존 사용자 영향
- 테스트, 검증 증거, 문서, ADR과 핸드오프 조건

사용자가 대화를 줄여 달라고 명시한 경우에도 목표, 범위, 수용 기준과 중대한
위험은 생략하지 않는다. 요청 자체가 충분히 구체적이면 확인된 내용과 가정을
짧게 제시하고 바로 설계 검토로 이동할 수 있다.

## 아키텍처 원칙

### 단일 원본

공유 가능한 구성요소는 `.scv/plugin`에 한 번만 저장한다.

```text
.agents/skills/setup ─────┐
                          ├──> .scv/plugin/skills/setup
.claude/skills/setup ─────┘

.agents/skills/scv-1-plan ──────┐
                                ├──> .scv/plugin/skills/scv-1-plan
.claude/skills/scv-1-plan ──────┘
```

심볼릭 링크는 다음 규칙을 따른다.

- Skill 디렉터리별 상대 링크를 생성한다.
- `.agents`와 `.claude`가 각각 `.scv/plugin/skills`를 직접 가리킨다.
- 링크 체인과 절대 링크를 만들지 않는다.
- 링크 해석 결과가 프로젝트의 `.scv/plugin/**` 밖이면 거부한다.
- 기존 파일, 디렉터리 또는 다른 대상의 링크를 덮어쓰지 않는다.
- setup이 관리하는 링크만 manifest에 기록하고 복구 대상으로 취급한다.

`.agents/**`와 `.claude/**`에는 호스트가 발견하기 위한 링크만 둔다. 실제
플러그인 콘텐츠와 실행 산출물은 `.scv/**`에 유지한다.

### 공통 구성요소와 호스트 어댑터

다음 구성요소는 두 호스트가 공유한다.

- `SKILL.md`
- `README.md`
- `references/*.md`
- 산출물 템플릿
- JSON Schema
- Python 커널과 검증 스크립트
- 워크플로 및 모델 정책

다음 구성요소는 형식이 다르므로 호스트별로 분리한다.

- `.codex-plugin/plugin.json`
- `.claude-plugin/plugin.json`
- 호스트별 agent 정의
- 호스트별 hook descriptor
- 호스트별 모델 이름과 실행 옵션
- 호스트별 설치 및 marketplace metadata

호스트별 파일은 공통 정책을 참조하는 얇은 어댑터로 유지한다. 서로 다른 형식의
설정 파일을 동일한 심볼릭 링크로 강제로 공유하지 않는다.

`agents/openai.yaml`은 Codex Skill UI metadata가 Skill 디렉터리 안에 있어야 하는
형식상의 예외다. Claude는 이 파일을 실행 계약으로 사용하지 않는다. Claude 전용
agent와 hook metadata는 `hosts/claude`, Codex 전용 호출·검증 metadata는
`hosts/codex`에 둔다.

### Skill과 Python 커널

Skill은 다음을 담당한다.

- 요청과 프로젝트 문맥 파악
- 필요한 조사와 reference 선택
- 계획과 대안 설명
- 사용자 승인 요청
- 진행 상황과 결과 설명

Python 커널은 다음을 담당한다.

- setup, manifest, link와 translation digest 검증
- 태스크 상태 전이와 잠금
- task ID 발급, 선택 후보와 worktree execution lease
- 승인 대상의 버전과 해시 기록
- 작업 공간 신원 검증
- 정적 지시문의 변수 렌더링
- registry executor 검증, 모델 backend 선택과 격리 호출
- acceptance sandbox, 시도 예산과 수렴 판정
- 구조화된 결과 검증
- 문서와 증거의 원자적 저장
- 재개와 충돌 복구

모델의 자유 형식 문장, 화면 표시용 라벨 또는 sentinel 문자열을 상태 제어에
사용하지 않는다.

## 목표 구조

```text
{{project}}/
├── .scv/
│   ├── plugin/
│   │   ├── .codex-plugin/
│   │   │   └── plugin.json
│   │   ├── .claude-plugin/
│   │   │   └── plugin.json
│   │   ├── skills/
│   │   │   ├── setup/
│   │   │   │   ├── SKILL.md
│   │   │   │   ├── README.md
│   │   │   │   ├── agents/
│   │   │   │   │   └── openai.yaml
│   │   │   │   └── references/
│   │   │   └── scv-1-plan/
│   │   │       ├── SKILL.md
│   │   │       ├── README.md
│   │   │       ├── agents/
│   │   │       │   └── openai.yaml
│   │   │       └── references/
│   │   ├── scripts/
│   │   │   ├── scv.py
│   │   │   └── core/
│   │   ├── schemas/
│   │   ├── assets/
│   │   ├── registry/
│   │   └── hosts/
│   │       ├── codex/
│   │       └── claude/
│   ├── config/
│   │   ├── project.json
│   │   ├── workflow-policy.json
│   │   ├── model-policy.json
│   │   ├── link-manifest.json
│   │   └── setup-manifest.json
│   ├── docs/
│   │   ├── SYSTEM.md
│   │   ├── DOCS-POLICY.md
│   │   ├── CHANGES.md
│   │   ├── work-items/
│   │   │   └── <task-id>/
│   │   │       ├── request.md
│   │   │       ├── plan.md
│   │   │       └── handoff.md
│   │   ├── adr/
│   │   ├── investigations/
│   │   └── quality/
│   │       ├── VERIFICATION.md
│   │       └── KNOWN-GAPS.md
│   ├── tasks/
│   │   └── <task-id>/
│   │       ├── state.json
│   │       ├── request.md
│   │       ├── planning-dialogue.json
│   │       ├── plan.json
│   │       ├── approval.json
│   │       ├── execution.json
│   │       ├── evidence/
│   │       ├── events/
│   │       └── handoff.md
│   ├── runtime/
│   │   ├── locks/
│   │   ├── cache/
│   │   └── tmp/
│   └── .gitignore
├── .agents/
│   └── skills/
│       ├── setup -> ../../.scv/plugin/skills/setup
│       └── scv-1-plan  -> ../../.scv/plugin/skills/scv-1-plan
└── .claude/
    └── skills/
        ├── setup -> ../../.scv/plugin/skills/setup
        └── scv-1-plan  -> ../../.scv/plugin/skills/scv-1-plan
```

### 주요 경로의 책임

- `plugin/schemas`: 모델 출력과 커널 상태가 만족해야 하는 versioned JSON Schema
- `plugin/assets`: 커널이 결과 문서를 렌더링할 때 사용하는 비실행 템플릿
- `plugin/registry`: operation ID, executor, reference, schema, 모델 role과 출력
  artifact의 allowlist
- `plugin/hosts/codex`: Codex 모델 ID·옵션 매핑, 호출 adapter와 Codex 전용 검증
- `plugin/hosts/claude`: Claude 모델 ID·옵션 매핑, hook과 Claude 전용 호출 adapter
- `config/project.json`: 절대 경로나 자격증명 없이 저장하는 프로젝트 식별자,
  기본 응답 언어와 layout version
- `config/workflow-policy.json`: 합법 상태 전이, 질문·승인 gate, 시도 예산,
  finding waiver와 executor 정책
- `config/model-policy.json`: operation이 참조하는 논리적 모델 role과 fallback 정책
- `config/link-manifest.json`, `config/setup-manifest.json`: 현재 장비에서 setup이
  관리하는 링크와 설치 결과를 기록하는 로컬 manifest
- `tasks/<task-id>/state.json`: authoritative state, revision, binding, checkpoint와
  `BLOCKED` 복귀 정보
- `tasks/<task-id>/request.md`: 해당 태스크에 정규화된 최초 요청과 후속 범위 변경
- `tasks/<task-id>/planning-dialogue.json`: 확정 사실, 결정, 가정, 질문과 구간별
  승인 상태
- `tasks/<task-id>/plan.json`: schema로 검증된 실행 단계, acceptance와 attempt 정책
- `tasks/<task-id>/approval.json`: plan, dialogue, workspace와 waiver hash에 대한
  사용자 승인 revision
- `tasks/<task-id>/execution.json`: run과 step별 attempt, 종료 사유 및 현재 실행 index
- `tasks/<task-id>/evidence/`: command, backend, verifier와 artifact의 hash된 실행 증거
- `tasks/<task-id>/events/`: 커널이 순서와 revision을 부여한 append-only 상태 사건
- `tasks/<task-id>/handoff.md`: 검증 후 생성하는 인계 초안과 READY gate 입력
- `docs/work-items/<task-id>`: 승인된 요청·계획과 완료된 핸드오프의 Git 추적용
  렌더링 사본

plugin 루트에는 별도의 `references/`를 두지 않는다. 실행 지시문은 항상 소유
Skill의 `references/*.md`에 두어 registry의 적용 대상을 모호하지 않게 한다.

## Skill 구성

### setup

`setup/SKILL.md`는 사용자와 초기 설정을 협의하고 결정적 setup 커널을 호출한다.

```text
setup/
├── SKILL.md
├── README.md
├── agents/
│   └── openai.yaml
└── references/
    ├── inspect-project.md
    ├── prepare-installation.md
    ├── repair-installation.md
    └── verify-installation.md
```

### scv-1-plan

`scv-1-plan/SKILL.md`는 새 태스크 시작과 재개의 단일 대화 진입점이다. 구현 이후의
단계도 별도 Skill을 호출하지 않고 operation registry와 Python 커널을 통해
진행한다.

이름의 `1`은 setup 이후 첫 번째 공개 워크플로 진입점이라는 안정적인 정렬
prefix다. 이후 Skill의 존재나 이름을 자동으로 암시하지 않으며, 새 공개 Skill은
별도 설계와 승인을 거쳐 추가한다.

```text
scv-1-plan/
├── SKILL.md
├── README.md
├── agents/
│   └── openai.yaml
└── references/
    ├── understand-request.md
    ├── conduct-dialogue.md
    ├── compare-approaches.md
    ├── present-design.md
    ├── compose-plan.md
    ├── review-plan.md
    ├── revise-plan.md
    ├── perform-change.md
    ├── check-result.md
    ├── assess-change.md
    ├── record-decision.md
    ├── prepare-handoff.md
    └── explain-blocker.md
```

reference 파일은 모두 Markdown으로 작성한다. 파일 자체에는 별도의 YAML
metadata를 넣지 않는다. operation ID, 버전, 필수 변수, backend profile,
출력 스키마와 산출물 템플릿은 JSON registry가 관리한다.

`SKILL.md`에는 계획 대화의 순서와 중단 조건만 간결하게 둔다. 질문 형식, 대안
비교, 설계 제시와 계획 자체 검토의 세부 지시문은 각각의 reference로 분리해
필요한 시점에만 읽는다.

초기 operation의 주 실행자는 다음과 같이 고정한다.

| Reference | Executor | 경계 |
|---|---|---|
| `understand-request.md` | host | 요청과 현재 대화 문맥 정리 |
| `conduct-dialogue.md` | host | 한 번에 하나의 질문과 응답 수집 |
| `compare-approaches.md` | host | 대안과 추천 설명, 사용자 선택 |
| `present-design.md` | host | 설계 구간 제시와 확인 |
| `compose-plan.md` | backend | 구조화된 계획 초안 생성 |
| `review-plan.md` | backend | 계획 누락·모순·범위 검토 |
| `revise-plan.md` | backend | 사용자 결정에 따른 계획 revision 생성 |
| `perform-change.md` | backend | 격리된 worktree 구현 |
| `check-result.md` | kernel | acceptance와 결정적 검증 실행 |
| `assess-change.md` | backend | 읽기 전용 Codex 리뷰 |
| `record-decision.md` | backend | ADR 내용 초안, ID와 저장은 kernel 담당 |
| `prepare-handoff.md` | backend | schema 대상 인계 초안 생성 |
| `explain-blocker.md` | host | kernel blocker를 한글로 설명하고 선택 요청 |

한 operation 안에서 executor를 암묵적으로 바꾸지 않는다. backend 결과를 저장,
승인 또는 상태 전이에 사용하는 후속 처리는 항상 별도 kernel gate다.

### 지침 언어와 한글판

Skill의 실행 계약은 영문 원본 한 벌로 관리한다.

- `SKILL.md`의 frontmatter와 본문은 영문으로 작성한다.
- `references/*.md`의 역할, 입력, 절차, 금지 동작과 출력 계약도 영문으로
  작성한다.
- 모델이 직접 소비하는 기본 prompt와 host adapter 지침은 영문으로 작성한다.
- 파일명, Skill 이름, operation ID, 상태값과 JSON 필드명은 영문 식별자를
  유지한다.
- 사용자에게 보여주는 질문, 진행 상황, 오류와 최종 결과는 기본적으로 한글로
  응답하도록 영문 지침에 명시한다.

각 Skill의 `README.md`는 사람이 검토할 수 있는 한글판 한 벌이다.

- 해당 Skill의 `SKILL.md` 전체 동작과 모든 reference의 목적, 입력, 절차, 중단
  조건 및 출력 계약을 빠짐없이 한글로 설명한다.
- 영문 원본의 섹션 순서와 의미를 유지하되 직역보다 정확하고 자연스러운 설명을
  우선한다.
- 새로운 동작, 예외 또는 권한을 `README.md`에만 추가하지 않는다.
- YAML frontmatter를 넣지 않으며 Skill discovery와 모델 prompt 입력에 사용하지
  않는다.
- 영문 원본과 충돌하면 `SKILL.md`와 `references/*.md`가 우선한다.

각 `SKILL.md`에는 다음 `Maintenance` 섹션과 유지보수 규칙을 영문으로 명시한다.

> ## Maintenance
>
> When this skill or any referenced instruction changes, update README.md in the
> same change and refresh its source digest.

번역본의 불일치를 막기 위해 Skill별 영문 원본 묶음의 digest를 관리한다. 원본
묶음은 `SKILL.md`와 파일명 순으로 정렬한 `references/*.md`로 구성한다.
`README.md`에는 번역 대상 버전, 전체 source digest와 원본 파일별 digest를
기계가 읽을 수 있는 주석으로 기록한다. 원본이 바뀌었는데 digest가 갱신되지
않았거나 reference 설명이 빠진 경우 setup 검증과 결정적 테스트를 실패시킨다.
모든 `SKILL.md`에 `Maintenance` 섹션과 위 규칙이 존재하는지도 함께 검증한다.
번역 생성은 모델을 사용할 수 있지만 digest 계산, 파일 목록 대조와 누락 판정은
Python 커널이 수행한다.

## 정적 지시문과 산출물

정적 지시문은 모델에게 수행할 역할, 입력 자료, 금지 동작과 출력 계약을
제공한다. 프로젝트 데이터와 태스크 값은 allowlist 변수만 치환한다.

```text
Skill
  → operation 선택
  → registry에서 executor와 계약 확인
      ├── host: 사용자 대화와 승인 표시
      ├── kernel: 상태·검증·렌더링 같은 결정적 처리
      └── backend: reference 렌더링 → 격리된 모델 실행 → schema 검증
  → kernel이 artifact 저장과 상태 전이를 확정
```

각 registry entry는 최소한 `operation_id`, `executor`, `reference`, `schema`,
`model_role`, `required_inputs`, `output_artifact`와 버전을 가진다. `host`와
`kernel` operation에는 불필요한 backend 호출을 강제하지 않는다.

- `host`: 요청 이해, 계획 질문, 대안·설계 제시와 사용자 승인 수집
- `kernel`: setup, 상태 전이, 잠금, workspace 검증, digest, schema와 문서 렌더링
- `backend`: 계획 초안 통합, 격리된 구현, 검증 보조, 리뷰와 문서 초안 생성

- 등록되지 않은 reference는 실행하지 않는다.
- registry에 executor가 없거나 executor와 operation이 맞지 않으면 실행하지 않는다.
- 정의되지 않았거나 치환되지 않은 변수가 있으면 실행을 중단한다.
- 사용한 reference, schema, 모델 profile과 결과의 버전·해시를 기록한다.
- 모델이 최종 파일명, ADR 번호, 상태값과 승인 여부를 결정하지 않는다.
- 문서 구조는 모델의 Markdown 작성 능력에 의존하지 않고 렌더러가 보장한다.
- 런타임 prompt에는 한글판 `README.md`를 포함하지 않는다.

## setup 동작

setup은 다음 명령 개념을 지원한다.

```text
status
init
repair
upgrade
validate
```

- `status`: 파일을 만들거나 고치지 않고 설치, 링크, 버전과 충돌 상태를 조회한다.
- `init`: 미구성 프로젝트에 변경 계획을 제시하고 승인 후 새 구조를 만든다.
- `repair`: setup manifest가 소유한 누락·손상 항목만 복구한다. 사용자가 수정한
  파일과 소유권을 증명할 수 없는 경로는 덮어쓰지 않는다.
- `upgrade`: 현재 layout version에서 목표 version까지의 migration 계획, 보존
  항목과 rollback 범위를 보여주고 별도 승인 후 적용한다.
- `validate`: 상태를 변경하지 않고 manifest, 링크, schema, registry, 번역 digest,
  권한과 containment를 전체 검증한다.

setup 커널은 별도 상태 엔진이 아니라 공통 Python 커널의 setup 모듈이다. 같은
경로 검증, 잠금, 원자적 교체와 manifest API를 사용하되 태스크 상태를 만들지
않는다.

setup은 다음 순서로 동작한다.

1. 프로젝트 루트, 운영체제, Git, Python과 설치된 모델 backend를 읽기 전용으로
   검사한다.
2. 신규 설치, 기존 설치, 복구 또는 업그레이드 여부를 판정한다.
3. 생성, 유지, 충돌, 변경될 경로를 보여준다.
4. 사용자 승인 후 `.scv` 원본과 Skill별 상대 심볼릭 링크를 구성한다.
5. manifest, registry, schema, Python, 링크, 경로 containment, Skill 유지보수 규칙과
   한글판 source digest를 검증한다.
6. 생성한 파일과 링크의 해시·대상·버전을 setup manifest에 기록한다.
7. 실패하면 이번 실행에서 새로 만든 항목만 롤백한다.

setup은 기존 일반 파일이나 사용자가 수정한 파일을 자동으로 덮어쓰지 않는다.
Ollama 모델 다운로드, plugin 설치 또는 호스트 외부 설정 변경은 별도 승인을
요구한다.

프로젝트 Skill 링크를 지원하지 않는 Claude Code 버전에서는 Skill을 복사하지
않고 `.scv/plugin`을 직접 로드하는 실행 방식을 안내한다.

### 활성화 모드

- `linked`: 프로젝트의 `.agents/skills`와 `.claude/skills` 링크를 사용하는 기본
  개발 모드
- `installed`: 호스트의 plugin cache를 이용하는 배포 검증 모드

두 모드를 동시에 활성화해 같은 Skill을 중복 노출하지 않는다. 설치형 플러그인은
호스트 cache로 복사되므로 필요한 파일을 모두 `.scv/plugin/**` 안에 포함하고
프로젝트 외부 링크에 의존하지 않는다.

## 태스크 상태와 진행

### 태스크 식별과 선택

- task ID는 커널이 UTC 시각과 충돌 방지 난수로 발급한다. 모델은 ID를 만들거나
  기존 태스크와의 동일성을 판정하지 않는다.
- 명시적인 task ID가 주어지면 해당 worktree에 결속된 태스크만 재개한다.
- ID 없이 호출했을 때 재개 가능한 태스크가 없으면 새 태스크를 만든다.
- 재개 가능한 태스크가 하나 이상 있거나 요청이 신규인지 재개인지 불명확하면
  기존 태스크 목록과 새 태스크 선택지를 보여주고 사용자가 고르게 한다. 요청의
  의미가 비슷하다는 이유로 자동 재개하지 않는다.
- 한 worktree에는 동시에 하나의 `EXECUTING`, `VERIFYING` 또는 `HANDOFF_READY`
  태스크만 허용한다. 여러 계획·보류·차단 태스크는 보관할 수 있다.
- 태스크별 process lock과 worktree별 비차단 execution lease를 분리한다. 잠금
  파일이 남아 있어도 실제 lock을 획득할 수 있으면 stale 파일로 간주하며, 이전
  `running` attempt는 증거 audit 후 같은 attempt 번호로 재개한다.
- 태스크 포기는 사용자의 명시적 확인만으로 수행하고 상태, 문서와 증거를 삭제하지
  않는다.

### 합법 상태 전이

정상 진행과 되돌림은 다음과 같다.

```text
PLANNING
  → AWAITING_APPROVAL
      ├── 변경 요청 → PLANNING
      └── 승인 → PLAN_READY
                    ├── 계획까지만 요청 → PLAN_READY에 유지
                    └── 실행 요청 → EXECUTING
                                      ├── 계획 변경 필요 → PLANNING
                                      └── 완료 → VERIFYING
                                                   ├── 승인 범위 내 수정 → EXECUTING
                                                   ├── 계획 변경 필요 → PLANNING
                                                   └── 통과 → HANDOFF_READY
                                                                ├── 수정 필요 → EXECUTING
                                                                ├── 계획 변경 필요 → PLANNING
                                                                └── gate 통과 → READY
```

커널은 `workflow-policy.json`의 명시적 전이표만 허용한다.

- `PLANNING → AWAITING_APPROVAL | BLOCKED | ABANDONED`
- `AWAITING_APPROVAL → PLANNING | PLAN_READY | BLOCKED | ABANDONED`
- `PLAN_READY → EXECUTING | PLANNING | BLOCKED | ABANDONED`
- `EXECUTING → VERIFYING | PLANNING | BLOCKED | ABANDONED`
- `VERIFYING → EXECUTING | PLANNING | HANDOFF_READY | BLOCKED | ABANDONED`
- `HANDOFF_READY → EXECUTING | PLANNING | READY | BLOCKED | ABANDONED`
- `BLOCKED → resume_to | ABANDONED`
- `READY`와 `ABANDONED`는 종료 상태다.

`scv-1-plan`은 커널이 사용자 입력 필요, 차단 또는 완료를 반환할 때까지 다음
operation을 실행한다. 모델 출력이나 화면 라벨로 상태를 건너뛰지 않는다.

### 계획과 승인

- `PLANNING` 내부 진행 단계는 `CONTEXT`, `DIALOGUE`, `OPTIONS`, `DESIGN_REVIEW`,
  `PLAN_REVIEW` 순서로 관리한다.
- 요청, 관련 구현, 프로젝트 문서와 테스트를 조사한 뒤 질문을 시작한다.
- 목표, 비목표, 변경 범위, 단계, 위험, 검증 방법과 핸드오프 조건을 기록한다.
- `planning-dialogue.json`에는 원문 transcript 대신 질문 ID, 답변 요약, 확인된
  사실, 결정, 거절한 대안과 이유, 사용자가 수용한 가정, 미해결 질문 및 설계
  구간별 확인 상태를 저장한다.
- 커널은 대화 상태를 잠금 아래 원자적으로 갱신한다. 재개 시 확정된 질문을 다시
  묻지 않고 짧은 합의 요약과 다음 미해결 질문부터 시작한다.
- 모든 필수 영역이 확인되고 미해결 질문이 없거나 남은 가정을 사용자가 명시적으로
  수용했으며 계획 자체 검토가 통과한 경우에만 `AWAITING_APPROVAL`로 이동한다.
- 사용자가 현재 계획의 정확한 버전과 계획 대화 revision을 승인하면
  `PLAN_READY`로 이동한다. 계획까지만 요청한 태스크는 이 상태에서 안전하게
  보류한다.
- 계획, 승인된 가정 또는 대화 revision의 실질적인 내용이 바뀌면 기존 승인을
  무효화하고 `PLANNING`으로 돌아간다.
- `PLAN_READY`에서 나중에 구현을 시작할 때 workspace checkpoint를 다시 검증한다.
  기준 HEAD가 바뀌었으면 재결속과 새 계획 승인을 거쳐야 한다.

### 구현

- 승인된 계획과 결속된 worktree에서만 변경하고, host 대화 세션이 직접 구현
  명령을 실행하지 않는다. 커널이 승인된 backend operation을 격리해 호출한다.
- 승인 범위의 실질적 변경이 필요하면 계획으로 돌아간다.
- 모델의 완료 보고만으로 다음 단계로 이동하지 않는다.

### 실행 예산과 수렴

- 계획은 각 구현 단계의 `max_attempts`를 1~3으로 제한하며 기본 권장값은 2다.
- 구현 또는 verifier 실패만 attempt를 소비한다. backend·샌드박스·필수 도구
  시작 실패, 취소와 controller crash는 예산을 소비하지 않고 `BLOCKED`로 남긴다.
- 실패 후에는 failure signature, acceptance 결과 벡터와 workspace fingerprint로
  커널이 수렴 fingerprint를 계산한다.
- 동일 fingerprint가 연속되면 `stalled`, 과거 fingerprint가 반복되면
  `oscillating`, workspace 변경 없이 같은 verifier 실패가 반복되면
  `verifier_disagreement`로 종료한다.
- `budget_exhausted`, `stalled`, `oscillating`, `verifier_disagreement`가 발생하면
  추가 모델 호출이나 acceptance 재실행 없이 `PLANNING`으로 돌아가 계획 수정과
  새 승인을 요구한다. 이전 run의 증거는 삭제하지 않는다.
- 승인 범위, acceptance 또는 정책을 실행 도중 자동으로 완화하지 않는다.

### 검증과 리뷰

- 결정적 검사, 관련 테스트, 전체 테스트와 읽기 전용 리뷰를 구분한다.
- 초기 버전의 리뷰 backend는 Codex만 사용한다.
- Claude에서 `scv-1-plan`을 실행한 경우에도 리뷰 operation은 Codex로 라우팅한다.
- finding은 위치, 심각도, 근거, 조치, 상태와 verdict를 가진 구조화된 데이터로
  저장한다.
- 수정 후에는 승인된 계획의 범위와 검증 결과를 다시 확인한다.
- `workflow-policy.json`이 `must_fix`로 분류한 finding이 남아 있으면
  `HANDOFF_READY`로 이동하지 않는다. waiver 가능한 finding만 사용자가 영향과
  후속 조치를 명시적으로 수용한 경우 승인 revision에 결속해 남길 수 있다.

### 핸드오프

- 변경 범위, 계획 대비 결과, 검증 결과, 미해결 위험, 관련 ADR과 다음 행동을
  고정된 문서 구조로 기록한다.
- handoff schema, 증거 해시, workspace checkpoint와 필요한 finding waiver가
  모두 유효하면 커널이 `HANDOFF_READY → READY`를 확정한다. 별도의 형식적 사용자
  확인은 요구하지 않지만 미해결 위험 수용은 이 전 단계에서 명시적으로 받는다.
- `READY`는 인계 가능한 논리 상태다. commit, merge, push 또는 정리를 의미하지
  않는다.

### 차단과 포기

- `BLOCKED`에는 `blocked_from`, 허용된 `resume_to`, 원인, 필요한 사용자 조치와
  검증 조건을 저장한다.
- 차단 원인이 정확히 복구되면 커널이 조건을 확인한 뒤 원래 checkpoint로
  복귀한다. 미래 단계로 건너뛰는 `resume_to`는 거부한다.
- workspace를 원래 상태로 복구할 수 없으면 사용자가 명시적 재결속을 선택할 수
  있다. 재결속은 새 checkpoint를 기록하고 `PLANNING`으로 돌아가 전제 검토와
  재승인을 요구한다.
- 복구 불가능하다는 이유만으로 자동 `ABANDONED` 처리하지 않는다. 포기는 사용자
  확인 후에만 수행한다.

## 작업 공간 정책

SCV는 작업자가 브랜치 또는 worktree를 준비한 상태에서 시작한다고 가정한다.
태스크 상태와 잠금은 해당 worktree의 `.scv/tasks`와 `.scv/runtime`에만 저장한다.
Git common directory는 저장 위치가 아니라 repository identity를 확인하기 위한
읽기 전용 값으로만 사용한다.

태스크를 만들 때 다음 고정 binding을 기록한다.

- repository identity
- 프로젝트 실제 경로
- worktree 실제 경로
- 현재 브랜치

같은 태스크는 결속된 worktree에서만 재개한다. 다른 worktree로 태스크를 옮기거나
두 worktree가 같은 `.scv/tasks/<task-id>`를 공유하는 기능은 초기 범위에서
지원하지 않는다. 서로 다른 worktree의 독립 태스크는 병렬 실행할 수 있다.

고정 binding과 별도로 마지막 검증 checkpoint를 기록한다.

- 검증 시점의 HEAD
- tracked, staged와 승인 범위의 non-ignored untracked 콘텐츠 fingerprint
- checkpoint를 만든 operation과 state revision
- 승인된 plan 및 workspace policy의 hash

`.scv/tasks/**`, `.scv/runtime/**`, `link-manifest.json`과
`setup-manifest.json`은 control-plane 자체 변경이므로 workspace 콘텐츠
fingerprint에서 제외한다. `.scv/plugin/**`, 추적 대상 config와 `.scv/docs/**`의
변경은 일반 프로젝트 변경과 동일하게 포함한다.

재개할 때 초기 작업 트리와 비교하지 않고 마지막 검증 checkpoint와 비교한다.
커널이 승인된 backend operation을 완료하고 결과를 검증한 경우에만 새
checkpoint를 기록한다. 예상하지 않은 HEAD, 브랜치 또는 콘텐츠 변경은
`BLOCKED`로 전환한다.

- 사용자가 workspace를 기존 checkpoint와 정확히 같게 복구하면 원래 state로
  재개한다.
- 복구할 수 없으면 사용자가 재결속을 명시적으로 승인할 수 있다.
- 재결속은 새 HEAD와 fingerprint를 기록하고 `PLANNING`으로 돌아가 범위, 전제,
  acceptance와 계획을 다시 승인받는다.
- SCV는 drift를 자동으로 무시하거나 새 기준선으로 승격하지 않는다.

SCV는 다음 작업을 수행하지 않는다.

- 브랜치 생성, 전환, 삭제
- worktree 생성, 전환, 채택, 삭제
- commit, merge, rebase, tag, push
- stash, reset, checkout을 통한 사용자 변경 조작

## 문서와 ADR

문서는 역할별로 관리하고 동일한 내용을 여러 위치에 중복 저장하지 않는다.

- `SYSTEM.md`: 시스템 경계, 구성요소와 주요 데이터 흐름
- `DOCS-POLICY.md`: 문서별 책임, 갱신 조건과 검토 규칙
- `CHANGES.md`: 사용자에게 의미 있는 변경
- `work-items/`: 승인된 작업 정의와 계획 이력
- `adr/`: 아키텍처 결정 기록
- `investigations/`: 의사결정에 사용된 조사와 실험 결과
- `quality/VERIFICATION.md`: 검증 계층과 표준 실행 방법
- `quality/KNOWN-GAPS.md`: 알려진 품질 공백과 개선 계획

태스크별 상태, 실행 기록과 증거는 `.scv/tasks/<task-id>`에 저장한다. 모델 원시
출력, 임시 파일과 cache는 장기 문서로 취급하지 않는다.

`tasks/<task-id>`는 재개 가능한 control-plane 원본이고 `docs/work-items/<task-id>`는
승인 또는 완료 gate에서 커널이 해시와 함께 발행한 사람용 snapshot이다. 두 위치를
동일한 mutable 문서의 공동 원본으로 사용하지 않는다.

### Git 추적 경계

다음 항목은 팀에 전파할 선언적 구성과 누적 문서이므로 Git으로 추적한다.

- `.scv/plugin/**`
- `.scv/config/project.json`, `workflow-policy.json`, `model-policy.json`
- `.scv/docs/**`
- `.scv/.gitignore`
- `.agents/skills/*`, `.claude/skills/*`의 setup 관리 상대 심볼릭 링크

다음 항목은 장비 또는 실행별 mutable 데이터이므로 `.scv/.gitignore`로 제외한다.

- `.scv/tasks/**`
- `.scv/runtime/**`
- `.scv/config/link-manifest.json`
- `.scv/config/setup-manifest.json`

clone 후 심볼릭 링크와 plugin 원본이 보존되면 Skill discovery는 가능하지만,
태스크 실행 전에 setup `validate` 또는 `init`으로 로컬 manifest, runtime 권한과
host backend를 확인해야 한다. setup은 추적된 선언 파일을 장비별 값으로
덮어쓰지 않는다.

### ADR

ADR은 병렬 branch에서도 충돌하지 않는 task 기반 ID로 누적한다.

```text
.scv/docs/adr/
├── README.md
├── ADR-<task-id>-01-<slug>.md
└── ADR-<task-id>-02-<slug>.md
```

- task ID는 커널이 충돌 방지 난수로 발급하고 뒤의 sequence는 해당 태스크 안에서
  잠금 아래 증가시킨다. 전역 순차 번호 원장을 사용하지 않는다.
- 기존 `ADR-0001-*` 형식 문서는 이름을 바꾸지 않고 그대로 보존한다.
- 기존 ID를 재사용하거나 ADR 파일을 자동 삭제하지 않는다.
- 대체 결정은 새 ADR로 추가하고 기존 결정과의 관계를 기록한다.
- 모델은 내용을 제안할 수 있지만 번호, 날짜, 상태와 파일명은 결정하지 않는다.
- 배경, 결정, 고려한 대안, 영향, 상태, 관련 태스크를 기록한다.
- `adr/README.md`는 번호 원장이 아니라 형식과 상태 정책을 설명한다. 목록은 파일을
  기준으로 결정적으로 생성할 수 있어야 한다.

## 모델 정책

모델 이름은 정적 지시문에 하드코딩하지 않는다. `.scv/config/model-policy.json`은
논리적 role, capability tier, fallback과 허용 operation의 권위 원본이다.
`hosts/codex`와 `hosts/claude` adapter는 이 role을 각 호스트가 지원하는 실제
모델 ID와 실행 옵션으로 해석한다.

초기 OpenAI capability profile은 다음 방향으로 구성한다.

- 복잡한 계획, 고위험 검증과 리뷰: Sol 계열
- 일반 계획 보조와 구현: Terra 계열
- 정형 변환, 탐색과 기계적 수정: Luna 계열

setup은 공통 policy와 host mapping이 일치하며 현재 설치된 backend가 실제 모델
ID와 옵션을 지원하는지 검증한다. 모델이 바뀌어도 operation과 산출물 계약은
유지되어야 한다.

Claude는 요청 이해, 계획, 구현, 문서화, 검증 보조와 핸드오프에 사용할 수 있다.
초기 리뷰 최종 판정만 Codex로 제한하며 Claude의 실제 모델 이름과 옵션은
`hosts/claude` mapping에서 관리한다.

### 계획 대화의 모델과 토큰 정책

- 저장소 탐색, 관련 문서 분류와 초벌 요약은 저비용 profile을 사용할 수 있다.
- 사용자 의도 재구성, 전제 반박, 대안 비교, 고위험 판단과 최종 계획 통합은
  계획용 고성능 profile을 사용한다.
- 매 대화 턴에 전체 transcript를 다시 넣지 않는다. 현재 질문에 필요한 코드와
  문서 발췌, 확정된 결정 요약, 미해결 항목만 컨텍스트로 구성한다.
- 사용자 답변 뒤에는 구조화된 대화 상태를 먼저 갱신하고, 다음 질문은 그 상태를
  기준으로 생성한다.
- 동일한 사실을 여러 reference와 prompt에 중복하지 않고, 장기 대화가 재개될
  때도 원문 대신 검증된 요약을 사용한다.
- 저비용 profile이나 Ollama가 만든 요약은 근거 위치를 보존하고 계획용 모델이
  중요한 결정에 사용하기 전에 검토한다.

### Ollama

Ollama는 기본 비활성화하고 평가를 통과한 저위험 operation에만 사용한다.

허용 후보는 다음과 같다.

- 문서 분류
- 로컬 자료 초벌 요약
- 계획 대화의 확정 사실과 답변 초벌 정규화
- 로그와 테스트 결과 정규화
- 단순한 구조 변환
- 공개 문서 문장 정리

다음 작업에는 사용하지 않는다.

- 사용자 승인 판정
- 다음 질문의 필요 여부와 계획 대화 종료에 대한 최종 판정
- 계획의 최종 위험 판단
- 보안 또는 리뷰의 최종 verdict
- 정책 자동 변경
- 최종 핸드오프 승인

setup은 Ollama 설치, 서버 상태와 보유 모델만 검사한다. 모델을 자동으로 pull,
start 또는 delete하지 않는다. 모델별 스키마 준수율, 정확도, 재시도율, 지연과
비용 절감 효과를 평가한 뒤 명시적으로 활성화한다.

## 실행 격리와 명령 안전

host 세션은 대화와 승인을 담당하고, 구현 backend와 acceptance 명령은 Python
커널이 통제하는 내부 격리 경계를 통해서만 실행한다.

- 구현 backend는 결속된 worktree와 controller가 만든 per-operation agent
  home·scratch만 쓰도록 제한하고 그 밖의 경로 쓰기를 거부한다.
- acceptance와 최종 검증 명령은 네트워크를 차단하고, controller가 만든 최소
  환경변수 allowlist만 상속한다.
- SSH, cloud, package manager, Git credential, keychain과 source backend home의
  알려진 자격증명 경로 읽기를 거부한다.
- 명령별 scratch는 macOS system temporary 영역에 `0700` 권한으로 만들고,
  worktree와 `.scv` control-plane 밖에 둔다. 쓰기는 worktree와 해당 scratch에만
  허용한다.
- 중첩 Codex 또는 Claude backend는 임시 agent home을 사용한다. 인증에 필요한
  최소 자료만 전달하고 사용자 rules, skills, plugins와 일반 설정을 상속하지
  않는다.
- sandbox 기능, 필수 flag 또는 containment를 검증할 수 없으면 fail-closed로
  `BLOCKED` 처리하며 일반 shell로 fallback하지 않는다.
- timeout, 취소 또는 출력 한도 초과 시 controller가 소유한 process group을
  종료한다. stdout과 stderr의 합산 보존 한도는 기본 8 MiB로 제한하고 policy에서
  더 넓힐 수 없게 한다.

Claude와 Codex adapter는 같은 안전 계약을 만족해야 한다. 특정 backend가 이
계약을 구현할 수 없으면 해당 backend의 구현·acceptance operation을 활성화하지
않고 다른 허용 backend로 명시적으로 fallback하거나 차단한다.

## 기존 improve와 학습 시스템

초기 재구성의 공개 Skill은 setup과 `scv-1-plan`으로 제한하지만 기존
`$scv:improve`와 학습 데이터를 암묵적으로 삭제하지 않는다.

- 기존 Git common directory의 learning store와 기존 태스크는 migration 결정
  전까지 기존 계약이 적용되는 read-only compatibility 대상으로 유지한다.
- 새 커널은 task-local 실패 증거를 저장할 수 있지만 기존 learning store에 새
  observation, candidate, proposal 또는 active lesson을 쓰거나 주입하지 않는다.
- Failure Analyst와 cross-task lesson 활성화는 별도 설계가 승인될 때까지 새
  실행 루프에서 비활성화한다. 이 비활성화는 worker 실패로 취급하지 않는다.
- 향후 학습 기능을 승계하면 controller-only 기록, secret redaction, hashed
  evidence 분리, exact-signature 주입, 명시적 활성화와 proposal-only 자기개선
  경계를 유지해야 한다.
- retain, 새 공개 Skill로 재설계, archive 후 폐기 중 하나를 별도 ADR과 migration
  계획으로 결정한다. 결정 전에는 기존 데이터를 변환하거나 삭제하지 않는다.

### 기존 데이터 migration

- setup은 legacy Git common directory 상태와 Git으로 추적된 기존
  `.scv/tasks/**`를 읽기 전용으로 탐지하고 새 layout으로 오인해 덮어쓰지 않는다.
- legacy 태스크는 기존 runtime에서 완료 또는 포기하거나, 별도 승인된 export로
  승인 문서만 `.scv/docs/work-items`에 복사한다.
- mutable state, locks, 실행 index와 learning lifecycle은 자동 변환하지 않는다.
- 모든 migration은 source layout version, 대상 version, 파일별 결과와 rollback
  가능 범위를 manifest에 기록한다.

## 안전 및 품질 기준

- macOS와 Python 3.9 이상을 지원하고 외부 Python 패키지를 추가하지 않는다.
- 모든 경로는 프로젝트 기준으로 정규화하고 containment를 검증한다.
- 상태와 manifest는 잠금 아래 원자적으로 교체한다.
- 상태, 승인, registry, schema와 증거에 버전을 둔다.
- 모델 입력에서 사용자 콘텐츠와 실행 지시를 명확히 분리한다.
- 자격증명, 환경변수 원문과 인증 정보를 산출물에 저장하지 않는다.
- task lock, worktree execution lease와 crash recovery를 실제 multi-process
  테스트로 검증한다.
- sandbox 부재, 자격증명 경로, 네트워크, 쓰기 범위와 process cleanup을
  fail-closed 테스트로 고정한다.
- host adapter와 공통 커널을 독립적으로 테스트한다.
- Claude와 Codex에서 Skill 발견, 구조화 출력, 재개와 링크 복구를 검증한다.
- 실제 모델 E2E, 외부 설치와 모델 다운로드는 명시적으로 활성화한 경우에만
  수행한다.

## 범위에서 제외하는 항목

- Claude를 리뷰 최종 판정자로 사용하는 기능
- 브랜치와 worktree 수명주기 자동화
- 자동 commit, merge, release, tag, push
- Ollama 모델 자동 설치와 다운로드
- 다른 worktree에서 같은 태스크를 직접 재개하거나 control state를 공유하는 기능
- 초기 재구성에서 cross-task 학습을 활성화하거나 기존 learning 데이터를 자동
  migration하는 기능
- 승인 없는 ADR 확정 또는 정책 변경
- 기존 파일을 덮어쓰는 자동 복구
- Linux, WSL과 Windows 지원
- 모델 원시 추론과 전체 transcript 장기 보관

## 구현 순서

1. 합법 상태 전이, task 선택, worktree binding, checkpoint, 승인, Git 추적과
   sandbox 계약을 새 versioned contract로 확정한다.
2. legacy common-directory 상태와 learning을 탐지·조회·export하는 비파괴
   compatibility 경계를 구현한다.
3. task store, process lock, worktree execution lease, crash audit와 원자적 상태
   전이를 공통 Python 커널에 구현한다.
4. setup Skill과 `status`, `init`, `repair`, `upgrade`, `validate` 커널 operation을
   구현한다.
5. `.scv/plugin` 단일 원본, 선언 파일의 Git 추적 경계와 호스트별 Skill 링크를
   구현한다.
6. 영문 `SKILL.md`와 `references/*.md`, 한글 `README.md` 및 번역 동기화 검증을
   구현한다.
7. executor가 명시된 operation registry, JSON Schema, 모델 role mapping과 문서
   렌더러를 구현한다.
8. 계획 대화 상태, 질문 계약, `PLAN_READY`, 재개와 승인 무효화 규칙을 구현한다.
9. `scv-1-plan` Skill과 host·kernel·backend operation 진행을 구현한다.
10. 기존 `execute.py`에 남아 있는 hardcoded prompt, backend dispatch, acceptance
    sandbox, 실행 index, 수렴 판정과 evidence sealing 책임을 모듈로 분리한다.
11. bounded retry, fail-closed sandbox, process cleanup과 finding waiver를 구현하고
    적대적 테스트로 고정한다.
12. 문서 snapshot, Git 추적 경계와 task 기반 ADR을 태스크 흐름에 연결한다.
13. OpenAI model profile을 적용하고 성공률, 토큰과 지연 기준선을 측정한다.
14. Claude adapter와 양쪽 호스트 E2E를 구현한다.
15. Ollama 후보를 평가하고 통과한 operation만 활성화한다.
16. 기존 계약, 사용자 문서와 테스트를 새 동작으로 전환한다.

## 완료 기준

다음 조건을 모두 만족해야 재구성이 완료된다.

1. setup이 신규 구성, 재실행, 충돌, 복구와 업그레이드를 결정적으로 처리한다.
2. Claude와 Codex가 같은 Skill 원본을 발견한다.
3. 합법 전이표가 계획 보류, 승인 무효화, 실행→계획, 검증→실행,
   핸드오프→실행과 `BLOCKED` 복귀를 테스트한다.
4. task ID 발급·선택, 복수 미완료 태스크, worktree execution lease, crash audit와
   명시적 포기 절차가 모델 판단 없이 동작한다.
5. `scv-1-plan` 하나로 승인 이후 핸드오프까지 정상 진행하고 계획까지만 요청한
   태스크는 `PLAN_READY`에서 안전하게 보류한다.
6. 계획 단계가 조사 가능한 사실을 먼저 확인하고 미해결 쟁점을 한 번에 하나씩
   질문하며 대안과 설계 구간을 사용자와 검토한다.
7. 계획 대화 중단과 재개 후에도 확정된 결정을 다시 묻지 않고 다음 미해결
   질문부터 계속한다.
8. 마지막 checkpoint 기반 재개, 예상 drift 차단, 정확 복구와 명시적 재결속 후
   재승인이 모두 검증된다.
9. attempt 예산과 `budget_exhausted`, `stalled`, `oscillating`,
   `verifier_disagreement`가 추가 모델 호출 없이 종료된다.
10. 구현과 acceptance가 네트워크·환경·자격증명·쓰기 범위가 제한된 sandbox에서
    실행되고 sandbox 부재 시 fail-closed로 차단된다.
11. operation마다 host, kernel 또는 backend executor가 명확하며 잘못된 executor
    조합은 실행 전에 거부된다.
12. 두 호스트가 동일한 schema를 만족하는 산출물을 생성한다.
13. 태스크 중단과 재개 후에도 상태, 승인과 증거의 신원이 유지된다.
14. SCV가 Git 브랜치, worktree와 이력을 변경하지 않는다는 테스트가 통과한다.
15. 추적 구성과 runtime 제외 경계가 clone, setup 재실행과 workspace fingerprint에서
    일관되게 유지된다.
16. 문서 snapshot과 task 기반 ADR이 정해진 위치와 형식으로 생성되고 병렬
    branch에서 파일 ID가 충돌하지 않는다.
17. 모델, executor, reference, schema와 산출물의 버전과 해시를 추적할 수 있다.
18. 결정적 테스트는 실제 모델 호출 없이 통과한다.
19. Claude/Codex 통합 E2E가 같은 태스크 흐름과 결과 계약을 검증한다.
20. 모든 Skill의 실행 지침은 영문이고, 한글 `README.md`가 같은 source digest와
    reference 범위를 반영하며 Skill 자체에 동시 갱신 규칙이 명시되어 있다.
21. legacy 상태와 learning은 자동 변경·삭제되지 않고 compatibility 경계 밖에서
    새 실행에 주입되지 않는다.

## 구현 전 결정할 항목

1. SCV가 없는 프로젝트에서 setup을 시작할 bootstrap 배포 방식을 정한다.
2. Claude와 Codex의 최소 지원 버전, 필수 CLI flag와 sandbox capability를 확정한다.
3. 공통 `SKILL.md` frontmatter를 두 호스트의 validator로 검증한다.
4. linked 모드와 installed 모드의 전환 및 중복 탐지 절차를 정한다.
5. Claude를 리뷰 이외의 최종 검증에 사용할 수 있는지 정한다.
6. Ollama 모델의 정량 통과 기준과 fallback 한도를 정한다.
7. ADR 상태 변경과 대체 관계를 파일 metadata와 생성 index 중 어디에서 관리할지
   정한다.
8. 기존 improve와 learning을 retain, 재설계 또는 archive 후 폐기할 최종 시점과
   migration 절차를 정한다.
9. finding severity별 `must_fix`와 사용자 waiver 허용 기준을 확정한다.
10. 완료·포기된 `.scv/tasks`와 runtime evidence의 보존 기간 및 수동 정리 명령을
    정한다.

## 참고 자료

- TRIP workflow Skills: <https://github.com/PiLastDigit/TRIP-workflow/tree/master/skills>
- TRIP 정적 prompt 사례: <https://github.com/PiLastDigit/TRIP-workflow/tree/master/skills/codex-code-review/prompts>
- gstack office-hours Skill: <https://github.com/garrytan/gstack/blob/main/office-hours/SKILL.md>
- gstack 계획 검토 Skills: <https://github.com/garrytan/gstack/tree/main/plan-ceo-review>, <https://github.com/garrytan/gstack/tree/main/plan-eng-review>
- Superpowers brainstorming Skill: <https://github.com/obra/superpowers/blob/main/skills/brainstorming/SKILL.md>
- Codex Skills: <https://learn.chatgpt.com/docs/build-skills>
- Codex Plugins: <https://learn.chatgpt.com/docs/build-plugins>
- Claude Code Skills: <https://code.claude.com/docs/en/skills>
- Claude Code Plugins: <https://code.claude.com/docs/en/plugins>
