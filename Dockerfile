FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY VERSION .
COPY technitium_sync/ technitium_sync/

# Build metadata. The version comes from the VERSION file (copied above);
# there is intentionally no version build arg.
ARG GIT_COMMIT=unknown
# release | main | dev | pr | local -- set by publish.yml from the trigger.
ARG BUILD_CHANNEL=local
RUN echo "version=$(tr -d '[:space:]' < /app/VERSION)" > /app/version.txt && \
    echo "commit=${GIT_COMMIT}" >> /app/version.txt && \
    echo "channel=${BUILD_CHANNEL}" >> /app/version.txt && \
    echo "build_time=$(date -u +"%Y-%m-%dT%H:%M:%SZ")" >> /app/version.txt

LABEL org.opencontainers.image.source="https://github.com/crzykidd/technitium-sync" \
      org.opencontainers.image.description="Technitium DHCP reservations to STD inventory stream" \
      org.opencontainers.image.licenses="MIT"

RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin tsync
USER tsync

# /healthz (HEALTH_PORT, default 8080): healthy while the last push cycle succeeded.
# One failed cycle flips it, so 3 retries (about 3 minutes) keep a blip from showing red.
EXPOSE 8080
HEALTHCHECK --interval=60s --timeout=10s --start-period=120s --retries=3 \
    CMD ["python", "-m", "technitium_sync", "healthcheck"]

CMD ["python", "-m", "technitium_sync"]
