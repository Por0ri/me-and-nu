"use client";
// My Page 프로필 수정 (2026-11 design): load the profile, save the nickname, go back to My Page.
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { ApiError } from "@/lib/api";
import { NICKNAME_RULE, NICKNAME_TAKEN_MESSAGE } from "@/lib/pivot/onboarding-service";
import { checkMyNickname, loadEditableProfile, saveMyNickname, type EditableProfile } from "@/lib/pivot/profile-edit";
import { isActiveSession } from "@/lib/pivot/services";
import { usePivotSession } from "./pivot-session";
import { PivotBrand, PivotFrame } from "./pivot-ui";
import { ProfileEditScreen } from "./profile-edit-screen";
import pivotStyles from "./pivot.module.css";

const MY_PAGE = "/pivot/home?view=my";

export function ProfileEditRoute() {
  const router = useRouter();
  const { state: session, refresh } = usePivotSession();
  const active = isActiveSession(session.session);
  const [profile, setProfile] = useState<EditableProfile | null>(null);
  const [failed, setFailed] = useState(false);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => {
    if (!active) return;
    const abort = new AbortController();
    loadEditableProfile(abort.signal).then(setProfile).catch((reason) => {
      if (abort.signal.aborted) return;
      if (reason instanceof ApiError && reason.status === 401) { refresh(); router.replace("/pivot/login"); return; }
      setFailed(true);
    });
    return () => abort.abort();
  }, [active, refresh, router]);
  async function save(nickname: string) {
    if (saving) return;
    setSaving(true); setMessage("");
    try {
      await saveMyNickname(nickname);
      if (mounted.current) router.replace(MY_PAGE);
    } catch (reason) {
      if (!mounted.current) return;
      if (reason instanceof ApiError && reason.status === 401) { refresh(); router.replace("/pivot/login"); return; }
      // Someone took the name after the check, or the server's rule failed: the same red line as onboarding.
      setMessage(reason instanceof ApiError && reason.code === "NICKNAME_TAKEN" ? NICKNAME_TAKEN_MESSAGE
        : reason instanceof ApiError && reason.status === 422 ? NICKNAME_RULE : "닉네임을 저장하지 못했어요. 잠시 후 다시 시도해주세요.");
    } finally {
      if (mounted.current) setSaving(false);
    }
  }
  if (active && profile) return <ProfileEditScreen profile={profile} onBack={() => router.replace(MY_PAGE)} onSave={(nickname) => void save(nickname)}
    onType={() => setMessage("")} saving={saving} message={message} check={checkMyNickname} />;
  if (session.status === "loading" || (active && !failed)) return <PivotFrame decorated={false}>{null}</PivotFrame>;
  return <PivotFrame>
    <PivotBrand screen="consent" className={pivotStyles.consentBrand} />
    <section className={pivotStyles.gate}><h1>프로필 수정</h1>
      <p role={failed ? "alert" : "status"}>{failed ? "프로필을 불러오지 못했어요. 잠시 후 다시 시도해주세요." : "로그인한 뒤 프로필을 수정할 수 있어요."}</p>
      <Link href={MY_PAGE}>마이페이지로 돌아가기</Link>
    </section>
  </PivotFrame>;
}
