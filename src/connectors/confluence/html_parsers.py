"""HTML parser classes for extracting structured content from Confluence pages.

Includes parsers for tables, mentions, emails, macros, links, sections,
plain text, and code blocks.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser

from .models import (
    ExtractedEmail,
    ExtractedLink,
    ExtractedMacro,
    ExtractedMention,
    ExtractedTable,
)


class TableExtractor(HTMLParser):
    """HTML에서 테이블 추출"""

    def __init__(self):
        super().__init__()
        self.tables: list[ExtractedTable] = []
        self.current_table: dict | None = None
        self.current_row: list[str] = []
        self.current_cell: str = ""
        self.in_table = False
        self.in_header = False
        self.in_row = False
        self.in_cell = False

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self.in_table = True
            self.current_table = {"headers": [], "rows": []}
        elif tag == "thead":
            self.in_header = True
        elif tag == "tr":
            self.in_row = True
            self.current_row = []
        elif tag in ("td", "th"):
            self.in_cell = True
            self.current_cell = ""

    def handle_endtag(self, tag):
        if tag == "table" and self.current_table:
            if self.current_table["headers"] or self.current_table["rows"]:
                # 첫 번째 행이 헤더일 수 있음
                if not self.current_table["headers"] and self.current_table["rows"]:
                    self.current_table["headers"] = self.current_table["rows"].pop(0) if self.current_table["rows"] else []

                # 테이블 타입 추론
                table_type = self._infer_table_type(self.current_table["headers"])

                self.tables.append(ExtractedTable(
                    headers=self.current_table["headers"],
                    rows=[
                        dict(zip(self.current_table["headers"], row))
                        for row in self.current_table["rows"]
                        if len(row) == len(self.current_table["headers"])
                    ],
                    table_type=table_type,
                ))
            self.in_table = False
            self.current_table = None
        elif tag == "thead":
            self.in_header = False
        elif tag == "tr" and self.current_table:
            if self.in_header:
                self.current_table["headers"] = self.current_row
            else:
                self.current_table["rows"].append(self.current_row)
            self.in_row = False
        elif tag in ("td", "th"):
            self.current_row.append(self.current_cell.strip())
            self.in_cell = False

    def handle_data(self, data):
        if self.in_cell:
            self.current_cell += data

    def _infer_table_type(self, headers: list[str]) -> str | None:
        """테이블 타입 추론"""
        headers_str = " ".join(headers).lower()

        if any(kw in headers_str for kw in ["담당자", "담당", "pm", "tl"]):
            return "owner_table"
        elif any(kw in headers_str for kw in ["시스템", "서비스", "api", "url"]):
            return "system_table"
        elif any(kw in headers_str for kw in ["일정", "마감", "주기"]):
            return "schedule_table"
        elif any(kw in headers_str for kw in ["상태", "진행"]):
            return "status_table"
        return None


class MentionExtractor(HTMLParser):
    """HTML에서 @멘션 추출 (이메일 정보 포함)"""

    def __init__(self):
        super().__init__()
        self.mentions: list[ExtractedMention] = []
        self.current_text = ""
        self.in_link = False
        self.current_user_id: str | None = None

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        # Confluence 멘션 패턴
        if tag == "ri:user":
            user_id = attrs_dict.get("ri:account-id") or attrs_dict.get("ri:userkey")
            self.current_user_id = user_id
            self.mentions.append(ExtractedMention(
                user_id=user_id,
                display_name=None,
                context="",
            ))
        elif tag == "ac:link":
            self.in_link = True

    def handle_endtag(self, tag):
        if tag == "ac:link":
            self.in_link = False
            self.current_user_id = None

    def handle_data(self, data):
        self.current_text = data
        # @ 패턴 찾기
        for match in re.finditer(r"@([가-힣]+(?:\s[가-힣]+)?)", data):
            self.mentions.append(ExtractedMention(
                user_id=None,
                display_name=match.group(1),
                context=data[:100],
            ))
        # ac:link 내부의 텍스트가 사용자 이름일 수 있음
        if self.in_link and self.mentions and self.mentions[-1].display_name is None:
            self.mentions[-1].display_name = data.strip()


class EmailExtractor(HTMLParser):
    """HTML에서 mailto 이메일 링크 추출"""

    def __init__(self):
        super().__init__()
        self.emails: list[ExtractedEmail] = []
        self.current_email: str | None = None
        self.in_mailto_link = False
        self.link_text = ""
        self.context_buffer = ""

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        if tag == "a":
            href = attrs_dict.get("href", "")
            if href.startswith("mailto:"):
                self.current_email = href.replace("mailto:", "").split("?")[0]  # ?subject= 등 제거
                self.in_mailto_link = True
                self.link_text = ""

    def handle_endtag(self, tag):
        if tag == "a" and self.in_mailto_link and self.current_email:
            self.emails.append(ExtractedEmail(
                email=self.current_email,
                display_name=self.link_text.strip() if self.link_text.strip() else None,
                context=self.context_buffer[-100:] if self.context_buffer else "",
            ))
            self.in_mailto_link = False
            self.current_email = None

    def handle_data(self, data):
        self.context_buffer += data
        if len(self.context_buffer) > 200:
            self.context_buffer = self.context_buffer[-200:]
        if self.in_mailto_link:
            self.link_text += data


class MacroExtractor(HTMLParser):
    """HTML에서 Confluence 매크로 추출 (expand, panel, note, info, warning, status 등)"""

    # 추출 대상 매크로 타입
    TARGET_MACROS = {"expand", "panel", "note", "info", "warning", "tip", "status", "toc", "children", "excerpt"}

    def __init__(self):
        super().__init__()
        self.macros: list[ExtractedMacro] = []
        self.macro_stack: list[dict] = []  # 중첩 매크로 처리용
        self.current_param_name: str | None = None
        self.in_body = False
        self.body_content = ""

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)

        if tag == "ac:structured-macro":
            macro_name = attrs_dict.get("ac:name", "")
            if macro_name in self.TARGET_MACROS:
                self.macro_stack.append({
                    "type": macro_name,
                    "title": None,
                    "content": "",
                    "parameters": {},
                })

        elif tag == "ac:parameter" and self.macro_stack:
            self.current_param_name = attrs_dict.get("ac:name")

        elif tag == "ac:rich-text-body" and self.macro_stack:
            self.in_body = True
            self.body_content = ""

        elif tag == "ac:plain-text-body" and self.macro_stack:
            self.in_body = True
            self.body_content = ""

    def handle_endtag(self, tag):
        if tag == "ac:structured-macro" and self.macro_stack:
            macro_data = self.macro_stack.pop()
            self.macros.append(ExtractedMacro(
                macro_type=macro_data["type"],
                title=macro_data.get("title") or macro_data["parameters"].get("title"),
                content=macro_data["content"],
                parameters=macro_data["parameters"],
            ))

        elif tag == "ac:parameter":
            self.current_param_name = None

        elif tag in ("ac:rich-text-body", "ac:plain-text-body"):
            if self.macro_stack:
                self.macro_stack[-1]["content"] = self.body_content.strip()
            self.in_body = False

    def handle_data(self, data):
        if self.current_param_name and self.macro_stack:
            self.macro_stack[-1]["parameters"][self.current_param_name] = data.strip()
            # title 파라미터는 별도 저장
            if self.current_param_name == "title":
                self.macro_stack[-1]["title"] = data.strip()

        if self.in_body:
            self.body_content += data


class LinkExtractor(HTMLParser):
    """HTML에서 내부/외부 링크 추출"""

    # 무시할 URL 패턴 (스타일, 스크립트 등)
    IGNORE_PATTERNS = {"javascript:", "#", "data:", "blob:"}

    def __init__(self, base_url: str = ""):
        super().__init__()
        self.base_url = base_url
        self.internal_links: list[ExtractedLink] = []
        self.external_links: list[ExtractedLink] = []
        self.current_link: dict | None = None
        self.in_link = False
        self.link_text = ""
        self.context_buffer = ""

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)

        # Confluence 내부 링크: ac:link + ri:page
        if tag == "ac:link":
            self.current_link = {"type": "internal", "page_id": None, "anchor": None}
            self.in_link = True
            self.link_text = ""

        elif tag == "ri:page" and self.current_link:
            # 내부 페이지 링크
            content_id = attrs_dict.get("ri:content-id")
            if content_id:
                self.current_link["page_id"] = content_id

        elif tag == "ri:attachment" and self.current_link:
            # 첨부파일 링크는 건너뜀
            self.current_link = None
            self.in_link = False

        # 일반 a 태그 링크
        elif tag == "a":
            href = attrs_dict.get("href", "")

            # 무시할 패턴 체크
            if any(href.startswith(p) for p in self.IGNORE_PATTERNS):
                return

            # mailto는 EmailExtractor에서 처리
            if href.startswith("mailto:"):
                return

            self.in_link = True
            self.link_text = ""

            # 내부 링크 판별
            if "/pages/viewpage.action" in href or "/display/" in href:
                # Confluence 내부 링크
                page_id = None
                if "pageId=" in href:
                    try:
                        page_id = href.split("pageId=")[1].split("&")[0]
                    except (IndexError, ValueError):
                        pass
                self.current_link = {"type": "internal", "page_id": page_id, "url": href}
            elif href.startswith("http://") or href.startswith("https://"):
                # 외부 링크
                self.current_link = {"type": "external", "url": href}
            elif href.startswith("/"):
                # 상대 경로 (내부)
                self.current_link = {"type": "internal", "url": self.base_url + href, "page_id": None}

    def handle_endtag(self, tag):
        if tag == "ac:link" and self.current_link and self.current_link.get("page_id"):
            self.internal_links.append(ExtractedLink(
                link_type="internal",
                target_page_id=self.current_link.get("page_id"),
                anchor_text=self.link_text.strip() if self.link_text.strip() else None,
                context=self.context_buffer[-100:] if self.context_buffer else "",
            ))
            self.current_link = None
            self.in_link = False

        elif tag == "a" and self.current_link:
            link_type = self.current_link.get("type", "external")

            if link_type == "internal":
                self.internal_links.append(ExtractedLink(
                    link_type="internal",
                    target_page_id=self.current_link.get("page_id"),
                    target_url=self.current_link.get("url"),
                    anchor_text=self.link_text.strip() if self.link_text.strip() else None,
                    context=self.context_buffer[-100:] if self.context_buffer else "",
                ))
            else:
                self.external_links.append(ExtractedLink(
                    link_type="external",
                    target_url=self.current_link.get("url"),
                    anchor_text=self.link_text.strip() if self.link_text.strip() else None,
                    context=self.context_buffer[-100:] if self.context_buffer else "",
                ))

            self.current_link = None
            self.in_link = False

    def handle_data(self, data):
        self.context_buffer += data
        if len(self.context_buffer) > 200:
            self.context_buffer = self.context_buffer[-200:]

        if self.in_link:
            self.link_text += data


class SectionExtractor(HTMLParser):
    """HTML에서 섹션 구조 추출"""

    def __init__(self):
        super().__init__()
        self.sections: list[dict] = []
        self.current_heading: dict | None = None
        self.in_heading = False
        self.heading_text = ""

    def handle_starttag(self, tag, attrs):
        if tag in ("h1", "h2", "h3", "h4"):
            self.in_heading = True
            self.current_heading = {"level": int(tag[1]), "title": ""}
            self.heading_text = ""

    def handle_endtag(self, tag):
        if tag in ("h1", "h2", "h3", "h4") and self.current_heading:
            self.current_heading["title"] = self.heading_text.strip()
            self.sections.append(self.current_heading)
            self.in_heading = False
            self.current_heading = None

    def handle_data(self, data):
        if self.in_heading:
            self.heading_text += data


class PlainTextExtractor(HTMLParser):
    """HTML에서 plain text 추출"""

    def __init__(self):
        super().__init__()
        self.text_parts = []
        self.in_script = False
        self.in_style = False

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            self.in_script = True
        elif tag == "style":
            self.in_style = True
        elif tag in ("br", "p", "div", "tr", "li", "h1", "h2", "h3", "h4"):
            self.text_parts.append("\n")

    def handle_endtag(self, tag):
        if tag == "script":
            self.in_script = False
        elif tag == "style":
            self.in_style = False

    def handle_data(self, data):
        if not self.in_script and not self.in_style:
            self.text_parts.append(data)

    def get_text(self) -> str:
        text = "".join(self.text_parts).strip()
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = re.sub(r" {2,}", " ", text)
        return text


class CodeBlockExtractor(HTMLParser):
    """HTML에서 코드 블록 추출"""

    def __init__(self):
        super().__init__()
        self.code_blocks: list[dict] = []
        self.current_block: dict | None = None
        self.in_code = False
        self.code_content = ""

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        # Confluence 코드 매크로 패턴
        if tag == "ac:structured-macro" and attrs_dict.get("ac:name") == "code":
            self.current_block = {"language": None, "content": ""}
        elif tag == "ac:parameter" and self.current_block is not None:
            if attrs_dict.get("ac:name") == "language":
                pass  # handle_data에서 언어 추출
        elif tag == "ac:plain-text-body" and self.current_block is not None:
            self.in_code = True
            self.code_content = ""
        # 일반 pre/code 태그
        elif tag == "pre":
            self.current_block = {"language": None, "content": ""}
            self.in_code = True
            self.code_content = ""
        elif tag == "code" and self.current_block is None:
            # standalone code 태그
            lang = attrs_dict.get("class", "").replace("language-", "")
            self.current_block = {"language": lang if lang else None, "content": ""}
            self.in_code = True
            self.code_content = ""

    def handle_endtag(self, tag):
        if tag in ("ac:plain-text-body", "pre", "code") and self.current_block is not None:
            self.current_block["content"] = self.code_content.strip()
            if self.current_block["content"]:  # 빈 코드 블록 제외
                self.code_blocks.append(self.current_block)
            self.current_block = None
            self.in_code = False

    def handle_data(self, data):
        if self.in_code:
            self.code_content += data


@dataclass
class ExtractedCodeBlock:
    """추출된 코드 블록"""
    language: str | None
    content: str
    section: str | None = None
