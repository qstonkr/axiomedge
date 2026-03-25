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

WORKDIR /paddle

RUN git config --global http.sslVerify false \
    && git clone --depth 1 --branch v3.0.0 https://github.com/PaddlePaddle/Paddle.git . \
    && git submodule update --init --recursive --depth 1

RUN mkdir build && cd build && cmake .. \
    -DWITH_MKLDNN=OFF \
    -DWITH_GPU=OFF \
    -DWITH_TESTING=OFF \
    -DWITH_INFERENCE_API_TEST=OFF \
    -DON_INFER=ON \
    -DWITH_PYTHON=ON \
    -DWITH_AVX=ON \
    -DWITH_DISTRIBUTE=OFF \
    -DCMAKE_BUILD_TYPE=Release \
    && make -j$(nproc)

RUN cd build && pip wheel . -w /paddle/dist/
