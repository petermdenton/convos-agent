FROM ghcr.io/borski/patchright-docker:latest

COPY scripts/search_sutochno.py /app/search_sutochno.py
COPY scripts/entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

ENV SUTOCHNO_IN_DOCKER=1

ENTRYPOINT ["/app/entrypoint.sh"]
