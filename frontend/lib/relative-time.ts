/** "1시간 전"처럼 지금 기준 경과 시간을 짧게 표시한다. */
export function formatRelativeTime(iso: string | undefined, now = Date.now()): string | null {
  if (!iso) return null;
  const time = Date.parse(iso);
  if (Number.isNaN(time)) return null;

  const minutes = Math.max(0, Math.floor((now - time) / 60_000));
  if (minutes < 1) return "방금 전";
  if (minutes < 60) return `${minutes}분 전`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}시간 전`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days}일 전`;
  const date = new Date(time);
  return `${date.getFullYear()}.${String(date.getMonth() + 1).padStart(2, "0")}.${String(date.getDate()).padStart(2, "0")}`;
}

/** 알림함용: 24시간이 지나면 달력 날짜로 세어 전날은 "어제"로 표시한다. */
export function formatRelativeDay(iso: string | undefined, now = Date.now()): string | null {
  const text = formatRelativeTime(iso, now);
  if (!iso || !text?.endsWith("일 전")) return text;
  const midnight = (value: number) => new Date(value).setHours(0, 0, 0, 0);
  const days = Math.round((midnight(now) - midnight(Date.parse(iso))) / 86_400_000);
  return days === 1 ? "어제" : `${days}일 전`;
}
