import type { DetailArticle } from "./detail";

// Canonical editorial examples shared by Home and Detail. No Consumer data,
// onboarding answers, recommendation results, event availability or server DTO.
export const mockMagazines: readonly DetailArticle[] = [
  {
    origin: "demo", postId: "demo-home-story", contentId: "demo-content-story", work: "하이큐!!",
    title: "코트 밖 6개 학교, 이번 승부는 밥상에서",
    image: { src: "/pivot/home/slide-1.png", alt: "하이큐 여섯 학교의 등장인물과 음식 협업을 소개하는 표지" },
    sections: [
      { paragraphs: ["하이큐!!의 여섯 학교가 코트 밖 밥상에서 만났어요. 익숙한 등장인물과 음식이 함께 놓인 표지에서 시작해, 학교마다 다른 분위기를 천천히 살펴보세요."] },
      { heading: "코트 밖에서 만난 얼굴들", paragraphs: ["경기장에서 공을 쫓던 인물들이 이번 이미지에서는 각자의 음식을 들고 있어요. 유니폼과 표정, 배경의 색이 어우러져 서로 다른 학교의 개성을 보여줘요.", "Home에서 보았던 다섯 장의 이미지는 한 게시물에 속해 있어요. 좌우로 넘기며 발견한 장면을 떠올리면서, 가장 먼저 눈에 들어온 음식과 인물을 찾아보세요."] },
      { heading: "출처 및 정보", paragraphs: ["이 글과 이미지는 me;nu Figma 시안을 바탕으로 구성한 고정 Mock 콘텐츠예요. 표지의 행사명·날짜·장소·가격은 디자인 예시이며, 현재 진행 중인 행사나 구매 가능한 상품을 안내하지 않아요."] },
      { heading: "부가 관련 정보", paragraphs: ["좋아하는 작품을 보는 방법은 경기 장면에만 머물지 않아요. 색과 표정, 음식처럼 작은 요소를 따라가다 보면 같은 작품에서도 다른 이야기를 발견할 수 있어요.", "여러 학교의 이미지를 나란히 떠올려보세요. 배경색이 달라지면 같은 음식도 다른 인상을 줄 수 있고, 인물의 몸짓은 한 장의 이미지에 리듬을 만들어요.", "아래 선택은 화면 동작을 확인하기 위한 Demo예요. 선택 내용은 이 글을 닫으면 사라지며, 실제 추천이나 취향 저장으로 이어지지 않아요."] },
    ],
  },
  {
    origin: "demo", postId: "demo-home-discovery", contentId: "demo-content-discovery", work: "하이큐!!",
    title: "하이큐!!의 색과 표정으로 다시 보는 밥상",
    image: { src: "/pivot/home/slide-1.png", alt: "하이큐 등장인물과 음식, 학교별 색을 담은 표지" },
    sections: [
      { paragraphs: ["같은 하이큐!! 이미지에서도 학교별 색과 표정을 따라가면 새로운 장면이 보여요. 이 게시물은 음식 협업의 시각적 구성을 살펴보는 별도의 읽기 예시예요."] },
      { heading: "색을 따라 읽기", paragraphs: ["표지의 빨강·노랑·초록은 인물과 음식을 나누면서 시선을 이어줘요. Home에서 두 번째 이미지까지 넘겼다면 각 장면의 배경과 표정을 비교해보세요."] },
      { heading: "출처 및 정보", paragraphs: ["me;nu Figma 시안 이미지를 재사용한 Mock 편집 예시예요. 이미지에 표시된 일정과 가격은 현재 행사 정보가 아니며, 온보딩 답변에 따른 추천도 수행하지 않아요."] },
      { heading: "부가 관련 정보", paragraphs: ["Home 캐러셀의 마지막 장면은 이미지가 없는 상태의 표시 예시예요. 대표 이미지는 첫 장과 같고, 이 본문은 위 게시물과 다른 관점으로 작성했어요."] },
    ],
  },
  {
    origin: "demo", postId: "demo-home-reading", contentId: "demo-content-reading", work: "하이큐!!",
    title: "하이큐!! 협업 소품에서 발견한 작은 이야기",
    image: { src: "/pivot/home/slide-5.png", alt: "하이큐 협업 소품을 소개하는 Figma 이미지" },
    sections: [
      { paragraphs: ["하이큐!!의 인물과 색이 작은 소품에 담겼어요. Home에서 보았던 협업 상품 이미지의 구성과 디테일을 천천히 읽어보는 시간이에요."] },
      { heading: "작은 디테일 살펴보기", paragraphs: ["물건의 모양과 인물이 놓인 위치를 따라가 보세요. 작품의 분위기가 작은 면적 안에서 어떻게 전해지는지 살펴보는 읽기 예시예요."] },
      { heading: "출처 및 정보", paragraphs: ["me;nu Figma의 협업 상품 이미지를 사용했어요. 이 Mock은 실제 판매·재고·가격을 안내하지 않으며, 서버 저장이나 실제 서비스 피드 반영도 수행하지 않아요."] },
      { heading: "부가 관련 정보", paragraphs: ["한 장을 오래 보는 것도 작품을 즐기는 방법이에요. 본문을 다 읽었다면 상단 뒤로가기나 하단 HOME으로 돌아가 다른 게시물을 이어서 살펴보세요."] },
    ],
  },
];
