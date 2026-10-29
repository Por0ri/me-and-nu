# view_agent_content — 에이전트가 만든 글을 눈으로 보는 페이지를 만든다
#
# 에이전트 DB(SQLite)에서 피드에 올라갈 글(feed_queue)과 완성된 카드 이미지를 꺼내 HTML 한 장으로 만든다.
# 글마다 유형 · 연결된 작품 · 날짜 · 품질 점수와, 모델 입장에서 확인할 점(발표일 없음 · 대표 작품 없음 · 같은 원문)을 같이 보여준다.
# 맨 위 "데이터 사전": 에이전트가 저장하는 표 · 칸 전부를 한국어 뜻과 예시 값으로 보여준다 (설명이 없는 새 칸도 빠짐없이 표시)
# 글마다 "저장된 데이터 보기": 글 정보 · 작품 연결 · 원문 · 사실 · 카드 텍스트 · 이미지 · 품질 검수를 칸 이름과 함께 보여준다
# DB 는 읽기만 한다 (임시 파일도 안 만듦). 결과는 model/data/agent_view/ 에 저장 (커밋 안 함)
# --share 경로 를 주면 이미지를 줄여서 HTML 안에 넣은 파일 하나를 만든다 (팀원에게 보내기용)
# --new 예전DB 를 주면 예전 DB 에 없던 글만 보여준다 (새로 받은 글만 확인할 때)
#
#   python model/scripts/view_agent_content.py "C:/Users/06swl/Downloads/ani_menu_local.sqlite3"
#   python model/scripts/view_agent_content.py "…/ani_menu_local.sqlite3" --share "C:/Users/06swl/Downloads/에이전트_글_보기.html"
#   python model/scripts/view_agent_content.py "…/새 파일.sqlite3" --new "…/예전 파일.sqlite3" --share "…/새_글.html"

import base64
import csv
import html
import io
import json
import pathlib
import sqlite3
import sys
import webbrowser
from collections import Counter
from urllib.parse import urlsplit

from PIL import Image

SHARE_WIDTH = 540        # 보내기용 파일의 카드 이미지 가로 (픽셀). 원본은 1장에 1MB 가까이라 줄임

DATA = pathlib.Path(__file__).resolve().parents[1] / "data"
OUT = DATA / "agent_view"
EXT = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "image/gif": "gif"}
CSS = """
body{margin:0;padding:16px;font-family:system-ui,'Malgun Gothic',sans-serif;background:#f4f4f5;color:#18181b}
h1{font-size:20px} h2{font-size:17px} h3{font-size:14px;margin:14px 0 4px} .post,.box{background:#fff;border-radius:12px;padding:16px;margin:0 0 20px;box-shadow:0 1px 3px #0002}
.meta{font-size:13px;color:#52525b;line-height:1.7} .chip{display:inline-block;background:#eef2ff;color:#3730a3;
border-radius:6px;padding:0 6px;margin-right:4px} .warn{color:#b91c1c;font-size:13px;margin-top:6px}
.cards{display:flex;gap:10px;overflow-x:auto;padding:10px 0} .card{flex:0 0 auto;width:300px}
.card img{width:300px;border-radius:8px;display:block} .text{width:300px;min-height:200px;border:1px dashed #a1a1aa;
border-radius:8px;padding:10px;box-sizing:border-box;font-size:13px} .page{font-size:12px;color:#71717a}
details{margin-top:8px;font-size:13px} summary{cursor:pointer;color:#3730a3;font-weight:600}
.scroll{overflow-x:auto} table.kv{border-collapse:collapse;margin:6px 0;width:100%;font-size:12.5px}
table.kv th,table.kv td{border:1px solid #e4e4e7;padding:3px 6px;text-align:left;vertical-align:top;word-break:break-word}
table.kv th{background:#f4f4f5;white-space:nowrap} .code{font-family:Consolas,monospace;color:#6b21a8} .muted{color:#71717a}
"""

# ## 데이터 사전 (에이전트 docs/DATA_STORAGE.md · 코드 · 실제 값 기준)
TABLES = [
    ("feed_queue", "피드 대기열", "검수를 통과해 피드에 올릴 글. 글 하나 = 한 줄", {
        "request_id": "글 번호 (모든 표를 잇는 열쇠)", "topic": "주제 (글 제목처럼 쓰이지만 작품 이름만 있는 경우가 있음)",
        "content_type": "유형 코드 (11개)", "package_json": "글 전체 묶음 (아래 '글 패키지' 칸 전부)",
        "queued_at": "대기열에 들어간 시각", "status": "상태 (ready = 내보낼 수 있음, held_for_quality_review = 검수 대기)",
        "valid_until": "노출 끝 (피드에서 내리는 시각, 화면엔 안 보임)", "purge_at": "DB 에서 완전히 지우는 시각",
        "event_start": "행사 · 발매 시작 날짜 (원문 근거가 있을 때)", "event_end": "행사 · 발매 끝 날짜",
        "event_location": "행사 장소", "event_country": "행사 국가 코드 (KR · JP · US)",
        "event_region": "국내(domestic) · 해외(overseas) 구분 (내부용)"}),
    ("content_drafts", "글 초안", "만든 글 전부 (통과 · 보류 · 탈락 포함)와 품질 검수 결과", {
        "request_id": "글 번호", "topic": "주제", "content_kind": "만든 경로 (information = 정보, humor = 유머)",
        "content_type": "유형 코드", "status": "상태 (ready_for_feed · needs_crawler_update · review_returned · rejected_archived · held_for_quality_review)",
        "package_json": "글 전체 묶음", "valid_until": "노출 끝", "purge_at": "완전 삭제 시각",
        "quality_score": "품질 총점 (100점 만점, 85점 이상 통과)", "quality_report_json": "품질 검수 보고서 (아래 '품질 검수' 칸 전부)",
        "created_at": "만든 시각", "updated_at": "마지막 수정 시각", "event_start": "행사 · 발매 시작", "event_end": "행사 · 발매 끝",
        "event_location": "행사 장소", "event_country": "행사 국가 코드", "event_region": "국내 · 해외 구분"}),
    ("content_cards", "카드", "카드 한 장 = 한 줄. 텍스트와 완성 이미지 열쇠를 따로 저장", {
        "request_id": "글 번호", "page": "몇 번째 장", "role": "역할 (cover 표지 · detail 본문 · ending 마무리)",
        "eyebrow": "머리말 (제목 위 작은 글)", "title_lines_json": "제목 줄 (줄바꿈 단위 목록)", "body_json": "본문 문장 목록",
        "fact_ids_json": "이 카드가 쓴 사실 번호", "image_id": "쓴 이미지 번호", "image_explanation": "이미지에 대한 한국어 설명",
        "layout_json": "화면 배치 (틀 · 이미지 · 글 상자 위치 · 맞춤 방식)", "rendered_image_key": "완성 카드 이미지 열쇠 (이미지 파일 표의 sha256)",
        "visual_mode": "화면 방식 (source_image 원본 이미지 카드 · editorial_graphic 자체 그래픽 카드)",
        "overlay_json": "이미지 위에 덧씌우는 정보"}),
    ("content_work_links", "글 ↔ 작품 연결", "글이 어떤 작품 이야기인지 (me;nu 작품 번호)", {
        "request_id": "글 번호", "menu_work_id": "me;nu 작품 번호", "sort_order": "순서 (1 = 대표 작품)", "is_primary": "대표 작품 여부"}),
    ("content_types", "유형 목록", "글 유형 11개", {
        "type_code": "유형 코드", "label_ko": "한국어 이름", "sort_order": "표시 순서", "active": "사용 중 여부"}),
    ("image_blobs", "이미지 파일", "원본 이미지와 완성 카드 이미지의 바이트. 같은 이미지는 한 번만 저장", {
        "sha256": "이미지 지문 (= 열쇠)", "media_type": "형식 (image/png 등)", "file_size_bytes": "파일 크기 (바이트)",
        "binary_data": "이미지 바이트 자체", "created_at": "저장 시각"}),
    ("rejected_archive", "탈락 보관", "검수에서 3번 탈락한 글", {
        "request_id": "글 번호", "topic": "주제", "content_kind": "만든 경로", "content_type": "유형 코드",
        "review_failures": "검수 탈락 횟수", "reasons_json": "탈락 이유 코드 목록", "package_json": "글 전체 묶음",
        "archived_at": "보관 시각", "purge_at": "완전 삭제 시각"}),
    ("agent_records", "수집 기록", "에이전트가 모은 원자료 (원문 · 사실 · 이미지 · 읽은 공식 페이지 · 소식 후보 · 관심 주제)", {
        "agent_type": "담당 에이전트 (information 정보 · humor 유머 · image 이미지)", "record_id": "기록 번호",
        "record_kind": "기록 종류 (source 원문 · fact 사실 · image_asset 이미지 · official_page_capture 읽은 공식 페이지 · discovery_candidate 소식 후보 · trend_source 관심 주제)",
        "request_id": "글 번호 (어느 수집 묶음인지)", "topic": "주제", "region": "지역", "event_start": "행사 시작", "event_end": "행사 끝",
        "valid_until": "노출 끝", "expires_at": "보관 만료 시각", "status": "상태 (collected · needs_crawler_update · rejected 등)",
        "payload_json": "기록 내용 (종류마다 칸이 다름)", "created_at": "만든 시각", "updated_at": "수정 시각"}),
]
PACKAGE = [
    ("package", "글 정보", "글 패키지(package_json) 맨 위 칸", {
        "request_id": "글 번호", "topic": "주제", "content_kind": "만든 경로 (information · humor)", "content_type": "유형 코드",
        "menu_work_ids": "연결된 me;nu 작품 번호 목록", "audience_region": "독자 지역 (KR = 한국 독자)",
        "region_policy": "지역 우선 정책 (kr_first = 한국 소식 먼저)", "event_start": "행사 · 발매 시작", "event_end": "행사 · 발매 끝",
        "event_location": "행사 장소", "event_country": "행사 국가 코드", "event_region": "국내 · 해외 · 모름",
        "feed_valid_until": "노출 끝", "purge_at": "완전 삭제 시각", "retention_expires_at": "보관 만료 시각",
        "lifecycle": "생애 상태 (active = 진행 중)", "content_plan_reason": "이 유형 · 구성으로 만든 이유",
        "editorial_angle": "기획 각도 (어떤 관점으로 썼는지)", "cover_mode": "표지 문장 방식 (질문 · 놀라운 사실 · 예고 등)",
        "ending_mode": "마무리 문장 방식 (팬 공감 · 무덤덤 등)", "term_glossary": "용어 풀이 목록",
        "sources": "원문 목록 (아래 '원문')", "facts": "사실 목록 (아래 '사실')", "images": "이미지 목록 (아래 '이미지')",
        "cards": "카드 목록 (아래 '카드')", "prior_image_fingerprints": "예전 글에 쓴 이미지 지문 (같은 이미지 반복 방지)",
        "status": "패키지 상태"}),
    ("sources", "원문 (sources)", "글의 근거가 된 원문 페이지", {
        "id": "원문 번호", "title": "원문 제목", "url": "원문 주소", "publisher": "발행처", "is_official": "공식 출처 여부",
        "published_at": "원문 발표일", "checked_at": "에이전트가 확인한 시각", "role": "출처 역할 (primary 글 만드는 출처 등)",
        "region": "원문 사이트 지역", "allowed_asset_hosts": "이미지를 받아도 되는 도메인"}),
    ("facts", "사실 (facts)", "원문에서 확인한 사실. 카드 문장은 이 사실만 씀", {
        "id": "사실 번호", "statement": "사실 문장 (한국어)", "source_ids": "근거 원문 번호", "verified": "원문으로 확인됨",
        "time_sensitive": "시간이 지나면 바뀌는 정보 (날짜 · 가격 등)", "fact_type": "사실 종류 (일정 · 장소 · 성우 · 가격 등)",
        "details": "세부 값 (아래 '사실 세부')"}),
    ("details", "사실 세부 (facts.details)",
     "사실마다 붙는 세부 값. 정해진 칸 목록 없이 글마다 다르게 붙음 (일본어 칸 이름 · 상품 이름이 칸 이름인 경우도 있음 → 자동으로 꺼내 쓰기 어려움)", {
        "source_quote": "원문 인용 (일본어 원문 그대로)", "start_date": "시작 날짜", "end_date": "끝 날짜", "date": "날짜",
        "venue": "장소", "locations": "장소 목록", "price_yen": "가격 (엔)", "price": "가격", "currency": "통화",
        "tax_included": "세금 포함 여부", "release_date": "발매일", "release_year": "발매 · 방영 연도", "year": "연도",
        "announcement_date": "발표일", "hours": "운영 시간", "start_datetime": "시작 일시", "online_datetime": "온라인 일시",
        "platform": "플랫폼", "platforms": "플랫폼 목록", "mobile_steam_release_date": "모바일 · Steam 출시일",
        "channel": "채널", "hashtag": "해시태그", "arcs": "다루는 에피소드 범위", "event": "행사 이름", "theme": "주제",
        "format": "형식", "formats": "형식 목록", "sizes": "크기 목록", "variants": "종류 목록", "item_count": "상품 수",
        "menu_count": "메뉴 수", "minimum_age": "대상 연령", "manufacturer": "제조사", "publisher": "발행 · 판매사",
        "developer": "개발사", "system": "방식 · 시스템", "series_first": "시리즈 첫 작품 여부", "phone": "전화번호",
        "harajuku_date": "하라주쿠 매장 날짜 (매장별 날짜를 칸 이름에 넣은 예)"}),
    ("cards", "카드 (cards)", "카드 한 장의 텍스트와 화면 정보 (content_cards 표와 같은 내용)", {
        "page": "몇 번째 장", "role": "역할 (표지 · 본문 · 마무리)", "eyebrow": "머리말", "title_lines": "제목 줄",
        "body_sentences": "본문 문장", "protected_phrases": "바꾸면 안 되는 표현 (고유명사 · 공식 표기)",
        "fact_ids": "쓴 사실 번호", "image_id": "쓴 이미지 번호", "visual_mode": "화면 방식 (원본 이미지 · 자체 그래픽)",
        "image_explanation": "이미지에 대한 한국어 설명", "image_reuse_mode": "이미지 재사용 방식 (unique = 한 번만)",
        "layout": "화면 배치 (아래 '카드 배치')", "ending_joke": "마무리 농담", "rendered_image_key": "완성 카드 이미지 열쇠",
        "rendered_width": "완성 이미지 가로 (px)", "rendered_height": "완성 이미지 세로 (px)"}),
    ("layout", "카드 배치 (cards.layout)", "카드 화면을 그릴 때 쓰는 배치 값", {
        "template": "배치 틀 이름", "fit_mode": "이미지 맞춤 방식 (contain = 잘리지 않게 전체 표시)",
        "image_box": "이미지 상자 위치 · 크기", "copy_box": "글 상자 위치 · 크기", "image_scale": "이미지 배율",
        "image_offset_y": "이미지 세로 이동", "background_style": "배경 방식", "blur_background": "흐린 배경 사용",
        "contrast_mode": "글자 대비 방식"}),
    ("images", "이미지 (images)", "카드에 쓸 수 있는 원본 이미지와 권리 · 내용 정보", {
        "id": "이미지 번호", "url": "원본 이미지 주소", "source_url": "이미지가 있던 페이지", "local_path": "받은 파일 경로",
        "storage_key": "이미지 파일 표 열쇠 (sha256)", "media_type": "형식", "file_size_bytes": "파일 크기", "width": "가로 (px)",
        "height": "세로 (px)", "has_alpha": "투명 배경 있음", "alpha_coverage": "투명 부분 비율", "fingerprint": "이미지 지문",
        "download_status": "받기 결과 (downloaded · failed)", "download_error": "받기 실패 이유", "official": "공식 이미지 여부",
        "rights_status": "권리 상태", "rights_basis": "권리 근거", "rights_scope": "사용 범위", "rights_checked_at": "권리 확인 시각",
        "rights_evidence_url": "권리 근거 주소", "attribution_text": "출처 표기 문구", "source_description": "원래 설명 (alt 등)",
        "visible_text": "이미지 속 글자", "detected_languages": "이미지 속 언어", "text_confidence": "글자 인식 신뢰도",
        "korean_explanation": "한국어 설명", "subject_hint": "주요 대상", "subject_box": "주요 대상 위치 (잘림 방지)",
        "visual_match": "카드 내용과 맞는 정도", "related_fact_ids": "관련 사실 번호", "used_in_previous_content": "예전 글에 쓴 적 있음"}),
    ("quality", "품질 검수 (content_drafts.quality_report_json)", "검수 에이전트가 매긴 점수와 문제 목록", {
        "total": "총점", "maximum": "만점", "decision": "판정", "dimensions": "항목별 점수 (아래)", "card_scores": "카드별 점수",
        "lowest_card_score": "가장 낮은 카드 점수", "thresholds": "통과 기준 (pass 총점 · card_minimum 카드별 · safety 안전)",
        "issues": "문제 목록 (code · message · severity · owner)", "hard_blockers": "점수로 상쇄 못 하는 치명 문제",
        "duplicate_hold": "중복이라 보류 (duplicate_of · reason)", "manual_review_blocker": "사람 확인이 필요한 문제",
        "manual_visual_review": "사람이 화면을 봐야 하는지", "accepted_image_risk_count": "위험 감수 이미지 수",
        "accepted_image_risk_note": "위험 감수 메모", "visual_mode_counts": "화면 방식별 장 수", "note": "메모"}),
]
DIMENSIONS = {"evidence_and_accuracy": "근거 · 정확성 (25)", "image_suitability_and_rights": "이미지 적합성 · 권리 (15)",
              "image_full_visibility": "이미지 전체 노출 (10)", "readability_and_layout": "가독성 · 배치 (15)",
              "korean_naturalness_and_translation": "한국어 · 번역 (20)", "structure_and_style_rules": "구성 · 문체 (5)",
              "safety_and_respect": "안전 · 존중 (10)"}
VALUES = {
    "status": {"ready": "내보낼 수 있음", "held_for_quality_review": "검수 대기", "ready_for_feed": "피드로 보냄",
               "needs_crawler_update": "수집 보완 필요", "review_returned": "고쳐서 다시 검수", "rejected_archived": "3번 탈락 보관",
               "generated_pending_review": "검수 전", "draft": "초안", "collected": "모음", "rejected": "버림"},
    "role": {"cover": "표지", "detail": "본문", "ending": "마무리", "primary": "글 만드는 출처", "discovery": "찾기용", "trend": "관심 주제"},
    "visual_mode": {"source_image": "원본 이미지 카드", "editorial_graphic": "자체 그래픽 카드"},
    "cover_mode": {"surprising_fact": "놀라운 사실", "question": "질문", "teaser": "예고", "in_world_scene": "작품 속 장면",
                   "contrast_irony": "반전", "declarative": "선언", "wordplay": "말장난", "deadpan": "무덤덤"},
    "ending_mode": {"fan_empathy": "팬 공감", "deadpan": "무덤덤", "in_world_scene": "작품 속 장면", "wordplay": "말장난",
                    "surprising_fact": "놀라운 사실"},
    "event_region": {"domestic": "국내", "overseas": "해외", "unknown": "모름"},
    "rights_status": {"official_public_risk_accepted": "공식 공개 이미지 (위험 감수)", "needs_review": "확인 필요",
                      "allowed": "사용 허가", "rejected": "사용 안 함"},
    "download_status": {"downloaded": "받음", "failed": "실패", "pending": "대기"},
    "region_policy": {"kr_first": "한국 소식 먼저"}, "content_kind": {"information": "정보", "humor": "유머"},
    "image_reuse_mode": {"unique": "한 번만 사용", "editorial_graphic": "자체 그래픽"}, "lifecycle": {"active": "진행 중"},
}


def esc(x):
    return html.escape("" if x is None else str(x))


def show(key, v, limit=400):
    # 값 하나를 화면용으로: 코드 값이면 한국어 뜻을 붙이고, 목록 · 묶음은 JSON 한 줄로
    if v is None or v == "" or v == []:
        return '<span class="muted">-</span>'
    if isinstance(v, (list, dict)):
        s = json.dumps(v, ensure_ascii=False)
    else:
        s = str(v)
    s = s if len(s) <= limit else s[:limit] + " …"
    ko = VALUES.get(key, {}).get(str(v)) if not isinstance(v, (list, dict)) else None
    return esc(s) + (f' <span class="muted">({esc(ko)})</span>' if ko else "")


def table(headers, rows):
    head = "".join(f"<th>{h}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f'<div class="scroll"><table class="kv"><tr>{head}</tr>{body}</table></div>'


def shrunk(data):
    # 보내기용: 가로 SHARE_WIDTH 로 줄인 JPEG 을 HTML 에 바로 넣을 수 있는 주소(data URI)로
    im = Image.open(io.BytesIO(data))
    if im.mode != "RGB":
        bg = Image.new("RGB", im.size, "white")
        bg.paste(im.convert("RGBA"), mask=im.convert("RGBA").getchannel("A"))
        im = bg
    if im.width > SHARE_WIDTH:
        im = im.resize((SHARE_WIDTH, round(im.height * SHARE_WIDTH / im.width)), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=82)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def open_db(path):
    return sqlite3.connect(f"file:{pathlib.Path(path).as_posix()}?mode=ro&immutable=1", uri=True)


def dictionary(db, packages, reports):
    # 데이터 사전: DB 표마다 칸 · 뜻 · 예시 값, 글 패키지 · 품질 검수 안의 칸마다 뜻 · 예시 값. 설명이 없는 칸도 빠짐없이
    parts = ['<details open><summary>DB 표 (8개)</summary>']
    for name, ko, about, cols in TABLES:
        exists = db.execute("select 1 from sqlite_master where type='table' and name=?", (name,)).fetchone()
        if not exists:
            continue
        n = db.execute(f'select count(*) from "{name}"').fetchone()[0]
        rows = []
        for c in [r[1] for r in db.execute(f'pragma table_info("{name}")')]:
            if c in ("binary_data", "package_json", "payload_json", "quality_report_json"):
                ex = {"binary_data": "(이미지 바이트)", "payload_json": "(기록 종류마다 다름)"}.get(c, "(아래 글 패키지 · 품질 검수 참고)")
                ex = f'<span class="muted">{ex}</span>'
            else:
                v = db.execute(f'select "{c}" from "{name}" where "{c}" is not null and "{c}" != \'\' limit 1').fetchone()
                ex = show(c, v[0] if v else None, 120)
            rows.append([f'<span class="code">{esc(c)}</span>', esc(cols.get(c, "(설명 없음 · 새 칸)")), ex])
        parts.append(f"<h3>{esc(ko)} <span class='code'>{esc(name)}</span> · {n:,}줄</h3><div class='meta'>{esc(about)}</div>"
                     + table(["칸", "뜻", "예시 값"], rows))
    parts.append("</details><details open><summary>글 패키지 안의 칸 (feed_queue · content_drafts 의 package_json)</summary>")
    seen = {g: {} for g, *_ in PACKAGE}
    for p in packages:
        for k, v in p.items():
            seen["package"].setdefault(k, v)
        for g in ("sources", "facts", "images", "cards"):
            for item in p.get(g) or []:
                for k, v in item.items():
                    seen[g].setdefault(k, v)
                for k, v in ((item.get("details") or {}).items() if g == "facts" else ()):
                    seen["details"].setdefault(k, v)
                for k, v in ((item.get("layout") or {}).items() if g == "cards" else ()):
                    seen["layout"].setdefault(k, v)
    for r in reports:
        for k, v in r.items():
            seen["quality"].setdefault(k, v)
    for g, ko, about, cols in PACKAGE:
        keys = list(cols) + sorted(k for k in seen[g] if k not in cols)
        rows = [[f'<span class="code">{esc(k)}</span>', esc(cols.get(k, "(설명 없음 · 새 칸)")),
                 show(k, seen[g].get(k), 120) if k in seen[g] else '<span class="muted">(이번 글들엔 없음)</span>']
                for k in keys if k in seen[g] or g != "details"]
        parts.append(f"<h3>{esc(ko)}</h3><div class='meta'>{esc(about)}</div>" + table(["칸", "뜻", "예시 값"], rows))
    return "".join(parts) + "</details>"


def stored(p, rid, feed_row, links, names, report):
    # 글 하나에 저장된 데이터 전부를 묶음별 표로
    parts = []
    pk = PACKAGE[0][3]
    top = [k for k in p if k not in ("sources", "facts", "images", "cards")]
    rows = [[esc(pk.get(k, "(설명 없음)")), f'<span class="code">{esc(k)}</span>', show(k, p[k])] for k in top]
    rows += [["피드 상태 (대기열)", '<span class="code">feed_queue.status</span>', show("status", feed_row["status"])],
             ["대기열에 들어간 시각", '<span class="code">feed_queue.queued_at</span>', show("", feed_row["queued_at"])],
             ["노출 끝 (대기열)", '<span class="code">feed_queue.valid_until</span>', show("", feed_row["valid_until"])]]
    parts.append("<h3>글 정보</h3>" + table(["뜻", "칸", "값"], rows))
    parts.append("<h3>작품 연결 (content_work_links)</h3>" + table(
        ["작품", "작품 번호", "순서 (1 = 대표)", "대표 여부"],
        [[esc(names.get(w, w)), esc(w), esc(so), esc(prim)] for w, so, prim in links]) if links
        else "<h3>작품 연결 (content_work_links)</h3><div class='warn'>연결된 작품 없음</div>")
    sc = ["publisher", "title", "url", "published_at", "is_official", "role", "region", "checked_at", "id", "allowed_asset_hosts"]
    parts.append(f"<h3>원문 (sources) · {len(p.get('sources') or [])}개</h3>" + table(
        [PACKAGE[1][3][c] for c in sc], [[show(c, s.get(c), 200) for c in sc] for s in p.get("sources") or []]))
    fr = []
    for f in p.get("facts") or []:
        d = dict(f.get("details") or {})
        quote = d.pop("source_quote", None)
        fr.append([esc(f.get("id")), esc(f.get("fact_type")), esc(f.get("statement")), show("", quote, 300),
                   show("", d or None, 300), esc(f.get("verified")), esc(f.get("time_sensitive")), show("", f.get("source_ids"))])
    parts.append(f"<h3>사실 (facts) · {len(fr)}개</h3>" + table(
        ["번호", "종류", "사실 (한국어)", "원문 인용", "세부 값", "확인됨", "시간 따라 바뀜", "근거 원문"], fr))
    cr = []
    for c in p.get("cards") or []:
        lay = c.get("layout") or {}
        cr.append([esc(c.get("page")), show("role", c.get("role")), esc(c.get("eyebrow")), esc(" / ".join(c.get("title_lines") or [])),
                   esc(" ".join(c.get("body_sentences") or [])), show("", c.get("protected_phrases")), show("", c.get("fact_ids")),
                   esc(c.get("image_id")), show("visual_mode", c.get("visual_mode")), esc(c.get("image_explanation")),
                   esc(c.get("ending_joke")), esc(f"{lay.get('template', '')} · {lay.get('fit_mode', '')}")])
    parts.append(f"<h3>카드 텍스트 (cards) · {len(cr)}장</h3>" + table(
        ["장", "역할", "머리말", "제목 줄", "본문 문장", "바꾸면 안 되는 표현", "쓴 사실", "이미지", "화면 방식", "이미지 설명",
         "마무리 농담", "배치 (틀 · 맞춤)"], cr))
    ir = []
    for im in p.get("images") or []:
        ir.append([esc(im.get("id")), esc(im.get("official")), show("rights_status", im.get("rights_status")),
                   show("download_status", im.get("download_status")),
                   esc(f"{im.get('media_type') or ''} · {im.get('width') or '?'}×{im.get('height') or '?'} · {(im.get('file_size_bytes') or 0) // 1024}KB"),
                   esc(im.get("has_alpha")), show("", im.get("visible_text"), 150), show("", im.get("detected_languages")),
                   esc(im.get("korean_explanation")), esc(im.get("subject_hint")), show("", im.get("related_fact_ids")),
                   esc(im.get("used_in_previous_content")), show("", im.get("url"), 150), show("", im.get("source_url"), 150)])
    parts.append(f"<h3>이미지 (images) · {len(ir)}개</h3>" + table(
        ["번호", "공식", "권리 상태", "받기", "형식 · 크기", "투명 배경", "이미지 속 글자", "언어", "한국어 설명", "주요 대상",
         "관련 사실", "예전 글에 씀", "원본 주소", "있던 페이지"], ir))
    if report:
        dim = report.get("dimensions") or {}
        rows = [[esc(DIMENSIONS.get(k, k)), esc(v)] for k, v in dim.items()]
        issues = [[esc(i.get("code")), esc(i.get("message")), esc(i.get("severity")), esc(i.get("owner"))] for i in report.get("issues") or []]
        rest = {k: v for k, v in report.items() if k not in ("dimensions", "issues", "total", "maximum", "decision")}
        parts.append(f"<h3>품질 검수 · 총점 {esc(report.get('total'))} / {esc(report.get('maximum'))} · 판정 {esc(report.get('decision'))}</h3>"
                     + table(["항목 (만점)", "점수"], rows)
                     + (table(["문제 코드", "내용", "심각도", "고칠 곳"], issues) if issues else "<div class='muted'>문제 없음</div>")
                     + table(["뜻", "칸", "값"], [[esc(PACKAGE[-1][3].get(k, "(설명 없음)")), f'<span class="code">{esc(k)}</span>',
                                                show("", v)] for k, v in rest.items()]))
    return "<details><summary>저장된 데이터 보기 (칸 전부)</summary>" + "".join(parts) + "</details>"


def main(db_path, share=None, old_db=None):
    db = open_db(db_path)
    db.row_factory = sqlite3.Row
    old = open_db(old_db) if old_db else None
    skip = {r for (r,) in old.execute("select request_id from feed_queue")} if old else set()
    labels = {r[0]: r[1] for r in db.execute("select type_code, label_ko from content_types")}
    names = {}
    works_csv = DATA / "menu_works.csv"
    if works_csv.exists():
        with open(works_csv, encoding="utf-8-sig") as f:
            names = {int(r["menu_work_id"]): r["title_ko"] or r["title_romaji"] for r in csv.DictReader(f)}
    has = lambda t: db.execute("select 1 from sqlite_master where type='table' and name=?", (t,)).fetchone()
    links = {}
    if has("content_work_links"):
        for rid, wid, so, prim in db.execute("select request_id, menu_work_id, sort_order, is_primary from content_work_links order by sort_order"):
            links.setdefault(rid, []).append((wid, so, prim))
    drafts = {r["request_id"]: r for r in db.execute("select request_id, quality_score, quality_report_json from content_drafts")}
    reports = {rid: json.loads(r["quality_report_json"]) for rid, r in drafts.items() if r["quality_report_json"]}

    (OUT / "img").mkdir(parents=True, exist_ok=True)
    seen_src, posts, packages = {}, [], []
    rows = db.execute("select * from feed_queue order by queued_at").fetchall()
    for row in rows:
        rid, topic, ctype, until = row["request_id"], row["topic"], row["content_type"], row["valid_until"]
        p = json.loads(row["package_json"])
        src = p.get("sources") or []
        warns = []
        if not any(s.get("published_at") for s in src):
            warns.append("원문 발표일 없음")
        w = links.get(rid, [])
        if not w:
            warns.append("대표 작품 없음 (작품 연결 없음)")
        for wid, _, _ in w:
            if len(str(names.get(wid, "")).replace(" ", "")) <= 2:
                warns.append(f"짧은 제목 연결 의심: {names.get(wid, wid)}")
        for s in src:                                  # 같은 원문 (주소의 ? 뒤는 빼고 비교)
            u = urlsplit(s.get("url") or "")
            key = f"{u.netloc}{u.path}"
            if key in seen_src:
                warns.append(f"같은 원문이 앞 글에도 있음: {seen_src[key]}")
            seen_src.setdefault(key, topic)
        last_day = p.get("event_end") or p.get("event_start")   # 행사·발매가 끝나기(또는 시작하기) 전에 노출이 끝나는지
        if last_day and until and until[:10] < last_day[:10]:
            warns.append(f"노출 끝({until[:10]})이 행사·발매 날짜({last_day[:10]})보다 빠름")
        if row["status"] != "ready":
            warns.append(f"피드 상태: {row['status']} ({VALUES['status'].get(row['status'], '')})")
        if rid in skip:                                # --new: 예전 DB 에 있던 글은 중복 확인에만 쓰고 안 보여줌
            continue
        packages.append(p)

        cards = []
        for page, role, eyebrow, title, body, key in db.execute(
                "select page, role, eyebrow, title_lines_json, body_json, rendered_image_key from content_cards "
                "where request_id=? order by page", (rid,)):
            blob = db.execute("select media_type, binary_data from image_blobs where sha256=?", (key,)).fetchone() if key else None
            if blob and blob[0] in EXT:
                if share:
                    img = shrunk(blob[1])
                else:
                    img = f"img/{key}.{EXT[blob[0]]}"
                    if not (OUT / img).exists():
                        (OUT / img).write_bytes(blob[1])
                inner = f'<img src="{esc(img)}" loading="lazy" alt="{esc(page)}장">'
            else:                                      # 완성 이미지가 없으면 글자만
                lines = " / ".join(json.loads(title or "[]"))
                inner = (f'<div class="text"><b>{esc(eyebrow)}</b><br>{esc(lines)}<br><br>'
                         f'{esc(" ".join(json.loads(body or "[]")))}</div>')
            cards.append(f'<div class="card"><div class="page">{esc(page)}장 · {esc(role)}</div>{inner}</div>')

        work_html = " ".join(f'<span class="chip">{"대표 " if so == 1 else ""}{esc(names.get(wid, wid))} ({esc(wid)})</span>'
                             for wid, so, _ in w) or "없음"
        region = (f'지역: 행사 {esc(p.get("event_country") or "-")} · {esc(p.get("event_region") or "-")} '
                  f'({esc(p.get("event_location") or "장소 없음")}) / 원문 사이트 '
                  f'{esc(", ".join(sorted({s.get("region") or "-" for s in src})))} / 독자 {esc(p.get("audience_region") or "-")}')
        src_html = " · ".join(f'<a href="{esc(s.get("url"))}" target="_blank" rel="noopener">{esc(s.get("publisher") or "원문")}</a>'
                              f' (발표일 {esc(s.get("published_at") or "없음")})' for s in src)
        q = drafts.get(rid)
        posts.append(
            f'<div class="post"><h2>{esc(topic)}</h2><div class="meta">'
            f'<span class="chip">{esc(labels.get(ctype, ctype))}</span> 상태 {show("status", row["status"])} · '
            f'품질 {esc(q["quality_score"] if q else "-")} · 만든 날 {esc((row["queued_at"] or "")[:10])} · 노출 끝 {esc((until or "")[:10])} · '
            f'행사 {esc(p.get("event_start") or "-")} ~ {esc(p.get("event_end") or "-")}<br>'
            f'작품: {work_html}<br>{region}<br>원문: {src_html or "없음"}</div>'
            + "".join(f'<div class="warn">⚠ {esc(x)}</div>' for x in warns)
            + f'<div class="cards">{"".join(cards)}</div>{stored(p, rid, row, w, names, reports.get(rid))}</div>')

    status = Counter(r["status"] for r in rows)
    shown_ids = {r["request_id"] for r in rows if r["request_id"] not in skip}
    rejected = [r for r in db.execute("select topic, content_type, review_failures, reasons_json, request_id from rejected_archive")
                if not old or not old.execute("select 1 from rejected_archive where request_id=?", (r[4],)).fetchone()]
    rej_codes = Counter(c if isinstance(c, str) else c.get("code", str(c)) for r in rejected for c in json.loads(r[3] or "[]"))
    summary = (f'<div class="box"><div class="meta">피드 대기열 {len(rows)}개: '
               + " · ".join(f"{show('status', k)} {v}개" for k, v in status.most_common())
               + f' · 이 페이지에 보이는 글 {len(shown_ids)}개 · {"새로 " if old else ""}탈락 보관 {len(rejected)}개'
               + (" (이유: " + ", ".join(f"{esc(k)} {v}" for k, v in rej_codes.most_common(6)) + ")" if rej_codes else "")
               + '</div><details><summary>데이터 사전: 에이전트가 저장하는 표 · 칸 전부</summary>'
               + dictionary(db, packages, [reports[r] for r in shown_ids if r in reports]) + "</details></div>")
    page = (f'<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
            f'<title>에이전트 글 보기</title><style>{CSS}</style></head><body>'
            f'<h1>에이전트 {"새 " if old_db else ""}글 {len(posts)}개 · {esc(pathlib.Path(db_path).name)}</h1>'
            f'{summary}{"".join(posts)}</body></html>')
    target = pathlib.Path(share) if share else OUT / "index.html"
    target.write_text(page, encoding="utf-8")
    print(f"글 {len(posts)}개 → {target} ({target.stat().st_size / 1e6:.1f}MB)")
    webbrowser.open(target.as_uri())


if __name__ == "__main__":
    arg = lambda name: sys.argv[sys.argv.index(name) + 1] if name in sys.argv else None
    main(sys.argv[1], arg("--share"), arg("--new"))
