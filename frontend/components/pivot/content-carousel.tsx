"use client";

import { useEffect, useId, useRef, useState, type CSSProperties, type PointerEvent } from "react";
import { API_UNAUTHORIZED_EVENT } from "@/lib/api";
import { orderedHomeSlides, type HomeSlideModel } from "@/lib/pivot/home";
import homeStyles from "./home.module.css";
import styles from "./content-carousel.module.css";

type ImageState = { status: "loading" } | { status: "ready"; url: string } | { status: "error"; message: string };

/** A single component-owned request: never cache authenticated pixels across users. */
export function requestProtectedImage(src: string, publish: (state: ImageState) => void): () => void {
  const controller = new AbortController();
  let disposed = false;
  let objectUrl: string | undefined;
  publish({ status: "loading" });
  void (async () => {
    try {
      const response = await fetch(src, {
        credentials: "include", cache: "no-store", redirect: "error", signal: controller.signal,
        headers: { Accept: "image/png" },
      });
      if (disposed) return;
      if (response.status === 401) {
        window.dispatchEvent(new Event(API_UNAUTHORIZED_EVENT));
        throw new Error("로그인이 만료되어 이미지를 불러올 수 없어요. 다시 로그인해 주세요.");
      }
      if (response.status === 404) throw new Error("현재 제공되지 않는 이미지예요. 콘텐츠가 만료되었을 수 있어요.");
      if (!response.ok) throw new Error("이미지를 불러오지 못했어요.");
      if (response.headers.get("content-type")?.split(";")[0].trim().toLowerCase() !== "image/png") {
        throw new Error("이미지 응답을 확인할 수 없어요.");
      }
      const blob = await response.blob();
      if (disposed) return;
      if (!blob.size || blob.size > 20 * 1024 * 1024) throw new Error("이미지 크기를 확인할 수 없어요.");
      objectUrl = URL.createObjectURL(blob);
      publish({ status: "ready", url: objectUrl });
    } catch (error) {
      if (!disposed && !controller.signal.aborted) {
        publish({ status: "error", message: error instanceof Error && error.message.endsWith("요.")
          ? error.message : "이미지를 불러오지 못했어요. 다시 시도해 주세요." });
      }
    }
  })();
  return () => {
    disposed = true;
    controller.abort();
    if (objectUrl) { URL.revokeObjectURL(objectUrl); objectUrl = undefined; }
  };
}

export function ProtectedSlideImage({ src, alt, enabled = true, retryable = true }: {
  src: string; alt: string; enabled?: boolean; retryable?: boolean;
}) {
  const [state, setState] = useState<ImageState>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);
  const frame = useRef<HTMLSpanElement>(null);
  const [nearViewport, setNearViewport] = useState(false);
  const cancel = useRef<(() => void) | null>(null);
  const liveUrl = useRef<string | null>(null);
  useEffect(() => {
    const element = frame.current;
    if (!element) return;
    if (typeof IntersectionObserver === "undefined") { setNearViewport(true); return; }
    const observer = new IntersectionObserver(([entry]) => setNearViewport(entry.isIntersecting), { rootMargin: "300px 0px" });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  const shouldLoad = enabled && nearViewport;
  useEffect(() => {
    // Leaving the visible neighbourhood revokes pixels. Reset before a later
    // return can attempt to render the revoked URL from the previous request.
    setState({ status: "loading" });
    if (!shouldLoad) return;
    const stop = requestProtectedImage(src, (next) => {
      liveUrl.current = next.status === "ready" ? next.url : null;
      setState(next);
    });
    cancel.current = stop;
    return () => { liveUrl.current = null; stop(); if (cancel.current === stop) cancel.current = null; };
  }, [src, shouldLoad, attempt]);
  return <span ref={frame} className={styles.imageFrame}>{shouldLoad && state.status === "ready" && liveUrl.current === state.url ? <img src={state.url} alt={alt} className={styles.image} data-carousel-image-state="ready"
    width={1308} height={1632} draggable={false} decoding="async"
    onError={() => { liveUrl.current = null; cancel.current?.(); setState({ status: "error", message: "이미지를 표시하지 못했어요. 다시 시도해 주세요." }); }} /> : <span className={styles.imageState} data-image-state={state.status} data-carousel-image-state={shouldLoad && state.status === "error" ? "error" : "loading"}>
    <span role="status">{state.status === "error" ? state.message : "이미지를 불러오고 있어요."}</span>
    {state.status === "error" && retryable ? <button type="button" onClick={() => setAttempt((value) => value + 1)}>이미지 다시 시도</button> : null}
  </span>}</span>;
}

function SlideImage({ slide, enabled }: { slide: HomeSlideModel; enabled: boolean }) {
  const [failed, setFailed] = useState(false);
  if (slide.image?.protected) return <ProtectedSlideImage key={slide.image.src} {...slide.image} enabled={enabled} />;
  return slide.image && !failed
    ? <img src={slide.image.src} alt={slide.image.alt} className={styles.image} width={1308} height={1632}
        loading="lazy" draggable={false} onError={() => setFailed(true)} />
    : <div className={styles.imageState} role="img" aria-label="이미지 준비 중">이미지 준비 중</div>;
}

function SlideFigure({ slide, enabled, focusable, home = false, onTextVisibilityChange }: {
  slide: HomeSlideModel; enabled: boolean; focusable: boolean; home?: boolean; onTextVisibilityChange?: (visible: boolean) => void;
}) {
  const figure = useRef<HTMLElement>(null);
  useEffect(() => {
    const element = figure.current;
    if (!element || !onTextVisibilityChange) return;
    const caption = element.querySelector("figcaption");
    if (!caption) return;
    let disposed = false;
    const measure = () => {
      if (disposed) return;
      const image = element.querySelector("img");
      // Fail open: SSR, pending/error pixels and unmeasured/clipped text must
      // keep their complete original in Detail's transcript below the card.
      onTextVisibilityChange(!!image?.complete && image.naturalWidth > 0 &&
        caption.clientHeight > 0 && caption.clientWidth > 0 &&
        caption.scrollHeight <= caption.clientHeight && caption.scrollWidth <= caption.clientWidth);
    };
    measure();
    const resize = new ResizeObserver(measure);
    resize.observe(caption);
    for (const child of caption.children) resize.observe(child);
    const mutation = new MutationObserver(measure);
    mutation.observe(element, { childList: true, subtree: true, attributes: true, attributeFilter: ["data-carousel-image-state"] });
    element.addEventListener("load", measure, true);
    element.addEventListener("error", measure, true);
    document.fonts.addEventListener("loadingdone", measure);
    void document.fonts.ready.then(measure);
    return () => {
      disposed = true; resize.disconnect(); mutation.disconnect();
      element.removeEventListener("load", measure, true);
      element.removeEventListener("error", measure, true);
      document.fonts.removeEventListener("loadingdone", measure);
    };
  }, [slide, onTextVisibilityChange]);
  const box = slide.layout?.textOverlayBox;
  const overlay = !!box && !!slide.titleLines;
  const region: CSSProperties | undefined = box && !home ? {
    left: `${box.x * 100}%`, top: `max(56px, ${box.y * 100}%)`, width: `${box.width * 100}%`, height: `${box.height * 100}%`,
    maxHeight: `max(0px, calc(100% - max(56px, ${box.y * 100}%) - 12px))`,
  } : undefined;
  return <figure ref={figure} className={styles.stage}>
    <SlideImage key={slide.image?.src ?? "missing"} slide={slide} enabled={enabled} />
    {home && overlay ? <div className={styles.previewScrim} aria-hidden="true" /> : null}
    {overlay ? <figcaption className={styles.overlay} style={region} tabIndex={!home && focusable ? 0 : -1}
      aria-label={home ? "카드 미리보기" : "카드 본문"} data-slide-overlay="" data-carousel-overlay="">
      {slide.eyebrow ? <p className={styles.eyebrow}>{slide.eyebrow}</p> : null}
      <h3 className={styles.title}>{home ? slide.titleLines!.join("\n") : slide.titleLines!.map((line, lineIndex) => <span key={lineIndex}>{line}</span>)}</h3>
      {slide.bodySentences?.length ? <div className={styles.copy}>{home ? <p>{slide.bodySentences.join(" ")}</p> : slide.bodySentences.map((sentence, sentenceIndex) => <p key={sentenceIndex}>{sentence}</p>)}</div> : null}
    </figcaption> : <figcaption className={styles.legacyCaption}>{slide.description}</figcaption>}
  </figure>;
}

/** Keep Detail's cover/article boundary while reusing the server-sized carousel. */
export function ContentCover({ slides, title, onSlideChange, onCoverTextVisibilityChange }: {
  slides: readonly HomeSlideModel[]; title: string; onSlideChange?: (index: number) => void;
  onCoverTextVisibilityChange?: (visible: boolean) => void;
}) {
  return slides.length ? <div className={styles.cover} role="group" aria-label={title} data-content-cover="">
    <ContentCarousel slides={slides} title={title} scope="detail" onSlideChange={onSlideChange} onCoverTextVisibilityChange={onCoverTextVisibilityChange} />
  </div> : null;
}

export function ContentCarousel({ slides: sourceSlides, title, labelledBy, scope = "home", onSlideChange, onCoverTextVisibilityChange }: {
  slides: readonly HomeSlideModel[]; title: string; labelledBy?: string; scope?: "home" | "detail"; onSlideChange?: (index: number) => void;
  onCoverTextVisibilityChange?: (visible: boolean) => void;
}) {
  const id = useId();
  const track = useRef<HTMLDivElement>(null);
  const drag = useRef<{ pointerId: number; x: number; y: number; left: number; moving: boolean } | null>(null);
  const suppressClick = useRef(false);
  const [active, setActive] = useState(0);
  const slides = orderedHomeSlides(sourceSlides);
  const current = Math.max(0, Math.min(active, slides.length - 1));
  function goTo(index: number) {
    const element = track.current;
    if (!element || index < 0 || index >= slides.length) return;
    element.scrollTo({ left: index * element.clientWidth });
  }
  function finishDrag(event: PointerEvent<HTMLDivElement>) {
    const start = drag.current;
    if (!start || start.pointerId !== event.pointerId) return;
    drag.current = null;
    const element = event.currentTarget;
    if (!start.moving) return;
    const index = Math.round(element.scrollLeft / element.clientWidth);
    delete element.dataset.dragging;
    if (element.hasPointerCapture(event.pointerId)) element.releasePointerCapture(event.pointerId);
    goTo(Math.max(0, Math.min(index, slides.length - 1)));
  }
  if (!slides.length) return null;
  return <div className={styles.carousel} role="region" aria-roledescription="캐러셀"
    aria-labelledby={labelledBy} aria-label={labelledBy ? undefined : title} data-content-carousel={scope}>
    <div id={`${id}-track`} ref={track} className={styles.track} tabIndex={0} data-carousel-track=""
      data-home-track={scope === "home" ? "" : undefined} data-detail-track={scope === "detail" ? "" : undefined}
      aria-label="좌우로 슬라이드 탐색" aria-describedby={`${id}-help`}
      onPointerDown={(event) => {
        // A fresh input (including touch or a button) must keep its normal click.
        suppressClick.current = false;
        // Touch keeps native horizontal swiping and vertical feed/article scrolling.
        if (event.pointerType !== "mouse" || event.button !== 0 || (event.target as Element).closest("button, a")) return;
        drag.current = { pointerId: event.pointerId, x: event.clientX, y: event.clientY, left: event.currentTarget.scrollLeft, moving: false };
      }}
      onPointerMove={(event) => {
        const start = drag.current;
        if (event.pointerType !== "mouse" || !start || start.pointerId !== event.pointerId) return;
        if (event.buttons !== 1) { finishDrag(event); return; }
        const dx = event.clientX - start.x, dy = event.clientY - start.y;
        if (!start.moving) {
          if (Math.abs(dy) >= 6 && Math.abs(dy) >= Math.abs(dx)) { drag.current = null; return; }
          if (Math.abs(dx) < 6) return;
          start.moving = true;
          suppressClick.current = true;
          event.currentTarget.setPointerCapture(event.pointerId);
          event.currentTarget.dataset.dragging = "true";
        }
        event.preventDefault();
        event.currentTarget.scrollLeft = start.left - dx;
      }}
      onPointerUp={finishDrag}
      onPointerCancel={finishDrag}
      onLostPointerCapture={finishDrag}
      onPointerLeave={() => { if (!drag.current?.moving) drag.current = null; }}
      onClickCapture={(event) => {
        // Pointer capture retargets the release click to this track. Consume it
        // before a card action can run; keyboard activation has no click count.
        if (!suppressClick.current || event.detail === 0) return;
        suppressClick.current = false;
        event.preventDefault(); event.stopPropagation();
      }}
      onScroll={(event) => {
        const element = event.currentTarget;
        if (element.clientWidth) { const index = Math.round(element.scrollLeft / element.clientWidth); setActive(index); onSlideChange?.(index); }
      }} onKeyDown={(event) => {
        if (event.target !== event.currentTarget) return;
        // Scroll can settle before React commits onScroll state. Read the actual
        // track position so consecutive keyboard moves do not use a stale page.
        const element = event.currentTarget;
        const position = element.clientWidth ? Math.round(element.scrollLeft / element.clientWidth) : current;
        const index = event.key === "ArrowRight" ? position + 1 : event.key === "ArrowLeft" ? position - 1
          : event.key === "Home" ? 0 : event.key === "End" ? slides.length - 1 : null;
        if (index === null) return;
        event.preventDefault(); goTo(index);
      }}>
      {slides.map((slide, index) => <div key={slide.id} className={styles.slide} role="group" aria-roledescription="슬라이드"
          aria-label={`${index + 1} / ${slides.length}`} data-home-slide={scope === "home" ? slide.id : undefined}
          data-content-slide={slide.page ?? index + 1} data-carousel-slide={slide.page ?? index + 1} aria-hidden={current !== index} inert={current !== index}>
          <SlideFigure slide={slide} enabled={Math.abs(current - index) <= 1} focusable={current === index} home={scope === "home"}
            onTextVisibilityChange={scope === "detail" && index === 0 ? onCoverTextVisibilityChange : undefined} />
        </div>)}
    </div>
    <div className={homeStyles.slideControls} role="group" aria-label={`${title} 슬라이드 이동`}>
      <button type="button" className={`${homeStyles.slideButton} ${styles.slideButton}`} aria-label="이전 슬라이드" aria-controls={`${id}-track`} disabled={current === 0} onClick={() => goTo(current - 1)}>이전</button>
      <p className={`${homeStyles.position} ${styles.position}`} aria-live="polite" aria-atomic="true" data-home-position={scope === "home" ? "" : undefined} data-carousel-position="">
        <span className={styles.srOnly}>현재 슬라이드 </span>{current + 1} / {slides.length}
      </p>
      <button type="button" className={`${homeStyles.slideButton} ${styles.slideButton}`} aria-label="다음 슬라이드" aria-controls={`${id}-track`} disabled={current === slides.length - 1} onClick={() => goTo(current + 1)}>다음</button>
    </div>
    <div className={`${homeStyles.indicators} ${styles.indicators}`} role="group" aria-label={`${title} 슬라이드 위치`}>
      {slides.map((slide, index) => <button type="button" className={homeStyles.indicator} key={slide.id} aria-label={`${index + 1}번 슬라이드 보기`}
        aria-current={current === index ? "true" : undefined} aria-controls={`${id}-track`} onClick={() => goTo(index)}><span /></button>)}
    </div>
    <p className={styles.srOnly} id={`${id}-help`}>좌우로 넘기거나 방향키·이전·다음 버튼으로 탐색하세요. {scope === "home" ? "카드는 미리보기예요. 전체 글과 슬라이드 원문은 자세히 보기에서 읽을 수 있어요." : "카드에 다 보이지 않는 원문은 이미지 아래 본문과 슬라이드 추가 원문에서 읽을 수 있어요."}</p>
  </div>;
}

export function ContentMetadata({ productionType, notices, publishedAt, publisher }: {
  productionType?: "human" | "ai" | "hybrid"; notices?: readonly { code: string; message: string }[];
  publishedAt?: string | null; publisher?: string;
}) {
  const date = publishedAt && Number.isFinite(Date.parse(publishedAt))
    ? new Intl.DateTimeFormat("ko-KR", { year: "numeric", month: "long", day: "numeric", timeZone: "Asia/Seoul" }).format(new Date(publishedAt)) : null;
  const messages = [...new Set(notices?.map((notice) => notice.message).filter(Boolean) ?? [])];
  if (!notices?.some((notice) => notice.code === "AI_GENERATED")) {
    if (productionType === "ai") messages.unshift("AI가 생성한 콘텐츠입니다.");
    else if (productionType === "hybrid") messages.unshift("AI와 함께 만든 콘텐츠입니다.");
  }
  if (!date && !publisher && !messages.length) return null;
  return <div className={styles.metadata} data-content-metadata="">
    {publisher || date ? <p>{publisher ? <span>{publisher}</span> : null}{date ? <time dateTime={publishedAt!}>{date}</time> : null}</p> : null}
    {messages.map((message) => <p key={message} className={styles.notice}>{message}</p>)}
  </div>;
}
