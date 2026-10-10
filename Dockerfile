# Signal TV Guide: static web app + stream proxy + periodic guide rebuild,
# all from scripts/serve.py. Standard library only, so no pip layer.
#
# Base pinned by tag and digest; bump both together.
FROM python:3.12.15-slim-trixie@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY scripts/ scripts/
COPY web/index.html web/app.css web/app.js web/

# Runs as an unprivileged UID; the root filesystem can be read-only because
# the only thing written is the guide data, which goes to /data (mount an
# emptyDir there).
USER 1000:1000
EXPOSE 8080
VOLUME ["/data"]

CMD ["python3", "scripts/serve.py", "--host", "0.0.0.0", "--port", "8080", "--data", "/data", "--refresh", "4"]
