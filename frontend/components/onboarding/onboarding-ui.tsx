import type { ReactNode } from "react";

export function OnboardingIcon({ name }: { name: string }) {
  return <img src={`/ui-onboarding/${name}.svg`} alt="" aria-hidden="true" />;
}

export function OnboardingBrand({ children }: { children: ReactNode }) {
  return (
    <header className="ob-brand">
      <p className="ob-wordmark">me;nu</p>
      {children}
    </header>
  );
}

export function OnboardingBackdrop() {
  return <div className="ob-glow" aria-hidden="true"><OnboardingIcon name="login-glow" /></div>;
}

export function OnboardingBack({ onBack, disabled = false }: { onBack: () => void; disabled?: boolean }) {
  return (
    <header className="ob-navigation">
      <button type="button" onClick={onBack} disabled={disabled} aria-label="이전 단계">
        <OnboardingIcon name="back" />
      </button>
    </header>
  );
}
