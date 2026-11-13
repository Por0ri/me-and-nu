import type { AdminContentDetail } from "./admin-content";

export const contentInputLimits = { title: 300, summary: 10_000, body: 100_000, sources: 50, url: 1000 } as const;
export type AdminSourceInput = { url: string; title: string | null };
export type AdminContentInput = { title: string; summary: string | null; body: string | null; sources: AdminSourceInput[] };
export type ContentFormValue = { title: string; summary: string; body: string; sources: { url: string; title: string }[] };
export type ContentInputErrors = Record<string, string>;
const control = /[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f\ud800-\udfff]/u;
const length = (value: string) => Array.from(value).length;

export function contentFormValue(detail?: AdminContentDetail): ContentFormValue {
  return { title: detail?.title ?? "", summary: detail?.summary ?? "", body: detail?.body ?? "",
    sources: detail?.sources.map(source => ({ url: source.url, title: source.title ?? "" })) ?? [] };
}

export function validateContentInput(value: ContentFormValue): { input?: AdminContentInput; errors: ContentInputErrors } {
  const errors: ContentInputErrors = {};
  const text = (key: string, value: string, limit: number, required = false) => {
    const cleaned = value.trim();
    if (required && !cleaned) errors[key] = "필수 입력값입니다.";
    else if (control.test(value)) errors[key] = "사용할 수 없는 제어 문자가 포함되어 있습니다.";
    else if (length(cleaned) > limit) errors[key] = `${limit.toLocaleString("ko-KR")}자 이하로 입력해 주세요.`;
    return cleaned;
  };
  const title = text("title", value.title, contentInputLimits.title, true);
  const summary = text("summary", value.summary, contentInputLimits.summary) || null;
  const body = text("body", value.body, contentInputLimits.body) || null;
  if (value.sources.length > contentInputLimits.sources) errors.sources = "출처는 50개까지 입력할 수 있습니다.";
  const seen = new Set<string>();
  const sources = value.sources.map((source, index) => {
    const key = `sources.${index}.url`;
    const url = text(key, source.url, contentInputLimits.url, true);
    const title = text(`sources.${index}.title`, source.title, contentInputLimits.title) || null;
    if (!errors[key]) {
      try {
        const parsed = new URL(url);
        if (!/^https?:\/\/[^/?#]+/i.test(url) || /\s|\\/.test(url) || !["http:", "https:"].includes(parsed.protocol) ||
          !parsed.hostname || parsed.username || parsed.password || parsed.port === "0" ||
          url.slice(url.indexOf("://") + 3).split(/[/?#]/)[0].includes("@")) throw new Error();
      } catch { errors[key] = "계정 정보가 없는 올바른 http 또는 https URL을 입력해 주세요."; }
    }
    if (seen.has(url)) errors[key] = "같은 출처 URL이 이미 있습니다.";
    seen.add(url);
    return { url, title };
  });
  return Object.keys(errors).length ? { errors } : { errors, input: { title, summary, body, sources } };
}

export function hasContentInputChanges(initial: ContentFormValue, current: ContentFormValue): boolean {
  return JSON.stringify(initial) !== JSON.stringify(current);
}
