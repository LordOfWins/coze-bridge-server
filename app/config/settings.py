"""
프로젝트 전역 설정 모듈
- 환경변수를 Pydantic Settings로 읽어서 타입 안전하게 관리
- Railway 배포 시 환경변수 자동 주입됨
- 멀티 고객사 환경에서는 이 설정이 폴백(기본값) 역할
- 실제 고객사별 설정은 clients.json -> ClientConfigManager에서 관리
"""
from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    """전역 설정 클래스 — .env 또는 시스템 환경변수에서 값을 로드"""

    # --- 서버 설정 ---
    PORT: int = 8000                              # 서버 포트 (Railway는 자동 주입)
    ENV: str = "production"                       # 환경 구분: development / production
    LOG_LEVEL: str = "info"                       # 로그 레벨

    # --- 기본 Coze API 설정 (clients.json 없을 때 폴백) ---
    COZE_BOT_ID: str = ""                         # 기본 Coze 봇 ID
    COZE_PAT: str = ""                            # 기본 Coze PAT
    COZE_API_BASE: str = "https://api.coze.com"   # Coze API 기본 URL
    COZE_TIMEOUT: float = 4.5                     # Coze 응답 대기 타임아웃 (초)

    # --- 네이버톡톡 설정 (clients.json 없을 때 폴백) ---
    NAVER_TALK_PARTNER_ID: str = ""               # 네이버톡톡 파트너 ID
    NAVER_TALK_TOKEN: str = ""                    # 네이버톡톡 인증 토큰

    # --- 멀티 고객사 설정 ---
    CLIENT_CONFIG_JSON: str = "clients.json"      # 고객사 설정 파일 경로

    # --- 관리자 설정 ---
    ADMIN_SECRET: str = ""                        # /admin 엔드포인트 인증키 (선택)

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": True,
    }


@lru_cache()
def get_settings() -> Settings:
    """설정 싱글턴 — 앱 전체에서 동일 인스턴스 재사용"""
    return Settings()
