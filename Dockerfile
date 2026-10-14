FROM python:3.11-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir '.[semantic]'
ENV FC_HOME=/app/data
EXPOSE 8123
CMD ["filingcopilot", "serve", "--host", "0.0.0.0", "--port", "8123"]
