"""
프로젝트 전역 설정 모듈
- 환경변수를 Pydantic Settings로 읽어서 타입 안전하게 관리
- Railway 배포 시 환경변수 자동 주입됨
"""
from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    """전역 설정 클래스 — .env 또는 시스템 환경변수에서 값을 로드"""

    # --- 서버 설정 ---
    PORT: int = 8000
    ENV: str = "production"
    LOG_LEVEL: str = "info"

    # --- 기본 Coze API 설정 ---
    COZE_BOT_ID: str = ""
    COZE_PAT: str = ""
    COZE_API_BASE: str = "https://api.coze.com"

    # --- 네이버톡톡 설정 ---
    NAVER_TALK_PARTNER_ID: str = ""
    NAVER_TALK_TOKEN: str = ""

    # --- 멀티 고객사 설정 파일 경로 ---
    CLIENT_CONFIG_JSON: str = "clients.json"

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": True,
    }


@lru_cache()
def get_settings() -> Settings:
    """설정 싱글턴 — 앱 전체에서 동일 인스턴스 재사용"""
    return Settings()
