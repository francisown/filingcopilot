# Pin the base image used by the reproducible lock file; update deliberately.
FROM python:3.10-slim@sha256:6ff506466f8b1e981719b468cc0023b8ef000b983cdec0eafe119d01f379e31d

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN groupadd --system filingcopilot \
    && useradd --system --gid filingcopilot --create-home filingcopilot

WORKDIR /app

# The lock file is generated from the supported Python 3.10 environment. Keep
# the image build independent from whatever happens to be installed on the
# host running Docker.
COPY requirements-lock.txt ./
RUN python -m pip install --no-cache-dir -r requirements-lock.txt

COPY pyproject.toml README.md ./
COPY src ./src
RUN python -m pip install --no-cache-dir --no-deps . \
    && mkdir -p /app/data \
    && chown -R filingcopilot:filingcopilot /app

ENV FC_HOME=/app/data
EXPOSE 8123

USER filingcopilot

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import os,urllib.request; r=urllib.request.Request('http://127.0.0.1:8123/api/health'); t=os.getenv('FC_AUTH_TOKEN'); r.add_header('Authorization','Bearer '+t) if t else None; urllib.request.urlopen(r, timeout=3)"

CMD ["filingcopilot", "serve", "--host", "0.0.0.0", "--allow-non-loopback", "--port", "8123"]
