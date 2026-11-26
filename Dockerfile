FROM node:24-slim AS web
WORKDIR /app/web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web ./
RUN npm run build

FROM python:3.13-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
ARG APP_UID=10001
RUN useradd --uid ${APP_UID} --create-home --shell /usr/sbin/nologin switchboard
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
COPY --from=web /app/src/switchboard/static/app ./src/switchboard/static/app
RUN pip install --no-cache-dir .

FROM base AS test
COPY tests ./tests
COPY config ./config
COPY manifests ./manifests
RUN pip install --no-cache-dir ".[test]" && python -m pytest -q

FROM base AS runtime
USER switchboard
ENV SWITCHBOARD_CONFIG=/config/switchboard.yaml SWITCHBOARD_DATA=/data
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4)"
CMD ["switchboard"]
