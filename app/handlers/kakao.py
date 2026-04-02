"""
카카오 오픈빌더 스킬 서버 핸들러

담당 역할:
- 카카오 오픈빌더의 스킬 요청(SkillPayload)을 파싱
- Coze API를 호출하여 봇 응답을 받아옴
- Coze 응답을 카카오 SkillResponse 정규 포맷으로 변환
- 5초 타임아웃 초과 시 useCallback + callbackUrl 비동기 처리

카카오 SkillResponse 스펙 참고:
- https://kakaobusiness.gitbook.io/main/tool/chatbot/skill_guide/answer_json_format
- https://kakaobusiness.gitbook.io/main/tool/chatbot/skill_guide/ai_chatbot_callback_guide

주요 포맷:
- 일반 응답: {"version": "2.0", "template": {"outputs": [...]}}
- 콜백 응답: {"version": "2.0", "useCallback": true, "data": {"text": "..."}}
- callbackUrl POST: {"version": "2.0", "template": {"outputs": [...]}}
"""
import asyncio
from typing import Any, Optional

import httpx

from app.handlers.base import BaseMessageHandler
from app.modules.coze_client import CozeClient
from app.config.logging import logger


class KakaoHandler(BaseMessageHandler):
    """
    카카오톡 오픈빌더 SkillResponse 핸들러

    생성 시 CozeClient 인스턴스를 주입받아 사용
    멀티 고객사 환경에서는 고객사별로 다른 CozeClient가 주입됨
    """

    def __init__(self, coze_client: CozeClient):
        """
        Args:
            coze_client: 해당 고객사용 Coze API 클라이언트
        """
        self._coze = coze_client

    # =========================================================================
    # 1. 요청 파싱 — 카카오 SkillPayload -> 공통 내부 포맷
    # =========================================================================

    async def parse_request(self, raw_request: dict) -> dict:
        """
        카카오 오픈빌더 SkillPayload에서 필요한 정보를 추출

        카카오 SkillPayload 구조:
        {
            "intent": {...},
            "userRequest": {
                "utterance": "사용자 메시지",
                "user": {"id": "botUserKey", "properties": {"botUserKey": "..."}},
                "callbackUrl": "https://..."  (콜백 활성화 시에만 존재)
            },
            "action": {"params": {...}, "detailParams": {...}},
            "bot": {"id": "...", "name": "..."},
            "contexts": [...]
        }

        Returns:
            {
                "user_id": str,        # 카카오 botUserKey (Coze user_id로 사용)
                "message": str,        # 사용자 발화 텍스트
                "callback_url": str,   # 콜백 URL (없으면 빈 문자열)
                "raw": dict,           # 원본 요청 전체 (디버깅용)
            }
        """
        # userRequest 블록에서 핵심 정보 추출
        user_request = raw_request.get("userRequest", {})
        user_info = user_request.get("user", {})

        # 사용자 ID: botUserKey를 Coze의 user_id로 매핑
        # -> 동일 사용자의 대화 맥락이 Coze에서 유지됨
        user_id = user_info.get("id", "unknown")

        # 사용자 발화 텍스트
        utterance = user_request.get("utterance", "")

        # 콜백 URL: AI 챗봇 콜백이 활성화된 블록에서만 전달됨
        # 없으면 빈 문자열 -> 콜백 불가능 상태로 처리
        callback_url = user_request.get("callbackUrl", "")

        logger.info(
            f"카카오 요청 파싱 완료 "
            f"user_id={user_id} "
            f"utterance={utterance[:50]} "
            f"has_callback={'Y' if callback_url else 'N'}"
        )

        return {
            "user_id": user_id,
            "message": utterance,
            "callback_url": callback_url,
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
        - 4.5초 초과 -> timed_out=True + chat_id/conversation_id (비동기 폴링용)

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
            f"Coze 호출 결과 "
            f"success={result['success']} "
            f"timed_out={result['timed_out']} "
            f"has_text={'Y' if result['text'] else 'N'} "
            f"cards={len(result['cards'])}"
        )

        return result

    # =========================================================================
    # 3. 응답 포맷팅 — Coze 결과 -> 카카오 SkillResponse
    # =========================================================================

    async def format_response(self, coze_result: dict, parsed: dict) -> dict:
        """
        Coze 응답을 카카오 SkillResponse 정규 포맷으로 변환

        카카오 SkillResponse 구조 (공식 스펙):
        {
            "version": "2.0",
            "template": {
                "outputs": [
                    {"simpleText": {"text": "..."}}     # 텍스트 응답
                    또는
                    {"basicCard": {...}}                 # 카드 응답
                    또는
                    {"carousel": {"type": "basicCard", "items": [...]}}  # 캐러셀
                ],
                "quickReplies": [...]  # 바로가기 버튼 (선택)
            }
        }

        Args:
            coze_result: Coze API 응답 (call_coze의 반환값)
            parsed: 파싱된 요청 정보

        Returns:
            카카오 SkillResponse dict
        """
        # --- 에러 발생 시 안전한 에러 메시지 반환 ---
        if not coze_result["success"] and not coze_result["timed_out"]:
            return self._error_response("죄송합니다 일시적인 오류가 발생했습니다")

        # --- 카드형 응답이 있는 경우 -> Task 5의 카드 모듈 호출 ---
        if coze_result.get("cards"):
            # Task 5에서 구현할 카드 모듈을 여기서 호출
            # 지금은 카드 데이터를 텍스트로 폴백 처리
            try:
                from app.cards.kakao_card import build_kakao_card_output
                card_output = build_kakao_card_output(coze_result["cards"])
                return {
                    "version": "2.0",
                    "template": {"outputs": card_output}
                }
            except ImportError:
                # Task 5 미구현 시 -> 카드 정보를 텍스트로 변환하여 폴백
                logger.warning("카카오 카드 모듈 미구현 -> 텍스트 폴백")
                fallback_text = self._cards_to_text_fallback(coze_result["cards"])
                text = coze_result.get("text", "")
                if text and fallback_text:
                    text = f"{text}\n\n{fallback_text}"
                elif fallback_text:
                    text = fallback_text
                return self._text_response(text)

        # --- 텍스트 응답 ---
        text = coze_result.get("text", "")
        if not text:
            text = "죄송합니다 응답을 생성하지 못했습니다"

        return self._text_response(text)

    # =========================================================================
    # 4. 타임아웃 처리 — 5초 초과 시 콜백 응답
    # =========================================================================

    async def handle_timeout(self, parsed: dict) -> dict:
        """
        5초 타임아웃 초과 시 카카오 콜백 응답 반환

        카카오 콜백 응답 스펙:
        {
            "version": "2.0",
            "useCallback": true,
            "data": {"text": "잠시만 기다려주세요"}
        }

        이 응답을 반환한 후 별도로 callbackUrl에 실제 응답을 POST 전송해야 함
        (handle() 메서드에서 백그라운드 태스크로 처리)

        Returns:
            카카오 콜백 SkillResponse dict
        """
        return {
            "version": "2.0",
            "useCallback": True,
            "data": {
                "text": "답변을 생성 중입니다 잠시만 기다려주세요"
            }
        }

    # =========================================================================
    # 5. 메인 파이프라인 오버라이드 — 타임아웃/콜백 분기 로직 포함
    # =========================================================================

    async def handle(self, raw_request: dict) -> dict:
        """
        카카오 스킬 요청 처리 메인 파이프라인

        BaseMessageHandler의 handle()을 오버라이드하여
        타임아웃/콜백 분기 로직을 추가

        처리 흐름:
        1. 요청 파싱
        2. Coze API 호출 (4.5초 타임아웃)
        3-A. 4.5초 내 응답 완료 -> 즉시 SkillResponse 반환
        3-B. 4.5초 초과 + callbackUrl 있음 -> useCallback 응답 + 백그라운드 콜백
        3-C. 4.5초 초과 + callbackUrl 없음 -> 에러 메시지 반환 (콜백 불가)
        """
        # --- Step 1: 요청 파싱 ---
        parsed = await self.parse_request(raw_request)

        # 빈 메시지 검증
        if not parsed["message"].strip():
            return self._text_response("메시지를 입력해주세요")

        # --- Step 2: Coze API 호출 ---
        coze_result = await self.call_coze(parsed)

        # --- Step 3-A: 정상 응답 (타임아웃 없음) ---
        if coze_result["success"] and not coze_result["timed_out"]:
            return await self.format_response(coze_result, parsed)

        # --- Step 3-B: 타임아웃 + 콜백 가능 ---
        if coze_result["timed_out"] and parsed["callback_url"]:
            logger.info(
                f"카카오 콜백 모드 진입 "
                f"chat_id={coze_result['chat_id']} "
                f"callback_url 존재"
            )

            # 백그라운드에서 Coze 폴링 -> callbackUrl로 응답 전송
            asyncio.create_task(
                self._async_callback(
                    callback_url=parsed["callback_url"],
                    chat_id=coze_result["chat_id"],
                    conversation_id=coze_result["conversation_id"],
                    parsed=parsed,
                )
            )

            # 즉시 콜백 응답 반환 (카카오에 "기다려주세요" 메시지)
            return await self.handle_timeout(parsed)

        # --- Step 3-C: 타임아웃 + 콜백 불가 (callbackUrl 없음) ---
        if coze_result["timed_out"] and not parsed["callback_url"]:
            logger.warning("카카오 타임아웃 발생 but 콜백URL 없음 -> 에러 응답")
            return self._error_response(
                "응답 생성에 시간이 걸리고 있습니다 잠시 후 다시 시도해주세요"
            )

        # --- 기타 에러 (Coze API 실패 등) ---
        error_msg = coze_result.get("error", "알 수 없는 오류")
        logger.error(f"카카오 Coze 호출 실패: {error_msg}")
        return self._error_response("죄송합니다 일시적인 오류가 발생했습니다")

    # =========================================================================
    # 비동기 콜백 — 백그라운드에서 Coze 폴링 후 callbackUrl로 전송
    # =========================================================================

    async def _async_callback(
        self,
        callback_url: str,
        chat_id: str,
        conversation_id: str,
        parsed: dict,
    ) -> None:
        """
        백그라운드 태스크: Coze 폴링 완료 후 callbackUrl로 실제 응답 전송

        카카오 콜백 제약사항:
        - callbackUrl 유효 시간: 1분
        - 1회만 전송 가능
        - 전송 포맷: 일반 SkillResponse와 동일 (version + template)

        Args:
            callback_url: 카카오가 발급한 1회성 콜백 URL
            chat_id: Coze chat ID (폴링용)
            conversation_id: Coze conversation ID (폴링용)
            parsed: 파싱된 요청 정보 (응답 포맷팅에 필요)
        """
        try:
            logger.info(f"카카오 콜백 백그라운드 시작 chat_id={chat_id}")

            # Coze 폴링 — 최대 55초 대기 (callbackUrl 1분 제한 고려)
            coze_result = await self._coze.poll_and_get_result(
                chat_id=chat_id,
                conversation_id=conversation_id,
                max_wait=55.0,
                poll_interval=1.0,
            )

            # Coze 응답을 카카오 SkillResponse로 변환
            if coze_result["success"]:
                response_body = await self.format_response(coze_result, parsed)
            else:
                error_msg = coze_result.get("error", "응답 생성에 실패했습니다")
                logger.error(f"카카오 콜백 Coze 폴링 실패: {error_msg}")
                response_body = self._error_response(
                    "죄송합니다 응답 생성에 실패했습니다 다시 시도해주세요"
                )

            # callbackUrl로 POST 전송
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    callback_url,
                    json=response_body,
                    headers={"Content-Type": "application/json"},
                )

            logger.info(
                f"카카오 콜백 전송 완료 "
                f"status={resp.status_code} "
                f"chat_id={chat_id}"
            )

            # 콜백 응답 상태 확인
            if resp.status_code == 200:
                try:
                    callback_resp = resp.json()
                    status = callback_resp.get("status", "")
                    if status != "SUCCESS":
                        logger.warning(
                            f"카카오 콜백 응답 비정상 "
                            f"status={status} "
                            f"message={callback_resp.get('message', '')}"
                        )
                except Exception:
                    pass  # 응답 파싱 실패는 무시 (전송 자체는 성공)

        except httpx.TimeoutException:
            logger.error(f"카카오 콜백 전송 HTTP 타임아웃 chat_id={chat_id}")
        except Exception as e:
            logger.error(
                f"카카오 콜백 백그라운드 예외: "
                f"{type(e).__name__}: {str(e)} "
                f"chat_id={chat_id}"
            )

    # =========================================================================
    # 헬퍼 메서드 — SkillResponse 생성
    # =========================================================================

    @staticmethod
    def _text_response(text: str) -> dict:
        """
        simpleText SkillResponse 생성

        카카오 텍스트 응답 제한: 최대 1000자
        초과 시 잘라서 전송

        Args:
            text: 응답 텍스트

        Returns:
            카카오 SkillResponse dict
        """
        # 카카오 simpleText 최대 1000자 제한 처리
        if len(text) > 1000:
            text = text[:997] + "..."

        return {
            "version": "2.0",
            "template": {
                "outputs": [
                    {
                        "simpleText": {
                            "text": text
                        }
                    }
                ]
            }
        }

    @staticmethod
    def _error_response(message: str) -> dict:
        """
        에러 상황에서 카카오 정규 포맷 에러 응답 생성

        에러 시에도 반드시 유효한 SkillResponse를 반환해야
        카카오 오픈빌더가 사용자에게 적절한 메시지를 표시함

        Args:
            message: 사용자에게 보여줄 에러 메시지

        Returns:
            카카오 SkillResponse dict
        """
        return {
            "version": "2.0",
            "template": {
                "outputs": [
                    {
                        "simpleText": {
                            "text": message
                        }
                    }
                ]
            }
        }

    @staticmethod
    def _cards_to_text_fallback(cards: list) -> str:
        """
        카드 모듈이 없을 때 카드 데이터를 텍스트로 변환하는 폴백

        Task 5(카카오 카드 모듈) 구현 전까지 임시로 사용
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
