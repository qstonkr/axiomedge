"""CV Pipeline orchestrator.

6-step CV pipeline:
  Step 0: Input normalization (Pillow)
  Step 1: PaddleOCR coordinate OCR
  Step 2: Shape detection (OpenCV)
  Step 3: Arrow/line detection (OpenCV)
  Step 4: Text<->shape mapping
  Step 5: Graph normalization (local LLM via Ollama)

Quality-based case branching:
  FULL:          OCR + shapes both good -> full prompt
  OCR_PRIMARY:   Rich OCR + noisy shapes -> OCR coordinate layout only
  SHAPE_PRIMARY: Sparse OCR + good shapes -> shape centric
  OCR_ONLY:      No shapes/edges -> text only (no LLM needed)
  EMPTY:         No meaningful data
"""

from __future__ import annotations

import asyncio
import logging

from ..config.weights import weights as _w

from .arrow_detector import ArrowDetector
from .graph_normalizer import GraphNormalizer
from .models import CVResult, SignalQuality
from .ocr_with_coords import OCRWithCoords
from .preprocessor import ImagePreprocessor
from .shape_detector import ShapeDetector
from .text_shape_mapper import TextShapeMapper
from .visual_content_analyzer import VisualAnalysisResult

logger = logging.getLogger(__name__)

# Shape noise threshold constants
_SHAPE_TEXT_SPILLAGE_THRESHOLD = 6  # text mapped to a single shape > this = noise
_SHAPE_AREA_RATIO_THRESHOLD = 0.25  # shape area > 25% of image area = container
_UNMAPPED_EDGE_RATIO_THRESHOLD = 0.5  # >50% unmapped edges = noise
_MIN_OCR_BOXES_FOR_RICH = 5  # 5+ OCR boxes = "rich"

# Y-coordinate grouping constant (same value as graph_normalizer)
_Y_GROUP_SIZE = 15  # y-coordinate grouping unit (px)

# Vertical step detection constants
_VERTICAL_STEP_MIN_OCR = 6  # minimum OCR box count
_VERTICAL_STEP_LEFT_THRESHOLD_RATIO = 0.35  # left label area ratio
_VERTICAL_STEP_LEFT_MAX_LEN = 10  # max left label length
_VERTICAL_STEP_MERGE_GAP = 40  # label merge distance (px)
_VERTICAL_STEP_MIN_LABELS = 3  # minimum label count


def _ocr_in_process(image_bytes: bytes) -> list:
    """Run OCR in separate process (GIL bypass, top-level function).

    Must be module-level function for ProcessPoolExecutor(fork).
    fork COW shares PaddleOCR model without reloading.
    """
    return OCRWithCoords().extract(image_bytes)


def _detect_shapes_in_process(image_np_bytes: bytes, shape: tuple, ocr_boxes: list) -> list:
    """Run shape detection in separate process."""
    import numpy as np
    image_np = np.frombuffer(image_np_bytes, dtype=np.uint8).reshape(shape)
    return ShapeDetector().detect(image_np, ocr_boxes)


def _detect_arrows_in_process(image_np_bytes: bytes, shape: tuple, shapes: list) -> list:
    """Run arrow detection in separate process."""
    import numpy as np
    image_np = np.frombuffer(image_np_bytes, dtype=np.uint8).reshape(shape)
    return ArrowDetector().detect(image_np, shapes)


class CVPipeline:
    """Full CV pipeline orchestrator."""

    # Process-global ProcessPoolExecutor (fork COW usage)
    _process_pool = None
    _pool_lock = __import__("threading").Lock()

    @classmethod
    def _get_pool(cls):
        if cls._process_pool is None:
            with cls._pool_lock:
                if cls._process_pool is None:
                    import multiprocessing as mp
                    from concurrent.futures import ProcessPoolExecutor
                    ctx = mp.get_context("fork")
                    cls._process_pool = ProcessPoolExecutor(
                        max_workers=_w.ocr.cv_max_workers, mp_context=ctx
                    )
        return cls._process_pool

    def __init__(self) -> None:
        self.preprocessor = ImagePreprocessor()
        self.ocr = OCRWithCoords()
        self.shape_detector = ShapeDetector()
        self.arrow_detector = ArrowDetector()
        self.mapper = TextShapeMapper()
        self.normalizer = GraphNormalizer()

    async def analyze(self, image_bytes: bytes) -> VisualAnalysisResult:
        """Execute 6-step CV pipeline.

        Args:
            image_bytes: Raw image bytes

        Returns:
            VisualAnalysisResult (maintains existing interface)
        """
        # Input validation
        if not image_bytes or len(image_bytes) < 100:
            logger.warning("Empty or too small image input (%d bytes)", len(image_bytes) if image_bytes else 0)
            return VisualAnalysisResult(confidence=0.0)

        # Step 0: Input normalization (lightweight, thread is sufficient)
        image_np, _pil_image = await asyncio.to_thread(
            self.preprocessor.normalize, image_bytes
        )

        # Step 1-3: CPU-bound CV processing in separate processes (GIL bypass)
        loop = asyncio.get_running_loop()
        pool = self._get_pool()

        # Step 1: PaddleOCR (separate process)
        try:
            ocr_boxes = await loop.run_in_executor(
                pool, _ocr_in_process, image_bytes
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("OCR process failed (resetting pool): %s", exc)
            # SIGSEGV -> BrokenProcessPool: recreate pool
            with CVPipeline._pool_lock:
                CVPipeline._process_pool = None
            ocr_boxes = []

        # Serialize numpy to bytes (for inter-process transfer)
        image_np_bytes = image_np.tobytes()
        image_shape = image_np.shape

        # Step 2: Shape detection (separate process)
        try:
            shapes = await loop.run_in_executor(
                pool, _detect_shapes_in_process,
                image_np_bytes, image_shape, ocr_boxes
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Shape detection failed: %s", exc)
            shapes = []

        # Step 3: Arrow/line detection (separate process)
        try:
            edges = await loop.run_in_executor(
                pool, _detect_arrows_in_process,
                image_np_bytes, image_shape, shapes
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Arrow detection failed: %s", exc)
            edges = []

        # Step 4: Text<->shape mapping
        if shapes and ocr_boxes:
            shape_texts, unmapped = self.mapper.map(ocr_boxes, shapes)
        else:
            shape_texts = {}
            unmapped = [b.text for b in ocr_boxes if b.text.strip()]

        cv_result = CVResult(
            ocr_boxes=ocr_boxes,
            shapes=shapes,
            edges=edges,
            shape_texts=shape_texts,
            unmapped_texts=unmapped,
            image_width=image_np.shape[1],
            image_height=image_np.shape[0],
        )

        # Step 5: Quality assessment + case-based branching
        quality = self._assess_signal_quality(cv_result)
        logger.info(
            "CV signal quality: %s (ocr=%d, shapes=%d, edges=%d)",
            quality.value,
            len(ocr_boxes),
            len(shapes),
            len(edges),
        )

        graph_data = await self._process_by_quality(cv_result, quality)

        confidence = self._estimate_confidence(quality, cv_result)

        return VisualAnalysisResult(
            image_type=graph_data.get("image_type", "unknown"),
            raw_text=" ".join(b.text for b in ocr_boxes),
            description=graph_data.get("description", ""),
            entities=graph_data.get("entities", []),
            relationships=graph_data.get("relationships", []),
            process_steps=graph_data.get("process_steps", []),
            tags=graph_data.get("tags", []),
            confidence=confidence,
        )

    def _assess_signal_quality(self, cv_result: CVResult) -> SignalQuality:
        """Assess quality of each CV stage output to determine processing path.

        Criteria:
          1. OCR richness: text box count
          2. Shape noise: text spillage, container ratio
          3. Edge noise: unmapped ratio
        """
        has_ocr = len(cv_result.ocr_boxes) >= _MIN_OCR_BOXES_FOR_RICH
        has_shapes = bool(cv_result.shapes)
        has_edges = bool(cv_result.edges)

        # Case 5: nothing
        if not cv_result.ocr_boxes and not has_shapes:
            return SignalQuality.EMPTY

        # Case 4: OCR only, no shapes/edges
        if not has_shapes and not has_edges:
            return SignalQuality.OCR_ONLY

        # Shape noise assessment
        shapes_noisy = self._is_shapes_noisy(cv_result)

        # Edge noise assessment
        edges_noisy = self._is_edges_noisy(cv_result)

        # Case 3: Sparse OCR + good shapes
        if not has_ocr and has_shapes and not shapes_noisy:
            return SignalQuality.SHAPE_PRIMARY

        # Case 1: Rich OCR + noisy shapes/edges
        if has_ocr and (shapes_noisy or edges_noisy):
            return SignalQuality.OCR_PRIMARY

        # Case 2: OCR + shapes both good
        if has_ocr and has_shapes and not shapes_noisy:
            return SignalQuality.FULL

        # Default: if OCR exists, prioritize OCR
        if has_ocr:
            return SignalQuality.OCR_PRIMARY

        return SignalQuality.SHAPE_PRIMARY

    def _is_shapes_noisy(self, cv_result: CVResult) -> bool:
        """Determine if shape detection results are noisy.

        Noise conditions:
          - Excessive text mapped to a single shape (spillage)
          - Shape occupies >25% of image area (container box)
        """
        if not cv_result.shapes:
            return False

        image_area = cv_result.image_width * cv_result.image_height
        if image_area == 0:
            return False

        # Text spillage: too many texts mapped to one shape
        for texts in cv_result.shape_texts.values():
            if len(texts) > _SHAPE_TEXT_SPILLAGE_THRESHOLD:
                return True

        # Container box: shape area > 25% of image
        container_count = sum(
            1
            for shape in cv_result.shapes
            if shape.area / image_area > _SHAPE_AREA_RATIO_THRESHOLD
        )
        if container_count > 0 and container_count >= len(cv_result.shapes) * 0.5:
            return True

        return False

    def _is_edges_noisy(self, cv_result: CVResult) -> bool:
        """Determine if edge detection results are noisy.

        Noise conditions:
          - >50% of edges are unmapped to shapes
          - Excessive edges relative to shape count (shapes * 4)
        """
        if not cv_result.edges:
            return False

        # Unmapped ratio
        unmapped_count = sum(
            1
            for edge in cv_result.edges
            if edge.source_shape_idx is None or edge.target_shape_idx is None
        )
        if len(cv_result.edges) > 0:
            unmapped_ratio = unmapped_count / len(cv_result.edges)
            if unmapped_ratio > _UNMAPPED_EDGE_RATIO_THRESHOLD:
                return True

        # Excessive edges relative to shapes
        if cv_result.shapes and len(cv_result.edges) > len(cv_result.shapes) * 4:
            return True

        return False

    async def _process_by_quality(
        self, cv_result: CVResult, quality: SignalQuality
    ) -> dict:
        """Select appropriate processing path based on quality."""
        if quality == SignalQuality.EMPTY:
            return self._empty_result()

        if quality == SignalQuality.OCR_ONLY:
            # Structure from OCR text only (no LLM needed)
            return self._ocr_only_structure(cv_result)

        # Cases requiring LLM call
        try:
            return await self.normalizer.normalize(cv_result, quality)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Graph normalization failed: %s", exc)

        # Fallback on failure
        return self._fallback_structure(cv_result, quality)

    def _ocr_only_structure(self, cv_result: CVResult) -> dict:
        """Structure from OCR text only (images without shapes/edges).

        Detection patterns (priority):
          1. Arrow symbols (-->, =>) -> horizontal flowchart
          2. Vertical step pattern (left labels + right descriptions) -> vertical process
          3. Formula symbols (+, -, =) -> formula
          4. Multi-column structure -> table
          5. Default -> text_image
        """
        if not cv_result.ocr_boxes:
            return self._empty_result()

        # Group by y-coordinate into rows
        y_groups: dict[int, list[tuple[float, str]]] = {}
        for box in cv_result.ocr_boxes:
            y_key = int(box.center[1] / _Y_GROUP_SIZE) * _Y_GROUP_SIZE
            if y_key not in y_groups:
                y_groups[y_key] = []
            y_groups[y_key].append((box.center[0], box.text))

        # Build row texts for structure inference
        rows = []
        for y_key in sorted(y_groups.keys()):
            items = sorted(y_groups[y_key], key=lambda x: x[0])
            row_text = " ".join(text for _, text in items)
            rows.append(row_text)

        # Pattern 1: Extract process steps from arrow/symbol patterns
        process_steps = []
        step_num = 1
        for row in rows:
            if any(sym in row for sym in ("\u2192", "->", "\u21d2", "\u25b6")):
                process_steps.append({"step": step_num, "action": row})
                step_num += 1

        # Pattern 2: Vertical step pattern (when no arrows)
        if not process_steps:
            process_steps = self._detect_vertical_steps(cv_result)

        # Infer image type
        all_text = " ".join(rows)
        image_type = "text_image"
        if process_steps:
            image_type = "flowchart"
        elif any(sym in all_text for sym in ("+", "=", "\u00d7", "\u00f7")):
            image_type = "formula"
        elif len(y_groups) > 3 and all(
            len(items) > 1 for items in y_groups.values()
        ):
            image_type = "table"

        return {
            "image_type": image_type,
            "description": "",
            "entities": [],
            "relationships": [],
            "process_steps": process_steps,
            "tags": [],
        }

    @staticmethod
    def _classify_left_right_items(
        ocr_boxes, left_threshold: float,
    ) -> tuple[list[tuple[float, str]], list[tuple[float, str]]]:
        """Split OCR boxes into left labels and right descriptions by x-position."""
        left_items: list[tuple[float, str]] = []
        right_items: list[tuple[float, str]] = []
        for box in ocr_boxes:
            text = box.text.strip()
            if not text:
                continue
            if box.center[0] < left_threshold and len(text) <= _VERTICAL_STEP_LEFT_MAX_LEN:
                left_items.append((box.center[1], text))
            elif box.center[0] >= left_threshold:
                right_items.append((box.center[1], text))
        return left_items, right_items

    @staticmethod
    def _merge_consecutive_labels(
        left_items: list[tuple[float, str]],
    ) -> list[tuple[float, float, str]]:
        """Merge vertically adjacent left labels into groups."""
        left_items.sort(key=lambda x: x[0])
        merged: list[tuple[float, float, str]] = []
        for y, text in left_items:
            if merged and y - merged[-1][1] < _VERTICAL_STEP_MERGE_GAP:
                prev_start, _, prev_text = merged[-1]
                merged[-1] = (prev_start, y, f"{prev_text} {text}")
            else:
                merged.append((y, y, text))
        return merged

    @staticmethod
    def _map_labels_to_descriptions(
        merged_labels: list[tuple[float, float, str]],
        right_items: list[tuple[float, str]],
    ) -> list[dict]:
        """Map right descriptions to merged labels by y-range proximity."""
        right_items.sort(key=lambda x: x[0])
        consumed_right: set[int] = set()
        steps = []

        for i, (y_start, y_end, label) in enumerate(merged_labels):
            search_end = (
                merged_labels[i + 1][0] - 10 if i + 1 < len(merged_labels) else y_end + 60
            )
            search_start = y_start - 20
            detail_texts: list[str] = []
            for j, (ry, rtext) in enumerate(right_items):
                if j in consumed_right:
                    continue
                if search_start <= ry <= search_end:
                    detail_texts.append(rtext)
                    consumed_right.add(j)
            detail = " / ".join(detail_texts) if detail_texts else ""
            action = f"{label}: {detail}" if detail else label
            steps.append({"step": i + 1, "action": action})

        return steps

    def _detect_vertical_steps(self, cv_result: CVResult) -> list[dict]:
        """Detect vertical step pattern (left label column + right description column)."""
        if len(cv_result.ocr_boxes) < _VERTICAL_STEP_MIN_OCR:
            return []
        if cv_result.image_width == 0:
            return []

        left_threshold = cv_result.image_width * _VERTICAL_STEP_LEFT_THRESHOLD_RATIO
        left_items, right_items = self._classify_left_right_items(cv_result.ocr_boxes, left_threshold)

        if len(left_items) < _VERTICAL_STEP_MIN_LABELS:
            return []

        merged_labels = self._merge_consecutive_labels(left_items)
        if len(merged_labels) < _VERTICAL_STEP_MIN_LABELS:
            return []

        return self._map_labels_to_descriptions(merged_labels, right_items)

    def _fallback_structure(
        self, cv_result: CVResult, quality: SignalQuality
    ) -> dict:
        """Structure from CV results only without LLM.

        Uses shape-based or OCR-based structuring depending on quality.
        """
        if quality in (SignalQuality.OCR_PRIMARY, SignalQuality.OCR_ONLY):
            return self._ocr_only_structure(cv_result)

        # FULL or SHAPE_PRIMARY: shape-based structuring
        entities = []
        shape_names: dict[int, str] = {}
        for idx, texts in cv_result.shape_texts.items():
            if idx < len(cv_result.shapes):
                shape = cv_result.shapes[idx]
                name = " ".join(texts)
                shape_names[idx] = name
                entities.append({
                    "name": name,
                    "type": "Process" if shape.shape_type == "diamond" else "System",
                })

        # edge -> shape mapping for relationships + process_steps
        relationships, process_steps = self._edges_to_relationships(cv_result.edges, shape_names)

        if process_steps:
            image_type = "flowchart"
        elif cv_result.shapes:
            image_type = "diagram"
        else:
            image_type = "text_image"

        return {
            "image_type": image_type,
            "description": "",
            "entities": entities,
            "relationships": relationships,
            "process_steps": process_steps,
            "tags": [],
        }

    @staticmethod
    def _edges_to_relationships(
        edges, shape_names: dict[int, str],
    ) -> tuple[list[dict], list[dict]]:
        """Convert edges to relationship and process step lists."""
        relationships = []
        process_steps = []
        step_counter = 1
        for edge in edges:
            src = edge.source_shape_idx
            tgt = edge.target_shape_idx
            if src is not None and tgt is not None and src in shape_names and tgt in shape_names:
                relationships.append({
                    "source": shape_names[src],
                    "target": shape_names[tgt],
                    "type": "CONNECTS_TO",
                    "label": "",
                })
                if edge.has_arrowhead:
                    process_steps.append({
                        "step": step_counter,
                        "action": f"{shape_names[src]} \u2192 {shape_names[tgt]}",
                    })
                    step_counter += 1
        return relationships, process_steps

    def _estimate_confidence(
        self, quality: SignalQuality, cv_result: CVResult
    ) -> float:
        """Estimate confidence from quality + data richness."""
        base = {
            SignalQuality.FULL: 0.8,
            SignalQuality.OCR_PRIMARY: 0.7,
            SignalQuality.SHAPE_PRIMARY: 0.6,
            SignalQuality.OCR_ONLY: 0.5,
            SignalQuality.EMPTY: 0.1,
        }
        score = base.get(quality, 0.4)

        # Bonus for many OCR texts
        if len(cv_result.ocr_boxes) > 10:
            score = min(score + 0.05, 0.95)

        return score

    @staticmethod
    def _empty_result() -> dict:
        return _empty_graph_result()


def _empty_graph_result() -> dict:
    """SSOT -- empty graph result template."""
    return {
        "image_type": "unknown",
        "description": "",
        "entities": [],
        "relationships": [],
        "process_steps": [],
        "tags": [],
    }
