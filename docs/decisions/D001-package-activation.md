# D001 — 패키지 배포와 활성화

- 상태: Resolved
- 결정일: 2026-07-17
- 적용 범위: plugin identity, bootstrap, Skill discovery, cutover

## 결정

배포 identity는 모든 manifest와 marketplace에서 `scv`로 고정한다. 권위 있는
소스 package는 `<source>/plugins/scv`이고, setup이 소비 프로젝트에 원자적으로
복사한 `<project>/.scv/plugin`은 해당 프로젝트 runtime의 권위 원본이다. 두
경로를 같은 저장소에 혼합하지 않는다.

소스 package는 완전히 self-contained여야 한다. package 밖을 향하는 상대·절대
링크와 `../` 참조를 허용하지 않는다. Codex repo marketplace는
`.agents/plugins/marketplace.json`, Claude marketplace는
`.claude-plugin/marketplace.json`에 두고 둘 다 `./plugins/scv`만 가리킨다.

bootstrap은 다음 두 경로를 지원한다.

1. Codex: `codex plugin marketplace add <repository>` 후
   `codex plugin add scv@scv`로 설치한 namespaced `setup`을 호출한다.
2. Claude: `claude plugin marketplace add <repository>` 후
   `claude plugin install scv@scv`, 또는 개발 시
   `claude --plugin-dir <source>/plugins/scv`로 `/scv:setup`을 호출한다.

setup은 host plugin을 설치·비활성화·제거하거나 사용자 전역 설정을 바꾸지 않는다.
필요한 host 명령만 안내하고 그 결과를 읽기 전용으로 probe한다.
소비 프로젝트가 SCV source checkout 자체이거나 source와 target root가 서로 포함
관계이면 source 오염을 막기 위해 setup을 거부한다. E2E는 source 밖 임시 소비
프로젝트를 명시적으로 전달한다.

정상 runtime activation은 `linked` 하나다. setup은 각 Skill마다 다음 상대 링크를
만든다.

```text
.agents/skills/<skill> -> ../../.scv/plugin/skills/<skill>
.claude/skills/<skill> -> ../../.scv/plugin/skills/<skill>
```

`installed` 또는 `--plugin-dir`는 bootstrap·배포 검증을 위한 일시 activation이다.
setup 직후 현재 Claude 세션에서는 새 최상위 `.claude/skills`가 감지되지 않을 수
있으므로 이미 로드된 `/scv:scv-1-plan`으로 이어갈 수 있다. 세션 종료 또는 plugin
비활성화 뒤에는 project link만 사용한다. installed copy와 linked copy가 동시에
발견되면 scope 간 deduplication을 가정하지 않고 실행을 차단한다.

공유 `SKILL.md` frontmatter의 허용 교집합은 `name`과 `description`뿐이다. 둘 다
영문이며 folder name과 `name`이 같아야 한다. Claude 전용 frontmatter와 Codex UI
metadata는 공유 frontmatter에 넣지 않고 host adapter 또는 `agents/openai.yaml`로
분리한다. 두 실제 validator를 통과하는 fixture로 이 교집합을 고정한다.

## 근거와 결과

Codex는 repo `.agents/skills`와 symlink target을 발견한다. Claude Code 2.1.203
이상은 project `.claude/skills/<name>` 디렉터리 symlink를 지원한다. 설치형 Claude
plugin은 cache에 복사되며 plugin 또는 marketplace 밖 symlink는 보존되지 않는다.
따라서 배포 package의 자급성과 설치 후 linked cutover가 모두 필요하다.

참고:

- <https://learn.chatgpt.com/docs/build-plugins>
- <https://learn.chatgpt.com/docs/build-skills>
- <https://code.claude.com/docs/en/plugins>
- <https://code.claude.com/docs/en/plugins-reference>
- <https://code.claude.com/docs/en/skills>
