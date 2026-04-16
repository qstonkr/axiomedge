"""Download PaddleOCR Korean models with SSL verification disabled."""

import os
import ssl
import tarfile
import urllib.request

ssl._create_default_https_context = ssl._create_unverified_context

MODELS = [
    # Det: PP-OCRv4 server (more accurate than v3 multilingual)
    (
        "https://paddleocr.bj.bcebos.com/PP-OCRv4/chinese/ch_PP-OCRv4_det_server_infer.tar",
        "/root/.paddleocr/whl/det/server",
    ),
    # Rec: PP-OCRv4 Korean (stable, pre-downloaded to avoid SSL issues at runtime)
    (
        "https://paddleocr.bj.bcebos.com/PP-OCRv4/multilingual/korean_PP-OCRv4_rec_infer.tar",
        "/root/.paddleocr/whl/rec/korean/korean_PP-OCRv4_rec_infer",
    ),
    # Cls: angle classifier (kept for optional use)
    (
        "https://paddleocr.bj.bcebos.com/dygraph_v2.0/ch/ch_ppocr_mobile_v2.0_cls_infer.tar",
        "/root/.paddleocr/whl/cls/ch_ppocr_mobile_v2.0_cls_infer",
    ),
]

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx))
urllib.request.install_opener(opener)

for url, dest_dir in MODELS:
    os.makedirs(dest_dir, exist_ok=True)
    tar_path = os.path.join(dest_dir, os.path.basename(url))
    print(f"Downloading {url} ...")
    urllib.request.urlretrieve(url, tar_path)
    print(f"  Extracting to {dest_dir}")
    with tarfile.open(tar_path) as tf:
        tf.extractall(dest_dir)
    os.remove(tar_path)

print("All Korean OCR models downloaded successfully.")
