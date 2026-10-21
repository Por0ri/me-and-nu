import {
  mockLoginProviderOptions,
  mockLoginResult,
} from "@/mocks/consumer";
import { ApiError, getSession, login, register, type RegisterInput } from "@/lib/api";
import { isConsumerApiMode } from "@/lib/consumer-api/mode";
import { isLocalDemoLoginAvailable } from "@/lib/consumer-api/local-demo";
import type {
  LoginProviderOption,
  LoginRequest,
  LoginResult,
} from "@/types/consumer";

// Credentials stay in memory only. Failed requests reuse this account so retries
// do not create another user when a prior response was lost.
let pendingDemoAccount: { input: RegisterInput; registered: boolean } | null = null;
let pendingDemoLogin: Promise<LoginResult> | null = null;

async function startLocalDemo(providerId: string): Promise<LoginResult> {
  if (!pendingDemoAccount) {
    const suffix = window.crypto.randomUUID().replaceAll("-", "");
    pendingDemoAccount = {
      input: {
        email: `consumer-demo-${suffix}@example.com`,
        password: `Demo!${window.crypto.randomUUID()}A9`,
        nickname: `시연사용자${suffix.slice(0, 8)}`,
        role: "consumer",
      },
      registered: false,
    };
  }
  const account = pendingDemoAccount;
  if (!account.registered) {
    try {
      await register(account.input);
    } catch (error) {
      // A previous successful registration may have lost its response.
      if (!(error instanceof ApiError) || error.status !== 409 || error.code !== "EMAIL_ALREADY_REGISTERED") throw error;
    }
    account.registered = true;
  }
  await login({ email: account.input.email, password: account.input.password });
  const session = await getSession();
  if (!session.authenticated || session.sessionState !== "onboarding_pending" || session.onboardingCompleted) {
    throw new Error("새 시연 계정의 온보딩 세션을 확인할 수 없습니다. 로컬 인증 우회 설정을 확인해 주세요.");
  }
  pendingDemoAccount = null;
  // providerId selects the demonstration card only. Backend auth remains local.
  return { authStatus: "authenticated", signupStatus: "new", providerId };
}

export async function getLoginOptions(): Promise<LoginProviderOption[]> {
  if (isConsumerApiMode) {
    return isLocalDemoLoginAvailable()
      ? [
          { id: "kakao", label: "카카오로 시연 시작" },
          { id: "naver", label: "네이버로 시연 시작" },
        ]
      : [];
  }
  return structuredClone(mockLoginProviderOptions);
}

export async function loginWithProvider({
  providerId,
}: LoginRequest): Promise<LoginResult> {
  const providerExists = mockLoginProviderOptions.some(
    (provider) => provider.id === providerId,
  );

  if (!providerExists) {
    throw new Error(`Unknown login provider: ${providerId}`);
  }

  if (isConsumerApiMode) {
    if (!isLocalDemoLoginAvailable()) {
      throw new Error("로컬 시연 로그인이 비활성화되어 있습니다. 실제 소셜 로그인은 아직 연결되지 않았습니다.");
    }
    if (!pendingDemoLogin) {
      pendingDemoLogin = startLocalDemo(providerId).finally(() => {
        pendingDemoLogin = null;
      });
    }
    return pendingDemoLogin;
  }

  return {
    ...structuredClone(mockLoginResult),
    providerId,
  };
}
