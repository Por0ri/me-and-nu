# ani_rag work reference snapshot

아래 표는 2026-11-07에 복사한 자료의 기록이다. 현재 정식 작품·시즌 목록은 두 에이전트 모두 저장소 루트의 `catalog/`에서 읽는다. 이 폴더의 예전 작품·시즌 CSV는 과거 자료의 번호를 해석할 때만 사용한다. 현재 외부 ID 연결표와 임시 작품의 위치는 [작품 ID 연결 규칙](../../docs/WORK_ID_MATCHING.md)을 본다.

`work_theme_songs.csv` is a reference-only lookup dataset. Its source documentation says not to use it as training data; do not treat it as evidence for content claims or include it in RAG chunks/embeddings. The character-name lookup is also a matching aid, not a substitute for source evidence.

| File | Bytes | SHA-256 |
|---|---:|---|
| `menu_works.csv` | 331073 | `c0cabf642a4fb87a06328bbd978bcea8e95ca8103ff8a1ff6c52b89cce64ac75` |
| `menu_work_entries.csv` | 520366 | `d1577f4a046feb8280de0a9f9798e1b4d7b2cce176b6844f4bef06d9669cbaf0` |
| `snapshot_20261107/menu_work_external_entries.csv` | 9003 | `5804dd838038123b237eac799ab9311e4b7d349dc2ada3b5d8280fc47954d10f` |
| `menu_work_aliases.csv` | 639 | `a6570ee84113b54a42d9ff99c67580b9c9a03c98c3c0ab637aaa6039c67831e7` |
| `menu_work_franchises.csv` | 223 | `ec91486bba18860e8a91f79c1126c9196f549b847955887acbea97c71c6b6c00` |
| `menu_work_parent_overrides.csv` | 81 | `8ab687d56fe790758bfdc3acc9a7efd3e3b310d71f379bb106aebf7dc4d08d6a` |
| `work_characters.csv` | 2422422 | `1fbb8f30df296ac00eee72d1d115bbcb97469b27e9584d173cd890ab7e54346c` |
| `work_theme_songs.csv` | 619828 | `4edea524ff2fbbcd74a70bfd6b2ed83d99f48e78af6d6e55cab7db5313ba2d94` |
