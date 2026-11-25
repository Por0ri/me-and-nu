"""욕설 · 선정적인 말 거르기 (app/core/profanity, 2026-11-25 사용자 결정): 잡을 말은 잡고, 멀쩡한 말 · 작품 제목은 넘기고,
채팅용으로 걸린 부분만 같은 길이의 '*' 로 가린다."""

import csv
from pathlib import Path

import pytest

from app.core.profanity import _HERE, banned_spans, has_banned, mask_banned

CATALOG = Path(__file__).resolve().parents[2] / "catalog" / "menu_works.csv"


@pytest.mark.parametrize("text", [
    "씨발", "씨 발", "씨.발", "씨1발 진짜", "아 씨발 진짜", "개새끼야", "저 새끼 뭐야", "병신같네", "존나 웃기네",
    "tlqkf", "야동배우이타치", "섹스", "fuck you", "凸",
])
def test_banned_words_are_caught_even_spelled_apart(text):
    assert has_banned(text), text


@pytest.mark.parametrize("text", [
    # 걸리는 글자를 품은 멀쩡한 말 (allow_ko.txt) 과 띄어쓰기에 걸쳐 우연히 생긴 것
    "시발점", "시발역에서 출발", "수박씨 발라 먹기", "십팔번 노래", "졸라맨", "자위대 이야기", "새끼손가락 걸고 약속", "강아지 새끼",
    "위험한 녀석", "내 마음의 위험한 녀석",
    # 일상 말과 겹쳐서 목록에서 뺀 것 · 넣지 않은 것
    "미친 듯이 재밌다", "보지 마세요", "자지 말고 일어나", "Sexy Commando", "Betrothed to My Sister's Ex",
    # 목록 낱말과 겹치는 작품 제목 (11/25 시험에서 걸렸던 것)
    "옆자리 세키군", "야미시바이", "마도정병의 슬레이브",
    # 토론 주제 · 채팅 예
    "이타치 VS 지라이야", "히소카와 크로로가 다시 싸우면 누가 이길까?", "넨연구가", "개꿀잼",
])
def test_ordinary_words_and_titles_pass(text):
    assert not has_banned(text), (text, banned_spans(text))


def test_mask_hides_only_the_banned_part_with_the_same_length():
    assert mask_banned("아 씨발 진짜") == "아 ** 진짜"
    assert mask_banned("씨 발 뭐야") == "*** 뭐야"          # 띄워 쓴 글자 사이 공백까지 한 덩어리로 가린다
    assert mask_banned("시발점부터 씨발") == "시발점부터 **"  # 예외 낱말 안은 그대로, 밖에서 걸린 것만
    text = "멀쩡한 채팅"
    assert mask_banned(text) is text                        # 걸린 게 없으면 받은 그대로
    long = "씨발" * 150
    assert len(mask_banned(long)) == len(long) == 300       # 길이가 같아 300자 한도를 넘지 않는다


def test_no_catalog_title_is_caught():
    """레포의 작품 목록 (한국어 · 영어 · 로마자 제목) 은 하나도 걸리면 안 된다. 목록 파일이 없는 환경에서는 건너뛴다."""
    if not CATALOG.exists():
        pytest.skip("catalog/menu_works.csv 가 없는 환경")
    with CATALOG.open(encoding="utf-8-sig") as file:
        titles = [row[key] for row in csv.DictReader(file) for key in ("title_ko", "title_english", "title_romaji") if row.get(key)]
    assert len(titles) > 1000
    caught = [title for title in titles if has_banned(title)]
    assert caught == []


def test_word_list_keeps_its_mit_notice():
    """바탕 목록 badwords-ko 의 MIT 고지를 목록과 같이 둔다."""
    notice = (_HERE / "LICENSE-badwords-ko.txt").read_text(encoding="utf-8")
    assert "MIT License" in notice and "Permission is hereby granted" in notice
    assert "badwords-ko" in (_HERE / "words_ko.txt").read_text(encoding="utf-8")
