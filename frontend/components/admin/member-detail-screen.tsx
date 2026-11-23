"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import type { AdminReadState } from "@/lib/admin-content";
import { loadAdminMember, memberDate, memberProviders, memberQueryString, parseMemberQuery, privateEmail, type MemberDetail } from "@/lib/admin-members";
import { AdminStatus } from "./admin-status";
import { MemberBadge } from "./members-screen";
import { useAdminRead } from "./use-admin-read";
import { memberSupportFailure } from "@/lib/admin-read-contract";
import shared from "./admin.module.css";
import styles from "./members.module.css";

export function MemberDetailRoute({ memberId, state }: { memberId: string; state?: AdminReadState<MemberDetail> }) {
  const params = useSearchParams();
  let back = "/admin/users";
  try { back += `?${memberQueryString(parseMemberQuery(params))}`; } catch { /* Only local, allowlisted list context. */ }
  return state ? <MemberDetailScreen key={state.status === "ready" ? state.data.id : state.status} state={state} back={back} />
    : <ConnectedMemberDetail key={memberId} memberId={memberId} back={back} />;
}

function ConnectedMemberDetail({ memberId, back }: { memberId: string; back: string }) {
  const read = useCallback((signal: AbortSignal) => loadAdminMember(memberId, signal), [memberId]);
  const { state, retry } = useAdminRead(memberId, read, memberSupportFailure);
  return <MemberDetailScreen key={state.status} state={state} back={back} retry={retry} />;
}

function Tags({ values }: { values?: string[] }) {
  return values ? values.length ? <div className={styles.tags}>{values.map(value => <span key={value}>{value}</span>)}</div> : <span className={styles.missing}>응답 없음</span> : <span className={styles.missing}>해당 문항 답변 미수집</span>;
}

export function MemberDetailScreen({ state, back, retry }: { state: AdminReadState<MemberDetail>; back: string; retry?: () => void }) {
  const member = state.status === "ready" ? state.data : null;
  const history = member?.history;
  const latest = history?.[0];
  const dialog = useRef<HTMLDialogElement>(null);
  const [historyOpen, setHistoryOpen] = useState(false);
  useEffect(() => {
    if (!historyOpen) return;
    const panel = dialog.current;
    panel?.showModal();
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => { panel?.close(); document.body.style.overflow = overflow; };
  }, [historyOpen]);
  return <div className={styles.detail} data-member-detail={state.status}>
    <Link className={styles.back} href={back}><img src="/admin/member-back.svg" alt="" />회원 목록으로</Link>
    <div className={styles.identity}><h1>{member?.nickname ?? "회원 상세"}</h1><MemberBadge status={member?.status ?? null} /></div>
    {state.status !== "ready" && <div className={styles.connection}><AdminStatus subject="회원" state={state} retry={retry} /></div>}
    <div className={styles.detailTop}>
      <div className={styles.leftColumn}>
        <section className={`${styles.card} ${styles.information}`} aria-labelledby="member-info-title"><h2 id="member-info-title">회원 정보</h2><dl>
          {[["이메일", privateEmail(member?.maskedEmail ?? null)], ["가입 경로", member?.provider ? memberProviders[member.provider] : "—"], ["도메인", member?.domain ?? "—"], ["가입일", memberDate(member?.joinedAt ?? null)], ["최근 로그인", memberDate(member?.lastLoginAt ?? null)]].map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}
        </dl></section>
        <section className={`${styles.card} ${styles.historySummary}`} aria-labelledby="member-history-title"><div className={styles.cardHeading}><h2 id="member-history-title">닉네임 변경 이력</h2>
          <button type="button" className={styles.smallButton} aria-haspopup="dialog" aria-controls="nickname-history" onClick={() => setHistoryOpen(true)}>전체 보기 <img src="/admin/member-forward.svg" alt="" /></button></div>
          {latest ? <><p>{latest.before} → {latest.after}</p><p className={styles.muted}>최근 변경일 | {memberDate(latest.changedAt)}</p></> : <p className={styles.missing}>{history ? "닉네임 변경 이력이 없습니다" : "변경 이력 미수집"}</p>}
          <span className={styles.count}>{history ? `총 ${history.length}회` : "총 —회"}</span>
        </section>
      </div>
      <section className={`${styles.card} ${styles.onboarding}`} aria-labelledby="member-onboarding-title"><span className={styles.domain}>{member?.domain ?? "분야 미연결"}</span><h2 id="member-onboarding-title">온보딩 답변</h2>
        <div className={styles.questions}><div><h3>Q1. 좋아하는 작품</h3><Tags values={member?.onboarding?.works} /></div>
          <div><h3>Q2. 장르와 소재</h3><div className={styles.tagRow}><span>장르</span><Tags values={member?.onboarding?.genres} /></div><div className={styles.tagRow}><span>소재</span><Tags values={member?.onboarding?.themes} /></div></div>
          <div><h3>Q3. 선호 콘텐츠 유형</h3><Tags values={member?.onboarding?.contentTypes} /></div></div>
      </section>
    </div>
    <div className={styles.detailBottom}>
      <section className={`${styles.card} ${styles.notes}`} aria-labelledby="member-notes-title"><div className={styles.cardHeading}><h2 id="member-notes-title">관리 메모</h2><button type="button" className={styles.smallButton} disabled title="관리 메모 쓰기 준비 중"><img src="/admin/memo-add.svg" alt="" />메모 작성</button></div>
        {member?.notes?.length ? member.notes.map(note => <div className={styles.memo} key={note.id}><div><p>{note.text}</p><div className={styles.memoActions}><button type="button" disabled aria-label="메모 수정 준비 중"><img src="/admin/memo-edit.svg" alt="" /></button><button type="button" disabled aria-label="메모 삭제 준비 중"><img src="/admin/memo-delete.svg" alt="" /></button></div></div><p className={styles.muted}>{note.author} | {memberDate(note.createdAt)}</p></div>) : <p className={styles.missing}>{member?.notes ? "등록된 메모가 없습니다" : "관리 메모 미수집"}</p>}
      </section>
      <section className={`${styles.card} ${styles.activity}`} aria-labelledby="member-activity-title"><h2 id="member-activity-title">회원 상태</h2><dl><div><dt>저장한 콘텐츠</dt><dd>{member?.savedCount ?? "—"}</dd></div><div><dt>좋아요한 콘텐츠</dt><dd>{member?.likedCount ?? "—"}</dd></div></dl></section>
    </div>
    <p className={styles.availability}>회원 정보·제재·삭제·메모 변경은 준비 중입니다. 미연결 정보의 — 표시는 0을 의미하지 않습니다.</p>
    <dialog ref={dialog} id="nickname-history" className={styles.drawer} aria-labelledby="nickname-history-title" aria-describedby="nickname-history-description" onClose={() => setHistoryOpen(false)}
      onKeyDown={event => { if (event.key === "Tab") { event.preventDefault(); event.currentTarget.querySelector<HTMLButtonElement>("button")?.focus(); } }}>
      <div className={styles.drawerHeading}><div><h2 id="nickname-history-title">닉네임 변경 이력</h2><p id="nickname-history-description">해당 회원의 닉네임 변경 내역을 확인할 수 있어요.</p></div><button type="button" autoFocus aria-label="닉네임 변경 이력 닫기" onClick={() => dialog.current?.close()}><img src="/admin/history-close.svg" alt="" /></button></div>
      <table><caption className={shared.srOnly}>닉네임 변경 일시와 이전·변경 닉네임</caption><thead><tr><th scope="col">변경 일시</th><th scope="col">변경 내용</th></tr></thead><tbody>{history?.map((item, index) => <tr key={`${item.changedAt}-${index}`}><td>{memberDate(item.changedAt)}</td><td><div className={styles.change}><span>{item.before}</span><img src="/admin/history-forward.svg" alt="→" /><span>{item.after}</span></div></td></tr>)}</tbody></table>
      {!history?.length && <p className={styles.drawerEmpty} role="status">{history ? "닉네임 변경 이력이 없습니다" : "닉네임 변경 이력이 수집되지 않았습니다."}</p>}
    </dialog>
  </div>;
}
