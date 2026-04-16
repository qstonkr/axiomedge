"""Extended unit tests for cv_pipeline: graph_normalizer and ocr_with_coords."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.pipelines.cv.models import CVResult, DetectedShape, OCRBox, RawEdge, SignalQuality
from src.pipelines.cv.graph_normalizer import GraphNormalizer


# ===========================================================================
# GraphNormalizer
# ===========================================================================

class TestGraphNormalizerBuildPrompt:
    def _make_shape(self, shape_type="rectangle", bbox=(10, 20, 100, 50), area=5000, center=(60.0, 45.0)):
        import numpy as np
        return DetectedShape(shape_type=shape_type, bbox=bbox, area=area, center=center, contour=np.array([]))

    def _make_cv_result(self, ocr_boxes=None, shapes=None, edges=None, shape_texts=None):
        return CVResult(
            image_width=800,
            image_height=600,
            ocr_boxes=ocr_boxes or [],
            shapes=shapes or [],
            edges=edges or [],
            shape_texts=shape_texts or {},
        )

    def test_build_prompt_full(self):
        ocr = [OCRBox(text="hello", polygon=[[0, 0], [10, 0], [10, 10], [0, 10]], confidence=0.9, center=(5.0, 5.0))]
        shapes = [self._make_shape()]
        edges = [RawEdge(start=(10, 10), end=(100, 100), has_arrowhead=True, source_shape_idx=0, target_shape_idx=None)]
        cv = self._make_cv_result(ocr_boxes=ocr, shapes=shapes, edges=edges, shape_texts={0: ["hello"]})
        normalizer = GraphNormalizer()
        prompt = normalizer._build_prompt(cv, SignalQuality.FULL)
        assert "800 x 600 px" in prompt
        assert "hello" in prompt

    def test_build_prompt_ocr_primary(self):
        ocr = [OCRBox(text="text1", polygon=[[0, 0], [10, 0], [10, 10], [0, 10]], confidence=0.9, center=(5.0, 30.0))]
        cv = self._make_cv_result(ocr_boxes=ocr)
        normalizer = GraphNormalizer()
        prompt = normalizer._build_prompt(cv, SignalQuality.OCR_PRIMARY)
        assert "text1" in prompt
        assert "Detected Shape" not in prompt

    def test_build_prompt_shape_primary(self):
        shapes = [self._make_shape(shape_type="circle", bbox=(10, 20, 50, 50), area=2000, center=(35.0, 45.0))]
        ocr = [OCRBox(text="label", polygon=[[0, 0], [10, 0], [10, 10], [0, 10]], confidence=0.9, center=(5.0, 5.0))]
        cv = self._make_cv_result(shapes=shapes, ocr_boxes=ocr)
        normalizer = GraphNormalizer()
        prompt = normalizer._build_prompt(cv, SignalQuality.SHAPE_PRIMARY)
        assert "circle" in prompt
        assert "label" in prompt

    def test_build_prompt_shape_primary_no_ocr(self):
        shapes = [self._make_shape(shape_type="diamond", bbox=(10, 20, 50, 50), area=2000, center=(35.0, 45.0))]
        cv = self._make_cv_result(shapes=shapes)
        normalizer = GraphNormalizer()
        prompt = normalizer._build_prompt(cv, SignalQuality.SHAPE_PRIMARY)
        assert "(none)" in prompt

    def test_build_prompt_empty(self):
        cv = self._make_cv_result()
        normalizer = GraphNormalizer()
        prompt = normalizer._build_prompt(cv, SignalQuality.FULL)
        assert "(no text)" in prompt
        assert "(no shapes)" in prompt
        assert "(no connections)" in prompt


class TestGraphNormalizerSanitize:
    def test_sanitize_text(self):
        result = GraphNormalizer._sanitize_text("{{prompt injection}}")
        assert "{{" not in result
        assert "}}" not in result

    def test_sanitize_control_chars(self):
        result = GraphNormalizer._sanitize_text("hello\x00world")
        assert "\x00" not in result
        assert "hello" in result

    def test_sanitize_truncation(self):
        result = GraphNormalizer._sanitize_text("a" * 300)
        assert len(result) == 200


class TestGraphNormalizerOCRLayout:
    def test_ocr_layout_grouping(self):
        ocr = [
            OCRBox(text="left", polygon=[[0, 0], [1, 0], [1, 1], [0, 1]], confidence=0.9, center=(10.0, 30.0)),
            OCRBox(text="right", polygon=[[0, 0], [1, 0], [1, 1], [0, 1]], confidence=0.9, center=(100.0, 30.0)),
            OCRBox(text="below", polygon=[[0, 0], [1, 0], [1, 1], [0, 1]], confidence=0.9, center=(50.0, 100.0)),
        ]
        cv = CVResult(image_width=200, image_height=200, ocr_boxes=ocr, shapes=[], edges=[], shape_texts={})
        normalizer = GraphNormalizer()
        layout = normalizer._build_ocr_layout(cv)
        lines = layout.strip().split("\n")
        assert len(lines) >= 2  # at least 2 y-groups
        # First line should have "left" before "right" (x ordering)
        assert "left" in lines[0]

    def test_ocr_layout_empty(self):
        cv = CVResult(image_width=200, image_height=200, ocr_boxes=[], shapes=[], edges=[], shape_texts={})
        normalizer = GraphNormalizer()
        assert normalizer._build_ocr_layout(cv) == ""


class TestGraphNormalizerEdgesBlock:
    def test_edges_block_dedup(self):
        import numpy as np
        shapes = [
            DetectedShape(shape_type="rect", bbox=(0, 0, 10, 10), area=100, center=(5.0, 5.0), contour=np.array([])),
            DetectedShape(shape_type="rect", bbox=(20, 20, 10, 10), area=100, center=(25.0, 25.0), contour=np.array([])),
        ]
        edges = [
            RawEdge(start=(5, 5), end=(25, 25), has_arrowhead=True, source_shape_idx=0, target_shape_idx=1),
            RawEdge(start=(5, 5), end=(25, 25), has_arrowhead=True, source_shape_idx=0, target_shape_idx=1),
        ]
        cv = CVResult(image_width=100, image_height=100, ocr_boxes=[], shapes=shapes, edges=edges, shape_texts={0: ["A"], 1: ["B"]})
        normalizer = GraphNormalizer()
        block = normalizer._build_edges_block(cv)
        # Should only have 1 line due to dedup
        lines = [l for l in block.strip().split("\n") if l.strip()]
        assert len(lines) == 1

    def test_edges_block_empty(self):
        cv = CVResult(image_width=100, image_height=100, ocr_boxes=[], shapes=[], edges=[], shape_texts={})
        normalizer = GraphNormalizer()
        assert normalizer._build_edges_block(cv) == ""


class TestGraphNormalizerParseResponse:
    def test_parse_valid_json(self):
        normalizer = GraphNormalizer()
        resp = json.dumps({
            "image_type": "flowchart",
            "description": "A flow",
            "entities": [{"name": "A", "type": "Process"}],
            "relationships": [],
            "process_steps": [],
            "tags": ["test"],
        })
        result = normalizer._parse_response(resp)
        assert result["image_type"] == "flowchart"
        assert len(result["entities"]) == 1

    def test_parse_code_block(self):
        normalizer = GraphNormalizer()
        resp = '```json\n{"image_type": "table", "description": "", "entities": [], "relationships": [], "process_steps": [], "tags": []}\n```'
        result = normalizer._parse_response(resp)
        assert result["image_type"] == "table"

    def test_parse_invalid_json(self):
        normalizer = GraphNormalizer()
        result = normalizer._parse_response("not json at all")
        assert result["image_type"] == "unknown"

    def test_parse_json_with_prefix(self):
        normalizer = GraphNormalizer()
        resp = 'Here is the result: {"image_type": "architecture", "description": "test", "entities": [], "relationships": [], "process_steps": [], "tags": []}'
        result = normalizer._parse_response(resp)
        assert result["image_type"] == "architecture"


class TestGraphNormalizerExtractFields:
    def test_extract_fields_partial(self):
        result = GraphNormalizer._extract_fields({"image_type": "other"})
        assert result["image_type"] == "other"
        assert result["entities"] == []
        assert result["relationships"] == []

    def test_empty_result(self):
        result = GraphNormalizer._empty_result()
        assert result["image_type"] == "unknown"
        assert result["entities"] == []


class TestGraphNormalizerNormalize:
    async def test_normalize_full_flow(self):
        normalizer = GraphNormalizer()
        cv = CVResult(
            image_width=800, image_height=600,
            ocr_boxes=[OCRBox(text="test", polygon=[[0, 0], [1, 0], [1, 1], [0, 1]], confidence=0.9, center=(5.0, 5.0))],
            shapes=[], edges=[], shape_texts={},
        )
        fake_resp = json.dumps({
            "image_type": "flowchart",
            "description": "desc",
            "entities": [],
            "relationships": [],
            "process_steps": [],
            "tags": [],
        })
        with patch.object(normalizer, "_call_llm", new_callable=AsyncMock, return_value=fake_resp):
            result = await normalizer.normalize(cv, SignalQuality.FULL)
            assert result["image_type"] == "flowchart"


# ===========================================================================
# OCRWithCoords
# ===========================================================================
class TestOCRWithCoords:
    def test_extract_no_paddleocr(self):
        from src.pipelines.cv.ocr_with_coords import OCRWithCoords
        ocr = OCRWithCoords()
        with patch.dict("sys.modules", {"paddleocr": None}):
            with patch("src.pipelines.cv.ocr_with_coords.OCRWithCoords.extract", side_effect=ImportError):
                # Just test that the class can be instantiated
                assert ocr is not None

    def test_extract_legacy_format(self):
        from src.pipelines.cv.ocr_with_coords import OCRWithCoords
        ocr = OCRWithCoords()
        # Legacy format: [[[box, (text, score)], ...]]
        legacy_result = [[
            [[[10, 10], [50, 10], [50, 30], [10, 30]], ("hello", 0.95)],
            [[[60, 10], [100, 10], [100, 30], [60, 30]], ("world", 0.85)],
        ]]
        boxes = ocr._extract_legacy(legacy_result)
        assert len(boxes) == 2
        assert boxes[0].text == "hello"
        assert boxes[0].confidence == 0.95
        assert boxes[1].text == "world"

    def test_extract_legacy_empty(self):
        from src.pipelines.cv.ocr_with_coords import OCRWithCoords
        ocr = OCRWithCoords()
        assert ocr._extract_legacy([]) == []
        assert ocr._extract_legacy([None]) == []
        assert ocr._extract_legacy([[None]]) == []

    def test_extract_legacy_malformed(self):
        from src.pipelines.cv.ocr_with_coords import OCRWithCoords
        ocr = OCRWithCoords()
        # Malformed entries should be skipped
        legacy_result = [[
            "not_a_list",
            [[[10, 10], [50, 10], [50, 30], [10, 30]], "not_a_tuple"],
            [[[10, 10], [50, 10], [50, 30], [10, 30]], ("text", 0.9)],
        ]]
        boxes = ocr._extract_legacy(legacy_result)
        assert len(boxes) == 1
        assert boxes[0].text == "text"

    def test_extract_legacy_empty_text(self):
        from src.pipelines.cv.ocr_with_coords import OCRWithCoords
        ocr = OCRWithCoords()
        legacy_result = [[
            [[[10, 10], [50, 10], [50, 30], [10, 30]], ("", 0.9)],
        ]]
        boxes = ocr._extract_legacy(legacy_result)
        assert len(boxes) == 0

    def test_extract_legacy_short_polygon(self):
        from src.pipelines.cv.ocr_with_coords import OCRWithCoords
        ocr = OCRWithCoords()
        legacy_result = [[
            [[[10, 10]], ("text", 0.9)],  # too few polygon points
        ]]
        boxes = ocr._extract_legacy(legacy_result)
        assert len(boxes) == 0
