"use client";

import { consumerRoutes } from "@/lib/consumer-routes";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";

import { useConsumerSession } from "@/components/providers/consumer-session-provider";
import { getLoginOptions, loginWithProvider } from "@/lib/consumer-api/auth";
import type { LoginProviderOption } from "@/types/consumer";

const LOGIN_OPTIONS_ERROR_MESSAGE =
  "로그인 방법을 불러오지 못했습니다. 다시 시도해 주세요.";
const LOGIN_ERROR_MESSAGE = "로그인에 실패했습니다. 다시 시도해 주세요.";

export function LoginScreen() {
  const router = useRouter();
  const { setSession } = useConsumerSession();
  const [providers, setProviders] = useState<LoginProviderOption[]>([]);
  const [isLoadingProviders, setIsLoadingProviders] = useState(true);
  const [providersError, setProvidersError] = useState<string | null>(null);
  const [pendingProviderId, setPendingProviderId] = useState<string | null>(null);
  const [loginError, setLoginError] = useState<string | null>(null);
  const isLoginRequestPending = useRef(false);

  useEffect(() => {
    let isCancelled = false;

    async function loadLoginOptions() {
      try {
        const loginOptions = await getLoginOptions();

        if (!isCancelled) {
          setProviders(loginOptions);
        }
      } catch {
        if (!isCancelled) {
          setProvidersError(LOGIN_OPTIONS_ERROR_MESSAGE);
        }
      } finally {
        if (!isCancelled) {
          setIsLoadingProviders(false);
        }
      }
    }

    void loadLoginOptions();

    return () => {
      isCancelled = true;
    };
  }, []);

  async function retryLoginOptions() {
    setIsLoadingProviders(true);
    setProvidersError(null);

    try {
      const loginOptions = await getLoginOptions();
      setProviders(loginOptions);
    } catch {
      setProviders([]);
      setProvidersError(LOGIN_OPTIONS_ERROR_MESSAGE);
    } finally {
      setIsLoadingProviders(false);
    }
  }

  async function handleLogin(providerId: string) {
    if (isLoginRequestPending.current) {
      return;
    }

    isLoginRequestPending.current = true;
    setPendingProviderId(providerId);
    setLoginError(null);

    try {
      const loginResult = await loginWithProvider({ providerId });
      setSession(loginResult);
      router.push(consumerRoutes.onboarding);
    } catch {
      isLoginRequestPending.current = false;
      setLoginError(LOGIN_ERROR_MESSAGE);
      setPendingProviderId(null);
    }
  }

  const isLoginPending = pendingProviderId !== null;

  return (
    <main className="flex flex-1 items-center justify-center px-6 py-12">
      <section
        aria-labelledby="login-title"
        className="w-full max-w-sm space-y-6 rounded-2xl border border-black/10 bg-white p-8 shadow-sm"
      >
        <div className="space-y-2 text-center">
          <h1 id="login-title" className="text-2xl font-semibold text-black">
            로그인
          </h1>
          <p className="text-sm text-black/60">
            관심사에 맞춘 콘텐츠를 시작해 보세요.
          </p>
        </div>

        {isLoadingProviders ? (
          <p role="status" className="text-center text-sm text-black/60">
            로그인 방법을 불러오는 중입니다.
          </p>
        ) : providersError ? (
          <div className="space-y-3 text-center">
            <p role="alert" className="text-sm text-red-600">
              {providersError}
            </p>
            <button
              type="button"
              onClick={() => void retryLoginOptions()}
              className="w-full rounded-lg border border-black/20 px-4 py-3 text-sm font-medium text-black"
            >
              다시 시도
            </button>
          </div>
        ) : providers.length === 0 ? (
          <p role="status" className="text-center text-sm text-black/60">
            사용 가능한 로그인 방법이 없습니다.
          </p>
        ) : (
          <div className="space-y-3">
            {providers.map((provider) => (
              <button
                key={provider.id}
                type="button"
                disabled={isLoginPending}
                onClick={() => void handleLogin(provider.id)}
                className="w-full rounded-lg bg-black px-4 py-3 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-60"
              >
                {pendingProviderId === provider.id
                  ? "로그인 중..."
                  : provider.label}
              </button>
            ))}
          </div>
        )}

        {loginError ? (
          <p role="alert" className="text-center text-sm text-red-600">
            {loginError}
          </p>
        ) : null}
      </section>
    </main>
  );
}
