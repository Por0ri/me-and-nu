-- 챗봇 이름 찾기용 정보사전 색인 (데이터는 바꾸지 않고 색인만 더한다. 여러 번 돌려도 같은 결과)
--
--   docker exec -i menu-dict sh -c 'psql -U "$POSTGRES_USER" -d menu_dict -v ON_ERROR_STOP=1' < backend/scripts/dictionary_indexes.sql
--
-- 챗봇은 질문을 단어 묶음으로 잘라 별칭과 맞춘다 (backend/app/integrations/dictionary.py).
-- 색인이 없으면 질문마다 별칭 약 25만 개를 처음부터 끝까지 훑는다.
-- 아래 식은 dictionary.py 의 _SQL_DROP 과 글자 하나까지 같아야 색인을 탄다 (tests/test_dictionary.py 가 확인한다).
-- concurrently: 만드는 동안에도 정보사전에 쓰기를 막지 않는다. 그래서 트랜잭션 안에서 돌리면 안 된다.

-- 별칭 전체 비교: 소문자로 바꾸고 기호 · 띄어쓰기를 지운 글자 (확인된 별칭만)
create index concurrently if not exists alias_name_key
  on dict.alias ((regexp_replace(lower(alias), '[^0-9a-z가-힣ぁ-んァ-ヶー一-龥]', '', 'g')))
  where not is_candidate;

-- 이름 일부 비교: 띄어쓰기로 나눈 조각 ("카마도 탄지로" → 카마도, 탄지로). 확인된 별칭 중 띄어쓰기가 있는 것만
create index concurrently if not exists alias_name_pieces
  on dict.alias using gin ((string_to_array(lower(alias), ' ')))
  where not is_candidate and alias like '% %';
