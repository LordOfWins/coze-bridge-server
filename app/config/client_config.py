"""
멀티 고객사 설정 관리 모듈

설계 원칙:
- clients.json 파일 하나로 모든 고객사의 Coze/네이버톡톡 설정을 관리
- 서버 코드 수정 없이 설정값만 변경하여 고객사 추가/수정/삭제 가능
- Railway 환경에서는 CLIENT_CONFIG_JSON 환경변수로 파일 경로 지정
- 서버 시작 시 1회 로드 후 캐싱 (핫 리로드 엔드포인트 별도 제공)

사용 예시:
    config = get_client_config("kimhyun")
    coze_client = CozeClient(bot_id=config.coze_bot_id, pat=config.coze_pat)

고객사 추가 방법:
    1. clients.json에 새 블록 추가
    2. 서버 재시작 또는 /admin/reload-config 호출
    3. /skill/kakao/{client_key} 또는 /skill/navertalk/{client_key} 로 접근
"""
import json
import os
from dataclasses import dataclass, field
from typing import Optional
from pathlib import Path

from app.config.logging import logger


@dataclass(frozen=True)
class ClientConfig:
    """
    개별 고객사 설정 데이터 클래스

    frozen=True로 설정하여 런타임 중 실수로 값이 변경되는 것을 방지
    모든 필드에 기본값을 제공하여 JSON에서 일부 키가 누락되어도 안전하게 동작
    """
    # --- 고객사 식별 ---
    client_key: str = ""                          # 고객사 고유 키 (URL 경로에 사용)
    label: str = ""                               # 고객사 표시명 (로그/관리용)

    # --- Coze API 설정 ---
    coze_bot_id: str = ""                         # Coze 봇 ID
    coze_pat: str = ""                            # Coze Personal Access Token
    coze_api_base: str = "https://api.coze.com"   # Coze API 기본 URL (글로벌/중국 구분)

    # --- 네이버톡톡 설정 ---
    naver_talk_partner_id: str = ""               # 네이버톡톡 파트너 ID
    naver_talk_token: str = ""                    # 네이버톡톡 인증 토큰

    # --- 동작 설정 ---
    timeout_seconds: float = 4.5                  # Coze 응답 대기 타임아웃 (초)
    enabled: bool = True                          # 고객사 활성 상태 (false면 요청 거부)

    def is_valid(self) -> bool:
        """필수 설정값이 모두 채워져 있는지 검증"""
        return bool(self.coze_bot_id and self.coze_pat)

    def masked_summary(self) -> str:
        """로그 출력용 마스킹된 설정 요약 — PAT/토큰은 앞 8자만 표시"""
        pat_masked = f"{self.coze_pat[:12]}********" if self.coze_pat else "(미설정)"
        token_masked = f"{self.naver_talk_token[:6]}********" if self.naver_talk_token else "(미설정)"
        return (
            f"[{self.client_key}] {self.label} | "
            f"bot={self.coze_bot_id} | "
            f"pat={pat_masked} | "
            f"api={self.coze_api_base} | "
            f"naver={self.naver_talk_partner_id} | "
            f"token={token_masked} | "
            f"timeout={self.timeout_seconds}s | "
            f"enabled={self.enabled}"
        )


class ClientConfigManager:
    """
    멀티 고객사 설정 매니저

    역할:
    1. clients.json 파일에서 전체 고객사 설정을 로드
    2. client_key로 개별 고객사 설정을 조회
    3. 설정 핫 리로드 지원 (서버 재시작 없이 갱신)
    4. 환경변수 폴백 — clients.json이 없으면 .env의 기본값 사용

    싱글턴 패턴:
    - get_config_manager() 팩토리 함수를 통해 전역 단일 인스턴스 사용
    """

    def __init__(self, config_path: Optional[str] = None):
        """
        Args:
            config_path: clients.json 파일 경로 (미지정 시 환경변수 또는 기본값)
        """
        # 설정 파일 경로 결정: 파라미터 -> 환경변수 -> 기본값
        self._config_path = config_path or os.getenv("CLIENT_CONFIG_JSON", "clients.json")
        # 고객사 설정 저장소: {client_key: ClientConfig}
        self._clients: dict[str, ClientConfig] = {}
        # 초기 로드 실행
        self._load()

    def _load(self) -> None:
        """
        clients.json 파일에서 설정을 로드하여 _clients 딕셔너리에 저장

        파일이 없거나 파싱 실패 시 -> 환경변수 기본값으로 폴백 생성
        각 고객사 블록을 ClientConfig 객체로 변환
        """
        config_file = Path(self._config_path)

        if not config_file.exists():
            # clients.json이 없으면 환경변수에서 기본 설정 생성
            logger.warning(f"고객사 설정 파일 없음: {self._config_path} -> 환경변수 폴백 사용")
            self._load_from_env()
            return

        try:
            raw_text = config_file.read_text(encoding="utf-8")
            raw_data = json.loads(raw_text)
        except (json.JSONDecodeError, OSError) as e:
            logger.error(f"고객사 설정 파일 파싱 실패: {e} -> 환경변수 폴백 사용")
            self._load_from_env()
            return

        # JSON 최상위 키를 순회하며 고객사 설정 로드
        loaded_count = 0
        for key, value in raw_data.items():
            # _로 시작하는 키는 메타/설명용이므로 스킵
            if key.startswith("_"):
                continue

            if not isinstance(value, dict):
                logger.warning(f"고객사 설정 스킵 (dict가 아님): {key}")
                continue

            # ClientConfig 객체 생성 — JSON 키와 dataclass 필드 매핑
            config = ClientConfig(
                client_key=key,
                label=value.get("label", ""),
                coze_bot_id=value.get("coze_bot_id", ""),
                coze_pat=value.get("coze_pat", ""),
                coze_api_base=value.get("coze_api_base", "https://api.coze.com"),
                naver_talk_partner_id=value.get("naver_talk_partner_id", ""),
                naver_talk_token=value.get("naver_talk_token", ""),
                timeout_seconds=float(value.get("timeout_seconds", 4.5)),
                enabled=bool(value.get("enabled", True)),
            )

            # 필수값 검증
            if not config.is_valid():
                logger.warning(f"고객사 설정 불완전 (필수값 누락): {key}")
                # 불완전해도 일단 등록 (enabled=false 상태로 쓸 수 있음)

            self._clients[key] = config
            loaded_count += 1
            logger.info(f"고객사 설정 로드: {config.masked_summary()}")

        logger.info(f"총 {loaded_count}개 고객사 설정 로드 완료")

    def _load_from_env(self) -> None:
        """
        환경변수에서 기본(default) 고객사 설정을 생성하는 폴백 메서드

        clients.json이 없는 환경(로컬 개발/초기 배포)에서도 동작하도록 보장
        .env 또는 Railway 환경변수에서 값을 읽음
        """
        config = ClientConfig(
            client_key="default",
            label="환경변수 기본 설정",
            coze_bot_id=os.getenv("COZE_BOT_ID", ""),
            coze_pat=os.getenv("COZE_PAT", ""),
            coze_api_base=os.getenv("COZE_API_BASE", "https://api.coze.com"),
            naver_talk_partner_id=os.getenv("NAVER_TALK_PARTNER_ID", ""),
            naver_talk_token=os.getenv("NAVER_TALK_TOKEN", ""),
            timeout_seconds=float(os.getenv("COZE_TIMEOUT", "4.5")),
            enabled=True,
        )
        self._clients["default"] = config
        logger.info(f"환경변수 폴백 설정 로드: {config.masked_summary()}")

    def get(self, client_key: Optional[str] = None) -> Optional[ClientConfig]:
        """
        고객사 설정 조회

        조회 우선순위:
        1. client_key가 지정되면 해당 키로 조회
        2. client_key가 None이면 "default" 조회
        3. "default"도 없으면 첫 번째 등록된 설정 반환
        4. 아무것도 없으면 None 반환

        Args:
            client_key: 고객사 고유 키 (URL 경로에서 추출)

        Returns:
            ClientConfig 또는 None (설정 없음)
        """
        # 명시적 키로 조회
        if client_key and client_key in self._clients:
            config = self._clients[client_key]
            if not config.enabled:
                logger.warning(f"비활성 고객사 요청: {client_key}")
                return None
            return config

        # 기본 설정 조회
        if "default" in self._clients:
            return self._clients["default"]

        # 첫 번째 활성 설정 반환
        for config in self._clients.values():
            if config.enabled:
                return config

        logger.error("사용 가능한 고객사 설정이 없습니다")
        return None

    def get_all(self) -> dict[str, ClientConfig]:
        """전체 고객사 설정 조회 (관리/모니터링용)"""
        return dict(self._clients)

    def reload(self) -> int:
        """
        설정 핫 리로드 — 서버 재시작 없이 clients.json을 다시 읽음

        Returns:
            로드된 고객사 수
        """
        logger.info("고객사 설정 핫 리로드 시작")
        self._clients.clear()
        self._load()
        return len(self._clients)

    def get_by_naver_partner_id(self, partner_id: str) -> Optional[ClientConfig]:
        """
        네이버톡톡 파트너 ID로 고객사 설정 역조회

        네이버톡톡 웹훅은 URL 경로에 client_key가 없을 수 있으므로
        파트너 ID로 역방향 매칭하는 메서드 제공

        Args:
            partner_id: 네이버톡톡 파트너 ID

        Returns:
            매칭되는 ClientConfig 또는 None
        """
        for config in self._clients.values():
            if config.naver_talk_partner_id == partner_id and config.enabled:
                return config
        return None


# === 모듈 레벨 싱글턴 ===
_manager: Optional[ClientConfigManager] = None


def get_config_manager() -> ClientConfigManager:
    """
    ClientConfigManager 싱글턴 팩토리

    전역에서 동일한 매니저 인스턴스를 재사용
    최초 호출 시 clients.json 로드
    """
    global _manager
    if _manager is None:
        _manager = ClientConfigManager()
    return _manager


def get_client_config(client_key: Optional[str] = None) -> Optional[ClientConfig]:
    """
    고객사 설정 조회 헬퍼 — 가장 많이 사용되는 단축 함수

    사용 예:
        config = get_client_config("kimhyun")
        config = get_client_config()  # default 설정
    """
    return get_config_manager().get(client_key)
