import os
import sys
import json
import argparse
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv
from google import genai

load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
KAKAO_API_KEY = os.getenv("KAKAO_API_KEY")


def validate_date(date_str):
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
        return True
    except ValueError:
        return False


def require_api_keys():
    if not GEMINI_API_KEY:
        print("[오류] GEMINI_API_KEY가 설정되지 않았습니다.")
        print("Windows PowerShell: $env:GEMINI_API_KEY=\"YOUR_KEY\"")
        print("macOS/Linux: export GEMINI_API_KEY=\"YOUR_KEY\"")
        sys.exit(1)

    if not KAKAO_API_KEY:
        print("[경고] KAKAO_API_KEY가 없습니다. 맛집 검색은 '데이터 없음'으로 진행합니다.")


client = genai.Client(api_key=GEMINI_API_KEY)


def clean_json_text(text):
    text = (text or "").strip()
    if text.startswith("```"):
        parts = text.split("```")
        if len(parts) >= 3:
            text = parts[1].strip()
            if text.lower().startswith("json"):
                text = text[4:].strip()
    return text


def validate_recommendation(data):
    required = {
        "recommended_city": str,
        "weather": str,
        "events": list,
        "reason": str,
    }
    for key, expected_type in required.items():
        if key not in data or not isinstance(data[key], expected_type):
            raise ValueError(f"필수 키/타입 오류: {key}")
    if not all(isinstance(x, str) for x in data["events"]):
        raise ValueError("events는 문자열 배열이어야 합니다.")
    return data


def get_llm_recommendation(date_str, errors):
    print(f"[1/3] 1차 추천 생성 중(LLM)... (기준일: {date_str})")

    prompt = f"""
여행 기준일은 {date_str}입니다.
국내 여행하기 좋은 도시 1곳을 추천하세요.
해당 시기의 일반적인 날씨, 행사/축제 후보 1~3개, 추천 이유를 작성하세요.

중요:
- 반드시 JSON 객체 하나만 출력하세요.
- Markdown 코드블록을 사용하지 마세요.
- JSON 외의 설명을 출력하지 마세요.
- 필수 키와 타입:
  recommended_city: string
  weather: string
  events: array of string
  reason: string
"""

    def call(prompt_text):
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt_text,
        )
        return response.text.strip()

    raw = ""
    try:
        raw = call(prompt)
        return validate_recommendation(json.loads(clean_json_text(raw)))
    except Exception as e:
        print("  - [경고] 1차 JSON 파싱 실패 → 1회 재시도합니다.")
        errors.append({
            "step": "llm_recommendation",
            "type": "PARSE_ERROR",
            "message": str(e),
        })

    retry_prompt = f"""
아래 원문을 참고하되, 반드시 다음 4개 키만 포함하는
유효한 JSON 객체를 출력하세요. 설명, Markdown, 코드블록은 금지합니다.

{{
  "recommended_city": "string",
  "weather": "string",
  "events": ["string"],
  "reason": "string"
}}

원문:
{raw}
"""
    try:
        fixed = call(retry_prompt)
        return validate_recommendation(json.loads(clean_json_text(fixed)))
    except Exception as e:
        errors.append({
            "step": "llm_recommendation_retry",
            "type": "FATAL_PARSE_ERROR",
            "message": str(e),
        })
        print("  - [오류] 재시도도 실패했습니다. 기본 데이터를 사용합니다.")
        return {
            "recommended_city": "서울",
            "weather": "LLM JSON 파싱 실패",
            "events": [],
            "reason": "LLM 응답 파싱 실패로 기본 지역을 사용합니다.",
        }


def search_restaurants(city, errors):
    print(f"[2/3] 맛집 검색 중(카카오 Local)... ({city} 맛집)")

    if not KAKAO_API_KEY:
        errors.append({
            "step": "place_search",
            "type": "AUTH_ERROR",
            "message": "KAKAO_API_KEY is missing",
        })
        return []

    url = "https://dapi.kakao.com/v2/local/search/keyword.json"
    headers = {"Authorization": f"KakaoAK {KAKAO_API_KEY}"}
    params = {"query": f"{city} 맛집", "size": 5}

    try:
        response = requests.get(url, headers=headers, params=params, timeout=10)

        if response.status_code in (401, 403):
            errors.append({
                "step": "place_search",
                "type": "AUTH_ERROR",
                "message": f"HTTP {response.status_code}",
            })
            print(f"  - [오류] 인증 실패(HTTP {response.status_code}) → 맛집=데이터 없음")
            return []

        response.raise_for_status()
        data = response.json()
        documents = data.get("documents", [])

        if not documents:
            errors.append({
                "step": "place_search",
                "type": "EMPTY_RESULT",
                "message": f"0 results for query={city} 맛집",
            })
            print("  - 검색 결과 0건 → 다음 단계로 진행합니다.")
            return []

        restaurants = []
        for doc in documents:
            restaurants.append({
                "name": doc.get("place_name", ""),
                "address": doc.get("road_address_name") or doc.get("address_name", ""),
                "category": doc.get("category_name", ""),
                "url": doc.get("place_url", ""),
                "lat": float(doc["y"]) if doc.get("y") else None,
                "lng": float(doc["x"]) if doc.get("x") else None,
            })

        print(f"  - 맛집 {len(restaurants)}곳 검색 완료")
        return restaurants

    except requests.RequestException as e:
        errors.append({
            "step": "place_search",
            "type": "NETWORK_OR_API_ERROR",
            "message": str(e),
        })
        print("  - [오류] 장소 API 호출 실패 → 맛집=데이터 없음")
        return []
    except (ValueError, KeyError, TypeError) as e:
        errors.append({
            "step": "place_search",
            "type": "PARSE_ERROR",
            "message": str(e),
        })
        print("  - [오류] 장소 API 응답 파싱 실패 → 맛집=데이터 없음")
        return []


def generate_final_report(date_str, recommendation, restaurants, errors):
    print("[3/3] 최종 리포트 생성 중(LLM)...")

    prompt = f"""
다음 데이터를 바탕으로 국내 여행 추천 리포트를 Markdown으로 작성하세요.

여행 기준일: {date_str}
1차 추천 JSON:
{json.dumps(recommendation, ensure_ascii=False, indent=2)}

맛집 목록:
{json.dumps(restaurants, ensure_ascii=False, indent=2)}

오류 목록:
{json.dumps(errors, ensure_ascii=False, indent=2)}

반드시 다음 순서의 섹션을 포함하세요.
# {date_str} 국내 여행 추천 리포트
## 추천 지역
## 추천 이유
## 날씨 요약
## 행사/축제
## 맛집 추천
## 1일 일정 제안
### 오전
### 오후
### 저녁
## 오류 요약(errors)

맛집 목록이 비어 있으면 반드시 '데이터 없음'이라고 표시하세요.
실제 확인되지 않은 세부 일정이나 가격은 사실처럼 단정하지 마세요.
"""

    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
        )
        return response.text.strip()
    except Exception as e:
        errors.append({
            "step": "final_report",
            "type": "LLM_ERROR",
            "message": str(e),
        })
        print("  - [오류] 최종 LLM 생성 실패 → 기본 Markdown으로 저장합니다.")
        restaurant_text = (
            "- 데이터 없음"
            if not restaurants
            else "\n".join(
                f"- **{r['name']}** — {r['address']} ({r['category']})"
                for r in restaurants
            )
        )
        event_text = (
            "- 데이터 없음"
            if not recommendation.get("events")
            else "\n".join(f"- {e}" for e in recommendation["events"])
        )
        return f"""# {date_str} 국내 여행 추천 리포트

## 추천 지역
{recommendation.get("recommended_city", "정보 없음")}

## 추천 이유
{recommendation.get("reason", "정보 없음")}

## 날씨 요약
{recommendation.get("weather", "정보 없음")}

## 행사/축제
{event_text}

## 맛집 추천
{restaurant_text}

## 1일 일정 제안
### 오전
지역의 대표 관광지를 중심으로 여유 있게 이동합니다.

### 오후
맛집 식사 후 주변 관광지나 카페를 방문합니다.

### 저녁
대표 명소를 둘러보고 숙소로 이동합니다.

## 오류 요약(errors)
{json.dumps(errors, ensure_ascii=False, indent=2)}
"""


def main():
    parser = argparse.ArgumentParser(description="CLI 기반 AI 국내 여행 플래너")
    parser.add_argument("-date", "--date", required=True, help="여행 기준일 (YYYY-MM-DD 형식)")
    args = parser.parse_args()

    if not validate_date(args.date):
        parser.print_usage()
        print(f"[오류] 날짜 형식이 올바르지 않습니다: {args.date}")
        print('사용법: python travel_planner.py -date "YYYY-MM-DD"')
        sys.exit(1)

    require_api_keys()
    errors = []

    recommendation = get_llm_recommendation(args.date, errors)
    city = recommendation.get("recommended_city", "서울").strip() or "서울"

    restaurants = search_restaurants(city, errors)
    report = generate_final_report(args.date, recommendation, restaurants, errors)

    results_dir = Path("results")
    results_dir.mkdir(exist_ok=True)

    raw_path = results_dir / f"{args.date}_travel_data.json"
    md_path = results_dir / f"{args.date}_travel_plan.md"

    raw_data = {
        "date": args.date,
        "recommendation": recommendation,
        "restaurants": restaurants,
        "errors": errors,
    }

    raw_path.write_text(
        json.dumps(raw_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    md_path.write_text(report, encoding="utf-8")

    print("\n완료!")
    print(f"- 원본 JSON: {raw_path}")
    print(f"- 최종 리포트: {md_path}")


if __name__ == "__main__":
    main()