"use client";

import { useEffect, useRef, useState } from "react";
import { reportPeriods, type ReportPeriod } from "@/lib/admin-reports";
import styles from "./reports.module.css";

const periods = Object.keys(reportPeriods) as ReportPeriod[];

export function ReportPeriodFilter({ value, onChange }: { value: ReportPeriod; onChange: (value: ReportPeriod) => void }) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null), trigger = useRef<HTMLButtonElement>(null);
  const options = useRef<(HTMLButtonElement | null)[]>([]);
  useEffect(() => {
    if (!open) return;
    options.current[periods.indexOf(value)]?.focus();
    const outside = (event: PointerEvent) => { if (!root.current?.contains(event.target as Node)) setOpen(false); };
    document.addEventListener("pointerdown", outside);
    return () => document.removeEventListener("pointerdown", outside);
  }, [open, value]);
  return <div ref={root} className={styles.period} data-open={open} onBlur={event => { if (!event.currentTarget.contains(event.relatedTarget)) setOpen(false); }}
    onKeyDown={event => {
      if (event.key === "Escape" && open) { event.preventDefault(); setOpen(false); trigger.current?.focus(); }
      if (["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
        event.preventDefault();
        if (!open) { setOpen(true); return; }
        const index = options.current.findIndex(option => option === document.activeElement);
        const next = event.key === "Home" ? 0 : event.key === "End" ? periods.length - 1 : (index + (event.key === "ArrowDown" ? 1 : -1) + periods.length) % periods.length;
        options.current[next]?.focus();
      }
    }}>
    <button ref={trigger} type="button" className={styles.periodTrigger} aria-label={`조회 기간: ${reportPeriods[value]}`} aria-haspopup="menu" aria-expanded={open} aria-controls="report-period-options" onClick={() => setOpen(!open)}>
      {reportPeriods[value]}<span><img src="/admin/sort-down.svg" alt="" /></span>
    </button>
    {open && <div id="report-period-options" role="menu" aria-label="조회 기간" className={styles.periodOptions}>{periods.map((period, index) =>
      <button ref={node => { options.current[index] = node; }} key={period} type="button" role="menuitemradio" aria-checked={value === period} tabIndex={-1}
        onClick={() => { onChange(period); setOpen(false); trigger.current?.focus(); }}>{reportPeriods[period]}</button>)}</div>}
  </div>;
}
