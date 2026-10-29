# 평가·검수 에이전트

콘텐츠 생성 에이전트와 분리된 최종 게이트입니다. 코드 기반 품질 검사와 독립 LLM 언어 검수를 합쳐 통과·수정 반환·수집 보완 요청·탈락 보관을 판정합니다.

- `agent.py`: 코드 검사와 별도 LLM 자연스러움·번역·명료성 평가를 결합
- `graph.py`: 평가 단계를 독립 LangGraph로 실행
- `prompt.py`: 검수 점수표와 평가 규칙

Language naturalness, translation, clarity, fan engagement, and card flow use a common 2/5 minimum; image relevance uses 2/5; image composition and content-type fit use 2/5; detail factual support and completeness use 3/5; safety/respect remains 4/5. Overall and per-card score thresholds are 68/100 and 55/100. Blockers cannot be offset by scores, revise issues route to their owner when the configured threshold is missed, and warnings are logged only. HTML copy goes below the image; PNGs remain text-free.

The content-type fit threshold is the shared policy value of 2/5; an issue below that threshold is routed for revision.

API 키 없이 실행한 구조 시험이나 사람이 주입한 review fixture는 `offline_review_only`로 표시하며 피드에 게시 승인하지 않습니다.
