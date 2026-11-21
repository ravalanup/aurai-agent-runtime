# Aurai agent runtime image. One image runs either service: `serve-runtime` or `serve-approvals`. It carries the example
# config so `check` runs inside it; deployments mount their own config over /config.
FROM python:3.15-slim AS build
WORKDIR /build
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir --prefix=/install .

FROM python:3.15-slim
COPY --from=build /install /usr/local
WORKDIR /app
COPY config ./config
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin aurai
USER 10001
EXPOSE 8080
ENV PYTHONUNBUFFERED=1 AURAI_CONFIG_DIR=/app/config
ENTRYPOINT ["aurai-agents"]
CMD ["--help"]

LABEL org.opencontainers.image.title="aurai-agent-runtime" \
      org.opencontainers.image.description="Aurai agent runtime and approval service" \
      org.opencontainers.image.licenses="MIT"
