"""Swagger에 표시할 합성 요청·응답. 식별자 형식은 Spring 합의 전 예시다."""

from pydantic import JsonValue

REQUEST_EXAMPLE: dict[str, JsonValue] = {
    "request_id": "9b3a7c64-1d52-4e8f-a906-2c714f85d390",
    "as_of": "2026-10-13T18:00:00+09:00",
    "window_minutes": 10,
    "spots": [
        {
            "spot_id": 101,
            "post_bundles": [
                {
                    "post_id": 20481,
                    "context_only": False,
                    "title": "지금 잠실역 행사 인파 많아요",
                    "content": "지금 잠실역에 있는데 행사 때문에 사람이 너무 많아서 못 움직여요.",
                    "author_key": "member_4821",
                    "created_at": "2026-10-13T17:53:00+09:00",
                    "comments": [
                        {
                            "comment_id": 50931,
                            "content": "저도 지금 여기 있는데 사람이 너무 많아서 거의 못 움직여요.",
                            "author_key": "member_7310",
                            "created_at": "2026-10-13T17:56:00+09:00",
                        },
                        {
                            "comment_id": 50932,
                            "content": "근처 저녁 먹을 곳 추천해 주세요.",
                            "author_key": "member_9652",
                            "created_at": "2026-10-13T17:57:00+09:00",
                        },
                    ],
                },
                {
                    "post_id": 20486,
                    "context_only": False,
                    "title": "잠실역 아직도 이동하기 힘들어요",
                    "content": "아직 잠실역에 있어요. 행사 인파 때문에 지금도 이동하기 힘들어요.",
                    "author_key": "member_4821",
                    "created_at": "2026-10-13T17:59:00+09:00",
                    "comments": [],
                },
            ],
        },
        {"spot_id": 102, "post_bundles": []},
    ],
}

# 같은 작성자의 반복 글은 한 명으로 집계하고, 원인 없는 댓글에는 부모 원인을 복사하지 않는다.
RESPONSE_EXAMPLE: dict[str, JsonValue] = {
    "request_id": "9b3a7c64-1d52-4e8f-a906-2c714f85d390",
    "spots": [
        {
            "spot_id": 101,
            "analysis_status": "SUCCESS",
            "result": {
                "congestion_level": "HIGH",
                "report_count": 2,
                "causes": [{"cause_code": "EVENT", "report_count": 1}],
                "summary": "제보 기준 혼잡도는 매우 혼잡이며, 혼잡 제보자는 2명입니다. "
                "제보된 원인은 행사·공연·경기(1명)입니다.",
            },
        },
        {
            "spot_id": 102,
            "analysis_status": "SUCCESS",
            "result": {
                "congestion_level": "UNKNOWN",
                "report_count": 0,
                "causes": [],
                "summary": "혼잡 단계를 판단할 유효 제보가 없습니다.",
            },
        },
    ],
}
