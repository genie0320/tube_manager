# 📺 TubeSSOT (YouTube Subscription SSOT Manager)

> **YouTube 다중 계정 구독 자산 통합 및 수명주기 관리자**  
> N개의 Google 계정 및 브랜드 채널에 흩어진 구독 목록을 단일 진실 공급원(SSOT)으로 통합하고, 최신 활동성(Liveness: 🟢활성 / 🟡정체 / 🔴휴면)을 판정하여 피드 오염을 방지하고 로컬 자산(SQLite, Excel)으로 영구 보존하는 로컬 웹 애플리케이션입니다.

---

## ✨ 핵심 기능

1. **단일 진실 공급원(SSOT) 구축**:
   - 업무용, 개인용, 스터디용 등 다수의 Google 계정/브랜드 채널을 OAuth 2.0으로 연동하여 `channel_id` 기준으로 중복 없이 하나의 데이터베이스에 병합 관리합니다.
2. **신호등(Traffic Light) 수명주기 판정**:
   - 각 채널의 최근 3개 동영상 및 마지막 게시일을 분석하여 4단계 상태 자동 부여:
     - 🟢 **GREEN (활성)**: 최근 90일(3개월) 이내 업로드 확인
     - 🟡 **YELLOW (정체)**: 91일 ~ 180일(3~6개월) 업로드 정체
     - 🔴 **RED (휴면)**: 180일(6개월) 이상 업로드 중단 (정리 최우선 대상)
     - ⚪ **UNKNOWN (미확인)**: 업로드된 동영상이 0개인 채널
3. **극단적 API 쿼터 절감**:
   - 공식 YouTube Data API v3의 재생목록 캐시(`UU...`) 구조 및 50개 배치 조회를 활용하여, 500개 채널 동기화 시 일일 무료 할당량(10,000 유닛)의 **5% 미만(약 360 유닛)**만 소모합니다.
4. **온디맨드 상세 검사기 & API 원시 JSON 아카이빙**:
   - 채널 클릭 시에만 최근 영상 3개(썸네일, 제목, 게시일, 링크)를 지연 로딩하여 렌더링 부하를 최소화합니다.
   - API 원시 응답 전문(Raw JSON)을 보존하여 언제든 열람할 수 있습니다.
5. **서식화된 엑셀(.xlsx) 내보내기**:
   - 상태별 조건부 서식(초록/노랑/빨강)과 최근 영상 메타데이터가 적용된 엑셀 보고서 다운로드를 지원합니다.

---

## 🛠️ 시작하기 (Quick Start)

### 1. 사전 준비 (Google Cloud Console OAuth 2.0)
1. [Google Cloud Console](https://console.cloud.google.com/) 접속 및 프로젝트 생성
2. **API 및 서비스 > 라이브러리**에서 **YouTube Data API v3** 검색 후 사용 설정
3. **API 및 서비스 > OAuth 동의 화면** 설정 (사용자 유형: 외부, 테스트 사용자 등록)
4. **API 및 서비스 > 사용자 인증 정보**에서 **OAuth 클라이언트 ID** 생성:
   - 애플리케이션 유형: **데스크톱 앱 (Desktop App)**
   - 승인된 리디렉션 URI: `http://localhost:8080/`
5. 발급된 `Client ID`와 `Client Secret`을 확인하거나 `client_secret.json` 파일을 다운로드합니다.

### 2. 실행 (Windows 원클릭 실행)
프로젝트 루트의 `run.bat` 파일을 더블 클릭하거나 명령 프롬프트에서 실행합니다:
```cmd
run.bat
```
*(가상환경 `.venv` 자동 생성 및 의존성 패키지가 자동 설치된 후 브라우저에 Streamlit 대시보드가 열립니다.)*

---

## 📁 프로젝트 구조

```text
tubeManager/
├── .venv/                 # 파이썬 가상환경
├── app.py                 # Streamlit 통합 대시보드 UI (사이드바 & 4개 탭)
├── collector.py           # YouTube Data API v3 수집 및 Liveness 판정 파이프라인
├── db.py                  # SQLite 로컬 데이터베이스 모듈 (CRUD, 쿼터 추적, 설정)
├── test_core.py           # 핵심 비즈니스 로직 및 DB 단위 테스트
├── requirements.txt       # 의존성 패키지 목록
├── run.bat                # 윈도우 원클릭 실행 스크립트
├── .gitignore             # 보안 및 캐시 파일 제외 규칙
├── agent.md               # 에이전트 행동 지침
├── prd.md.txt             # 제품 요구사항 정의서 (PRD)
└── trd.md.txt             # 기술 요구사항 정의서 (TRD)
```

---

## 🧪 테스트 실행
로컬 단위 테스트를 통해 실제 API 키 없이도 데이터베이스 CRUD, Liveness 판정 알고리즘, 엑셀 생성을 검증할 수 있습니다:
```cmd
.\.venv\Scripts\python test_core.py
```
