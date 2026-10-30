const transientMessages = new Set([
  "저장했어요.",
  "저장을 취소했어요.",
  "좋아요를 눌렀어요.",
  "좋아요를 취소했어요.",
  "공유 기능은 준비 중이에요. 아직 반영되지 않아요.",
  "AI 콘텐츠 탐색은 준비 중이에요.",
  "AI 질문 기능은 준비 중이에요. 질문을 전송하지 않아요.",
]);

/** Keep progress and errors readable; dismiss completed actions and feature notices. */
export function dismissActionNotice(message: string, dismiss: () => void) {
  if (!transientMessages.has(message)) return;
  const timer = window.setTimeout(dismiss, 2000);
  return () => window.clearTimeout(timer);
}
