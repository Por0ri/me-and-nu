# ruff: noqa: E501
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Flowable,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output" / "pdf" / "ani_menu_agent_architecture_report.pdf"
FONT_PATH = Path(r"C:\Windows\Fonts\malgun.ttf")
FONT_BOLD_PATH = Path(r"C:\Windows\Fonts\malgunbd.ttf")

pdfmetrics.registerFont(TTFont("Malgun", str(FONT_PATH)))
pdfmetrics.registerFont(TTFont("Malgun-Bold", str(FONT_BOLD_PATH)))

INK = colors.HexColor("#211B33")
PURPLE = colors.HexColor("#7953E8")
LAVENDER = colors.HexColor("#F1ECFF")
MINT = colors.HexColor("#DDF6EB")
PEACH = colors.HexColor("#FFF0E5")
PALE = colors.HexColor("#F7F6FA")
GRAY = colors.HexColor("#716C7D")
RED = colors.HexColor("#B8324A")
GREEN = colors.HexColor("#237B56")

styles = getSampleStyleSheet()
styles.add(
    ParagraphStyle(
        name="CoverTitle",
        fontName="Malgun-Bold",
        fontSize=25,
        leading=35,
        textColor=INK,
        alignment=TA_LEFT,
        spaceAfter=10,
    )
)
styles.add(
    ParagraphStyle(
        name="H1ko",
        fontName="Malgun-Bold",
        fontSize=17,
        leading=24,
        textColor=INK,
        spaceBefore=2,
        spaceAfter=9,
    )
)
styles.add(
    ParagraphStyle(
        name="H2ko",
        fontName="Malgun-Bold",
        fontSize=11,
        leading=16,
        textColor=PURPLE,
        spaceBefore=8,
        spaceAfter=5,
    )
)
styles.add(
    ParagraphStyle(
        name="Bodyko",
        fontName="Malgun",
        fontSize=9,
        leading=14,
        textColor=INK,
        spaceAfter=5,
    )
)
styles.add(
    ParagraphStyle(
        name="Smallko",
        fontName="Malgun",
        fontSize=7.5,
        leading=11,
        textColor=GRAY,
        spaceAfter=3,
    )
)
styles.add(
    ParagraphStyle(
        name="Tableko",
        fontName="Malgun",
        fontSize=7.5,
        leading=10.5,
        textColor=INK,
    )
)
styles.add(
    ParagraphStyle(
        name="TableHeadko",
        fontName="Malgun-Bold",
        fontSize=7.5,
        leading=10.5,
        textColor=colors.white,
    )
)
styles.add(
    ParagraphStyle(
        name="Calloutko",
        fontName="Malgun-Bold",
        fontSize=9,
        leading=14,
        textColor=INK,
        backColor=LAVENDER,
        borderColor=PURPLE,
        borderWidth=0.5,
        borderPadding=8,
        spaceBefore=5,
        spaceAfter=8,
    )
)


def p(text: str, style: str = "Bodyko") -> Paragraph:
    return Paragraph(text, styles[style])


class ArchitectureDiagram(Flowable):
    """Vector architecture chart with pipeline roles and decision branches."""

    def __init__(self, width=175 * mm, height=65 * mm):
        super().__init__()
        self.width = width
        self.height = height

    def draw(self):
        canvas = self.canv
        scale_x = self.width / 500
        scale_y = self.height / 185
        canvas.saveState()
        canvas.scale(scale_x, scale_y)

        def box(x, y, w, h, label, fill, stroke=PURPLE, font=8):
            canvas.setFillColor(fill)
            canvas.setStrokeColor(stroke)
            canvas.roundRect(x, y, w, h, 6, fill=1, stroke=1)
            canvas.setFillColor(INK)
            canvas.setFont("Malgun-Bold", font)
            lines = label.split("\n")
            top = y + h / 2 + (len(lines) - 1) * 5
            for idx, line in enumerate(lines):
                canvas.drawCentredString(x + w / 2, top - idx * 11, line)

        def arrow(x1, y1, x2, y2, color=GRAY):
            canvas.setStrokeColor(color)
            canvas.setFillColor(color)
            canvas.setLineWidth(1.3)
            canvas.line(x1, y1, x2, y2)
            import math

            angle = math.atan2(y2 - y1, x2 - x1)
            size = 5
            points = [
                (x2, y2),
                (
                    x2 - size * math.cos(angle - 0.45),
                    y2 - size * math.sin(angle - 0.45),
                ),
                (
                    x2 - size * math.cos(angle + 0.45),
                    y2 - size * math.sin(angle + 0.45),
                ),
            ]
            path = canvas.beginPath()
            path.moveTo(*points[0])
            path.lineTo(*points[1])
            path.lineTo(*points[2])
            path.close()
            canvas.drawPath(path, fill=1, stroke=0)

        box(4, 145, 76, 30, "정보 수집", LAVENDER)
        box(4, 95, 76, 30, "유머·토론 수집", LAVENDER)
        box(4, 45, 76, 30, "이미지 수집", LAVENDER)
        box(
            105,
            80,
            84,
            65,
            "PostgreSQL\n자료 + 이미지 BYTEA",
            PEACH,
            stroke=colors.HexColor("#E69352"),
        )
        box(
            220, 80, 95, 65, "콘텐츠 생성\n카피 → 레이아웃", MINT, stroke=colors.HexColor("#48A47A")
        )
        box(340, 80, 88, 65, "독립 검수\n규칙 + 언어 점수", LAVENDER)
        box(440, 148, 56, 27, "게시 대기", MINT, stroke=colors.HexColor("#48A47A"), font=6.5)
        box(440, 97, 56, 27, "수정", PEACH, stroke=colors.HexColor("#E69352"), font=7)
        box(440, 45, 56, 27, "탈락 보관", colors.HexColor("#FBE7EA"), stroke=RED, font=6.5)
        box(340, 8, 88, 27, "수집 보완 대기", PEACH, stroke=colors.HexColor("#E69352"), font=6.5)

        arrow(80, 160, 105, 130)
        arrow(80, 110, 105, 112)
        arrow(80, 60, 105, 96)
        arrow(189, 112, 220, 112)
        arrow(315, 112, 340, 112)
        arrow(428, 135, 440, 161, GREEN)
        arrow(428, 110, 440, 110, PURPLE)
        arrow(440, 103, 415, 60, PURPLE)
        arrow(415, 60, 315, 60, PURPLE)
        arrow(315, 60, 267, 80, PURPLE)
        arrow(374, 80, 374, 35, RED)
        arrow(410, 80, 440, 70, RED)
        canvas.restoreState()


class ReviewLoopDiagram(Flowable):
    """Reviewer decision and bounded retry flow."""

    def __init__(self, width=175 * mm, height=42 * mm):
        super().__init__()
        self.width = width
        self.height = height

    def draw(self):
        c = self.canv
        sx, sy = self.width / 500, self.height / 125
        c.saveState()
        c.scale(sx, sy)
        nodes = [
            (8, 53, 90, 38, "초안 생성", MINT),
            (130, 53, 112, 38, "독립 검수·점수화", LAVENDER),
            (305, 92, 88, 26, "게시 대기", MINT),
            (305, 53, 88, 26, "문제 분류", PEACH),
            (305, 14, 88, 26, "수집 보완", PEACH),
            (413, 53, 78, 26, "생성 수정", LAVENDER),
            (413, 14, 78, 26, "탈락 보관", colors.HexColor("#FBE7EA")),
        ]
        for x, y, w, h, label, fill in nodes:
            c.setFillColor(fill)
            c.setStrokeColor(PURPLE if fill == LAVENDER else GRAY)
            c.roundRect(x, y, w, h, 6, fill=1, stroke=1)
            c.setFillColor(INK)
            c.setFont("Malgun-Bold", 8)
            c.drawCentredString(x + w / 2, y + h / 2 - 3, label)

        def line(points):
            c.setStrokeColor(GRAY)
            c.setLineWidth(1.2)
            path = c.beginPath()
            path.moveTo(*points[0])
            for point in points[1:]:
                path.lineTo(*point)
            c.drawPath(path)
            x1, y1 = points[-2]
            x2, y2 = points[-1]
            c.setFillColor(GRAY)
            c.circle(x2, y2, 1.5, fill=1, stroke=0)

        line([(98, 72), (130, 72)])
        line([(242, 78), (278, 78), (278, 105), (305, 105)])
        line([(242, 72), (305, 66)])
        line([(242, 64), (278, 64), (278, 27), (305, 27)])
        line([(393, 66), (413, 66)])
        line([(452, 79), (452, 112), (185, 112), (185, 91)])
        line([(452, 53), (452, 40)])
        c.restoreState()


class VerticalPipelineDiagram(Flowable):
    """Detailed top-down pipeline matching the requested architecture style."""

    def __init__(self, width=175 * mm, height=238 * mm):
        super().__init__()
        self.width = width
        self.height = height

    def draw(self):
        c = self.canv
        c.saveState()
        y_ratio = 700 / 1000
        c.scale(self.width / 500, self.height / 700)

        def box(x, y, w, h, title, detail="", fill=LAVENDER, stroke=PURPLE, fs=8):
            y *= y_ratio
            h *= y_ratio
            c.setFillColor(fill)
            c.setStrokeColor(stroke)
            c.setLineWidth(1.1)
            c.roundRect(x, y, w, h, 7, fill=1, stroke=1)
            c.setFillColor(INK)
            c.setFont("Malgun-Bold", fs)
            if detail:
                c.drawCentredString(x + w / 2, y + h - 11, title)
                c.setFont("Malgun", fs - 1.3)
                for idx, line in enumerate(detail.split("\n")):
                    c.drawCentredString(x + w / 2, y + h - 21 - idx * 8, line)
            else:
                c.drawCentredString(x + w / 2, y + h / 2 - 3, title)

        def decision(cx, y, w, h, title, detail=""):
            y *= y_ratio
            h *= y_ratio
            path = c.beginPath()
            path.moveTo(cx, y + h)
            path.lineTo(cx + w / 2, y + h / 2)
            path.lineTo(cx, y)
            path.lineTo(cx - w / 2, y + h / 2)
            path.close()
            c.setFillColor(colors.HexColor("#F1ECFF"))
            c.setStrokeColor(PURPLE)
            c.setLineWidth(1.2)
            c.drawPath(path, fill=1, stroke=1)
            c.setFillColor(INK)
            c.setFont("Malgun-Bold", 7.2)
            c.drawCentredString(cx, y + h / 2 + (4 if detail else -2), title)
            if detail:
                c.setFont("Malgun", 5.9)
                c.drawCentredString(cx, y + h / 2 - 7, detail)

        def arrow(points, color=GRAY, label=None, label_at=None):
            points = [(x, y * y_ratio) for x, y in points]
            c.setStrokeColor(color)
            c.setFillColor(color)
            c.setLineWidth(1.2)
            path = c.beginPath()
            path.moveTo(*points[0])
            for point in points[1:]:
                path.lineTo(*point)
            c.drawPath(path)
            import math

            x1, y1 = points[-2]
            x2, y2 = points[-1]
            angle = math.atan2(y2 - y1, x2 - x1)
            size = 6
            tri = c.beginPath()
            tri.moveTo(x2, y2)
            tri.lineTo(x2 - size * math.cos(angle - 0.5), y2 - size * math.sin(angle - 0.5))
            tri.lineTo(x2 - size * math.cos(angle + 0.5), y2 - size * math.sin(angle + 0.5))
            tri.close()
            c.drawPath(tri, fill=1, stroke=0)
            if label and label_at:
                c.setFillColor(INK)
                c.setFont("Malgun", 6.5)
                c.drawCentredString(label_at[0], label_at[1] * y_ratio, label)

        # Crawler tier
        c.setFillColor(GRAY)
        box(
            8,
            895,
            150,
            66,
            "정보 수집 에이전트",
            "신작·방영·행사 facts\n공식 출처 URL · 날짜 · 근거",
            LAVENDER,
        )
        box(
            175,
            895,
            150,
            66,
            "유머·토론 수집 에이전트",
            "소재·논점 후보\n검증 전에는 사실로 승격 금지",
            LAVENDER,
        )
        box(
            342,
            895,
            150,
            66,
            "이미지 수집 에이전트",
            "공식 이미지 · 출처·권리\n원본 바이너리 + OCR/설명 메타데이터",
            LAVENDER,
        )
        box(
            74,
            795,
            352,
            66,
            "수집 적재 경계 · PostgreSQL",
            "agent_records / request_id / source-fact-image 연결\nimage_blobs BYTEA · SHA-256 중복 방지",
            PEACH,
            colors.HexColor("#E69352"),
            8,
        )
        arrow([(83, 895), (150, 861)])
        arrow([(250, 895), (250, 861)])
        arrow([(417, 895), (350, 861)])

        # Generation run
        c.setFillColor(PURPLE)
        c.setFont("Malgun-Bold", 9)
        c.drawCentredString(250, 765 * y_ratio, "생성 실행 · request_id로 DB 자료만 소비")
        box(
            116,
            700,
            268,
            48,
            "요청 입력 및 자료 로드",
            "대상·콘텐츠 유형·기간·출처/fact/image 묶음 조회",
            PALE,
            GRAY,
            8,
        )
        box(
            116,
            620,
            268,
            54,
            "결정론적 사전 검사",
            "스키마 · 자료 신선도 · 출처 연결 · 3~10장 범위\n공식성/이용 상태 · 재사용 여부 · 이미지 storage key 확인",
            PALE,
            GRAY,
            8,
        )
        decision(250, 530, 268, 64, "적격 자료가 충분한가?", "검증 fact + 공식 출처 + 이미지 근거")
        box(
            8,
            530,
            88,
            62,
            "수집 보완 대기",
            "출처/권리/이미지\n자료 부족",
            PEACH,
            colors.HexColor("#E69352"),
            7,
        )
        box(
            116,
            438,
            268,
            66,
            "콘텐츠 생성 에이전트 · LangGraph",
            "copy_writer: 근거에 연결된 한국어 카피\n3~10장 · 카드별 fact_id/image_id 지정",
            MINT,
            colors.HexColor("#48A47A"),
            8,
        )
        box(
            116,
            350,
            268,
            62,
            "레이아웃 디렉터",
            "원본 전체 표시 · contain/contain_blur\n확대 1.0 · 오프셋 0 · 텍스트 프레임 검사",
            MINT,
            colors.HexColor("#48A47A"),
            8,
        )
        box(
            116,
            267,
            268,
            58,
            "콘텐츠 DB에 초안 저장",
            "content_drafts + content_cards\n카피·fact/image 연결·레이아웃·버전",
            PEACH,
            colors.HexColor("#E69352"),
            8,
        )
        box(
            116,
            185,
            268,
            56,
            "독립 평가·검수 에이전트",
            "결정론적 차단 검사 + OpenAI LLM 언어·안전 평가\n최종 PNG 픽셀 단위 비전 QA는 미구현",
            LAVENDER,
            PURPLE,
            7.2,
        )
        arrow([(250, 795), (250, 748)])
        arrow([(250, 700), (250, 674)])
        arrow([(250, 620), (250, 594)])
        arrow([(116, 562), (96, 562)], RED, "불충분", (105, 574))
        arrow([(250, 530), (250, 504)], GREEN, "충분", (270, 517))
        arrow([(250, 438), (250, 412)])
        arrow([(250, 350), (250, 325)])
        arrow([(250, 267), (250, 241)])

        # Reviewer and storage outcomes
        box(
            12, 40, 112, 52, "게시 대기열", "통과만 feed_queue", MINT, colors.HexColor("#48A47A"), 7
        )
        box(136, 40, 112, 52, "문제 카드 수정", "카피·레이아웃만 갱신", LAVENDER, PURPLE, 7)
        box(
            260,
            40,
            112,
            52,
            "탈락 보관",
            "반려 누적 3회째 + 사유",
            colors.HexColor("#FBE7EA"),
            RED,
            6.6,
        )
        box(
            384,
            40,
            104,
            52,
            "수집 보완",
            "근거·권리·이미지",
            PEACH,
            colors.HexColor("#E69352"),
            6.7,
        )
        # Explicit review decision and vertically ordered outcomes.
        arrow([(250, 185), (250, 174)])
        decision(250, 140, 128, 34, "최종 판정", "점수·치명 이슈·실패 횟수")
        arrow([(250, 140), (250, 110)])
        arrow([(250, 110), (68, 110), (68, 92)], GREEN, "통과", (93, 123))
        arrow([(250, 110), (192, 110), (192, 92)], PURPLE, "1·2회 반려: 문제 부분 수정", (193, 123))
        arrow([(250, 110), (316, 110), (316, 92)], RED, "3회째 탈락", (324, 123))
        arrow([(384, 205), (436, 205), (436, 92)], RED, "자료·권리 결함", (421, 173))
        # Regeneration loop from revision queue back to authoring node.
        arrow(
            [(192, 92), (4, 92), (4, 470), (116, 470)],
            PURPLE,
            "문제 카드만 qa_report 반영 · 3회째 탈락",
            (77, 300),
        )
        # QA limitation annotation connector
        arrow([(384, 212), (390, 212)], colors.HexColor("#E69352"))
        c.restoreState()


class ReviewerDetailDiagram(Flowable):
    """Expanded review gate and retention paths in a readable vertical layout."""

    def __init__(self, width=175 * mm, height=208 * mm):
        super().__init__()
        self.width = width
        self.height = height

    def draw(self):
        c = self.canv
        c.saveState()
        y_ratio = 590 / 820
        c.scale(self.width / 500, self.height / 590)

        def box(x, y, w, h, title, details, fill=LAVENDER, stroke=PURPLE):
            y *= y_ratio
            h *= y_ratio
            c.setFillColor(fill)
            c.setStrokeColor(stroke)
            c.roundRect(x, y, w, h, 7, fill=1, stroke=1)
            c.setFillColor(INK)
            c.setFont("Malgun-Bold", 8)
            c.drawCentredString(x + w / 2, y + h - 12, title)
            c.setFont("Malgun", 6.8)
            for i, line in enumerate(details.split("\n")):
                c.drawCentredString(x + w / 2, y + h - 23 - i * 9, line)

        def link(points, color=GRAY, label=None, label_at=None):
            points = [(x, y * y_ratio) for x, y in points]
            c.setStrokeColor(color)
            c.setFillColor(color)
            c.setLineWidth(1.2)
            path = c.beginPath()
            path.moveTo(*points[0])
            for pt in points[1:]:
                path.lineTo(*pt)
            c.drawPath(path)
            import math

            x1, y1 = points[-2]
            x2, y2 = points[-1]
            a = math.atan2(y2 - y1, x2 - x1)
            s = 6
            tri = c.beginPath()
            tri.moveTo(x2, y2)
            tri.lineTo(x2 - s * math.cos(a - 0.5), y2 - s * math.sin(a - 0.5))
            tri.lineTo(x2 - s * math.cos(a + 0.5), y2 - s * math.sin(a + 0.5))
            tri.close()
            c.drawPath(tri, fill=1, stroke=0)
            if label and label_at:
                c.setFillColor(INK)
                c.setFont("Malgun", 6.5)
                c.drawCentredString(label_at[0], label_at[1] * y_ratio, label)

        c.setFillColor(GRAY)
        box(
            108,
            716,
            284,
            58,
            "1 · 결정론적 차단 검사",
            "사실-출처 연결 · 권리 상태 · 이미지 연관성\n카드 수 · 프레임 초과 · 확대/이동 · 수치/날짜 형식",
            PALE,
            GRAY,
        )
        box(
            108,
            626,
            284,
            58,
            "2 · 독립 LLM 언어·안전 평가",
            "한국어 자연스러움 · 번역투 · 명료성 · 카드 흐름\n안전·존중 점수와 혐오/성적 대상화 hard blocker",
            LAVENDER,
            PURPLE,
        )
        box(
            108,
            536,
            284,
            58,
            "3 · 가중 점수 집계",
            "사실 25 · 이미지/권리 15 · 전체 노출 10\n레이아웃 15 · 한국어/번역 20 · 구성 5 · 안전 10",
            PEACH,
            colors.HexColor("#E69352"),
        )
        box(
            108,
            446,
            284,
            58,
            "4 · 통과 관문",
            "전체 ≥ 85/100 · 각 카드 ≥ 75/100\n언어 ≥ 3/5 · 안전·존중 ≥ 4/5 · blocker 0",
            MINT,
            colors.HexColor("#48A47A"),
        )
        box(
            108,
            348,
            284,
            62,
            "5 · 실패 원인 분류",
            "페이지 특정 카피 결함 → 해당 카드만 수정\n근거/출처/권리 결함 → 별도 수집 보완\n전역 카피 결함만 덱 전체 수정",
            colors.HexColor("#FFF8DD"),
            colors.HexColor("#D6A522"),
        )
        link([(250, 716), (250, 684)])
        link([(250, 626), (250, 594)])
        link([(250, 536), (250, 504)])
        link([(250, 446), (250, 410)])

        box(
            16,
            238,
            140,
            64,
            "통과",
            "approved_for_feed\nfeed_queue에 저장\n피드 게시는 별도 발행 단계",
            MINT,
            colors.HexColor("#48A47A"),
        )
        box(
            180,
            238,
            140,
            64,
            "수정 가능",
            "문제 카드의 카피/레이아웃 수정\n나머지 카드 내용은 보존\nqa_report 대상만 재평가",
            LAVENDER,
            PURPLE,
        )
        box(
            344,
            238,
            140,
            64,
            "자료 결함",
            "generation 금지\nneeds_crawler_update\n새 근거 적재 후 새 생성 실행",
            PEACH,
            colors.HexColor("#E69352"),
        )
        link([(200, 348), (86, 302)], GREEN, "통과", (127, 327))
        link([(250, 348), (250, 302)], PURPLE, "수정 가능", (282, 325))
        link([(300, 348), (414, 302)], RED, "근거/권리", (375, 327))

        box(
            180,
            105,
            140,
            74,
            "세 번째 검수도 미달",
            "누적 반려 3회\nrejected_archive\n점수·QA 사유·시도 횟수 보관",
            colors.HexColor("#FBE7EA"),
            RED,
        )
        link([(250, 238), (250, 179)], RED, "재검수 실패", (300, 207))
        link(
            [(180, 270), (112, 270), (112, 695), (108, 695)],
            PURPLE,
            "1·2회 반려 → 문제 부분 수정",
            (61, 480),
        )

        box(
            68,
            6,
            364,
            67,
            "보존/노출 시점은 feed 상태와 물리 삭제를 분리",
            "팝업·행사: 종료 다음 날 피드 제외 · 30일 유예 뒤 정리\n방영 일정: 방영일 이후 30일까지 노출 · 종료일 미정은 기본 90일 유효\nvalid_until(노출 종료)와 purge_at(DB 정리)을 별도 기록",
            PALE,
            GRAY,
        )
        link([(250, 105), (250, 73)], GRAY)
        c.restoreState()


class DetailedArchitecturePage(Flowable):
    """Three focused, top-down diagrams with orthogonal non-crossing routes."""

    def __init__(self, section: str, width=175 * mm, height=225 * mm):
        super().__init__()
        self.section = section
        self.width = width
        self.height = height

    def draw(self):
        c = self.canv
        c.saveState()
        c.scale(self.width / 500, self.height / 700)

        fills = {
            "source": colors.HexColor("#FFF1D6"),
            "process": colors.HexColor("#F5F6FA"),
            "llm": colors.HexColor("#EAF1FF"),
            "db": colors.HexColor("#FFF4D9"),
            "pass": colors.HexColor("#E4F6EB"),
            "warn": colors.HexColor("#FFF0E5"),
            "fail": colors.HexColor("#FCE8EB"),
            "decision": colors.HexColor("#F1ECFF"),
        }
        strokes = {
            "source": colors.HexColor("#D6A64E"),
            "process": colors.HexColor("#73758A"),
            "llm": colors.HexColor("#416FC7"),
            "db": colors.HexColor("#C99632"),
            "pass": colors.HexColor("#3C9B68"),
            "warn": colors.HexColor("#D98139"),
            "fail": colors.HexColor("#C74C5D"),
            "decision": colors.HexColor("#8367D8"),
        }

        def process(x, y, w, h, title, detail="", kind="process", font=8.2):
            c.setFillColor(fills[kind])
            c.setStrokeColor(strokes[kind])
            c.setLineWidth(1.0)
            c.roundRect(x, y, w, h, 5, fill=1, stroke=1)
            c.setFillColor(INK)
            c.setFont("Malgun-Bold", font)
            c.drawCentredString(x + w / 2, y + h - 13, title)
            if detail:
                c.setFont("Malgun", 7.0)
                lines = detail.split("\n")
                for index, line in enumerate(lines):
                    c.drawCentredString(x + w / 2, y + h - 25 - index * 9, line)

        def cylinder(x, y, w, h, title, detail=""):
            c.setFillColor(fills["db"])
            c.setStrokeColor(strokes["db"])
            c.setLineWidth(1.0)
            eh = 11
            c.rect(x, y + eh / 2, w, h - eh, fill=1, stroke=0)
            c.ellipse(x, y + h - eh, x + w, y + h + eh, fill=1, stroke=1)
            c.arc(x, y - eh, x + w, y + eh, 180, 180)
            c.line(x, y + eh / 2, x, y + h - eh / 2)
            c.line(x + w, y + eh / 2, x + w, y + h - eh / 2)
            c.setFillColor(INK)
            c.setFont("Malgun-Bold", 8.0)
            c.drawCentredString(x + w / 2, y + h - 16, title)
            c.setFont("Malgun", 6.8)
            for index, line in enumerate(detail.split("\n")):
                c.drawCentredString(x + w / 2, y + h - 28 - index * 8, line)

        def diamond(cx, y, w, h, title, detail=""):
            c.setFillColor(fills["decision"])
            c.setStrokeColor(strokes["decision"])
            c.setLineWidth(1.0)
            path = c.beginPath()
            path.moveTo(cx, y + h)
            path.lineTo(cx + w / 2, y + h / 2)
            path.lineTo(cx, y)
            path.lineTo(cx - w / 2, y + h / 2)
            path.close()
            c.drawPath(path, fill=1, stroke=1)
            c.setFillColor(INK)
            c.setFont("Malgun-Bold", 7.8)
            c.drawCentredString(cx, y + h / 2 + (5 if detail else -3), title)
            if detail:
                c.setFont("Malgun", 6.2)
                c.drawCentredString(cx, y + h / 2 - 8, detail)

        def arrow(points, label=None, color=GRAY, label_at=None):
            c.setStrokeColor(color)
            c.setFillColor(color)
            c.setLineWidth(1.0)
            path = c.beginPath()
            path.moveTo(*points[0])
            for point in points[1:]:
                path.lineTo(*point)
            c.drawPath(path)
            import math

            x1, y1 = points[-2]
            x2, y2 = points[-1]
            angle = math.atan2(y2 - y1, x2 - x1)
            size = 5
            head = c.beginPath()
            head.moveTo(x2, y2)
            head.lineTo(x2 - size * math.cos(angle - 0.48), y2 - size * math.sin(angle - 0.48))
            head.lineTo(x2 - size * math.cos(angle + 0.48), y2 - size * math.sin(angle + 0.48))
            head.close()
            c.drawPath(head, fill=1, stroke=0)
            if label and label_at:
                c.setFillColor(INK)
                c.setFont("Malgun", 6.4)
                c.drawCentredString(label_at[0], label_at[1], label)

        def group(x, y, w, h, title):
            c.saveState()
            c.setDash(4, 3)
            c.setStrokeColor(PURPLE)
            c.setFillColor(colors.white)
            c.roundRect(x, y, w, h, 8, fill=0, stroke=1)
            c.restoreState()
            c.setFillColor(PURPLE)
            c.setFont("Malgun-Bold", 8)
            c.drawString(x + 12, y + h - 13, title)

        if self.section == "crawl":
            process(
                135,
                638,
                230,
                42,
                "수집 작업 설정",
                "한국 우선 후보 · 출처 허용목록 · 요청 간격",
                "source",
            )
            # Three independent crawler agents.
            process(
                8,
                535,
                150,
                67,
                "정보 수집 에이전트",
                "신작·방영·행사 후보\n날짜·장소·조건 fact와 URL",
                "source",
                7.8,
            )
            process(
                175,
                535,
                150,
                67,
                "유머·토론 수집 에이전트",
                "밈·논점 후보\n검증되지 않은 주장은 사실과 분리",
                "source",
                7.5,
            )
            process(
                342,
                535,
                150,
                67,
                "이미지 수집 에이전트",
                "공식 이미지 후보·원본 파일\n출처·권리·OCR·설명 메타데이터",
                "source",
                7.5,
            )
            # Split from config into each crawler, no connector crossings.
            arrow([(250, 638), (250, 620), (83, 620), (83, 602)])
            arrow([(250, 638), (250, 602)])
            arrow([(250, 620), (417, 620), (417, 602)])
            process(
                115,
                445,
                270,
                54,
                "페이지 캡처와 콘텐츠 추출",
                "본문·구조화 데이터 · 날짜/고유명사 · 이미지 내려받기/OCR",
                "process",
            )
            # Merge bus, with each branch entering from a dedicated x position.
            arrow([(83, 535), (83, 518), (250, 518), (250, 499)])
            arrow([(250, 535), (250, 499)])
            arrow([(417, 535), (417, 518), (250, 518), (250, 499)])
            process(
                115,
                355,
                270,
                54,
                "정규화와 근거 연결",
                "한국어 표기·시간대 정리 · fact/source/image ID 연결",
                "process",
            )
            arrow([(250, 445), (250, 409)])
            process(
                115,
                265,
                270,
                54,
                "수집 품질·적격성 검사",
                "HTTPS·공식성·최신성 · 필수 행사 정보 · 파일/권리 상태",
                "process",
            )
            arrow([(250, 355), (250, 319)])
            diamond(250, 175, 150, 68, "패키지 적재 가능?", "필수 근거와 출처가 유효한가")
            arrow([(250, 265), (250, 243)])
            process(
                8,
                177,
                118,
                61,
                "오류 격리 / 재수집",
                "실패 URL·오류 코드\n재시도/수동 보완 큐",
                "warn",
                7.6,
            )
            arrow([(175, 209), (126, 209)], "아니오", RED, (148, 222))
            process(
                155,
                103,
                190,
                48,
                "중복 제거·request_id 묶기",
                "콘텐츠 패키지 확정 · image hash 검사",
                "process",
                7.8,
            )
            arrow([(250, 175), (250, 151)], "예", GREEN, (270, 162))
            cylinder(
                135,
                25,
                230,
                60,
                "PostgreSQL 자료 + image_blobs",
                "agent_records · sources/facts/images · 상태\nBYTEA 원본 + SHA-256 · 수집 실행 종료",
            )
            arrow([(250, 103), (250, 85)])
            c.setFillColor(GRAY)
            c.setFont("Malgun", 6.7)
            c.drawCentredString(250, 7, "콘텐츠 생성은 별도 generate-db <request_id> 실행")

        elif self.section == "generation":
            process(145, 648, 210, 38, "생성 실행 요청", "generate-db <request_id>", "process")
            cylinder(
                135,
                555,
                230,
                61,
                "PostgreSQL 자료 조회",
                "request_id의 facts·sources·images\nimage_blobs의 BYTEA 원본 조회",
            )
            arrow([(250, 648), (250, 616)])
            process(
                120,
                475,
                260,
                49,
                "입력·스키마 검사",
                "자료 연결 · 날짜/상태 · 카드 제약 · 콘텐츠 유형",
                "process",
            )
            arrow([(250, 555), (250, 524)])
            diamond(250, 383, 158, 72, "생성에 쓸 자료가 충분한가?", "검증 사실 + 적격 이미지")
            arrow([(250, 475), (250, 455)])
            process(
                8,
                389,
                122,
                58,
                "수집 보완 대기",
                "근거·출처·권리·이미지\n별도 수집/적재 후 새 요청",
                "warn",
                7.6,
            )
            arrow([(171, 419), (130, 419)], "부족", RED, (149, 432))

            group(58, 108, 384, 240, "콘텐츠 생성 에이전트 · 별도 LangGraph")
            process(
                112,
                270,
                276,
                51,
                "1 · Copy writer / 카피 작성",
                "검증 facts로 3~10장 구성 · 카드마다 fact_id 연결\n외국어 이미지 핵심 문구는 자연스러운 한국어로 설명",
                "llm",
                8.0,
            )
            process(
                112,
                191,
                276,
                51,
                "2 · Layout director / 이미지·레이아웃",
                "카드별 image_id 선택 · 원본 전체 표시 · contain/contain_blur\n확대 1.0 · 오프셋 0 · 제목/본문 프레임 검사",
                "llm",
                7.7,
            )
            process(
                112,
                126,
                276,
                42,
                "3 · 카드 패키지 조립",
                "3~10장 · 순서 · 출처 연결 · 레이아웃 메타데이터",
                "process",
                7.8,
            )
            arrow([(250, 383), (250, 348)], "충분", GREEN, (274, 363))
            arrow([(250, 270), (250, 242)])
            arrow([(250, 191), (250, 168)])
            process(
                145,
                48,
                210,
                42,
                "독립 검수 에이전트로 인계",
                "초안 작성자는 통과/게시 판정 권한 없음",
                "decision",
                7.5,
            )
            arrow([(250, 108), (250, 90)])
            process(
                345,
                400,
                145,
                61,
                "이미지 잘림 보장 범위",
                "현재 레이아웃 규칙/메타데이터 검사\n최종 렌더 픽셀 QA 미구현",
                "warn",
                7.3,
            )

        elif self.section == "review":
            process(140, 648, 220, 38, "검수 입력", "생성 패키지 + 출처/fact/image 연결", "process")
            process(
                110,
                568,
                280,
                50,
                "A · 결정론적 가드레일",
                "사실-출처 · 공식성/권리 · 이미지-사실 연결\n카드 수 · 반복/길이 · 레이아웃 파라미터 · 날짜 표기",
                "process",
            )
            arrow([(250, 648), (250, 618)])
            diamond(250, 478, 140, 68, "치명 차단 항목이 있는가?", "총점으로 상쇄 불가")
            arrow([(250, 568), (250, 546)])
            process(
                8,
                485,
                137,
                62,
                "근거·권리 결함",
                "needs_crawler_update\n증거 수정 전 생성 금지",
                "warn",
                7.5,
            )
            arrow([(180, 512), (145, 512)], "예", RED, (161, 525))
            process(
                115,
                295,
                270,
                50,
                "B · 독립 LLM 언어·안전 평가",
                "한국어 자연스러움 · 번역 · 명료성 · 카드 흐름\n안전·존중 0~5점 · 안전 blocker는 총점으로 상쇄 불가",
                "llm",
                7.8,
            )
            arrow([(250, 444), (250, 345)], "OpenAI 검수", GREEN, (278, 390))
            process(
                115,
                215,
                270,
                50,
                "C · 점수 집계와 이슈 분류",
                "사실 25 · 이미지/권리 15 · 전체 노출 10 · 레이아웃 15\n한국어/번역 20 · 구성 5 · 안전/존중 10",
                "process",
                7.8,
            )
            arrow([(250, 295), (250, 265)])
            diamond(
                250,
                128,
                148,
                70,
                "통과 기준을 모두 만족?",
                "85점 · 카드 75점 · 언어 3/5 · 안전 4/5",
            )
            arrow([(250, 215), (250, 198)])
            process(
                358, 136, 132, 52, "게시 대기열", "approved_for_feed\nfeed_queue 저장", "pass", 7.6
            )
            arrow([(324, 163), (358, 163)], "통과", GREEN, (341, 177))
            diamond(250, 38, 130, 58, "반려 누적 < 3회?", "재시도 상한")
            arrow([(250, 128), (250, 96)], "미달", RED, (273, 111))
            process(
                8,
                37,
                140,
                56,
                "문제 카드만 수정",
                "카드별 qa_report 반영\n통과 카드 내용은 보존",
                "llm",
                7.2,
            )
            process(
                352,
                37,
                140,
                56,
                "3회 반려 → 탈락 보관",
                "rejected_archive\n점수·이유·시도 횟수 저장",
                "fail",
                7.2,
            )
            arrow([(185, 67), (148, 67)], "예", PURPLE, (165, 80))
            arrow([(315, 67), (352, 67)], "아니오", RED, (334, 80))

        c.restoreState()


def table(data, widths, header=True):
    t = Table(data, colWidths=widths, repeatRows=1 if header else 0, hAlign="LEFT")
    commands = [
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#DCD8E5")),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("BACKGROUND", (0, 1 if header else 0), (-1, -1), colors.white),
    ]
    if header:
        commands.extend(
            [
                ("BACKGROUND", (0, 0), (-1, 0), INK),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ]
        )
    t.setStyle(TableStyle(commands))
    return t


def bullet(text):
    return p("• " + text)


def footer(canvas, doc):
    canvas.saveState()
    canvas.setStrokeColor(colors.HexColor("#E6E1EF"))
    canvas.line(18 * mm, 14 * mm, 192 * mm, 14 * mm)
    canvas.setFont("Malgun", 7)
    canvas.setFillColor(GRAY)
    canvas.drawString(18 * mm, 9 * mm, "me;nu · 에이전트 아키텍처 설계 보고서")
    canvas.drawRightString(192 * mm, 9 * mm, f"{doc.page}")
    canvas.restoreState()


def build():
    OUT.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(OUT),
        pagesize=A4,
        rightMargin=18 * mm,
        leftMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=20 * mm,
        title="me;nu 콘텐츠 에이전트 아키텍처 보고서",
        author="me;nu",
    )
    story = []

    story += [Spacer(1, 9 * mm), p("me;nu 콘텐츠 에이전트", "CoverTitle")]
    story += [p("아키텍처 · 품질 평가 · 검수 루프 설계 보고서", "H1ko")]
    story += [
        p(
            "수집 3개, 생성 1개, 독립 검수 1개의 역할을 분리해 "
            "근거 기반 카드뉴스가 생성·평가·수정·보관되는 흐름을 정리했습니다."
        ),
        Spacer(1, 4 * mm),
        p(
            "문서 범위: 현재 저장소 구현과 설정을 기준으로 실행 경계, DB 흐름, 점수 기준, 재시도, 이미지 가드레일 및 미구현 항목을 설명합니다.",
            "Smallko",
        ),
        p("핵심 설계 원칙", "H2ko"),
        p(
            "생성 에이전트는 초안을 만들고, 별도 검수 에이전트가 최종 점수와 통과 여부를 판단합니다. "
            "이미지 잘림·권리·사실 같은 차단 조건은 총점으로 상쇄되지 않습니다."
        ),
        p(
            "구성: 3개 수집 에이전트 + 콘텐츠 생성 에이전트 + 평가·검수 에이전트 = 역할 5개",
            "Calloutko",
        ),
        p(
            "세부 아키텍처는 다음 페이지의 하향식 설계도에서 수집부터 최종 라우팅까지 확인할 수 있습니다."
        ),
        Spacer(1, 2 * mm),
        p("문서 기준일: 2026-10-14 · 대상 코드: ani_me;nu 저장소", "Smallko"),
        PageBreak(),
    ]

    story += [p("1. 수집 에이전트와 DB 적재 · 하향식 설계도", "H1ko")]
    story += [
        p(
            "출처 설정부터 수집 에이전트 병렬 실행, 정규화·검증, PostgreSQL에 메타데이터·이미지 바이너리 적재까지입니다. 실패 자료는 적재 전에 격리합니다.",
            "Smallko",
        ),
        DetailedArchitecturePage("crawl"),
        PageBreak(),
    ]

    story += [p("2. 콘텐츠 생성 그래프 · 하향식 설계도", "H1ko")]
    story += [
        p(
            "DB에 적재된 request_id만 입력으로 사용합니다. 카피 작성과 이미지 레이아웃은 생성 에이전트의 하위 그래프이며, 검수는 별도 에이전트로 넘깁니다.",
            "Smallko",
        ),
        DetailedArchitecturePage("generation"),
        PageBreak(),
    ]

    story += [p("3. 평가·검수와 최종 라우팅 · 하향식 설계도", "H1ko")]
    story += [
        p(
            "결정론적 차단 검사와 독립 언어 평가를 분리하고, 통과·수정·보완·보류·탈락의 끝점을 나눴습니다.",
            "Smallko",
        ),
        DetailedArchitecturePage("review"),
        PageBreak(),
    ]

    story += [p("4. 전체 처리 흐름 · 통합 하향식 설계도", "H1ko")]
    story += [
        p(
            "수집 입력부터 데이터베이스, request_id 생성 실행, 독립 검수와 게시/보관까지 한 장으로 연결했습니다. "
            "반려 루프는 왼쪽 외곽 경로로 돌아가며, 사실·출처·권리 결함은 생성 루프와 분리해 수집 보완으로 보냅니다.",
            "Smallko",
        ),
        VerticalPipelineDiagram(width=175 * mm, height=218 * mm),
        PageBreak(),
    ]

    story += [p("1. 에이전트 구성과 책임", "H1ko")]
    story += [
        p(
            "수집기와 생성기는 실행 경계를 분리합니다. 수집기는 자료를 DB에 적재하고 종료하며, "
            "생성기는 적재된 request_id 자료를 읽습니다. 생성 중 웹 탐색이나 이미지 다운로드를 추가하지 않습니다."
        )
    ]
    agent_rows = [
        [
            p("역할", "TableHeadko"),
            p("책임·입출력", "TableHeadko"),
            p("폴더/주요 파일", "TableHeadko"),
        ],
        [
            p("정보 수집", "Tableko"),
            p("방영·신작·행사 사실과 출처 URL을 구조화해 적재", "Tableko"),
            p("agents/information/agent.py · prompt.py", "Tableko"),
        ],
        [
            p("유머·토론 수집", "Tableko"),
            p(
                "소재와 논점 신호를 아이디어로 저장; 검증되지 않은 주장은 사실화하지 않음",
                "Tableko",
            ),
            p("agents/humor/agent.py · prompt.py", "Tableko"),
        ],
        [
            p("이미지 수집", "Tableko"),
            p("공식 이미지 후보, 파일, 출처·권리·OCR·설명을 기록", "Tableko"),
            p("agents/image/agent.py · download.py · prompt.py", "Tableko"),
        ],
        [
            p("콘텐츠 생성", "Tableko"),
            p("검증된 자료에서 3~10장 카피·사실 연결·레이아웃 생성", "Tableko"),
            p("agents/content_generation/graph.py · generation.py · layout_engine.py", "Tableko"),
        ],
        [
            p("독립 검수", "Tableko"),
            p("결정론적 오류와 카드별 언어 품질을 평가해 승인·반환·보관 판정", "Tableko"),
            p("agents/reviewer/agent.py · graph.py · prompt.py", "Tableko"),
        ],
    ]
    story += [table(agent_rows, [31 * mm, 85 * mm, 58 * mm]), Spacer(1, 3 * mm)]
    story += [p("공유 코드", "H2ko")]
    story += [
        bullet("상위 graph.py는 생성·검수 하위 그래프와 bounded retry 경로를 연결합니다."),
        bullet("nodes.py는 입력·권리 사전검사, 생성 초안 실행, 큐·보관 라우팅을 제공합니다."),
        bullet(
            "storage.py는 수집 자료·콘텐츠·점수·게시 대기·탈락 이유와 image_blobs BYTEA를 PostgreSQL에 관리합니다."
        ),
        bullet("agents/README.md와 각 하위 README가 담당 역할과 파일 위치의 진입점입니다."),
        p(
            "생성 에이전트는 자신의 결과를 최종 승인할 수 없습니다. 최종 승인 책임은 reviewer에 한 번만 둡니다.",
            "Calloutko",
        ),
        PageBreak(),
    ]

    story += [p("2. 실행 흐름과 재검수 루프", "H1ko")]
    story += [
        p(
            "검수 결과에 따라 수정 책임자를 다르게 라우팅합니다. 근거 결함은 생성 모델이 추측으로 채우지 않고 수집 보완을 요구합니다."
        )
    ]
    story += [
        p(
            "검수 통과는 게시 대기로, 페이지가 특정된 카피·레이아웃 미달은 해당 카드만 수정해 재검수합니다. 사실·출처·권리 결함은 별도 수집 보완으로 보냅니다. 세 번째 검수에서도 미달이면 탈락 보관합니다.",
            "Calloutko",
        ),
        Spacer(1, 2 * mm),
        p(
            "통과 조건은 총점 85/100 이상, 카드별 75/100 이상, 언어 3/5 이상, 안전·존중 4/5 이상이며 차단 항목이 없어야 합니다.",
            "Smallko",
        ),
        Spacer(1, 2 * mm),
    ]
    routing_rows = [
        [
            p("검수 결과", "TableHeadko"),
            p("다음 단계", "TableHeadko"),
            p("종료 조건", "TableHeadko"),
        ],
        [
            p("통과 후보", "Tableko"),
            p("게시 대기열", "Tableko"),
            p("총점 ≥ 85, 카드별 ≥ 75, 미해결 차단 항목 없음", "Tableko"),
        ],
        [
            p("카피·레이아웃 미달", "Tableko"),
            p("콘텐츠 생성 에이전트 재실행", "Tableko"),
            p("1·2회 반려는 해당 부분 수정; 3회째면 보관", "Tableko"),
        ],
        [
            p("사실·출처·권리·이미지 증거 결함", "Tableko"),
            p("크롤러 보완 대기", "Tableko"),
            p("수정된 자료를 DB에 다시 적재한 뒤 별도 생성 재실행", "Tableko"),
        ],
        [
            p("오프라인 fixture 시험", "Tableko"),
            p("offline_review_only 종료", "Tableko"),
            p("fixture 점수로 게시 대기열에 들어갈 수 없음", "Tableko"),
        ],
    ]
    story += [table(routing_rows, [39 * mm, 55 * mm, 80 * mm]), Spacer(1, 3 * mm)]
    story += [p("반복 횟수 해석", "H2ko")]
    story += [
        p(
            "max_revision=3은 허용되는 누적 검수 반려 횟수입니다. 첫째·둘째 반려는 문제 카드만 수정해 재검수하고, "
            "세 번째 반려에서 rejected_archive에 점수·실패 사유를 남깁니다. 크롤러 보완은 검증 자료가 바뀌는 별도 실행입니다."
        ),
        PageBreak(),
    ]

    story += [p("3. 평가 점수와 차단 정책", "H1ko")]
    story += [
        p(
            "100점은 품질을 비교하고 개선 지점을 설명하는 기준입니다. 치명 오류를 높은 다른 점수로 덮지 못하게 별도 hard blocker를 운용합니다."
        )
    ]
    score_rows = [
        [
            p("평가 차원", "TableHeadko"),
            p("배점", "TableHeadko"),
            p("구체적 판정 기준", "TableHeadko"),
        ],
        [
            p("근거·정보 정확성", "Tableko"),
            p("25", "Tableko"),
            p("공식 출처, 검증 사실, fact-source 연결, 행사 날짜/장소/가격/참여 요건", "Tableko"),
        ],
        [
            p("이미지 적합성·출처/권리", "Tableko"),
            p("15", "Tableko"),
            p(
                "공식성, 출처·권리 상태, 사실과 시각 소재의 관련성, 파일/중복 상태; 위험 감수 사용은 최대 12점",
                "Tableko",
            ),
        ],
        [
            p("이미지 전체 노출", "Tableko"),
            p("10", "Tableko"),
            p("원본 전체 표시, contain 계열, 배율 1.0, 오프셋 0, 알파 피사체 경계", "Tableko"),
        ],
        [
            p("가독성·레이아웃", "Tableko"),
            p("15", "Tableko"),
            p("텍스트 과밀, 제목 길이, 프레임·박스 초과, 이미지 안 외국어 설명", "Tableko"),
        ],
        [
            p("한국어 자연스러움·번역", "Tableko"),
            p("20", "Tableko"),
            p(
                "카드별 자연스러움, 번역 자연스러움, 독자 명료성, 카드 흐름 각 0~5점 평균 환산",
                "Tableko",
            ),
        ],
        [
            p("구성·문체", "Tableko"),
            p("5", "Tableko"),
            p("3~10장 구성, 중복, 사용자에게 확인을 떠넘기는 문장, 어색한 마무리", "Tableko"),
        ],
        [
            p("안전·존중", "Tableko"),
            p("10", "Tableko"),
            p(
                "차별·혐오·성적 대상화·괴롭힘·위협·현실 폭력 조장; 카드별 0~5점 및 hard blocker",
                "Tableko",
            ),
        ],
    ]
    story += [table(score_rows, [43 * mm, 15 * mm, 116 * mm]), Spacer(1, 3 * mm)]
    story += [p("통과/반환 기준", "H2ko")]
    threshold_rows = [
        [p("항목", "TableHeadko"), p("기준", "TableHeadko"), p("미달 시", "TableHeadko")],
        [
            p("전체", "Tableko"),
            p("85 / 100 이상", "Tableko"),
            p("카피·레이아웃 수정 루프", "Tableko"),
        ],
        [
            p("개별 카드", "Tableko"),
            p("75 / 100 이상", "Tableko"),
            p("문제 카드만 수정; 반려 3회째 탈락", "Tableko"),
        ],
        [
            p("언어 항목", "Tableko"),
            p("각 항목 3 / 5 이상", "Tableko"),
            p(
                "자연스러움·번역·명료성·흐름 중 하나라도 3점 미만 또는 안전·존중 4점 미만이면 반환",
                "Tableko",
            ),
        ],
        [
            p("치명 차단", "Tableko"),
            p("미해결 0개", "Tableko"),
            p("권리/근거는 수집 보완; 크롭/카피/안전 표현은 수정; 재시도 소진 시 보관", "Tableko"),
        ],
    ]
    story += [table(threshold_rows, [37 * mm, 42 * mm, 95 * mm]), Spacer(1, 2 * mm)]
    story += [
        p(
            "번역 평가 세부: 일본어 이미지 속 일정, 응모 방법, 당첨 인원 등 핵심 정보를 자연스러운 한국어로 설명해야 합니다. "
            "정확한 날짜가 확인되지 않았으면 연도/확정된 사실까지만 전달하고 독자에게 공식 페이지를 찾아보라고 넘기지 않습니다. "
            "숫자 시각은 ‘11:59’로 쓰며 숫자 자체 뒤에 마침표를 붙이지 않습니다.",
            "Calloutko",
        ),
        PageBreak(),
    ]

    story += [p("4. 자료 흐름과 보존 경계", "H1ko")]
    data_rows = [
        [
            p("데이터", "TableHeadko"),
            p("저장 위치/연결", "TableHeadko"),
            p("생성·검수에서의 용도", "TableHeadko"),
        ],
        [
            p("출처·fact·유머 소재·image metadata", "Tableko"),
            p("PostgreSQL agent_records JSON 레코드; request_id로 묶음", "Tableko"),
            p("근거·캠페인 조건·이미지 출처/권리/설명 확인", "Tableko"),
        ],
        [
            p("이미지 바이너리", "Tableko"),
            p("PostgreSQL image_blobs BYTEA; SHA-256 키로 중복 방지", "Tableko"),
            p("원본 바이너리 반환 및 중복/재사용 검사", "Tableko"),
        ],
        [
            p("생성 콘텐츠·카드", "Tableko"),
            p("content_drafts와 content_cards", "Tableko"),
            p("상태, 카드 문구, fact/image ID, 레이아웃, 점수 저장", "Tableko"),
        ],
        [
            p("게시 대기", "Tableko"),
            p("feed_queue", "Tableko"),
            p("reviewer 최종 통과 콘텐츠만 적재", "Tableko"),
        ],
        [
            p("탈락 콘텐츠", "Tableko"),
            p("rejected_archive", "Tableko"),
            p("재시도 수, 점수, 실패 코드와 반환 이유 보관", "Tableko"),
        ],
    ]
    story += [table(data_rows, [43 * mm, 72 * mm, 59 * mm]), Spacer(1, 3 * mm)]
    story += [p("생성 전후 데이터 경계", "H2ko")]
    story += [
        p(
            "수집 실행: 후보 페이지 → 원문/공식 출처 근거 → 구조화 레코드 + image_blobs → PostgreSQL 적재 → 종료"
        ),
        p(
            "생성 실행: request_id → 적재 자료 검증 → 카드 초안/이미지 연결 → 별도 검수 → 게시 대기 또는 보관"
        ),
        p(
            "수정 시: reviewer의 카드별 점수와 qa_report가 담당 단계로 되돌아가며, 근거를 추측으로 고치는 것은 허용하지 않습니다."
        ),
        p("이미지 정책", "H2ko"),
        bullet("공식 공개 이미지 사용 위험 수용과 공식적인 재사용 허가 확인은 구분해 기록합니다."),
        bullet(
            "이미지가 공식인지, 카드의 사실을 실제로 보여주는지, 출처/권리 확인 상태가 어떤지 함께 보관합니다."
        ),
        bullet(
            "페이지 이미지 전체 표시를 우선해 contain/contain_blur를 적용하고 잘림 위험을 코드로 차단합니다."
        ),
        p("기간성 콘텐츠 보존", "H2ko"),
        p(
            "행사·팝업·응모는 종료/마감 다음 날 피드에서 제외하고 30일 유예 뒤 관련 초안·카드·수집 레코드·미참조 이미지를 정리합니다. "
            "방영 일정은 방영일 후 30일까지 노출하며, 종료일이 특정되지 않은 정보는 기본 90일 유효기간을 둡니다. "
            "valid_until(피드 노출 종료)과 purge_at(DB 삭제 시점)은 구분합니다."
        ),
        PageBreak(),
    ]

    story += [p("5. 실행·비용·현재 한계", "H1ko")]
    story += [p("일반 실행", "H2ko")]
    story += [
        p("1) `discover` / `collect-page` / `extract-page`로 후보와 공식 근거를 수집합니다."),
        p("2) `crawl-ingest`가 검증 패키지와 이미지 바이너리를 PostgreSQL에 적재합니다."),
        p(
            "3) `generate-db &lt;request_id&gt;`가 DB 자료를 읽어 카드뉴스를 생성하고 reviewer를 호출합니다."
        ),
        p(
            "4) `uv run pytest`로 단위·그래프 테스트, `uv run ani-menu graph-smoke`로 오프라인 경로를 검사합니다."
        ),
        p("비용 구조", "H2ko"),
        p(
            "수집 API/페이지 요청은 호출량과 공급자의 제한을 따릅니다. 생성 LLM 호출과 독립 검수 LLM 호출은 각각 토큰 usage를 기록하고 "
            "추정 비용을 콘텐츠 usage 기록에 추가합니다. 수정 루프 횟수만큼 호출이 더해질 수 있습니다. "
            "비용 추정은 코드에 설정된 모델별 가격을 사용하므로, 모델이나 공급 가격이 바뀌면 가격표를 업데이트해야 합니다."
        ),
        p("알려진 한계와 후속 작업", "H2ko"),
        bullet(
            "현재 reviewer는 이미지 원본 픽셀을 직접 보지 않고 OCR·설명·관련 사실·레이아웃 메타데이터로 판단합니다."
        ),
        bullet(
            "그래서 HTML/CSS 카드의 최종 렌더 결과에서 텍스트 겹침·실제 피사체 잘림을 비전 QA로 재확인하는 작업이 남아 있습니다."
        ),
        bullet(
            "이미지 파일 자체가 원본 단계에서 잘렸거나 OCR이 놓친 정보는 자동 검수가 완전히 복구하지 못합니다."
        ),
        bullet(
            "현재 실행 설정은 OpenAI 검수 키가 준비된 정상 경로를 기준으로 설명합니다. 오프라인 fixture는 테스트 전용이며 승인 경로가 아닙니다."
        ),
        p("완료된 검증", "H2ko"),
        p(
            "정적 검사와 테스트 현황은 실행 시점 결과로 갱신해 기록합니다. 실제 LLM 통과 판정과 오프라인 구조 시험은 구분합니다."
        ),
        p("팀 공유 전에", "H2ko"),
        p(
            ".env, 데이터베이스, 수집 이미지 원본은 저장소에 커밋하지 않고, 코드·문서·테스트만 공유합니다. API 자격 증명은 각 팀원이 로컬 환경에 설정합니다."
        ),
        Spacer(1, 4 * mm),
        p(
            "관련 구현 문서: README.md · docs/AGENT_ARCHITECTURE.md · docs/CARD_GUARDRAILS.md · docs/DATA_STORAGE.md",
            "Smallko",
        ),
    ]

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    print(OUT)


if __name__ == "__main__":
    build()
