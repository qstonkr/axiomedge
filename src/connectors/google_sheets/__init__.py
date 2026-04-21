"""Google Sheets connector — Sheets API v4.

Drive 의 spreadsheet 와 별도 카드 — 행 단위 의미 (header + values) 보존이
중요한 데이터셋용. 사용자가 spreadsheet_id list 입력 → 각 sheet (worksheet)
의 grid → markdown table 으로 변환 → 1 sheet = 1 RawDocument.
"""

from .config import GoogleSheetsConnectorConfig
from .connector import GoogleSheetsConnector

__all__ = ["GoogleSheetsConnector", "GoogleSheetsConnectorConfig"]
