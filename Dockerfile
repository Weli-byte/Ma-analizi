# Production image for the read-only API (ADR 0036). Build: docker build -t forecast-api .
FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.lock .
RUN pip install --no-cache-dir --require-hashes --no-deps -r requirements.lock
COPY src ./src
COPY configs ./configs
RUN useradd --create-home --uid 10001 app && mkdir -p /app/artifacts && chown -R app /app
USER app
# Keys come from the environment at run time (FORECAST_API_KEYS), never from the image.
# artifacts/ is a mounted volume (read-only for the API).
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/v1/health',timeout=4)"
CMD ["python", "-m", "uvicorn", "src.api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
