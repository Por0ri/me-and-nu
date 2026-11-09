// FE display model only. This is not an agreed server DTO or API contract.
export type MyPageProfile = { origin: "demo" | "service"; nickname: string; maskedEmail: string };
export type MyPageResult =
  | { status: "ready"; profile: MyPageProfile }
  | { status: "unconfigured"; message: string };
export type MyPageState = MyPageResult | { status: "loading" } | { status: "error"; message: string };
export type MyPageDataSource = {
  kind: "demo" | "service";
  load(options: { signal: AbortSignal }): Promise<MyPageResult>;
};

export function maskProfileEmail(email: unknown): string {
  const unavailable = "이메일 정보를 확인할 수 없어요";
  if (typeof email !== "string" || email.length > 254) return unavailable;
  const [local, domain, extra] = email.split("@");
  if (!local || local.length > 64 || !domain || extra !== undefined
    || !/^[a-zA-Z0-9!#$%&'*+/=?^_`{|}~.-]+$/.test(local)
    || local.startsWith(".") || local.endsWith(".") || local.includes("..")) return unavailable;
  const labels = domain.split(".");
  if (labels.length < 2 || labels.some((label) => !/^[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?$/.test(label))) {
    return unavailable;
  }
  // Even a one-character local part must not be exposed in full.
  return `${local.slice(0, Math.min(4, local.length - 1))}***@${domain}`;
}

export function createMyPageRequestController(source: MyPageDataSource, publish: (state: MyPageState) => void) {
  let active: AbortController | undefined;
  return {
    async load() {
      active?.abort();
      const request = new AbortController(); active = request;
      publish({ status: "loading" });
      try {
        const result = await source.load({ signal: request.signal });
        if (request.signal.aborted) return;
        // Never substitute a Mock identity for an unavailable/failed Service.
        if (result.status === "ready" && (result.profile.origin !== source.kind
          || !result.profile.nickname.trim() || !result.profile.maskedEmail.trim())) throw new Error("Invalid profile source");
        publish(result);
      } catch {
        if (!request.signal.aborted) publish({ status: "error", message: "프로필을 불러오지 못했어요. 다시 시도해주세요." });
      }
    },
    cancel() { active?.abort(); },
  };
}
