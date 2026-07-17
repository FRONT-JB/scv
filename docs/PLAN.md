# SCV 크로스 호스트 플러그인 재구성 계획

> **이 저장소는 SCV가 설치되는 프로젝트가 아니라 SCV 플러그인을 만드는 소스
> 저장소다.**
>
> 저장소의 구현 원본은 `plugins/scv/**`에 둔다. `.scv/**`, `.agents/skills/**`,
> `.claude/skills/**`는 이 플러그인의 `setup`을 실행한 소비 프로젝트에만 생성한다.
> 이 저장소의 E2E 테스트는 임시 디렉터리에 소비 프로젝트를 만들며, 저장소 루트에
> `.scv`를 생성하면 실패로 간주한다.

## 1. 계획의 기준

- [GOAL.md](GOAL.md)는 플러그인을 사용한 프로젝트의 목표 동작과 설치 결과를
  정의한다.
- 이 PLAN은 그 결과를 만드는 배포 가능한 플러그인 소스, setup 과정과 검증 순서를
  정의한다.
- 기존 구현은 첫 태스크에서 제거하고 복사, rename 또는 import하지 않는다. 필요한
  legacy 형식은 fixture와 read-only compatibility 계약으로 새로 기술한다.
- SCV는 사용자가 준비한 현재 branch/worktree에서만 동작한다. branch, worktree,
  commit, merge, rebase, stash, checkout, reset, tag와 push를 실행하지 않는다.
- 아래의 태스크별 commit과 push는 플러그인 runtime 동작이 아니라 이 소스 저장소를
  구현하는 작업자의 release 절차다.
- 구현 태스크 하나가 acceptance를 통과하면 해당 태스크 변경만 별도 Conventional
  Commit으로 기록한다. 커밋 전에는 다음 태스크를 `done`으로 표시하지 않는다.
- 태스크 acceptance와 독립 커밋이 끝나면 즉시 현재 branch를 원격으로 push한다.
  push 성공과 원격 commit 일치를 확인하기 전에는 다음 태스크를 시작하지 않는다.
- Skill 실행 지침과 `references/*.md`는 영문으로 작성한다. 각 Skill의 한글
  `README.md`, source digest와 Maintenance 규칙을 같은 태스크에서 검증한다.
- 실제 Codex·Claude 모델 호출과 Ollama 평가는 명시적 opt-in test에서만 실행한다.
  기본 CI는 deterministic fake backend를 사용한다.

## 2. 소스 저장소와 소비 프로젝트의 경계

### 2.1 이 저장소에 만들 플러그인 원본

```text
scv/
├── plugins/
│   └── scv/
│       ├── .codex-plugin/
│       │   └── plugin.json
│       ├── .claude-plugin/
│       │   └── plugin.json
│       ├── skills/
│       │   ├── setup/
│       │   │   ├── SKILL.md
│       │   │   ├── README.md
│       │   │   ├── agents/
│       │   │   │   └── openai.yaml
│       │   │   └── references/
│       │   └── scv-1-plan/
│       │       ├── SKILL.md
│       │       ├── README.md
│       │       ├── agents/
│       │       │   └── openai.yaml
│       │       └── references/
│       ├── scripts/
│       │   ├── scv.py
│       │   └── core/
│       ├── schemas/
│       ├── registry/
│       ├── hosts/
│       │   ├── codex/
│       │   └── claude/
│       └── assets/
│           ├── project-template/
│           │   ├── config/
│           │   ├── docs/
│           │   └── scv.gitignore
│           └── document-templates/
├── .agents/
│   └── plugins/
│       └── marketplace.json
├── .claude-plugin/
│   └── marketplace.json
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── e2e/
│   └── fixtures/
├── docs/
│   ├── GOAL.md
│   └── PLAN.md
├── .github/
│   └── workflows/
└── README.md
```

`plugins/scv`는 두 host가 설치하거나 개발 모드로 직접 읽는 self-contained plugin
root다. plugin identity는 `scv`이며 source directory와 manifest identity를
일치시킨다. 소비 프로젝트용 config와 문서는 저장소 루트의 `.scv`가 아니라
`plugins/scv/assets/project-template`에서 관리한다.

### 2.2 setup이 소비 프로젝트에 생성할 결과

```text
{{consumer-project}}/
├── .scv/
│   ├── plugin/                  # 검증된 plugins/scv bundle의 project-local 사본
│   │   ├── .codex-plugin/
│   │   ├── .claude-plugin/
│   │   ├── skills/
│   │   ├── scripts/
│   │   ├── schemas/
│   │   ├── registry/
│   │   ├── hosts/
│   │   └── assets/
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
│   │   ├── adr/
│   │   ├── investigations/
│   │   └── quality/
│   ├── tasks/
│   ├── runtime/
│   └── .gitignore
├── .agents/
│   └── skills/
│       ├── setup -> ../../.scv/plugin/skills/setup
│       └── scv-1-plan -> ../../.scv/plugin/skills/scv-1-plan
└── .claude/
    └── skills/
        ├── setup -> ../../.scv/plugin/skills/setup
        └── scv-1-plan -> ../../.scv/plugin/skills/scv-1-plan
```

소비 프로젝트의 `.scv/plugin`은 setup manifest가 소유하는 배포 결과다. source
bundle 전체의 digest를 기록하고, upgrade는 사용자가 수정하지 않은 소유 파일만
새 release로 교체한다. `.scv/tasks`, `.scv/runtime`, 장비별 manifest는 소비
프로젝트에서 Git ignore하고 plugin·선언 config·누적 docs·Skill link는 추적한다.

### 2.3 bootstrap과 활성화 전환

1. 사용자는 Codex marketplace 또는 Claude marketplace/`--plugin-dir`로 이 저장소의
   `plugins/scv` bundle을 로드한다.
2. bootstrap plugin의 `setup` Skill이 현재 working directory를 소비 프로젝트로
   간주하고 read-only preflight를 수행한다.
3. setup은 생성·유지·충돌·교체 경로와 bundle digest를 보여주고 명시적 승인을
   받는다.
4. 승인 후 `.scv/plugin`, project template과 Skill별 상대 링크를 원자적으로
   materialize한다.
5. bootstrap source와 project-local link가 동시에 노출되는 동안에는
   `cutover_pending`으로 보고한다. setup은 host plugin을 자동 uninstall하거나 host
   설정을 임의 변경하지 않는다.
6. 사용자가 bootstrap source를 비활성화하거나 다음 session을 project-local linked
   mode로 시작한 뒤 `setup validate`를 실행한다.
7. 단일 source digest와 양쪽 Skill discovery가 확인되어야 `scv-1-plan`을 사용할 수
   있다.

installed mode를 계속 사용할 경우에도 project state와 docs는 `.scv`에 생성하지만
project Skill links는 활성화하지 않는다. linked와 installed mode를 동시에 READY로
판정하지 않는다.

## 3. 태스크 운영 규칙

이 문서의 `BUILD-xxx`는 플러그인 구현 작업을 식별하는 계획 ID다. 플러그인이
소비 프로젝트에서 발급하는 runtime task ID나 ADR ID가 아니며 runtime schema로
검증하지 않는다.

태스크 상태는 다음 값을 사용한다.

- `pending`: 아직 시작하지 않았거나 dependency 대기
- `in_progress`: 해당 태스크의 파일만 변경 중
- `blocked`: 사용자 결정 또는 외부 capability가 반드시 필요함
- `done`: acceptance 통과 후 독립 커밋까지 완료

각 태스크는 시작 전에 소유 경로, 비범위, 검증 명령과 rollback 범위를 고정한다.
다른 태스크의 미완료 변경을 acceptance 근거로 사용하지 않는다.

## 4. 의존성 및 진행표

```text
BUILD-000 → BUILD-010 → BUILD-020
BUILD-020 → {BUILD-030, BUILD-040}
{BUILD-030, BUILD-040} → BUILD-050
{BUILD-030, BUILD-050} → {BUILD-060, BUILD-070, BUILD-100}
{BUILD-050, BUILD-060, BUILD-070} → BUILD-080
{BUILD-060, BUILD-080} → BUILD-090
{BUILD-050, BUILD-100} → BUILD-110
{BUILD-060, BUILD-090, BUILD-100, BUILD-110} → BUILD-120
{BUILD-030, BUILD-050, BUILD-060} → BUILD-130
{BUILD-100, BUILD-110, BUILD-120, BUILD-130} → BUILD-140
{BUILD-060, BUILD-110, BUILD-130, BUILD-140} → BUILD-150
{BUILD-050, BUILD-100, BUILD-150} → BUILD-160
{BUILD-090, BUILD-120, BUILD-150, BUILD-160} → BUILD-170
BUILD-170 → BUILD-180 → BUILD-190 → BUILD-200
```

| ID | 상태 | 태스크 | blockedBy | 커밋 결과 |
|---|---|---|---|---|
| BUILD-000 | done | 기존 구현 clean reset | 없음 | legacy source 제거 |
| BUILD-010 | done | source/target 및 안전 계약 확정 | BUILD-000 | 수정된 GOAL·계약 결정 |
| BUILD-020 | pending | cross-host plugin package scaffold | BUILD-010 | 설치 가능한 plugin root |
| BUILD-030 | pending | 소비 프로젝트 template·Schema·Policy | BUILD-020 | setup payload 계약 |
| BUILD-040 | pending | plugin source test·CI bootstrap | BUILD-020 | 임시 소비 프로젝트 harness |
| BUILD-050 | pending | 안전한 공통 Python primitive | BUILD-030, BUILD-040 | 경로·원자성·digest·validator |
| BUILD-060 | pending | operation registry·정적 reference·번역 digest | BUILD-030, BUILD-050 | 결정적 지시문 엔진 |
| BUILD-070 | pending | legacy read-only compatibility | BUILD-030, BUILD-050 | 비파괴 탐지·export |
| BUILD-080 | pending | setup kernel | BUILD-050, BUILD-060, BUILD-070 | 5개 setup operation |
| BUILD-090 | pending | setup Skill·host cutover·E2E | BUILD-060, BUILD-080 | 소비 프로젝트 설치 가능 |
| BUILD-100 | pending | runtime task state·lock·lease | BUILD-030, BUILD-050 | 재개 가능한 state kernel |
| BUILD-110 | pending | workspace binding·checkpoint | BUILD-050, BUILD-100 | drift 차단·재기준 설정 |
| BUILD-120 | pending | `scv-1-plan` 계획 대화 | BUILD-060, BUILD-090, BUILD-100, BUILD-110 | PLAN_READY 흐름 |
| BUILD-130 | pending | backend adapter·sandbox process | BUILD-030, BUILD-050, BUILD-060 | fail-closed 실행 경계 |
| BUILD-140 | pending | 구현 loop·attempt·수렴 판정 | BUILD-100, BUILD-110, BUILD-120, BUILD-130 | bounded execution |
| BUILD-150 | pending | 검증·Codex review·handoff | BUILD-060, BUILD-110, BUILD-130, BUILD-140 | READY gate |
| BUILD-160 | pending | project docs snapshot·ADR | BUILD-050, BUILD-100, BUILD-150 | 누적 문서 시스템 |
| BUILD-170 | pending | Codex full-flow integration | BUILD-090, BUILD-120, BUILD-150, BUILD-160 | Codex E2E |
| BUILD-180 | pending | Claude adapter·cross-host integration | BUILD-170 | Claude/Codex 동일 계약 |
| BUILD-190 | pending | 모델 routing·토큰·Ollama 평가 | BUILD-180 | 측정된 model policy |
| BUILD-200 | pending | release package·문서·최종 CI | BUILD-190 | 배포·push 가능 후보 |

## 5. 태스크 상세

### BUILD-000 — 기존 구현 clean reset

**목적**

기존 Codex 전용 구현을 제거하고 플러그인 소스 저장소의 clean slate를 만든다.

**삭제 대상**

- `plugins/codex/scv/**`
- 기존 `.agents/plugins/marketplace.json`
- 기존 `.github/workflows/codex-ci.yml`
- 기존 동작만 설명하는 root `README.md`와 banner

**보존 대상**

- `.git`, `AGENTS.md`, `.gitignore`, 승인된 `docs/GOAL.md`, `docs/PLAN.md`
- Git common directory의 legacy task·learning data
- 삭제 manifest 밖의 사용자 파일

**Acceptance**

- 삭제 전 dirty-file audit와 보존 checksum을 기록한다.
- 승인 manifest 밖의 파일을 삭제하지 않는다.
- 기존 `execute.py`, `workflow`, `improve` 실행 경로가 0건이다.
- 저장소 root에 `.scv`를 만들지 않는다.
- reset diff만 포함한 독립 커밋을 만든다.

**완료 기록 (2026-07-17)**

- 삭제 대상의 dirty tracked file은 없었다.
- 보존 체크섬: `AGENTS.md` `d8701806…`, `.gitignore` `90aa8dfd…`,
  `docs/GOAL.md` `094bc176…`, `docs/PLAN.md` `27636e6b…`.
- 승인된 tracked manifest 29개만 삭제했고 Git common directory는 변경하지 않았다.

### BUILD-010 — source/target 및 안전 계약 확정

**목적**

GOAL이 설명하는 소비 프로젝트 결과와 이 저장소의 plugin source를 명확히 분리하고
구현 전 결정을 확정한다.

**소유 경로**

- `docs/GOAL.md`, `docs/PLAN.md`
- `docs/decisions/**`

**필수 결정**

- source root `plugins/scv`와 manifest identity `scv`
- Codex marketplace 및 Claude marketplace/`--plugin-dir` bootstrap
- linked/installed cutover와 중복 Skill 처리
- shared Skill frontmatter 교집합과 validator fixture
- host별 정확한 invocation flag와 capability probe
- immutable repository/worktree binding과 mutable HEAD checkpoint 분리
- `BLOCKED` 상태별 명시적 safe resume map
- controller의 provider egress와 model tool/acceptance network deny 분리
- 환경변수 allowlist, credential deny path class와 임시 auth 전달 방식
- Ollama threshold, finding waiver, ADR metadata, legacy learning과 retention
- `stash`, `checkout`을 포함한 Git mutation 전체 금지
- Git-tracked legacy `.scv/tasks/**` 탐지 경계

**Acceptance**

- GOAL의 구현 전 결정 항목마다 `resolved`, 근거와 결정 문서 링크가 있다.
- source tree와 consumer target tree가 서로 다른 경로로 명시된다.
- 계획용 `BUILD-xxx`와 runtime task ID가 구분된다.
- 서로 모순되는 binding, resume, sandbox 또는 activation 문장이 없다.
- 문서만 포함한 독립 커밋을 만든다.

**완료 기록 (2026-07-17)**

- `plugins/scv` source package와 소비 프로젝트 `.scv/plugin` 배포본을 분리했다.
- bootstrap/cutover, host invocation, state/workspace, sandbox/auth와 quality/data
  lifecycle을 `docs/decisions/D001`~`D005`로 모두 `Resolved` 처리했다.
- Codex `0.144.5`, Claude Code `2.1.204`, Claude project Skill symlink 최소
  `2.1.203`과 두 host의 필수 flag를 공식 문서 및 로컬 capability probe로 확인했다.

### BUILD-020 — cross-host plugin package scaffold

**목적**

Codex와 Claude가 같은 `plugins/scv` package를 읽을 수 있는 최소 배포 골격을 만든다.

**소유 경로**

- `plugins/scv/.codex-plugin/plugin.json`
- `plugins/scv/.claude-plugin/plugin.json`
- `plugins/scv/hosts/{codex,claude}/capabilities.json`
- `.agents/plugins/marketplace.json`
- `.claude-plugin/marketplace.json`

**Acceptance**

- 두 manifest의 identity와 version이 일치한다.
- Codex 공식 validator와 `claude plugin validate --strict`가 통과한다.
- marketplace source가 `plugins/scv`만 가리킨다.
- package 밖 절대 경로와 symlink 의존성이 없다.
- 아직 setup payload나 runtime code를 만들지 않는다.

### BUILD-030 — 소비 프로젝트 template·Schema·Policy

**목적**

setup이 소비 프로젝트에 materialize할 선언 파일과 모든 runtime artifact schema를
plugin assets로 만든다.

**소유 경로**

- `plugins/scv/assets/project-template/**`
- `plugins/scv/schemas/**`
- `tests/fixtures/schemas/**`

**요구사항**

- state, request, planning dialogue, plan, approval, execution, event, finding,
  evidence, handoff, setup/link manifest, registry와 model mapping schema를 정의한다.
- project template에는 portable config와 문서 초기본만 두며 absolute path, 장비 ID,
  자격증명을 넣지 않는다.
- runtime task와 manifest는 target `.scv/.gitignore`에서 제외한다.

**Acceptance**

- 정상·경계·오염 fixture가 모든 schema에 존재한다.
- unknown field, version mismatch, 잘못된 상태·attempt·경로를 거부한다.
- template를 임시 소비 프로젝트에 렌더링해도 source 저장소에 `.scv`가 생기지 않는다.

### BUILD-040 — plugin source test·CI bootstrap

**목적**

플러그인 자체를 검증하는 Python 3.9 stdlib test harness와 새 CI를 만든다.

**소유 경로**

- `tests/**`
- `.github/workflows/scv-ci.yml`

**Acceptance**

- unit, integration, e2e와 fixture root가 분리된다.
- E2E는 `tempfile.TemporaryDirectory`의 소비 프로젝트만 변경한다.
- source root `.scv` 생성 방지 test가 있다.
- 기본 CI는 compile, JSON, manifest, schema와 deterministic test만 수행한다.
- live model, network install과 Ollama pull을 실행하지 않는다.

### BUILD-050 — 안전한 공통 Python primitive

**목적**

모든 kernel이 공유하는 containment, 원자적 파일, digest, schema와 렌더링 기반을
구현한다.

**소유 경로**

- `plugins/scv/scripts/core/paths.py`
- `plugins/scv/scripts/core/atomic.py`
- `plugins/scv/scripts/core/digests.py`
- `plugins/scv/scripts/core/schema.py`
- `plugins/scv/scripts/core/rendering.py`
- 관련 `tests/unit/**`

**Acceptance**

- traversal, symlink race, 비정상 file type, oversized input과 root escape를 거부한다.
- owner-only directory, fsync와 atomic replace를 검증한다.
- canonical JSON, source bundle, evidence와 template digest가 결정적이다.
- Python 3.9에서 외부 package 없이 통과한다.

### BUILD-060 — operation registry·정적 reference·번역 digest

**목적**

operation, executor, reference, schema, model role과 artifact를 allowlist로 결속한다.

**소유 경로**

- `plugins/scv/registry/**`
- `plugins/scv/scripts/core/registry.py`
- `plugins/scv/scripts/core/prompts.py`
- `plugins/scv/scripts/core/translation.py`
- `plugins/scv/skills/*/references/*.md` 중 공통 계약 파일
- 관련 tests

**Acceptance**

- 등록되지 않은 reference, executor mismatch, unknown/missing variable를 차단한다.
- 사용자 content와 instruction을 분리한다.
- `SKILL.md`와 정렬된 references의 file별·bundle digest를 검증한다.
- README 누락, stale digest와 Maintenance 섹션 부재가 실패한다.
- README는 runtime prompt에 포함되지 않는다.

### BUILD-070 — legacy read-only compatibility

**목적**

기존 구현을 복원하지 않고 legacy 상태·learning과 Git-tracked `.scv/tasks`를
read-only로 탐지한다.

**소유 경로**

- `plugins/scv/scripts/core/legacy.py`
- `tests/unit/test_legacy.py`
- `tests/fixtures/legacy/**`

**Acceptance**

- common-directory data와 tracked legacy target을 새 layout으로 오인하지 않는다.
- mutable state, lock, execution index와 learning을 변환·삭제·주입하지 않는다.
- 승인 문서 export만 provenance와 함께 허용한다.
- fixture source의 전후 byte hash가 같다.

### BUILD-080 — setup kernel

**목적**

plugin bundle을 소비 프로젝트 구조로 비파괴 materialize하는 결정적 kernel을
구현한다.

**소유 경로**

- `plugins/scv/scripts/scv.py`
- `plugins/scv/scripts/core/setup.py`
- `plugins/scv/scripts/core/manifests.py`
- 관련 unit/integration tests

**요구사항**

- `status`, `init`, `repair`, `upgrade`, `validate`를 구분한다.
- source plugin root는 읽기 전용이고 consumer root만 명시적 승인 후 변경한다.
- copy plan은 source digest, create/keep/conflict/replace와 rollback 범위를 포함한다.
- setup 소유권을 증명하지 못하는 path를 덮어쓰지 않는다.

**Acceptance**

- 신규, 재실행, 충돌, partial failure, repair와 version upgrade가 통과한다.
- 실패 rollback은 이번 operation이 만든 target만 제거한다.
- status/validate와 dry-run은 byte-level read-only다.
- source 저장소를 consumer root로 잘못 선택하면 차단한다.

### BUILD-090 — setup Skill·host cutover·E2E

**목적**

설치된 bootstrap plugin에서 setup을 호출해 임시 소비 프로젝트를 linked 또는
installed mode로 구성한다.

**소유 경로**

- `plugins/scv/skills/setup/**`
- `plugins/scv/hosts/{codex,claude}/setup/**`
- setup integration/E2E tests

**Acceptance**

- 영문 `SKILL.md`·references와 한글 README/digest가 동기화된다.
- linked mode가 target `.scv/plugin`과 Skill별 상대 링크를 만든다.
- installed mode는 project links를 활성화하지 않는다.
- bootstrap/project-local 중복은 `cutover_pending`이며 READY로 오인하지 않는다.
- host 설정이나 plugin을 자동 install/uninstall하지 않는다.
- Codex와 Claude의 project Skill discovery를 각각 검증한다.

### BUILD-100 — runtime task state·lock·lease

**목적**

소비 프로젝트 `.scv/tasks`와 `.scv/runtime`에만 저장되는 상태 엔진을 구현한다.

**소유 경로**

- `plugins/scv/scripts/core/state.py`
- `plugins/scv/scripts/core/tasks.py`
- `plugins/scv/scripts/core/locks.py`
- 관련 tests

**Acceptance**

- kernel runtime task ID, 신규/재개 선택과 명시적 abandon이 동작한다.
- 전체 합법·불법 전이, PLAN_READY와 명시적 safe resume map을 테스트한다.
- task process lock, worktree execution lease, stale file과 crash audit를 실제
  multi-process test로 검증한다.
- BUILD plan ID를 runtime task ID로 허용하지 않는다.

### BUILD-110 — workspace binding·checkpoint

**목적**

고정 worktree identity와 변경 가능한 검증 checkpoint를 분리해 재개 drift를
판정한다.

**소유 경로**

- `plugins/scv/scripts/core/workspace.py`
- 관련 tests

**Acceptance**

- immutable binding에는 repository/common-dir/worktree/branch identity를 기록하고
  HEAD·content는 checkpoint와 rebaseline history에 둔다.
- target `.scv/tasks`, runtime과 local manifest만 fingerprint에서 제외한다.
- 정상 backend 변경, 외부 drift, HEAD 변경, 정확 복구와 같은 binding의 명시적
  재기준 설정을 구분한다.
- Git mutation command 전체가 차단된다.

### BUILD-120 — `scv-1-plan` 계획 대화

**목적**

소비 프로젝트 조사부터 exact plan approval과 PLAN_READY까지의 단일 대화 진입점을
구현한다.

**소유 경로**

- `plugins/scv/skills/scv-1-plan/SKILL.md`
- 계획 관련 `references/*.md`
- `plugins/scv/scripts/core/planning.py`
- 관련 tests

**Acceptance**

- 저장소 사실을 먼저 조사하고 사용자에게 한 번에 하나의 주된 결정을 질문한다.
- 대안·추천·설계 구간 확인과 모호한 답변 후속 질문을 지원한다.
- transcript 대신 fact/decision/assumption/open-question state로 재개한다.
- exact dialogue/plan/checkpoint hash 승인만 PLAN_READY로 이동한다.
- 계획까지만 요청한 경우 구현을 시작하지 않는다.
- 전체 transcript 재주입 없이 필요한 근거만 사용한다.

### BUILD-130 — backend adapter·sandbox process

**목적**

Codex·Claude·qualified Ollama backend와 acceptance command를 분리된 안전 경계에서
실행한다.

**소유 경로**

- `plugins/scv/scripts/core/backends.py`
- `plugins/scv/scripts/core/processes.py`
- `plugins/scv/scripts/core/sandbox.py`
- `plugins/scv/hosts/{codex,claude}/runtime/**`
- 관련 tests

**Acceptance**

- controller의 provider API egress와 model/tool·acceptance network deny를 별도
  policy로 강제한다.
- 정확한 env allowlist, credential deny paths, temporary agent home과 최소 auth
  material을 검증한다.
- provider command의 cwd, model, structured output, bare/ignore-user-config flag를
  capability probe로 확정한다.
- timeout, cancel, output 8 MiB와 background child를 process group 단위로 정리한다.
- sandbox capability가 없으면 ordinary shell fallback 없이 차단한다.

### BUILD-140 — 구현 loop·attempt·수렴 판정

**목적**

승인된 plan만 bound consumer worktree에서 backend로 실행하고 제한된 attempt 안에서
수렴시킨다.

**소유 경로**

- `plugins/scv/scripts/core/executor.py`
- `plugins/scv/scripts/core/attempts.py`
- `plugins/scv/scripts/core/convergence.py`
- 구현 관련 references와 tests

**Acceptance**

- host 대화 session이 직접 구현하지 않는다.
- attempt 1~3, 기본 2와 consuming/non-consuming outcome을 구분한다.
- `budget_exhausted`, `stalled`, `oscillating`, `verifier_disagreement`가 추가 호출 없이
  PLANNING으로 돌아간다.
- execution index, crash evidence와 이전 run을 덮어쓰지 않는다.

### BUILD-150 — 검증·Codex review·handoff

**목적**

결정적 acceptance와 read-only Codex review를 수행하고 READY gate를 확정한다.

**소유 경로**

- `plugins/scv/scripts/core/verification.py`
- `plugins/scv/scripts/core/findings.py`
- `plugins/scv/scripts/core/handoff.py`
- 검증·review·handoff references와 tests

**Acceptance**

- Claude에서 시작해도 final review는 Codex로 라우팅한다.
- must-fix와 hash-bound waiver를 정책대로 판정한다.
- 수정은 EXECUTING, 계획 변경은 PLANNING으로 돌아간다.
- handoff schema, evidence, checkpoint와 waiver가 유효할 때만 READY가 된다.
- READY는 commit, merge, push 또는 cleanup을 실행하지 않는다.

### BUILD-160 — project docs snapshot·ADR

**목적**

소비 프로젝트의 runtime state와 Git-tracked snapshot·ADR을 분리해 누적한다.

**소유 경로**

- `plugins/scv/scripts/core/documents.py`
- `plugins/scv/scripts/core/adr.py`
- `plugins/scv/assets/document-templates/**`
- 관련 tests

**Acceptance**

- 승인 request/plan과 완료 handoff를 target `.scv/docs/work-items`에 렌더링한다.
- runtime artifact와 snapshot의 revision/hash를 검증한다.
- ADR은 runtime task ID + task-local sequence로 발급해 병렬 branch 충돌을 피한다.
- 모델이 ID, 상태, 날짜와 filename을 결정하지 않는다.
- 기존 순차 ADR을 rename/delete하지 않고 supersedes 관계만 추가한다.

### BUILD-170 — Codex full-flow integration

**목적**

Codex에서 plugin install→setup→project-local workflow 전체를 검증한다.

**Acceptance**

- Codex marketplace 설치 또는 격리된 local test source를 사용한다.
- setup, cutover, plan-only, full execution, retry, review, handoff와 resume가 통과한다.
- deterministic fake backend E2E가 기본이며 live Codex smoke는 opt-in이다.
- source repository root에 `.scv`가 생기지 않는다.

### BUILD-180 — Claude adapter·cross-host integration

**목적**

Claude가 같은 plugin source와 target state/schema를 사용하도록 통합한다.

**Acceptance**

- Claude marketplace/`--plugin-dir` bootstrap과 project Skill symlink discovery가
  통과한다.
- 두 host가 같은 fixture에서 의미가 같은 artifact를 만든다.
- Claude implementation·verification 결과를 Codex final review에 연결한다.
- host-specific config가 shared Skill 또는 policy를 복제하지 않는다.

### BUILD-190 — 모델 routing·토큰·Ollama 평가

**목적**

계획, 구현, 검증, review와 handoff에 logical model role을 배정하고 token 사용량을
측정한다.

**Acceptance**

- 실제 model ID는 host adapter에만 있고 reference에는 없다.
- 저비용 분류·요약과 고위험 계획·review의 role이 분리된다.
- 성공률, schema 준수율, retry, token, 지연과 비용 기준선을 같은 fixture로 비교한다.
- Ollama는 저위험 후보만 read-only discovery 후 opt-in 평가한다.
- 기준 미달 시 disabled로 완료하며 pull/start/delete하지 않는다.
- 승인, 계획 종료, 보안·review verdict와 READY gate를 Ollama에 배정하지 않는다.

### BUILD-200 — release package·문서·최종 CI

**목적**

새 plugin을 설치 가능한 release 후보로 만들고 GOAL 전체 완료 기준을 감사한다.

**소유 경로**

- root `README.md`
- release metadata와 marketplace version
- `.github/workflows/**`
- `docs/GOAL.md`, `docs/PLAN.md`

**Acceptance**

- source archive가 self-contained이고 두 host manifest validation을 통과한다.
- 신규 소비 프로젝트 설치, 재실행, repair, upgrade와 clone 후 validate를 문서화한다.
- GOAL 완료 기준 21개마다 test/evidence가 연결된다.
- legacy 실행 경로와 source-root `.scv`가 0건이다.
- 전체 deterministic suite와 opt-in host smoke 결과를 기록한다.
- BUILD-200 커밋을 push한 뒤 원격 branch가 로컬 HEAD와 같은지 확인한다.

## 6. GOAL 완료 기준 매핑

| GOAL 기준 | 담당 태스크 |
|---|---|
| C01 setup 신규·재실행·복구·upgrade | BUILD-080, BUILD-090 |
| C02 두 host의 같은 Skill 원본 | BUILD-020, BUILD-090, BUILD-180 |
| C03 합법 상태 전이 | BUILD-100, BUILD-140, BUILD-150 |
| C04 task 선택·lease·crash·abandon | BUILD-100 |
| C05 단일 `scv-1-plan`과 PLAN_READY | BUILD-120~BUILD-180 |
| C06 조사·단일 쟁점·대안 | BUILD-120 |
| C07 계획 대화 재개 | BUILD-120 |
| C08 checkpoint·drift·재기준 설정 | BUILD-110 |
| C09 attempt·수렴 종료 | BUILD-140 |
| C10 fail-closed sandbox | BUILD-130~BUILD-150 |
| C11 executor 검증 | BUILD-060, BUILD-130 |
| C12 cross-host schema | BUILD-030, BUILD-180 |
| C13 상태·승인·evidence identity | BUILD-100~BUILD-160 |
| C14 Git lifecycle 비변경 | BUILD-010, BUILD-110, BUILD-170, BUILD-180 |
| C15 추적/runtime 경계 | BUILD-030, BUILD-080, BUILD-090 |
| C16 snapshot·task ADR | BUILD-160 |
| C17 version·hash 추적 | BUILD-030, BUILD-050, BUILD-060, BUILD-140 |
| C18 모델 없는 deterministic tests | BUILD-040~BUILD-200 |
| C19 Claude/Codex E2E | BUILD-170, BUILD-180 |
| C20 영문 지침·한글 README·digest | BUILD-060, BUILD-090, BUILD-120 |
| C21 legacy 비변경·비주입 | BUILD-070, BUILD-080 |

## 7. 단계 Gate

### Gate 0 — Source reset

- BUILD-000 완료 및 독립 커밋
- legacy source 부재, GOAL·PLAN 보존
- source repository root `.scv` 부재

### Gate 1 — Installable plugin skeleton

- BUILD-010~BUILD-040 완료 및 태스크별 커밋
- 두 host manifest·marketplace validation
- source/target 혼동 방지 test 통과

### Gate 2 — Safe setup

- BUILD-050~BUILD-090 완료 및 태스크별 커밋
- 임시 소비 프로젝트에서 setup 5개 operation 통과
- source bundle→target `.scv/plugin` copy와 linked cutover 검증

### Gate 3 — Usable workflow

- BUILD-100~BUILD-160 완료 및 태스크별 커밋
- PLAN_READY, execution, retry, review, docs와 READY 흐름 통과
- sandbox·state·workspace 적대적 test 통과

### Gate 4 — Cross-host and model policy

- BUILD-170~BUILD-190 완료 및 태스크별 커밋
- Codex/Claude 동일 계약과 Codex final review route 검증
- Ollama는 qualified operation만 enabled 또는 전체 disabled

### Gate 5 — Release and remote sync

- BUILD-200 완료 및 독립 커밋
- GOAL C01~C21 evidence audit 통과
- working tree clean, commit 단위와 원격 차이 검토
- BUILD-200 push 완료와 `origin/main`·로컬 HEAD 일치

## 8. 커밋 규칙

- BUILD 태스크 하나당 최소 하나, 원칙적으로 하나의 독립 커밋을 만든다.
- 태스크 acceptance가 실패한 상태에서는 커밋하지 않는다.
- 다른 BUILD 태스크의 파일을 같은 커밋에 섞지 않는다.
- PLAN 상태 변경은 해당 태스크 커밋에 포함한다.
- 커밋 메시지는 저장소 규칙에 따라 `type(scope): 한국어 요약` 형식을 사용한다.
- 각 BUILD 태스크 커밋은 acceptance 직후 push하고 원격 반영을 확인한다.
