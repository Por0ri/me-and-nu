"use client";

import { useId, useState } from "react";
import { contentFormValue, contentInputLimits, hasContentInputChanges, validateContentInput } from "@/lib/admin-content-input";
import type { AdminContentDetail } from "@/lib/admin-content";
import { useContentInputGuard } from "./use-content-input-guard";
import styles from "./admin.module.css";

export function ContentForm({ detail, active }: { detail?: AdminContentDetail; active: boolean }) {
  const id = useId();
  const [initial] = useState(() => contentFormValue(detail));
  const [value, setValue] = useState(initial);
  const [checked, setChecked] = useState(false);
  const [checkedValue, setCheckedValue] = useState("");
  const dirty = hasContentInputChanges(initial, value);
  const validation = validateContentInput(value);
  const errors = checked ? validation.errors : {};
  const verified = checkedValue === JSON.stringify(value) && !!validation.input;
  useContentInputGuard(dirty);
  const update = (field: "title" | "summary" | "body", text: string) => setValue(current => ({ ...current, [field]: text }));
  const updateSource = (index: number, field: "url" | "title", text: string) => setValue(current => ({ ...current,
    sources: current.sources.map((source, at) => at === index ? { ...source, [field]: text } : source) }));
  const errorProps = (field: string) => ({ "aria-invalid": !!errors[field], "aria-describedby": errors[field] ? `${id}-${field}-error` : undefined });
  const error = (field: string) => errors[field] ? <p id={`${id}-${field}-error`} className={styles.fieldError}>{errors[field]}</p> : null;
  function check(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!active) return;
    setChecked(true);
    setCheckedValue(JSON.stringify(value));
    const first = Object.keys(validation.errors)[0];
    if (first) event.currentTarget.querySelector<HTMLElement>(`[name="${first}"]`)?.focus();
  }
  return <form className={styles.contentForm} noValidate onSubmit={check} data-admin-form={detail ? "edit" : "create"}>
    <p className={styles.inputNotice}>저장 기능을 준비 중입니다. 입력 내용은 서버에 저장되지 않습니다.</p>
    <fieldset disabled={!active}>
      <legend className={styles.panelTitle}>기본 정보</legend>
      {(["title", "summary", "body"] as const).map(field => <div className={styles.formField} key={field}>
        <label htmlFor={`${id}-${field}`}>{({ title: "제목", summary: "요약", body: "본문" })[field]}{field === "title" ? " (필수)" : " (선택)"}</label>
        {field === "title" ? <input id={`${id}-${field}`} name={field} required value={value[field]} {...errorProps(field)} onChange={event => update(field, event.target.value)} />
          : <textarea id={`${id}-${field}`} name={field} rows={field === "body" ? 10 : 3} value={value[field]} {...errorProps(field)} onChange={event => update(field, event.target.value)} />}
        <p className={styles.fieldHint}>{contentInputLimits[field].toLocaleString("ko-KR")}자 이하</p>{error(field)}
      </div>)}
    </fieldset>
    <fieldset disabled={!active}>
      <legend className={styles.panelTitle}>출처 (선택)</legend>
      <p className={styles.note}>출처를 추가하면 URL은 필수입니다. 출처 제목은 생략할 수 있습니다.</p>
      {value.sources.map((source, index) => <fieldset key={index} className={styles.sourceInput}>
        <legend>출처 {index + 1}</legend>
        {(["url", "title"] as const).map(field => {
          const name = `sources.${index}.${field}`;
          return <div className={styles.formField} key={field}>
            <label htmlFor={`${id}-${name}`}>출처 {index + 1} {field === "url" ? "URL (필수)" : "제목 (선택)"}</label>
            <input id={`${id}-${name}`} name={name} type={field === "url" ? "url" : "text"} required={field === "url"}
              value={source[field]} {...errorProps(name)} onChange={event => updateSource(index, field, event.target.value)} />{error(name)}
          </div>;
        })}
        <button type="button" className={styles.secondaryButton} aria-label={`출처 ${index + 1} 삭제`}
          onClick={() => setValue(current => ({ ...current, sources: current.sources.filter((_, at) => at !== index) }))}>출처 삭제</button>
      </fieldset>)}
      {error("sources")}
      <button type="button" className={styles.secondaryButton} disabled={value.sources.length >= contentInputLimits.sources}
        onClick={() => setValue(current => ({ ...current, sources: [...current.sources, { url: "", title: "" }] }))}>출처 추가</button>
    </fieldset>
    <div className={styles.formActions}>
      <button type="submit" className={styles.secondaryButton} disabled={!active}>입력 확인</button>
      <button type="button" className={styles.primaryButton} disabled aria-describedby={`${id}-save-status`}>저장 준비 중</button>
    </div>
    <p id={`${id}-save-status`} role="status" aria-live="polite" className={styles.note}>
      {!active ? "접근 권한을 다시 확인하고 있습니다." : checked && !validation.input ? "입력 오류를 확인해 주세요. 아직 저장되지 않았습니다."
        : verified ? "입력 확인을 마쳤습니다. 아직 저장되지 않았습니다."
          : dirty ? "수정 중 · 저장되지 않은 변경 내용이 있습니다." : "저장 기능 연결 전까지 입력 내용을 저장할 수 없습니다."}
    </p>
  </form>;
}
