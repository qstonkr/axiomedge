FROM python:3.10

RUN apt-get update && \
    apt-get install -y libgl1 libglib2.0-0 libsm6 libxrender1 libxext6 && \
    rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
    paddlepaddle>=3.0.0 \
    paddleocr>=3.4.0 \
    flask==3.1.0 \
    opencv-python-headless>=4.9.0 \
    numpy>=1.26.0

WORKDIR /app

# CV Pipeline code (shape/arrow detection, text-shape mapping)
COPY cv_pipeline/ cv_pipeline/

# Pre-downloaded PP-OCRv5 models (avoid SSL issues during runtime)
COPY models/korean_PP-OCRv5_mobile_rec_infer /root/.paddlex/official_models/korean_PP-OCRv5_mobile_rec/
COPY models/PP-OCRv5_mobile_det_infer /root/.paddlex/official_models/PP-OCRv5_mobile_det/

# API server
COPY server.py .

EXPOSE 8866

CMD ["python", "server.py"]
