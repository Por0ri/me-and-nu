"use client";
// My Page 프로필 수정 screen (2026-11 design), apart from loading and saving (profile-edit-route.tsx) so tests can render it.
import { useState, type FormEvent } from "react";
import { NICKNAME_TAKEN_MESSAGE } from "@/lib/pivot/onboarding-service";
import type { EditableProfile } from "@/lib/pivot/profile-edit";
import { HomeHeader } from "./home-screen";
import { NicknameField, useNicknameStatus, type NicknameCheckRequest } from "./nickname-field";
import { PrimaryButton } from "./pivot-ui";
import homeStyles from "./home.module.css";
import onboardingStyles from "./onboarding.module.css";
import styles from "./profile-edit.module.css";

/** 저장하기 turns on only for a changed nickname that passes the onboarding rule and duplicate check. */
export function ProfileEditScreen({ profile, onBack, onNotifications, onSave, onType, saving = false, message = "", check }: {
  profile: EditableProfile; onBack: () => void; onNotifications?: () => void; onSave: (nickname: string) => void; onType?: () => void;
  saving?: boolean; message?: string; check?: NicknameCheckRequest;
}) {
  const [typed, setTyped] = useState(profile.nickname);
  const status = useNicknameStatus(typed, check, profile.nickname);
  const ready = typed !== profile.nickname && status.ready && !saving;
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (ready) onSave(typed);
  }
  return <main className={`${homeStyles.screen} ${styles.screen}`} data-profile-edit="">
    <HomeHeader title="프로필 수정" showMascot={false} onBack={onBack} onNotifications={onNotifications} />
    <form className={styles.content} onSubmit={submit} noValidate>
      <h2 className={styles.title}>프로필에 표시되는 닉네임을 변경할 수 있어요</h2>
      <div className={`${onboardingStyles.profileField} ${styles.card}`}>
        <div className={styles.who}><p className={styles.name}>{profile.nickname}</p><p className={styles.email}>{profile.maskedEmail}</p></div>
        <hr className={styles.divider} />
        <NicknameField id="profile-nickname" value={typed} onChange={(value) => { setTyped(value); onType?.(); }}
          breaks={status.breaks} error={status.taken ? NICKNAME_TAKEN_MESSAGE : message} />
      </div>
      <div className={styles.actions}><PrimaryButton type="submit" disabled={!ready}>{saving ? "저장하고 있어요…" : "저장하기"}</PrimaryButton></div>
    </form>
  </main>;
}
