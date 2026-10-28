"""Swagger에 표시할 단일 스팟 합성 요청·성공 결과."""

from pydantic import JsonValue

REQUEST_EXAMPLE: dict[str, JsonValue] = {
    "event_id": "9b3a7c64-1d52-4e8f-a906-2c714f85d390",
    "schema_version": 1,
    "window_end": "2026-10-13T18:00:00+09:00",
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
}

# 같은 작성자는 한 명으로 집계하며 부모 원인을 댓글에 자동 복사하지 않는다.
RESPONSE_EXAMPLE: dict[str, JsonValue] = {
    "event_id": "7d2a9e10-5c4b-4a8e-b1f3-0e6d2c9a7b18",
    "schema_version": 1,
    "window_end": "2026-10-13T18:00:00+09:00",
    "spot_id": 101,
    "analysis_status": "SUCCESS",
    "result": {
        "congestion_level": "HIGH",
        "report_count": 2,
        "causes": [{"cause_code": "EVENT", "report_count": 1}],
        "summary": "제보 기준 혼잡도는 매우 혼잡이며, 혼잡 제보자는 2명입니다. 제보된 원인은 행사·공연·경기(1명)입니다.",
    },
    "analyzed_at": "2026-10-13T09:00:41Z",
}
