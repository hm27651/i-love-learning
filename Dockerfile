FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    STUDY_DATA_DIR=/data \
    STUDY_BACKUP_DIR=/backups \
    STUDY_MAX_UPLOAD_MB=50

WORKDIR /app

RUN groupadd --gid 10001 study \
    && useradd --uid 10001 --gid 10001 --create-home --home-dir /home/study --shell /usr/sbin/nologin study \
    && install -d -o 10001 -g 10001 /data /backups

COPY requirements.txt ./
RUN python -m pip install --no-cache-dir --requirement requirements.txt

COPY --chown=10001:10001 *.py ./
COPY --chown=10001:10001 routes ./routes
COPY --chown=10001:10001 services ./services
COPY --chown=10001:10001 templates ./templates
COPY --chown=10001:10001 static ./static

USER 10001:10001

EXPOSE 23456

CMD ["sh", "-c", "umask 077 && exec waitress-serve --listen=0.0.0.0:23456 app:app"]
