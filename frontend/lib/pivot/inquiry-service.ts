import { getSession, submitInquiry, type Session } from "@/lib/api";
import { isActiveSession, requirePivotApi } from "./services";
import { InquirySessionChanged, inquiryValidation, type InquirySource } from "./inquiry";

export function createServiceInquirySource(
  readSession: () => Session | null,
  api = { getSession, submitInquiry },
  guard: () => void = requirePivotApi,
): InquirySource {
  const owner = readSession(), userId = owner?.user?.id;
  function assertCurrent() {
    if (!isActiveSession(owner) || readSession() !== owner || owner?.user?.id !== userId) throw new InquirySessionChanged();
  }
  return {
    kind: "service", assertCurrent,
    async submit(input, signal) {
      guard(); signal.throwIfAborted(); assertCurrent();
      const invalid = inquiryValidation(input);
      if (invalid) throw new Error(invalid);
      // Confirm the cookie owner before a write, and refresh expired CSRF on a manual retry.
      const session = await api.getSession();
      signal.throwIfAborted(); assertCurrent();
      if (!isActiveSession(session) || session.user?.id !== userId || !session.csrfToken) throw new InquirySessionChanged();
      const response = await api.submitInquiry({ ...input, detail: input.detail.trim() }, {
        signal, headers: { "X-CSRF-Token": session.csrfToken },
      });
      signal.throwIfAborted(); assertCurrent();
      return response;
    },
  };
}
