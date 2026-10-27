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

export function maskProfileEmail(email: string): string {
  const [local, domain, extra] = email.split("@");
  if (!local || !domain || extra !== undefined) return "이메일 표시 준비 중";
  return `${local.slice(0, Math.min(4, Math.max(1, local.length - 1)))}***@${domain}`;
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
