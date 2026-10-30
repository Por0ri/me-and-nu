const DEFAULT_AI_NOTICE =
  "AI가 생성한 답변입니다. 부정확한 내용이 포함될 수 있으니 출처와 근거를 확인해 주세요.";

export function AINotice({ notice }: { notice?: string }) {
  return (
    <p className="va-muted text-xs leading-5" aria-label="AI 답변 안내">
      {notice?.trim() || DEFAULT_AI_NOTICE}
    </p>
  );
}
