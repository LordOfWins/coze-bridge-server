# Coze Bridge Server 운영 가이드

## 1. 서버 기본 정보

| 항목 | 값 |
|------|-----|
| EC2 인스턴스 | t3.micro (ap-northeast-2) |
| 퍼블릭 IP | 43.201.25.90 |
| 도메인 | imhyun-bot.duckdns.org |
| OS | Amazon Linux 2023 |
| SSH 접속 | `ssh -i key.pem ec2-user@43.201.25.90` |
| 프로젝트 경로 | ~/coze-bridge-server |
| TLS 인증서 | Let's Encrypt (Caddy 자동 갱신) |

## 2. 엔드포인트

| 용도 | URL |
|------|-----|
| 헬스체크 | https://imhyun-bot.duckdns.org/health |
| 카카오 스킬 | https://imhyun-bot.duckdns.org/skill/kakao |
| 네이버톡톡 웹훅 | https://imhyun-bot.duckdns.org/skill/navertalk |
| 설정 리로드 | POST https://imhyun-bot.duckdns.org/admin/reload-config |
| 고객사 목록 | GET https://imhyun-bot.duckdns.org/admin/clients |

## 3. 일상 운영 명령어

### SSH 접속
ssh -i key.pem ec2-user@43.201.25.90 cd ~/coze-bridge-server

### 서버 상태 확인
컨테이너 상태
docker compose ps

헬스체크
curl https://imhyun-bot.duckdns.org/health

리소스 사용량
docker stats --no-stream

디스크 사용량
df -h /

### 로그 확인
실시간 로그 (Ctrl+C로 종료)
docker compose logs -f app

최근 100줄
docker compose logs --tail=100 app

에러만 필터링
docker compose logs app | grep -i error | tail -20

Caddy(HTTPS) 로그
docker compose logs caddy

## 4. 서버 재시작

### 전체 재시작 (일반적인 문제 해결)
cd ~/coze-bridge-server docker compose restart

### 완전 재시작 (설정 변경 후)
cd ~/coze-bridge-server docker compose down docker compose up -d

### EC2 인스턴스 재부팅 시
- Docker restart policy가 `unless-stopped`로 설정되어 있어 자동 재시작됩니다
- 재부팅 후 1~2분 뒤 `docker compose ps`로 확인하세요

## 5. 설정 변경

### Coze 봇 토큰 변경 / 네이버톡톡 토큰 변경
cd ~/coze-bridge-server nano .env

값 수정 후 저장 (Ctrl+O -> Enter -> Ctrl+X)
docker compose down docker compose up -d

### 고객사 추가 / 설정 변경 (clients.json)
cd ~/coze-bridge-server nano clients.json

값 수정 후 저장
방법 1: 서버 재시작 없이 핫 리로드
curl -X POST https://imhyun-bot.duckdns.org/admin/reload-config

방법 2: 재시작
docker compose restart app

## 6. 코드 업데이트 (GitHub에서 최신 코드 받기)

cd ~/coze-bridge-server git pull origin main docker compose down docker compose build --no-cache app docker compose up -d

## 7. DuckDNS 도메인 관리

### IP 자동 갱신 (cron에 등록되어 있음)
현재 cron 확인
crontab -l

수동으로 IP 갱신
curl "https://www.duckdns.org/update?domains=imhyun-bot&token=7aabcbb5-3a15-4a81-a60c-178507e17bbd&ip="

### EC2 IP가 바뀌었을 때 (인스턴스 중지 후 재시작 시)
1. AWS 콘솔에서 새 퍼블릭 IP 확인
2. DuckDNS 수동 갱신 (위 curl 명령어 실행)
3. 또는 cron이 5분마다 자동 갱신함

> ⚠️ EC2 **중지(Stop)** 후 **시작(Start)** 하면 퍼블릭 IP가 바뀝니다
> **재부팅(Reboot)** 은 IP가 유지됩니다
> IP 고정이 필요하면 Elastic IP(무료 1개) 할당을 권장합니다

## 8. HTTPS 인증서

- Caddy가 Let's Encrypt 인증서를 자동 발급/갱신합니다
- 별도 관리 불필요
- 인증서 만료 걱정 없음 (Caddy가 자동으로 갱신)

### 인증서 상태 확인
브라우저에서 자물쇠 아이콘 클릭 -> 인증서 정보 확인
또는 CLI:
curl -vI https://imhyun-bot.duckdns.org/health 2>&1 | grep -E "expire|subject|issuer"

## 9. 트러블슈팅

### 증상: 카카오톡/톡톡에서 응답이 안 옴
1. 서버 상태 확인
docker compose ps

STATUS가 Up이 아니면 -> docker compose up -d
2. 헬스체크
curl https://imhyun-bot.duckdns.org/health

응답 없으면 -> docker compose restart
3. 로그에서 에러 확인
docker compose logs --tail=50 app | grep -i error

### 증상: "CozeToken balance insufficient" 에러
Coze API 토큰 잔액 부족
-> coze.com 로그인 -> Settings -> API -> 토큰 잔액 확인/충전

### 증상: 디스크 부족
디스크 확인
df -h /

Docker 불필요 이미지/캐시 정리
docker system prune -f

오래된 로그 확인 (로그 로테이션 설정되어 있어 보통 문제 없음)
docker compose logs app | wc -l


### 증상: 컨테이너가 계속 재시작됨
종료 이유 확인
docker compose logs --tail=30 app

메모리 부족 확인 (t3.micro = 1GB)
free -h docker stats --no-stream


## 10. CloudWatch 모니터링 설정 (권장)

### CPU 알람 설정
1. AWS Console -> EC2 -> 인스턴스 선택
2. 하단 **모니터링** 탭 클릭
3. **경보 관리** -> **경보 생성**
4. 지표: CPU 사용률 / 조건: 80% 초과 / 기간: 5분
5. 알림: 이메일 입력
6. **생성** 클릭

### 확인할 메트릭
- CPU 사용률: 평상시 5% 이하 / 80% 이상 지속되면 문제
- 네트워크: 트래픽 급증 시 확인
- 디스크 I/O: 급증 시 로그 과다 가능성

## 11. 보안 주의사항

- `.env` / `clients.json` / `.pem` 파일은 절대 GitHub에 push 금지
- `.gitignore`에 등록되어 있지만 수동 주의 필요
- SSH 키(.pem)는 안전한 곳에 백업 (분실 시 EC2 접속 불가)
- Security Group 포트는 22(SSH) / 80(HTTP) / 443(HTTPS)만 열려 있어야 함
- admin 엔드포인트 보호: ADMIN_SECRET 환경변수 설정 권장

### ADMIN_SECRET 설정 방법
nano .env

ADMIN_SECRET=원하는비밀키 추가
docker compose down docker compose up -d

이후 admin API 호출 시:
curl -H "X-Admin-Secret: 원하는비밀키" https://imhyun-bot.duckdns.org/admin/clients


## 12. 비용 안내

| 항목 | 비용 |
|------|------|
| EC2 t3.micro | 프리티어 12개월 무료 (이후 월 ~$10) |
| DuckDNS 도메인 | 무료 |
| Let's Encrypt 인증서 | 무료 |
| Coze API | 무료 30회 이후 토큰 구매 필요 |
| CloudWatch 기본 모니터링 | 무료 |

> 프리티어 만료 후 EC2 비용이 발생합니다
> 만료 시점은 AWS 콘솔 -> Billing 에서 확인 가능

