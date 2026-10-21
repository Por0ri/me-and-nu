import type { HomeDataSource, HomeViewModel } from "./home";

// Fixed display examples, independent of onboarding answers and Consumer fixtures.
const exampleHome: HomeViewModel = {
  origin: "demo",
  cards: [
    {
      id: "demo-home-story",
      title: "한 작품에서 시작되는 여러 이야기",
      summary: "작품에 관한 이야기를 담을 기본 카드예요. 화면 구조를 확인하기 위한 예시이며 실제 추천 콘텐츠가 아니에요.",
    },
    {
      id: "demo-home-discovery",
      title: "좋아하는 장면을 새로운 시선으로 살펴보기",
      summary: "제목과 소개 글이 세로 피드에 어떻게 표시되는지 확인해보세요. 선택한 취향에 따른 추천이나 검색은 수행하지 않아요.",
    },
    {
      id: "demo-home-reading",
      title: "이야기를 천천히 읽으며 새로운 관심사를 발견하는 시간",
      summary: "콘텐츠의 구성과 디자인은 추후 조정될 수 있어요. 이 예시는 서버에 저장되거나 실제 서비스 피드에 표시되지 않아요.",
    },
  ],
};

export const mockHomeSource: HomeDataSource = {
  kind: "demo",
  async load() {
    return { status: "ready", home: structuredClone(exampleHome) };
  },
};
