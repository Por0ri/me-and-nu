import { ApiError, getSession, submitReport, type Session } from "@/lib/api";
import { isActiveSession, requirePivotApi } from "./services";
import { ReportBlocked, ReportPreflightFailed, ReportSessionChanged, reportValidation, validReportReceipt, type ReportSource } from "./report";

export function createServiceReportSource(readSession: () => Session | null, api = { getSession, submitReport }, guard: () => void = requirePivotApi): ReportSource {
  const owner = readSession(), userId = owner?.user?.id;
  // Session-scoped tombstones survive closing/reopening DETAIL. No report text is stored.
  const outcomes = new Map<number, "duplicate" | "uncertain">();
  const pending = new Set<number>();
  function assertCurrent() {
    if (!isActiveSession(owner) || readSession() !== owner || owner?.user?.id !== userId) throw new ReportSessionChanged();
  }
  return {
    kind: "service", assertCurrent,
    async submit(input, signal) {
      guard(); signal.throwIfAborted(); assertCurrent();
      if (input.targetType !== "content" || !Number.isSafeInteger(input.targetId) || input.targetId <= 0 || reportValidation({ ...input, detail: input.detail ?? "" })) throw new Error("Invalid report input");
      const previous = outcomes.get(input.targetId);
      if (previous || pending.has(input.targetId)) throw new ReportBlocked(previous ?? "uncertain");
      pending.add(input.targetId);
      let sent = false;
      try {
        let session: Session;
        try { session = await api.getSession({ signal }); } catch (error) {
          if (error instanceof ApiError && error.status === 401) throw error;
          throw new ReportPreflightFailed();
        }
        signal.throwIfAborted(); assertCurrent();
        if (!isActiveSession(session) || session.user?.id !== userId || !session.csrfToken) throw new ReportSessionChanged();
        const detail = input.detail?.trim();
        // Abort/lost response after dispatch cannot prove that the server rolled back.
        sent = true; outcomes.set(input.targetId, "uncertain");
        const result = await api.submitReport({ targetType: "content", targetId: input.targetId, reasonCode: input.reasonCode, ...(detail ? { detail } : {}) }, {
          signal, headers: { "X-CSRF-Token": session.csrfToken },
        });
        signal.throwIfAborted(); assertCurrent();
        if (!validReportReceipt(result)) throw new ApiError("Invalid report response", 201, "INVALID_RESPONSE");
        outcomes.set(input.targetId, "duplicate");
        return result;
      } catch (error) {
        if (sent && !signal.aborted && error instanceof ApiError && error.status >= 400 && error.status < 500 && error.status !== 408 && error.code !== "INVALID_RESPONSE") {
          if (error.status === 409) outcomes.set(input.targetId, "duplicate");
          else outcomes.delete(input.targetId);
        }
        throw error;
      } finally { pending.delete(input.targetId); }
    },
  };
}
