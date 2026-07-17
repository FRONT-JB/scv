# SCV

![SCV construction utility units cycling through Scope, Construct, Verify, and Improve](docs/assets/scv-readme-banner.png)

> **S**cope → **C**onstruct → **V**erify. SCV good to go, sir.

SCV는 요구사항을 계획하고, 격리된 작업 공간에서 구현하고, 검증 결과를
다음 실행에 반영하는 재개 가능한 Codex 소프트웨어 변경 워크플로다. 명시적인
승인 게이트와 재현 가능한 실행 증거를 유지하면서 분석부터 인계까지 한 태스크로
연결한다.

## SCV 루프

```text
Scope      요구사항 조사 → 스펙 작성 → 스펙 승인 → 구현 계획 → 계획 승인
   ↓
Construct  소스 기준 재확인 → 승인 문서 커밋 → 격리 worktree 생성 → 구현과 인수 검사
   ↓
Verify     전체 인수 조건 → 읽기 전용 최종 검증 → 실행 증거 고정 → 인계
   ↺
Improve    실패 분석 → 제한된 재시도 → lesson 후보/개선 제안 → 사람 승인
```

## 상태별 SCV 대사

제어기와 실행기의 JSON 출력에는 한글 `state_label`·`stage_label`과 현재 상태를
설명하는 `scv_line`이 함께 표시된다. 사용자에게는 한글 라벨을 표시하고,
자동화에서는 기존 `state`, `stage`, `status`, 종료 코드를 계속 기준으로 사용한다.
라벨과 `scv_line`은 상태 파일에 저장하지 않는다.

| `state` | `state_label` | `scv_line` |
| --- | --- | --- |
| `NEW` | 태스크 초기화 | `Reportin' for duty.` |
| `INTAKING` | 요구사항 접수 | `I read you.` |
| `AWAITING_SPEC_APPROVAL` | 스펙 승인 대기 | `Orders, Cap'n?` |
| `PLANNING` | 구현 계획 작성 | `SCV good to go, sir.` |
| `AWAITING_PLAN_APPROVAL` | 계획 승인 대기 | `Yes sir?` |
| `BASE_REVALIDATION` | 승인 기준 재확인 | `Affirmative.` |
| `MATERIALIZING_WORKTREE` | 격리 워크트리 생성 | `Right away sir.` |
| `EXECUTING` | 실행 중 | `Orders received.` |
| `HANDOFF` | 검증 결과 인계 | `Roger that.` |
| `READY` | 인계 준비 완료 | `Job's finished.` |
| `BLOCKED` | 차단됨 | `I can't build there.` |
| `ABANDONED` | 포기됨 | `I'm not readin' you clearly.` |

문구는 원작 [StarCraft SCV 음성 대사](https://starcraft.fandom.com/wiki/StarCraft_unit_quotations#SCV)를 사용한다.

실행 중 메인 세션은 장기 `execute` 프로세스를 그대로 유지하면서 별도 `status`
호출로 15–30초마다 변경된 `execution_progress`만 확인한다. 두 번째 `execute`를
시작하지 않으며 다음처럼 보고한다.

```text
실행 중 — "Orders received." — 단계 1/2, 단계 구현, 1차 시도.
실행 중 — "Come again, Cap'n?" — 단계 1/2, 실패 증거 분석, 1차 시도.
실행 중 — "SCV good to go, sir." — 단계 1/2, 재시도 준비, 2차 시도.
```

| `stage` | `stage_label` | `scv_line` |
| --- | --- | --- |
| `starting` | 실행 환경 준비 | `Reportin' for duty.` |
| `worker` | 단계 구현 | `Orders received.` |
| `acceptance` | 단계 인수 검사 | `Affirmative.` |
| `verifier` | 단계 읽기 전용 검증 | `I read you.` |
| `failure-analysis` | 실패 증거 분석 | `Come again, Cap'n?` |
| `retry` | 재시도 준비 | `SCV good to go, sir.` |
| `step-complete` | 단계 완료 | `Job's finished.` |
| `final-acceptance` | 전체 인수 검사 | `Affirmative.` |
| `final-verifier` | 전체 읽기 전용 검증 | `I read you.` |
| `complete` | 실행 완료 | `Job's finished.` |
| `blocked` / `failed` | 차단됨 / 실패 | `I can't build there.` |
| `cancelled` | 취소됨 | `I'm not readin' you clearly.` |

진행 스냅샷은 단계·시도·완료 개수만 공개하며 프롬프트, 원시 출력, 증거 본문,
환경값은 노출하지 않는다. 원자적으로 교체된 인덱스를 읽으므로 실행기 잠금도
방해하지 않는다.

`$scv:workflow`는 다음 종료 목표를 지원한다.

- `analyze`: 스펙 승인까지 진행하고 브랜치나 worktree를 만들지 않는다.
- `plan`: 계획 승인까지 진행하고 브랜치나 worktree를 만들지 않는다.
- `full`: 격리 실행, 검증, 인계 준비까지 진행한다.

`$scv:improve`는 실행 중 수집한 실패 관찰, lesson 후보, SCV 자체 개선 제안을
검토하고 승인하거나 폐기하는 별도 흐름이다.

## 안전 원칙

- 스펙과 구현 계획은 서로 다른 사용자 승인 게이트를 통과해야 한다.
- 계획 승인 전에는 브랜치나 worktree를 만들지 않는다.
- materialize는 기본 worktree를 switch하지 않는다. 임시 Git index로 승인된 소스
  기준 `A` 위에 계획 커밋 `P`를 만들고 작업 브랜치를 `P`에 원자적으로 연결한
  다음, 그 브랜치의 linked worktree를 생성한다. 기본 worktree가 dirty여도 HEAD,
  index, 파일 상태를 변경하지 않는다.
- 구현 diff의 기준은 `A`, 실행 중 고정하는 worktree HEAD는 `P`다. `P`에 포함된
  `.scv/tasks/<task-id>/` 승인 문서는 실행 중 변경할 수 없다.
- 승인 문서는 `P`의 Git object에 남으므로 자격증명, 토큰, 비공개 원문 같은
  민감값을 스펙이나 계획에 기록하지 않는다.
- 인수 검사는 부모 환경을 최소 allowlist로 다시 만들고, Codex·SSH·클라우드·
  패키지 관리자 자격증명 경로 읽기와 네트워크, 허용 경로 밖 쓰기를 차단한
  Codex sandbox에서 실행한다.
- 명령별 인수 `HOME`·`TMP*`는 `/private/tmp`의 독립 `0700` 경로를 사용하며 모든
  worktree와 Git common directory 밖인지 확인한다. 정확한 `packageManager` pin은
  PATH에 사전 설치된 동일 버전을 offline으로 검증한 뒤에만 wrapper로 제공하고,
  부재·불일치·자동 설치 요구는 worker 전 infrastructure blocker로 처리한다.
- subprocess stdout/stderr는 합계 8 MiB로 제한하고 초과·timeout·취소 시 전체
  process group을 종료한다. 명령 본체가 먼저 끝나도 같은 group에 남은 background
  자식을 정리한다. controller 재시작으로 미완료된 attempt는 구현 예산에서
  제외하고 blocker 감사 기록으로 보존한다.
- 중첩 Codex는 인증만 연결한 임시 `CODEX_HOME`과 별도 모델 HOME을 사용한다.
  worker는 worktree 쓰기, verifier와 Failure Analyst는 읽기 전용 권한 프로필을
  사용하며, 이 프로필에 구형 `--sandbox` 옵션을 다시 합성하지 않는다.
- 이 격리는 열거한 자격증명과 전달 환경을 보호하는 exact-path 경계다. 홈 아래에
  설치된 Node·pnpm 호환성을 위해 사용자 홈 전체 읽기를 차단하지 않으므로,
  목록 밖 일반 사용자 파일의 알려진 절대 경로까지 숨기지는 않는다.
- 단계별 검사와 전체 인수 조건, 읽기 전용 최종 검증이 모두 성공해야 한다.
- 새 계획은 최초 실행과 자동 수정 1회를 기본으로 하는 v2 `loop_policy`를 사용한다.
  같은 실패와 같은 워크트리 상태가 반복되면 남은 횟수가 있어도 멈추며,
  감지는 기존 실패·인수·워크트리 증거만 비교한다.
- 실행 후 HEAD나 파일 내용이 바뀌면 기존 증거를 무효화하고 재검증을 요구한다.
- 상태, 실행 결과, 최종 증거는 해시로 봉인하고 재개 시 실제 파일과 다시 대조한다.
- 상태·잠금 디렉터리는 소유자 전용 권한으로 만들고, 상태 파일과 실행 인덱스는
  심볼릭 링크·비정상 파일 유형·과도한 크기를 거부한 뒤에만 읽는다.
- 같은 태스크를 여러 세션이 다뤄도 상태·제어기·실행기 잠금으로 충돌을 방지한다.
- `READY`는 인계 가능한 상태일 뿐이다. worktree 삭제, merge, push는 자동으로
  실행하지 않는다.

## 계획 커밋과 저장 위치

```text
A  승인된 소스 기준
└── P  .scv 승인 문서만 추가한 계획 커밋 ← 작업 브랜치와 linked worktree
    └── 구현 변경은 P를 고정 HEAD로 둔 worktree에서 생성
```

| 데이터 | 저장 위치 | Git 추적 |
| --- | --- | --- |
| 상태, 승인 기록, 잠금, 실행·학습 증거 | `<git-common-dir>/scv/` | 아니요 |
| 승인된 스펙 사본 | `.scv/tasks/<task-id>/spec.md` | 계획 커밋 `P`에서 추적 |
| 승인된 계획 사본 | `.scv/tasks/<task-id>/plan.json` | 계획 커밋 `P`에서 추적 |
| 소스 기준과 승인 해시 | `.scv/tasks/<task-id>/manifest.json` | 계획 커밋 `P`에서 추적 |

`plan.json.expected_base_sha`와 manifest의 `source_base`는 `A`를 가리킨다. `P`는
자기 커밋 내용 안에 자신의 SHA를 기록할 수 없으므로 Git common directory의
`plan_anchor.commit_sha`와 실행 인덱스의 `expected_head_sha`에 기록한다.

## 실패 학습과 개선

실행 실패는 곧바로 영구 규칙이 되지 않는다. 읽기 전용 Failure Analyst가 실패
증거를 정제하고, 다음 worker 재시도에는 해당 진단만 제한적으로 전달한다. 성공한
재시도의 교훈도 우선 `candidate`로 저장하며, 최종 실행 증거 재검증과 명시적인
사람 승인을 통과해야 같은 실패 signature에 사용할 수 있는 `active` lesson이 된다.

활성 lesson이 같은 실패를 다시 만들면 `suspect`로 내려 더 이상 자동 주입하지
않는다. 실행 중 발견한 제어기 결함 역시 설치된 플러그인을 자기 수정하지 않고
별도 개선 제안과 소스 worktree 작업으로 넘긴다.

## 얕은 실행 루프

새 계획은 다음과 같이 총 시도 횟수와 정체 감지를 명시한다. 기존 v1 계획은
호환을 위해 종전의 최대 3회 동작으로 읽지만 새로 만들지는 않는다.

```json
{
  "schema_version": 2,
  "loop_policy": {
    "max_attempts": 2,
    "detect_stagnation": true
  }
}
```

실패할 때만 정규화된 실패 signature, 인수 결과 벡터, 워크트리 지문을 비교한다.
정상 성공 경로에는 감지용 지문 계산이 추가되지 않는다. 연속된 동일 상태는
`stalled`, 과거 상태로 복귀하면 `oscillating`, 동일한 검증자 지적이 반복되면
`verifier_disagreement`로 종료하고 계획 수정 여부를 사람이 결정한다.

## 저장소 구조

```text
.
├── .agents/plugins/marketplace.json
├── .github/workflows/codex-ci.yml
└── plugins/codex/scv/
    ├── .codex-plugin/plugin.json
    ├── README.md
    ├── skills/
    │   ├── workflow/
    │   │   ├── SKILL.md
    │   │   ├── agents/openai.yaml
    │   │   └── references/workflow-contract.md
    │   └── improve/
    │       ├── SKILL.md
    │       ├── agents/openai.yaml
    │       └── references/improvement-contract.md
    ├── scripts/
    │   ├── scv.py
    │   ├── scv_state.py
    │   ├── execute.py
    │   ├── improve.py
    │   ├── learning.py
    │   ├── cli_ko.py
    │   ├── runtime.py
    │   ├── scv_dialogue.py
    │   └── workspace.py
    └── tests/
        ├── e2e/
        │   ├── __init__.py
        │   └── test_workflow_live.py
        ├── test_dialogue.py
        ├── test_execute.py
        ├── test_learning.py
        ├── test_runtime.py
        ├── test_scv.py
        └── test_scv_state.py
```

- `scv.py`: 상태 전이, 승인 게이트, worktree 수명주기를 소유하는 제어기
- `scv_state.py`: Git common directory 아래의 원자적 태스크 상태 저장소
- `execute.py`: 승인된 계획 실행과 단계별·최종 검증 증거 수집
- `learning.py`: 실패 관찰, lesson, 개선 제안 저장 및 무결성 검증
- `improve.py`: 검증된 증거를 기반으로 한 사람 승인형 학습 관리
- `cli_ko.py`: 각 명령의 `argparse` 도움말과 오류 접두사를 한글로 통일
- `runtime.py`: 지원 운영체제와 필수 런타임 검사
- `scv_dialogue.py`: 상태·진행 결과에 표시 전용 한글 라벨과 SCV 대사를 결정적으로 부가
- `workspace.py`: worktree 상태 지문 계산과 실행·인계 간 동일성 검증

## 설치

```bash
git clone git@github.com:FRONT-JB/scv.git
codex plugin marketplace add ./scv
codex plugin add scv@scv
```

설치 후 새 Codex 대화에서 호출한다.

```text
$scv:workflow 이 변경을 요구사항 접수부터 인계까지 진행해줘.
$scv:workflow 이 요구사항을 분석만 하고 구현은 시작하지 마.
$scv:workflow 태스크 20260713-example을 재개해줘.
$scv:improve 태스크 20260713-example의 실패 학습 후보를 검토해줘.
```

## 요구 사항

- macOS
- Git
- Python 3.9 이상
- Codex CLI 0.144.1 이상
- POSIX `sh`

Linux, WSL, Windows에서는 태스크나 실행 상태를 만들기 전에 중단한다.

## 검증

```bash
python3 -m unittest discover -s plugins/codex/scv/tests -p 'test_*.py' -v
python3 -m json.tool .agents/plugins/marketplace.json
python3 -m json.tool plugins/codex/scv/.codex-plugin/plugin.json
git diff --check
```

실제 Codex Worker·Failure Analyst·Verifier와 sandbox 인수 검증을 포함한 Live
E2E는 유효한 Codex 인증과 네트워크가 있는 macOS 호스트에서 명시적으로 실행한다.
정상 완료뿐 아니라 실제 인수 실패, 분석 증거 봉인, 2차 Worker 재시도도 검증한다.

```bash
SCV_LIVE_E2E=1 python3 -m unittest discover \
  -s plugins/codex/scv/tests/e2e -p 'test_workflow_live.py' -v
```

기본 테스트에서는 Live E2E를 건너뛴다. 결정적 테스트는 각 공개 진행 단계,
잠금 중 조회, 재시도, 차단·취소, 구버전 v1 인덱스 호환과 증거 무결성을 다룬다.
GitHub Actions는 macOS에서 Python 3.9와 최신 Python 3.x 조합을 검증한다.
