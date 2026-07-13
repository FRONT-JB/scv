# SCV

![SCV construction utility units cycling through Scope, Construct, Verify, and Improve](docs/assets/scv-readme-banner.png)

> **S**cope → **C**onstruct → **V**erify. SCV good to go.

SCV는 요구사항을 계획하고, 격리된 작업 공간에서 구현하고, 검증 결과를
다음 실행에 반영하는 재개 가능한 Codex 소프트웨어 변경 워크플로다. 명시적인
승인 게이트와 재현 가능한 실행 증거를 유지하면서 분석부터 인계까지 한 태스크로
연결한다.

## SCV 루프

```text
Scope      요구사항 조사 → 스펙 작성 → 스펙 승인 → 구현 계획 → 계획 승인
   ↓
Construct  기준 리비전 재확인 → 격리 worktree 생성 → 단계별 구현과 인수 검사
   ↓
Verify     전체 인수 조건 → 읽기 전용 최종 검증 → 실행 증거 고정 → 인계
   ↺
Improve    실패 분석 → 제한된 재시도 → lesson 후보/개선 제안 → 사람 승인
```

`$scv:workflow`는 다음 종료 목표를 지원한다.

- `analyze`: 스펙 승인까지 진행하고 브랜치나 worktree를 만들지 않는다.
- `plan`: 계획 승인까지 진행하고 브랜치나 worktree를 만들지 않는다.
- `full`: 격리 실행, 검증, 인계 준비까지 진행한다.

`$scv:improve`는 실행 중 수집한 실패 관찰, lesson 후보, SCV 자체 개선 제안을
검토하고 승인하거나 폐기하는 별도 흐름이다.

## 안전 원칙

- 스펙과 구현 계획은 서로 다른 사용자 승인 게이트를 통과해야 한다.
- 계획 승인 전에는 브랜치나 worktree를 만들지 않는다.
- 구현은 승인된 기준 SHA에서 만든 격리 worktree 안에서만 수행한다.
- 인수 검사는 부모 환경을 최소 allowlist로 다시 만들고, Codex·SSH·클라우드·
  패키지 관리자 자격증명 경로 읽기와 네트워크, 허용 경로 밖 쓰기를 차단한
  Codex sandbox에서 실행한다.
- 중첩 Codex는 인증만 연결한 임시 `CODEX_HOME`과 별도 모델 HOME을 사용한다.
  worker는 worktree 쓰기, verifier와 Failure Analyst는 읽기 전용 권한 프로필을
  사용하며, 이 프로필에 구형 `--sandbox` 옵션을 다시 합성하지 않는다.
- 이 격리는 열거한 자격증명과 전달 환경을 보호하는 exact-path 경계다. 홈 아래에
  설치된 Node·pnpm 호환성을 위해 사용자 홈 전체 읽기를 차단하지 않으므로,
  목록 밖 일반 사용자 파일의 알려진 절대 경로까지 숨기지는 않는다.
- 단계별 검사와 전체 인수 조건, 읽기 전용 최종 검증이 모두 성공해야 한다.
- 실행 후 HEAD나 파일 내용이 바뀌면 기존 증거를 무효화하고 재검증을 요구한다.
- 상태, 실행 결과, 최종 증거는 해시로 봉인하고 재개 시 실제 파일과 다시 대조한다.
- 같은 태스크를 여러 세션이 다뤄도 상태·제어기·실행기 잠금으로 충돌을 방지한다.
- `READY`는 인계 가능한 상태일 뿐이다. worktree 삭제, merge, push는 자동으로
  실행하지 않는다.

## 실패 학습과 개선

실행 실패는 곧바로 영구 규칙이 되지 않는다. 읽기 전용 Failure Analyst가 실패
증거를 정제하고, 다음 worker 재시도에는 해당 진단만 제한적으로 전달한다. 성공한
재시도의 교훈도 우선 `candidate`로 저장하며, 최종 실행 증거 재검증과 명시적인
사람 승인을 통과해야 같은 실패 signature에 사용할 수 있는 `active` lesson이 된다.

활성 lesson이 같은 실패를 다시 만들면 `suspect`로 내려 더 이상 자동 주입하지
않는다. 실행 중 발견한 제어기 결함 역시 설치된 플러그인을 자기 수정하지 않고
별도 개선 제안과 소스 worktree 작업으로 넘긴다.

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
    └── scripts/
        ├── scv.py
        ├── scv_state.py
        ├── execute.py
        ├── improve.py
        ├── learning.py
        ├── runtime.py
        └── workspace.py
```

- `scv.py`: 상태 전이, 승인 게이트, worktree 수명주기를 소유하는 제어기
- `scv_state.py`: Git common directory 아래의 원자적 태스크 상태 저장소
- `execute.py`: 승인된 계획 실행과 단계별·최종 검증 증거 수집
- `learning.py`: 실패 관찰, lesson, 개선 제안 저장 및 무결성 검증
- `improve.py`: 검증된 증거를 기반으로 한 사람 승인형 학습 관리
- `runtime.py`: 지원 운영체제와 필수 런타임 검사
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
python3 -m unittest discover -s plugins/codex/scv/scripts -p 'test_*.py' -v
python3 -m json.tool .agents/plugins/marketplace.json
python3 -m json.tool plugins/codex/scv/.codex-plugin/plugin.json
git diff --check
```

GitHub Actions는 macOS에서 Python 3.9와 최신 Python 3.x 조합을 검증한다.
