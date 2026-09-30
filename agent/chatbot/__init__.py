"""소비자 챗봇 에이전트 (LangGraph).

흐름: 앞단(코드) → ① 입구 검사 → ② 총괄 ⇄ [③ 근거 찾기 · ④ 사실 확인]
→ ⑤ 답변 작성 ⇄ ⑥ 출구 점검 → 결과.

DB도 백엔드 설정도 모른다. 검색 · 사실 조회 · LLM 도구는 ChatDeps로 밖에서 받는다.
백엔드는 app.integrations.chatbot_agent에서 도구를 만들어 run_turn을 부르고 기록을 저장한다.

중요한 제한(토픽 값, 호출 횟수, 멈추기, 고정 문구)은 프롬프트가 아니라 코드에 둔다.
"""

from agent.chatbot.graph import TurnInput, TurnOutcome, build_graph, run_turn

__all__ = ["TurnInput", "TurnOutcome", "build_graph", "run_turn"]
