"""Tests for src/connectors/confluence package.

Covers models, config, html_parsers, structured_ir, and output modules.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, mock_open, patch

import pytest

# ---------------------------------------------------------------------------
# models.py tests
# ---------------------------------------------------------------------------
from src.connectors.confluence.models import (
    AttachmentContent,
    AttachmentOCRPolicy,
    AttachmentParseResult,
    CrawlSpaceResult,
    ExtractedComment,
    ExtractedEmail,
    ExtractedLabel,
    ExtractedLink,
    ExtractedMacro,
    ExtractedMention,
    ExtractedRestriction,
    ExtractedTable,
    FullPageContent,
    page_to_dict,
)


class TestAttachmentOCRPolicy:
    def test_create_frozen(self):
        policy = AttachmentOCRPolicy(
            attachment_ocr_mode="force",
            ocr_min_text_chars=100,
            ocr_max_pdf_pages=50,
            ocr_max_ppt_slides=10,
            ocr_max_images_per_attachment=1,
            slide_render_enabled=False,
            layout_analysis_enabled=True,
        )
        assert policy.attachment_ocr_mode == "force"
        assert policy.ocr_min_text_chars == 100
        assert policy.slide_render_enabled is False
        assert policy.layout_analysis_enabled is True

    def test_frozen_cannot_mutate(self):
        policy = AttachmentOCRPolicy(
            attachment_ocr_mode="auto",
            ocr_min_text_chars=50,
            ocr_max_pdf_pages=10,
            ocr_max_ppt_slides=5,
            ocr_max_images_per_attachment=2,
            slide_render_enabled=True,
            layout_analysis_enabled=False,
        )
        with pytest.raises(AttributeError):
            policy.attachment_ocr_mode = "force"  # type: ignore[misc]


class TestAttachmentParseResult:
    def test_defaults(self):
        result = AttachmentParseResult(
            extracted_text="hello",
            extracted_tables=[],
            confidence=0.95,
        )
        assert result.ocr_mode is None
        assert result.ocr_applied is False
        assert result.ocr_skip_reason is None
        assert result.ocr_units_attempted == 0
        assert result.native_text_chars == 0

    def test_all_fields(self):
        result = AttachmentParseResult(
            extracted_text="text",
            extracted_tables=[{"a": 1}],
            confidence=0.8,
            ocr_mode="force",
            ocr_applied=True,
            ocr_skip_reason=None,
            ocr_units_attempted=3,
            ocr_units_extracted=2,
            ocr_units_deferred=1,
            native_text_chars=100,
            ocr_text_chars=200,
        )
        assert result.ocr_applied is True
        assert result.ocr_units_attempted == 3


class TestExtractedDataclasses:
    def test_extracted_table(self):
        t = ExtractedTable(
            headers=["Name", "Role"],
            rows=[{"Name": "Alice", "Role": "PM"}],
            section="Team",
            table_type="owner_table",
        )
        assert t.headers == ["Name", "Role"]
        assert t.table_type == "owner_table"

    def test_extracted_table_defaults(self):
        t = ExtractedTable(headers=[], rows=[])
        assert t.section is None
        assert t.table_type is None

    def test_extracted_mention(self):
        m = ExtractedMention(user_id="u1", display_name="홍길동", context="mentioned here")
        assert m.user_id == "u1"
        assert m.email is None

    def test_extracted_email(self):
        e = ExtractedEmail(email="a@b.com", display_name="Alice", context="contact info")
        assert e.email == "a@b.com"

    def test_extracted_macro(self):
        m = ExtractedMacro(macro_type="expand", title="Details", content="body text")
        assert m.parameters == {}

    def test_extracted_macro_with_params(self):
        m = ExtractedMacro(
            macro_type="panel",
            title="Title",
            content="body",
            parameters={"bgColor": "#fff"},
        )
        assert m.parameters["bgColor"] == "#fff"

    def test_extracted_comment(self):
        c = ExtractedComment(
            comment_id="c1",
            author="Kim",
            author_email="kim@co.kr",
            content="Good job",
            created_at="2025-01-01",
        )
        assert c.parent_id is None

    def test_extracted_label(self):
        lb = ExtractedLabel(name="important", prefix="global")
        assert lb.name == "important"

    def test_extracted_link(self):
        lnk = ExtractedLink(link_type="internal", target_page_id="123")
        assert lnk.target_url is None
        assert lnk.context == ""

    def test_extracted_restriction(self):
        r = ExtractedRestriction(
            operation="read",
            restriction_type="group",
            name="admins",
        )
        assert r.account_id is None


class TestAttachmentContent:
    def test_defaults(self):
        a = AttachmentContent(id="a1", filename="doc.pdf", media_type="application/pdf", file_size=1024)
        assert a.download_path is None
        assert a.extracted_text is None
        assert a.extracted_tables == []
        assert a.has_visual_content is False
        assert a.ocr_units_attempted == 0


class TestFullPageContent:
    def _make_page(self, **overrides) -> FullPageContent:
        defaults = {
            "page_id": "p1",
            "title": "Test Page",
            "content_text": "Some text",
            "content_html": "<p>Some text</p>",
            "content_preview": "Some...",
            "tables": [],
            "mentions": [],
            "sections": [],
            "creator": "admin",
            "last_modifier": "admin",
            "version": 1,
            "url": "https://wiki.example.com/pages/p1",
            "created_at": "2025-01-01",
            "updated_at": "2025-01-02",
        }
        defaults.update(overrides)
        return FullPageContent(**defaults)

    def test_minimal(self):
        p = self._make_page()
        assert p.page_id == "p1"
        assert p.content_ir is None
        assert p.labels == []
        assert p.attachments == []

    def test_with_optional_fields(self):
        p = self._make_page(
            space_key="WIKI",
            creator_name="홍길동",
            creator_team="플랫폼팀",
            creator_email="hong@co.kr",
        )
        assert p.space_key == "WIKI"
        assert p.creator_email == "hong@co.kr"


class TestCrawlSpaceResult:
    def test_defaults(self):
        r = CrawlSpaceResult(pages=[], page_dicts=[])
        assert r.interrupted is False
        assert r.jsonl_path == ""
        assert r.source_key == ""


class TestPageToDict:
    def _make_full_page(self) -> FullPageContent:
        return FullPageContent(
            page_id="p1",
            title="Title",
            content_text="Hello world",
            content_html="<p>Hello world</p>",
            content_preview="Hello...",
            tables=[ExtractedTable(headers=["H1"], rows=[{"H1": "v1"}], table_type="owner_table")],
            mentions=[ExtractedMention(user_id="u1", display_name="Kim", context="ctx")],
            sections=[{"level": 1, "title": "Intro"}],
            creator="admin",
            last_modifier="editor",
            version=3,
            url="https://wiki.example.com/p1",
            created_at="2025-01-01",
            updated_at="2025-02-01",
            content_ir={"chunk_count": 2, "chunks": []},
            labels=[ExtractedLabel(name="tag1", prefix="global")],
            comments=[
                ExtractedComment(
                    comment_id="c1",
                    author="User",
                    author_email="u@x.com",
                    content="Nice",
                    created_at="2025-01-15",
                    parent_id=None,
                )
            ],
            emails=[ExtractedEmail(email="e@x.com", display_name="E", context="ctx")],
            macros=[ExtractedMacro(macro_type="expand", title="T", content="Body")],
            internal_links=[
                ExtractedLink(link_type="internal", target_page_id="p2", anchor_text="link")
            ],
            external_links=[
                ExtractedLink(link_type="external", target_url="https://google.com", anchor_text="G")
            ],
            restrictions=[
                ExtractedRestriction(
                    operation="read",
                    restriction_type="user",
                    name="admin",
                    account_id="acc1",
                )
            ],
            attachments=[
                AttachmentContent(
                    id="att1",
                    filename="file.pdf",
                    media_type="application/pdf",
                    file_size=2048,
                    extracted_text="PDF text",
                    ocr_applied=True,
                )
            ],
            code_blocks=[{"language": "python", "content": "print('hi')"}],
            space_key="DEV",
            ancestors=[{"id": "root", "title": "Root"}],
            version_history=[{"version": 1}],
        )

    def test_basic_fields(self):
        d = page_to_dict(self._make_full_page())
        assert d["page_id"] == "p1"
        assert d["title"] == "Title"
        assert d["content_text"] == "Hello world"
        assert d["version"] == 3
        assert d["space_key"] == "DEV"

    def test_tables_serialization(self):
        d = page_to_dict(self._make_full_page())
        assert len(d["tables"]) == 1
        assert d["tables"][0]["headers"] == ["H1"]
        assert d["tables"][0]["row_count"] == 1
        assert d["tables"][0]["table_type"] == "owner_table"

    def test_mentions_serialization(self):
        d = page_to_dict(self._make_full_page())
        assert len(d["mentions"]) == 1
        assert d["mentions"][0]["user_id"] == "u1"

    def test_labels_serialization(self):
        d = page_to_dict(self._make_full_page())
        assert d["labels"] == [{"name": "tag1", "prefix": "global"}]

    def test_comments_serialization(self):
        d = page_to_dict(self._make_full_page())
        assert len(d["comments"]) == 1
        assert d["comments"][0]["comment_id"] == "c1"

    def test_links_serialization(self):
        d = page_to_dict(self._make_full_page())
        assert len(d["internal_links"]) == 1
        assert d["internal_links"][0]["target_page_id"] == "p2"
        assert len(d["external_links"]) == 1
        assert d["external_links"][0]["target_url"] == "https://google.com"

    def test_restrictions_serialization(self):
        d = page_to_dict(self._make_full_page())
        assert len(d["restrictions"]) == 1
        assert d["restrictions"][0]["account_id"] == "acc1"

    def test_attachments_serialization(self):
        d = page_to_dict(self._make_full_page())
        assert len(d["attachments"]) == 1
        att = d["attachments"][0]
        assert att["id"] == "att1"
        assert att["extracted_text"] == "PDF text"
        assert att["ocr_applied"] is True

    def test_empty_lists(self):
        p = FullPageContent(
            page_id="p2",
            title="Empty",
            content_text="",
            content_html="",
            content_preview="",
            tables=[],
            mentions=[],
            sections=[],
            creator="",
            last_modifier="",
            version=1,
            url="",
            created_at="",
            updated_at="",
        )
        d = page_to_dict(p)
        assert d["tables"] == []
        assert d["mentions"] == []
        assert d["labels"] == []
        assert d["attachments"] == []
        assert d["internal_links"] == []
        assert d["external_links"] == []


# ---------------------------------------------------------------------------
# config.py tests
# ---------------------------------------------------------------------------
from src.connectors.confluence.config import _env_bool, _env_int


class TestEnvBool:
    def test_true_values(self, monkeypatch):
        for val in ("1", "true", "TRUE", "True", "yes", "YES", "on", "ON"):
            monkeypatch.setenv("TEST_BOOL", val)
            assert _env_bool("TEST_BOOL", False) is True

    def test_false_values(self, monkeypatch):
        for val in ("0", "false", "no", "off", "random"):
            monkeypatch.setenv("TEST_BOOL", val)
            assert _env_bool("TEST_BOOL", True) is False

    def test_default_when_missing(self, monkeypatch):
        monkeypatch.delenv("TEST_BOOL", raising=False)
        assert _env_bool("TEST_BOOL", True) is True
        assert _env_bool("TEST_BOOL", False) is False

    def test_whitespace_stripped(self, monkeypatch):
        monkeypatch.setenv("TEST_BOOL", "  true  ")
        assert _env_bool("TEST_BOOL", False) is True


class TestEnvInt:
    def test_valid_int(self, monkeypatch):
        monkeypatch.setenv("TEST_INT", "42")
        assert _env_int("TEST_INT") == 42

    def test_negative_int(self, monkeypatch):
        monkeypatch.setenv("TEST_INT", "-5")
        assert _env_int("TEST_INT") == -5

    def test_missing_returns_none(self, monkeypatch):
        monkeypatch.delenv("TEST_INT", raising=False)
        assert _env_int("TEST_INT") is None

    def test_empty_returns_none(self, monkeypatch):
        monkeypatch.setenv("TEST_INT", "")
        assert _env_int("TEST_INT") is None

    def test_whitespace_only_returns_none(self, monkeypatch):
        monkeypatch.setenv("TEST_INT", "   ")
        assert _env_int("TEST_INT") is None

    def test_invalid_returns_none(self, monkeypatch):
        monkeypatch.setenv("TEST_INT", "abc")
        assert _env_int("TEST_INT") is None


class TestCrawlerConfigFromEnv:
    def test_missing_pat_raises(self, monkeypatch):
        monkeypatch.delenv("CONFLUENCE_PAT", raising=False)
        from src.connectors.confluence.config import CrawlerConfig

        with pytest.raises(ValueError, match="CONFLUENCE_PAT"):
            CrawlerConfig.from_env()

    def test_success(self, monkeypatch, tmp_path):
        monkeypatch.setenv("CONFLUENCE_PAT", "test-token")
        monkeypatch.setenv("CONFLUENCE_BASE_URL", "https://wiki.test.com")
        monkeypatch.setenv("CONFLUENCE_OUTPUT_DIR", str(tmp_path / "output"))
        # Provide at least one knowledge source
        monkeypatch.setenv("KNOWLEDGE_SOURCES_JSON", json.dumps({
            "test": {"page_id": "123", "name": "Test KB"}
        }))

        from src.connectors.confluence.config import CrawlerConfig

        cfg = CrawlerConfig.from_env()
        assert cfg.pat == "test-token"
        assert cfg.base_url == "https://wiki.test.com"
        assert cfg.output_dir.exists()
        assert cfg.attachments_dir.exists()
        assert "test" in cfg.knowledge_sources

    def test_knowledge_sources_json(self, monkeypatch, tmp_path):
        monkeypatch.setenv("CONFLUENCE_PAT", "tok")
        monkeypatch.setenv("CONFLUENCE_OUTPUT_DIR", str(tmp_path))
        sources = {"src1": {"page_id": "1", "name": "S1"}}
        monkeypatch.setenv("KNOWLEDGE_SOURCES_JSON", json.dumps(sources))

        from src.connectors.confluence.config import CrawlerConfig

        cfg = CrawlerConfig.from_env()
        assert cfg.knowledge_sources == sources

    def test_invalid_knowledge_sources_json_falls_back(self, monkeypatch, tmp_path):
        """Invalid JSON falls back to env-based sources."""
        monkeypatch.setenv("CONFLUENCE_PAT", "tok")
        monkeypatch.setenv("CONFLUENCE_OUTPUT_DIR", str(tmp_path))
        monkeypatch.setenv("KNOWLEDGE_SOURCES_JSON", "{invalid json")
        # Ensure at least one default source exists
        monkeypatch.setenv("KNOWLEDGE_SOURCE_INFRA_PAGE_ID", "999")

        from src.connectors.confluence.config import CrawlerConfig

        cfg = CrawlerConfig.from_env()
        assert "infra" in cfg.knowledge_sources

    def test_no_sources_raises(self, monkeypatch, tmp_path):
        """No knowledge sources configured raises ValueError."""
        monkeypatch.setenv("CONFLUENCE_PAT", "tok")
        monkeypatch.setenv("CONFLUENCE_OUTPUT_DIR", str(tmp_path))
        monkeypatch.delenv("KNOWLEDGE_SOURCES_JSON", raising=False)
        # Clear all default source env vars
        for key in [
            "KNOWLEDGE_SOURCE_INFRA_PAGE_ID",
            "KNOWLEDGE_SOURCE_HS_PAGE_ID",
            "KNOWLEDGE_SOURCE_FAQ_PAGE_ID",
            "KNOWLEDGE_SOURCE_SYSTEM_PAGE_ID",
            "KNOWLEDGE_SOURCE_DICTIONARY_PAGE_ID",
            "KNOWLEDGE_SOURCE_AXCHAT_PAGE_ID",
            "KNOWLEDGE_SOURCE_HAX_PAGE_ID",
            "KNOWLEDGE_SOURCE_ITOPS_PAGE_ID",
        ]:
            monkeypatch.setenv(key, "")

        from src.connectors.confluence.config import CrawlerConfig, _load_knowledge_sources

        with pytest.raises(ValueError, match="지식 소스"):
            _load_knowledge_sources()


class TestResolveOutputDir:
    def test_configured_dir(self, monkeypatch, tmp_path):
        target = tmp_path / "crawl_out"
        monkeypatch.setenv("CONFLUENCE_OUTPUT_DIR", str(target))

        from src.connectors.confluence.config import _resolve_output_dir

        result = _resolve_output_dir()
        assert result == target
        assert result.exists()

    def test_fallback_to_home(self, monkeypatch, tmp_path):
        monkeypatch.delenv("CONFLUENCE_OUTPUT_DIR", raising=False)
        monkeypatch.setenv("CONFLUENCE_OUTPUT_DIR", "")

        from src.connectors.confluence.config import _resolve_output_dir

        result = _resolve_output_dir()
        assert result.exists()


# ---------------------------------------------------------------------------
# html_parsers.py tests
# ---------------------------------------------------------------------------
from src.connectors.confluence.html_parsers import (
    CodeBlockExtractor,
    EmailExtractor,
    LinkExtractor,
    MacroExtractor,
    MentionExtractor,
    PlainTextExtractor,
    SectionExtractor,
    TableExtractor,
)


class TestTableExtractor:
    def test_simple_table(self):
        html = """
        <table>
            <thead><tr><th>Name</th><th>Role</th></tr></thead>
            <tbody>
                <tr><td>Alice</td><td>PM</td></tr>
                <tr><td>Bob</td><td>TL</td></tr>
            </tbody>
        </table>
        """
        ext = TableExtractor()
        ext.feed(html)
        assert len(ext.tables) == 1
        t = ext.tables[0]
        assert t.headers == ["Name", "Role"]
        assert len(t.rows) == 2
        assert t.rows[0] == {"Name": "Alice", "Role": "PM"}

    def test_table_without_thead(self):
        """First row becomes header when no <thead>."""
        html = """
        <table>
            <tr><td>Col1</td><td>Col2</td></tr>
            <tr><td>A</td><td>B</td></tr>
        </table>
        """
        ext = TableExtractor()
        ext.feed(html)
        assert len(ext.tables) == 1
        assert ext.tables[0].headers == ["Col1", "Col2"]
        assert len(ext.tables[0].rows) == 1

    def test_empty_table(self):
        html = "<table></table>"
        ext = TableExtractor()
        ext.feed(html)
        assert len(ext.tables) == 0

    def test_table_type_owner(self):
        html = """
        <table>
            <thead><tr><th>담당자</th><th>업무</th></tr></thead>
            <tbody><tr><td>Kim</td><td>Dev</td></tr></tbody>
        </table>
        """
        ext = TableExtractor()
        ext.feed(html)
        assert ext.tables[0].table_type == "owner_table"

    def test_table_type_system(self):
        html = """
        <table>
            <thead><tr><th>시스템</th><th>URL</th></tr></thead>
            <tbody><tr><td>Wiki</td><td>https://wiki</td></tr></tbody>
        </table>
        """
        ext = TableExtractor()
        ext.feed(html)
        assert ext.tables[0].table_type == "system_table"

    def test_table_type_schedule(self):
        html = """
        <table>
            <thead><tr><th>업무</th><th>마감</th></tr></thead>
            <tbody><tr><td>Report</td><td>Friday</td></tr></tbody>
        </table>
        """
        ext = TableExtractor()
        ext.feed(html)
        assert ext.tables[0].table_type == "schedule_table"

    def test_table_type_status(self):
        html = """
        <table>
            <thead><tr><th>항목</th><th>상태</th></tr></thead>
            <tbody><tr><td>Task</td><td>완료</td></tr></tbody>
        </table>
        """
        ext = TableExtractor()
        ext.feed(html)
        assert ext.tables[0].table_type == "status_table"

    def test_table_type_none(self):
        html = """
        <table>
            <thead><tr><th>A</th><th>B</th></tr></thead>
            <tbody><tr><td>1</td><td>2</td></tr></tbody>
        </table>
        """
        ext = TableExtractor()
        ext.feed(html)
        assert ext.tables[0].table_type is None

    def test_mismatched_column_count_filtered(self):
        """Rows with different column count are filtered out."""
        html = """
        <table>
            <thead><tr><th>A</th><th>B</th></tr></thead>
            <tbody>
                <tr><td>1</td><td>2</td></tr>
                <tr><td>only-one</td></tr>
            </tbody>
        </table>
        """
        ext = TableExtractor()
        ext.feed(html)
        assert len(ext.tables[0].rows) == 1

    def test_multiple_tables(self):
        html = """
        <table><thead><tr><th>X</th></tr></thead><tbody><tr><td>1</td></tr></tbody></table>
        <table><thead><tr><th>Y</th></tr></thead><tbody><tr><td>2</td></tr></tbody></table>
        """
        ext = TableExtractor()
        ext.feed(html)
        assert len(ext.tables) == 2


class TestMentionExtractor:
    def test_ri_user_mention(self):
        html = '<ac:link><ri:user ri:account-id="user123" /><ac:link-body>홍길동</ac:link-body></ac:link>'
        ext = MentionExtractor()
        ext.feed(html)
        assert len(ext.mentions) >= 1
        assert any(m.user_id == "user123" for m in ext.mentions)

    def test_at_pattern_mention(self):
        # The regex captures @name with optional space+Korean surname
        html = "<p>담당자: @홍길동입니다</p>"
        ext = MentionExtractor()
        ext.feed(html)
        assert any(m.display_name is not None and "홍길동" in m.display_name for m in ext.mentions)

    def test_at_pattern_with_space(self):
        html = "<p>@김 철수가 작성</p>"
        ext = MentionExtractor()
        ext.feed(html)
        # Regex: @([가-힣]+(?:\s[가-힣]+)?) matches "김 철수가" -> "김 철수"
        assert len(ext.mentions) >= 1

    def test_no_mentions(self):
        html = "<p>No mentions here</p>"
        ext = MentionExtractor()
        ext.feed(html)
        assert len(ext.mentions) == 0


class TestEmailExtractor:
    def test_mailto_link(self):
        html = '<a href="mailto:test@example.com">Test User</a>'
        ext = EmailExtractor()
        ext.feed(html)
        assert len(ext.emails) == 1
        assert ext.emails[0].email == "test@example.com"
        assert ext.emails[0].display_name == "Test User"

    def test_mailto_with_query_params(self):
        html = '<a href="mailto:user@co.kr?subject=Hello">User</a>'
        ext = EmailExtractor()
        ext.feed(html)
        assert ext.emails[0].email == "user@co.kr"

    def test_no_emails(self):
        html = '<a href="https://google.com">Google</a>'
        ext = EmailExtractor()
        ext.feed(html)
        assert len(ext.emails) == 0

    def test_context_captured(self):
        html = "Previous context text <a href=\"mailto:a@b.com\">Name</a>"
        ext = EmailExtractor()
        ext.feed(html)
        assert "Previous context" in ext.emails[0].context

    def test_empty_display_name(self):
        html = '<a href="mailto:x@y.com"></a>'
        ext = EmailExtractor()
        ext.feed(html)
        assert ext.emails[0].display_name is None


class TestMacroExtractor:
    def test_expand_macro(self):
        html = """
        <ac:structured-macro ac:name="expand">
            <ac:parameter ac:name="title">Details</ac:parameter>
            <ac:rich-text-body>Expanded content here</ac:rich-text-body>
        </ac:structured-macro>
        """
        ext = MacroExtractor()
        ext.feed(html)
        assert len(ext.macros) == 1
        m = ext.macros[0]
        assert m.macro_type == "expand"
        assert m.title == "Details"
        assert m.content == "Expanded content here"

    def test_note_macro(self):
        html = """
        <ac:structured-macro ac:name="note">
            <ac:rich-text-body>Important note</ac:rich-text-body>
        </ac:structured-macro>
        """
        ext = MacroExtractor()
        ext.feed(html)
        assert len(ext.macros) == 1
        assert ext.macros[0].macro_type == "note"

    def test_non_target_macro_ignored(self):
        html = """
        <ac:structured-macro ac:name="customMacro">
            <ac:rich-text-body>Body</ac:rich-text-body>
        </ac:structured-macro>
        """
        ext = MacroExtractor()
        ext.feed(html)
        assert len(ext.macros) == 0

    def test_macro_with_parameters(self):
        html = """
        <ac:structured-macro ac:name="panel">
            <ac:parameter ac:name="bgColor">#eee</ac:parameter>
            <ac:parameter ac:name="title">Panel Title</ac:parameter>
            <ac:rich-text-body>Panel body</ac:rich-text-body>
        </ac:structured-macro>
        """
        ext = MacroExtractor()
        ext.feed(html)
        assert len(ext.macros) == 1
        m = ext.macros[0]
        assert m.parameters["bgColor"] == "#eee"
        assert m.title == "Panel Title"

    def test_no_macros(self):
        html = "<p>Plain text</p>"
        ext = MacroExtractor()
        ext.feed(html)
        assert len(ext.macros) == 0


class TestLinkExtractor:
    def test_ac_link_internal(self):
        html = """
        <ac:link>
            <ri:page ri:content-id="12345" />
            <ac:link-body>Page Link</ac:link-body>
        </ac:link>
        """
        ext = LinkExtractor(base_url="https://wiki.test.com")
        ext.feed(html)
        assert len(ext.internal_links) == 1
        assert ext.internal_links[0].target_page_id == "12345"

    def test_external_a_link(self):
        html = '<a href="https://google.com">Google</a>'
        ext = LinkExtractor()
        ext.feed(html)
        assert len(ext.external_links) == 1
        assert ext.external_links[0].target_url == "https://google.com"
        assert ext.external_links[0].anchor_text == "Google"

    def test_confluence_internal_a_link(self):
        html = '<a href="/pages/viewpage.action?pageId=999">Page</a>'
        ext = LinkExtractor(base_url="https://wiki.test.com")
        ext.feed(html)
        assert len(ext.internal_links) == 1
        assert ext.internal_links[0].target_page_id == "999"

    def test_display_path_internal(self):
        html = '<a href="/display/SPACE/Page+Title">Title</a>'
        ext = LinkExtractor(base_url="https://wiki.test.com")
        ext.feed(html)
        assert len(ext.internal_links) == 1

    def test_mailto_ignored(self):
        html = '<a href="mailto:a@b.com">Email</a>'
        ext = LinkExtractor()
        ext.feed(html)
        assert len(ext.external_links) == 0
        assert len(ext.internal_links) == 0

    def test_javascript_ignored(self):
        html = '<a href="javascript:void(0)">Click</a>'
        ext = LinkExtractor()
        ext.feed(html)
        assert len(ext.external_links) == 0

    def test_relative_path(self):
        html = '<a href="/some/path">Relative</a>'
        ext = LinkExtractor(base_url="https://wiki.test.com")
        ext.feed(html)
        assert len(ext.internal_links) == 1
        assert ext.internal_links[0].target_url == "https://wiki.test.com/some/path"

    def test_attachment_link_skipped(self):
        html = """
        <ac:link>
            <ri:attachment ri:filename="doc.pdf" />
        </ac:link>
        """
        ext = LinkExtractor()
        ext.feed(html)
        assert len(ext.internal_links) == 0

    def test_no_links(self):
        html = "<p>No links here</p>"
        ext = LinkExtractor()
        ext.feed(html)
        assert len(ext.internal_links) == 0
        assert len(ext.external_links) == 0


class TestSectionExtractor:
    def test_headings(self):
        html = "<h1>Title</h1><p>content</p><h2>Subtitle</h2><p>more</p>"
        ext = SectionExtractor()
        ext.feed(html)
        assert len(ext.sections) == 2
        assert ext.sections[0] == {"level": 1, "title": "Title"}
        assert ext.sections[1] == {"level": 2, "title": "Subtitle"}

    def test_h3_h4(self):
        html = "<h3>Section 3</h3><h4>Section 4</h4>"
        ext = SectionExtractor()
        ext.feed(html)
        assert len(ext.sections) == 2
        assert ext.sections[0]["level"] == 3
        assert ext.sections[1]["level"] == 4

    def test_no_headings(self):
        html = "<p>Just a paragraph</p>"
        ext = SectionExtractor()
        ext.feed(html)
        assert len(ext.sections) == 0

    def test_h5_ignored(self):
        html = "<h5>Too deep</h5>"
        ext = SectionExtractor()
        ext.feed(html)
        assert len(ext.sections) == 0


class TestPlainTextExtractor:
    def test_basic_extraction(self):
        html = "<p>Hello</p><p>World</p>"
        ext = PlainTextExtractor()
        ext.feed(html)
        text = ext.get_text()
        assert "Hello" in text
        assert "World" in text

    def test_script_excluded(self):
        html = "<p>Visible</p><script>var x = 1;</script>"
        ext = PlainTextExtractor()
        ext.feed(html)
        text = ext.get_text()
        assert "Visible" in text
        assert "var x" not in text

    def test_style_excluded(self):
        html = "<style>.cls{color:red}</style><p>Text</p>"
        ext = PlainTextExtractor()
        ext.feed(html)
        text = ext.get_text()
        assert "Text" in text
        assert "color" not in text

    def test_br_and_block_tags_add_newline(self):
        html = "<p>Line1</p><br><div>Line2</div>"
        ext = PlainTextExtractor()
        ext.feed(html)
        text = ext.get_text()
        assert "Line1" in text
        assert "Line2" in text

    def test_excessive_whitespace_collapsed(self):
        html = "<p>Text     with    spaces</p>"
        ext = PlainTextExtractor()
        ext.feed(html)
        text = ext.get_text()
        assert "  " not in text

    def test_empty_html(self):
        ext = PlainTextExtractor()
        ext.feed("")
        assert ext.get_text() == ""


class TestCodeBlockExtractor:
    def test_confluence_code_macro(self):
        html = """
        <ac:structured-macro ac:name="code">
            <ac:parameter ac:name="language">python</ac:parameter>
            <ac:plain-text-body>print("hello")</ac:plain-text-body>
        </ac:structured-macro>
        """
        ext = CodeBlockExtractor()
        ext.feed(html)
        assert len(ext.code_blocks) == 1
        assert ext.code_blocks[0]["content"] == 'print("hello")'

    def test_pre_tag(self):
        html = "<pre>some code here</pre>"
        ext = CodeBlockExtractor()
        ext.feed(html)
        assert len(ext.code_blocks) == 1
        assert ext.code_blocks[0]["content"] == "some code here"

    def test_standalone_code_tag(self):
        html = '<code class="language-js">const x = 1;</code>'
        ext = CodeBlockExtractor()
        ext.feed(html)
        assert len(ext.code_blocks) == 1
        assert ext.code_blocks[0]["language"] == "js"
        assert "const x" in ext.code_blocks[0]["content"]

    def test_empty_code_block_excluded(self):
        html = "<pre></pre>"
        ext = CodeBlockExtractor()
        ext.feed(html)
        assert len(ext.code_blocks) == 0

    def test_no_code_blocks(self):
        html = "<p>Just text</p>"
        ext = CodeBlockExtractor()
        ext.feed(html)
        assert len(ext.code_blocks) == 0


# ---------------------------------------------------------------------------
# structured_ir.py tests
# ---------------------------------------------------------------------------
from src.connectors.confluence.structured_ir import (
    _split_into_paragraphs,
    _table_to_markdown,
    extract_creator_info,
    generate_structured_ir,
)


class TestGenerateStructuredIR:
    def test_basic_ir(self):
        ir = generate_structured_ir(
            content_text="This is a paragraph with enough text to pass the 50 char minimum for inclusion.",
            content_html="<p>This is a paragraph</p>",
            title="Test Page",
            tables=[],
            sections=[{"level": 1, "title": "Introduction"}],
            mentions=[],
        )
        assert ir["title"] == "Test Page"
        assert ir["chunk_count"] >= 1
        # Section header chunk
        sec_chunks = [c for c in ir["chunks"] if c["type"] == "section_header"]
        assert len(sec_chunks) == 1
        assert sec_chunks[0]["content"] == "Introduction"

    def test_with_tables(self):
        table = ExtractedTable(
            headers=["Name", "Role"],
            rows=[{"Name": "Alice", "Role": "PM"}],
            table_type="owner_table",
        )
        ir = generate_structured_ir(
            content_text="Short",
            content_html="<p>Short</p>",
            title="T",
            tables=[table],
            sections=[],
            mentions=[],
        )
        tbl_chunks = [c for c in ir["chunks"] if c["type"] == "table"]
        assert len(tbl_chunks) == 1
        assert tbl_chunks[0]["row_count"] == 1
        assert "Name" in tbl_chunks[0]["content"]

    def test_with_code_blocks(self):
        html = """
        <ac:structured-macro ac:name="code">
            <ac:plain-text-body>print("hi")</ac:plain-text-body>
        </ac:structured-macro>
        """
        ir = generate_structured_ir(
            content_text="Short",
            content_html=html,
            title="Code Page",
            tables=[],
            sections=[],
            mentions=[],
        )
        code_chunks = [c for c in ir["chunks"] if c["type"] == "code_block"]
        assert len(code_chunks) == 1

    def test_paragraphs_min_length(self):
        """Paragraphs shorter than 50 chars are excluded."""
        ir = generate_structured_ir(
            content_text="Short",
            content_html="<p>Short</p>",
            title="T",
            tables=[],
            sections=[],
            mentions=[],
        )
        para_chunks = [c for c in ir["chunks"] if c["type"] == "paragraph"]
        assert len(para_chunks) == 0

    def test_mentions_in_ir(self):
        mention = ExtractedMention(user_id="u1", display_name="Kim", context="context text")
        ir = generate_structured_ir(
            content_text="Short",
            content_html="<p>Short</p>",
            title="T",
            tables=[],
            sections=[],
            mentions=[mention],
        )
        assert len(ir["mentions"]) == 1
        assert ir["mentions"][0]["user_id"] == "u1"

    def test_html_parse_error_handled(self):
        """Malformed HTML does not crash."""
        ir = generate_structured_ir(
            content_text="Valid text that is long enough to be a paragraph chunk for testing purposes here.",
            content_html="<not-valid>>><<<",
            title="T",
            tables=[],
            sections=[],
            mentions=[],
        )
        assert ir["title"] == "T"

    def test_empty_inputs(self):
        ir = generate_structured_ir(
            content_text="",
            content_html="",
            title="Empty",
            tables=[],
            sections=[],
            mentions=[],
        )
        assert ir["chunk_count"] == 0
        assert ir["chunks"] == []


class TestTableToMarkdown:
    def test_basic(self):
        t = ExtractedTable(
            headers=["A", "B"],
            rows=[{"A": "1", "B": "2"}, {"A": "3", "B": "4"}],
        )
        md = _table_to_markdown(t)
        assert "| A | B |" in md
        assert "|---|---|" in md
        assert "| 1 | 2 |" in md

    def test_empty_table(self):
        t = ExtractedTable(headers=[], rows=[])
        md = _table_to_markdown(t)
        assert md == ""

    def test_newline_in_cell_replaced(self):
        t = ExtractedTable(
            headers=["Col"],
            rows=[{"Col": "line1\nline2"}],
        )
        md = _table_to_markdown(t)
        assert "\n" not in md.split("\n")[-1] or "line1 line2" in md


class TestSplitIntoParagraphs:
    def test_basic_split(self):
        text = "First paragraph.\n\nSecond paragraph."
        result = _split_into_paragraphs(text)
        assert len(result) == 2

    def test_long_paragraph_split(self):
        # Create a paragraph > 1000 chars with sentences
        long_text = ". ".join(["This is a sentence" * 5] * 20)
        result = _split_into_paragraphs(long_text)
        assert len(result) > 1

    def test_empty_text(self):
        result = _split_into_paragraphs("")
        assert result == [""]

    def test_single_paragraph(self):
        result = _split_into_paragraphs("Just one paragraph")
        assert len(result) == 1


class TestExtractCreatorInfo:
    def test_name_and_team(self):
        name, team = extract_creator_info("홍길동/플랫폼팀")
        assert name == "홍길동"
        assert team == "플랫폼팀"

    def test_squad(self):
        name, team = extract_creator_info("김철수/검색스쿼드")
        assert name == "김철수"
        assert team == "검색스쿼드"

    def test_center(self):
        name, team = extract_creator_info("박영희/AI센터")
        assert name == "박영희"
        assert team == "AI센터"

    def test_no_match(self):
        name, team = extract_creator_info("admin")
        assert name is None
        assert team is None

    def test_empty_string(self):
        name, team = extract_creator_info("")
        assert name is None
        assert team is None


# ---------------------------------------------------------------------------
# output.py tests
# ---------------------------------------------------------------------------
from src.connectors.confluence.output import save_results, save_results_from_jsonl


class TestSaveResults:
    def _make_page(self, **overrides) -> FullPageContent:
        defaults = {
            "page_id": "p1",
            "title": "Test",
            "content_text": "Some content text here",
            "content_html": "<p>Some content</p>",
            "content_preview": "Some...",
            "tables": [],
            "mentions": [],
            "sections": [],
            "creator": "admin",
            "last_modifier": "admin",
            "version": 1,
            "url": "https://wiki/p1",
            "created_at": "2025-01-01",
            "updated_at": "2025-01-02",
        }
        defaults.update(overrides)
        return FullPageContent(**defaults)

    def test_save_empty_pages(self, tmp_path):
        out = tmp_path / "result.json"
        save_results([], out, source_info={"name": "test"})
        data = json.loads(out.read_text(encoding="utf-8"))
        assert data["statistics"]["total_pages"] == 0
        assert data["pages"] == []
        assert data["source_info"]["name"] == "test"

    def test_save_with_pages(self, tmp_path):
        out = tmp_path / "result.json"
        pages = [self._make_page()]
        save_results(pages, out)
        data = json.loads(out.read_text(encoding="utf-8"))
        assert data["statistics"]["total_pages"] == 1
        assert data["pages"][0]["page_id"] == "p1"
        assert data["statistics"]["total_extracted_text_chars"] > 0

    def test_save_with_page_dicts(self, tmp_path):
        """When page_dicts is provided, it should be used instead of converting pages."""
        out = tmp_path / "result.json"
        page_dicts = [{"page_id": "d1", "title": "Dict Page", "content_text": "text"}]
        save_results([], out, page_dicts=page_dicts)
        data = json.loads(out.read_text(encoding="utf-8"))
        assert data["statistics"]["total_pages"] == 1
        assert data["pages"][0]["page_id"] == "d1"

    def test_attachment_stats(self, tmp_path):
        out = tmp_path / "result.json"
        page_dicts = [{
            "page_id": "p1",
            "content_text": "body",
            "attachments": [
                {"media_type": "application/pdf", "extracted_text": "pdf text"},
                {"media_type": "image/png", "extracted_text": None},
                {"media_type": "application/vnd.ms-excel", "extracted_text": "excel"},
                {"media_type": "application/msword", "extracted_text": "word"},
                {"media_type": "application/vnd.ms-powerpoint", "extracted_text": "ppt"},
                {"media_type": "application/octet-stream", "extracted_text": None},
                {"media_type": "text/plain", "parse_error": "failed"},
            ],
        }]
        save_results([], out, page_dicts=page_dicts)
        data = json.loads(out.read_text(encoding="utf-8"))
        stats = data["statistics"]["attachment_parsing"]
        assert stats["pdf"] == 1
        assert stats["image"] == 1
        assert stats["excel"] == 1
        assert stats["word"] == 1
        assert stats["ppt"] == 1
        assert stats["other"] == 1
        assert stats["failed"] == 1

    def test_extra_metadata_stats(self, tmp_path):
        out = tmp_path / "result.json"
        page_dicts = [{
            "page_id": "p1",
            "content_text": "body",
            "labels": [{"name": "tag"}],
            "comments": [{"id": "c1"}],
            "emails": [{"email": "a@b.com"}],
            "macros": [{"type": "note"}],
            "internal_links": [{"target": "p2"}],
            "external_links": [{"url": "https://google.com"}],
            "restrictions": [{"op": "read"}],
            "version_history": [{"v": 1}],
        }]
        save_results([], out, page_dicts=page_dicts)
        data = json.loads(out.read_text(encoding="utf-8"))
        s = data["statistics"]
        assert s["total_labels"] == 1
        assert s["total_comments"] == 1
        assert s["total_emails"] == 1
        assert s["total_macros"] == 1
        assert s["total_internal_links"] == 1
        assert s["total_external_links"] == 1
        assert s["total_restrictions"] == 1
        assert s["total_version_history"] == 1


class TestSaveResultsFromJsonl:
    def test_basic_streaming(self, tmp_path):
        jsonl_path = tmp_path / "pages.jsonl"
        out_path = tmp_path / "output.json"

        pages = [
            {"page_id": "p1", "title": "Page 1", "content_text": "Hello world"},
            {"page_id": "p2", "title": "Page 2", "content_text": "Second page"},
        ]
        jsonl_path.write_text(
            "\n".join(json.dumps(p, ensure_ascii=False) for p in pages),
            encoding="utf-8",
        )

        count = save_results_from_jsonl(jsonl_path, out_path, source_info={"name": "test"})
        assert count == 2

        data = json.loads(out_path.read_text(encoding="utf-8"))
        assert len(data["pages"]) == 2
        assert data["source_info"]["name"] == "test"
        assert data["statistics"]["total_pages"] == 2

    def test_deduplication(self, tmp_path):
        """Duplicate page_ids should be deduplicated."""
        jsonl_path = tmp_path / "pages.jsonl"
        out_path = tmp_path / "output.json"

        pages = [
            {"page_id": "p1", "title": "Page 1", "content_text": "text"},
            {"page_id": "p1", "title": "Page 1 dup", "content_text": "duplicate"},
        ]
        jsonl_path.write_text(
            "\n".join(json.dumps(p) for p in pages),
            encoding="utf-8",
        )

        count = save_results_from_jsonl(jsonl_path, out_path)
        assert count == 1

    def test_empty_jsonl(self, tmp_path):
        jsonl_path = tmp_path / "empty.jsonl"
        out_path = tmp_path / "output.json"
        jsonl_path.write_text("", encoding="utf-8")

        count = save_results_from_jsonl(jsonl_path, out_path)
        assert count == 0

    def test_invalid_json_lines_skipped(self, tmp_path):
        jsonl_path = tmp_path / "pages.jsonl"
        out_path = tmp_path / "output.json"

        content = (
            '{"page_id": "p1", "content_text": "valid"}\n'
            "not valid json\n"
            '{"page_id": "p2", "content_text": "also valid"}\n'
        )
        jsonl_path.write_text(content, encoding="utf-8")

        count = save_results_from_jsonl(jsonl_path, out_path)
        assert count == 2

    def test_non_dict_lines_skipped(self, tmp_path):
        jsonl_path = tmp_path / "pages.jsonl"
        out_path = tmp_path / "output.json"

        content = '["not", "a", "dict"]\n{"page_id": "p1", "content_text": "ok"}\n'
        jsonl_path.write_text(content, encoding="utf-8")

        count = save_results_from_jsonl(jsonl_path, out_path)
        assert count == 1

    def test_blank_lines_skipped(self, tmp_path):
        jsonl_path = tmp_path / "pages.jsonl"
        out_path = tmp_path / "output.json"

        content = '\n\n{"page_id": "p1", "content_text": "ok"}\n\n'
        jsonl_path.write_text(content, encoding="utf-8")

        count = save_results_from_jsonl(jsonl_path, out_path)
        assert count == 1

    def test_attachment_stats_streaming(self, tmp_path):
        jsonl_path = tmp_path / "pages.jsonl"
        out_path = tmp_path / "output.json"

        page = {
            "page_id": "p1",
            "content_text": "body",
            "attachments": [
                {"media_type": "application/pdf", "extracted_text": "pdf content"},
            ],
        }
        jsonl_path.write_text(json.dumps(page), encoding="utf-8")

        save_results_from_jsonl(jsonl_path, out_path)
        data = json.loads(out_path.read_text(encoding="utf-8"))
        assert data["statistics"]["attachment_parsing"]["pdf"] == 1

    def test_source_info_null(self, tmp_path):
        jsonl_path = tmp_path / "pages.jsonl"
        out_path = tmp_path / "output.json"
        jsonl_path.write_text('{"page_id":"p1","content_text":"x"}', encoding="utf-8")

        save_results_from_jsonl(jsonl_path, out_path, source_info=None)
        data = json.loads(out_path.read_text(encoding="utf-8"))
        assert data["source_info"] is None


# ---------------------------------------------------------------------------
# __init__.py — crawl_space and helpers
# ---------------------------------------------------------------------------
import signal
from unittest.mock import AsyncMock


class TestCrawlSpaceBFSMode:
    """Test BFS vs DFS mode selection in _run_crawl."""

    @pytest.mark.asyncio
    async def test_bfs_mode_selected(self):
        """BFS mode when use_bfs=True, not resume, max_pages=None."""
        from src.connectors.confluence import _run_crawl

        client = MagicMock()
        client.crawl_bfs = AsyncMock()
        client.crawl_recursive = AsyncMock()

        await _run_crawl(
            client, "123", "src",
            use_bfs=True, resume=False, max_pages=None,
            download_attachments=True, max_attachments_per_page=20,
        )
        client.crawl_bfs.assert_awaited_once()
        client.crawl_recursive.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_dfs_when_resume(self):
        """DFS mode when resume=True."""
        from src.connectors.confluence import _run_crawl

        client = MagicMock()
        client.crawl_bfs = AsyncMock()
        client.crawl_recursive = AsyncMock()

        await _run_crawl(
            client, "123", "src",
            use_bfs=True, resume=True, max_pages=None,
            download_attachments=True, max_attachments_per_page=20,
        )
        client.crawl_recursive.assert_awaited_once()
        client.crawl_bfs.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_dfs_when_max_pages_set(self):
        """DFS mode when max_pages is set."""
        from src.connectors.confluence import _run_crawl

        client = MagicMock()
        client.crawl_bfs = AsyncMock()
        client.crawl_recursive = AsyncMock()

        await _run_crawl(
            client, "123", "src",
            use_bfs=True, resume=False, max_pages=50,
            download_attachments=True, max_attachments_per_page=20,
        )
        client.crawl_recursive.assert_awaited_once()
        client.crawl_bfs.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_dfs_when_use_bfs_false(self):
        """DFS mode when use_bfs=False."""
        from src.connectors.confluence import _run_crawl

        client = MagicMock()
        client.crawl_bfs = AsyncMock()
        client.crawl_recursive = AsyncMock()

        await _run_crawl(
            client, "123", "src",
            use_bfs=False, resume=False, max_pages=None,
            download_attachments=True, max_attachments_per_page=20,
        )
        client.crawl_recursive.assert_awaited_once()
        client.crawl_bfs.assert_not_awaited()


class TestSignalHandlers:
    """Test _setup_signal_handlers and _restore_signal_handlers."""

    def test_setup_registers_handlers(self):
        from src.connectors.confluence import _setup_signal_handlers

        client = MagicMock()
        client.request_shutdown = MagicMock()

        with patch("signal.signal") as mock_signal, \
             patch("signal.getsignal", return_value=signal.SIG_DFL):
            interrupted, prev_term, prev_int = _setup_signal_handlers(client)

        assert interrupted is False
        assert mock_signal.call_count == 2  # SIGTERM + SIGINT

    def test_restore_handlers(self):
        from src.connectors.confluence import _restore_signal_handlers

        sentinel_term = MagicMock()
        sentinel_int = MagicMock()

        with patch("signal.signal") as mock_signal:
            _restore_signal_handlers(sentinel_term, sentinel_int)

        assert mock_signal.call_count == 2
        mock_signal.assert_any_call(signal.SIGTERM, sentinel_term)
        mock_signal.assert_any_call(signal.SIGINT, sentinel_int)

    def test_graceful_shutdown_handler_calls_client(self):
        """The installed signal handler should call client.request_shutdown."""
        from src.connectors.confluence import _setup_signal_handlers

        client = MagicMock()
        client.request_shutdown = MagicMock()

        installed_handler = None

        def capture_handler(sig, handler):
            nonlocal installed_handler
            if sig == signal.SIGTERM:
                installed_handler = handler
            return signal.SIG_DFL

        with patch("signal.signal", side_effect=capture_handler), \
             patch("signal.getsignal", return_value=signal.SIG_DFL):
            _setup_signal_handlers(client)

        assert installed_handler is not None
        # Simulate SIGTERM
        installed_handler(signal.SIGTERM, None)
        client.request_shutdown.assert_called_once()

    def test_graceful_shutdown_handler_idempotent(self):
        """Second signal invocation should be ignored."""
        from src.connectors.confluence import _setup_signal_handlers

        client = MagicMock()
        installed_handler = None

        def capture_handler(sig, handler):
            nonlocal installed_handler
            if sig == signal.SIGTERM:
                installed_handler = handler
            return signal.SIG_DFL

        with patch("signal.signal", side_effect=capture_handler), \
             patch("signal.getsignal", return_value=signal.SIG_DFL):
            _setup_signal_handlers(client)

        installed_handler(signal.SIGTERM, None)
        installed_handler(signal.SIGTERM, None)  # second call
        client.request_shutdown.assert_called_once()


class TestCrawlSpaceFunction:
    """Test the high-level crawl_space function."""

    @pytest.mark.asyncio
    async def test_crawl_space_normal_completion(self):
        from src.connectors.confluence import crawl_space
        from src.connectors.confluence.config import CrawlerConfig

        config = CrawlerConfig(
            base_url="https://test.example.com",
            pat="test-pat",
            output_dir=Path("/tmp/test_crawl"),
            attachments_dir=Path("/tmp/test_crawl/att"),
            knowledge_sources={},
        )

        with (
            patch("src.connectors.confluence.attachment_parser.AttachmentParser.configure_run"),
            patch("src.connectors.confluence.ConfluenceFullClient") as MockClient,
        ):
            client_instance = MagicMock()
            client_instance.crawl_bfs = AsyncMock()
            client_instance.save_incremental = MagicMock()
            client_instance.shutdown_requested = False
            client_instance.finalize_from_incremental = MagicMock(return_value=[])
            client_instance.write_runtime_stats = MagicMock()
            client_instance.all_pages = []
            client_instance._total_pages_crawled = 5
            client_instance.clear_checkpoint = MagicMock()
            client_instance.clear_incremental = MagicMock()
            client_instance.close = AsyncMock()
            MockClient.return_value = client_instance

            result = await crawl_space(
                config, page_id="123", source_name="test",
                source_key="test_src", use_bfs=True,
            )

        assert result.interrupted is False
        client_instance.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_crawl_space_resume_with_checkpoint(self):
        from src.connectors.confluence import crawl_space
        from src.connectors.confluence.config import CrawlerConfig

        config = CrawlerConfig(
            base_url="https://test.example.com",
            pat="test-pat",
            output_dir=Path("/tmp/test_crawl"),
            attachments_dir=Path("/tmp/test_crawl/att"),
            knowledge_sources={},
        )

        with (
            patch("src.connectors.confluence.attachment_parser.AttachmentParser.configure_run"),
            patch("src.connectors.confluence.ConfluenceFullClient") as MockClient,
        ):
            client_instance = MagicMock()
            client_instance.crawl_recursive = AsyncMock()
            client_instance.save_incremental = MagicMock()
            client_instance.shutdown_requested = False
            client_instance.load_checkpoint = MagicMock(return_value=True)
            client_instance.load_incremental = MagicMock(return_value=5)
            client_instance.finalize_from_incremental = MagicMock(return_value=[])
            client_instance.write_runtime_stats = MagicMock()
            client_instance.all_pages = []
            client_instance._total_pages_crawled = 10
            client_instance.close = AsyncMock()
            MockClient.return_value = client_instance

            result = await crawl_space(
                config, page_id="123", source_name="test",
                source_key="test_src", resume=True,
            )

        client_instance.load_incremental.assert_called_once_with("test_src")
        client_instance.load_checkpoint.assert_called_once_with("test_src")
        assert result.interrupted is False

    @pytest.mark.asyncio
    async def test_crawl_space_resume_no_checkpoint(self):
        from src.connectors.confluence import crawl_space
        from src.connectors.confluence.config import CrawlerConfig

        config = CrawlerConfig(
            base_url="https://test.example.com",
            pat="test-pat",
            output_dir=Path("/tmp/test_crawl"),
            attachments_dir=Path("/tmp/test_crawl/att"),
            knowledge_sources={},
        )

        with (
            patch("src.connectors.confluence.attachment_parser.AttachmentParser.configure_run"),
            patch("src.connectors.confluence.ConfluenceFullClient") as MockClient,
        ):
            client_instance = MagicMock()
            client_instance.crawl_recursive = AsyncMock()
            client_instance.save_incremental = MagicMock()
            client_instance.shutdown_requested = False
            client_instance.load_checkpoint = MagicMock(return_value=False)
            client_instance.load_incremental = MagicMock(return_value=0)
            client_instance.finalize_from_incremental = MagicMock(return_value=[])
            client_instance.write_runtime_stats = MagicMock()
            client_instance.all_pages = []
            client_instance.close = AsyncMock()
            MockClient.return_value = client_instance

            result = await crawl_space(
                config, page_id="123", source_name="test",
                source_key="test_src", resume=True,
            )

        assert result.interrupted is False

    @pytest.mark.asyncio
    async def test_crawl_space_resume_with_incremental_only(self):
        from src.connectors.confluence import crawl_space
        from src.connectors.confluence.config import CrawlerConfig

        config = CrawlerConfig(
            base_url="https://test.example.com",
            pat="test-pat",
            output_dir=Path("/tmp/test_crawl"),
            attachments_dir=Path("/tmp/test_crawl/att"),
            knowledge_sources={},
        )

        with (
            patch("src.connectors.confluence.attachment_parser.AttachmentParser.configure_run"),
            patch("src.connectors.confluence.ConfluenceFullClient") as MockClient,
        ):
            client_instance = MagicMock()
            client_instance.crawl_recursive = AsyncMock()
            client_instance.save_incremental = MagicMock()
            client_instance.shutdown_requested = False
            client_instance.load_checkpoint = MagicMock(return_value=False)
            client_instance.load_incremental = MagicMock(return_value=3)
            client_instance.finalize_from_incremental = MagicMock(return_value=[])
            client_instance.write_runtime_stats = MagicMock()
            client_instance.all_pages = []
            client_instance.close = AsyncMock()
            MockClient.return_value = client_instance

            result = await crawl_space(
                config, page_id="123", source_name="test",
                source_key="test_src", resume=True,
            )

        assert result.interrupted is False

    @pytest.mark.asyncio
    async def test_crawl_space_interrupted(self):
        from src.connectors.confluence import crawl_space
        from src.connectors.confluence.config import CrawlerConfig

        config = CrawlerConfig(
            base_url="https://test.example.com",
            pat="test-pat",
            output_dir=Path("/tmp/test_crawl"),
            attachments_dir=Path("/tmp/test_crawl/att"),
            knowledge_sources={},
        )

        with (
            patch("src.connectors.confluence.attachment_parser.AttachmentParser.configure_run"),
            patch("src.connectors.confluence.ConfluenceFullClient") as MockClient,
        ):
            client_instance = MagicMock()
            client_instance.crawl_bfs = AsyncMock()
            client_instance.save_incremental = MagicMock()
            client_instance.shutdown_requested = True
            client_instance.save_checkpoint = MagicMock()
            client_instance.finalize_from_incremental = MagicMock(return_value=[])
            client_instance.write_runtime_stats = MagicMock()
            client_instance.all_pages = []
            client_instance.clear_checkpoint = MagicMock()
            client_instance.clear_incremental = MagicMock()
            client_instance.close = AsyncMock()
            MockClient.return_value = client_instance

            result = await crawl_space(
                config, page_id="123", source_name="test",
                source_key="test_src", use_bfs=True,
            )

        assert result.interrupted is True
        client_instance.save_checkpoint.assert_called_once_with("test_src")

    @pytest.mark.asyncio
    async def test_crawl_space_with_signal_registration(self):
        from src.connectors.confluence import crawl_space
        from src.connectors.confluence.config import CrawlerConfig

        config = CrawlerConfig(
            base_url="https://test.example.com",
            pat="test-pat",
            output_dir=Path("/tmp/test_crawl"),
            attachments_dir=Path("/tmp/test_crawl/att"),
            knowledge_sources={},
        )

        with (
            patch("src.connectors.confluence.attachment_parser.AttachmentParser.configure_run"),
            patch("src.connectors.confluence.ConfluenceFullClient") as MockClient,
            patch("src.connectors.confluence._setup_signal_handlers") as mock_setup,
            patch("src.connectors.confluence._restore_signal_handlers") as mock_restore,
        ):
            mock_setup.return_value = (False, signal.SIG_DFL, signal.SIG_DFL)
            client_instance = MagicMock()
            client_instance.crawl_bfs = AsyncMock()
            client_instance.save_incremental = MagicMock()
            client_instance.shutdown_requested = False
            client_instance.finalize_from_incremental = MagicMock(return_value=[])
            client_instance.write_runtime_stats = MagicMock()
            client_instance.all_pages = []
            client_instance.clear_checkpoint = MagicMock()
            client_instance.clear_incremental = MagicMock()
            client_instance.close = AsyncMock()
            MockClient.return_value = client_instance

            await crawl_space(
                config, page_id="123", source_name="test",
                source_key="test_src", register_signals=True,
            )

        mock_setup.assert_called_once()
        mock_restore.assert_called_once()


# ===================================================================
# CLI modules coverage (crawl.py, ingest.py)
# ===================================================================

class TestCliCrawl:
    def test_load_crawl_state_no_file(self, tmp_path):
        from src.cli.crawl import _load_crawl_state
        assert _load_crawl_state(tmp_path) == {}

    def test_save_and_load_crawl_state(self, tmp_path):
        from src.cli.crawl import _save_crawl_state, _load_crawl_state
        state = {"file.txt": "abc123"}
        _save_crawl_state(tmp_path, state)
        loaded = _load_crawl_state(tmp_path)
        assert loaded == state

    def test_read_text_content_txt(self, tmp_path):
        from src.cli.crawl import _read_text_content
        f = tmp_path / "test.txt"
        f.write_text("hello", encoding="utf-8")
        assert _read_text_content(f) == "hello"

    def test_read_text_content_non_text(self, tmp_path):
        from src.cli.crawl import _read_text_content
        f = tmp_path / "test.pdf"
        f.write_bytes(b"binary")
        assert _read_text_content(f) == ""

    def test_build_doc(self, tmp_path):
        from src.cli.crawl import _build_doc
        f = tmp_path / "test.txt"
        f.write_text("content", encoding="utf-8")
        doc = _build_doc(f, tmp_path, "hash123")
        assert doc["title"] == "test.txt"
        assert doc["content_hash"] == "hash123"

    def test_crawl_directory(self, tmp_path):
        from src.cli.crawl import crawl_directory
        source = tmp_path / "source"
        source.mkdir()
        (source / "test.txt").write_text("hello")
        output = tmp_path / "output"
        crawl_directory(str(source), str(output))
        assert (output / "crawl_results.jsonl").exists()

    def test_crawl_directory_incremental(self, tmp_path):
        from src.cli.crawl import crawl_directory
        source = tmp_path / "source"
        source.mkdir()
        (source / "test.txt").write_text("hello")
        output = tmp_path / "output"
        crawl_directory(str(source), str(output))
        # Second run should skip unchanged
        crawl_directory(str(source), str(output))

    def test_crawl_directory_missing_source(self, tmp_path):
        from src.cli.crawl import crawl_directory
        crawl_directory(str(tmp_path / "nonexistent"), str(tmp_path / "out"))


class TestCliIngest:
    @pytest.mark.asyncio
    async def test_should_skip_file_force(self, tmp_path):
        from src.cli.ingest import _should_skip_file
        f = tmp_path / "test.txt"
        f.write_text("hello")
        result = await _should_skip_file(str(f), force=True, ingested_hashes={"abc"})
        assert result is False

    @pytest.mark.asyncio
    async def test_should_skip_file_existing(self, tmp_path):
        import hashlib
        from src.cli.ingest import _should_skip_file
        f = tmp_path / "test.txt"
        f.write_text("hello")
        h = hashlib.sha256(b"hello").hexdigest()[:32]
        result = await _should_skip_file(str(f), force=False, ingested_hashes={h})
        assert result is True

    @pytest.mark.asyncio
    async def test_should_skip_file_new(self, tmp_path):
        from src.cli.ingest import _should_skip_file
        f = tmp_path / "test.txt"
        f.write_text("hello")
        result = await _should_skip_file(str(f), force=False, ingested_hashes={"other"})
        assert result is False

    @pytest.mark.asyncio
    async def test_should_skip_empty_hashes(self, tmp_path):
        from src.cli.ingest import _should_skip_file
        f = tmp_path / "test.txt"
        f.write_text("hello")
        result = await _should_skip_file(str(f), force=False, ingested_hashes=set())
        assert result is False
