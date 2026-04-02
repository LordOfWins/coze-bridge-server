"""
Coze Bridge Server — FastAPI 메인 앱
카카오톡 + 네이버톡톡 2채널 동시 운영 브릿지 서버
"""
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from app.config.settings import get_settings
from app.config.logging import logger

settings = get_settings()

app = FastAPI(
    title="Coze Bridge Server",
    description="카카오톡 + 네이버톡톡 멀티채널 Coze AI 챗봇 브릿지 서버",
    version="1.0.0",
    docs_url="/docs" if settings.ENV == "development" else None,
    redoc_url=None,
)


@app.get("/health")
async def health_check():
    """서버 상태 확인 — Railway 헬스체크용"""
    return {"status": "ok", "service": "coze-bridge-server"}


@app.post("/skill/kakao")
async def kakao_skill(request: Request):
    """카카오 오픈빌더 스킬 엔드포인트 — Task 4에서 KakaoHandler 연결"""
    logger.info("카카오 스킬 요청 수신")
    return JSONResponse(content={
        "version": "2.0",
        "template": {"outputs": [{"simpleText": {"text": "서버 준비중입니다"}}]}
    })


@app.post("/skill/navertalk")
async def navertalk_webhook(request: Request):
    """네이버톡톡 웹훅 엔드포인트 — Task 6에서 NaverTalkHandler 연결"""
    logger.info("네이버톡톡 웹훅 요청 수신")
    return JSONResponse(
        content={"event": "send", "textContent": {"text": "서버 준비중입니다"}},
        headers={"Content-Type": "application/json;charset=UTF-8"}
    )


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """전역 예외 핸들러 — 모든 에러를 채널별 정규 포맷으로 안전하게 반환"""
    logger.error(f"미처리 예외: {type(exc).__name__}: {str(exc)}")
    path = request.url.path
    if "/skill/kakao" in path:
        return JSONResponse(content={
            "version": "2.0",
            "template": {"outputs": [{"simpleText": {"text": "죄송합니다 일시적인 오류가 발생했습니다"}}]}
        })
    elif "/skill/navertalk" in path:
        return JSONResponse(
            content={"event": "send", "textContent": {"text": "죄송합니다 일시적인 오류가 발생했습니다"}},
            headers={"Content-Type": "application/json;charset=UTF-8"}
        )
    return JSONResponse(status_code=500, content={"error": "Internal Server Error"})


@app.on_event("startup")
async def startup_event():
    """서버 시작 시 설정 확인 로그"""
    logger.info("=== Coze Bridge Server 시작 ===")
    logger.info(f"환경: {settings.ENV}")
    logger.info(f"포트: {settings.PORT}")
    logger.info(f"Coze Bot ID: {settings.COZE_BOT_ID}")
    logger.info(f"Coze PAT: {settings.COZE_PAT[:12]}********")
    logger.info(f"네이버톡톡 파트너: {settings.NAVER_TALK_PARTNER_ID}")
    logger.info("================================")
