"""
Coze API 연동 테스트 스크립트
- 터미널에서 직접 실행하여 Coze 봇 응답 확인
- 사용법: source .venv/bin/activate && python test_coze.py
"""
import asyncio
from app.modules.coze_client import get_coze_client
from app.config.settings import get_settings
from dotenv import load_dotenv

load_dotenv()


async def main():
    settings = get_settings()
    print(f"Bot ID: {settings.COZE_BOT_ID}")
    print(f"API Base: {settings.COZE_API_BASE}")
    print(f"PAT: {settings.COZE_PAT[:12]}********")
    print("-" * 50)

    client = get_coze_client()

    # 테스트 메시지 전송
    result = await client.chat(
        user_id="test_user_001",
        message="안녕하세요",
    )

    print(f"성공: {result['success']}")
    print(f"타임아웃: {result['timed_out']}")
    print(f"텍스트: {result['text']}")
    print(f"카드: {result['cards']}")
    print(f"chat_id: {result['chat_id']}")
    print(f"conversation_id: {result['conversation_id']}")
    print(f"에러: {result['error']}")


if __name__ == "__main__":
    asyncio.run(main())
