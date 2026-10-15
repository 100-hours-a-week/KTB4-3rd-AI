# send_time은 날짜와 시각 사이 공백만 보정하고 나머지는 적힌 그대로 둔다.
from app.core.oj_candidates import datetime_value


def test_datetime_value():
    cases = {
        "2026-09-2312:30:45": "2026-09-23 12:30:45",
        "2026.09.0708:15": "2026.09.07 08:15",
        "2026년9월 1일오후 7:42": "2026년9월 1일 오후 7:42",
        "2026-09-23 12:30:45": "2026-09-23 12:30:45",
        "2026년 9월 1일 오후 7:42": "2026년 9월 1일 오후 7:42",
        "2026-09-23": "2026-09-23",
    }
    for raw, expected in cases.items():
        assert datetime_value(raw) == expected, raw
