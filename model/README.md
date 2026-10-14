# model — 콘텐츠 추천 모델

me;nu 피드에 콘텐츠를 어떤 순서로 보여줄지 정하는 코드. 기준 문서는 Notion의 콘텐츠 명세서 v0.2, 로그 명세서 v0.2, ERD V0.7이다.

## 폴더

| 경로 | 내용 |
|---|---|
| `menu_reco/v0.py` | v0 추천. 규칙 점수(유형·장르·작품·최신·반응) → 같은 작품 연달아 금지 → 스포일러 흐림 |
| `scripts/make_sample_data.py` | 테스트용 샘플 작품·콘텐츠·유저 생성 |
| `sample/` | 샘플 데이터 (생성 결과, 커밋함) |
| `notebooks/v0_demo.ipynb` | 결과 확인용 노트북. 유저별 추천과 점수 내역, 가중치 바꿔 보기 |
| `data/` | 공개 데이터·AniList 원본 등 큰 파일. **커밋 안 함** |

## 실행

저장소 루트에서 실행한다. 표준 라이브러리만 쓴다.

```bash
python model/scripts/make_sample_data.py   # 샘플 데이터 다시 만들기
python -m model.menu_reco.v0               # 샘플 유저 6명 추천 결과
```

## 버전

| 버전 | 내용 | 상태 |
|---|---|---|
| v0 | 규칙 점수. 학습 없음 | 완료 (샘플 데이터) |
| v0.5 | 작품 유사도(MAL), 태그 관련도, 장기·단기 취향, 채점기로 비교 | 예정 10/19~24 |
| v1 | 매일 배치 → `recommendation_round`/`recommendation_item` 저장 → 피드 연결 | 예정 10/24~30 |
