import { apiRequest, ApiError } from "./api";
import type { AdminFailure } from "./admin-content";
import { withRequestDeadline } from "./request-deadline";

export const record = (value: unknown): value is Record<string, unknown> => !!value && typeof value === "object" && !Array.isArray(value);
export const integer = (value: unknown, min = 0): value is number => typeof value === "number" && Number.isSafeInteger(value) && value >= min;
export const decimalId = (value: unknown): value is string => typeof value === "string" && /^[1-9]\d{0,18}$/.test(value) && BigInt(value) <= BigInt("9223372036854775807");
export const nullableText = (value: unknown): value is string | null => value === null || typeof value === "string";
export const timestamp = (value: unknown): value is string => typeof value === "string" && /^\d{4}-\d{2}-\d{2}T/.test(value) && Number.isFinite(Date.parse(value));
export const nullableTimestamp = (value: unknown): value is string | null => value === null || timestamp(value);
export const textArray = (value: unknown): value is string[] => Array.isArray(value) && value.every(item => typeof item === "string");
export function invalidAdminResponse(): never { throw new ApiError("관리자 조회 응답을 확인하지 못했습니다.", 200, "INVALID_ADMIN_RESPONSE"); }
export function privateSearchHeaders(search: string): HeadersInit {
  if (search.length > 300 || /[\u0000-\u001f\u007f]/.test(search)) throw new Error("검색어를 확인해 주세요.");
  return search.trim() ? { "X-Admin-Search": encodeURIComponent(search.trim()) } : {};
}
export function adminReadRequest(path: string, init: Pick<RequestInit, "signal" | "headers"> = {}): Promise<unknown> {
  return withRequestDeadline(signal => apiRequest<unknown>(path, { ...init, signal }, 200), { signal: init.signal });
}
export function memberSupportFailure(error: unknown): AdminFailure {
  if (error instanceof ApiError) {
    if (error.status === 401) return { status: "login-required", message: "다시 로그인한 후 관리자 조회 권한을 확인해 주세요." };
    if (error.status === 403) return { status: "forbidden", message: "이 정보를 조회할 관리자 권한이 없습니다." };
    if (error.status === 404 && ["MEMBER_NOT_FOUND", "ADMIN_REPORT_NOT_FOUND", "ADMIN_INQUIRY_NOT_FOUND"].includes(error.code)) return { status: "not-found", message: "해당 항목이 존재하지 않거나 더 이상 조회할 수 없습니다." };
    if (error.status === 404 || error.status === 503) return { status: "unavailable", message: "조회 서비스를 이용할 수 없습니다. 잠시 후 다시 확인해 주세요." };
  }
  return { status: "error", message: "정보를 불러오지 못했습니다. 잠시 후 다시 시도해 주세요." };
}
