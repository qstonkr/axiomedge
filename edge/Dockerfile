FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential cmake && \
    rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
    llama-cpp-python>=0.3.0 \
    fastapi>=0.115.0 \
    uvicorn[standard]>=0.34.0 \
    httpx>=0.27.0 \
    pydantic>=2.0

WORKDIR /app
COPY server.py sync.py /app/
RUN mkdir -p /models/current /logs

ENV STORE_ID=unknown \
    EDGE_API_KEY="" \
    MODEL_PATH=/models/current/model.gguf \
    LOG_DIR=/logs \
    EDGE_N_CTX=512 \
    EDGE_N_THREADS=4 \
    EDGE_MAX_TOKENS=256

EXPOSE 8080
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8080"]
