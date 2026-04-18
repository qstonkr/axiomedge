"""Step 5: Graph normalization (local LLM via Ollama).

Serializes CV results to text and sends to local LLM for node/edge JSON refinement.

Key design:
  Since text-only LLMs cannot see images, OCR coordinates (text + x,y position)
  are passed as spatial layout for the LLM to understand the structure.

Quality-based prompt composition:
  FULL:          OCR coordinates + shapes + edges all included
  OCR_PRIMARY:   OCR coordinate layout only (noisy shapes/edges excluded)
  SHAPE_PRIMARY: Shape + edge centric (OCR supplementary)
"""

from __future__ import annotations

import json
import logging
import os
import re

from src.config.weights import weights as _w

from .models import CVResult, SignalQuality

logger = logging.getLogger(__name__)

# --- Prompt templates ---

# FULL: OCR + shapes + edges all included
_PROMPT_FULL = """Below is data extracted from an image using OCR and computer vision.
The (x,y) coordinates of each text indicate its position in the image (origin at top-left, x=right, y=down).
Texts at the same y-coordinate are on the same row, and x-coordinates determine left-to-right order.

## Image Size
{image_size}

## OCR Text (with coordinates)
{ocr_layout_block}

## Detected Shape Regions
{shapes_block}

## Detected Connection Lines
{edges_block}

## Request
Analyze the diagram structure based on the coordinate data above.
- Texts near the same y-coordinate belong to the same row/group
- Text inside a shape is that shape's label
- Arrows/connection lines indicate flow direction
- Symbols like +, -, =, -> indicate operations/flow relationships

Normalize to the following JSON format:
{{
  "image_type": "flowchart|architecture|data_flow|org_chart|table|formula|other",
  "description": "1-2 sentence description of what this diagram explains",
  "entities": [{{"name": "...", "type": "Person|System|Process|Concept"}}],
  "relationships": [{{"source": "...", "target": "...", "type": "USES|CALLS|DEPENDS_ON|CONNECTS_TO|PRODUCES|SUBTRACTS|ADDS", "label": "..."}}],
  "process_steps": [{{"step": 1, "action": "..."}}],
  "tags": ["..."]
}}

Output JSON only."""  # noqa: E501

# OCR_PRIMARY: OCR coordinate layout only (no shapes/edges)
_PROMPT_OCR_PRIMARY = """Below is text and coordinate data extracted from an image using OCR.
The (x,y) coordinates of each text indicate its position in the image (origin at top-left).
Texts at the same y-coordinate are on the same row, and x-coordinates determine left-to-right order.

## Image Size
{image_size}

## OCR Text (with coordinates)
{ocr_layout_block}

## Request
Analyze the diagram/document structure based on the coordinate layout above.
- Texts near the same y-coordinate belong to the same row/group
- Indentation (increasing x-coordinate) indicates hierarchical relationships
- Symbols like ->, -, +, = indicate flow/operation relationships
- Large text (headings) and small text (content) can be distinguished by y-coordinate gaps

Normalize to the following JSON format:
{{
  "image_type": "flowchart|architecture|data_flow|org_chart|table|formula|other",
  "description": "1-2 sentence description of what this diagram explains",
  "entities": [{{"name": "...", "type": "Person|System|Process|Concept"}}],
  "relationships": [{{"source": "...", "target": "...", "type": "USES|CALLS|DEPENDS_ON|CONNECTS_TO|PRODUCES|SUBTRACTS|ADDS", "label": "..."}}],
  "process_steps": [{{"step": 1, "action": "..."}}],
  "tags": ["..."]
}}

Output JSON only."""  # noqa: E501

# SHAPE_PRIMARY: Shape/edge centric
_PROMPT_SHAPE_PRIMARY = """Below is shape and connection line data extracted from an image using computer vision.

## Image Size
{image_size}

## Detected Shape Regions
{shapes_block}

## Detected Connection Lines
{edges_block}

## Supplementary Text
{supplementary_text}

## Request
Analyze the diagram structure based on the shape and connection line data above.
- Shape labels represent entities (nodes)
- Arrow connections indicate flow direction
- Shape types (rectangle, diamond, circle) help infer roles

Normalize to the following JSON format:
{{
  "image_type": "flowchart|architecture|data_flow|org_chart|table|formula|other",
  "description": "1-2 sentence description of what this diagram explains",
  "entities": [{{"name": "...", "type": "Person|System|Process|Concept"}}],
  "relationships": [{{"source": "...", "target": "...", "type": "USES|CALLS|DEPENDS_ON|CONNECTS_TO|PRODUCES|SUBTRACTS|ADDS", "label": "..."}}],
  "process_steps": [{{"step": 1, "action": "..."}}],
  "tags": ["..."]
}}

Output JSON only."""  # noqa: E501


class GraphNormalizer:
    """Normalize CV results using local LLM (Ollama)."""

    async def normalize(
        self,
        cv_result: CVResult,
        quality: SignalQuality = SignalQuality.FULL,
    ) -> dict:
        """CV result -> local LLM -> normalized graph.

        Args:
            cv_result: CV pipeline intermediate result
            quality: Data quality classification (affects prompt composition)
        """
        prompt = self._build_prompt(cv_result, quality)

        response_text = await self._call_llm(prompt)
        return self._parse_response(response_text)

    def _build_prompt(
        self, cv_result: CVResult, quality: SignalQuality
    ) -> str:
        """Generate appropriate prompt based on quality.

        FULL:          OCR + shapes + edges all included
        OCR_PRIMARY:   OCR coordinate layout only (noisy shapes/edges excluded)
        SHAPE_PRIMARY: Shapes + edges centric (OCR supplementary)
        """
        image_size = f"{cv_result.image_width} x {cv_result.image_height} px"

        if quality == SignalQuality.OCR_PRIMARY:
            return _PROMPT_OCR_PRIMARY.format(
                image_size=image_size,
                ocr_layout_block=self._build_ocr_layout(cv_result) or "(no text)",
            )

        if quality == SignalQuality.SHAPE_PRIMARY:
            # Supplementary text: brief OCR text if available
            supplementary = ""
            if cv_result.ocr_boxes:
                texts = [b.text for b in cv_result.ocr_boxes if b.text.strip()]
                supplementary = ", ".join(texts[:20]) if texts else "(none)"
            return _PROMPT_SHAPE_PRIMARY.format(
                image_size=image_size,
                shapes_block=self._build_shapes_block(cv_result) or "(no shapes)",
                edges_block=self._build_edges_block(cv_result) or "(no connections)",
                supplementary_text=supplementary or "(none)",
            )

        # FULL: all included
        return _PROMPT_FULL.format(
            image_size=image_size,
            ocr_layout_block=self._build_ocr_layout(cv_result) or "(no text)",
            shapes_block=self._build_shapes_block(cv_result) or "(no shapes)",
            edges_block=self._build_edges_block(cv_result) or "(no connections)",
        )

    @staticmethod
    def _sanitize_text(text: str) -> str:
        """OCR text sanitization -- prompt injection prevention."""
        # Remove patterns that could be interpreted as LLM instructions
        sanitized = text.replace("{{", "{").replace("}}", "}")
        # Remove control characters (except tab/newline)
        sanitized = "".join(
            c for c in sanitized if c in ("\t", "\n") or (ord(c) >= 32)
        )
        # Truncate to prevent excessive input
        return sanitized[:200]

    def _build_ocr_layout(self, cv_result: CVResult) -> str:
        """Group OCR text by y-coordinate into rows.

        Groups texts at the same y-coordinate (within 15px) into a single row
        so the LLM can understand spatial layout.
        """
        if not cv_result.ocr_boxes:
            return ""

        # Group by y-coordinate (15px units)
        y_groups: dict[int, list[tuple[float, str]]] = {}
        for box in cv_result.ocr_boxes:
            y_key = int(box.center[1] / 15) * 15
            if y_key not in y_groups:
                y_groups[y_key] = []
            y_groups[y_key].append((box.center[0], box.text))

        # Output rows in y-coordinate order
        lines = []
        for y_key in sorted(y_groups.keys()):
            items = sorted(y_groups[y_key], key=lambda x: x[0])  # left-to-right by x
            texts = [f"\"{self._sanitize_text(text)}\"(x={x:.0f})" for x, text in items]
            lines.append(f"y={y_key}: {' | '.join(texts)}")

        return "\n".join(lines)

    def _build_shapes_block(self, cv_result: CVResult) -> str:
        """Serialize shape information."""
        if not cv_result.shapes:
            return ""

        lines = []
        for idx, shape in enumerate(cv_result.shapes):
            texts = cv_result.shape_texts.get(idx, [])
            label = " / ".join(texts) if texts else "(no label)"
            x, y, w, h = shape.bbox
            lines.append(
                f"- #{idx} [{shape.shape_type}] "
                f"region({x},{y})~({x+w},{y+h}): {label}"
            )

        return "\n".join(lines)

    def _build_edges_block(self, cv_result: CVResult) -> str:
        """Serialize connection line info (with shape label mapping + dedup)."""
        if not cv_result.edges:
            return ""

        # Shape label mapping
        shape_labels: dict[int, str] = {}
        for idx, texts in cv_result.shape_texts.items():
            if idx < len(cv_result.shapes):
                shape_labels[idx] = " / ".join(texts)

        # Dedup (same source-target pair)
        seen: set[tuple[str, str]] = set()
        lines = []
        for edge in cv_result.edges:
            src = shape_labels.get(edge.source_shape_idx, "?") if edge.source_shape_idx is not None else "?"
            tgt = shape_labels.get(edge.target_shape_idx, "?") if edge.target_shape_idx is not None else "?"
            pair = (src, tgt)
            if pair in seen:
                continue
            seen.add(pair)

            direction = "\u2192" if edge.has_arrowhead else "\u2014"
            lines.append(f"- \"{src}\" {direction} \"{tgt}\"")

        return "\n".join(lines)

    async def _call_llm(self, prompt: str) -> str:
        """Call local LLM via Ollama for graph normalization."""
        import httpx

        base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").strip()
        from src.config import DEFAULT_LLM_MODEL
        model = os.getenv("KNOWLEDGE_VISION_MODEL", DEFAULT_LLM_MODEL)

        async with httpx.AsyncClient(timeout=_w.llm.graph_normalizer_timeout) as client:
            resp = await client.post(
                f"{base_url}/api/generate",
                json={
                    "model": model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {
                        "temperature": 0,
                        "num_predict": 4000,
                    },
                },
            )
            resp.raise_for_status()
            return resp.json().get("response", "")

    def _parse_response(self, response_text: str) -> dict:
        """Extract JSON from LLM response (with repair)."""
        json_str = response_text
        # Extract code block with regex
        json_block = re.search(r"```(?:json)?\s*\n?(.*?)```", response_text, re.DOTALL)
        if json_block:
            json_str = json_block.group(1)
        else:
            # Direct JSON response without code block -- extract { ... } range
            brace_match = re.search(r"\{.*\}", response_text, re.DOTALL)
            if brace_match:
                json_str = brace_match.group(0)

        json_str = json_str.strip()

        # First attempt: parse as-is
        try:
            parsed = json.loads(json_str)
            return self._extract_fields(parsed)
        except ValueError as e:
            logger.debug("Initial JSON parse failed, attempting repair: %s", e)

        # Second attempt: repair with json-repair library
        try:
            from json_repair import repair_json
            repaired = repair_json(json_str)
            parsed = json.loads(repaired)
            logger.info("JSON repaired successfully for graph normalization")
            return self._extract_fields(parsed)
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
            logger.warning("Failed to parse LLM response as JSON after repair: %s", exc)
            return {
                "image_type": "unknown",
                "description": response_text[:500] if response_text else "",
                "entities": [],
                "relationships": [],
                "process_steps": [],
                "tags": [],
            }

    @staticmethod
    def _extract_fields(parsed: dict) -> dict:
        """Extract required fields from parsed JSON."""
        return {
            "image_type": parsed.get("image_type", "unknown"),
            "description": parsed.get("description", ""),
            "entities": parsed.get("entities", []),
            "relationships": parsed.get("relationships", []),
            "process_steps": parsed.get("process_steps", []),
            "tags": parsed.get("tags", []),
        }

    @staticmethod
    def _empty_result() -> dict:
        return {
            "image_type": "unknown",
            "description": "",
            "entities": [],
            "relationships": [],
            "process_steps": [],
            "tags": [],
        }
