FROM python:3.13-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
ARG APP_UID=10001
RUN useradd --uid ${APP_UID} --create-home --shell /usr/sbin/nologin switchboard
WORKDIR /app
COPY pyproject.toml README.md LICENSE NOTICE ./
COPY src ./src
RUN pip install --no-cache-dir .

FROM base AS test
COPY tests ./tests
COPY config/switchboard.example.yaml ./config/switchboard.example.yaml
RUN pip install --no-cache-dir ".[test]" && python -m pytest -q

FROM base AS runtime
LABEL org.opencontainers.image.title="SwB" \
      org.opencontainers.image.description="Self-hosted MCP server for routing work and sharing scoped memory" \
      org.opencontainers.image.url="https://swb.one" \
      org.opencontainers.image.licenses="Apache-2.0"
RUN install -d -m 0700 -o switchboard -g switchboard /data
WORKDIR /
USER switchboard
ENV SWITCHBOARD_DATA=/data
VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.getenv('PORT', '8000') + '/healthz', timeout=4)"
CMD ["swb", "serve"]

FROM runtime AS paas
USER root
ENTRYPOINT ["sh", "-ec", "chown -h switchboard:switchboard /data && exec setpriv --reuid=switchboard --regid=switchboard --init-groups --no-new-privs -- \"$@\"", "--"]
CMD ["swb", "serve"]
