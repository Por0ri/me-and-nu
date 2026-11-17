# 추천 계산 (scripts/daily_recommend.py) 전용 이미지 — 서버에서 API 와 따로 돈다 (deploy/checking/recommend.sh)
# 원본 데이터 없이 통계 문맥 파일 (scripts/save_context.py)로 계산. 문맥 파일은 이미지에 넣지 않고 실행할 때 읽기 전용으로 붙인다
# Python · numpy 는 문맥 파일을 만든 환경과 같은 버전 (pickle 로 저장한 배열을 그대로 읽어야 함)
#
#   docker build -f model/Dockerfile -t me-nu-recommend-checking model      # 저장소 루트에서. 문맥은 model/ (.dockerignore 참고)
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONIOENCODING=utf-8
RUN pip install --no-cache-dir "numpy==2.5.4" "psycopg[binary]==3.3.6" \
    && useradd --create-home --uid 10001 recommend

WORKDIR /app
COPY . /app/model/
USER recommend
ENTRYPOINT ["python", "model/scripts/daily_recommend.py"]
