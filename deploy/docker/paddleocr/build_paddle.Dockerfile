# PaddlePaddle 3.x source build WITHOUT OneDNN (MKLDNN)
#
# OneDNN is required to be disabled because PIR executor in PaddlePaddle 3.x
# auto-inserts OneDNN passes at graph lowering time, which causes
# "ConvertPirAttribute2RuntimeAttribute not support" errors on CPU.
# Runtime flags (FLAGS_use_mkldnn, FLAGS_enable_pir_api) are ineffective
# because the OneDNN dialect is already baked in at compile time.
#
# Usage:
#   docker build -f build_paddle.Dockerfile -t paddle-builder .
#   docker run --rm -v $(pwd)/wheels:/out paddle-builder cp /paddle/dist/*.whl /out/
#
# The resulting wheel can be used in the main Dockerfile:
#   COPY wheels/paddlepaddle-3*.whl /tmp/
#   RUN pip install /tmp/paddlepaddle-3*.whl

FROM python:3.10

RUN apt-get update && apt-get install -y \
    git cmake g++ make wget patchelf \
    libprotobuf-dev protobuf-compiler \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir protobuf numpy setuptools wheel

# Disable SSL verification globally (corporate proxy)
ENV GIT_SSL_NO_VERIFY=1
ENV CURL_CA_BUNDLE=""
ENV REQUESTS_CA_BUNDLE=""
ENV SSL_CERT_FILE=""
ENV PIP_TRUSTED_HOST="pypi.org pypi.python.org files.pythonhosted.org"

WORKDIR /paddle

RUN git config --global http.sslVerify false \
    && git clone --branch v3.0.0 https://github.com/PaddlePaddle/Paddle.git .

RUN git config --global http.sslVerify false \
    && git submodule update --init --recursive || true

RUN mkdir build && cd build && cmake .. \
    -DCMAKE_TLS_VERIFY=OFF \
    -DWITH_MKLDNN=OFF \
    -DWITH_GPU=OFF \
    -DWITH_TESTING=OFF \
    -DWITH_INFERENCE_API_TEST=OFF \
    -DON_INFER=ON \
    -DWITH_PYTHON=ON \
    -DWITH_AVX=OFF \
    -DWITH_SSE42=OFF \
    -DWITH_DISTRIBUTE=OFF \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_CXX_FLAGS="-Wno-error" \
    && make -j$(nproc)

RUN cd build && pip wheel . -w /paddle/dist/
