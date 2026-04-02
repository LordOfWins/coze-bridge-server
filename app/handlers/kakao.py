"""
카카오 오픈빌더 스킬 서버 핸들러 — Task 4에서 상세 구현
"""
from app.handlers.base import BaseMessageHandler
from typing import Any


class KakaoHandler(BaseMessageHandler):
    """카카오톡 오픈빌더 SkillResponse 핸들러"""

    async def parse_request(self, raw_request: dict) -> dict:
        raise NotImplementedError("Task 4에서 구현")

    async def call_coze(self, parsed: dict) -> dict:
        raise NotImplementedError("Task 4에서 구현")

    async def format_response(self, coze_result: dict, parsed: dict) -> Any:
        raise NotImplementedError("Task 4에서 구현")

    async def handle_timeout(self, parsed: dict) -> Any:
        raise NotImplementedError("Task 4에서 구현")
