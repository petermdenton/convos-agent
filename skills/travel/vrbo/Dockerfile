FROM ghcr.io/borski/patchright-docker:latest

COPY scripts/search_vrbo.py /app/search_vrbo.py
COPY scripts/entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

ENV VRBO_IN_DOCKER=1

ENTRYPOINT ["/app/entrypoint.sh"]
