import {
  mockLoginProviderOptions,
  mockLoginResult,
} from "@/mocks/consumer";
import type {
  LoginProviderOption,
  LoginRequest,
  LoginResult,
} from "@/types/consumer";

export async function getLoginOptions(): Promise<LoginProviderOption[]> {
  return structuredClone(mockLoginProviderOptions);
}

export async function loginWithProvider({
  providerId,
}: LoginRequest): Promise<LoginResult> {
  const providerExists = mockLoginProviderOptions.some(
    (provider) => provider.id === providerId,
  );

  if (!providerExists) {
    throw new Error(`Unknown Mock login provider: ${providerId}`);
  }

  return {
    ...structuredClone(mockLoginResult),
    providerId,
  };
}
