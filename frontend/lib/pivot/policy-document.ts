import type { Policy } from "@/lib/api";

export type MyPagePolicyType = "terms" | "privacy";
export const myPagePolicyTitles = { terms: "이용약관", privacy: "개인정보처리방침" } as const;
export type PolicyContent = { kind: "text"; text: string } | { kind: "url"; url: string } | { kind: "unavailable" };

/** Only a complete, credential-free HTTP(S) URL is a link. Never interpret HTML. */
export function policyContent(text: string): PolicyContent {
  const value = text.trim();
  if (!value) return { kind: "unavailable" };
  if (/^https?:\/\//i.test(value)) {
    try {
      const url = new URL(value);
      if (/\s|[\\\u0000-\u001f\u007f]/.test(value) || !url.hostname || url.username || url.password) return { kind: "unavailable" };
      return { kind: "url", url: url.href };
    } catch { return { kind: "unavailable" }; }
  }
  // Reject unsupported schemes/relative links instead of presenting them as legal text.
  if (/^(?:[a-z][a-z\d+.-]*:\/\/|(?:javascript|vbscript|data|file|blob|ftp|mailto|tel|about):|\/|www\.)/i.test(value)) return { kind: "unavailable" };
  return { kind: "text", text };
}

export function isSamplePolicy(policy: Policy): boolean {
  return /(?:checking|test|local|preview|fixture|demo|sample|seed)/i.test(policy.policyVersion) ||
    /테스트용|테스트 안내|테스트 항목|로컬 개발 환경|디자인 확인용|실제.{0,20}(?:아닙니다|아니에요)|정식 서비스.{0,20}아닙니다/.test(policy.text);
}

export type PolicyPublication = Readonly<{ type: MyPagePolicyType; policyVersion: string; textSha256: string }>;
// Intentionally empty: Checking currently serves test notices. Add an entry only
// after operational approval AND deployed API verification, hashing exact API text
// (or the approved original URL). Figma versions alone are not publication approval.
const approvedPublications: readonly PolicyPublication[] = [];

export type PolicyDocumentState =
  | { status: "loading" }
  | { status: "ready"; policy: Policy; content: Exclude<PolicyContent, { kind: "unavailable" }> }
  | { status: "unavailable" | "error"; message: string };

export async function resolvePolicyDocument(policies: Policy[], type: MyPagePolicyType,
  publications: readonly PolicyPublication[] = approvedPublications): Promise<PolicyDocumentState> {
  const matches = policies.filter((policy) => policy.type === type);
  if (matches.length !== 1) return { status: "unavailable", message: "요청한 정책 문서를 확인할 수 없어요. 잠시 후 다시 확인해주세요." };
  const policy = matches[0];
  const publication = publications.find((item) => item.type === type && item.policyVersion === policy.policyVersion);
  if (isSamplePolicy(policy) || !publication) return {
    status: "unavailable", message: "공개가 확인된 정책 문서가 아직 없어요. 문서가 준비되면 여기에서 확인할 수 있어요.",
  };
  const content = policyContent(policy.text);
  if (content.kind === "unavailable") return { status: "unavailable", message: "정책 원문을 확인할 수 없어요. 잠시 후 다시 확인해주세요." };
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(policy.text));
  const hash = Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
  if (hash !== publication.textSha256) return { status: "unavailable", message: "정책 원문을 확인 중이에요. 잠시 후 다시 확인해주세요." };
  return { status: "ready", policy, content };
}
