# Hugging Face Space (Docker SDK) image for the web app (ADR 0054). Built from the bundle made by scripts/make_space_bundle.py.
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git openssh-client && rm -rf /var/lib/apt/lists/*
RUN useradd -m -u 1000 user
WORKDIR /app
COPY requirements-api.lock .
RUN pip install --no-cache-dir --require-hashes --no-deps -r requirements-api.lock
COPY src ./src
COPY configs ./configs
COPY space_start.sh .
RUN chmod +x space_start.sh && mkdir -p artifacts && chown -R user /app
USER user
ENV PORT=7860
EXPOSE 7860
CMD ["./space_start.sh"]
