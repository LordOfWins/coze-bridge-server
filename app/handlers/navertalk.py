"""
네이버톡톡 웹훅 서버 핸들러 — Task 6에서 상세 구현
"""
from app.handlers.base import BaseMessageHandler
from typing import Any


class NaverTalkHandler(BaseMessageHandler):
    """네이버톡톡 챗봇 API 핸들러"""

    async def parse_request(self, raw_request: dict) -> dict:
        raise NotImplementedError("Task 6에서 구현")

    async def call_coze(self, parsed: dict) -> dict:
        raise NotImplementedError("Task 6에서 구현")

    async def format_response(self, coze_result: dict, parsed: dict) -> Any:
        raise NotImplementedError("Task 6에서 구현")

    async def handle_timeout(self, parsed: dict) -> Any:
        raise NotImplementedError("Task 6에서 구현")
