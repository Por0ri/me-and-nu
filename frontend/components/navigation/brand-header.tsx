/** "me;movie  SAVE"처럼 로고와 화면 이름을 보여주는 상단 머리글. */
export function BrandHeader({ topicLabel, title }: { topicLabel: string; title: string }) {
  return (
    <header className="va-home-header">
      <p className="va-home-brand">
        me;<span className="va-accent">{topicLabel}</span>
      </p>
      <h1 className="va-page-title">{title}</h1>
    </header>
  );
}
