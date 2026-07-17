# 아키텍처 결정 기록

이 디렉터리는 장기적으로 유지해야 하는 아키텍처 결정을 누적한다. ADR은 번호
원장이 아니며, 현재 목록과 대체 관계는 디렉터리의 파일 집합에서 결정한다.

## 파일 이름

새 ADR은 다음 형식을 사용한다.

```text
ADR-<task-id>-<task-sequence>-<slug>.md
```

- `task-id`는 커널이 충돌 방지 난수로 발급한다.
- `task-sequence`는 같은 태스크 안에서 `01`부터 잠금 아래 증가한다.
- `slug`는 결정을 설명하는 짧은 영문 소문자 문자열이다.
- 기존 `ADR-0001-*` 형식 파일은 이름을 바꾸거나 ID를 재사용하지 않는다.

모델은 ADR 본문을 제안할 수 있지만 ID, 날짜, 상태, 해시와 파일 이름은 결정하지
않는다.

## 필수 메타데이터

```yaml
---
id: ADR-<task-id>-<task-sequence>-<slug>
title: 결정 제목
date: YYYY-MM-DD
task_id: <task-id>
status: proposed
decision_hash: <kernel-computed-sha256>
supersedes: []
---
```

`status`는 커널이 승인 흐름에 따라 기록한다. `accepted`가 된 ADR의 의미를
제자리에서 덮어쓰지 않는다. 기존 결정을 바꾸려면 새 ADR을 추가하고
`supersedes`에 이전 ID를 기록한다. 이전 파일은 자동으로 수정하거나 삭제하지
않는다.

## 본문 구조

각 ADR은 다음 내용을 포함한다.

1. 배경과 해결할 문제
2. 최종 결정
3. 고려한 대안과 선택하지 않은 이유
4. 긍정적·부정적 영향과 후속 조치
5. 관련 태스크, 조사와 다른 ADR

ADR에는 자격증명, 환경변수 원문, 개인 정보와 장비별 절대 경로를 넣지 않는다.
