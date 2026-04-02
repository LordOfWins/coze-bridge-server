"""
네이버톡톡 웹훅 서버 핸들러

담당 역할:
- 네이버톡톡 웹훅 이벤트(open/send/leave/friend) 수신 및 파싱
- send 이벤트일 때만 Coze API 호출하여 봇 응답 생성
- Coze 응답을 네이버톡톡 textContent/compositeContent 포맷으로 변환
- 5초 Read Timeout 초과 시 즉시 200 OK 반환 후 보내기 API로 비동기 응답

네이버톡톡 API 스펙 참고:
- https://github.com/navertalk/chatbot-api

웹훅 요구사항:
- TLS 필수
- Read Timeout: 5초 (5초 내 응답 못하면 네이버가 연결 끊음)
- ACL IP: 211.249.40.0/27, 211.249.68.0/27, 220.230.168.0/27

보내기 API (비동기 응답용):
- POST https://gw.talk.naver.com/chatbot/v1/event
- Authorization 헤더에 인증 토큰 필수
- Content-Type: application/json;charset=UTF-8

이벤트 종류:
- open: 사용자가 채팅창 진입 (환영 메시지 가능)
- send: 사용자가 메시지 전송 (텍스트/이미지/compositeContent)
- leave: 사용자가 채팅방 나감 (응답 불가)
- friend: 친구 추가/해제
- echo: 봇이 보낸 메시지의 에코 (무시)
- action: 타이핑 표시 등 (무시)
- persistentMenu: 고정 메뉴 설정
"""
import asyncio
from typing import Any, Optional

import httpx

from app.handlers.base import BaseMessageHandler
from app.modules.coze_client import CozeClient
from app.config.logging import logger


# --- 네이버톡톡 보내기 API URL ---
NAVER_TALK_SEND_API = "https://gw.talk.naver.com/chatbot/v1/event"


class NaverTalkHandler(BaseMessageHandler):
    """
    네이버톡톡 웹훅 핸들러

    생성 시 CozeClient + 네이버톡톡 인증 토큰을 주입받아 사용
    멀티 고객사 환경에서는 고객사별로 다른 설정이 주입됨
    """

    def __init__(self, coze_client: CozeClient, naver_talk_token: str):
        """
        Args:
            coze_client: 해당 고객사용 Coze API 클라이언트
            naver_talk_token: 네이버톡톡 보내기 API 인증 토큰
        """
        self._coze = coze_client
        self._token = naver_talk_token

    # =========================================================================
    # 1. 요청 파싱 — 네이버톡톡 웹훅 이벤트 -> 공통 내부 포맷
    # =========================================================================

    async def parse_request(self, raw_request: dict) -> dict:
        """
        네이버톡톡 웹훅 이벤트에서 필요한 정보를 추출

        네이버톡톡 웹훅 기본 구조:
        {
            "event": "send",                    # 이벤트 종류
            "user": "al-2eGuGr5WQOnco1_V-FQ",   # 사용자 고유 식별자 (암호화됨)
            "textContent": {"text": "안녕"},      # 텍스트 메시지 (send 이벤트)
            "imageContent": {...},               # 이미지 메시지 (send 이벤트)
            "compositeContent": {...},           # 복합 메시지 (send 이벤트)
            "options": {...}                     # 이벤트별 옵션
        }

        Returns:
            {
                "event": str,          # 이벤트 종류 (open/send/leave/friend/echo/action)
                "user_id": str,        # 네이버톡톡 사용자 ID (Coze user_id로 사용)
                "message": str,        # 사용자 메시지 텍스트 (send 이벤트에서만)
                "input_type": str,     # 입력 타입 (typing/button/sticker 등)
                "options": dict,       # 이벤트 옵션 (open의 inflow 등)
                "raw": dict,           # 원본 요청 전체 (디버깅용)
            }
        """
        event = raw_request.get("event", "")
        user_id = raw_request.get("user", "unknown")
        options = raw_request.get("options", {})

        # send 이벤트에서 메시지 텍스트 추출
        message = ""
        input_type = ""

        if event == "send":
            # textContent에서 텍스트 추출
            text_content = raw_request.get("textContent", {})
            if text_content:
                message = text_content.get("text", "")
                input_type = text_content.get("inputType", "typing")

            # imageContent인 경우 -> 이미지 URL을 메시지로 전달
            image_content = raw_request.get("imageContent", {})
            if not message and image_content:
                message = image_content.get("imageUrl", "")
                input_type = "image"

            # compositeContent인 경우 -> 버튼 code 값을 메시지로 전달
            # (사용자가 카드 버튼을 클릭한 경우 textContent.code로 옴)
            if not message and text_content:
                code = text_content.get("code", "")
                if code:
                    message = code
                    input_type = "button"

        logger.info(
            f"네이버톡톡 요청 파싱 완료 "
            f"event={event} "
            f"user_id={user_id[:10]}... "
            f"message={message[:50] if message else '(없음)'} "
            f"input_type={input_type}"
        )

        return {
            "event": event,
            "user_id": user_id,
            "message": message,
            "input_type": input_type,
            "options": options,
            "raw": raw_request,
        }

    # =========================================================================
    # 2. Coze API 호출 — 공용 모듈 사용
    # =========================================================================

    async def call_coze(self, parsed: dict) -> dict:
        """
        Coze API를 호출하여 봇 응답을 받아옴

        CozeClient.chat()이 내부적으로 타임아웃(4.5초) 관리:
        - 4.5초 내 완료 -> success=True + 텍스트/카드 응답
        - 4.5초 초과 -> timed_out=True + chat_id/conversation_id

        Args:
            parsed: parse_request()의 반환값

        Returns:
            CozeClient.chat()의 반환값 (dict)
        """
        result = await self._coze.chat(
            user_id=parsed["user_id"],
            message=parsed["message"],
        )

        logger.info(
            f"Coze 호출 결과 (네이버톡톡) "
            f"success={result['success']} "
            f"timed_out={result['timed_out']} "
            f"has_text={'Y' if result['text'] else 'N'} "
            f"cards={len(result['cards'])}"
        )

        return result

    # =========================================================================
    # 3. 응답 포맷팅 — Coze 결과 -> 네이버톡톡 응답 포맷
    # =========================================================================

    async def format_response(self, coze_result: dict, parsed: dict) -> dict:
        """
        Coze 응답을 네이버톡톡 응답 포맷으로 변환

        네이버톡톡 웹훅 응답 구조 (동기 — 5초 내):
        {
            "event": "send",
            "textContent": {"text": "봇 응답 메시지"}
        }

        또는 카드형:
        {
            "event": "send",
            "compositeContent": {"compositeList": [...]}
        }

        Args:
            coze_result: Coze API 응답 (call_coze의 반환값)
            parsed: 파싱된 요청 정보

        Returns:
            네이버톡톡 응답 dict
        """
        # --- 에러 발생 시 안전한 에러 메시지 반환 ---
        if not coze_result["success"] and not coze_result["timed_out"]:
            return self._text_response("죄송합니다 일시적인 오류가 발생했습니다")

        # --- 카드형 응답이 있는 경우 -> Task 7의 카드 모듈 호출 ---
        if coze_result.get("cards"):
            try:
                from app.cards.navertalk_card import build_navertalk_card_response
                card_response = build_navertalk_card_response(coze_result["cards"])
                if card_response:
                    return card_response
            except ImportError:
                # Task 7 미구현 시 -> 카드 정보를 텍스트로 변환하여 폴백
                logger.warning("네이버톡톡 카드 모듈 미구현 -> 텍스트 폴백")

            # 카드 모듈 실패 또는 미구현 시 텍스트 폴백
            fallback_text = self._cards_to_text_fallback(coze_result["cards"])
            text = coze_result.get("text", "")
            if text and fallback_text:
                text = f"{text}\n\n{fallback_text}"
            elif fallback_text:
                text = fallback_text

            if text:
                return self._text_response(text)

        # --- 텍스트 응답 ---
        text = coze_result.get("text", "")
        if not text:
            text = "죄송합니다 응답을 생성하지 못했습니다"

        return self._text_response(text)

    # =========================================================================
    # 4. 타임아웃 처리 — 5초 초과 시 빈 200 OK 반환
    # =========================================================================

    async def handle_timeout(self, parsed: dict) -> dict:
        """
        5초 타임아웃 초과 시 즉시 빈 200 OK 반환

        네이버톡톡은 카카오와 달리 useCallback 같은 메커니즘이 없음
        -> 5초 내 응답 못하면 연결이 끊김
        -> 즉시 200 OK를 반환한 뒤 보내기 API로 비동기 응답 전송

        Returns:
            빈 dict (200 OK만 반환하면 됨)
        """
        return {}

    # =========================================================================
    # 5. 메인 파이프라인 오버라이드 — 이벤트 분기 + 타임아웃 처리
    # =========================================================================

    async def handle(self, raw_request: dict) -> dict:
        """
        네이버톡톡 웹훅 이벤트 처리 메인 파이프라인

        처리 흐름:
        1. 이벤트 파싱
        2. 이벤트 종류별 분기 처리
           - open: 환영 메시지 (동기 응답)
           - send: Coze API 호출 -> 응답 반환 (동기 또는 비동기)
           - leave: 빈 200 OK (응답 불가)
           - friend: 친구 추가/해제 메시지 (동기 응답)
           - echo/action: 무시 (빈 200 OK)
        3. send 이벤트 처리 시 4.5초 타임아웃 적용
           3-A. 4.5초 내 완료 -> 즉시 응답
           3-B. 4.5초 초과 -> 빈 200 OK + 백그라운드 보내기 API
        """
        # --- Step 1: 이벤트 파싱 ---
        parsed = await self.parse_request(raw_request)
        event = parsed["event"]

        # --- Step 2: 이벤트 종류별 분기 ---

        # open 이벤트 — 채팅창 진입
        if event == "open":
            return self._handle_open(parsed)

        # leave 이벤트 — 채팅방 나감 (응답 불필요)
        if event == "leave":
            logger.info(f"네이버톡톡 leave 이벤트 user={parsed['user_id'][:10]}...")
            return {}

        # friend 이벤트 — 친구 추가/해제
        if event == "friend":
            return self._handle_friend(parsed)

        # echo 이벤트 — 봇이 보낸 메시지의 에코 (무시)
        if event == "echo":
            return {}

        # action 이벤트 — 타이핑 표시 등 (무시)
        if event == "action":
            return {}

        # persistentMenu 이벤트 — 고정 메뉴 (무시)
        if event == "persistentMenu":
            return {}

        # send 이벤트가 아니면 빈 200 반환
        if event != "send":
            logger.warning(f"네이버톡톡 미지원 이벤트: {event}")
            return {}

        # --- Step 3: send 이벤트 처리 ---

        # 빈 메시지 검증
        if not parsed["message"].strip():
            logger.info("네이버톡톡 빈 메시지 수신 -> 무시")
            return {}

        # Coze API 호출
        coze_result = await self.call_coze(parsed)

        # --- Step 3-A: 정상 응답 (타임아웃 없음) ---
        if coze_result["success"] and not coze_result["timed_out"]:
            return await self.format_response(coze_result, parsed)

        # --- Step 3-B: 타임아웃 -> 빈 200 OK + 백그라운드 비동기 응답 ---
        if coze_result["timed_out"]:
            logger.info(
                f"네이버톡톡 타임아웃 -> 비동기 모드 진입 "
                f"chat_id={coze_result['chat_id']} "
                f"user={parsed['user_id'][:10]}..."
            )

            # 백그라운드에서 Coze 폴링 -> 보내기 API로 응답 전송
            asyncio.create_task(
                self._async_send(
                    user_id=parsed["user_id"],
                    chat_id=coze_result["chat_id"],
                    conversation_id=coze_result["conversation_id"],
                    parsed=parsed,
                )
            )

            # 즉시 빈 200 OK 반환 (네이버 Read Timeout 전에 응답)
            return await self.handle_timeout(parsed)

        # --- 기타 에러 ---
        error_msg = coze_result.get("error", "알 수 없는 오류")
        logger.error(f"네이버톡톡 Coze 호출 실패: {error_msg}")
        return self._text_response("죄송합니다 일시적인 오류가 발생했습니다")

    # =========================================================================
    # 이벤트 핸들러 — open / friend
    # =========================================================================

    def _handle_open(self, parsed: dict) -> dict:
        """
        open 이벤트 처리 — 채팅창 진입 시 환영 메시지

        open 이벤트 options:
        - inflow: "list" (목록) / "button" (버튼) / "none" (직접 URL)
        - referer: 유입 경로 URL
        - friend: 친구 여부 (bool)
        - under14: 14세 미만 여부 (bool)

        Args:
            parsed: 파싱된 요청 정보

        Returns:
            네이버톡톡 응답 dict (환영 메시지)
        """
        options = parsed.get("options", {})
        inflow = options.get("inflow", "none")

        logger.info(
            f"네이버톡톡 open 이벤트 "
            f"user={parsed['user_id'][:10]}... "
            f"inflow={inflow}"
        )

        # 유입 경로별 환영 메시지 (필요 시 커스터마이징 가능)
        welcome_text = "안녕하세요! 무엇을 도와드릴까요?"

        return self._text_response(welcome_text)

    def _handle_friend(self, parsed: dict) -> dict:
        """
        friend 이벤트 처리 — 친구 추가/해제

        friend 이벤트 options:
        - set: "on" (추가) / "off" (해제)

        Args:
            parsed: 파싱된 요청 정보

        Returns:
            네이버톡톡 응답 dict
        """
        options = parsed.get("options", {})
        friend_set = options.get("set", "")

        logger.info(
            f"네이버톡톡 friend 이벤트 "
            f"user={parsed['user_id'][:10]}... "
            f"set={friend_set}"
        )

        if friend_set == "on":
            return self._text_response("친구 추가 감사합니다! 무엇이든 물어보세요")
        elif friend_set == "off":
            # 친구 해제 시에도 응답 가능하지만 보통 무시
            return {}

        return {}

    # =========================================================================
    # 비동기 보내기 — 백그라운드에서 Coze 폴링 후 보내기 API 전송
    # =========================================================================

    async def _async_send(
        self,
        user_id: str,
        chat_id: str,
        conversation_id: str,
        parsed: dict,
    ) -> None:
        """
        백그라운드 태스크: Coze 폴링 완료 후 네이버톡톡 보내기 API로 응답 전송

        보내기 API:
        - URL: https://gw.talk.naver.com/chatbot/v1/event
        - Method: POST
        - Headers:
            Content-Type: application/json;charset=UTF-8
            Authorization: {인증 토큰}
        - Body:
            {
                "event": "send",
                "user": "사용자ID",
                "textContent": {"text": "응답 메시지"}
            }

        Args:
            user_id: 네이버톡톡 사용자 ID
            chat_id: Coze chat ID (폴링용)
            conversation_id: Coze conversation ID (폴링용)
            parsed: 파싱된 요청 정보
        """
        try:
            logger.info(f"네이버톡톡 비동기 전송 시작 chat_id={chat_id}")

            # Coze 폴링 — 최대 55초 대기
            coze_result = await self._coze.poll_and_get_result(
                chat_id=chat_id,
                conversation_id=conversation_id,
                max_wait=55.0,
                poll_interval=1.0,
            )

            # Coze 응답을 네이버톡톡 포맷으로 변환
            if coze_result["success"]:
                response_body = await self.format_response(coze_result, parsed)
            else:
                error_msg = coze_result.get("error", "응답 생성에 실패했습니다")
                logger.error(f"네이버톡톡 비동기 Coze 폴링 실패: {error_msg}")
                response_body = self._text_response(
                    "죄송합니다 응답 생성에 실패했습니다 다시 시도해주세요"
                )

            # 보내기 API용 페이로드 구성
            # response_body에는 event + textContent/compositeContent가 들어있음
            # 여기에 user 필드를 추가해야 함
            send_payload = {**response_body, "user": user_id}

            # 보내기 API 호출
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    NAVER_TALK_SEND_API,
                    json=send_payload,
                    headers={
                        "Content-Type": "application/json;charset=UTF-8",
                        "Authorization": self._token,
                    },
                )

            logger.info(
                f"네이버톡톡 보내기 API 전송 완료 "
                f"status={resp.status_code} "
                f"chat_id={chat_id}"
            )

            # 응답 확인
            if resp.status_code == 200:
                try:
                    result = resp.json()
                    if not result.get("success", False):
                        logger.warning(
                            f"네이버톡톡 보내기 API 비정상 응답 "
                            f"code={result.get('resultCode')} "
                            f"msg={result.get('resultMessage', '')}"
                        )
                except Exception:
                    pass  # 응답 파싱 실패는 무시
            else:
                logger.error(
                    f"네이버톡톡 보내기 API HTTP 에러 "
                    f"status={resp.status_code} "
                    f"body={resp.text[:200]}"
                )

        except httpx.TimeoutException:
            logger.error(f"네이버톡톡 보내기 API HTTP 타임아웃 chat_id={chat_id}")
        except Exception as e:
            logger.error(
                f"네이버톡톡 비동기 전송 예외: "
                f"{type(e).__name__}: {str(e)} "
                f"chat_id={chat_id}"
            )

    # =========================================================================
    # 동기 보내기 — 웹훅 응답 외에 추가 메시지 전송이 필요할 때
    # =========================================================================

    async def send_message(self, user_id: str, text: str) -> bool:
        """
        네이버톡톡 보내기 API를 직접 호출하여 메시지 전송

        웹훅 응답과 별개로 능동적 메시지 전송이 필요할 때 사용
        예: 환영 메시지 추가 전송 / 알림 발송 등

        Args:
            user_id: 네이버톡톡 사용자 ID
            text: 전송할 메시지 텍스트

        Returns:
            전송 성공 여부
        """
        payload = {
            "event": "send",
            "user": user_id,
            "textContent": {"text": text},
        }

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    NAVER_TALK_SEND_API,
                    json=payload,
                    headers={
                        "Content-Type": "application/json;charset=UTF-8",
                        "Authorization": self._token,
                    },
                )

            if resp.status_code == 200:
                result = resp.json()
                return result.get("success", False)

            logger.error(f"보내기 API 실패 status={resp.status_code}")
            return False

        except Exception as e:
            logger.error(f"보내기 API 예외: {type(e).__name__}: {str(e)}")
            return False

    # =========================================================================
    # 헬퍼 메서드 — 네이버톡톡 응답 생성
    # =========================================================================

    @staticmethod
    def _text_response(text: str) -> dict:
        """
        네이버톡톡 textContent 응답 생성

        네이버톡톡 텍스트 응답 구조:
        {
            "event": "send",
            "textContent": {"text": "봇 응답 메시지"}
        }

        텍스트에 \\n 사용 가능 (줄바꿈 지원)
        URL은 자동으로 링크 처리됨

        Args:
            text: 응답 텍스트

        Returns:
            네이버톡톡 응답 dict
        """
        # 빈 텍스트 방어
        if not text or not text.strip():
            text = "죄송합니다 응답을 생성하지 못했습니다"

        return {
            "event": "send",
            "textContent": {
                "text": text,
            },
        }

    @staticmethod
    def _error_response(message: str) -> dict:
        """
        에러 상황에서 네이버톡톡 정규 포맷 에러 응답 생성

        에러 시에도 반드시 유효한 응답을 반환해야
        사용자에게 적절한 메시지가 전달됨

        Args:
            message: 사용자에게 보여줄 에러 메시지

        Returns:
            네이버톡톡 응답 dict
        """
        return {
            "event": "send",
            "textContent": {
                "text": message,
            },
        }

    @staticmethod
    def _cards_to_text_fallback(cards: list) -> str:
        """
        카드 모듈이 없을 때 카드 데이터를 텍스트로 변환하는 폴백

        Task 7(네이버톡톡 카드 모듈) 구현 전까지 임시로 사용
        카드의 주요 필드를 줄바꿈으로 나열

        Args:
            cards: Coze에서 파싱된 카드 데이터 리스트

        Returns:
            텍스트 형태로 변환된 카드 정보
        """
        lines = []
        for i, card in enumerate(cards, 1):
            parts = []
            # 상품명 또는 제목
            name = card.get("product_name") or card.get("title", "")
            if name:
                parts.append(f"[{name}]")
            # 설명
            desc = card.get("description", "")
            if desc:
                parts.append(desc)
            # 가격
            price = card.get("price", "")
            if price:
                parts.append(f"가격: {price}")
            # URL
            url = card.get("button_url", "")
            if url:
                parts.append(url)

            if parts:
                lines.append(f"{i}. " + " / ".join(parts))

        return "\n".join(lines)
