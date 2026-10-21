import type { HomeDataSource, HomeViewModel } from "./home";
import { mockMagazines } from "./magazine-mock";

// Fixed display examples, independent of onboarding answers and Consumer fixtures.
const homeLayout: HomeViewModel = {
  origin: "demo",
  cards: [
    {
      id: "demo-home-story",
      title: "한 작품에서 시작되는 여러 이야기",
      detailContentId: "demo-content-story",
      slides: [
        { id: "story-cover", order: 1, image: { src: "/pivot/home/slide-1.png", alt: "하이큐 등장인물이 음식을 소개하는 Figma 표지 예시" }, description: "하나의 작품을 여러 장면으로 펼쳐보세요. Figma의 슬라이드 구성 확인을 위한 시연용 이미지예요." },
        { id: "story-second", order: 2, image: { src: "/pivot/home/slide-2.png", alt: "하이큐 음식 협업의 두 번째 Figma 슬라이드 예시" }, description: "좌우로 넘기면 다음 이미지와 설명을 볼 수 있어요. 이미지 안의 날짜·가격은 시안 내용이며 현재 행사 정보가 아니에요." },
        { id: "story-third", order: 3, image: { src: "/pivot/home/slide-3.png", alt: "하이큐 음식 협업의 세 번째 Figma 슬라이드 예시" }, description: "한 게시물 안에서 이어지는 세 번째 장면이에요." },
        { id: "story-fourth", order: 4, image: { src: "/pivot/home/slide-4.png", alt: "하이큐 음식 협업의 네 번째 Figma 슬라이드 예시" }, description: "이미지와 문구의 표시 순서를 확인하는 네 번째 장면이에요." },
        { id: "story-last", order: 5, image: { src: "/pivot/home/slide-5.png", alt: "하이큐 협업 상품을 소개하는 마지막 Figma 슬라이드 예시" }, description: "마지막 장면이에요. 아래로 스크롤하면 다른 게시물이 이어져요." },
      ],
    },
    {
      id: "demo-home-discovery",
      title: "좋아하는 장면을 새로운 시선으로 살펴보기",
      detailContentId: "demo-content-discovery",
      slides: [
        { id: "discovery-cover", order: 1, image: { src: "/pivot/home/slide-1.png", alt: "다른 게시물에 재사용한 Figma 표지 예시" }, description: "같은 시안 이미지를 재사용한 별도의 예시 게시물이에요. 위 게시물과 슬라이드 위치를 공유하지 않아요." },
        { id: "discovery-next", order: 2, image: { src: "/pivot/home/slide-3.png", alt: "다른 시선으로 살펴보는 Figma 슬라이드 예시" }, description: "두 번째 게시물의 두 번째 장면이에요. 온보딩에서 선택한 취향에 따른 추천은 수행하지 않아요." },
        { id: "discovery-last", order: 3, description: "이미지가 없는 경우에도 설명을 읽고 다음 게시물로 이동할 수 있어요." },
      ],
    },
    {
      id: "demo-home-reading",
      title: "이야기를 천천히 읽으며 새로운 관심사를 발견하는 시간",
      detailContentId: "demo-content-reading",
      slides: [
        { id: "reading-cover", order: 1, image: { src: "/pivot/home/slide-5.png", alt: "읽기 예시에 재사용한 Figma 협업 상품 이미지" }, description: "구성과 디자인은 추후 조정될 수 있어요. 이 예시는 서버에 저장되거나 실제 서비스 피드에 표시되지 않아요." },
        { id: "reading-last", order: 2, description: "자세히 보기를 누르면 이 이미지와 이어지는 매거진 본문을 읽을 수 있어요." },
      ],
    },
  ],
};

const exampleHome: HomeViewModel = {
  ...homeLayout,
  cards: homeLayout.cards.map((card) => {
    const article = mockMagazines.find((item) => item.postId === card.id && item.contentId === card.detailContentId);
    if (!article) throw new Error("Missing Mock magazine for Home card");
    return { ...card, title: article.title, slides: card.slides.map((slide, index) => index === 0
      ? { ...slide, image: article.image, description: article.sections[0].paragraphs[0] } : slide) };
  }),
};

export const mockHomeSource: HomeDataSource = {
  kind: "demo",
  async load() {
    return { status: "ready", home: structuredClone(exampleHome) };
  },
};
