# Solutions Hub — Flask + Gunicorn (ECS Express Mode / ECR)
#
# Build:  docker build -t solution-hub .
# Run:    docker run --rm -p 8080:8080 --env-file .env solution-hub

FROM python:3.12-slim-bookworm

ARG BUILD_ID=unknown
ENV SOLUTIONS_HUB_BUILD_ID=${BUILD_ID}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080 \
    TRUST_PROXY_HEADERS=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

COPY . .

RUN test -f /app/public/index.html && test -f /app/app.py && test -f /app/wsgi.py

RUN useradd --create-home --shell /usr/sbin/nologin appuser \
    && mkdir -p /app/data \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8080

CMD ["sh", "-c", "exec gunicorn \
  --bind 0.0.0.0:${PORT} \
  --workers ${WEB_CONCURRENCY:-1} \
  --threads 4 \
  --timeout ${GUNICORN_TIMEOUT:-180} \
  --access-logfile - \
  --error-logfile - \
  wsgi:application"]
