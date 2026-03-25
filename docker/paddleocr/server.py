"""PaddleOCR + CV Pipeline HTTP API Server.

Combined OCR text extraction + shape/arrow detection in one container.
Runs on linux/amd64 with PaddlePaddle + OpenCV.

Endpoints:
    POST /ocr      -- Text extraction only (fast)
    POST /analyze   -- Full CV pipeline: OCR + shapes + arrows + mapping
    GET  /health    -- Health check
"""

import base64
import json
import logging
import os
import ssl
import tempfile

import cv2
import numpy as np

# Disable SSL verification for corporate proxy
ssl._create_default_https_context = ssl._create_unverified_context

from flask import Flask, request, jsonify

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Lazy-load PaddleOCR
_ocr_engine = None


def get_engine():
    global _ocr_engine
    if _ocr_engine is None:
        import paddle
        paddle.set_flags({
            'FLAGS_use_mkldnn': False,
            'FLAGS_enable_pir_api': 0,
        })

        from paddleocr import PaddleOCR
        # PaddleOCR 3.x API with PP-OCRv5 Korean model (pre-downloaded in Dockerfile)
        _ocr_engine = PaddleOCR(
            text_recognition_model_name="korean_PP-OCRv5_mobile_rec",
            text_detection_model_name="PP-OCRv5_mobile_det",
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
            device="cpu",
        )
        logger.info("PaddleOCR 3.x engine initialized (Korean PP-OCRv5 mobile rec + server det)")
    return _ocr_engine


def _decode_image(data):
    """Decode base64 image to bytes and numpy array."""
    img_bytes = base64.b64decode(data["image"])
    nparr = np.frombuffer(img_bytes, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    return img_bytes, img


def _preprocess_image(img_bytes):
    """Validate and normalise image to avoid PaddleOCR internal errors.

    Guards against:
    - Too-small images (< 10px on any side)
    - Extreme aspect ratios that cause broadcast dimension mismatch
    - Corrupt / un-decodable images
    Returns sanitised PNG bytes, or None if the image is unusable.
    """
    MIN_SIDE = 20
    MAX_ASPECT_RATIO = 20.0
    MIN_SIDE_FOR_PAD = 32  # PaddleOCR det model minimum

    nparr = np.frombuffer(img_bytes, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    if img is None:
        logger.warning("Image could not be decoded, skipping")
        return None

    h, w = img.shape[:2]
    if h < MIN_SIDE or w < MIN_SIDE:
        logger.warning("Image too small (%dx%d), skipping", w, h)
        return None

    aspect = max(h, w) / max(min(h, w), 1)
    if aspect > MAX_ASPECT_RATIO:
        # Pad short side to reduce aspect ratio (avoids broadcast mismatch)
        target = max(h, w) // int(MAX_ASPECT_RATIO)
        target = max(target, MIN_SIDE_FOR_PAD)
        if h < w:
            pad_total = target - h
            top = pad_total // 2
            bottom = pad_total - top
            img = cv2.copyMakeBorder(img, top, bottom, 0, 0, cv2.BORDER_CONSTANT, value=(255, 255, 255))
        else:
            pad_total = target - w
            left = pad_total // 2
            right = pad_total - left
            img = cv2.copyMakeBorder(img, 0, 0, left, right, cv2.BORDER_CONSTANT, value=(255, 255, 255))
        logger.info("Padded extreme aspect ratio image (%dx%d -> %dx%d)", w, h, img.shape[1], img.shape[0])

    # Ensure minimum dimensions for PaddleOCR det model
    h, w = img.shape[:2]
    if h < MIN_SIDE_FOR_PAD or w < MIN_SIDE_FOR_PAD:
        new_h = max(h, MIN_SIDE_FOR_PAD)
        new_w = max(w, MIN_SIDE_FOR_PAD)
        img = cv2.copyMakeBorder(
            img, 0, new_h - h, 0, new_w - w,
            cv2.BORDER_CONSTANT, value=(255, 255, 255),
        )

    _, buf = cv2.imencode(".png", img)
    return buf.tobytes()


def _resize_to_multiple(img_bytes, multiple=32):
    """Resize image so width and height are multiples of `multiple`.

    PaddleOCR rec model expects width divisible by certain values.
    Mismatches cause 'Broadcast dimension mismatch' errors.
    """
    nparr = np.frombuffer(img_bytes, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    if img is None:
        return None
    h, w = img.shape[:2]
    new_w = max(multiple, (w + multiple - 1) // multiple * multiple)
    new_h = max(multiple, (h + multiple - 1) // multiple * multiple)
    if new_w != w or new_h != h:
        # Pad with white to reach target dimensions
        img = cv2.copyMakeBorder(
            img, 0, new_h - h, 0, new_w - w,
            cv2.BORDER_CONSTANT, value=(255, 255, 255),
        )
        logger.info("Padded image to multiple of %d: %dx%d -> %dx%d", multiple, w, h, new_w, new_h)
    _, buf = cv2.imencode(".png", img)
    return buf.tobytes()


def _run_ocr_once(img_bytes):
    """Single OCR attempt using PaddleOCR 3.x predict() API."""
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        f.write(img_bytes)
        tmp_path = f.name

    try:
        engine = get_engine()
        results = engine.predict(tmp_path)

        texts = []
        boxes = []
        confidences = []

        for res in results:
            data = res["res"] if isinstance(res, dict) else getattr(res, "res", res)
            rec_texts = data.get("rec_texts", [])
            rec_scores = data.get("rec_scores", [])
            rec_polys = data.get("rec_polys", data.get("dt_polys", []))

            for i, text in enumerate(rec_texts):
                score = rec_scores[i] if i < len(rec_scores) else 0.0
                polygon = rec_polys[i] if i < len(rec_polys) else []
                if hasattr(polygon, "tolist"):
                    polygon = polygon.tolist()
                texts.append(text)
                confidences.append(score)
                center = [0, 0]
                if polygon and len(polygon) >= 4:
                    center = [
                        sum(p[0] for p in polygon) / len(polygon),
                        sum(p[1] for p in polygon) / len(polygon),
                    ]
                boxes.append({
                    "text": text,
                    "polygon": polygon,
                    "confidence": score,
                    "center": center,
                })

        avg_conf = sum(confidences) / len(confidences) if confidences else 0.0
        return texts, boxes, avg_conf
    finally:
        os.unlink(tmp_path)


def _run_ocr(img_bytes):
    """Run PaddleOCR with preprocessing. Single attempt to avoid segfault on retry."""
    sanitised = _preprocess_image(img_bytes)
    if sanitised is None:
        return [], [], 0.0

    try:
        return _run_ocr_once(sanitised)
    except Exception as e:
        logger.warning("OCR failed: %s", e)
        return [], [], 0.0


def _detect_shapes(img):
    """Detect shapes using OpenCV contours."""
    from cv_pipeline.shape_detector import ShapeDetector
    detector = ShapeDetector()
    return detector.detect(img)


def _detect_arrows(img):
    """Detect arrows using HoughLinesP."""
    from cv_pipeline.arrow_detector import ArrowDetector
    detector = ArrowDetector()
    return detector.detect(img)


def _map_text_to_shapes(ocr_boxes, shapes):
    """Map OCR text to detected shapes using point-in-polygon."""
    from cv_pipeline.text_shape_mapper import TextShapeMapper
    mapper = TextShapeMapper()
    return mapper.map(ocr_boxes, shapes)


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "engine": "paddleocr+cv_pipeline"})


@app.route("/ocr", methods=["POST"])
def ocr():
    """Text extraction only (fast path)."""
    data = request.get_json()
    if not data or "image" not in data:
        return jsonify({"error": "Missing 'image' field (base64)"}), 400

    try:
        img_bytes = base64.b64decode(data["image"])
        texts, boxes, confidence = _run_ocr(img_bytes)

        return jsonify({
            "texts": texts,
            "full_text": "\n".join(texts),
            "boxes": boxes,
            "confidence": confidence,
            "line_count": len(texts),
        })
    except Exception as e:
        logger.error("OCR failed: %s", e)
        return jsonify({"error": str(e)}), 500


@app.route("/analyze", methods=["POST"])
def analyze():
    """Full CV pipeline: OCR + shapes + arrows + text-shape mapping."""
    data = request.get_json()
    if not data or "image" not in data:
        return jsonify({"error": "Missing 'image' field (base64)"}), 400

    try:
        img_bytes, img = _decode_image(data)

        # 1. OCR (text + bounding boxes)
        texts, ocr_boxes, ocr_confidence = _run_ocr(img_bytes)

        # 2. Shape detection (OpenCV)
        shapes = []
        try:
            raw_shapes = _detect_shapes(img)
            shapes = [s if isinstance(s, dict) else {"type": str(s)} for s in raw_shapes]
        except Exception as e:
            logger.warning("Shape detection failed: %s", e)

        # 3. Arrow detection (OpenCV)
        arrows = []
        try:
            raw_arrows = _detect_arrows(img)
            arrows = [a if isinstance(a, dict) else {"type": str(a)} for a in raw_arrows]
        except Exception as e:
            logger.warning("Arrow detection failed: %s", e)

        # 4. Text-shape mapping
        mappings = []
        try:
            if ocr_boxes and shapes:
                mappings = _map_text_to_shapes(ocr_boxes, shapes)
        except Exception as e:
            logger.warning("Text-shape mapping failed: %s", e)

        return jsonify({
            "texts": texts,
            "full_text": "\n".join(texts),
            "ocr_boxes": ocr_boxes,
            "ocr_confidence": ocr_confidence,
            "shapes": shapes,
            "arrows": arrows,
            "text_shape_mappings": mappings,
            "shape_count": len(shapes),
            "arrow_count": len(arrows),
        })
    except Exception as e:
        logger.error("Analyze failed: %s", e)
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8866"))
    logger.info("Starting PaddleOCR + CV Pipeline API server on port %d", port)
    get_engine()  # Pre-load
    app.run(host="0.0.0.0", port=port)
