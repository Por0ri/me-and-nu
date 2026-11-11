/** HOME has a separate scrollport above its dock; other screens use the page. */
export function homeScrollTarget(): HTMLElement | Window {
  const screen = document.querySelector<HTMLElement>("[data-home-state]");
  return screen && getComputedStyle(screen).overflowY === "auto" ? screen : window;
}

export function homeScrollY(): number {
  const target = homeScrollTarget();
  return target === window ? window.scrollY : (target as HTMLElement).scrollTop;
}

export function scrollHomeTo(top: number): void {
  homeScrollTarget().scrollTo({ top, behavior: "instant" });
}
