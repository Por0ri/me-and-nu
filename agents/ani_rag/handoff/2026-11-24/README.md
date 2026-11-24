# RAG 데이터 인계 · 2026-11-24

기간형·RAG 에이전트 저장소의 RAG 운영 DB, 벡터 색인, 카드 이미지 스냅샷입니다.

| 복원 경로 (저장소 루트 기준) | 내용 |
| --- | --- |
| `agents/ani_rag/data/ani_rag_live_20261111.sqlite3` | 원문, 근거, 작품 연결, 초안과 검수 기록 |
| `agents/ani_rag/output/ani_rag_evidence_vectors_20261119.sqlite3` | 근거 텍스트 조각과 기존 임베딩 |
| `agents/ani_rag/data/image_assets/` | DB에서 참조하는 이미지와 폴더 내 파일 2,798개 |

## 내려받고 복원하기

Git LFS와 Python 3가 설치된 환경에서 이 브랜치를 받은 뒤, 저장소 루트에서 실행합니다.

```sh
git lfs pull --include="agents/ani_rag/handoff/2026-11-24/me-nu-rag.tar.gz.*" --exclude=""
python agents/ani_rag/handoff/2026-11-24/restore.py ../me-nu-rag-snapshot
```

복원할 폴더 이름은 새로 지정합니다. 분할 파일을 번호순으로 읽어 압축을 풀고, 파일 2,800개 각각의 SHA-256을 인계 원본과 대조합니다. 완료되면 지정한 폴더 아래에 표와 같은 경로가 생깁니다. DB·이미지의 원래 크기를 합해 약 7.25 GiB의 여유 공간이 필요합니다.
