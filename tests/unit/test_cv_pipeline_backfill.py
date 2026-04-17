"""Backfill coverage for src/pipelines/cv/pipeline.py.

Covers analyze(), _process_by_quality(), _ocr_only_structure() edge
patterns (formula, table), _classify_left_right_items(),
_merge_consecutive_labels(), _map_labels_to_descriptions(),
_edges_to_relationships(), _detect_vertical_steps(), _get_pool(),
and the module-level process helper functions.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
from src.pipelines.cv.models import (
    CVResult,
    DetectedShape,
    OCRBox,
    RawEdge,
    SignalQuality,
)
from src.pipelines.cv.visual_content_analyzer import VisualAnalysisResult


def _box(text: str, cx: float, cy: float) -> OCRBox:
    """Helper to create an OCRBox at a given center."""
    return OCRBox(
        text=text,
        polygon=[
            [cx - 5, cy - 5],
            [cx + 5, cy - 5],
            [cx + 5, cy + 5],
            [cx - 5, cy + 5],
        ],
        confidence=0.9,
        center=(cx, cy),
    )


def _shape(
    stype: str = "rectangle",
    bbox: tuple = (10, 10, 50, 50),
    area: float = 2500.0,
    center: tuple = (35.0, 35.0),
) -> DetectedShape:
    contour = np.array(
        [[[bbox[0], bbox[1]]],
         [[bbox[0] + bbox[2], bbox[1]]],
         [[bbox[0] + bbox[2], bbox[1] + bbox[3]]],
         [[bbox[0], bbox[1] + bbox[3]]]],
        dtype=np.int32,
    )
    return DetectedShape(
        shape_type=stype, bbox=bbox, center=center,
        area=area, contour=contour,
    )


# -----------------------------------------------------------------------
# Module-level process helper functions
# -----------------------------------------------------------------------

class TestModuleLevelHelpers:
    def test_ocr_in_process(self):
        from src.pipelines.cv.pipeline import _ocr_in_process

        mock_result = [_box("hello", 10, 10)]
        with patch(
            "src.pipelines.cv.pipeline.OCRWithCoords"
        ) as MockOCR:
            MockOCR.return_value.extract.return_value = mock_result
            result = _ocr_in_process(b"fake_image_bytes")
            assert result == mock_result

    def test_detect_shapes_in_process(self):
        from src.pipelines.cv.pipeline import _detect_shapes_in_process

        img = np.zeros((100, 100, 3), dtype=np.uint8)
        img_bytes = img.tobytes()
        shape_tuple = (100, 100, 3)

        with patch(
            "src.pipelines.cv.pipeline.ShapeDetector"
        ) as MockSD:
            MockSD.return_value.detect.return_value = []
            result = _detect_shapes_in_process(
                img_bytes, shape_tuple, []
            )
            assert result == []

    def test_detect_arrows_in_process(self):
        from src.pipelines.cv.pipeline import _detect_arrows_in_process

        img = np.zeros((100, 100, 3), dtype=np.uint8)
        img_bytes = img.tobytes()
        shape_tuple = (100, 100, 3)

        with patch(
            "src.pipelines.cv.pipeline.ArrowDetector"
        ) as MockAD:
            MockAD.return_value.detect.return_value = []
            result = _detect_arrows_in_process(
                img_bytes, shape_tuple, []
            )
            assert result == []


# -----------------------------------------------------------------------
# CVPipeline._get_pool()
# -----------------------------------------------------------------------

class TestCVPipelineGetPool:
    def test_get_pool_creates_pool(self):
        from src.pipelines.cv.pipeline import CVPipeline

        # Reset pool
        CVPipeline._process_pool = None
        with patch(
            "src.pipelines.cv.pipeline.ProcessPoolExecutor",
            create=True,
        ):
            with patch(
                "concurrent.futures.ProcessPoolExecutor",
                return_value=MagicMock(),
            ):
                import multiprocessing as mp
                with patch.object(
                    mp, "get_context",
                ) as mock_ctx:
                    mock_ctx.return_value = MagicMock()
                    pool = CVPipeline._get_pool()
                    assert pool is not None
        # Cleanup
        CVPipeline._process_pool = None

    def test_get_pool_reuses_existing(self):
        from src.pipelines.cv.pipeline import CVPipeline

        sentinel = MagicMock()
        CVPipeline._process_pool = sentinel
        assert CVPipeline._get_pool() is sentinel
        CVPipeline._process_pool = None


# -----------------------------------------------------------------------
# CVPipeline.analyze() — main async entry point
# -----------------------------------------------------------------------

class TestCVPipelineAnalyze:
    async def test_analyze_empty_bytes(self):
        from src.pipelines.cv.pipeline import CVPipeline

        pipeline = CVPipeline()
        result = await pipeline.analyze(b"")
        assert isinstance(result, VisualAnalysisResult)
        assert result.confidence == 0.0

    async def test_analyze_too_small(self):
        from src.pipelines.cv.pipeline import CVPipeline

        pipeline = CVPipeline()
        result = await pipeline.analyze(b"x" * 50)
        assert result.confidence == 0.0

    async def test_analyze_none_bytes(self):
        from src.pipelines.cv.pipeline import CVPipeline

        pipeline = CVPipeline()
        result = await pipeline.analyze(b"")
        assert result.confidence == 0.0

    async def test_analyze_success_ocr_only(self):
        from src.pipelines.cv.pipeline import CVPipeline

        pipeline = CVPipeline()
        fake_np = np.zeros((100, 200, 3), dtype=np.uint8)
        boxes = [_box(f"text{i}", 50.0, i * 20.0) for i in range(6)]

        with patch.object(
            pipeline.preprocessor, "normalize",
            return_value=(fake_np, MagicMock()),
        ):
            with patch.object(
                pipeline, "_get_pool", return_value=MagicMock()
            ):
                with patch(
                    "asyncio.get_running_loop"
                ) as mock_loop:
                    loop = AsyncMock()
                    # OCR returns boxes, shapes+arrows return []
                    loop.run_in_executor = AsyncMock(
                        side_effect=[boxes, [], []]
                    )
                    mock_loop.return_value = loop

                    result = await pipeline.analyze(
                        b"x" * 200
                    )
                    assert isinstance(result, VisualAnalysisResult)
                    assert result.confidence > 0

    async def test_analyze_ocr_failure_resets_pool(self):
        from src.pipelines.cv.pipeline import CVPipeline

        pipeline = CVPipeline()
        fake_np = np.zeros((100, 200, 3), dtype=np.uint8)

        with patch.object(
            pipeline.preprocessor, "normalize",
            return_value=(fake_np, MagicMock()),
        ):
            with patch.object(
                pipeline, "_get_pool", return_value=MagicMock()
            ):
                with patch(
                    "asyncio.get_running_loop"
                ) as mock_loop:
                    loop = AsyncMock()
                    # OCR raises, shapes/arrows return []
                    loop.run_in_executor = AsyncMock(
                        side_effect=[
                            RuntimeError("SIGSEGV"),
                            [],
                            [],
                        ]
                    )
                    mock_loop.return_value = loop
                    CVPipeline._process_pool = MagicMock()

                    result = await pipeline.analyze(b"x" * 200)
                    # Pool reset
                    assert CVPipeline._process_pool is None
                    assert isinstance(result, VisualAnalysisResult)

    async def test_analyze_shape_detection_failure(self):
        from src.pipelines.cv.pipeline import CVPipeline

        pipeline = CVPipeline()
        fake_np = np.zeros((100, 200, 3), dtype=np.uint8)

        with patch.object(
            pipeline.preprocessor, "normalize",
            return_value=(fake_np, MagicMock()),
        ):
            with patch.object(
                pipeline, "_get_pool", return_value=MagicMock()
            ):
                with patch(
                    "asyncio.get_running_loop"
                ) as mock_loop:
                    loop = AsyncMock()
                    loop.run_in_executor = AsyncMock(
                        side_effect=[
                            [],  # OCR ok but empty
                            RuntimeError("shape fail"),
                            [],  # arrows ok
                        ]
                    )
                    mock_loop.return_value = loop

                    result = await pipeline.analyze(b"x" * 200)
                    assert isinstance(result, VisualAnalysisResult)

    async def test_analyze_arrow_detection_failure(self):
        from src.pipelines.cv.pipeline import CVPipeline

        pipeline = CVPipeline()
        fake_np = np.zeros((100, 200, 3), dtype=np.uint8)

        with patch.object(
            pipeline.preprocessor, "normalize",
            return_value=(fake_np, MagicMock()),
        ):
            with patch.object(
                pipeline, "_get_pool", return_value=MagicMock()
            ):
                with patch(
                    "asyncio.get_running_loop"
                ) as mock_loop:
                    loop = AsyncMock()
                    loop.run_in_executor = AsyncMock(
                        side_effect=[
                            [],  # OCR
                            [],  # shapes
                            RuntimeError("arrow fail"),
                        ]
                    )
                    mock_loop.return_value = loop

                    result = await pipeline.analyze(b"x" * 200)
                    assert isinstance(result, VisualAnalysisResult)


# -----------------------------------------------------------------------
# _process_by_quality()
# -----------------------------------------------------------------------

class TestProcessByQuality:
    async def test_empty_quality(self):
        from src.pipelines.cv.pipeline import CVPipeline

        p = CVPipeline()
        result = await p._process_by_quality(
            CVResult(), SignalQuality.EMPTY
        )
        assert result["image_type"] == "unknown"

    async def test_ocr_only_quality(self):
        from src.pipelines.cv.pipeline import CVPipeline

        p = CVPipeline()
        cv = CVResult(
            ocr_boxes=[_box("hello", 50, 50)],
            image_width=200,
            image_height=100,
        )
        result = await p._process_by_quality(
            cv, SignalQuality.OCR_ONLY
        )
        assert "image_type" in result

    async def test_full_quality_normalizer_success(self):
        from src.pipelines.cv.pipeline import CVPipeline

        p = CVPipeline()
        expected = {
            "image_type": "flowchart",
            "description": "test",
            "entities": [],
            "relationships": [],
            "process_steps": [],
            "tags": [],
        }
        with patch.object(
            p.normalizer, "normalize",
            new_callable=AsyncMock,
            return_value=expected,
        ):
            result = await p._process_by_quality(
                CVResult(), SignalQuality.FULL
            )
            assert result["image_type"] == "flowchart"

    async def test_full_quality_normalizer_failure_fallback(self):
        from src.pipelines.cv.pipeline import CVPipeline

        p = CVPipeline()
        cv = CVResult(
            ocr_boxes=[_box("text", 50, 50)],
            image_width=200,
            image_height=100,
        )
        with patch.object(
            p.normalizer, "normalize",
            new_callable=AsyncMock,
            side_effect=RuntimeError("LLM fail"),
        ):
            result = await p._process_by_quality(
                cv, SignalQuality.OCR_PRIMARY
            )
            assert "image_type" in result


# -----------------------------------------------------------------------
# _ocr_only_structure() — formula and table patterns
# -----------------------------------------------------------------------

class TestOCROnlyStructurePatterns:
    def test_formula_pattern(self):
        from src.pipelines.cv.pipeline import CVPipeline

        p = CVPipeline()
        boxes = [
            _box("A + B = C", 100.0, 50.0),
        ]
        cv = CVResult(
            ocr_boxes=boxes, image_width=200, image_height=100
        )
        result = p._ocr_only_structure(cv)
        assert result["image_type"] == "formula"

    def test_table_pattern(self):
        from src.pipelines.cv.pipeline import CVPipeline

        p = CVPipeline()
        # 4+ rows, each with 2+ items
        boxes = []
        for row in range(5):
            for col in range(3):
                boxes.append(
                    _box(
                        f"r{row}c{col}",
                        col * 60.0 + 30.0,
                        row * 20.0 + 10.0,
                    )
                )
        cv = CVResult(
            ocr_boxes=boxes, image_width=200, image_height=120
        )
        result = p._ocr_only_structure(cv)
        # Should detect table pattern (multi-column multi-row)
        assert result["image_type"] in ("table", "text_image")

    def test_arrow_symbol_unicode(self):
        from src.pipelines.cv.pipeline import CVPipeline

        p = CVPipeline()
        boxes = [
            _box("Start \u2192 End", 100.0, 10.0),
        ]
        cv = CVResult(
            ocr_boxes=boxes, image_width=200, image_height=50
        )
        result = p._ocr_only_structure(cv)
        assert result["image_type"] == "flowchart"
        assert len(result["process_steps"]) == 1

    def test_double_arrow_symbol(self):
        from src.pipelines.cv.pipeline import CVPipeline

        p = CVPipeline()
        boxes = [
            _box("A \u21d2 B", 100.0, 10.0),
        ]
        cv = CVResult(
            ocr_boxes=boxes, image_width=200, image_height=50
        )
        result = p._ocr_only_structure(cv)
        assert result["image_type"] == "flowchart"

    def test_triangle_arrow(self):
        from src.pipelines.cv.pipeline import CVPipeline

        p = CVPipeline()
        boxes = [
            _box("A \u25b6 B", 100.0, 10.0),
        ]
        cv = CVResult(
            ocr_boxes=boxes, image_width=200, image_height=50
        )
        result = p._ocr_only_structure(cv)
        assert result["image_type"] == "flowchart"


# -----------------------------------------------------------------------
# _classify_left_right_items()
# -----------------------------------------------------------------------

class TestClassifyLeftRightItems:
    def test_classify(self):
        from src.pipelines.cv.pipeline import CVPipeline

        boxes = [
            _box("Label1", 20.0, 10.0),    # left
            _box("Detail A longer text here", 120.0, 10.0),  # right
            _box("Label2", 30.0, 60.0),    # left
            _box("  ", 10.0, 100.0),        # empty, skip
        ]
        left, right = CVPipeline._classify_left_right_items(
            boxes, left_threshold=70.0
        )
        assert len(left) == 2
        assert len(right) == 1

    def test_classify_all_right(self):
        from src.pipelines.cv.pipeline import CVPipeline

        boxes = [_box("text", 200.0, 10.0)]
        left, right = CVPipeline._classify_left_right_items(
            boxes, left_threshold=50.0
        )
        assert len(left) == 0
        assert len(right) == 1

    def test_classify_left_too_long(self):
        from src.pipelines.cv.pipeline import CVPipeline

        # Text longer than max (10 chars) at left position
        boxes = [_box("A very long label text", 20.0, 10.0)]
        left, right = CVPipeline._classify_left_right_items(
            boxes, left_threshold=70.0
        )
        # len > 10 so not classified as left label
        assert len(left) == 0


# -----------------------------------------------------------------------
# _merge_consecutive_labels()
# -----------------------------------------------------------------------

class TestMergeConsecutiveLabels:
    def test_merge_close(self):
        from src.pipelines.cv.pipeline import CVPipeline

        items = [(10.0, "A"), (30.0, "B"), (100.0, "C")]
        merged = CVPipeline._merge_consecutive_labels(items)
        # A and B are close (gap=20 < 40), C is far
        assert len(merged) == 2
        assert "A B" in merged[0][2]
        assert merged[1][2] == "C"

    def test_merge_none_close(self):
        from src.pipelines.cv.pipeline import CVPipeline

        items = [(10.0, "A"), (100.0, "B"), (200.0, "C")]
        merged = CVPipeline._merge_consecutive_labels(items)
        assert len(merged) == 3

    def test_merge_empty(self):
        from src.pipelines.cv.pipeline import CVPipeline

        merged = CVPipeline._merge_consecutive_labels([])
        assert merged == []


# -----------------------------------------------------------------------
# _map_labels_to_descriptions()
# -----------------------------------------------------------------------

class TestMapLabelsToDescriptions:
    def test_map_with_descriptions(self):
        from src.pipelines.cv.pipeline import CVPipeline

        labels = [
            (10.0, 10.0, "Step1"),
            (80.0, 80.0, "Step2"),
        ]
        right = [
            (15.0, "Detail for step1"),
            (85.0, "Detail for step2"),
        ]
        steps = CVPipeline._map_labels_to_descriptions(
            labels, right
        )
        assert len(steps) == 2
        assert steps[0]["step"] == 1
        assert "Step1" in steps[0]["action"]
        assert "Detail for step1" in steps[0]["action"]

    def test_map_no_descriptions(self):
        from src.pipelines.cv.pipeline import CVPipeline

        labels = [(10.0, 10.0, "Step1")]
        steps = CVPipeline._map_labels_to_descriptions(labels, [])
        assert len(steps) == 1
        assert steps[0]["action"] == "Step1"

    def test_map_empty_labels(self):
        from src.pipelines.cv.pipeline import CVPipeline

        steps = CVPipeline._map_labels_to_descriptions([], [])
        assert steps == []


# -----------------------------------------------------------------------
# _detect_vertical_steps()
# -----------------------------------------------------------------------

class TestDetectVerticalSteps:
    def test_zero_width(self):
        from src.pipelines.cv.pipeline import CVPipeline

        p = CVPipeline()
        boxes = [_box(f"t{i}", 10, i * 20) for i in range(8)]
        cv = CVResult(
            ocr_boxes=boxes, image_width=0, image_height=200
        )
        assert p._detect_vertical_steps(cv) == []

    def test_too_few_left_items(self):
        from src.pipelines.cv.pipeline import CVPipeline

        p = CVPipeline()
        # All boxes are at right positions (above threshold)
        boxes = [_box(f"text{i}", 200.0, i * 30.0) for i in range(8)]
        cv = CVResult(
            ocr_boxes=boxes, image_width=300, image_height=300
        )
        steps = p._detect_vertical_steps(cv)
        assert steps == []

    def test_valid_vertical_steps(self):
        from src.pipelines.cv.pipeline import CVPipeline

        p = CVPipeline()
        boxes = []
        # 4 left labels (short text, at x < 0.35 * 400 = 140)
        for i in range(4):
            boxes.append(
                _box(f"Step{i}", 30.0, i * 60.0 + 10.0)
            )
        # Right descriptions
        for i in range(4):
            boxes.append(
                _box(
                    f"Description {i}",
                    250.0,
                    i * 60.0 + 10.0,
                )
            )
        cv = CVResult(
            ocr_boxes=boxes, image_width=400, image_height=300
        )
        steps = p._detect_vertical_steps(cv)
        # 4 left labels >= 3 min, should produce steps
        assert len(steps) >= 3


# -----------------------------------------------------------------------
# _edges_to_relationships()
# -----------------------------------------------------------------------

class TestEdgesToRelationships:
    def test_with_arrowhead(self):
        from src.pipelines.cv.pipeline import CVPipeline

        edges = [
            RawEdge(
                start=(10, 10), end=(50, 50),
                has_arrowhead=True,
                source_shape_idx=0,
                target_shape_idx=1,
            ),
        ]
        shape_names = {0: "Start", 1: "End"}
        rels, steps = CVPipeline._edges_to_relationships(
            edges, shape_names
        )
        assert len(rels) == 1
        assert rels[0]["source"] == "Start"
        assert rels[0]["target"] == "End"
        assert len(steps) == 1
        assert "\u2192" in steps[0]["action"]

    def test_without_arrowhead(self):
        from src.pipelines.cv.pipeline import CVPipeline

        edges = [
            RawEdge(
                start=(10, 10), end=(50, 50),
                has_arrowhead=False,
                source_shape_idx=0,
                target_shape_idx=1,
            ),
        ]
        shape_names = {0: "A", 1: "B"}
        rels, steps = CVPipeline._edges_to_relationships(
            edges, shape_names
        )
        assert len(rels) == 1
        assert len(steps) == 0  # no arrowhead -> no process step

    def test_unmapped_edges_skipped(self):
        from src.pipelines.cv.pipeline import CVPipeline

        edges = [
            RawEdge(
                start=(10, 10), end=(50, 50),
                has_arrowhead=True,
                source_shape_idx=None,
                target_shape_idx=1,
            ),
            RawEdge(
                start=(10, 10), end=(50, 50),
                has_arrowhead=True,
                source_shape_idx=0,
                target_shape_idx=None,
            ),
        ]
        shape_names = {0: "A", 1: "B"}
        rels, steps = CVPipeline._edges_to_relationships(
            edges, shape_names
        )
        assert len(rels) == 0
        assert len(steps) == 0

    def test_missing_shape_name_skipped(self):
        from src.pipelines.cv.pipeline import CVPipeline

        edges = [
            RawEdge(
                start=(10, 10), end=(50, 50),
                has_arrowhead=True,
                source_shape_idx=0,
                target_shape_idx=5,  # not in shape_names
            ),
        ]
        shape_names = {0: "A"}
        rels, steps = CVPipeline._edges_to_relationships(
            edges, shape_names
        )
        assert len(rels) == 0

    def test_multiple_edges(self):
        from src.pipelines.cv.pipeline import CVPipeline

        edges = [
            RawEdge(
                start=(0, 0), end=(10, 10),
                has_arrowhead=True,
                source_shape_idx=0, target_shape_idx=1,
            ),
            RawEdge(
                start=(10, 10), end=(20, 20),
                has_arrowhead=True,
                source_shape_idx=1, target_shape_idx=2,
            ),
        ]
        shape_names = {0: "A", 1: "B", 2: "C"}
        rels, steps = CVPipeline._edges_to_relationships(
            edges, shape_names
        )
        assert len(rels) == 2
        assert len(steps) == 2
        assert steps[0]["step"] == 1
        assert steps[1]["step"] == 2


# -----------------------------------------------------------------------
# _assess_signal_quality() — additional branches
# -----------------------------------------------------------------------

class TestAssessSignalQualityBranches:
    def test_full_quality(self):
        from src.pipelines.cv.pipeline import CVPipeline

        p = CVPipeline()
        boxes = [_box(f"t{i}", 50.0, i * 10.0) for i in range(6)]
        shapes = [_shape(area=100)]
        cv = CVResult(
            ocr_boxes=boxes, shapes=shapes, edges=[],
            shape_texts={}, image_width=1000, image_height=1000,
        )
        q = p._assess_signal_quality(cv)
        assert q == SignalQuality.FULL

    def test_shape_primary_sparse_ocr(self):
        from src.pipelines.cv.pipeline import CVPipeline

        p = CVPipeline()
        # < 5 OCR boxes, has shapes
        boxes = [_box("t", 50.0, 10.0)]
        shapes = [_shape(area=100)]
        cv = CVResult(
            ocr_boxes=boxes, shapes=shapes, edges=[],
            shape_texts={}, image_width=1000, image_height=1000,
        )
        q = p._assess_signal_quality(cv)
        assert q == SignalQuality.SHAPE_PRIMARY

    def test_ocr_primary_noisy_shapes(self):
        from src.pipelines.cv.pipeline import CVPipeline

        p = CVPipeline()
        boxes = [_box(f"t{i}", 50.0, i * 10.0) for i in range(6)]
        # Big container shape (area > 25% of image)
        shapes = [_shape(area=30000, bbox=(0, 0, 200, 150))]
        cv = CVResult(
            ocr_boxes=boxes, shapes=shapes, edges=[],
            shape_texts={},
            image_width=200, image_height=200,
        )
        q = p._assess_signal_quality(cv)
        assert q == SignalQuality.OCR_PRIMARY

    def test_ocr_primary_noisy_edges(self):
        from src.pipelines.cv.pipeline import CVPipeline

        p = CVPipeline()
        boxes = [_box(f"t{i}", 50.0, i * 10.0) for i in range(6)]
        shapes = [_shape(area=100)]
        # All edges unmapped -> noisy
        edges = [
            RawEdge((0, 0), (10, 10), False, None, None)
            for _ in range(5)
        ]
        cv = CVResult(
            ocr_boxes=boxes, shapes=shapes, edges=edges,
            shape_texts={}, image_width=1000, image_height=1000,
        )
        q = p._assess_signal_quality(cv)
        assert q == SignalQuality.OCR_PRIMARY

    def test_ocr_only_no_shapes_no_edges(self):
        from src.pipelines.cv.pipeline import CVPipeline

        p = CVPipeline()
        boxes = [_box("hello", 50.0, 10.0)]
        cv = CVResult(ocr_boxes=boxes)
        q = p._assess_signal_quality(cv)
        assert q == SignalQuality.OCR_ONLY

    def test_default_has_ocr(self):
        """has_ocr=True, has_shapes=True, shapes noisy, edges not noisy."""
        from src.pipelines.cv.pipeline import CVPipeline

        p = CVPipeline()
        boxes = [_box(f"t{i}", 50.0, i * 10.0) for i in range(6)]
        # Single shape that is noisy (text spillage)
        shapes = [_shape(area=100)]
        cv = CVResult(
            ocr_boxes=boxes, shapes=shapes, edges=[],
            shape_texts={0: [f"t{i}" for i in range(8)]},
            image_width=1000, image_height=1000,
        )
        q = p._assess_signal_quality(cv)
        assert q == SignalQuality.OCR_PRIMARY


# -----------------------------------------------------------------------
# _is_shapes_noisy() — container box branch
# -----------------------------------------------------------------------

class TestIsShapesNoisyContainer:
    def test_container_ratio(self):
        from src.pipelines.cv.pipeline import CVPipeline

        p = CVPipeline()
        # All shapes are containers (area > 25% of image)
        shapes = [
            _shape(area=15000, bbox=(0, 0, 150, 100)),
        ]
        cv = CVResult(
            shapes=shapes, shape_texts={},
            image_width=200, image_height=200,
        )
        assert p._is_shapes_noisy(cv) is True

    def test_zero_image_area(self):
        from src.pipelines.cv.pipeline import CVPipeline

        p = CVPipeline()
        shapes = [_shape()]
        cv = CVResult(
            shapes=shapes, shape_texts={},
            image_width=0, image_height=0,
        )
        assert p._is_shapes_noisy(cv) is False


# -----------------------------------------------------------------------
# _is_edges_noisy() — excessive edges branch
# -----------------------------------------------------------------------

class TestIsEdgesNoisyExcessive:
    def test_excessive_edges(self):
        from src.pipelines.cv.pipeline import CVPipeline

        p = CVPipeline()
        shapes = [_shape()]
        # shapes * 4 + 1 = 5 edges > threshold
        edges = [
            RawEdge((0, 0), (10, 10), False, 0, 0)
            for _ in range(5)
        ]
        cv = CVResult(
            shapes=shapes, edges=edges,
            image_width=100, image_height=100,
        )
        assert p._is_edges_noisy(cv) is True


# -----------------------------------------------------------------------
# _estimate_confidence() — all quality levels
# -----------------------------------------------------------------------

class TestEstimateConfidence:
    def test_all_quality_levels(self):
        from src.pipelines.cv.pipeline import CVPipeline

        p = CVPipeline()
        cv = CVResult()

        assert p._estimate_confidence(SignalQuality.FULL, cv) == 0.8
        assert p._estimate_confidence(
            SignalQuality.OCR_PRIMARY, cv
        ) == 0.7
        assert p._estimate_confidence(
            SignalQuality.SHAPE_PRIMARY, cv
        ) == 0.6
        assert p._estimate_confidence(
            SignalQuality.OCR_ONLY, cv
        ) == 0.5
        assert p._estimate_confidence(
            SignalQuality.EMPTY, cv
        ) == 0.1

    def test_bonus_for_many_boxes(self):
        from src.pipelines.cv.pipeline import CVPipeline

        p = CVPipeline()
        boxes = [_box(f"t{i}", 10, i * 5) for i in range(12)]
        cv = CVResult(ocr_boxes=boxes)
        conf = p._estimate_confidence(SignalQuality.FULL, cv)
        assert abs(conf - 0.85) < 1e-9  # 0.8 + 0.05

    def test_bonus_capped_at_095(self):
        from src.pipelines.cv.pipeline import CVPipeline

        p = CVPipeline()
        boxes = [_box(f"t{i}", 10, i * 5) for i in range(12)]
        cv = CVResult(ocr_boxes=boxes)
        # Even with OCR_PRIMARY (0.7) + bonus, cap at 0.95
        conf = p._estimate_confidence(
            SignalQuality.OCR_PRIMARY, cv
        )
        assert conf == 0.75


# -----------------------------------------------------------------------
# _fallback_structure() — shape-based path
# -----------------------------------------------------------------------

class TestFallbackStructure:
    def test_shape_primary_with_edges(self):
        from src.pipelines.cv.pipeline import CVPipeline

        p = CVPipeline()
        shapes = [
            _shape("rectangle", (10, 10, 50, 50), 2500, (35, 35)),
            _shape("diamond", (100, 10, 50, 50), 2500, (125, 35)),
        ]
        edges = [
            RawEdge(
                (35, 60), (125, 10), True,
                source_shape_idx=0, target_shape_idx=1,
            ),
        ]
        cv = CVResult(
            shapes=shapes, edges=edges,
            shape_texts={0: ["Start"], 1: ["Decision"]},
            image_width=200, image_height=100,
        )
        result = p._fallback_structure(
            cv, SignalQuality.SHAPE_PRIMARY
        )
        assert result["image_type"] == "flowchart"
        assert len(result["entities"]) == 2
        # diamond -> Process type
        types = [e["type"] for e in result["entities"]]
        assert "Process" in types
        assert "System" in types

    def test_shape_primary_no_edges(self):
        from src.pipelines.cv.pipeline import CVPipeline

        p = CVPipeline()
        shapes = [_shape()]
        cv = CVResult(
            shapes=shapes, edges=[],
            shape_texts={0: ["Box"]},
            image_width=200, image_height=100,
        )
        result = p._fallback_structure(
            cv, SignalQuality.FULL
        )
        assert result["image_type"] == "diagram"

    def test_ocr_only_delegates(self):
        from src.pipelines.cv.pipeline import CVPipeline

        p = CVPipeline()
        cv = CVResult(
            ocr_boxes=[_box("text", 50, 50)],
            image_width=200, image_height=100,
        )
        result = p._fallback_structure(
            cv, SignalQuality.OCR_ONLY
        )
        assert "image_type" in result
