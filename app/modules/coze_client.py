"""
Coze API v3 공용 클라이언트 모듈
- 모든 채널 핸들러가 공유하는 Coze API 호출 로직
- Non-streaming 방식: POST /v3/chat (stream=false) -> 폴링 -> 메시지 조회
- 5초 타임아웃 내에 응답 완료 여부 판단
- 타임아웃 초과 시 백그라운드 태스크로 폴링 계속

참고 문서:
- Coze API v3 Chat: https://www.coze.com/open/docs/developer_guides/chat_v3
- Coze Python SDK: https://github.com/coze-dev/coze-py/blob/main/examples/chat_no_stream.py
"""
import asyncio
import time
import json
from typing import Optional
import httpx

from app.config.logging import logger
from app.config.settings import get_settings


# --- Coze API 응답 상태 상수 ---
class ChatStatus:
    """Coze Chat API 상태값 상수"""
    CREATED = "created"            # 대화 생성됨
    IN_PROGRESS = "in_progress"    # 처리 중
    COMPLETED = "completed"        # 완료
    FAILED = "failed"              # 실패
    REQUIRES_ACTION = "requires_action"  # 도구 실행 필요 (이 프로젝트에서는 미사용)


class CozeClient:
    """
    Coze v3 Chat API 비동기 클라이언트

    사용 흐름:
    1. chat() 호출 -> 대화 생성 + 폴링 + 메시지 조회 (5초 타임아웃 적용)
    2. 타임아웃 초과 시 -> chat_id/conversation_id 반환 -> 호출자가 백그라운드에서 poll_and_get_result() 사용
    3. 응답 파싱 -> type="answer" 메시지에서 텍스트/카드 데이터 추출
    """

    def __init__(
        self,
        bot_id: str,
        pat: str,
        api_base: str = "https://api.coze.com",
        timeout_seconds: float = 4.5,  # 카카오/네이버 5초 제한 고려하여 4.5초로 설정
    ):
        """
        Args:
            bot_id: Coze 봇 ID
            pat: Personal Access Token
            api_base: Coze API 기본 URL
            timeout_seconds: 동기 응답 대기 최대 시간 (초)
        """
        self.bot_id = bot_id
        self.pat = pat
        self.api_base = api_base.rstrip("/")
        self.timeout_seconds = timeout_seconds

        # 공용 HTTP 헤더 (Authorization에 PAT 포함)
        self._headers = {
            "Authorization": f"Bearer {pat}",
            "Content-Type": "application/json",
        }

    async def chat(
        self,
        user_id: str,
        message: str,
        conversation_id: Optional[str] = None,
    ) -> dict:
        """
        Coze 봇에게 메시지를 보내고 응답을 받아옴 (타임아웃 내)

        Args:
            user_id: 사용자 식별자 (채널별 유저 ID)
            message: 사용자 메시지 텍스트
            conversation_id: 기존 대화 ID (없으면 새 대화 생성)

        Returns:
            {
                "success": bool,           # 성공 여부
                "text": str,               # 봇 텍스트 응답
                "cards": list[dict],       # 카드형 응답 (JSON 파싱된 상품 데이터)
                "timed_out": bool,         # 타임아웃 여부
                "chat_id": str,            # Coze chat ID (비동기 폴링용)
                "conversation_id": str,    # Coze conversation ID (비동기 폴링용)
                "error": str | None,       # 에러 메시지
            }
        """
        start_time = time.monotonic()

        try:
            # --- Step 1: 대화 생성 (POST /v3/chat) ---
            chat_data = await self._create_chat(user_id, message, conversation_id)

            if not chat_data:
                return self._error_result("Coze 대화 생성 실패")

            chat_id = chat_data.get("id", "")
            conv_id = chat_data.get("conversation_id", "")

            logger.info(f"Coze 대화 생성 완료 chat_id={chat_id}")

            # --- Step 2: 폴링으로 완료 대기 (남은 시간 내에서) ---
            elapsed = time.monotonic() - start_time
            remaining = self.timeout_seconds - elapsed

            if remaining <= 0:
                # 대화 생성만으로 타임아웃 소진
                return self._timeout_result(chat_id, conv_id)

            completed = await self._poll_until_complete(chat_id, conv_id, remaining)

            if not completed:
                # 폴링 중 타임아웃 -> 비동기 처리 필요
                return self._timeout_result(chat_id, conv_id)

            # --- Step 3: 메시지 목록 조회 ---
            messages = await self._get_messages(chat_id, conv_id)
            return self._parse_messages(messages, chat_id, conv_id)

        except httpx.TimeoutException:
            logger.warning("Coze API HTTP 타임아웃 발생")
            return self._error_result("Coze API 타임아웃")
        except Exception as e:
            logger.error(f"Coze API 호출 중 예외: {type(e).__name__}: {str(e)}")
            return self._error_result(f"Coze API 오류: {type(e).__name__}")

    async def poll_and_get_result(
        self,
        chat_id: str,
        conversation_id: str,
        max_wait: float = 55.0,
        poll_interval: float = 1.0,
    ) -> dict:
        """
        백그라운드에서 Coze 응답을 폴링하여 결과를 가져옴
        - 타임아웃 초과 후 비동기 응답 시 사용
        - 최대 55초까지 대기 (Coze 봇 처리 시간 고려)

        Args:
            chat_id: Coze chat ID
            conversation_id: Coze conversation ID
            max_wait: 최대 대기 시간 (초)
            poll_interval: 폴링 간격 (초)

        Returns:
            chat() 메서드와 동일한 형태의 결과 dict
        """
        try:
            completed = await self._poll_until_complete(
                chat_id, conversation_id, max_wait, poll_interval
            )

            if not completed:
                return self._error_result("Coze 봇 응답 시간 초과 (최대 대기 시간 경과)")

            messages = await self._get_messages(chat_id, conversation_id)
            return self._parse_messages(messages, chat_id, conversation_id)

        except Exception as e:
            logger.error(f"비동기 폴링 중 예외: {type(e).__name__}: {str(e)}")
            return self._error_result(f"비동기 폴링 오류: {type(e).__name__}")

    # =========================================================================
    # 내부 메서드 — 외부에서 직접 호출하지 않음
    # =========================================================================

    async def _create_chat(
        self,
        user_id: str,
        message: str,
        conversation_id: Optional[str] = None,
    ) -> Optional[dict]:
        """
        Coze v3 Chat API로 대화 생성 (Non-streaming)

        POST /v3/chat
        - stream=false: 즉시 chat metadata 반환 (봇 응답은 별도 폴링으로 조회)
        - auto_save_history=true: 대화 히스토리 자동 저장
        """
        url = f"{self.api_base}/v3/chat"

        # 요청 바디 구성
        body = {
            "bot_id": self.bot_id,
            "user_id": user_id,
            "stream": False,
            "auto_save_history": True,
            "additional_messages": [
                {
                    "role": "user",
                    "content": message,
                    "content_type": "text",
                }
            ],
        }

        # conversation_id가 있으면 쿼리 파라미터로 전달
        params = {}
        if conversation_id:
            params["conversation_id"] = conversation_id

        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                url,
                headers=self._headers,
                json=body,
                params=params,
            )

        logger.info(f"Coze /v3/chat 응답 status={response.status_code}")

        if response.status_code != 200:
            logger.error(f"Coze API 에러 status={response.status_code} body={response.text[:200]}")
            return None

        result = response.json()

        # API 응답 코드 확인 (0 = 성공)
        if result.get("code", -1) != 0:
            logger.error(f"Coze API 비즈니스 에러 code={result.get('code')} msg={result.get('msg')}")
            return None

        return result.get("data", {})

    async def _poll_until_complete(
        self,
        chat_id: str,
        conversation_id: str,
        max_wait: float,
        poll_interval: float = 0.5,
    ) -> bool:
        """
        GET /v3/chat/retrieve를 주기적으로 호출하여 대화 완료 여부 확인

        Args:
            chat_id: Coze chat ID
            conversation_id: Coze conversation ID
            max_wait: 최대 대기 시간 (초)
            poll_interval: 폴링 간격 (초)

        Returns:
            True = 완료됨 / False = 타임아웃 (아직 처리 중)
        """
        url = f"{self.api_base}/v3/chat/retrieve"
        params = {
            "chat_id": chat_id,
            "conversation_id": conversation_id,
        }

        start = time.monotonic()

        async with httpx.AsyncClient(timeout=5.0) as client:
            while (time.monotonic() - start) < max_wait:
                try:
                    response = await client.get(
                        url,
                        headers=self._headers,
                        params=params,
                    )

                    if response.status_code == 200:
                        data = response.json().get("data", {})
                        status = data.get("status", "")

                        if status == ChatStatus.COMPLETED:
                            logger.info(f"Coze 대화 완료 chat_id={chat_id}")
                            return True
                        elif status == ChatStatus.FAILED:
                            error_msg = data.get("last_error", {}).get("msg", "알 수 없는 오류")
                            logger.error(f"Coze 대화 실패 chat_id={chat_id} error={error_msg}")
                            return True  # 실패도 "완료"로 처리 (메시지 조회에서 에러 확인)

                except httpx.TimeoutException:
                    logger.warning(f"폴링 HTTP 타임아웃 chat_id={chat_id}")

                # 다음 폴링까지 대기
                await asyncio.sleep(poll_interval)

        logger.warning(f"폴링 시간 초과 chat_id={chat_id} max_wait={max_wait}s")
        return False

    async def _get_messages(
        self,
        chat_id: str,
        conversation_id: str,
    ) -> list:
        """
        대화의 메시지 목록 조회

        GET /v3/chat/message/list
        - chat_id로 필터링하여 해당 대화의 메시지만 조회
        """
        url = f"{self.api_base}/v3/chat/message/list"
        params = {
            "chat_id": chat_id,
            "conversation_id": conversation_id,
        }

        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(
                url,
                headers=self._headers,
                params=params,
            )

        if response.status_code != 200:
            logger.error(f"메시지 조회 실패 status={response.status_code}")
            return []

        result = response.json()
        return result.get("data", [])

    def _parse_messages(self, messages: list, chat_id: str, conversation_id: str) -> dict:
        """
        Coze 메시지 목록에서 봇 응답(type=answer)을 추출

        카드형 응답 감지 로직:
        - content가 JSON 형태이고 상품 정보 키(image_url/product_name 등)를 포함하면 카드로 분류
        - 여러 상품이 배열로 오면 카드 목록으로 처리
        """
        text_parts = []  # 텍스트 응답 수집
        cards = []       # 카드형 응답 수집

        for msg in messages:
            # answer 타입 + assistant 역할만 처리
            if msg.get("type") != "answer" or msg.get("role") != "assistant":
                continue

            content = msg.get("content", "")
            content_type = msg.get("content_type", "text")

            # JSON 형태의 카드 응답인지 확인
            parsed_cards = self._try_parse_cards(content)
            if parsed_cards:
                cards.extend(parsed_cards)
            else:
                # 일반 텍스트 응답
                if content.strip():
                    text_parts.append(content.strip())

        return {
            "success": True,
            "text": "\n".join(text_parts) if text_parts else "",
            "cards": cards,
            "timed_out": False,
            "chat_id": chat_id,
            "conversation_id": conversation_id,
            "error": None,
        }

    def _try_parse_cards(self, content: str) -> list:
        """
        Coze 응답에서 카드형(상품) 데이터를 JSON 파싱 시도

        Coze 봇이 상품 정보를 JSON으로 반환하는 경우:
        - 단일 상품: {"image_url": "...", "product_name": "...", ...}
        - 다중 상품: [{"image_url": "...", ...}, {"image_url": "...", ...}]
        - 래핑된 형태: {"products": [...]} 또는 {"items": [...]}

        카드 필수 키: image_url 또는 product_name 중 하나 이상 포함
        """
        content = content.strip()
        if not content:
            return []

        # JSON 파싱 시도
        try:
            data = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            return []

        # 카드 데이터 판별용 키 목록
        card_keys = {"image_url", "product_name", "title", "description", "price", "button_url"}

        # 배열인 경우 -> 각 요소가 카드인지 확인
        if isinstance(data, list):
            cards = [item for item in data if isinstance(item, dict) and card_keys & set(item.keys())]
            return cards if cards else []

        # 딕셔너리인 경우
        if isinstance(data, dict):
            # 래핑된 형태 확인 (products / items 키)
            for wrapper_key in ("products", "items", "data", "cards"):
                if wrapper_key in data and isinstance(data[wrapper_key], list):
                    cards = [
                        item for item in data[wrapper_key]
                        if isinstance(item, dict) and card_keys & set(item.keys())
                    ]
                    if cards:
                        return cards

            # 단일 카드 객체인지 확인
            if card_keys & set(data.keys()):
                return [data]

        return []

    def _timeout_result(self, chat_id: str, conversation_id: str) -> dict:
        """타임아웃 발생 시 반환할 결과 — 호출자가 비동기 처리할 수 있도록 ID 포함"""
        return {
            "success": False,
            "text": "",
            "cards": [],
            "timed_out": True,
            "chat_id": chat_id,
            "conversation_id": conversation_id,
            "error": None,
        }

    def _error_result(self, error_msg: str) -> dict:
        """에러 발생 시 반환할 결과"""
        return {
            "success": False,
            "text": "",
            "cards": [],
            "timed_out": False,
            "chat_id": "",
            "conversation_id": "",
            "error": error_msg,
        }


def get_coze_client(
    bot_id: Optional[str] = None,
    pat: Optional[str] = None,
    api_base: Optional[str] = None,
    timeout: Optional[float] = None,
) -> CozeClient:
    """
    CozeClient 팩토리 함수

    우선순위:
    1. 파라미터로 직접 전달된 값
    2. 환경변수 기본값 (Settings)

    멀티 고객사 환경에서는 get_coze_client_for_client()를 사용

    Args:
        bot_id: Coze 봇 ID (미지정 시 환경변수)
        pat: Coze PAT (미지정 시 환경변수)
        api_base: Coze API URL (미지정 시 환경변수)
        timeout: 응답 대기 타임아웃 (미지정 시 환경변수)

    Returns:
        CozeClient 인스턴스
    """
    settings = get_settings()
    return CozeClient(
        bot_id=bot_id or settings.COZE_BOT_ID,
        pat=pat or settings.COZE_PAT,
        api_base=api_base or settings.COZE_API_BASE,
        timeout_seconds=timeout or settings.COZE_TIMEOUT,
    )


def get_coze_client_for_client(client_key: Optional[str] = None) -> CozeClient:
    """
    멀티 고객사 환경용 CozeClient 팩토리

    clients.json에서 client_key에 해당하는 설정을 찾아 CozeClient 생성
    client_key가 없으면 default 설정 사용
    설정이 없으면 환경변수 폴백으로 생성

    Args:
        client_key: 고객사 고유 키 (URL 경로에서 추출)

    Returns:
        CozeClient 인스턴스

    Raises:
        ValueError: 고객사 설정이 없거나 비활성인 경우
    """
    from app.config.client_config import get_client_config

    config = get_client_config(client_key)

    if config is None:
        raise ValueError(f"고객사 설정을 찾을 수 없습니다: {client_key}")

    if not config.is_valid():
        raise ValueError(f"고객사 설정이 불완전합니다: {client_key} (bot_id 또는 pat 누락)")

    return CozeClient(
        bot_id=config.coze_bot_id,
        pat=config.coze_pat,
        api_base=config.coze_api_base,
        timeout_seconds=config.timeout_seconds,
    )
