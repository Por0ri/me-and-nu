"use client";

import { consumerRoutes } from "@/lib/consumer-routes";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";

import { useConsumerSession } from "@/components/providers/consumer-session-provider";
import {
  OnboardingBackdrop,
  OnboardingBrand,
  OnboardingIcon,
} from "@/components/onboarding/onboarding-ui";
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
    <main className="ui-version-a ui-onboarding">
      <section
        aria-labelledby="login-title"
        className="va-shell ob-entry ob-login"
        aria-busy={isLoginPending}
      >
        <OnboardingBackdrop />
        <OnboardingBrand>
          <h1 id="login-title">나에게 맞는 새로운 소식</h1>
        </OnboardingBrand>
        <div className="ob-login-actions">
          <div className="ob-social-hint">
            <p>SNS로 간편하게 시작하기</p>
            <OnboardingIcon name="bubble-tip" />
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
            <div className="ob-social-buttons">
              {providers.map((provider) => (
                <button
                  key={provider.id}
                  type="button"
                  disabled={isLoginPending}
                  onClick={() => void handleLogin(provider.id)}
                  className={`ob-social-button ob-social-${provider.id}`}
                >
                  {provider.id === "kakao" ? (
                    <>
                      <span className="ob-kakao-background">
                        <OnboardingIcon name="kakao-background" />
                      </span>
                      <OnboardingIcon name="kakao-symbol" />
                    </>
                  ) : (
                    <span className="ob-naver-symbol" aria-hidden="true">
                      <img src="/ui-onboarding/naver-source.png" alt="" />
                    </span>
                  )}
                  <span>
                    {pendingProviderId === provider.id
                      ? "로그인 중..."
                      : provider.label}
                  </span>
                </button>
              ))}
            </div>
          )}

          {loginError ? (
            <p role="alert" className="text-center text-sm text-red-600">
              {loginError}
            </p>
          ) : null}
        </div>
      </section>
    </main>
  );
}
