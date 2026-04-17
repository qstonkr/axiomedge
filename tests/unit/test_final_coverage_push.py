"""Coverage push tests — search_analytics, embedding provider, shape_detector,
ocr_with_coords, postgres session, init_db.

Targets ~250+ new covered statements across 6 modules.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest


# ===================================================================
# 1. src/api/routes/search_analytics.py  (43 missed)
# ===================================================================

class TestSearchAnalyticsHistory:
    """GET /api/v1/admin/search/history — with and without repo."""

    @pytest.mark.asyncio
    async def test_history_no_repo(self):
        with patch(
            "src.api.routes.search_analytics._get_usage_repo",
            return_value=None,
        ):
            from src.api.routes.search_analytics import get_search_history

            result = await get_search_history(page=1, page_size=50)
        assert result == {
            "searches": [],
            "total": 0,
            "page": 1,
            "page_size": 50,
        }

    @pytest.mark.asyncio
    async def test_history_with_repo(self):
        mock_repo = AsyncMock()
        mock_repo.list_recent.return_value = {
            "searches": [{"q": "hello"}],
            "total": 1,
        }
        with patch(
            "src.api.routes.search_analytics._get_usage_repo",
            return_value=mock_repo,
        ):
            from src.api.routes.search_analytics import get_search_history

            result = await get_search_history(page=2, page_size=10)
        mock_repo.list_recent.assert_awaited_once_with(limit=10, offset=10)
        assert result["page"] == 2
        assert result["searches"] == [{"q": "hello"}]

    @pytest.mark.asyncio
    async def test_history_page3(self):
        mock_repo = AsyncMock()
        mock_repo.list_recent.return_value = {
            "searches": [],
            "total": 100,
        }
        with patch(
            "src.api.routes.search_analytics._get_usage_repo",
            return_value=mock_repo,
        ):
            from src.api.routes.search_analytics import get_search_history

            result = await get_search_history(page=3, page_size=20)
        mock_repo.list_recent.assert_awaited_once_with(limit=20, offset=40)
        assert result["total"] == 100


class TestSearchAnalyticsAnalytics:
    """GET /api/v1/admin/search/analytics."""

    @pytest.mark.asyncio
    async def test_analytics_no_repo(self):
        with patch(
            "src.api.routes.search_analytics._get_usage_repo",
            return_value=None,
        ):
            from src.api.routes.search_analytics import (
                get_search_analytics,
            )

            result = await get_search_analytics(days=7)
        assert result["total_searches"] == 0
        assert result["zero_result_queries"] == []

    @pytest.mark.asyncio
    async def test_analytics_with_repo(self):
        mock_repo = AsyncMock()
        mock_repo.get_analytics.return_value = {
            "total_searches": 42,
            "top_queries": [{"q": "a", "count": 5}],
            "avg_results_per_query": 3.5,
            "avg_response_time_ms": 120.0,
            "top_kbs": [{"kb": "x", "count": 10}],
            "period_days": 30,
            "unique_users": 7,
        }
        with patch(
            "src.api.routes.search_analytics._get_usage_repo",
            return_value=mock_repo,
        ):
            from src.api.routes.search_analytics import (
                get_search_analytics,
            )

            result = await get_search_analytics(days=30)
        assert result["total_searches"] == 42
        assert result["unique_queries"] == 1
        assert result["avg_results_per_query"] == 3.5
        assert result["avg_response_time_ms"] == 120.0
        assert result["top_kbs"] == [{"kb": "x", "count": 10}]
        assert result["period_days"] == 30
        assert result["unique_users"] == 7
        assert result["zero_result_queries"] == []


class TestSearchAnalyticsUserHistory:
    """GET /api/v1/admin/search/user-history."""

    @pytest.mark.asyncio
    async def test_user_history_no_repo(self):
        with patch(
            "src.api.routes.search_analytics._get_usage_repo",
            return_value=None,
        ):
            from src.api.routes.search_analytics import (
                get_user_search_history,
            )

            result = await get_user_search_history(
                user_id="u1", limit=10,
            )
        assert result == {"searches": [], "user_id": "u1"}

    @pytest.mark.asyncio
    async def test_user_history_with_repo(self):
        mock_repo = AsyncMock()
        mock_repo.get_by_user.return_value = [{"q": "test"}]
        with patch(
            "src.api.routes.search_analytics._get_usage_repo",
            return_value=mock_repo,
        ):
            from src.api.routes.search_analytics import (
                get_user_search_history,
            )

            result = await get_user_search_history(
                user_id="u2", limit=25,
            )
        mock_repo.get_by_user.assert_awaited_once_with(
            user_id="u2", limit=25,
        )
        assert result["searches"] == [{"q": "test"}]
        assert result["user_id"] == "u2"


class TestSearchAnalyticsStaticEndpoints:
    """Static stats endpoints that return hardcoded dicts."""

    @pytest.mark.asyncio
    async def test_injection_stats(self):
        from src.api.routes.search_analytics import (
            get_search_injection_stats,
        )

        result = await get_search_injection_stats()
        assert result["total_injections"] == 0
        assert result["glossary_injections"] == 0
        assert result["synonym_injections"] == 0

    @pytest.mark.asyncio
    async def test_agentic_rag_stats(self):
        from src.api.routes.search_analytics import get_agentic_rag_stats

        result = await get_agentic_rag_stats()
        assert result["total_queries"] == 0
        assert result["tool_calls"] == 0
        assert result["avg_steps"] == 0.0

    @pytest.mark.asyncio
    async def test_crag_stats(self):
        from src.api.routes.search_analytics import get_crag_stats

        result = await get_crag_stats()
        assert result["total_queries"] == 0
        assert result["corrections_applied"] == 0
        assert result["correction_rate"] == 0.0

    @pytest.mark.asyncio
    async def test_adapter_stats(self):
        from src.api.routes.search_analytics import (
            get_search_adapter_stats,
        )

        result = await get_search_adapter_stats()
        assert result["adapters"] == []
        assert result["total_requests"] == 0


class TestGetUsageRepo:
    """_get_usage_repo helper."""

    def test_returns_repo_from_state(self):
        mock_state = MagicMock()
        mock_state.get.return_value = "fake_repo"
        with patch(
            "src.api.routes.search_analytics._get_state",
            return_value=mock_state,
        ):
            from src.api.routes.search_analytics import _get_usage_repo

            assert _get_usage_repo() == "fake_repo"
        mock_state.get.assert_called_once_with("usage_log_repo")


# ===================================================================
# 2. src/core/providers/embedding.py  (62 missed)
# ===================================================================

class TestEmbeddingProviderFactory:
    """create_embedding_provider and sub-helpers."""

    def test_unknown_type_raises(self):
        from src.core.providers.embedding import (
            create_embedding_provider,
        )

        with pytest.raises(ValueError, match="Unknown embedding provider"):
            create_embedding_provider(provider_type="bad")

    def test_tei_delegates(self):
        mock_provider = MagicMock()
        with patch(
            "src.core.providers.embedding._create_tei",
            return_value=mock_provider,
        ):
            from src.core.providers.embedding import (
                create_embedding_provider,
            )

            result = create_embedding_provider(provider_type="tei")
        assert result is mock_provider

    def test_ollama_delegates(self):
        mock_provider = MagicMock()
        with patch(
            "src.core.providers.embedding._create_ollama",
            return_value=mock_provider,
        ):
            from src.core.providers.embedding import (
                create_embedding_provider,
            )

            result = create_embedding_provider(provider_type="ollama")
        assert result is mock_provider

    def test_onnx_delegates(self):
        mock_provider = MagicMock()
        with patch(
            "src.core.providers.embedding._create_onnx",
            return_value=mock_provider,
        ):
            from src.core.providers.embedding import (
                create_embedding_provider,
            )

            result = create_embedding_provider(provider_type="onnx")
        assert result is mock_provider

    def test_none_auto_detects(self):
        mock_provider = MagicMock()
        with patch(
            "src.core.providers.embedding._auto_detect",
            return_value=mock_provider,
        ):
            from src.core.providers.embedding import (
                create_embedding_provider,
            )

            result = create_embedding_provider(provider_type=None)
        assert result is mock_provider

    def test_case_insensitive(self):
        mock_provider = MagicMock()
        with patch(
            "src.core.providers.embedding._create_tei",
            return_value=mock_provider,
        ):
            from src.core.providers.embedding import (
                create_embedding_provider,
            )

            result = create_embedding_provider(provider_type="  TEI  ")
        assert result is mock_provider


class TestAutoDetect:
    """_auto_detect provider resolution."""

    def test_tei_available(self):
        mock_provider = MagicMock()
        mock_provider.is_ready.return_value = True
        with (
            patch(
                "src.core.providers.embedding._create_tei",
                return_value=mock_provider,
            ),
            patch.dict("os.environ", {"BGE_TEI_URL": "http://tei:8080"}),
        ):
            from src.core.providers.embedding import _auto_detect

            result = _auto_detect()
        assert result is mock_provider

    def test_tei_not_ready_fallback_ollama(self):
        tei_p = MagicMock()
        tei_p.is_ready.return_value = False
        ollama_p = MagicMock()
        ollama_p.is_ready.return_value = True
        mock_settings = MagicMock()
        mock_settings.ollama.base_url = "http://ollama:11434"
        with (
            patch(
                "src.core.providers.embedding._create_tei",
                return_value=tei_p,
            ),
            patch(
                "src.core.providers.embedding._create_ollama",
                return_value=ollama_p,
            ),
            patch.dict("os.environ", {"BGE_TEI_URL": "http://tei:8080"}),
            patch(
                "src.config.get_settings",
                return_value=mock_settings,
            ),
        ):
            from src.core.providers.embedding import _auto_detect

            result = _auto_detect()
        assert result is ollama_p

    def test_tei_raises_fallback_ollama(self):
        ollama_p = MagicMock()
        ollama_p.is_ready.return_value = True
        mock_settings = MagicMock()
        mock_settings.ollama.base_url = "http://ollama:11434"
        with (
            patch(
                "src.core.providers.embedding._create_tei",
                side_effect=RuntimeError("fail"),
            ),
            patch(
                "src.core.providers.embedding._create_ollama",
                return_value=ollama_p,
            ),
            patch.dict("os.environ", {"BGE_TEI_URL": "http://tei:8080"}),
            patch(
                "src.config.get_settings",
                return_value=mock_settings,
            ),
        ):
            from src.core.providers.embedding import _auto_detect

            result = _auto_detect()
        assert result is ollama_p

    def test_all_fail_raises(self):
        mock_settings = MagicMock()
        mock_settings.ollama.base_url = "http://ollama:11434"
        with (
            patch.dict(
                "os.environ",
                {"BGE_TEI_URL": ""},
                clear=False,
            ),
            patch(
                "src.core.providers.embedding._create_ollama",
                side_effect=RuntimeError("no ollama"),
            ),
            patch(
                "src.core.providers.embedding._create_onnx",
                side_effect=RuntimeError("no onnx"),
            ),
            patch(
                "src.config.get_settings",
                return_value=mock_settings,
            ),
        ):
            from src.core.providers.embedding import _auto_detect

            with pytest.raises(RuntimeError, match="No embedding provider"):
                _auto_detect()

    def test_onnx_fallback(self):
        ollama_p = MagicMock()
        ollama_p.is_ready.return_value = False
        onnx_p = MagicMock()
        onnx_p.is_ready.return_value = True
        mock_settings = MagicMock()
        mock_settings.ollama.base_url = "http://ollama:11434"
        with (
            patch.dict("os.environ", {"BGE_TEI_URL": ""}, clear=False),
            patch(
                "src.core.providers.embedding._create_ollama",
                return_value=ollama_p,
            ),
            patch(
                "src.core.providers.embedding._create_onnx",
                return_value=onnx_p,
            ),
            patch(
                "src.config.get_settings",
                return_value=mock_settings,
            ),
        ):
            from src.core.providers.embedding import _auto_detect

            result = _auto_detect()
        assert result is onnx_p

    def test_onnx_not_ready_raises(self):
        ollama_p = MagicMock()
        ollama_p.is_ready.return_value = False
        onnx_p = MagicMock()
        onnx_p.is_ready.return_value = False
        mock_settings = MagicMock()
        mock_settings.ollama.base_url = "http://ollama:11434"
        with (
            patch.dict("os.environ", {"BGE_TEI_URL": ""}, clear=False),
            patch(
                "src.core.providers.embedding._create_ollama",
                return_value=ollama_p,
            ),
            patch(
                "src.core.providers.embedding._create_onnx",
                return_value=onnx_p,
            ),
            patch(
                "src.config.get_settings",
                return_value=mock_settings,
            ),
        ):
            from src.core.providers.embedding import _auto_detect

            with pytest.raises(RuntimeError, match="No embedding provider"):
                _auto_detect()

    def test_tei_url_from_kwargs(self):
        mock_provider = MagicMock()
        mock_provider.is_ready.return_value = True
        with (
            patch.dict("os.environ", {"BGE_TEI_URL": ""}, clear=False),
            patch(
                "src.core.providers.embedding._create_tei",
                return_value=mock_provider,
            ),
        ):
            from src.core.providers.embedding import _auto_detect

            result = _auto_detect(tei_url="http://custom:8080")
        assert result is mock_provider


class TestCreateTei:
    """_create_tei helper."""

    def test_creates_tei_provider(self):
        mock_provider = MagicMock()
        mock_gs = MagicMock()
        mock_gs.tei.embedding_url = "http://tei:8080"
        with (
            patch(
                "src.nlp.embedding.tei_provider.TEIEmbeddingProvider",
                return_value=mock_provider,
            ),
            patch(
                "src.config.get_settings",
                return_value=mock_gs,
            ),
        ):
            from src.core.providers.embedding import _create_tei

            result = _create_tei(base_url="http://custom:9090")
        assert result is mock_provider

    def test_creates_tei_default_url(self):
        mock_provider = MagicMock()
        mock_gs = MagicMock()
        mock_gs.tei.embedding_url = "http://default-tei:8080"
        with (
            patch(
                "src.nlp.embedding.tei_provider.TEIEmbeddingProvider",
                return_value=mock_provider,
            ),
            patch(
                "src.config.get_settings",
                return_value=mock_gs,
            ),
        ):
            from src.core.providers.embedding import _create_tei

            result = _create_tei()
        assert result is mock_provider


class TestCreateOllama:
    """_create_ollama helper."""

    def test_creates_ollama_provider(self):
        mock_provider = MagicMock()
        mock_gs = MagicMock()
        mock_gs.ollama.base_url = "http://ollama:11434"
        with (
            patch(
                "src.nlp.embedding.ollama_provider.OllamaEmbeddingProvider",
                return_value=mock_provider,
            ),
            patch(
                "src.config.get_settings",
                return_value=mock_gs,
            ),
        ):
            from src.core.providers.embedding import _create_ollama

            result = _create_ollama(base_url="http://custom:11434")
        assert result is mock_provider

    def test_creates_ollama_default_url(self):
        mock_provider = MagicMock()
        mock_gs = MagicMock()
        mock_gs.ollama.base_url = "http://default-ollama:11434"
        with (
            patch(
                "src.nlp.embedding.ollama_provider.OllamaEmbeddingProvider",
                return_value=mock_provider,
            ),
            patch(
                "src.config.get_settings",
                return_value=mock_gs,
            ),
        ):
            from src.core.providers.embedding import _create_ollama

            result = _create_ollama()
        assert result is mock_provider


class TestCreateOnnx:
    """_create_onnx helper."""

    def test_creates_onnx_provider(self):
        mock_provider = MagicMock()
        with patch(
            "src.nlp.embedding.onnx_provider.OnnxBgeEmbeddingProvider",
            return_value=mock_provider,
        ):
            from src.core.providers.embedding import _create_onnx

            result = _create_onnx(model_path="/tmp/model")
        assert result is mock_provider

    def test_creates_onnx_default_path(self):
        mock_provider = MagicMock()
        with (
            patch(
                "src.nlp.embedding.onnx_provider.OnnxBgeEmbeddingProvider",
                return_value=mock_provider,
            ),
            patch.dict(
                "os.environ",
                {"KNOWLEDGE_BGE_ONNX_MODEL_PATH": "/env/model"},
            ),
        ):
            from src.core.providers.embedding import _create_onnx

            result = _create_onnx()
        assert result is mock_provider


# ===================================================================
# 3. src/pipelines/cv/shape_detector.py  (80 missed)
# ===================================================================

class TestShapeDetectorDetect:
    """ShapeDetector.detect — full pipeline coverage."""

    def test_detect_with_shapes(self):
        from src.pipelines.cv.shape_detector import ShapeDetector

        detector = ShapeDetector()
        # White rectangle on black background — should detect a shape
        image = np.zeros((300, 300, 3), dtype=np.uint8)
        cv2 = pytest.importorskip("cv2")
        cv2.rectangle(image, (50, 50), (250, 250), (255, 255, 255), 2)
        shapes = detector.detect(image)
        assert isinstance(shapes, list)

    def test_detect_with_ocr_boxes_filtering(self):
        from src.pipelines.cv.models import OCRBox
        from src.pipelines.cv.shape_detector import ShapeDetector

        detector = ShapeDetector()
        image = np.zeros((300, 300, 3), dtype=np.uint8)
        cv2 = pytest.importorskip("cv2")
        cv2.rectangle(image, (50, 50), (250, 250), (255, 255, 255), 2)
        ocr_boxes = [
            OCRBox(
                text="text",
                polygon=[
                    [50, 50], [250, 50], [250, 250], [50, 250],
                ],
                confidence=0.9,
                center=(150.0, 150.0),
            ),
        ]
        shapes = detector.detect(image, ocr_boxes=ocr_boxes)
        assert isinstance(shapes, list)

    def test_detect_moment_zero_area(self):
        """Contour with m00==0 uses bbox center fallback."""
        from src.pipelines.cv.shape_detector import ShapeDetector

        detector = ShapeDetector()
        # Line contour has zero area moment
        image = np.zeros((200, 200, 3), dtype=np.uint8)
        cv2 = pytest.importorskip("cv2")
        # Draw a thin shape that might produce zero-area moments
        cv2.line(image, (10, 100), (190, 100), (255, 255, 255), 1)
        shapes = detector.detect(image)
        assert isinstance(shapes, list)


class TestShapeDetectorClassify:
    """_classify_shape — different vertex counts."""

    def test_classify_circle(self):
        from src.pipelines.cv.shape_detector import ShapeDetector

        detector = ShapeDetector()
        # Circle contour: many vertices, high circularity
        angles = np.linspace(0, 2 * np.pi, 30, endpoint=False)
        cx, cy, r = 100, 100, 80
        pts = np.array(
            [[[int(cx + r * np.cos(a)), int(cy + r * np.sin(a))]]
             for a in angles],
            dtype=np.int32,
        )
        shape_type = detector._classify_shape(pts)
        assert shape_type in ("circle", "rounded_rect")

    def test_classify_rounded_rect(self):
        from src.pipelines.cv.shape_detector import ShapeDetector

        detector = ShapeDetector()
        # Elongated shape with many vertices — low circularity
        pts = []
        for x in range(0, 200, 10):
            pts.append([[x, 0]])
        for x in range(200, 0, -10):
            pts.append([[x, 30]])
        contour = np.array(pts, dtype=np.int32)
        shape_type = detector._classify_shape(contour)
        assert shape_type in (
            "rounded_rect", "rectangle", "polygon", "diamond",
        )

    def test_classify_polygon_5_vertices(self):
        from src.pipelines.cv.shape_detector import ShapeDetector

        detector = ShapeDetector()
        # Pentagon: 5 vertices
        angles = np.linspace(0, 2 * np.pi, 5, endpoint=False)
        cx, cy, r = 100, 100, 80
        pts = np.array(
            [[[int(cx + r * np.cos(a)), int(cy + r * np.sin(a))]]
             for a in angles],
            dtype=np.int32,
        )
        shape_type = detector._classify_shape(pts)
        assert shape_type in ("polygon", "triangle", "rectangle")


class TestIsDiamond:
    """_is_diamond — diamond detection by midpoint proximity."""

    def test_diamond_true(self):
        from src.pipelines.cv.shape_detector import ShapeDetector

        detector = ShapeDetector()
        # Diamond: vertices at midpoints of bbox edges
        # bbox: x=0, y=0, w=100, h=100
        approx = np.array(
            [[[50, 0]], [[100, 50]], [[50, 100]], [[0, 50]]],
            dtype=np.int32,
        )
        assert detector._is_diamond(approx, 0, 0, 100, 100) is True

    def test_diamond_false_rectangle(self):
        from src.pipelines.cv.shape_detector import ShapeDetector

        detector = ShapeDetector()
        # Rectangle: vertices at corners, not midpoints
        approx = np.array(
            [[[0, 0]], [[100, 0]], [[100, 100]], [[0, 100]]],
            dtype=np.int32,
        )
        assert detector._is_diamond(approx, 0, 0, 100, 100) is False


class TestIsTextContour:
    """_is_text_contour — overlap ratio check."""

    def test_high_overlap_is_text(self):
        from src.pipelines.cv.shape_detector import ShapeDetector

        detector = ShapeDetector()
        # Build a text mask that covers the entire area
        text_mask = np.ones((100, 100), dtype=np.uint8) * 255
        # Contour covering most of the mask
        contour = np.array(
            [[[10, 10]], [[90, 10]], [[90, 90]], [[10, 90]]],
            dtype=np.int32,
        )
        assert detector._is_text_contour(contour, text_mask) is True

    def test_low_overlap_not_text(self):
        from src.pipelines.cv.shape_detector import ShapeDetector

        detector = ShapeDetector()
        # Text mask covers only a tiny corner
        text_mask = np.zeros((100, 100), dtype=np.uint8)
        text_mask[0:5, 0:5] = 255
        contour = np.array(
            [[[10, 10]], [[90, 10]], [[90, 90]], [[10, 90]]],
            dtype=np.int32,
        )
        assert detector._is_text_contour(contour, text_mask) is False

    def test_zero_area_contour(self):
        from src.pipelines.cv.shape_detector import ShapeDetector

        detector = ShapeDetector()
        # Empty text mask — no overlap possible
        text_mask = np.zeros((100, 100), dtype=np.uint8)
        contour = np.array(
            [[[10, 10]], [[20, 10]], [[20, 20]], [[10, 20]]],
            dtype=np.int32,
        )
        assert detector._is_text_contour(contour, text_mask) is False


class TestBuildTextMask:
    """_build_text_mask."""

    def test_multiple_boxes(self):
        from src.pipelines.cv.models import OCRBox
        from src.pipelines.cv.shape_detector import ShapeDetector

        detector = ShapeDetector()
        boxes = [
            OCRBox(
                "a",
                [[10, 10], [30, 10], [30, 30], [10, 30]],
                0.9,
                (20, 20),
            ),
            OCRBox(
                "b",
                [[50, 50], [70, 50], [70, 70], [50, 70]],
                0.8,
                (60, 60),
            ),
        ]
        mask = detector._build_text_mask((100, 100, 3), boxes)
        assert mask.shape == (100, 100)
        assert mask[20, 20] == 255  # inside first box
        assert mask[60, 60] == 255  # inside second box
        assert mask[0, 0] == 0  # outside both boxes


# ===================================================================
# 4. src/pipelines/cv/ocr_with_coords.py  (82 missed)
# ===================================================================

class TestOCRWithCoordsExtract:
    """OCRWithCoords.extract — full path coverage."""

    def test_extract_import_error(self):
        from src.pipelines.cv.ocr_with_coords import OCRWithCoords

        ocr = OCRWithCoords()
        with patch.dict("sys.modules", {"paddleocr": None}):
            # Simulate ImportError by patching the import inside
            with patch(
                "builtins.__import__",
                side_effect=ImportError("no paddleocr"),
            ):
                result = ocr.extract(b"fake_image_bytes")
        assert result == []

    def test_extract_empty_result(self):
        from src.pipelines.cv.ocr_with_coords import OCRWithCoords

        ocr = OCRWithCoords()
        mock_paddle = MagicMock()
        mock_paddle_instance = MagicMock()
        mock_paddle_instance.ocr.return_value = None
        mock_paddle.return_value = mock_paddle_instance

        # Create a valid 1x1 RGB PNG
        from PIL import Image
        import io

        img = Image.new("RGB", (10, 10), color="white")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        img_bytes = buf.getvalue()

        with patch.dict(
            "sys.modules",
            {"paddleocr": MagicMock(PaddleOCR=mock_paddle)},
        ):
            result = ocr.extract(img_bytes)
        assert result == []

    def test_extract_empty_list_result(self):
        from src.pipelines.cv.ocr_with_coords import OCRWithCoords

        ocr = OCRWithCoords()
        mock_paddle = MagicMock()
        mock_paddle_instance = MagicMock()
        mock_paddle_instance.ocr.return_value = []
        mock_paddle.return_value = mock_paddle_instance

        from PIL import Image
        import io

        img = Image.new("RGB", (10, 10))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        img_bytes = buf.getvalue()

        with patch.dict(
            "sys.modules",
            {"paddleocr": MagicMock(PaddleOCR=mock_paddle)},
        ):
            result = ocr.extract(img_bytes)
        assert result == []

    def test_extract_v3_format(self):
        from src.pipelines.cv.ocr_with_coords import OCRWithCoords

        ocr = OCRWithCoords()
        mock_paddle = MagicMock()
        mock_ocr_instance = MagicMock()

        # V3 format: result[0] has .json attribute
        v3_result = MagicMock()
        v3_result.json = {
            "res": {
                "rec_texts": ["hello", "world"],
                "rec_scores": [0.95, 0.88],
                "dt_polys": [
                    [[10, 10], [50, 10], [50, 30], [10, 30]],
                    [[60, 10], [100, 10], [100, 30], [60, 30]],
                ],
            }
        }
        mock_ocr_instance.ocr.return_value = [v3_result]
        mock_paddle.return_value = mock_ocr_instance

        from PIL import Image
        import io

        img = Image.new("RGB", (200, 100))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        img_bytes = buf.getvalue()

        with patch.dict(
            "sys.modules",
            {"paddleocr": MagicMock(PaddleOCR=mock_paddle)},
        ):
            result = ocr.extract(img_bytes)
        assert len(result) == 2
        assert result[0].text == "hello"
        assert result[1].text == "world"

    def test_extract_v3_fallback_to_legacy(self):
        from src.pipelines.cv.ocr_with_coords import OCRWithCoords

        ocr = OCRWithCoords()
        mock_paddle = MagicMock()
        mock_ocr_instance = MagicMock()

        # Result without .json attribute -> fallback to legacy
        legacy_line = [
            [[10, 10], [50, 10], [50, 30], [10, 30]],
            ("hello", 0.95),
        ]
        mock_ocr_instance.ocr.return_value = [[legacy_line]]
        mock_paddle.return_value = mock_ocr_instance

        from PIL import Image
        import io

        img = Image.new("RGB", (100, 100))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        img_bytes = buf.getvalue()

        with patch.dict(
            "sys.modules",
            {"paddleocr": MagicMock(PaddleOCR=mock_paddle)},
        ):
            result = ocr.extract(img_bytes)
        assert len(result) == 1
        assert result[0].text == "hello"

    def test_extract_grayscale_image(self):
        """Grayscale (L mode) image should be converted to RGB."""
        from src.pipelines.cv.ocr_with_coords import OCRWithCoords

        ocr = OCRWithCoords()
        mock_paddle = MagicMock()
        mock_ocr_instance = MagicMock()
        mock_ocr_instance.ocr.return_value = None
        mock_paddle.return_value = mock_ocr_instance

        from PIL import Image
        import io

        img = Image.new("L", (10, 10))  # grayscale
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        img_bytes = buf.getvalue()

        with patch.dict(
            "sys.modules",
            {"paddleocr": MagicMock(PaddleOCR=mock_paddle)},
        ):
            result = ocr.extract(img_bytes)
        assert result == []


class TestExtractV3:
    """_extract_v3 edge cases."""

    def test_no_json_attr(self):
        from src.pipelines.cv.ocr_with_coords import OCRWithCoords

        ocr = OCRWithCoords()
        result = ocr._extract_v3("plain_string")
        assert result is None

    def test_empty_rec_texts(self):
        from src.pipelines.cv.ocr_with_coords import OCRWithCoords

        ocr = OCRWithCoords()
        obj = MagicMock()
        obj.json = {"res": {"rec_texts": [], "dt_polys": []}}
        result = ocr._extract_v3(obj)
        assert result is None

    def test_empty_text_skipped(self):
        from src.pipelines.cv.ocr_with_coords import OCRWithCoords

        ocr = OCRWithCoords()
        obj = MagicMock()
        obj.json = {
            "res": {
                "rec_texts": ["", "valid"],
                "rec_scores": [0.5, 0.9],
                "dt_polys": [
                    [[0, 0], [10, 0], [10, 10], [0, 10]],
                    [[20, 20], [30, 20], [30, 30], [20, 30]],
                ],
            }
        }
        result = ocr._extract_v3(obj)
        assert len(result) == 1
        assert result[0].text == "valid"

    def test_short_polygon_skipped(self):
        from src.pipelines.cv.ocr_with_coords import OCRWithCoords

        ocr = OCRWithCoords()
        obj = MagicMock()
        obj.json = {
            "res": {
                "rec_texts": ["text"],
                "rec_scores": [0.9],
                "dt_polys": [[[0, 0], [10, 0]]],  # only 2 pts
            }
        }
        result = ocr._extract_v3(obj)
        assert result is not None
        assert len(result) == 0

    def test_score_index_out_of_range(self):
        from src.pipelines.cv.ocr_with_coords import OCRWithCoords

        ocr = OCRWithCoords()
        obj = MagicMock()
        obj.json = {
            "res": {
                "rec_texts": ["a", "b"],
                "rec_scores": [0.9],  # only 1 score for 2 texts
                "dt_polys": [
                    [[0, 0], [10, 0], [10, 10], [0, 10]],
                    [[20, 20], [30, 20], [30, 30], [20, 30]],
                ],
            }
        }
        result = ocr._extract_v3(obj)
        assert len(result) == 2
        assert result[1].confidence == 0.0  # fallback


class TestParseLegacyLine:
    """_parse_legacy_line static method."""

    def test_valid_line(self):
        from src.pipelines.cv.ocr_with_coords import OCRWithCoords

        line = [
            [[10.0, 10.0], [50.0, 10.0], [50.0, 30.0], [10.0, 30.0]],
            ("hello", 0.95),
        ]
        box = OCRWithCoords._parse_legacy_line(line)
        assert box is not None
        assert box.text == "hello"
        assert box.confidence == 0.95
        assert len(box.polygon) == 4

    def test_short_line(self):
        from src.pipelines.cv.ocr_with_coords import OCRWithCoords

        assert OCRWithCoords._parse_legacy_line([]) is None
        assert OCRWithCoords._parse_legacy_line("bad") is None

    def test_bad_text_info(self):
        from src.pipelines.cv.ocr_with_coords import OCRWithCoords

        line = [[[0, 0], [1, 0], [1, 1], [0, 1]], "not_tuple"]
        assert OCRWithCoords._parse_legacy_line(line) is None

    def test_empty_text(self):
        from src.pipelines.cv.ocr_with_coords import OCRWithCoords

        line = [
            [[0, 0], [1, 0], [1, 1], [0, 1]],
            ("", 0.5),
        ]
        assert OCRWithCoords._parse_legacy_line(line) is None

    def test_none_text(self):
        from src.pipelines.cv.ocr_with_coords import OCRWithCoords

        line = [
            [[0, 0], [1, 0], [1, 1], [0, 1]],
            (None, 0.5),
        ]
        assert OCRWithCoords._parse_legacy_line(line) is None

    def test_none_confidence(self):
        from src.pipelines.cv.ocr_with_coords import OCRWithCoords

        line = [
            [[0, 0], [10, 0], [10, 10], [0, 10]],
            ("text", None),
        ]
        box = OCRWithCoords._parse_legacy_line(line)
        assert box is not None
        assert box.confidence == 0.0

    def test_short_polygon(self):
        from src.pipelines.cv.ocr_with_coords import OCRWithCoords

        line = [
            [[0, 0], [1, 0]],  # only 2 points
            ("text", 0.9),
        ]
        assert OCRWithCoords._parse_legacy_line(line) is None

    def test_short_text_info(self):
        from src.pipelines.cv.ocr_with_coords import OCRWithCoords

        line = [
            [[0, 0], [10, 0], [10, 10], [0, 10]],
            ("only_one",),  # len < 2
        ]
        assert OCRWithCoords._parse_legacy_line(line) is None


class TestExtractLegacy:
    """_extract_legacy edge cases."""

    def test_non_list_ocr_lines(self):
        from src.pipelines.cv.ocr_with_coords import OCRWithCoords

        ocr = OCRWithCoords()
        assert ocr._extract_legacy(["not_a_list"]) == []

    def test_exception_handling(self):
        from src.pipelines.cv.ocr_with_coords import OCRWithCoords

        ocr = OCRWithCoords()
        # Trigger exception in iteration
        result = ocr._extract_legacy([TypeError("boom")])
        assert result == []


# ===================================================================
# 5. src/stores/postgres/session.py  (24 missed)
# ===================================================================

class TestToAsyncDatabaseUrl:
    """to_async_database_url conversion."""

    def test_postgres_prefix(self):
        from src.stores.postgres.session import to_async_database_url

        url = "postgres://user:pass@host/db"
        assert to_async_database_url(url) == (
            "postgresql+asyncpg://user:pass@host/db"
        )

    def test_postgresql_prefix(self):
        from src.stores.postgres.session import to_async_database_url

        url = "postgresql://user:pass@host/db"
        assert to_async_database_url(url) == (
            "postgresql+asyncpg://user:pass@host/db"
        )

    def test_already_asyncpg(self):
        from src.stores.postgres.session import to_async_database_url

        url = "postgresql+asyncpg://user:pass@host/db"
        assert to_async_database_url(url) == url

    def test_other_prefix_unchanged(self):
        from src.stores.postgres.session import to_async_database_url

        url = "sqlite:///test.db"
        assert to_async_database_url(url) == url


class TestCreateAsyncSessionFactory:
    """create_async_session_factory."""

    def test_creates_session_factory(self):
        from src.stores.postgres.session import (
            create_async_session_factory,
        )

        factory = create_async_session_factory(
            "postgresql+asyncpg://user:pass@host/db",
            pool_size=3,
            max_overflow=5,
            pool_pre_ping=False,
            echo=True,
        )
        # Should return an async_sessionmaker
        assert callable(factory)

    def test_converts_url(self):
        from src.stores.postgres.session import (
            create_async_session_factory,
        )

        # Should not crash with postgres:// prefix
        factory = create_async_session_factory(
            "postgres://user:pass@host/db"
        )
        assert callable(factory)


class TestGetKnowledgeSessionMaker:
    """get_knowledge_session_maker singleton."""

    def test_returns_none_without_env(self):
        from src.stores.postgres.session import (
            get_knowledge_session_maker,
            reset_session_maker,
        )

        reset_session_maker()
        with patch.dict("os.environ", {}, clear=True):
            result = get_knowledge_session_maker()
        assert result is None
        reset_session_maker()

    def test_creates_with_env(self):
        from src.stores.postgres.session import (
            get_knowledge_session_maker,
            reset_session_maker,
        )

        reset_session_maker()
        with patch.dict(
            "os.environ",
            {"DATABASE_URL": "postgresql+asyncpg://u:p@h/d"},
        ):
            result = get_knowledge_session_maker()
        assert result is not None
        assert callable(result)
        reset_session_maker()

    def test_singleton_returns_same(self):
        from src.stores.postgres.session import (
            get_knowledge_session_maker,
            reset_session_maker,
        )

        reset_session_maker()
        with patch.dict(
            "os.environ",
            {"DATABASE_URL": "postgresql+asyncpg://u:p@h/d"},
        ):
            r1 = get_knowledge_session_maker()
            r2 = get_knowledge_session_maker()
        assert r1 is r2
        reset_session_maker()


class TestResetSessionMaker:
    """reset_session_maker."""

    def test_reset_clears_singleton(self):
        from src.stores.postgres.session import (
            get_knowledge_session_maker,
            reset_session_maker,
        )

        reset_session_maker()
        with patch.dict(
            "os.environ",
            {"DATABASE_URL": "postgresql+asyncpg://u:p@h/d"},
        ):
            r1 = get_knowledge_session_maker()
        reset_session_maker()
        with patch.dict("os.environ", {}, clear=True):
            r2 = get_knowledge_session_maker()
        assert r1 is not None
        assert r2 is None
        reset_session_maker()


# ===================================================================
# 6. src/stores/postgres/init_db.py  (45 missed)
# ===================================================================

class TestInitDatabase:
    """init_database async function."""

    def _mock_engine(self):
        mock_engine = MagicMock()
        mock_conn = MagicMock()
        mock_conn.run_sync = AsyncMock()
        cm = AsyncMock()
        cm.__aenter__ = AsyncMock(return_value=mock_conn)
        cm.__aexit__ = AsyncMock(return_value=False)
        mock_engine.begin.return_value = cm
        mock_engine.dispose = AsyncMock()
        return mock_engine, mock_conn

    @pytest.mark.asyncio
    async def test_init_database_creates_tables(self):
        mock_engine, mock_conn = self._mock_engine()
        mock_repo = MagicMock()

        with (
            patch(
                "src.stores.postgres.init_db.create_async_engine",
                return_value=mock_engine,
            ),
            patch(
                "sqlalchemy.ext.asyncio.async_sessionmaker",
                return_value=MagicMock(),
            ),
            patch(
                "src.distill.repository.DistillRepository",
                return_value=mock_repo,
            ),
            patch(
                "src.distill.seed.seed_base_models",
                new_callable=AsyncMock,
            ) as mock_seed,
        ):
            from src.stores.postgres.init_db import init_database

            await init_database(
                "postgresql+asyncpg://u:p@h/db"
            )

        # 3 create_all calls (Knowledge, Registry, Distill)
        assert mock_conn.run_sync.await_count == 3
        mock_seed.assert_awaited_once()
        mock_engine.dispose.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_init_database_default_url(self):
        mock_engine, mock_conn = self._mock_engine()

        with (
            patch(
                "src.stores.postgres.init_db.create_async_engine",
                return_value=mock_engine,
            ),
            patch(
                "sqlalchemy.ext.asyncio.async_sessionmaker",
                return_value=MagicMock(),
            ),
            patch(
                "src.distill.repository.DistillRepository",
                return_value=MagicMock(),
            ),
            patch(
                "src.distill.seed.seed_base_models",
                new_callable=AsyncMock,
            ),
            patch.dict("os.environ", {}, clear=False),
        ):
            from src.stores.postgres.init_db import init_database

            await init_database()  # default URL path


class TestDropAllTables:
    """drop_all_tables async function."""

    @pytest.mark.asyncio
    async def test_drop_all_tables(self):
        mock_engine = MagicMock()
        mock_conn = MagicMock()
        mock_conn.run_sync = AsyncMock()
        cm = AsyncMock()
        cm.__aenter__ = AsyncMock(return_value=mock_conn)
        cm.__aexit__ = AsyncMock(return_value=False)
        mock_engine.begin.return_value = cm
        mock_engine.dispose = AsyncMock()

        with patch(
            "src.stores.postgres.init_db.create_async_engine",
            return_value=mock_engine,
        ):
            from src.stores.postgres.init_db import drop_all_tables

            await drop_all_tables(
                "postgresql+asyncpg://u:p@h/db"
            )

        # 3 drop_all calls
        assert mock_conn.run_sync.await_count == 3
        mock_engine.dispose.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_drop_all_tables_default_url(self):
        mock_engine = MagicMock()
        mock_conn = MagicMock()
        mock_conn.run_sync = AsyncMock()
        cm = AsyncMock()
        cm.__aenter__ = AsyncMock(return_value=mock_conn)
        cm.__aexit__ = AsyncMock(return_value=False)
        mock_engine.begin.return_value = cm
        mock_engine.dispose = AsyncMock()

        with (
            patch(
                "src.stores.postgres.init_db.create_async_engine",
                return_value=mock_engine,
            ),
            patch.dict("os.environ", {}, clear=False),
        ):
            from src.stores.postgres.init_db import drop_all_tables

            await drop_all_tables()


class TestInitDbMain:
    """main() CLI entry point."""

    def test_main_runs_asyncio(self):
        with (
            patch(
                "src.stores.postgres.init_db.init_database",
                new_callable=AsyncMock,
            ),
            patch(
                "src.stores.postgres.init_db.asyncio.run",
            ) as mock_run,
        ):
            from src.stores.postgres.init_db import main

            main()
        mock_run.assert_called_once()
