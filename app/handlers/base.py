"""
채널 핸들러 추상 베이스 클래스 (ABC)
- 모든 채널 핸들러(카카오/네이버톡톡)가 이 클래스를 상속
- 공통 흐름: parse_request -> call_coze -> format_response
- 5초 타임아웃 초과 시 handle_timeout으로 비동기 처리
"""
from abc import ABC, abstractmethod
from typing import Any


class BaseMessageHandler(ABC):
    """
    채널별 메시지 처리 추상 클래스
    1. parse_request   — 채널별 요청을 공통 포맷으로 파싱
    2. call_coze       — Coze API 호출 (공용 모듈 사용)
    3. format_response — Coze 응답을 채널별 정규 포맷으로 변환
    4. handle_timeout  — 5초 초과 시 채널별 비동기 응답 처리
    """

    @abstractmethod
    async def parse_request(self, raw_request: dict) -> dict:
        """채널별 원본 요청을 공통 내부 포맷으로 변환"""
        pass

    @abstractmethod
    async def call_coze(self, parsed: dict) -> dict:
        """Coze API를 호출하여 봇 응답을 받아옴"""
        pass

    @abstractmethod
    async def format_response(self, coze_result: dict, parsed: dict) -> Any:
        """Coze 응답을 채널별 정규 응답 포맷으로 변환"""
        pass

    @abstractmethod
    async def handle_timeout(self, parsed: dict) -> Any:
        """5초 타임아웃 초과 시 채널별 비동기 응답 처리"""
        pass

    async def handle(self, raw_request: dict) -> Any:
        """
        메시지 처리 메인 파이프라인 (템플릿 메서드 패턴)
        1. 요청 파싱 -> 2. Coze 호출 -> 3. 응답 포맷팅
        """
        parsed = await self.parse_request(raw_request)
        coze_result = await self.call_coze(parsed)
        return await self.format_response(coze_result, parsed)
