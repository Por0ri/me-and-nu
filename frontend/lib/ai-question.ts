export const AI_QUESTION_MAX_LENGTH = 500;

// Count Unicode code points, matching Python's validation after trimming.
export function aiQuestionLength(question: string): number {
  return Array.from(question.trim()).length;
}

export function aiQuestionError(question: string): string | null {
  const length = aiQuestionLength(question);
  if (length === 0) return "질문을 입력해 주세요.";
  if (length > AI_QUESTION_MAX_LENGTH) return `질문은 최대 ${AI_QUESTION_MAX_LENGTH}자까지 입력할 수 있어요.`;
  return null;
}
