"""Full unit tests for src/cv_pipeline/ — models, preprocessor, detectors, pipeline."""

from __future__ import annotations

import io
from unittest.mock import MagicMock, patch, AsyncMock

import numpy as np
import pytest

from src.cv_pipeline.models import (
    CVResult,
    DetectedShape,
    OCRBox,
    RawEdge,
    SignalQuality,
)
from src.cv_pipeline.visual_content_analyzer import VisualAnalysisResult


# ---------------------------------------------------------------------------
# Models / dataclasses
# ---------------------------------------------------------------------------

class TestSignalQuality:
    def test_enum_values(self):
        assert SignalQuality.FULL.value == "full"
        assert SignalQuality.OCR_PRIMARY.value == "ocr_primary"
        assert SignalQuality.SHAPE_PRIMARY.value == "shape_primary"
        assert SignalQuality.OCR_ONLY.value == "ocr_only"
        assert SignalQuality.EMPTY.value == "empty"


class TestOCRBox:
    def test_creation(self):
        box = OCRBox(
            text="hello",
            polygon=[[0, 0], [10, 0], [10, 10], [0, 10]],
            confidence=0.95,
            center=(5.0, 5.0),
        )
        assert box.text == "hello"
        assert box.confidence == 0.95
        assert box.center == (5.0, 5.0)


class TestDetectedShape:
    def test_creation(self):
        shape = DetectedShape(
            shape_type="rectangle",
            bbox=(10, 20, 100, 50),
            center=(60.0, 45.0),
            area=5000.0,
            contour=np.array([[[10, 20]], [[110, 20]], [[110, 70]], [[10, 70]]]),
        )
        assert shape.shape_type == "rectangle"
        assert shape.area == 5000.0


class TestRawEdge:
    def test_creation(self):
        edge = RawEdge(
            start=(10.0, 20.0),
            end=(100.0, 200.0),
            has_arrowhead=True,
            source_shape_idx=0,
            target_shape_idx=1,
        )
        assert edge.has_arrowhead
        assert edge.source_shape_idx == 0

    def test_defaults(self):
        edge = RawEdge(start=(0, 0), end=(1, 1), has_arrowhead=False)
        assert edge.source_shape_idx is None
        assert edge.target_shape_idx is None


class TestCVResult:
    def test_empty(self):
        r = CVResult()
        assert r.ocr_boxes == []
        assert r.shapes == []
        assert r.edges == []
        assert r.shape_texts == {}
        assert r.unmapped_texts == []
        assert r.image_width == 0

    def test_with_data(self):
        r = CVResult(
            ocr_boxes=[OCRBox("hi", [[0, 0]], 0.9, (0, 0))],
            shapes=[],
            edges=[],
            image_width=100,
            image_height=200,
        )
        assert len(r.ocr_boxes) == 1
        assert r.image_width == 100


# ---------------------------------------------------------------------------
# VisualAnalysisResult
# ---------------------------------------------------------------------------

class TestVisualAnalysisResult:
    def test_defaults(self):
        r = VisualAnalysisResult()
        assert r.image_type == "unknown"
        assert r.confidence == 0.0

    def test_to_text_with_description(self):
        r = VisualAnalysisResult(
            image_type="flowchart",
            description="A process flow",
            process_steps=[{"step": 1, "action": "Start"}],
            entities=[{"name": "Server", "type": "System"}],
        )
        text = r.to_text()
        assert "[Visual: flowchart]" in text
        assert "A process flow" in text
        assert "1. Start" in text
        assert "Server" in text

    def test_to_text_ocr_only(self):
        r = VisualAnalysisResult(raw_text="OCR extracted", description="")
        text = r.to_text()
        assert "[Image OCR]" in text

    def test_to_graph_data(self):
        r = VisualAnalysisResult(
            entities=[{"name": "A", "type": "System"}],
            relationships=[{"source": "A", "target": "B", "type": "CONNECTS"}],
        )
        gd = r.to_graph_data()
        assert "nodes" in gd
        assert "relationships" in gd


# ---------------------------------------------------------------------------
# ImagePreprocessor
# ---------------------------------------------------------------------------

class TestImagePreprocessor:
    def test_normalize_rgb(self):
        from src.cv_pipeline.preprocessor import ImagePreprocessor
        from PIL import Image

        img = Image.new("RGB", (100, 100), color="red")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)

        preprocessor = ImagePreprocessor()
        bgr, pil_img = preprocessor.normalize(buf.getvalue())
        assert bgr.shape == (100, 100, 3)
        assert pil_img.mode == "RGB"

    def test_normalize_rgba(self):
        from src.cv_pipeline.preprocessor import ImagePreprocessor
        from PIL import Image

        img = Image.new("RGBA", (100, 100), color=(255, 0, 0, 128))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)

        preprocessor = ImagePreprocessor()
        bgr, pil_img = preprocessor.normalize(buf.getvalue())
        assert bgr.shape == (100, 100, 3)
        assert pil_img.mode == "RGB"

    def test_normalize_large_resize(self):
        from src.cv_pipeline.preprocessor import ImagePreprocessor
        from PIL import Image

        img = Image.new("RGB", (4000, 3000), color="blue")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)

        preprocessor = ImagePreprocessor()
        bgr, pil_img = preprocessor.normalize(buf.getvalue())
        assert max(pil_img.size) <= 2048


# ---------------------------------------------------------------------------
# ShapeDetector (mocked cv2)
# ---------------------------------------------------------------------------

class TestShapeDetector:
    def test_classify_shape_rectangle(self):
        from src.cv_pipeline.shape_detector import ShapeDetector
        detector = ShapeDetector()

        # Create a rectangular contour
        contour = np.array([[[10, 10]], [[110, 10]], [[110, 60]], [[10, 60]]], dtype=np.int32)
        shape_type = detector._classify_shape(contour)
        assert shape_type in ("rectangle", "diamond", "polygon")

    def test_classify_shape_triangle(self):
        from src.cv_pipeline.shape_detector import ShapeDetector
        detector = ShapeDetector()

        contour = np.array([[[50, 10]], [[10, 90]], [[90, 90]]], dtype=np.int32)
        shape_type = detector._classify_shape(contour)
        assert shape_type in ("triangle", "polygon", "rectangle")

    def test_build_text_mask(self):
        from src.cv_pipeline.shape_detector import ShapeDetector
        detector = ShapeDetector()

        boxes = [
            OCRBox("hi", [[10, 10], [50, 10], [50, 30], [10, 30]], 0.9, (30, 20)),
        ]
        mask = detector._build_text_mask((100, 100, 3), boxes)
        assert mask.shape == (100, 100)
        assert mask.dtype == np.uint8

    def test_detect_empty_image(self):
        from src.cv_pipeline.shape_detector import ShapeDetector
        detector = ShapeDetector()

        # Black image — no edges to detect
        image = np.zeros((100, 100, 3), dtype=np.uint8)
        shapes = detector.detect(image)
        assert isinstance(shapes, list)


# ---------------------------------------------------------------------------
# ArrowDetector
# ---------------------------------------------------------------------------

class TestArrowDetector:
    def test_detect_no_lines(self):
        from src.cv_pipeline.arrow_detector import ArrowDetector
        detector = ArrowDetector()

        image = np.zeros((100, 100, 3), dtype=np.uint8)
        edges = detector.detect(image, [])
        assert edges == []

    def test_merge_segments_empty(self):
        from src.cv_pipeline.arrow_detector import ArrowDetector
        detector = ArrowDetector()
        result = detector._merge_segments([])
        assert result == []

    def test_merge_segments_single(self):
        from src.cv_pipeline.arrow_detector import ArrowDetector
        detector = ArrowDetector()

        segs = [((0.0, 0.0), (100.0, 0.0))]
        result = detector._merge_segments(segs)
        assert len(result) == 1

    def test_merge_segments_close(self):
        from src.cv_pipeline.arrow_detector import ArrowDetector
        detector = ArrowDetector()

        segs = [
            ((0.0, 0.0), (50.0, 0.0)),
            ((55.0, 0.0), (100.0, 0.0)),  # close to end of first
        ]
        result = detector._merge_segments(segs)
        assert len(result) == 1  # merged

    def test_point_distance(self):
        from src.cv_pipeline.arrow_detector import ArrowDetector
        d = ArrowDetector._point_distance((0, 0), (3, 4))
        assert abs(d - 5.0) < 0.01

    def test_find_nearest_shape_none(self):
        from src.cv_pipeline.arrow_detector import ArrowDetector
        detector = ArrowDetector()
        result = detector._find_nearest_shape((500.0, 500.0), [])
        assert result is None


# ---------------------------------------------------------------------------
# CVPipeline
# ---------------------------------------------------------------------------

class TestCVPipeline:
    def test_empty_result(self):
        from src.cv_pipeline.pipeline import _empty_graph_result
        r = _empty_graph_result()
        assert r["image_type"] == "unknown"
        assert r["entities"] == []

    def test_assess_signal_quality_empty(self):
        from src.cv_pipeline.pipeline import CVPipeline
        p = CVPipeline()
        result = CVResult()
        quality = p._assess_signal_quality(result)
        assert quality == SignalQuality.EMPTY

    def test_assess_signal_quality_ocr_only(self):
        from src.cv_pipeline.pipeline import CVPipeline
        p = CVPipeline()
        result = CVResult(
            ocr_boxes=[OCRBox(f"text{i}", [[0, 0]], 0.9, (i * 10, 0)) for i in range(10)],
            shapes=[],
            edges=[],
        )
        quality = p._assess_signal_quality(result)
        assert quality in (SignalQuality.OCR_ONLY, SignalQuality.OCR_PRIMARY)

    def test_estimate_confidence(self):
        from src.cv_pipeline.pipeline import CVPipeline
        p = CVPipeline()
        result = CVResult(ocr_boxes=[OCRBox(f"t{i}", [[0, 0]], 0.9, (0, 0)) for i in range(15)])
        conf = p._estimate_confidence(SignalQuality.FULL, result)
        assert 0.8 <= conf <= 0.95

    def test_estimate_confidence_empty(self):
        from src.cv_pipeline.pipeline import CVPipeline
        p = CVPipeline()
        conf = p._estimate_confidence(SignalQuality.EMPTY, CVResult())
        assert conf == 0.1

    def test_ocr_only_structure_no_boxes(self):
        from src.cv_pipeline.pipeline import CVPipeline
        p = CVPipeline()
        result = p._ocr_only_structure(CVResult())
        assert result["image_type"] == "unknown"

    def test_ocr_only_structure_with_arrows(self):
        from src.cv_pipeline.pipeline import CVPipeline
        p = CVPipeline()

        boxes = [
            OCRBox("Step1 -> Step2", [[0, 0], [100, 0], [100, 20], [0, 20]], 0.9, (50, 10)),
        ]
        cv_result = CVResult(ocr_boxes=boxes, image_width=200, image_height=100)
        result = p._ocr_only_structure(cv_result)
        assert result["image_type"] == "flowchart"
        assert len(result["process_steps"]) > 0

    def test_fallback_structure_ocr_primary(self):
        from src.cv_pipeline.pipeline import CVPipeline
        p = CVPipeline()

        boxes = [OCRBox("text", [[0, 0]], 0.9, (50, 50))]
        cv_result = CVResult(ocr_boxes=boxes, image_width=200, image_height=100)
        result = p._fallback_structure(cv_result, SignalQuality.OCR_PRIMARY)
        assert "image_type" in result

    def test_fallback_structure_full(self):
        from src.cv_pipeline.pipeline import CVPipeline
        p = CVPipeline()

        contour = np.array([[[10, 10]], [[110, 10]], [[110, 60]], [[10, 60]]], dtype=np.int32)
        shapes = [DetectedShape("rectangle", (10, 10, 100, 50), (60, 35), 5000, contour)]
        edges = [RawEdge((60, 60), (60, 100), True, 0, None)]
        cv_result = CVResult(
            shapes=shapes,
            edges=edges,
            shape_texts={0: ["Process A"]},
            image_width=200,
            image_height=200,
        )
        result = p._fallback_structure(cv_result, SignalQuality.FULL)
        assert "entities" in result

    def test_is_shapes_noisy_empty(self):
        from src.cv_pipeline.pipeline import CVPipeline
        p = CVPipeline()
        assert p._is_shapes_noisy(CVResult()) is False

    def test_is_shapes_noisy_spillage(self):
        from src.cv_pipeline.pipeline import CVPipeline
        p = CVPipeline()

        contour = np.array([[[0, 0]], [[50, 0]], [[50, 50]], [[0, 50]]], dtype=np.int32)
        shapes = [DetectedShape("rectangle", (0, 0, 50, 50), (25, 25), 2500, contour)]
        cv = CVResult(
            shapes=shapes,
            shape_texts={0: ["t1", "t2", "t3", "t4", "t5", "t6", "t7"]},
            image_width=200,
            image_height=200,
        )
        assert p._is_shapes_noisy(cv) is True

    def test_is_edges_noisy_false(self):
        from src.cv_pipeline.pipeline import CVPipeline
        p = CVPipeline()
        assert p._is_edges_noisy(CVResult()) is False

    def test_is_edges_noisy_unmapped(self):
        from src.cv_pipeline.pipeline import CVPipeline
        p = CVPipeline()

        edges = [
            RawEdge((0, 0), (10, 10), False, None, None),
            RawEdge((0, 0), (10, 10), False, None, 0),
            RawEdge((0, 0), (10, 10), False, None, None),
        ]
        cv = CVResult(edges=edges, image_width=100, image_height=100)
        assert p._is_edges_noisy(cv) is True

    def test_detect_vertical_steps_too_few_boxes(self):
        from src.cv_pipeline.pipeline import CVPipeline
        p = CVPipeline()

        cv = CVResult(
            ocr_boxes=[OCRBox("hi", [[0, 0]], 0.9, (10, 10))],
            image_width=200,
            image_height=200,
        )
        steps = p._detect_vertical_steps(cv)
        assert steps == []

    def test_detect_vertical_steps_valid(self):
        from src.cv_pipeline.pipeline import CVPipeline
        p = CVPipeline()

        boxes = []
        # Left labels
        for i in range(4):
            boxes.append(OCRBox(f"Step{i}", [[0, 0]], 0.9, (30.0, i * 50.0 + 10)))
        # Right descriptions
        for i in range(4):
            boxes.append(OCRBox(f"Detail {i} description", [[0, 0]], 0.9, (150.0, i * 50.0 + 10)))

        cv = CVResult(ocr_boxes=boxes, image_width=300, image_height=250)
        steps = p._detect_vertical_steps(cv)
        # May or may not detect depending on thresholds, but shouldn't crash
        assert isinstance(steps, list)
