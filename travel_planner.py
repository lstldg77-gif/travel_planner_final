import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
KAKAO_API_KEY = os.getenv("KAKAO_API_KEY")
MODEL_NAME = "gemini-3.6-flash"


def validate_date(date_str):
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
        return True
    except ValueError:
        return False


def require_api_keys():
    if not GEMINI_API_KEY:
        print("[오류] GEMINI_API_KEY가 설정되지 않았습니다.")
        print('사용 예: GEMINI_API_KEY="YOUR_KEY"')
        sys.exit(1)
    if not KAKAO_API_KEY:
        print("[경고] KAKAO_API_KEY가 없습니다. 맛집 검색은 '데이터 없음'으로 진행합니다.")


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
    if not all(isinstance(event, str) for event in data["events"]):
        raise ValueError("events는 문자열 배열이어야 합니다.")
    return data


def create_client():
    return genai.Client(
        api_key=GEMINI_API_KEY,
        http_options=types.HttpOptions(timeout=30000),
    )


def ask_gemini(client, prompt):
    chat = client.chats.create(model=MODEL_NAME)
    response = chat.send_message(prompt)
    return (response.text or "").strip()


def get_llm_recommendation(client, date_str, errors):
    print(f"[1/3] 1차 추천 생성 중(LLM)... (기준일: {date_str})")
    prompt = f"""
여행 기준일은 {date_str}입니다.
국내 여행하기 좋은 도시 1곳을 추천하세요.
해당 시기의 일반적인 날씨, 행사/축제 후보 1~3개, 추천 이유를 작성하세요.
반드시 JSON 객체 하나만 출력하고 Markdown이나 설명은 출력하지 마세요.
필수 키와 타입: recommended_city(string), weather(string), events(array of string), reason(string)
"""

    raw = ""
    try:
        raw = ask_gemini(client, prompt)
        return validate_recommendation(json.loads(clean_json_text(raw)))
    except Exception as error:
        errors.append({
            "step": "llm_recommendation",
            "type": "PARSE_ERROR",
            "message": str(error),
        })
        print("  - [경고] 1차 JSON 파싱 실패 -> 1회 재시도합니다.")

    retry_prompt = f"""
다음 원문을 참고해 유효한 JSON 객체만 출력하세요.
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
        fixed = ask_gemini(client, retry_prompt)
        return validate_recommendation(json.loads(clean_json_text(fixed)))
    except Exception as error:
        errors.append({
            "step": "llm_recommendation_retry",
            "type": "FATAL_PARSE_ERROR",
            "message": str(error),
        })
        print("  - [오류] 재시도도 실패했습니다. 기본 데이터를 사용합니다.")
        return {
            "recommended_city": "서울",
            "weather": "LLM 응답 없음",
            "events": [],
            "reason": "LLM 응답 실패로 기본 지역을 사용합니다.",
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

    try:
        response = requests.get(
            "https://dapi.kakao.com/v2/local/search/keyword.json",
            headers={"Authorization": f"KakaoAK {KAKAO_API_KEY}"},
            params={"query": f"{city} 맛집", "size": 5},
            timeout=10,
        )
        if response.status_code in (401, 403):
            raise requests.HTTPError(f"HTTP {response.status_code}")
        response.raise_for_status()
        documents = response.json().get("documents", [])
        restaurants = [
            {
                "name": doc.get("place_name", ""),
                "address": doc.get("road_address_name") or doc.get("address_name", ""),
                "category": doc.get("category_name", ""),
                "url": doc.get("place_url", ""),
                "lat": float(doc["y"]) if doc.get("y") else None,
                "lng": float(doc["x"]) if doc.get("x") else None,
            }
            for doc in documents
        ]
        print(f"  - 맛집 {len(restaurants)}곳 검색 완료")
        return restaurants
    except (requests.RequestException, ValueError, KeyError, TypeError) as error:
        errors.append({
            "step": "place_search",
            "type": "API_ERROR",
            "message": str(error),
        })
        print("  - [오류] 장소 API 호출 또는 응답 처리 실패 -> 맛집=데이터 없음")
        return []


def fallback_report(date_str, recommendation, restaurants, errors):
    restaurant_text = "- 데이터 없음" if not restaurants else "\n".join(
        f"- **{item['name']}** - {item['address']} ({item['category']})"
        for item in restaurants
    )
    event_text = "- 데이터 없음" if not recommendation["events"] else "\n".join(
        f"- {event}" for event in recommendation["events"]
    )
    return f"""# {date_str} 국내 여행 추천 리포트

## 추천 지역
{recommendation['recommended_city']}

## 추천 이유
{recommendation['reason']}

## 날씨 요약
{recommendation['weather']}

## 행사/축제
{event_text}

## 맛집 추천
{restaurant_text}

## 1일 일정 제안
### 오전
지역의 대표 관광지를 중심으로 이동합니다.
### 오후
맛집 식사 후 주변 관광지를 방문합니다.
### 저녁
대표 명소를 둘러보고 숙소로 이동합니다.

## 오류 요약(errors)
{json.dumps(errors, ensure_ascii=False, indent=2)}
"""


def generate_final_report(client, date_str, recommendation, restaurants, errors):
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
다음 순서의 섹션을 포함하세요: 추천 지역, 추천 이유, 날씨 요약, 행사/축제,
맛집 추천, 1일 일정 제안, 오류 요약(errors).
"""
    try:
        return ask_gemini(client, prompt)
    except Exception as error:
        errors.append({
            "step": "final_report",
            "type": "LLM_ERROR",
            "message": str(error),
        })
        print("  - [오류] 최종 LLM 생성 실패 -> 기본 Markdown으로 저장합니다.")
        return fallback_report(date_str, recommendation, restaurants, errors)


def main():
    parser = argparse.ArgumentParser(description="CLI 기반 AI 국내 여행 플래너")
    parser.add_argument("--date", "-date", required=True, help="여행 기준일 (YYYY-MM-DD 형식)")
    args = parser.parse_args()

    if not validate_date(args.date):
        parser.error(f"날짜 형식이 올바르지 않습니다: {args.date}")

    require_api_keys()
    errors = []
    client = create_client()
    recommendation = get_llm_recommendation(client, args.date, errors)
    city = recommendation.get("recommended_city", "서울").strip() or "서울"
    restaurants = search_restaurants(city, errors)
    report = generate_final_report(client, args.date, recommendation, restaurants, errors)

    results_dir = Path("results")
    results_dir.mkdir(exist_ok=True)
    (results_dir / f"{args.date}_travel_data.json").write_text(
        json.dumps({
            "date": args.date,
            "recommendation": recommendation,
            "restaurants": restaurants,
            "errors": errors,
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (results_dir / f"{args.date}_travel_plan.md").write_text(report, encoding="utf-8")
    print("\n완료!")
    print(f"- 원본 JSON: results/{args.date}_travel_data.json")
    print(f"- 최종 리포트: results/{args.date}_travel_plan.md")


if __name__ == "__main__":
    main()
