import type { ConsentInput, Policy } from "@/lib/api";

export type ConsentSelection = Readonly<Record<string, boolean>>;
export type ConsentDraft = { consents: ConsentInput[] };

export const policyKey = (policy: Pick<Policy, "type" | "policyVersion">) =>
  JSON.stringify([policy.type, policy.policyVersion]);

/** Fail closed: never silently omit a malformed or duplicate server policy. */
export function validatePolicies(value: unknown): Policy[] {
  if (!Array.isArray(value) || value.length === 0) throw new Error("현재 약관 목록이 비어 있습니다.");
  const types = new Set<string>();
  for (const item of value) {
    if (!item || typeof item !== "object" ||
      typeof item.type !== "string" || !item.type.trim() || item.type.length > 50 ||
      typeof item.policyVersion !== "string" || !item.policyVersion.trim() || item.policyVersion.length > 50 ||
      typeof item.required !== "boolean" || typeof item.text !== "string" || !item.text.trim() ||
      types.has(item.type)) {
      throw new Error("약관 정보가 올바르지 않습니다. 다시 불러와 주세요.");
    }
    types.add(item.type);
  }
  return value.map((item) => ({ ...item }));
}

export function reconcileSelection(policies: Policy[], previous: ConsentSelection): ConsentSelection {
  return Object.fromEntries(policies.map((policy) => [policyKey(policy), previous[policyKey(policy)] === true]));
}
export function selectAll(policies: Policy[], agreed: boolean): ConsentSelection {
  return Object.fromEntries(policies.map((policy) => [policyKey(policy), agreed]));
}
export function consentSummary(policies: Policy[], selection: ConsentSelection) {
  const count = policies.filter((policy) => selection[policyKey(policy)] === true).length;
  return {
    all: policies.length > 0 && count === policies.length,
    partial: count > 0 && count < policies.length,
    canContinue: policies.length > 0 && policies.every((policy) => !policy.required || selection[policyKey(policy)] === true),
  };
}
export function createConsentDraft(policies: Policy[], selection: ConsentSelection): ConsentDraft {
  const valid = validatePolicies(policies);
  if (!consentSummary(valid, selection).canContinue) throw new Error("필수 약관에 동의해 주세요.");
  return { consents: valid.map(({ type, policyVersion }) => ({
    type, policyVersion, agreed: selection[policyKey({ type, policyVersion })] === true,
  })) };
}

const labels: Record<string, string> = {
  terms: "서비스 설명 및 이용약관", privacy: "개인정보 수집 및 이용동의",
  advertising: "광고 수신 동의", marketing: "마케팅 수신 동의",
};
export const policyLabel = (policy: Policy) => Object.hasOwn(labels, policy.type) ? labels[policy.type] : policy.type;
