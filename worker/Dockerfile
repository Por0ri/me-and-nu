FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY requirements.txt ./requirements.txt
RUN python -m pip install -r requirements.txt \
    && groupadd --gid 10001 runtime \
    && useradd --uid 10001 --gid runtime --no-create-home --shell /usr/sbin/nologin runtime \
    && mkdir -p /var/lib/menu-beat /var/lib/menu-flower \
    && chown runtime:runtime /var/lib/menu-beat /var/lib/menu-flower

COPY menu_worker/ ./menu_worker/
COPY flowerconfig.py ./flowerconfig.py
COPY tests/ ./tests/
USER 10001:10001
CMD ["celery", "-A", "menu_worker.app:app", "worker", "--loglevel=INFO", "--concurrency=1", "--queues=runtime", "--hostname=runtime@%h", "--events"]
