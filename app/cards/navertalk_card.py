"""
네이버톡톡 카드형(CompositeContent) 범용 모듈

Coze 봇에서 수신한 상품/카드 데이터를 네이버톡톡의
compositeContent(Composite / Carousel) 포맷으로 자동 변환

지원 카드 타입:
1. 단일 Composite — 이미지 + 제목 + 설명 + elementList + 버튼 (상품 1개)
2. Carousel — 상품 2개 이상일 때 compositeList 배열로 묶음 (최대 10개)

Coze 카드 데이터 입력 포맷 (CozeClient._try_parse_cards에서 파싱):
{
    "image_url": "https://...",        # 상품 이미지 URL (선택)
    "product_name": "정수기 A모델",      # 상품명 (선택 — title로도 가능)
    "title": "정수기 A모델",             # 상품명 대체 필드 (product_name 우선)
    "description": "월 29900원~",       # 상품 설명 (선택)
    "price": "29,900원",               # 가격 — 문자열 또는 숫자 (선택)
    "button_url": "https://...",       # 버튼 클릭 시 이동할 URL (선택)
    "button_label": "자세히 보기"        # 버튼 텍스트 (선택 — 기본값 "자세히 보기")
}

네이버톡톡 compositeContent 스펙 참고:
- https://github.com/navertalk/chatbot-api#compositecontent

compositeContent 구조:
{
    "compositeList": [
        {
            "title": "제목",                          # 최대 200자
            "description": "설명",                     # 최대 1000자
            "image": {"imageUrl": "https://..."},     # 530x290px 권장 (1.82:1)
            "elementList": {                          # 리스트형 하위 요소
                "type": "LIST",
                "data": [
                    {
                        "title": "항목 제목",           # 필수 / 최대 100자
                        "description": "항목 설명1",    # 선택 / 최대 100자
                        "subDescription": "항목 설명2", # 선택 / 최대 100자
                        "image": {"imageUrl": "..."},  # 선택 / 정사각형 권장
                        "button": {...}                # 선택 / TEXT 또는 LINK
                    }
                ]
            },
            "buttonList": [                           # 최대 10개
                {
                    "type": "TEXT",                    # TEXT / LINK / OPTION / PAY
                    "data": {
                        "title": "버튼 텍스트",          # 최대 18자
                        "code": "버튼 코드"             # 최대 1000자
                    }
                },
                {
                    "type": "LINK",
                    "data": {
                        "title": "버튼 텍스트",
                        "url": "https://pc-url",
                        "mobileUrl": "https://mobile-url"
                    }
                }
            ]
        }
    ]
}

제한사항 (네이버톡톡 공식):
- compositeList: 최대 10개 Composite
- composite 내 title: 최대 200자
- composite 내 description: 최대 1000자
- image: JPG/JPEG/PNG/GIF / 530x290px 권장 (1.82:1)
- elementList.data: 최대 3개 ElementData
- elementData.title: 필수 / 최대 100자
- buttonList: 최대 10개 / title 최대 18자
- TEXT 버튼 code: 최대 1000자
"""
from typing import Optional

from app.config.logging import logger


# =========================================================================
# 메인 진입점 — NaverTalkHandler.format_response()에서 호출
# =========================================================================

def build_navertalk_card_response(cards: list[dict]) -> Optional[dict]:
    """
    Coze 카드 데이터 리스트를 네이버톡톡 compositeContent 응답으로 변환

    변환 규칙:
    - 카드 0개 -> None (호출자가 텍스트 폴백 처리)
    - 카드 1~10개 -> compositeContent 응답 (단일이든 복수든 compositeList 배열로 통일)
    - 카드 10개 초과 -> 앞 10개만 사용 (네이버톡톡 제한)

    Args:
        cards: Coze에서 파싱된 카드 데이터 리스트

    Returns:
        네이버톡톡 응답 dict 또는 None (유효한 카드 없음)
        {
            "event": "send",
            "compositeContent": {
                "compositeList": [...]
            }
        }
    """
    # 빈 카드 리스트 방어
    if not cards:
        return None

    # 네이버톡톡 compositeList 최대 10개 제한
    if len(cards) > 10:
        logger.warning(
            f"네이버톡톡 카드 10개 초과 -> 앞 10개만 사용 (전체 {len(cards)}개)"
        )
        cards = cards[:10]

    # 각 카드를 Composite 객체로 변환
    composite_list = []
    for card in cards:
        composite = _build_composite(card)
        if composite:
            composite_list.append(composite)

    # 유효한 Composite가 없으면 None 반환
    if not composite_list:
        logger.warning("네이버톡톡 유효한 Composite 카드 없음 -> None 반환")
        return None

    # 네이버톡톡 compositeContent 응답 구조
    return {
        "event": "send",
        "compositeContent": {
            "compositeList": composite_list,
        },
    }


# =========================================================================
# Composite 빌드 — 개별 카드 데이터를 Composite 객체로 변환
# =========================================================================

def _build_composite(card: dict) -> Optional[dict]:
    """
    단일 Coze 카드 데이터를 네이버톡톡 Composite 객체로 변환

    Composite 필수 조건:
    - title / description / elementList 중 하나 이상 있어야 함
    - 모두 없으면 유효하지 않은 카드로 판단

    네이버톡톡 Composite 구조:
    {
        "title": "상품명",                           # 선택 / 최대 200자
        "description": "설명 + 가격 정보",             # 선택 / 최대 1000자
        "image": {"imageUrl": "https://..."},       # 선택 / 530x290px 권장
        "elementList": {...},                       # 선택 / 리스트형 하위 요소
        "buttonList": [...]                         # 선택 / 최대 10개 버튼
    }

    Args:
        card: 단일 카드 데이터 dict

    Returns:
        Composite dict 또는 None (유효하지 않은 경우)
    """
    result = {}

    # --- 제목 추출: product_name -> title -> 폴백 ---
    title = card.get("product_name") or card.get("title") or ""

    # 네이버톡톡 title 최대 200자 (\\n으로 줄바꿈 가능)
    if title:
        if len(title) > 200:
            title = title[:197] + "..."
        result["title"] = title

    # --- 설명 구성: description + price 합산 ---
    description_parts = []

    desc = card.get("description", "")
    if desc:
        description_parts.append(desc)

    # 가격 정보가 있으면 설명에 포함
    price = card.get("price")
    if price is not None:
        price_str = _format_price(price)
        if price_str:
            description_parts.append(f"가격: {price_str}")

    description = "\n".join(description_parts)

    # 네이버톡톡 description 최대 1000자
    if description:
        if len(description) > 1000:
            description = description[:997] + "..."
        result["description"] = description

    # --- 이미지 ---
    image_url = card.get("image_url", "")
    if image_url:
        result["image"] = {"imageUrl": image_url}

    # --- 버튼 리스트 ---
    buttons = _build_button_list(card)
    if buttons:
        result["buttonList"] = buttons

    # --- 유효성 검증: title/description/elementList 중 하나 이상 필요 ---
    if not result.get("title") and not result.get("description"):
        # 이미지만 있는 경우 -> 기본 제목 추가
        if result.get("image"):
            result["title"] = "상품 정보"
        else:
            logger.warning(f"Composite 빌드 실패 — 제목/설명/이미지 모두 없음: {card}")
            return None

    return result


# =========================================================================
# ElementList 빌드 — 리스트형 하위 요소 (카드 내부 목록)
# =========================================================================

def build_element_list(items: list[dict]) -> Optional[dict]:
    """
    리스트형 하위 요소(ElementList)를 빌드

    카드 내부에 여러 항목(예: 상품 옵션/스펙)을 목록으로 보여줄 때 사용
    일반적인 단일 상품 카드에서는 사용하지 않지만
    Coze 봇이 elements/options 키로 하위 항목을 보내면 자동 처리

    ElementList 구조:
    {
        "type": "LIST",
        "data": [
            {
                "title": "항목 제목",           # 필수 / 최대 100자
                "description": "항목 설명1",    # 선택 / 최대 100자
                "subDescription": "항목 설명2", # 선택 / 최대 100자
                "image": {"imageUrl": "..."},  # 선택
                "button": {...}                # 선택
            }
        ]
    }

    Args:
        items: 하위 항목 데이터 리스트

    Returns:
        ElementList dict 또는 None
    """
    if not items:
        return None

    # 최대 3개 ElementData 제한
    if len(items) > 3:
        logger.warning(
            f"네이버톡톡 ElementList 3개 초과 -> 앞 3개만 사용 (전체 {len(items)}개)"
        )
        items = items[:3]

    element_data_list = []

    for item in items:
        if not isinstance(item, dict):
            continue

        element = {}

        # title 필수 / 최대 100자
        item_title = item.get("title", "")
        if not item_title:
            continue  # title 없으면 스킵

        if len(item_title) > 100:
            item_title = item_title[:97] + "..."
        element["title"] = item_title

        # description 선택 / 최대 100자
        item_desc = item.get("description", "")
        if item_desc:
            if len(item_desc) > 100:
                item_desc = item_desc[:97] + "..."
            element["description"] = item_desc

        # subDescription 선택 / 최대 100자
        item_sub = item.get("subDescription") or item.get("sub_description", "")
        if item_sub:
            if len(item_sub) > 100:
                item_sub = item_sub[:97] + "..."
            element["subDescription"] = item_sub

        # image 선택
        item_image = item.get("image_url") or item.get("imageUrl", "")
        if item_image:
            element["image"] = {"imageUrl": item_image}

        # button 선택 (TEXT 또는 LINK / title 최대 10자)
        item_button = _build_element_button(item)
        if item_button:
            element["button"] = item_button

        element_data_list.append(element)

    if not element_data_list:
        return None

    return {
        "type": "LIST",
        "data": element_data_list,
    }


def _build_element_button(item: dict) -> Optional[dict]:
    """
    ElementData 내부 버튼 빌드

    ElementData의 button은 TEXT 또는 LINK만 가능
    title 최대 10자 (buttonList의 18자와 다름!)

    Args:
        item: 하위 항목 데이터 dict

    Returns:
        Button dict 또는 None
    """
    button_url = item.get("button_url", "")
    button_label = item.get("button_label", "")

    if button_url:
        # LINK 버튼
        label = button_label or "보기"
        if len(label) > 10:
            label = label[:10]
        return {
            "type": "LINK",
            "data": {
                "title": label,
                "url": button_url,
                "mobileUrl": button_url,
            },
        }

    button_code = item.get("button_code") or item.get("code", "")
    if button_code:
        # TEXT 버튼
        label = button_label or "선택"
        if len(label) > 10:
            label = label[:10]
        return {
            "type": "TEXT",
            "data": {
                "title": label,
                "code": button_code[:1000],  # code 최대 1000자
            },
        }

    return None


# =========================================================================
# ButtonList 빌드 — Composite 하단 버튼 목록
# =========================================================================

def _build_button_list(card: dict, default_label: str = "자세히 보기") -> list[dict]:
    """
    카드 데이터에서 네이버톡톡 버튼 목록 생성

    버튼 생성 규칙:
    1. card["buttons"] 배열이 있으면 -> 커스텀 버튼 변환 (최대 10개)
    2. card["button_url"]이 있으면 -> LINK 버튼 1개 생성
    3. 둘 다 없으면 -> 빈 리스트

    네이버톡톡 버튼 타입:
    - TEXT: 텍스트 전송 (채팅창에 텍스트 입력됨)
    - LINK: URL 이동 (PC/모바일 URL 분리 가능)
    - OPTION: 2depth 하위 버튼 (이 모듈에서는 미사용)
    - PAY: 결제 (이 모듈에서는 미사용)

    버튼 제한: 최대 10개 / title 최대 18자

    Args:
        card: 카드 데이터 dict
        default_label: 버튼 기본 텍스트

    Returns:
        네이버톡톡 Button dict 리스트
    """
    buttons = []

    # --- 1. 커스텀 버튼 배열 처리 ---
    raw_buttons = card.get("buttons", [])
    if isinstance(raw_buttons, list) and raw_buttons:
        for btn in raw_buttons[:10]:  # 최대 10개
            if isinstance(btn, dict):
                converted = _convert_custom_button(btn)
                if converted:
                    buttons.append(converted)
        if buttons:
            return buttons

    # --- 2. button_url로 기본 LINK 버튼 생성 ---
    button_url = card.get("button_url", "")
    if button_url:
        label = card.get("button_label", default_label)
        # 네이버톡톡 버튼 title 최대 18자
        if len(label) > 18:
            label = label[:18]

        buttons.append({
            "type": "LINK",
            "data": {
                "title": label,
                "url": button_url,
                "mobileUrl": button_url,
            },
        })

    return buttons


def _convert_custom_button(btn: dict) -> Optional[dict]:
    """
    Coze에서 전달된 커스텀 버튼을 네이버톡톡 Button 포맷으로 변환

    지원 변환:
    - type="link" / action="webLink" -> LINK 버튼
    - type="text" / action="message" -> TEXT 버튼

    미지원 타입은 LINK 또는 TEXT로 폴백 시도

    Args:
        btn: Coze 커스텀 버튼 dict

    Returns:
        네이버톡톡 Button dict 또는 None
    """
    # 버튼 타입 판별 (다양한 입력 형식 수용)
    btn_type = (
        btn.get("type", "")
        or btn.get("action", "")
    ).lower()

    label = btn.get("label") or btn.get("title") or "버튼"
    # 네이버톡톡 buttonList title 최대 18자
    if len(label) > 18:
        label = label[:18]

    # --- LINK 버튼 ---
    if btn_type in ("link", "weblink", "web_link", "url"):
        url = btn.get("url") or btn.get("webLinkUrl") or btn.get("button_url", "")
        mobile_url = btn.get("mobileUrl") or btn.get("mobile_url") or url
        if not url:
            return None
        return {
            "type": "LINK",
            "data": {
                "title": label,
                "url": url,
                "mobileUrl": mobile_url,
            },
        }

    # --- TEXT 버튼 ---
    if btn_type in ("text", "message"):
        code = (
            btn.get("code")
            or btn.get("messageText")
            or btn.get("text")
            or label
        )
        return {
            "type": "TEXT",
            "data": {
                "title": label,
                "code": str(code)[:1000],  # code 최대 1000자
            },
        }

    # --- 폴백: URL이 있으면 LINK / 없으면 TEXT ---
    url = btn.get("url") or btn.get("webLinkUrl", "")
    if url:
        mobile_url = btn.get("mobileUrl") or btn.get("mobile_url") or url
        return {
            "type": "LINK",
            "data": {
                "title": label,
                "url": url,
                "mobileUrl": mobile_url,
            },
        }

    # URL도 없으면 TEXT 버튼으로 폴백
    code = btn.get("code") or btn.get("text") or label
    return {
        "type": "TEXT",
        "data": {
            "title": label,
            "code": str(code)[:1000],
        },
    }


# =========================================================================
# 헬퍼 — 가격 포맷팅
# =========================================================================

def _format_price(price) -> str:
    """
    가격 값을 표시용 문자열로 포맷팅

    네이버톡톡은 카카오 CommerceCard와 달리 전용 가격 필드가 없으므로
    description 텍스트에 가격을 포함시킴
    -> 다양한 입력 형태를 사람이 읽기 좋은 문자열로 변환

    변환 예시:
    - 29900 -> "29,900원"
    - 29900.0 -> "29,900원"
    - "29900" -> "29,900원"
    - "29,900원" -> "29,900원" (이미 포맷팅됨)
    - "월 29,900원" -> "월 29,900원" (이미 포맷팅됨)

    Args:
        price: 가격 값 (int / float / str)

    Returns:
        포맷팅된 가격 문자열 (빈 문자열이면 가격 없음)
    """
    if price is None:
        return ""

    # 이미 문자열이고 한글/특수문자 포함 -> 이미 포맷팅된 것으로 간주
    if isinstance(price, str):
        # 숫자만으로 이루어진 문자열이면 포맷팅 필요
        digits_only = "".join(c for c in price if c.isdigit())
        has_non_digit = any(not c.isdigit() and c not in ",." for c in price)

        if has_non_digit:
            # "29,900원" / "월 29,900원" 같은 형태 -> 그대로 반환
            return price

        if digits_only:
            try:
                num = int(digits_only)
                return f"{num:,}원"
            except ValueError:
                return price

        return price

    # 숫자형 (int / float) -> 천 단위 쉼표 + "원"
    if isinstance(price, (int, float)):
        return f"{int(price):,}원"

    return str(price)


# =========================================================================
# 확장 빌더 — ElementList 포함 Composite (복합 카드)
# =========================================================================

def build_navertalk_composite_with_elements(
    card: dict,
    element_items: list[dict],
) -> Optional[dict]:
    """
    제목/설명/이미지 + ElementList를 결합한 복합 Composite 빌드

    Coze 봇이 상품 정보 + 하위 옵션 목록을 함께 보내는 경우 사용
    예: 정수기 상품 카드 + 옵션 3개 (온수/냉수/정수)

    이 함수는 NaverTalkHandler에서 직접 호출하지 않지만
    Coze 봇 응답이 복합 구조일 때 확장 포인트로 활용 가능

    Args:
        card: 메인 카드 데이터 (title/description/image_url 등)
        element_items: 하위 항목 리스트 (ElementList로 변환됨)

    Returns:
        Composite dict 또는 None
    """
    # 기본 Composite 빌드
    composite = _build_composite(card)
    if not composite:
        return None

    # ElementList 빌드 후 추가
    element_list = build_element_list(element_items)
    if element_list:
        composite["elementList"] = element_list

    return composite
