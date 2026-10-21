"""확정 기준만 사용한 신규 합성 정답셋과 별도 처리량 입력을 만든다."""

import hashlib
import json
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
AS_OF = datetime.fromisoformat("2026-10-13T18:00:00+09:00")
CASES = []


def signal(
    text, level=None, causes=(), *, author="a", minutes=2, related=True, comments=()
):
    """본문과 정답을 함께 선언하되 모델이나 제품 집계로 정답을 만들지 않는다."""
    return {
        "text": text,
        "level": level,
        "causes": list(causes),
        "author": author,
        "minutes": minutes,
        "related": related,
        "comments": list(comments),
    }


def result(level, count, causes=()):
    return {
        "congestion_level": level,
        "report_count": count,
        "causes": [
            {"cause_code": code, "report_count": number} for code, number in causes
        ],
    }


# 사례 이름 -> 원글 순서대로의 제목. 본문보다 단계를 세게 쓰거나 본문에 없는 원인을 넣지 않는다.
TITLES = {
    "low-1": ["시청역 승강장 한산해요"],
    "low-2": ["합정역 개찰구 줄 없어요"],
    "low-3": ["성수역 지금 여유로워요"],
    "low-4": ["왕십리역 정류장 바로 탔어요"],
    "low-5": ["서울숲 입구 한산합니다"],
    "medium-1": ["종각역 출구 줄 길어요"],
    "medium-2": ["신촌역 승강장 사람 많네요"],
    "medium-3": ["건대입구역 사람 많은데 다닐 만해요"],
    "medium-4": ["을지로입구역 탑승 줄 깁니다"],
    "medium-5": ["홍대입구역 개찰구 앞 사람 많아요"],
    "high-1": ["강남역 출구 앞 움직일 수가 없어요"],
    "high-2": ["잠실새내역 승강장 사람 엄청 많아요"],
    "high-3": ["사당역 환승 통로 엄청 붐벼요"],
    "high-4": ["고속터미널역 줄 엄청 길어요"],
    "high-5": ["신도림역 사람 너무 많아요"],
    "cause-rush_hour": ["여의도역 퇴근 인파로 줄 길어요"],
    "cause-event": ["올림픽공원역 공연 관람객 몰렸어요"],
    "cause-accident": ["충무로역 승객 사고로 출구 줄 길어요"],
    "cause-breakdown": ["가디역 개찰기 고장으로 줄 길어요"],
    "cause-delay": ["동대문역 열차 지연으로 탑승 줄 길어요"],
    "cause-service_disruption": ["신길역 운행 중단, 대체버스 줄 길어요"],
    "cause-weather": ["수유역 정류장 폭우에 줄 길어요"],
    "cause-road_traffic": ["양재역 도로 정체로 횡단보도 줄 길어요"],
    "cause-construction": ["혜화역 출구 공사로 줄 길어요"],
    "cause-demonstration": ["경복궁역 집회 인파로 줄 길어요"],
    "unrelated-1": ["성수역 근처 파란 카드지갑 찾아요"],
    "unrelated-2": ["신촌 영어 스터디 구해요"],
    "unrelated-3": ["추리소설 추천 부탁드려요"],
    "unrelated-4": ["합정 근처 자전거 수리점 있나요?"],
    "unrelated-5": ["여의도역 출발 산책 코스 공유해요"],
    "unrelated-6": ["감자조림 간 맞추기 어렵네요"],
    "unrelated-7": ["창밖에 눈 오네요"],
    "unrelated-8": ["올림픽공원 공연 예매일 공지"],
    "invalid-1": ["강남역 지금 사람 많나요?"],
    "invalid-2": ["종로3가역 지금 줄 긴가요?"],
    "invalid-3": ["친구 말로는 사당역 붐빈대요"],
    "invalid-4": ["신촌역 한산하다고 들었어요"],
    "invalid-5": ["내일 공연 끝나면 올림픽공원역 붐빌 듯"],
    "invalid-6": ["저녁엔 여의도역 줄 길어질 것 같아요"],
    "invalid-7": ["어제 왕십리역 사람 너무 많았어요"],
    "invalid-8": ["10분 전 합정역 입구는 한산했어요"],
    "unrelated-parent-related-comment": ["건대입구 근처 노트북 충전기 빌려요"],
    "comment-own-stage": ["선릉역 사람 너무 많아요"],
    "comment-no-parent-cause-copy": ["뚝섬역 행사 인파로 사람 너무 많아요"],
    "comment-explicit-cause": ["광화문역 집회 때문에 줄 길어요"],
    "comment-independent-cause": ["청량리역 열차 지연으로 줄 길어요"],
    "same-author-post-comment": ["불광역 사람 너무 많아요"],
    "latest-author-stage": ["연신내역 사람 엄청 많아요", "연신내역 이제 한산해요"],
    "latest-stage-independent-cause": ["대림역 설비 고장으로 줄 길어요", "대림역 지금은 줄 없어요"],
    "two-author-tie": ["노량진역 사람 엄청 많아요", "노량진역 많긴 한데 다닐 만해요"],
    "same-time-stage-conflict": ["이수역 사람 너무 많아요", "이수역 한산해요", "이수역 대기줄 길어요"],
    "same-time-cause-conflict": ["구로역 행사 인파로 줄 길어요", "구로역 개찰기 고장으로 줄 길어요"],
    "same-time-identical-votes": ["신설동역 사람 너무 많아요", "신설동역 사람 엄청 많네요"],
    "unique-author-majority": ["회기역 한산해요", "회기역 줄 없이 바로 들어왔어요", "회기역 사람 너무 많아요"],
    "cause-only-no-stage": ["동묘앞역 신호기 고장났어요"],
    "ten-minute-outside": ["문래역 사람 너무 많아요"],
    "sixty-minute-inside": ["영등포구청역 탑승 줄 길어요"],
    "old-parent-current-comment": ["용산역 근처에서 우산 잃어버렸어요"],
    "open-lower-closed-upper": ["상봉역 사람 엄청 많아요", "상봉역 지금 한산해요"],
    "empty-spot": [],
}
LOAD_TITLE = "행사 관람객 몰려서 입구 줄 길어요"


def add(name, category, posts, expected, rules, *, window=10):
    """직접 정한 최종 정답과 근거를 API 입력·개별 정답에 대응시킨다."""
    index = len(CASES) + 1
    annotations = []
    bundles = []
    next_id = index * 100
    titles = TITLES[name]
    assert len(titles) == len(posts), name
    for post_index, post in enumerate(posts):
        next_id += 1
        post_id = next_id
        bundle = {
            "post_id": post_id,
            "title": titles[post_index],
            "content": post["text"],
            "author_key": post["author"],
            "created_at": (AS_OF - timedelta(minutes=post["minutes"])).isoformat(),
            "comments": [],
        }
        for kind, value in [
            ("post", post),
            *(("comment", c) for c in post["comments"]),
        ]:
            if kind == "comment":
                next_id += 1
                bundle["comments"].append(
                    {
                        "comment_id": next_id,
                        "content": value["text"],
                        "author_key": value["author"],
                        "created_at": (
                            AS_OF - timedelta(minutes=value["minutes"])
                        ).isoformat(),
                    }
                )
            within = 0 <= value["minutes"] < window
            annotations.append(
                {
                    "signal_type": kind,
                    "signal_id": post_id if kind == "post" else next_id,
                    "in_window": within,
                    "relevance": ("RELATED" if value["related"] else "UNRELATED")
                    if within
                    else None,
                    "congestion_level": value["level"] if within else None,
                    "causes": value["causes"] if within else [],
                    "rules": rules,
                }
            )
        bundles.append(bundle)
    CASES.append(
        {
            "id": f"quality-{index:03d}-{name}",
            "category": category,
            "rules": rules,
            "request": {
                "request_id": f"synthetic-quality-v1-{index:03d}",
                "as_of": AS_OF.isoformat(),
                "window_minutes": window,
                "spots": [{"spot_id": index, "post_bundles": bundles}],
            },
            "annotations": annotations,
            "expected_result": expected,
        }
    )


def build_quality():
    # 15개 직접 단계 제보는 문장을 각각 작성하고 집계 정답도 명시한다.
    direct = {
        "LOW": [
            "지금 시청역 승강장에 서 있는데 한산해서 주변에 빈 공간이 넉넉합니다.",
            "현재 합정역에서 기다리는 중입니다. 대기줄이 없어서 바로 개찰구를 통과했어요.",
            "제가 지금 성수역에 있는데 사람이 적고 여유롭게 걸을 수 있습니다.",
            "현재 왕십리역 버스 정류장입니다. 줄 없이 바로 버스에 탈 수 있어요.",
            "지금 서울숲 입구에 와 있습니다. 한산해서 일행과 여유 있게 이동하고 있어요.",
        ],
        "MEDIUM": [
            "현재 종각역 출구에 있습니다. 앞에서 기다리는 사람들의 줄이 길어요.",
            "지금 신촌역 승강장에 와 보니 사람이 많습니다.",
            "제가 지금 건대입구역에 있는데 사람이 많지만 이동할 만합니다.",
            "현재 을지로입구역에서 기다리고 있습니다. 탑승 대기줄이 길어요.",
            "지금 홍대입구역 개찰구 앞입니다. 사람이 많지만 걸어갈 수는 있어요.",
        ],
        "HIGH": [
            "지금 강남역 출구 앞에 있는데 사람이 너무 많아 움직일 수가 없습니다.",
            "현재 잠실새내역 승강장입니다. 사람이 엄청 많습니다.",
            "제가 지금 사당역 환승 통로에 있는데 엄청 붐비고 있습니다.",
            "현재 고속터미널역에 서 있습니다. 사람들이 서 있는 줄이 엄청 길어요.",
            "지금 신도림역에서 열차를 기다립니다. 사람이 너무 많지만 이동은 가능합니다.",
        ],
    }
    for level, texts in direct.items():
        for index, text in enumerate(texts, start=1):
            add(
                f"{level.lower()}-{index}",
                "direct_stage",
                [signal(text, level)],
                result(level, 1, [("UNKNOWN", 0)]),
                ["5.1", "5.2", "6.2", "6.3"],
            )

    causes = [
        (
            "RUSH_HOUR",
            "현재 여의도역에 있습니다. 퇴근하는 사람들이 몰려서 대기줄이 길어요.",
        ),
        (
            "EVENT",
            "지금 올림픽공원역에 있습니다. 공연 관람객들이 몰려서 탑승 대기줄이 길어요.",
        ),
        (
            "ACCIDENT",
            "현재 충무로역에 있습니다. 승객 사고 때문에 출구 앞에 대기줄이 길어요.",
        ),
        (
            "BREAKDOWN",
            "지금 가산디지털단지역입니다. 개찰기 고장 때문에 입장 대기줄이 길어요.",
        ),
        (
            "DELAY",
            "현재 동대문역 승강장에 있습니다. 열차 운행 지연 때문에 탑승 대기줄이 길어요.",
        ),
        (
            "SERVICE_DISRUPTION",
            "지금 신길역에 있습니다. 열차 운행이 중단돼 대체 버스를 타는 줄이 길어요.",
        ),
        (
            "WEATHER",
            "현재 수유역 버스 정류장입니다. 폭우 때문에 버스를 기다리는 사람들이 몰려서 탑승 대기줄이 길어요.",
        ),
        (
            "ROAD_TRAFFIC",
            "현재 양재역 앞입니다. 도로 정체 때문에 횡단보도 앞의 대기줄이 길어요.",
        ),
        (
            "CONSTRUCTION",
            "현재 혜화역 출구입니다. 출구 공사 때문에 통과를 기다리는 줄이 길어요.",
        ),
        (
            "DEMONSTRATION",
            "지금 경복궁역 앞입니다. 집회 참가 인파 때문에 역으로 들어가는 줄이 길어요.",
        ),
    ]
    for code, text in causes:
        add(
            f"cause-{code.lower()}",
            "direct_cause",
            [signal(text, "MEDIUM", [code])],
            result("MEDIUM", 1, [(code, 1)]),
            ["5.1", "5.2", "5.4", "6.1", "6.3"],
        )

    unrelated = [
        "성수역 근처에서 지갑을 잃어버렸습니다. 파란색 카드지갑을 보신 분은 댓글 부탁드립니다.",
        "내일 신촌역 근처 카페에서 같이 영어 공부할 분을 찾습니다.",
        "이번 주에 읽을 만한 추리소설을 추천해 주세요.",
        "합정역 근처에서 자전거 수리를 잘하는 가게가 있나요?",
        "여의도역에서 출발하는 산책 코스를 지도에 표시해 봤습니다.",
        "오늘 저녁 반찬으로 감자조림을 만들었는데 간 맞추기가 어렵네요.",
        "지금 창밖을 보니 눈이 내립니다. 저는 집에서 쉬고 있어요.",
        "올림픽공원 공연 예매 날짜가 다음 주 화요일이라고 공지됐습니다.",
    ]
    for index, text in enumerate(unrelated, start=1):
        add(
            f"unrelated-{index}",
            "unrelated",
            [signal(text, related=False)],
            result("UNKNOWN", 0),
            ["5.1", "5.4", "U19", "U20"],
        )

    invalid = [
        "지금 강남역 승강장에 사람이 엄청 많은가요? 저는 아직 집에 있습니다.",
        "현재 종로3가역은 줄이 긴가요? 가기 전에 상황을 물어봅니다.",
        "친구가 지금 사당역에 사람이 너무 많다고 알려줬습니다. 저는 회사 안에 있어요.",
        "동료에게 지금 신촌역이 한산하다는 말을 들었습니다. 제가 확인한 건 아닙니다.",
        "내일 공연이 끝나면 올림픽공원역에 사람이 엄청 많아질 것 같습니다.",
        "저녁에는 퇴근하는 인파 때문에 여의도역의 줄이 길어질 것 같아요.",
        "어제 제가 본 왕십리역 승강장은 사람이 너무 많았습니다. 오늘 상황은 모릅니다.",
        "10분 전에 제가 본 합정역 입구는 한산했습니다. 지금은 집에 있어서 알 수 없어요.",
    ]
    for index, text in enumerate(invalid, start=1):
        add(
            f"invalid-{index}",
            "related_but_invalid",
            [signal(text)],
            result("UNKNOWN", 0),
            ["5.1", "U19", "U18"],
        )

    add(
        "unrelated-parent-related-comment",
        "parent_comment",
        [
            signal(
                "건대입구역 인근에서 노트북 충전기를 빌릴 수 있을까요?",
                related=False,
                comments=[
                    signal(
                        "저는 지금 이 역 승강장에 있는데 사람이 엄청 많습니다.",
                        "HIGH",
                        author="b",
                    ),
                ],
            )
        ],
        result("HIGH", 1, [("UNKNOWN", 0)]),
        ["5.3", "6.2"],
    )
    add(
        "comment-own-stage",
        "parent_comment",
        [
            signal(
                "지금 선릉역에 와 있는데 사람이 너무 많아요.",
                "HIGH",
                comments=[
                    signal(
                        "저도 지금 선릉역에 있는데 사람이 많지만 이동할 만합니다.",
                        "MEDIUM",
                        author="b",
                    ),
                ],
            )
        ],
        result("UNKNOWN", 2, [("UNKNOWN", 0)]),
        ["5.2", "5.3", "6.2"],
    )
    add(
        "comment-no-parent-cause-copy",
        "parent_comment",
        [
            signal(
                "현재 뚝섬역입니다. 행사 인파 때문에 사람이 너무 많아요.",
                "HIGH",
                ["EVENT"],
                comments=[
                    signal(
                        "저도 지금 뚝섬역에 있는데 사람이 엄청 많아요.",
                        "HIGH",
                        author="b",
                    ),
                ],
            )
        ],
        result("HIGH", 2, [("EVENT", 1)]),
        ["5.3", "5.4", "6.3"],
    )
    add(
        "comment-explicit-cause",
        "parent_comment",
        [
            signal(
                "지금 광화문역에 있습니다. 집회 인파 때문에 대기줄이 길어요.",
                "MEDIUM",
                ["DEMONSTRATION"],
                comments=[
                    signal(
                        "저도 현재 광화문역입니다. 집회 참가자들이 몰려서 입장 줄이 길어요.",
                        "MEDIUM",
                        ["DEMONSTRATION"],
                        author="b",
                    ),
                ],
            )
        ],
        result("MEDIUM", 2, [("DEMONSTRATION", 2)]),
        ["5.3", "5.4", "6.3"],
    )
    add(
        "comment-independent-cause",
        "parent_comment",
        [
            signal(
                "현재 청량리역입니다. 열차 운행 지연 때문에 대기줄이 길어요.",
                "MEDIUM",
                ["DELAY"],
                comments=[
                    signal(
                        "저는 지금 이 역 출구에 있습니다. 출구 공사 때문에 통과하는 줄이 길어요.",
                        "MEDIUM",
                        ["CONSTRUCTION"],
                        author="b",
                    ),
                ],
            )
        ],
        result("MEDIUM", 2, [("CONSTRUCTION", 1), ("DELAY", 1)]),
        ["5.3", "5.4", "6.3"],
    )
    add(
        "same-author-post-comment",
        "parent_comment",
        [
            signal(
                "현재 불광역에 서 있는데 사람이 너무 많아요.",
                "HIGH",
                minutes=4,
                comments=[
                    signal(
                        "제가 계속 불광역에 있는데 지금은 한산해서 여유가 있습니다.",
                        "LOW",
                        author="a",
                        minutes=1,
                    ),
                ],
            )
        ],
        result("LOW", 1, [("UNKNOWN", 0)]),
        ["5.3", "6.2"],
    )

    add(
        "latest-author-stage",
        "aggregation",
        [
            signal("지금 연신내역에 있는데 사람이 엄청 많아요.", "HIGH", minutes=4),
            signal(
                "계속 연신내역에 머물고 있습니다. 현재는 한산합니다.", "LOW", minutes=1
            ),
        ],
        result("LOW", 1, [("UNKNOWN", 0)]),
        ["6.2"],
    )
    add(
        "latest-stage-independent-cause",
        "aggregation",
        [
            signal(
                "지금 대림역입니다. 설비 고장 때문에 대기줄이 길어요.",
                "MEDIUM",
                ["BREAKDOWN"],
                minutes=4,
            ),
            signal(
                "제가 아직 대림역에 있는데 현재는 한산해서 줄이 없습니다.",
                "LOW",
                minutes=1,
            ),
        ],
        # 2026-10-14 U12 변경: 최신 유효 제보(한산)에 원인이 없어 앞선 BREAKDOWN을 버린다.
        result("LOW", 1, [("UNKNOWN", 0)]),
        ["6.2", "6.3", "U12"],
    )
    add(
        "two-author-tie",
        "aggregation",
        [
            signal("현재 노량진역에 있는데 사람이 엄청 많습니다.", "HIGH", author="a"),
            signal(
                "저도 현재 노량진역입니다. 사람이 많지만 이동할 만해요.",
                "MEDIUM",
                author="b",
            ),
        ],
        result("UNKNOWN", 2, [("UNKNOWN", 0)]),
        ["6.2", "6.3"],
    )
    add(
        "same-time-stage-conflict",
        "aggregation",
        [
            signal("지금 이수역에 있는데 사람이 너무 많습니다.", "HIGH", author="a"),
            signal(
                "현재 이수역에 있습니다. 한산해서 빈 공간이 넉넉해요.",
                "LOW",
                author="a",
            ),
            signal("지금 이수역에 와 있는데 대기줄이 길어요.", "MEDIUM", author="b"),
        ],
        # 2026-10-14 U11 변경: a는 ID가 큰 LOW 글을 쓰고 b의 MEDIUM과 동률이다.
        result("UNKNOWN", 2, [("UNKNOWN", 0)]),
        ["6.2", "U11"],
    )
    add(
        "same-time-cause-conflict",
        "aggregation",
        [
            signal(
                "현재 구로역입니다. 행사 인파 때문에 대기줄이 길어요.",
                "MEDIUM",
                ["EVENT"],
            ),
            signal(
                "지금 구로역에 있습니다. 개찰기 고장 때문에 대기줄이 길어요.",
                "MEDIUM",
                ["BREAKDOWN"],
            ),
        ],
        # 2026-10-14 U11 변경: ID가 큰 BREAKDOWN 글을 최신으로 쓴다.
        result("MEDIUM", 1, [("BREAKDOWN", 1)]),
        ["6.2", "6.3", "U11"],
    )
    add(
        "same-time-identical-votes",
        "aggregation",
        [
            signal("지금 신설동역에 와 있는데 사람이 너무 많아요.", "HIGH"),
            signal("현재 신설동역에 있습니다. 사람이 엄청 많습니다.", "HIGH"),
        ],
        result("HIGH", 1, [("UNKNOWN", 0)]),
        ["6.2", "U11"],
    )
    add(
        "unique-author-majority",
        "aggregation",
        [
            signal(
                "지금 회기역에 있습니다. 한산해서 여유 있게 걷고 있어요.",
                "LOW",
                author="a",
            ),
            signal(
                "현재 회기역입니다. 대기줄이 없어서 바로 들어왔어요.", "LOW", author="b"
            ),
            signal("지금 회기역에 와 있는데 사람이 너무 많습니다.", "HIGH", author="c"),
        ],
        result("LOW", 3, [("UNKNOWN", 0)]),
        ["6.2"],
    )
    add(
        "cause-only-no-stage",
        "aggregation",
        [
            signal(
                "지금 동묘앞역입니다. 신호기 고장 때문에 사람들이 기다리고 있습니다. 혼잡 정도는 모르겠습니다.",
                causes=["BREAKDOWN"],
            ),
        ],
        result("UNKNOWN", 0, [("BREAKDOWN", 1)]),
        ["5.4", "6.2", "6.3", "U15"],
    )

    add(
        "ten-minute-outside",
        "time_window",
        [
            signal("현재 문래역입니다. 사람이 너무 많습니다.", "HIGH", minutes=11),
        ],
        result("UNKNOWN", 0),
        ["U09", "U10", "U34"],
        window=10,
    )
    add(
        "sixty-minute-inside",
        "time_window",
        [
            signal(
                "지금 영등포구청역입니다. 탑승 대기줄이 길어요.", "MEDIUM", minutes=45
            ),
        ],
        result("MEDIUM", 1, [("UNKNOWN", 0)]),
        ["U10", "U34"],
        window=60,
    )
    add(
        "old-parent-current-comment",
        "time_window",
        [
            signal(
                "용산역 근처에서 분실한 우산을 찾습니다.",
                minutes=70,
                related=False,
                comments=[
                    signal(
                        "저는 지금 용산역 승강장에 있습니다. 사람이 너무 많아요.",
                        "HIGH",
                        author="b",
                        minutes=1,
                    ),
                ],
            )
        ],
        result("HIGH", 1, [("UNKNOWN", 0)]),
        ["5.3", "U10", "U34"],
        window=60,
    )
    add(
        "open-lower-closed-upper",
        "time_window",
        [
            signal(
                "현재 상봉역에 있습니다. 사람이 엄청 많아요.",
                "HIGH",
                author="a",
                minutes=10,
            ),
            signal(
                "지금 상봉역입니다. 한산해서 여유 있게 이동합니다.",
                "LOW",
                author="b",
                minutes=0,
            ),
        ],
        result("LOW", 1, [("UNKNOWN", 0)]),
        ["U10", "U34"],
        window=10,
    )
    add("empty-spot", "empty", [], result("UNKNOWN", 0), ["U20"])


def build_load():
    """품질 표본 수와 구분되는, 구성 비율이 같은 처리량 입력을 만든다."""
    loads = []
    for count in (30, 120, 300, 600, 1000):
        spots = [{"spot_id": index + 1, "post_bundles": []} for index in range(6)]
        for index in range(count // 5):
            spot = spots[index % 6]
            prefix = f"synthetic-load-{count}-{index}"
            post_id = index + 1
            spot["post_bundles"].append(
                {
                    "post_id": post_id,
                    "title": LOAD_TITLE,
                    "content": "지금 역 입구에 있습니다. 행사 관람객이 몰려서 입장 대기줄이 길어요.",
                    "author_key": prefix + "-post",
                    "created_at": (AS_OF - timedelta(minutes=3)).isoformat(),
                    "comments": [
                        {
                            "comment_id": post_id * 10 + offset,
                            "content": text,
                            "author_key": prefix + f"-comment-{offset}",
                            "created_at": (AS_OF - timedelta(minutes=2)).isoformat(),
                        }
                        for offset, text in enumerate(
                            [
                                "저도 지금 역 입구에 있는데 사람이 많지만 이동할 만합니다.",
                                "저는 현재 이 역 승강장에 있습니다. 사람이 엄청 많아요.",
                                "근처에서 휴대폰 충전 케이블을 살 수 있는 가게가 있나요?",
                                "다음 주 독서 모임에서 읽을 소설을 추천해 주세요.",
                            ],
                            start=1,
                        )
                    ],
                }
            )
        loads.append(
            {
                "id": f"load-{count}",
                "signal_count": count,
                "spot_count": 6,
                "related_fraction": 0.6,
                "unique_author_fraction": 1.0,
                "accuracy_sample": False,
                "request": {
                    "request_id": f"synthetic-load-v1-{count}",
                    "as_of": AS_OF.isoformat(),
                    "window_minutes": 10,
                    "spots": spots,
                },
                "expected_results": [
                    {
                        "spot_id": spot["spot_id"],
                        **result(
                            "MEDIUM",
                            len(spot["post_bundles"]) * 3,
                            [("EVENT", len(spot["post_bundles"]))],
                        ),
                    }
                    for spot in spots
                ],
            }
        )
    return loads


def main():
    build_quality()
    assert len(CASES) == 60, len(CASES)
    loads = build_load()
    paths = [HERE / "quality.json", HERE / "load.json", HERE / "manifest.json"]
    if any(path.exists() for path in paths):
        raise SystemExit("기존 데이터셋을 덮어쓰지 않습니다.")
    for path, data in zip(paths[:2], [CASES, loads], strict=True):
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    manifest = {
        "version": "v1",
        "data": "synthetic",
        "model_calls": 0,
        "label_status": "decision_record_based_draft_requires_semantic_review",
        "boundary_expressions": "deferred_by_user",
        "quality_cases": len(CASES),
        "quality_signals": sum(len(case["annotations"]) for case in CASES),
        "category_counts": dict(Counter(case["category"] for case in CASES)),
        "load_signal_counts": [entry["signal_count"] for entry in loads],
        "sha256": {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in paths[:2]
        },
    }
    paths[2].write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
