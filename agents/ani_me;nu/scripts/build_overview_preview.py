"""Render a compact, top-down architecture overview for review."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parents[1] / "output" / "architecture_overview_postgres.png"
W, H = 1800, 2500
BG = "#FBFAFF"
INK = "#283044"
MUTED = "#667085"
LINE = "#8290A4"
PURPLE = "#7357D9"
BLUE_FILL, BLUE_LINE = "#EAF1FF", "#6E91E8"
GOLD_FILL, GOLD_LINE = "#FFF4D8", "#D9A943"
MINT_FILL, MINT_LINE = "#E7F7EE", "#55A57A"
PEACH_FILL, PEACH_LINE = "#FFF0E5", "#D98752"
RED_FILL, RED_LINE = "#FCE9EB", "#D77980"
NEUTRAL_FILL, NEUTRAL_LINE = "#F3F4F7", "#A4ABB7"


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    path = Path("C:/Windows/Fonts/malgunbd.ttf" if bold else "C:/Windows/Fonts/malgun.ttf")
    return ImageFont.truetype(str(path), size)


im = Image.new("RGB", (W, H), BG)
d = ImageDraw.Draw(im)
F_TITLE, F_SUB, F_HEAD, F_BODY, F_SMALL = (
    font(38, True),
    font(20),
    font(22, True),
    font(18),
    font(15),
)


def centered_text(box, text: str, f, fill=INK, gap=5):
    x1, y1, x2, y2 = box
    lines = text.split("\n")
    heights = [d.textbbox((0, 0), line, font=f)[3] for line in lines]
    total = sum(heights) + gap * (len(lines) - 1)
    y = y1 + (y2 - y1 - total) / 2
    for line, height in zip(lines, heights, strict=True):
        width = d.textbbox((0, 0), line, font=f)[2]
        d.text((x1 + (x2 - x1 - width) / 2, y), line, font=f, fill=fill)
        y += height + gap


def box(x, y, w, h, title, detail="", fill=NEUTRAL_FILL, stroke=NEUTRAL_LINE, radius=3):
    rect = (x, y, x + w, y + h)
    d.rectangle(rect, fill=fill, outline=stroke, width=2)
    if detail:
        centered_text((x + 12, y + 8, x + w - 12, y + h * 0.48), title, F_HEAD)
        centered_text((x + 12, y + h * 0.45, x + w - 12, y + h - 8), detail, F_SMALL, MUTED, 3)
    else:
        centered_text((x + 10, y + 5, x + w - 10, y + h - 5), title, F_HEAD)
    return rect


def cylinder(x, y, w, h, title, detail):
    cap_h = 22
    d.rectangle((x, y + cap_h // 2, x + w, y + h - cap_h // 2), fill=GOLD_FILL)
    d.rectangle((x, y + cap_h // 2, x + w, y + h - cap_h // 2), outline=GOLD_LINE, width=2)
    d.ellipse((x, y, x + w, y + cap_h), fill=GOLD_FILL, outline=GOLD_LINE, width=2)
    d.arc((x, y + h - cap_h, x + w, y + h), 0, 180, fill=GOLD_LINE, width=2)
    d.line((x, y + h - cap_h // 2, x, y + h - cap_h // 2), fill=GOLD_LINE, width=2)
    d.line((x + w, y + h - cap_h // 2, x + w, y + h - cap_h // 2), fill=GOLD_LINE, width=2)
    centered_text((x + 8, y + 14, x + w - 8, y + h * 0.58), title, F_HEAD)
    centered_text((x + 8, y + h * 0.52, x + w - 8, y + h - 8), detail, F_SMALL, MUTED, 2)


def diamond(cx, cy, w, h, title, detail=""):
    pts = [(cx, cy - h // 2), (cx + w // 2, cy), (cx, cy + h // 2), (cx - w // 2, cy)]
    d.polygon(pts, fill="#F0EAFE", outline="#9A7BE5", width=2)
    d.line(pts + [pts[0]], fill="#9A7BE5", width=2)
    centered_text(
        (cx - w * 0.37, cy - h * 0.34, cx + w * 0.37, cy + h * 0.34),
        title + ("\n" + detail if detail else ""),
        F_SMALL,
        INK,
        2,
    )


def arrow(points, label=None, label_at=None, color=LINE, width=4, dash=False):
    width = max(2, round(width * 0.55))
    if dash:
        for (x1, y1), (x2, y2) in zip(points, points[1:], strict=False):
            if x1 == x2:
                lo, hi = sorted((y1, y2))
                for y in range(lo, hi, 20):
                    d.line((x1, y, x2, min(y + 11, hi)), fill=color, width=width)
            else:
                lo, hi = sorted((x1, x2))
                for x in range(lo, hi, 20):
                    d.line((x, y1, min(x + 11, hi), y2), fill=color, width=width)
    else:
        d.line(points, fill=color, width=width, joint="curve")
    x1, y1 = points[-2]
    x2, y2 = points[-1]
    if x2 == x1:
        sign = 1 if y2 > y1 else -1
        head = [(x2, y2), (x2 - 7, y2 - 12 * sign), (x2 + 7, y2 - 12 * sign)]
    else:
        sign = 1 if x2 > x1 else -1
        head = [(x2, y2), (x2 - 12 * sign, y2 - 7), (x2 - 12 * sign, y2 + 7)]
    d.polygon(head, fill=color)
    if label and label_at:
        tw = d.textbbox((0, 0), label, font=F_SMALL)[2]
        x, y = label_at
        d.rounded_rectangle((x - 7, y - 3, x + tw + 8, y + 25), radius=5, fill=BG)
        d.text((x, y), label, font=F_SMALL, fill=color)


# Header
d.text((75, 42), "me;nu 에이전트 전체 구조", font=F_TITLE, fill=INK)
d.text(
    (77, 101),
    "LangGraph StateGraph · OpenAI 검수 사용 · 수집 → PostgreSQL(메타데이터+이미지) → "
    "생성·검수 · 카드별 수정 루프 · 3회째 탈락",
    font=F_SUB,
    fill=MUTED,
)

# Source tier
src_y, src_w, src_h = 180, 300, 82
sources = [
    box(
        162,
        src_y,
        src_w,
        src_h,
        "공식 정보 출처",
        "방영 · 신작 · 행사 · 공지",
        GOLD_FILL,
        GOLD_LINE,
    ),
    box(
        750, src_y, src_w, src_h, "커뮤니티 · 트렌드", "유머 · 토론 소재 후보", GOLD_FILL, GOLD_LINE
    ),
    box(
        1337,
        src_y,
        src_w,
        src_h,
        "공식 이미지 출처",
        "출처 · 이용 조건 확인 대상",
        GOLD_FILL,
        GOLD_LINE,
    ),
]

# Three crawler agents
crawl_y, crawl_h, crawl_w = 350, 100, 300
crawlers = [
    box(
        162,
        crawl_y,
        crawl_w,
        crawl_h,
        "정보 수집 에이전트",
        "사실 · 날짜 · 원문 출처 구조화",
        BLUE_FILL,
        BLUE_LINE,
    ),
    box(
        750,
        crawl_y,
        crawl_w,
        crawl_h,
        "유머·토론 수집 에이전트",
        "소재 · 맥락 · 원문 출처 구조화",
        BLUE_FILL,
        BLUE_LINE,
    ),
    box(
        1337,
        crawl_y,
        crawl_w,
        crawl_h,
        "이미지 수집 에이전트",
        "원본 파일 · 출처\n권리 메타데이터",
        BLUE_FILL,
        BLUE_LINE,
    ),
]
for x in (312, 900, 1487):
    arrow([(x, src_y + src_h), (x, crawl_y)], width=3)

# Normalize / ingest
norm = box(
    620,
    555,
    560,
    96,
    "검증 · 정규화 · 중복 제거",
    "출처/날짜/권리 상태 · fact-image 연결 · 실패 항목 격리",
    NEUTRAL_FILL,
    NEUTRAL_LINE,
)
for x in (312, 900, 1487):
    arrow([(x, crawl_y + crawl_h), (x, 515), (900, 515), (900, 555)], width=3)

# One PostgreSQL database stores both relational metadata and deduplicated image bytes.
db = cylinder(755, 710, 290, 104, "PostgreSQL", "자료·콘텐츠·이력 + image_blobs(BYTEA)")
arrow([(900, 669), (900, 710)], width=3)

# Main content run
request = box(
    650,
    900,
    500,
    86,
    "콘텐츠 생성 실행 · request_id",
    "PostgreSQL에서 검증된 자료만 조회",
    BLUE_FILL,
    BLUE_LINE,
)
arrow([(900, 814), (900, 900)], width=3)

material = (900, 1080, 330, 140)
diamond(*material, "재료 충분?", "사실 · 출처 · 권리 · 이미지")
arrow([(900, 986), (900, 1010)], width=3)

gen = box(
    630,
    1280,
    540,
    112,
    "콘텐츠 생성 에이전트",
    "카피 작성 → fact/image 연결 → 카드 3~10장 레이아웃",
    BLUE_FILL,
    BLUE_LINE,
)
arrow([(900, 1150), (900, 1280)], label="예 · 진행", label_at=(920, 1200), width=3)

review = box(
    630,
    1490,
    540,
    130,
    "독립 평가·검수 에이전트",
    "OpenAI LLM 평가 · 점수 · 한국어/번역\n이미지 전체 노출 · 안전/존중 · 치명 이슈 차단",
    BLUE_FILL,
    BLUE_LINE,
)
arrow([(900, 1392), (900, 1490)], width=3)

# Outcome / retry gate
review_decision = (900, 1750, 350, 150)
diamond(*review_decision, "검수 판정", "통과 / 수정 / 자료 결함")
arrow([(900, 1620), (900, 1675)], width=3)

feed = box(1320, 1708, 360, 88, "게시 대기열", "통과 콘텐츠만 저장", MINT_FILL, MINT_LINE)
arrow([(1075, 1750), (1320, 1750)], label="통과", label_at=(1190, 1718), color=MINT_LINE, width=3)

retry = (900, 1985, 320, 135)
diamond(*retry, "반려 누적 < 3회?", "누적 3회째는 탈락")
arrow([(900, 1825), (900, 1917)], label="수정 가능 반려", label_at=(920, 1860), width=3)

archive = box(1320, 1970, 360, 88, "탈락 보관", "반려 이유 · 점수 · 시도 이력", RED_FILL, RED_LINE)
arrow(
    [(1060, 1985), (1320, 1985)],
    label="3회째 · 탈락",
    label_at=(1150, 1952),
    color=RED_LINE,
    width=3,
)

# Two explicit loop paths, routed on distinct outer lanes.
arrow(
    [(740, 1985), (490, 1985), (490, 1336), (630, 1336)],
    label="문제 카드만 수정 · qa_report 반영",
    label_at=(250, 1640),
    color=PURPLE,
    width=4,
)

# Material/evidence defect goes to a separate crawler cycle, not the copy retry.
supp = box(
    70, 1250, 350, 96, "수집 보완 대기열", "부족 출처 재수집 · 권리 확인", PEACH_FILL, PEACH_LINE
)
arrow(
    [(735, 1080), (500, 1080), (500, 1298), (420, 1298)],
    label="아니오 · 자료 부족",
    label_at=(520, 1045),
    color=PEACH_LINE,
    width=3,
)
# External collection loop returns to the source tier, then follows the normal crawler→DB path.
d.line([(70, 1298), (35, 1298), (35, 160), (1487, 160)], fill=PEACH_LINE, width=2)
d.text(
    (52, 128),
    "별도 수집 실행 · 적재 후 새 생성 요청으로 이어지는 보완 루프",
    font=F_SMALL,
    fill=PEACH_LINE,
)
for source_x in (312, 900, 1487):
    arrow([(source_x, 160), (source_x, src_y)], color=PEACH_LINE, width=3)

# Reviewer evidence defect route, separate from editorial retries.
arrow(
    [(725, 1750), (455, 1750), (455, 1346), (420, 1346)],
    label="사실·출처·권리 결함",
    label_at=(270, 1435),
    color=PEACH_LINE,
    width=3,
)

# The reviewer API is configured; only the normal approved/revise/defect routes are shown.
d.text(
    (1320, 1810),
    "feed_queue는 내부 대기열\nSNS 자동 발행과는 별도",
    font=F_SMALL,
    fill=MUTED,
    spacing=5,
)

# Context note
d.rectangle((70, 2220, 1730, 2300), fill="#F0EEFA", outline="#D7D0EF", width=2)
d.text(
    (95, 2241),
    "루프: 자료 결함 → 수집 단계 / 수정 반려 → 생성 단계 / 3회째 반려 → 탈락 보관",
    font=F_BODY,
    fill=INK,
)

OUT.parent.mkdir(parents=True, exist_ok=True)
im.save(OUT, optimize=True)
print(OUT)
