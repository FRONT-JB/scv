# D002 — 호스트 호환성과 호출 계약

- 상태: Resolved
- 결정일: 2026-07-17
- 적용 범위: 지원 버전, capability probe, backend invocation

## 지원 기준

초기 macOS 지원 기준은 Codex CLI `>= 0.144.5`, Claude Code `>= 2.1.203`,
Python `>= 3.9`다. 버전만으로 기능을 추정하지 않고 setup `validate`가 각 flag와
sandbox capability를 실제 probe한다. 현재 기준선은 Codex `0.144.5`, Claude
`2.1.204`, Ollama `0.30.10`, Darwin arm64에서 확인했다.

필수 probe는 다음과 같다.

- Codex: plugin marketplace/add, `exec`, `exec review`, `--ephemeral`,
  `--ignore-user-config`, `--ignore-rules`, `--strict-config`, `--output-schema`,
  `--json`, `--sandbox`, `--cd`와 network-disabled workspace sandbox
- Claude: `plugin validate --strict`, `--plugin-dir`, `--bare`, `--print`,
  `--output-format json`, `--json-schema`, `--no-session-persistence`,
  `--permission-mode dontAsk`, tool allow/deny, `--settings`와 fail-closed sandbox
- 공통: structured output, timeout 종료, process-group cleanup와 해당 host 인증 모드

필수 capability가 하나라도 없으면 해당 adapter를 비활성화한다. 버전 미달에서
Skill copy, 자유 형식 output 또는 일반 shell fallback을 사용하지 않는다.

## 호출 계약

Codex backend의 기본 argv 골격은 다음과 같다.

```text
codex exec --ephemeral --ignore-user-config --ignore-rules --strict-config
  --output-schema <schema> --json --sandbox <read-only|workspace-write>
  --cd <bound-worktree> --model <resolved-model> <operation-prompt>
```

리뷰는 같은 격리 옵션을 가진 `codex exec review --uncommitted`와 구조화된 output
schema를 사용한다. config override는 adapter의 고정 allowlist만 허용한다.
`--dangerously-bypass-approvals-and-sandbox`, 추가 writable directory, cloud task와
worktree 기능은 금지한다.

Claude backend의 API-key 격리 모드 argv 골격은 다음과 같다.

```text
claude --bare --plugin-dir <project>/.scv/plugin --print
  --output-format json --json-schema <schema> --no-session-persistence
  --permission-mode dontAsk --tools <minimal-tools>
  --disallowedTools <deny-rules> --settings <generated-settings>
  --model <resolved-model> <operation-prompt>
```

`--bare`는 API key 또는 격리된 `apiKeyHelper`가 있는 경우에만 사용한다. 사용자
OAuth/keychain과 설정을 상속하는 실행은 deterministic backend로 취급하지 않는다.
`bypassPermissions`, `--dangerously-skip-permissions`, `--worktree`, background agent,
WebFetch와 임의 MCP는 금지한다.

host 대화 executor는 현재 Codex/Claude 세션을 사용한다. backend executor만 위의
중첩 CLI 계약을 사용한다. 실제 model ID와 옵션은 host mapping 한 곳에만 두고
registry는 논리 role만 참조한다.

Claude는 결정적 acceptance 결과의 설명과 보조 검증에 사용할 수 있지만 최종
품질 gate의 단독 판정자가 될 수 없다. 결정적 kernel 검사와 Codex 최종 리뷰가
필수이며, 초기 버전에서 Claude는 review verdict를 만들지 않는다.

## 검증

host probe 결과에는 version, help-output digest, 지원 flag와 sandbox smoke 결과를
남긴다. unknown flag, warning-only manifest, 무효 settings의 silent ignore 또는
schema 미준수는 모두 실패다.

참고:

- <https://learn.chatgpt.com/docs/build-plugins>
- <https://learn.chatgpt.com/docs/agent-approvals-security>
- <https://code.claude.com/docs/en/cli-reference>
- <https://code.claude.com/docs/en/sandboxing>
