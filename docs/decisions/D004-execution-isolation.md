# D004 — 실행 격리와 자격증명

- 상태: Resolved
- 결정일: 2026-07-17
- 적용 범위: provider egress, tool sandbox, acceptance, environment, credentials

## 경계 분리

controller/backend 프로세스가 모델 provider에 연결하는 egress와 모델이 실행하는
tool 또는 acceptance command의 network 권한을 분리한다. provider egress는 선택된
host endpoint에만 허용할 수 있지만 모델 tool과 acceptance에는 network를 항상
거부한다. provider 호출이 필요하다는 이유로 command sandbox network를 켜지 않는다.

Codex tool은 `workspace-write` 또는 `read-only` sandbox와
`sandbox_workspace_write.network_access=false`를 사용한다. Claude tool은 생성된
settings에서 `sandbox.enabled=true`, `failIfUnavailable=true`,
`allowUnsandboxedCommands=false`, 빈 allowed domain, 명시적 credential deny를
사용한다. acceptance는 host tool sandbox에 의존하지 않고 kernel이 생성한 macOS
Seatbelt profile로 별도 실행한다. 어느 enforcement도 probe할 수 없으면 차단한다.

구현 쓰기는 결속된 worktree 안에서도 승인된 plan path root와 operation scratch만
허용한다. `.git/**`, `.scv/tasks/**`, `.scv/runtime/**`, setup 관리 링크와 kernel
소유 control file은 backend write 대상이 아니다. acceptance는 기본 read-only이며
계획에 명시된 test output path만 scratch로 허용한다. `.git` ref, config와 hook,
다른 worktree, 사용자 home, Unix socket와 Apple Events는 거부한다.

## 환경과 자격증명

controller가 child에 전달할 수 있는 기본 환경 allowlist는 다음뿐이다.

```text
PATH, LANG, LC_ALL, LC_CTYPE, TMPDIR, HOME, USER, LOGNAME,
TERM, CI, NO_COLOR, PYTHONUTF8
```

값은 controller가 정규화하고 `HOME`과 `TMPDIR`은 operation 전용 `0700` 경로로
대체한다. 계획에 선언된 변수만 이름·목적·secret 여부를 검증한 뒤 추가할 수 있다.
`*TOKEN*`, `*SECRET*`, `*KEY*`, cloud/provider 변수와 proxy 변수는 기본 deny다.

credential deny class에는 최소한 `.ssh`, `.aws`, `.azure`, `.config/gcloud`,
`.kube`, `.docker`, Git credential store/config, npm/pypi/netrc, provider agent home,
macOS Keychain database와 security/keychain 명령이 포함된다. deny class는 경로
realpath와 command allowlist 양쪽에서 검사한다.

provider 인증은 controller process에만 일시 전달하고 tool subprocess 환경에서는
제거한다. 초기 deterministic nested backend는 API key 또는 검증된 격리
`apiKeyHelper`만 지원한다. 사용자 OAuth/keychain 파일을 임시 home에 복사하지
않는다. 기존 CLI 인증밖에 없고 안전한 격리를 입증할 수 없으면 host 대화 executor는
계속 사용할 수 있지만 nested backend operation은 `CREDENTIAL_UNAVAILABLE`로
차단한다. secret 원문, 환경 전체와 auth file은 evidence에 저장하지 않는다.

## 종료와 증거

각 run은 새 process group으로 시작한다. timeout, cancel, schema error와 output
limit 초과 시 TERM 후 제한된 grace를 거쳐 KILL하고 descendant 부재를 확인한다.
stdout/stderr 합계는 최대 8 MiB이며 redaction 후 digest, 종료 코드, argv template,
sandbox profile digest와 duration만 evidence로 봉인한다.
