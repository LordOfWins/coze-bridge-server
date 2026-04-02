"""
Coze Bridge Server — FastAPI 메인 앱
카카오톡 + 네이버톡톡 2채널 동시 운영 브릿지 서버
멀티 고객사 지원: /skill/kakao/{client_key} 및 /skill/navertalk/{client_key} 패턴
"""
from typing import Optional
from fastapi import FastAPI, Request, Header
from fastapi.responses import JSONResponse
from app.config.settings import get_settings
from app.config.logging import logger
from app.config.client_config import get_config_manager, get_client_config
from app.modules.coze_client import CozeClient
from app.handlers.kakao import KakaoHandler
from app.handlers.navertalk import NaverTalkHandler

settings = get_settings()

app = FastAPI(
    title="Coze Bridge Server",
    description="카카오톡 + 네이버톡톡 멀티채널 Coze AI 챗봇 브릿지 서버",
    version="1.0.0",
    docs_url="/docs" if settings.ENV == "development" else None,
    redoc_url=None,
)


# =========================================================================
# 헬퍼 함수 — 고객사 설정으로 CozeClient/핸들러 생성
# =========================================================================

def _make_coze_client(config) -> CozeClient:
    """고객사 설정으로 CozeClient 인스턴스 생성"""
    return CozeClient(
        bot_id=config.coze_bot_id,
        pat=config.coze_pat,
        api_base=config.coze_api_base,
        timeout_seconds=config.timeout_seconds,
    )


# =========================================================================
# 헬스체크
# =========================================================================

@app.get("/health")
async def health_check():
    """서버 상태 확인 — Railway 헬스체크용"""
    manager = get_config_manager()
    clients = manager.get_all()
    return {
        "status": "ok",
        "service": "coze-bridge-server",
        "clients_loaded": len(clients),
        "client_keys": list(clients.keys()),
    }


# =========================================================================
# 카카오 오픈빌더 스킬 엔드포인트 — KakaoHandler 연결 완료
# =========================================================================

@app.post("/skill/kakao")
async def kakao_skill_default(request: Request):
    """카카오 스킬 — 기본(default) 고객사"""
    return await _handle_kakao(request, client_key=None)


@app.post("/skill/kakao/{client_key}")
async def kakao_skill_client(request: Request, client_key: str):
    """카카오 스킬 — 특정 고객사 (URL 경로로 구분)"""
    return await _handle_kakao(request, client_key=client_key)


async def _handle_kakao(request: Request, client_key: Optional[str] = None):
    """
    카카오 스킬 공용 처리 함수

    1. client_key로 고객사 설정 조회
    2. 설정 기반으로 CozeClient + KakaoHandler 생성
    3. KakaoHandler.handle()로 전체 파이프라인 실행
    """
    # 고객사 설정 조회
    config = get_client_config(client_key)

    if config is None:
        logger.error(f"카카오 스킬 요청 실패 — 고객사 설정 없음: {client_key}")
        return JSONResponse(content={
            "version": "2.0",
            "template": {"outputs": [{"simpleText": {"text": "서비스 설정 오류가 발생했습니다"}}]}
        })

    # 설정 유효성 검증
    if not config.is_valid():
        logger.error(f"카카오 스킬 요청 실패 — 고객사 설정 불완전: {client_key}")
        return JSONResponse(content={
            "version": "2.0",
            "template": {"outputs": [{"simpleText": {"text": "서비스 설정 오류가 발생했습니다"}}]}
        })

    logger.info(f"카카오 스킬 요청 수신 client_key={config.client_key} bot_id={config.coze_bot_id}")

    try:
        # 요청 바디 파싱
        body = await request.json()

        # CozeClient + KakaoHandler 생성 후 처리
        coze_client = _make_coze_client(config)
        handler = KakaoHandler(coze_client=coze_client)
        result = await handler.handle(body)

        return JSONResponse(content=result)

    except Exception as e:
        logger.error(f"카카오 스킬 처리 중 예외: {type(e).__name__}: {str(e)}")
        return JSONResponse(content={
            "version": "2.0",
            "template": {"outputs": [{"simpleText": {"text": "죄송합니다 일시적인 오류가 발생했습니다"}}]}
        })


# =========================================================================
# 네이버톡톡 웹훅 엔드포인트 — NaverTalkHandler 연결 완료
# =========================================================================

@app.post("/skill/navertalk")
async def navertalk_webhook_default(request: Request):
    """네이버톡톡 웹훅 — 기본(default) 고객사"""
    return await _handle_navertalk(request, client_key=None)


@app.post("/skill/navertalk/{client_key}")
async def navertalk_webhook_client(request: Request, client_key: str):
    """네이버톡톡 웹훅 — 특정 고객사 (URL 경로로 구분)"""
    return await _handle_navertalk(request, client_key=client_key)


async def _handle_navertalk(request: Request, client_key: Optional[str] = None):
    """
    네이버톡톡 웹훅 공용 처리 함수

    1. client_key로 고객사 설정 조회
    2. 네이버톡톡 인증 토큰 확인
    3. 설정 기반으로 CozeClient + NaverTalkHandler 생성
    4. NaverTalkHandler.handle()로 전체 파이프라인 실행

    네이버톡톡 웹훅 특이사항:
    - 모든 응답은 반드시 200 OK (에러여도)
    - Content-Type: application/json;charset=UTF-8
    - 5초 Read Timeout → 초과 시 네이버가 연결 끊음
    """
    # 고객사 설정 조회
    config = get_client_config(client_key)

    if config is None:
        logger.error(f"네이버톡톡 웹훅 요청 실패 — 고객사 설정 없음: {client_key}")
        # 네이버톡톡은 에러 시에도 200 OK 반환 (재시도 방지)
        return JSONResponse(
            status_code=200,
            content={},
            headers={"Content-Type": "application/json;charset=UTF-8"},
        )

    # 설정 유효성 검증
    if not config.is_valid():
        logger.error(f"네이버톡톡 웹훅 요청 실패 — 고객사 설정 불완전: {client_key}")
        return JSONResponse(
            status_code=200,
            content={},
            headers={"Content-Type": "application/json;charset=UTF-8"},
        )

    # 네이버톡톡 인증 토큰 존재 여부 확인
    if not config.naver_talk_token:
        logger.error(f"네이버톡톡 웹훅 요청 실패 — 인증 토큰 미설정: {client_key}")
        return JSONResponse(
            status_code=200,
            content={},
            headers={"Content-Type": "application/json;charset=UTF-8"},
        )

    logger.info(
        f"네이버톡톡 웹훅 요청 수신 "
        f"client_key={config.client_key} "
        f"partner_id={config.naver_talk_partner_id}"
    )

    try:
        # 요청 바디 파싱
        body = await request.json()

        # CozeClient + NaverTalkHandler 생성 후 처리
        coze_client = _make_coze_client(config)
        handler = NaverTalkHandler(
            coze_client=coze_client,
            naver_talk_token=config.naver_talk_token,
        )
        result = await handler.handle(body)

        return JSONResponse(
            content=result,
            headers={"Content-Type": "application/json;charset=UTF-8"},
        )

    except Exception as e:
        logger.error(f"네이버톡톡 웹훅 처리 중 예외: {type(e).__name__}: {str(e)}")
        # 네이버톡톡은 에러 시에도 200 OK 반환 (재시도 방지)
        return JSONResponse(
            status_code=200,
            content={},
            headers={"Content-Type": "application/json;charset=UTF-8"},
        )


# =========================================================================
# 관리자 엔드포인트 — 설정 핫 리로드 + 상태 확인
# =========================================================================

@app.post("/admin/reload-config")
async def reload_config(x_admin_secret: Optional[str] = Header(None)):
    """
    고객사 설정 핫 리로드 — 서버 재시작 없이 clients.json을 다시 읽음

    사용법: POST /admin/reload-config (Header: X-Admin-Secret: your_secret)
    ADMIN_SECRET 환경변수가 설정되어 있으면 인증 필요
    설정되어 있지 않으면 누구나 호출 가능 (개발 환경용)
    """
    # 관리자 인증 확인
    admin_secret = settings.ADMIN_SECRET
    if admin_secret and x_admin_secret != admin_secret:
        return JSONResponse(status_code=403, content={"error": "인증 실패"})

    manager = get_config_manager()
    count = manager.reload()

    return {
        "status": "ok",
        "clients_reloaded": count,
        "client_keys": list(manager.get_all().keys()),
    }


@app.get("/admin/clients")
async def list_clients(x_admin_secret: Optional[str] = Header(None)):
    """
    등록된 고객사 목록 확인 (민감 정보 마스킹)

    사용법: GET /admin/clients (Header: X-Admin-Secret: your_secret)
    """
    # 관리자 인증 확인
    admin_secret = settings.ADMIN_SECRET
    if admin_secret and x_admin_secret != admin_secret:
        return JSONResponse(status_code=403, content={"error": "인증 실패"})

    manager = get_config_manager()
    clients = manager.get_all()

    # 민감 정보 마스킹하여 반환
    result = {}
    for key, config in clients.items():
        result[key] = {
            "label": config.label,
            "coze_bot_id": config.coze_bot_id,
            "coze_pat": f"{config.coze_pat[:12]}********" if config.coze_pat else "",
            "coze_api_base": config.coze_api_base,
            "naver_talk_partner_id": config.naver_talk_partner_id,
            "naver_talk_token": f"{config.naver_talk_token[:6]}********" if config.naver_talk_token else "",
            "timeout_seconds": config.timeout_seconds,
            "enabled": config.enabled,
        }

    return {"clients": result}


# =========================================================================
# 전역 예외 핸들러
# =========================================================================

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """전역 예외 핸들러 — 모든 에러를 채널별 정규 포맷으로 안전하게 반환"""
    logger.error(f"미처리 예외: {type(exc).__name__}: {str(exc)}")
    path = request.url.path

    # 카카오 경로면 카카오 정규 포맷으로 반환
    if "/skill/kakao" in path:
        return JSONResponse(content={
            "version": "2.0",
            "template": {"outputs": [{"simpleText": {"text": "죄송합니다 일시적인 오류가 발생했습니다"}}]}
        })

    # 네이버톡톡 경로면 빈 200 반환 (에러 시 재시도 방지)
    elif "/skill/navertalk" in path:
        return JSONResponse(
            status_code=200,
            content={},
            headers={"Content-Type": "application/json;charset=UTF-8"},
        )

    # 기타 경로
    return JSONResponse(status_code=500, content={"error": "Internal Server Error"})


# =========================================================================
# 서버 시작/종료 이벤트
# =========================================================================

@app.on_event("startup")
async def startup_event():
    """서버 시작 시 설정 확인 로그"""
    logger.info("=== Coze Bridge Server 시작 ===")
    logger.info(f"환경: {settings.ENV}")
    logger.info(f"포트: {settings.PORT}")

    # 멀티 고객사 설정 로드 확인
    manager = get_config_manager()
    clients = manager.get_all()
    logger.info(f"로드된 고객사: {len(clients)}개 — {list(clients.keys())}")

    for key, config in clients.items():
        logger.info(f"  {config.masked_summary()}")

    logger.info("================================")
