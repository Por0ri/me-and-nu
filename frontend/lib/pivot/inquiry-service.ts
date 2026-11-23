import { getSession, submitInquiry, type Session } from "@/lib/api";
import { isActiveSession, requirePivotApi } from "./services";
import { InquirySessionChanged, inquiryValidation, type InquirySource } from "./inquiry";
import { getOwnInquiries, getOwnInquiry } from "./inquiry-history";
import { withRequestDeadline } from "@/lib/request-deadline";

export function createServiceInquirySource(
  readSession: () => Session | null,
  api = { getSession, submitInquiry },
  guard: () => void = requirePivotApi,
): InquirySource {
  const owner = readSession(), userId = owner?.user?.id;
  function assertCurrent() {
    if (!isActiveSession(owner) || readSession() !== owner || owner?.user?.id !== userId) throw new InquirySessionChanged();
  }
  async function read<T>(load: (signal: AbortSignal) => Promise<T>, signal: AbortSignal): Promise<T> {
    guard(); signal.throwIfAborted(); assertCurrent();
    return withRequestDeadline(async requestSignal => {
      const session = await api.getSession({ fresh: true, signal: requestSignal });
      requestSignal.throwIfAborted(); assertCurrent();
      if (!isActiveSession(session) || session.user?.id !== userId) throw new InquirySessionChanged();
      const result = await load(requestSignal);
      requestSignal.throwIfAborted(); assertCurrent();
      return result;
    }, { signal });
  }
  return {
    kind: "service", assertCurrent,
    history: {
      list: (page, signal) => read(requestSignal => getOwnInquiries(page, requestSignal, userId), signal),
      detail: (id, signal) => read(requestSignal => getOwnInquiry(id, requestSignal, userId), signal),
    },
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
