# agent/contents — 에이전트가 올린 글

에이전트 결과물(`agent/out/`)은 판정 기록 · 탈락 글 · 캐시가 섞여 있어 git에 올리지 않는다.
이 폴더는 그중 **올림** 글만 골라 옮긴 것이다. 화면 · API 테스트용 콘텐츠로 쓴다.

- `contents.json` — 글 전부. 한 건의 모양은 프론트 mock(`frontend/types/content.ts`의 `ContentDetail`)에 맞췄다.
  - `id` · `title` · `summary`(첫 문장) · `body` · `sourceName` · `sources[{id, name, url}]` · `saved`(false) · `topicId`
  - 덧붙인 칸: `topicName` · `subtopic`(영화 정보 · 영화 리뷰 · 음악 리뷰 · 애니 유형) · `work`(작품 이름) · `agent` · `agentVersion` · `createdAt` · `chars`(글자 수) · `origin`(원본 파일)
  - `topicId`는 프론트 mock과 같은 값이다(`topic-movie` · `topic-music` · `topic-anime`). 백엔드 Topic ID가 아니다.
- `movie/` · `music/` · `anime/` — 같은 글을 읽기 쉬운 .md로 한 편씩.

다시 뽑기:

```bash
python agent/export_contents.py
```

같은 원본 파일은 늘 같은 `id`가 나온다.

팀원마다 `agent/out/`이 다르므로 **합친다**. 이미 `contents.json`에 있는 글(남이 올린 글)은 그대로 두고,
내 `out/`의 올림 글만 `id`로 더하거나 덮어쓴다. 내 `out/`에 원본이 있는데 올림에서 빠진 글만 뺀다.
PR끼리 `contents.json`이 충돌하면 양쪽 글을 다 살려서 풀고, export를 한 번 더 돌리면 순서가 정리된다.

빼는 것:
- 애니 감상순서 · 기념일(9/29 유형에서 뺐다), 9/21 애니 시험판(제목이 '제목'인 글)
- PM이 짚은 글 두 편(`export_contents.py`의 `빼는글`)
- 원본 파일에 판 표기가 없는 글은 `agentVersion`이 빈 문자열이다(음악 전부, 애니 일부).
