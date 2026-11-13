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
    && mkdir -p /var/lib/menu-beat /var/lib/menu-flower /var/lib/menu-jobs \
    && chown runtime:runtime /var/lib/menu-beat /var/lib/menu-flower /var/lib/menu-jobs

COPY menu_worker/ ./menu_worker/
COPY flowerconfig.py ./flowerconfig.py
COPY setup_local.py ./setup_local.py
COPY tests/ ./tests/
COPY --from=anime-registry --chmod=0444 /sources.json /source_type_map.json /app/anime-catalog/
COPY --from=anime-works --chmod=0444 /menu_works.csv /app/anime-catalog/
RUN chmod 0555 /app/anime-catalog
USER 10001:10001
CMD ["celery", "-A", "menu_worker.app:app", "worker", "--loglevel=INFO", "--concurrency=1", "--queues=runtime", "--hostname=runtime@%h", "--events"]
