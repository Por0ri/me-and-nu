"use client";

import styles from "./chat.module.css";

/** 하단 마스코트 → 펼친 안내 바 (홈 · 상세 공용).
 *  누르면 흰 알약이 양옆으로 길게 펼쳐지고, 마스코트 동그라미는 같은 크기 그대로 오른쪽 끝으로 옮겨 가며 얼굴이 생긴다.
 *  알약 높이는 48px. 놓이는 곳(부모)은 하단바 바로 위의 48px 칸(chat.module.css .dockLayer)이다. */
export function ChatDock({ expanded, onExpandedChange, prompt, onOpen, label, offFace, opensDialog = true, home = false }: {
  expanded: boolean;
  onExpandedChange: (expanded: boolean) => void;
  /** 펼친 바 문구 */
  prompt: string;
  /** 펼친 바를 눌렀을 때 (대화 시트 열기, 또는 준비 중 안내) */
  onOpen: () => void;
  /** 마스코트 버튼 이름 */
  label: string;
  /** 얼굴 없는 마스코트 그림 */
  offFace: string;
  /** 바를 누르면 대화 시트가 열리는지 (시연 화면은 준비 중 안내) */
  opensDialog?: boolean;
  /** 홈 표시 (기존 data-home-ai 를 그대로 단다) */
  home?: boolean;
}) {
  return <>
    <div className={styles.dockPill} data-expanded={expanded || undefined} inert={!expanded || undefined}>
      <button type="button" className={styles.dockPrompt} aria-haspopup={opensDialog ? "dialog" : undefined} aria-disabled={opensDialog ? undefined : "true"}
        onClick={onOpen}><span>{prompt}</span></button>
    </div>
    <button type="button" className={styles.dockFace} aria-label={label} aria-expanded={expanded} data-home-ai={home ? "" : undefined}
      onClick={() => onExpandedChange(!expanded)}>
      <img key={expanded ? "on" : "off"} src={expanded ? "/pivot/detail/ai-expanded.svg" : offFace} alt="" data-face={expanded ? "on" : "off"} />
    </button>
  </>;
}
