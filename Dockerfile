FROM --platform=$BUILDPLATFORM node:24-slim AS web
WORKDIR /app/web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web ./
COPY src/switchboard/brand.json /app/src/switchboard/brand.json
COPY src/switchboard/static/theme.js src/switchboard/static/console-tokens.css /app/src/switchboard/static/
COPY src/switchboard/slack/avatars/ /app/src/switchboard/slack/avatars/
COPY src/switchboard/prompts/setup/ /app/src/switchboard/prompts/setup/
COPY site/lib/setup-picker.ts site/lib/urls.ts /app/site/lib/
COPY site/public/marks/captain.webp /app/site/public/marks/captain.webp
RUN npm run build

FROM python:3.13-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
ARG APP_UID=10001
RUN apt-get update && apt-get install -y --no-install-recommends git openssh-client && rm -rf /var/lib/apt/lists/*
RUN useradd --uid ${APP_UID} --create-home --shell /usr/sbin/nologin switchboard
WORKDIR /app
COPY pyproject.toml setup.py README.md LICENSE NOTICE ./
COPY web/licenses/ ./web/licenses/
COPY src ./src
COPY --from=web /app/src/switchboard/static/app ./src/switchboard/static/app
RUN pip install --no-cache-dir .

FROM base AS test
COPY tests ./tests
COPY config/switchboard.example.yaml ./config/switchboard.example.yaml
COPY manifests ./manifests
RUN pip install --no-cache-dir ".[test]" && python -m pytest -q

FROM base AS runtime
ARG GIT_COMMIT
ARG BUILD_DATE
LABEL org.opencontainers.image.title="SwB" \
      org.opencontainers.image.description="Self-hosted MCP server for routing work and sharing scoped memory" \
      org.opencontainers.image.url="https://swb.one" \
      org.opencontainers.image.licenses="Apache-2.0"
RUN install -d -m 0700 -o switchboard -g switchboard /data
WORKDIR /
USER switchboard
ENV PORT=8000 SWB_DEFAULT_HOST=0.0.0.0 SWB_DEFAULT_DATA_DIR=/data GIT_COMMIT=${GIT_COMMIT} BUILD_DATE=${BUILD_DATE}
VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.getenv('PORT', '8000') + '/healthz', timeout=4)"
CMD ["swb", "serve"]

FROM runtime AS paas
USER root
ENTRYPOINT ["sh", "-ec", "chown -h switchboard:switchboard /data && exec setpriv --reuid=switchboard --regid=switchboard --init-groups --no-new-privs -- \"$@\"", "--"]
CMD ["swb", "serve"]
