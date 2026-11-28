FROM ghcr.io/astral-sh/uv:0.13.4@sha256:9f130421e930bfbdc563c00740466603f8723158e90297d07957c2294640262b AS uv
FROM mcr.microsoft.com/playwright:v1.65.1-noble@sha256:1507cbfc550a63a6b7396791ca4f00df17beb56342d2447b3f106c306f525579

WORKDIR /app
ENV CI=true TZ=UTC LANG=C.UTF-8 SWB_VISUAL_DATA=/tmp/swb-visual
COPY --from=uv /uv /usr/local/bin/uv

COPY web/package.json web/package-lock.json ./web/
RUN npm ci --prefix web

COPY pyproject.toml uv.lock README.md LICENSE NOTICE MANIFEST.in setup.py ./
RUN uv sync --locked --no-dev --no-install-project --python /usr/bin/python3

COPY src ./src
COPY web/index.html web/vite.config.ts web/tsconfig.json ./web/
COPY web/src ./web/src
COPY web/licenses ./web/licenses
COPY site/lib/setup-picker.ts site/lib/urls.ts ./site/lib/
COPY site/public/marks/captain.webp ./site/public/marks/captain.webp
RUN npm run build --prefix web && uv sync --locked --no-dev --python /usr/bin/python3

COPY web/tests/channel_modes_fixture.py web/tests/visual_fixture.py ./web/tests/
COPY web/playwright.visual.config.ts ./web/
COPY web/tests/visual/*.ts ./web/tests/visual/
COPY web/tests/visual/compact_baselines.py ./web/tests/visual/
WORKDIR /app/web
ENTRYPOINT ["./node_modules/.bin/playwright", "test", "--config", "playwright.visual.config.ts"]
