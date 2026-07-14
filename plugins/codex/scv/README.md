# SCV for Codex

SCV는 macOS에서 요구사항 정리부터 구현 계획 승인, 격리 워크트리 실행, 검증, 인계까지 하나의 재개 가능한 프로세스로 묶는 Codex 플러그인이다. `$scv:workflow`는 `analyze`, `plan`, `full` 세 가지 종료 목표를 지원하고, `$scv:improve`는 실패 학습 후보와 SCV 개선 제안을 별도의 사람 승인 흐름으로 관리한다.

## 핵심 동작

- 스펙 승인과 계획 승인을 서로 다른 명시적 게이트로 유지한다.
- 계획 승인 전에는 브랜치나 워크트리를 만들지 않는다.
- 태스크 상태는 Git common directory에 저장해 워크트리 생성 전에도 같은 ID로 재개한다.
- 구현은 승인된 기준 리비전을 다시 확인한 뒤 격리 워크트리에서만 실행한다.
- 각 단계 검증을 통과한 뒤 전체 인수 조건을 다시 실행하고 읽기 전용 검증기로 최종 확인한다.
- 인수 조건은 부모 환경을 최소 allowlist로 다시 만들고, Codex·SSH·클라우드·패키지 관리자 자격증명 경로의 읽기와 네트워크, 워크트리·전용 임시 공간 밖 쓰기를 차단한 Codex 샌드박스에서 실행한다.
- 실행 완료 후 워크트리 HEAD 또는 내용이 바뀌면 인계를 차단하고 재검증을 요구한다.
- 단계별·최종 실행 증거 묶음의 해시를 저장하고, 재개와 상태 조회 때 실제 파일을 다시 확인한다.
- `READY`가 되어도 워크트리를 자동 삭제하거나 merge·push하지 않는다.
- 중첩 Codex 실행은 호스트 인증만 이어받는 임시 `CODEX_HOME`을 사용하므로 사용자 홈의 스킬·지침·플러그인·MCP 설정에 의존하지 않는다. 모델 셸은 별도 임시 HOME과 환경 allowlist만 받고, 권한 프로필은 연결된 `auth.json`, 실제 원본 인증 파일, SSH·클라우드·패키지 관리자 자격증명 경로를 읽지 못하게 차단한다.
- 이 격리는 열거한 자격증명과 전달 환경을 보호하는 경계다. macOS 사용자 홈에 설치된 Node·pnpm 같은 개발 도구의 동작을 유지하기 위해 전역 홈 읽기 차단은 사용하지 않으므로, 목록 밖의 일반 사용자 파일 절대 경로까지 모두 숨기지는 않는다.
- 같은 태스크를 여러 세션이 다뤄도 상태 잠금, 제어기 실행 임대, 실행기 잠금으로 상태 갱신 유실과 증거 충돌을 막는다.
- 상태와 실행 진행 출력에는 한글 `state_label`·`stage_label`과 실제 SCV 대사
  `scv_line`을 덧붙인다. 한글 라벨과 대사는 표시 전용이며 기존 상태값과 종료
  코드가 계속 권위 있는 계약이다.
- 실행 중 `status`는 단계·시도·완료 개수만 담은 `execution_progress`를 반환한다. 프롬프트, 원시 출력, 증거 본문, 환경값은 반환하지 않으며 실행기 잠금을 방해하지 않는다.
- 타임아웃을 제외한 구현·인수·검증 실패에는 별도의 읽기 전용 Failure Analyst를 한 번 호출하고, 정제된 분석만 다음 worker 재시도에 전달한다. 타임아웃은 원래 태스크의 제한된 재시도·증거로만 남긴다.
- 성공한 재시도의 교훈은 `candidate`로만 저장한다. 최종 실행 증거와 명시적 승인을 거친 `active` lesson만 같은 실패 signature에 재사용한다.
- 실행 중인 SCV는 자기 코드를 수정하지 않는다. 독립 분석이 제어기 결함으로 판정한 경우에만 개선 제안을 남기고 별도 소스 worktree 작업으로 넘긴다. 일반 구현 소진·타임아웃·환경 실패는 원래 태스크에서 처리한다.

## 구조

```text
plugins/codex/scv/
├── .codex-plugin/plugin.json
├── skills/workflow/
│   ├── SKILL.md
│   ├── agents/openai.yaml
│   └── references/workflow-contract.md
├── skills/improve/
│   ├── SKILL.md
│   ├── agents/openai.yaml
│   └── references/improvement-contract.md
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

`scv.py`가 상태 전이와 워크트리 수명주기를 소유하고, `scv_state.py`가 Git common directory 아래의 원자적 상태 저장을 담당한다. `execute.py`는 승인된 계획을 실행하고 `runs/<plan-sha>/`에 검증 증거를 남기는 내부 실행기다. `learning.py`는 같은 Git common directory의 `scv/learning/` 아래에서 실패 관찰·lesson·개선 제안을 별도 잠금으로 관리하고, `improve.py`는 최종 증거를 재검증한 사람 승인만 반영한다. `cli_ko.py`는 명령행 도움말과 오류 접두사를 한글로 통일하고, `scv_dialogue.py`는 권위 있는 상태값을 바꾸지 않고 표시 전용 한글 라벨과 대사를 덧붙인다. 개선 제안을 수리 태스크로 넘길 때는 Git이 추적하는 `plugins/codex/scv` 소스인지 확인하고, 설치된 플러그인 영역과 원본 태스크 ID를 거부한다. `workspace.py`의 공통 지문 계산으로 실행기가 검증한 정확한 워크트리 상태를 제어기와 인계 단계까지 연결한다. 외부 워크트리 도구를 필수로 요구하지 않으며 기본 구현은 `git worktree`를 사용한다. 사용자가 이미 만든 워크트리는 `--adopt-existing`을 명시한 경우에만 기준 SHA·브랜치·청결 상태를 확인한 뒤 채택한다.

## 설치

저장소 루트를 Codex marketplace로 등록한 뒤 플러그인을 추가한다.

```bash
codex plugin marketplace add <repository-root>
codex plugin add scv@scv
```

설치 후 새 대화에서 `$scv:workflow`를 호출한다. 예:

```text
$scv:workflow 이 변경을 요구사항 접수부터 인계까지 진행해줘.
$scv:workflow 이 요구사항을 분석만 하고 구현은 시작하지 마.
$scv:workflow 태스크 20260713-example을 재개해줘.
$scv:improve 태스크 20260713-example에서 생성된 실패 학습 후보를 검토해줘.
```

필수 런타임은 macOS, Git, Python 3.9 이상, Codex CLI 0.144.1 이상, POSIX `sh`다. Linux, WSL, Windows에서는 태스크나 실행 상태를 만들기 전에 한글 오류로 중단한다. `start full`, 완료된 `plan`의 `full` 승격, `materialize`, `execute`는 중첩 Codex·Seatbelt 경계를 소유하므로 관리형 Codex 세션에서 호스트 실행 승인을 받아 호출한다. 이 승인은 바깥 제어기 명령에만 적용되며 내부 worker·analyst·verifier·인수 검증 샌드박스의 권한은 넓히지 않는다. 자세한 단계와 상태 전이는 [workflow-contract.md](skills/workflow/references/workflow-contract.md)를 참고한다.

## 실행 진행 조회

메인 세션은 `execute`를 하나의 장기 exec 세션으로 유지하고, 별도 읽기 전용
`status` 호출로 15–30초마다 변경된 진행만 확인한다. 두 번째 `execute`를 시작하지
않으며 다음처럼 보고한다.

```text
실행 중 — "Orders received." — 단계 1/2, 단계 구현, 1차 시도.
실행 중 — "Come again, Cap'n?" — 단계 1/2, 실패 증거 분석, 1차 시도.
실행 중 — "SCV good to go, sir." — 단계 1/2, 재시도 준비, 2차 시도.
```

| `execution_progress.stage` | `stage_label` | `scv_line` |
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
| `complete` | 실행 증거 완료 | `Job's finished.` |
| `blocked` / `failed` | 차단 또는 실패 | `I can't build there.` |
| `cancelled` | 실행 취소 | `I'm not readin' you clearly.` |

## 검증

기본 테스트는 실제 Codex 호출 없이 각 진행 단계, 잠금 중 상태 조회, 재시도,
차단·취소, 증거 무결성을 결정적으로 검증한다. Live E2E는 명시적으로 활성화할
때만 임시 Git 저장소에서 실제 Worker·Failure Analyst·Verifier·sandbox·handoff와
실패 후 재시도를 검증한다.

```bash
python3 -m unittest discover -s plugins/codex/scv/tests -p 'test_*.py' -v

SCV_LIVE_E2E=1 python3 -m unittest discover \
  -s plugins/codex/scv/tests/e2e -p 'test_workflow_live.py' -v
```
