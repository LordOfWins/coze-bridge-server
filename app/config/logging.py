"""
로깅 설정 모듈
- PAT/토큰 등 민감 정보가 로그에 노출되지 않도록 마스킹 필터 적용
"""
import logging
import re


class SensitiveDataFilter(logging.Filter):
    """로그 메시지에서 민감 정보를 자동 마스킹하는 필터"""

    PATTERNS = [
        (re.compile(r"(pat_[A-Za-z0-9]{8})[A-Za-z0-9]+"), r"\1********"),
        (re.compile(r"(Bearer\s+[A-Za-z0-9_]{8})[A-Za-z0-9_\-+/=]+"), r"\1********"),
        (re.compile(r"(token[\"']?\s*[:=]\s*[\"']?[A-Za-z0-9+/]{8})[A-Za-z0-9+/=]+"), r"\1********"),
    ]

    def filter(self, record: logging.LogRecord) -> bool:
        """로그 레코드의 메시지에서 민감 정보를 마스킹"""
        if isinstance(record.msg, str):
            for pattern, replacement in self.PATTERNS:
                record.msg = pattern.sub(replacement, record.msg)
        if record.args:
            args = list(record.args) if isinstance(record.args, tuple) else [record.args]
            for i, arg in enumerate(args):
                if isinstance(arg, str):
                    for pattern, replacement in self.PATTERNS:
                        args[i] = pattern.sub(replacement, args[i])
            record.args = tuple(args)
        return True


def setup_logger(name: str = "bridge") -> logging.Logger:
    """앱 전역 로거 생성 — SensitiveDataFilter 자동 적용"""
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        formatter = logging.Formatter(
            "[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )
        handler.setFormatter(formatter)
        handler.addFilter(SensitiveDataFilter())
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger


logger = setup_logger("bridge")
