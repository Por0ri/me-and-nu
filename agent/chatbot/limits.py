"""루프 상한과 크기 제한. 값은 설계 그림 v4의 제안값이다. 멈추는 판단은 코드가 한다."""

# 루프 E — 한 턴 전체
LLM_CALL_CAP = 8
# [추천안] 작성 → 출구 → 되돌림 작성 → 출구 몫을 먼저 떼어 둔다.
WRITE_RESERVE = 4
TURN_SECONDS = 20.0
CALL_TIMEOUT_SECONDS = 12.0
RECURSION_LIMIT = 25  # LangGraph 단계 상한(최후 안전장치)

# 루프 A — 총괄 ⇄ 하위
SUB_CALL_CAP = 4
REPLAN_CAP = 2
# 루프 B — 근거 찾기 안
RETRIEVE_RETRY_CAP = 1
# 루프 C — 사실 확인 안
FACT_RETRY_CAP = 1
# 루프 D — 작성 ⇄ 출구 점검
EXIT_RETRY_CAP = 1

# 입력 · 근거 크기 (발췌 길이 · 후보 수는 백엔드 검색 도구가 정한다)
MAX_QUESTION_CHARS = 500  # FR-612
MAX_EXCERPTS = 5
HISTORY_TURNS = 3  # 최근 3번 주고받은 것은 원문으로 넣는다.
