# AI 국내 여행 플래너

## 1. 프로그램 개요

사용자가 입력한 여행 기준일(`YYYY-MM-DD`)을 바탕으로 Gemini LLM이 국내 추천 지역, 일반적인 날씨, 행사/축제 후보, 추천 이유를 JSON으로 생성합니다.

생성된 `recommended_city`를 다음 단계의 입력으로 연결하여 Kakao Local API에서 해당 지역의 맛집을 최대 5곳 검색합니다.

마지막으로 1차 추천 JSON과 맛집 검색 결과를 다시 Gemini에 전달하여 Markdown 여행 리포트를 생성합니다.

### 처리 흐름

```text
CLI 날짜 입력
   ↓
Gemini 1차 추천
   ↓
구조화 JSON 파싱
   ↓
recommended_city 추출
   ↓
Kakao Local 맛집 검색
   ↓
1차 JSON + 맛집 + errors
   ↓
Gemini 최종 Markdown 리포트
   ↓
results/ 저장
```

## 2. 개발 환경

- Python 3.10 이상
- Gemini API
- Kakao Local API
- `requests`
- `python-dotenv`
- `google-genai`

## 3. 설치

프로젝트 폴더에서 다음을 실행합니다.

```bash
pip install -r requirements.txt
```

## 4. API 키 설정

API 키는 코드에 직접 작성하지 않습니다.

### 방법 A: `.env`

프로젝트 폴더에 `.env` 파일을 만들고 다음처럼 설정합니다.

```env
GEMINI_API_KEY=YOUR_GEMINI_KEY
KAKAO_API_KEY=YOUR_KAKAO_REST_API_KEY
```

`YOUR_...` 부분에는 실제 키를 입력합니다.

### 방법 B: Windows PowerShell

```powershell
$env:GEMINI_API_KEY="YOUR_KEY"
$env:KAKAO_API_KEY="YOUR_KEY"
```

### 방법 C: macOS/Linux

```bash
export GEMINI_API_KEY="YOUR_KEY"
export KAKAO_API_KEY="YOUR_KEY"
```

실제 API 키는 README, 코드, Git 저장소, 로그, 결과 JSON에 넣지 마세요.

## 5. 실행 방법

```bash
python travel_planner.py -date "2026-08-20"
```

날짜 형식이 잘못되면 사용법을 출력하고 종료합니다.

예:

```text
python travel_planner.py -date "2026-08-20"
```

## 6. 결과물

실행이 완료되면 `results/` 폴더에 다음 두 파일이 생성됩니다.

```text
results/
├── 2026-08-20_travel_data.json
└── 2026-08-20_travel_plan.md
```

### 원본 JSON

다음 정보를 포함합니다.

- 여행 기준일
- 1차 Gemini 추천 JSON
- Kakao 맛집 검색 결과
- errors 배열

### 최종 Markdown

다음 항목을 포함합니다.

- 추천 지역
- 추천 이유
- 날씨 요약
- 행사/축제
- 맛집 추천
- 오전/오후/저녁 1일 일정
- 오류 요약

## 7. 오류 처리

### Gemini API 키 미설정

프로그램을 즉시 종료하고 키 설정 방법을 안내합니다.

### Kakao API 실패

인증 오류, 네트워크 오류, 응답 파싱 오류 등이 발생해도 프로그램을 중단하지 않습니다.

맛집 목록을 `데이터 없음`으로 처리하고 최종 리포트 생성을 계속합니다.

### 맛집 검색 결과 0건

프로그램을 중단하지 않고 `restaurants: []`로 저장하며 리포트에는 `데이터 없음`으로 표시합니다.

### Gemini JSON 파싱 실패

첫 번째 응답의 JSON 파싱에 실패하면 JSON 형식만 다시 출력하도록 1회 재요청합니다.

재시도도 실패하면 기본 추천 데이터를 사용하고 오류를 `errors`에 기록합니다.

무한 재시도는 하지 않습니다.

## 8. REST API 학습 포인트

### GET과 POST

- GET: 서버에서 데이터를 조회할 때 주로 사용합니다.
- POST: 서버에 데이터를 전달하여 생성/처리할 때 주로 사용합니다.

이 프로그램의 Kakao Local 검색은 GET 요청으로 검색 조건을 전달합니다.

HTTP 요청은 일반적으로 다음 요소로 구성됩니다.

```text
URL
HTTP Method
Headers
Query Parameters 또는 Body
Response
```

Kakao Local API에서는 인증 정보를 HTTP Header에 넣어 요청합니다.

## 9. LLM 출력의 구조화

LLM에게 자유로운 문장을 요청하는 대신 다음 JSON 구조를 요구합니다.

```json
{
  "recommended_city": "제주",
  "weather": "해당 시기 일반적 날씨 요약",
  "events": ["행사 후보 1", "행사 후보 2"],
  "reason": "추천 근거"
}
```

프로그램은 JSON에서 `recommended_city`를 꺼내 Kakao Local 검색의 입력값으로 사용합니다.

즉,

```text
LLM JSON
  ↓
recommended_city
  ↓
Kakao Local query
  ↓
맛집 JSON
```

이라는 데이터 연결 구조를 학습할 수 있습니다.

## 10. API 키 보안

API 키를 코드에 직접 작성하면 GitHub 공유, 파일 제출, 화면 캡처 등을 통해 키가 노출될 수 있습니다.

`.env` 또는 환경변수를 사용하면 다음과 같은 장점이 있습니다.

1. 협업/공유 과정에서 키가 공개될 가능성을 줄입니다.
2. 키를 교체할 때 코드를 수정할 필요가 없습니다.
3. API 과금 및 사용량 사고를 예방하는 데 도움이 됩니다.

`.env`를 사용하는 경우 `.gitignore`에 다음을 추가하는 것을 권장합니다.

```gitignore
.env
__pycache__/
results/
```

실제 키는 제출물에 포함하지 않습니다.

## 11. 주의사항

이 프로그램의 LLM 날씨/행사 정보는 과제의 구조화 및 API 연결 학습을 위한 예시 정보입니다. 실제 여행 전에는 공식 날씨 및 행사 정보를 별도로 확인해야 합니다.

Kakao API의 사용량, 인증 방식, 쿼터 및 서비스 정책은 변경될 수 있으므로 실제 사용 시 공식 문서를 확인하세요.
