"""
카카오 오픈빌더 카드형 말풍선 범용 모듈

Coze 봇에서 수신한 상품/카드 데이터를 카카오 SkillResponse의
BasicCard / CommerceCard / Carousel 포맷으로 자동 변환

지원 카드 타입:
1. BasicCard — 이미지 + 제목 + 설명 + 버튼 (가격 정보 없을 때)
2. CommerceCard — 이미지 + 제목 + 설명 + 가격 + 버튼 (가격 정보 있을 때)
3. Carousel — 상품 2개 이상일 때 BasicCard 또는 CommerceCard를 캐러셀로 묶음

Coze 카드 데이터 입력 포맷 (CozeClient._try_parse_cards에서 파싱):
{
    "image_url": "https://...",        # 상품 이미지 URL (선택)
    "product_name": "정수기 A모델",      # 상품명 (선택 — title로도 가능)
    "title": "정수기 A모델",             # 상품명 대체 필드 (product_name 우선)
    "description": "월 29900원~",       # 상품 설명 (선택)
    "price": 29900,                    # 가격 — 숫자 또는 문자열 (선택)
    "discount": 5000,                  # 할인액 (선택)
    "currency": "won",                 # 통화 (선택 — 기본값 won)
    "button_url": "https://...",       # 버튼 클릭 시 이동할 URL (선택)
    "button_label": "자세히 보기"        # 버튼 텍스트 (선택 — 기본값 "자세히 보기")
}

카카오 스펙 참고:
- https://kakaobusiness.gitbook.io/main/tool/chatbot/skill_guide/answer_json_format

제한사항 (카카오 공식):
- BasicCard: title + description 합계 최대 400자
- CommerceCard: price 필수 (정수)
- Carousel: 최대 10장 / 한 캐러셀 내 모든 이미지 동일 비율 권장
- 버튼: 최대 3개 / label 최대 14자
- thumbnail imageUrl: 필수 (BasicCard에서 thumbnail 사용 시)
"""
from typing import Optional
from app.config.logging import logger


# =========================================================================
# 메인 진입점 — KakaoHandler.format_response()에서 호출
# =========================================================================

def build_kakao_card_output(cards: list[dict]) -> list[dict]:
    """
    Coze 카드 데이터 리스트를 카카오 SkillResponse outputs 배열로 변환

    변환 규칙:
    - 카드 0개 -> 빈 리스트 (호출자가 텍스트 폴백 처리)
    - 카드 1개 -> [{"basicCard": {...}}] 또는 [{"commerceCard": {...}}]
    - 카드 2~10개 -> [{"carousel": {"type": "basicCard|commerceCard", "items": [...]}}]
    - 카드 10개 초과 -> 앞 10개만 캐러셀 처리 (카카오 제한)

    Args:
        cards: Coze에서 파싱된 카드 데이터 리스트

    Returns:
        카카오 SkillResponse의 outputs 배열에 넣을 수 있는 dict 리스트
    """
    if not cards:
        return []

    # 카카오 캐러셀 최대 10장 제한
    if len(cards) > 10:
        logger.warning(f"카카오 카드 10장 초과 -> 앞 10개만 사용 (전체 {len(cards)}개)")
        cards = cards[:10]

    # 가격 정보 유무로 카드 타입 결정
    # 전체 카드 중 하나라도 price가 있으면 CommerceCard 모드
    has_price = any(_extract_price(card) is not None for card in cards)

    if has_price:
        # CommerceCard 모드
        built_cards = [_build_commerce_card_inner(card) for card in cards]
        card_type = "commerceCard"
    else:
        # BasicCard 모드
        built_cards = [_build_basic_card_inner(card) for card in cards]
        card_type = "basicCard"

    # 유효한 카드만 필터링 (빌드 실패한 None 제거)
    built_cards = [c for c in built_cards if c is not None]

    if not built_cards:
        return []

    # 단일 카드 -> 개별 출력
    if len(built_cards) == 1:
        return [{card_type: built_cards[0]}]

    # 복수 카드 -> 캐러셀
    return [
        {
            "carousel": {
                "type": card_type,
                "items": built_cards,
            }
        }
    ]


# =========================================================================
# BasicCard 빌드 — 가격 정보 없는 일반 카드
# =========================================================================

def _build_basic_card_inner(card: dict) -> Optional[dict]:
    """
    단일 Coze 카드 데이터를 카카오 BasicCard 내부 포맷으로 변환

    카카오 BasicCard 구조:
    {
        "title": "상품명",
        "description": "설명",
        "thumbnail": {"imageUrl": "https://..."},
        "buttons": [{"action": "webLink", "label": "자세히 보기", "webLinkUrl": "https://..."}]
    }

    Args:
        card: 단일 카드 데이터 dict

    Returns:
        BasicCard 내부 dict 또는 None (유효하지 않은 경우)
    """
    # 제목 추출: product_name -> title -> "상품 정보" (폴백)
    title = card.get("product_name") or card.get("title") or "상품 정보"

    # 설명 추출 (선택)
    description = card.get("description", "")

    # 제목 + 설명 합산 400자 제한 (카카오 공식)
    if len(title) + len(description) > 400:
        # 제목은 유지하고 설명을 자름
        max_desc = 400 - len(title) - 3  # "..." 여유
        if max_desc > 0:
            description = description[:max_desc] + "..."
        else:
            description = ""

    result = {}

    # 제목 설정
    if title:
        result["title"] = title

    # 설명 설정
    if description:
        result["description"] = description

    # 썸네일 (이미지) — BasicCard에서 thumbnail은 필수는 아니지만 권장
    image_url = card.get("image_url", "")
    if image_url:
        result["thumbnail"] = {"imageUrl": image_url}

    # 버튼 — URL이 있으면 웹링크 버튼 추가
    buttons = _build_buttons(card)
    if buttons:
        result["buttons"] = buttons

    # 최소한 제목이라도 있어야 유효한 카드
    if not result.get("title"):
        logger.warning(f"BasicCard 빌드 실패 — 제목 없음: {card}")
        return None

    return result


# =========================================================================
# CommerceCard 빌드 — 가격 정보 포함 상품 카드
# =========================================================================

def _build_commerce_card_inner(card: dict) -> Optional[dict]:
    """
    단일 Coze 카드 데이터를 카카오 CommerceCard 내부 포맷으로 변환

    카카오 CommerceCard 구조:
    {
        "title": "상품명",
        "description": "설명",
        "price": 29900,
        "discount": 5000,
        "currency": "won",
        "thumbnails": [{"imageUrl": "https://..."}],
        "buttons": [{"action": "webLink", "label": "구매하기", "webLinkUrl": "https://..."}]
    }

    CommerceCard 필수 필드: price (정수)
    price가 없으면 BasicCard로 폴백

    Args:
        card: 단일 카드 데이터 dict

    Returns:
        CommerceCard 내부 dict 또는 None
    """
    # 가격 추출 (필수)
    price = _extract_price(card)

    if price is None:
        # 가격 없으면 BasicCard로 폴백
        logger.info("CommerceCard 가격 없음 -> BasicCard 폴백")
        return _build_basic_card_inner(card)

    # 제목 추출
    title = card.get("product_name") or card.get("title") or "상품 정보"

    result = {
        "title": title,
        "price": price,
        "currency": card.get("currency", "won"),
    }

    # 설명 (선택)
    description = card.get("description", "")
    if description:
        result["description"] = description

    # 할인 정보 (선택)
    discount = _extract_price(card, key="discount")
    if discount is not None and discount > 0:
        result["discount"] = discount

    # 할인율 (선택)
    discount_rate = card.get("discount_rate") or card.get("discountRate")
    if discount_rate is not None:
        try:
            result["discountRate"] = int(discount_rate)
        except (ValueError, TypeError):
            pass

    # 할인가 (선택)
    discounted_price = _extract_price(card, key="discounted_price")
    if discounted_price is None:
        discounted_price = _extract_price(card, key="discountedPrice")
    if discounted_price is not None:
        result["discountedPrice"] = discounted_price

    # 썸네일 — CommerceCard는 thumbnails 배열 형태
    image_url = card.get("image_url", "")
    if image_url:
        result["thumbnails"] = [{"imageUrl": image_url}]

    # 버튼
    buttons = _build_buttons(card, default_label="구매하기")
    if buttons:
        result["buttons"] = buttons

    return result


# =========================================================================
# 공용 헬퍼 — 버튼 빌드
# =========================================================================

def _build_buttons(card: dict, default_label: str = "자세히 보기") -> list[dict]:
    """
    카드 데이터에서 버튼 목록 생성

    버튼 생성 규칙:
    - button_url이 있으면 webLink 버튼 생성
    - button_label이 있으면 해당 텍스트 사용 (없으면 default_label)
    - buttons 배열이 직접 전달되면 그대로 사용 (Coze 봇이 커스텀 버튼 제공 시)

    카카오 버튼 제한: 최대 3개 / label 최대 14자

    Args:
        card: 카드 데이터 dict
        default_label: 버튼 기본 텍스트

    Returns:
        카카오 버튼 dict 리스트
    """
    buttons = []

    # Coze에서 커스텀 버튼 배열을 직접 보낸 경우
    raw_buttons = card.get("buttons", [])
    if isinstance(raw_buttons, list) and raw_buttons:
        for btn in raw_buttons[:3]:  # 최대 3개
            if isinstance(btn, dict):
                kakao_btn = _convert_custom_button(btn)
                if kakao_btn:
                    buttons.append(kakao_btn)
        if buttons:
            return buttons

    # button_url로 기본 웹링크 버튼 생성
    button_url = card.get("button_url", "")
    if button_url:
        label = card.get("button_label", default_label)
        # 카카오 버튼 label 최대 14자
        if len(label) > 14:
            label = label[:14]

        buttons.append({
            "action": "webLink",
            "label": label,
            "webLinkUrl": button_url,
        })

    return buttons


def _convert_custom_button(btn: dict) -> Optional[dict]:
    """
    Coze에서 전달된 커스텀 버튼을 카카오 버튼 포맷으로 변환

    지원 타입:
    - webLink: URL 링크
    - message: 메시지 전송
    - phone: 전화 걸기

    Args:
        btn: Coze 커스텀 버튼 dict

    Returns:
        카카오 버튼 dict 또는 None
    """
    action = btn.get("action", "webLink")
    label = btn.get("label", "버튼")

    # label 14자 제한
    if len(label) > 14:
        label = label[:14]

    if action == "webLink":
        url = btn.get("url") or btn.get("webLinkUrl", "")
        if not url:
            return None
        return {"action": "webLink", "label": label, "webLinkUrl": url}

    elif action == "message":
        msg = btn.get("messageText") or btn.get("text", label)
        return {"action": "message", "label": label, "messageText": msg}

    elif action == "phone":
        phone = btn.get("phoneNumber") or btn.get("phone", "")
        if not phone:
            return None
        return {"action": "phone", "label": label, "phoneNumber": phone}

    # 미지원 action은 webLink로 폴백 시도
    url = btn.get("url", "")
    if url:
        return {"action": "webLink", "label": label, "webLinkUrl": url}

    return None


# =========================================================================
# 공용 헬퍼 — 가격 추출
# =========================================================================

def _extract_price(card: dict, key: str = "price") -> Optional[int]:
    """
    카드 데이터에서 가격을 정수로 추출

    가격은 다양한 형태로 올 수 있음:
    - 정수: 29900
    - 실수: 29900.0
    - 문자열: "29900" / "29,900" / "29900원" / "월 29,900원"

    모두 정수로 정규화하여 반환
    카카오 CommerceCard의 price는 반드시 정수여야 함

    Args:
        card: 카드 데이터 dict
        key: 가격 필드 키 (price / discount / discounted_price 등)

    Returns:
        정수 가격 또는 None (가격 정보 없거나 파싱 실패)
    """
    value = card.get(key)

    if value is None:
        return None

    # 이미 정수인 경우
    if isinstance(value, int):
        return value

    # 실수인 경우 -> 정수로 변환
    if isinstance(value, float):
        return int(value)

    # 문자열인 경우 -> 숫자만 추출
    if isinstance(value, str):
        # 쉼표 제거 + 숫자가 아닌 문자 제거
        digits = "".join(c for c in value if c.isdigit())
        if digits:
            try:
                return int(digits)
            except ValueError:
                pass

    return None
