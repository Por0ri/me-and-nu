import Link from "next/link";

import type { Source } from "@/types/content";

type SourceInfoProps = {
  sources: Source[];
  headingId?: string;
  title?: string;
  /** true면 우리 서비스 안의 글로 이동한다(AI 답변 근거). */
  internal?: boolean;
};

export function SourceInfo({
  sources,
  headingId = "content-sources-title",
  title = "출처 / 원문",
  internal = false,
}: SourceInfoProps) {
  return (
    <section aria-labelledby={headingId} className="space-y-3">
      <h2 id={headingId} className="text-lg font-semibold text-black">
        {title}
      </h2>
      {sources.length === 0 ? (
        <p role="status" className="text-sm text-black/60">
          출처 정보가 없습니다.
        </p>
      ) : (
        <ul className="space-y-3">
          {sources.map((source) => (
            <li
              key={source.id}
              className="space-y-2 rounded-lg border border-black/10 p-4"
            >
              <p className="text-sm font-medium text-black">{source.name}</p>
              {source.url && internal ? (
                <Link
                  href={source.url}
                  className="inline-block text-sm text-black/70 underline underline-offset-4"
                >
                  이 글 보기
                </Link>
              ) : source.url ? (
                <a
                  href={source.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-block text-sm text-black/70 underline underline-offset-4"
                >
                  원문 링크 (새 탭)
                </a>
              ) : (
                <p className="text-sm text-black/60">원문 링크 없음</p>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
