# 합성 품질 데이터셋 v1 검토 목록

경계 표현은 제외했다. 표의 값은 모델 실행 결과가 아닌 결정 기록에 따른 사전 정답이다.
2026-10-14 v1.1에서 U11·U12 변경에 따라 049·051·052의 최종 정답만 바꿨다.

| 사례 ID | 유형 | 첫 본문 | 정답 단계 / 제보자 수 | 정답 원인 | 근거 |
| --- | --- | --- | --- | --- | --- |
| quality-001-low-1 | direct_stage | 지금 시청역 승강장에 서 있는데 한산해서 주변에 빈 공간이 넉넉합니다. | LOW / 1 | UNKNOWN:0 | 5.1, 5.2, 6.2, 6.3 |
| quality-002-low-2 | direct_stage | 현재 합정역에서 기다리는 중입니다. 대기줄이 없어서 바로 개찰구를 통과했어요. | LOW / 1 | UNKNOWN:0 | 5.1, 5.2, 6.2, 6.3 |
| quality-003-low-3 | direct_stage | 제가 지금 성수역에 있는데 사람이 적고 여유롭게 걸을 수 있습니다. | LOW / 1 | UNKNOWN:0 | 5.1, 5.2, 6.2, 6.3 |
| quality-004-low-4 | direct_stage | 현재 왕십리역 버스 정류장입니다. 줄 없이 바로 버스에 탈 수 있어요. | LOW / 1 | UNKNOWN:0 | 5.1, 5.2, 6.2, 6.3 |
| quality-005-low-5 | direct_stage | 지금 서울숲 입구에 와 있습니다. 한산해서 일행과 여유 있게 이동하고 있어요. | LOW / 1 | UNKNOWN:0 | 5.1, 5.2, 6.2, 6.3 |
| quality-006-medium-1 | direct_stage | 현재 종각역 출구에 있습니다. 앞에서 기다리는 사람들의 줄이 길어요. | MEDIUM / 1 | UNKNOWN:0 | 5.1, 5.2, 6.2, 6.3 |
| quality-007-medium-2 | direct_stage | 지금 신촌역 승강장에 와 보니 사람이 많습니다. | MEDIUM / 1 | UNKNOWN:0 | 5.1, 5.2, 6.2, 6.3 |
| quality-008-medium-3 | direct_stage | 제가 지금 건대입구역에 있는데 사람이 많지만 이동할 만합니다. | MEDIUM / 1 | UNKNOWN:0 | 5.1, 5.2, 6.2, 6.3 |
| quality-009-medium-4 | direct_stage | 현재 을지로입구역에서 기다리고 있습니다. 탑승 대기줄이 길어요. | MEDIUM / 1 | UNKNOWN:0 | 5.1, 5.2, 6.2, 6.3 |
| quality-010-medium-5 | direct_stage | 지금 홍대입구역 개찰구 앞입니다. 사람이 많지만 걸어갈 수는 있어요. | MEDIUM / 1 | UNKNOWN:0 | 5.1, 5.2, 6.2, 6.3 |
| quality-011-high-1 | direct_stage | 지금 강남역 출구 앞에 있는데 사람이 너무 많아 움직일 수가 없습니다. | HIGH / 1 | UNKNOWN:0 | 5.1, 5.2, 6.2, 6.3 |
| quality-012-high-2 | direct_stage | 현재 잠실새내역 승강장입니다. 사람이 엄청 많습니다. | HIGH / 1 | UNKNOWN:0 | 5.1, 5.2, 6.2, 6.3 |
| quality-013-high-3 | direct_stage | 제가 지금 사당역 환승 통로에 있는데 엄청 붐비고 있습니다. | HIGH / 1 | UNKNOWN:0 | 5.1, 5.2, 6.2, 6.3 |
| quality-014-high-4 | direct_stage | 현재 고속터미널역에 서 있습니다. 사람들이 서 있는 줄이 엄청 길어요. | HIGH / 1 | UNKNOWN:0 | 5.1, 5.2, 6.2, 6.3 |
| quality-015-high-5 | direct_stage | 지금 신도림역에서 열차를 기다립니다. 사람이 너무 많지만 이동은 가능합니다. | HIGH / 1 | UNKNOWN:0 | 5.1, 5.2, 6.2, 6.3 |
| quality-016-cause-rush_hour | direct_cause | 현재 여의도역에 있습니다. 퇴근하는 사람들이 몰려서 대기줄이 길어요. | MEDIUM / 1 | RUSH_HOUR:1 | 5.1, 5.2, 5.4, 6.1, 6.3 |
| quality-017-cause-event | direct_cause | 지금 올림픽공원역에 있습니다. 공연 관람객들이 몰려서 탑승 대기줄이 길어요. | MEDIUM / 1 | EVENT:1 | 5.1, 5.2, 5.4, 6.1, 6.3 |
| quality-018-cause-accident | direct_cause | 현재 충무로역에 있습니다. 승객 사고 때문에 출구 앞에 대기줄이 길어요. | MEDIUM / 1 | ACCIDENT:1 | 5.1, 5.2, 5.4, 6.1, 6.3 |
| quality-019-cause-breakdown | direct_cause | 지금 가산디지털단지역입니다. 개찰기 고장 때문에 입장 대기줄이 길어요. | MEDIUM / 1 | BREAKDOWN:1 | 5.1, 5.2, 5.4, 6.1, 6.3 |
| quality-020-cause-delay | direct_cause | 현재 동대문역 승강장에 있습니다. 열차 운행 지연 때문에 탑승 대기줄이 길어요. | MEDIUM / 1 | DELAY:1 | 5.1, 5.2, 5.4, 6.1, 6.3 |
| quality-021-cause-service_disruption | direct_cause | 지금 신길역에 있습니다. 열차 운행이 중단돼 대체 버스를 타는 줄이 길어요. | MEDIUM / 1 | SERVICE_DISRUPTION:1 | 5.1, 5.2, 5.4, 6.1, 6.3 |
| quality-022-cause-weather | direct_cause | 현재 수유역 버스 정류장입니다. 폭우 때문에 버스를 기다리는 사람들이 몰려서 탑승 대기줄이 길어요. | MEDIUM / 1 | WEATHER:1 | 5.1, 5.2, 5.4, 6.1, 6.3 |
| quality-023-cause-road_traffic | direct_cause | 현재 양재역 앞입니다. 도로 정체 때문에 횡단보도 앞의 대기줄이 길어요. | MEDIUM / 1 | ROAD_TRAFFIC:1 | 5.1, 5.2, 5.4, 6.1, 6.3 |
| quality-024-cause-construction | direct_cause | 현재 혜화역 출구입니다. 출구 공사 때문에 통과를 기다리는 줄이 길어요. | MEDIUM / 1 | CONSTRUCTION:1 | 5.1, 5.2, 5.4, 6.1, 6.3 |
| quality-025-cause-demonstration | direct_cause | 지금 경복궁역 앞입니다. 집회 참가 인파 때문에 역으로 들어가는 줄이 길어요. | MEDIUM / 1 | DEMONSTRATION:1 | 5.1, 5.2, 5.4, 6.1, 6.3 |
| quality-026-unrelated-1 | unrelated | 성수역 근처에서 지갑을 잃어버렸습니다. 파란색 카드지갑을 보신 분은 댓글 부탁드립니다. | UNKNOWN / 0 | 없음 | 5.1, 5.4, U19, U20 |
| quality-027-unrelated-2 | unrelated | 내일 신촌역 근처 카페에서 같이 영어 공부할 분을 찾습니다. | UNKNOWN / 0 | 없음 | 5.1, 5.4, U19, U20 |
| quality-028-unrelated-3 | unrelated | 이번 주에 읽을 만한 추리소설을 추천해 주세요. | UNKNOWN / 0 | 없음 | 5.1, 5.4, U19, U20 |
| quality-029-unrelated-4 | unrelated | 합정역 근처에서 자전거 수리를 잘하는 가게가 있나요? | UNKNOWN / 0 | 없음 | 5.1, 5.4, U19, U20 |
| quality-030-unrelated-5 | unrelated | 여의도역에서 출발하는 산책 코스를 지도에 표시해 봤습니다. | UNKNOWN / 0 | 없음 | 5.1, 5.4, U19, U20 |
| quality-031-unrelated-6 | unrelated | 오늘 저녁 반찬으로 감자조림을 만들었는데 간 맞추기가 어렵네요. | UNKNOWN / 0 | 없음 | 5.1, 5.4, U19, U20 |
| quality-032-unrelated-7 | unrelated | 지금 창밖을 보니 눈이 내립니다. 저는 집에서 쉬고 있어요. | UNKNOWN / 0 | 없음 | 5.1, 5.4, U19, U20 |
| quality-033-unrelated-8 | unrelated | 올림픽공원 공연 예매 날짜가 다음 주 화요일이라고 공지됐습니다. | UNKNOWN / 0 | 없음 | 5.1, 5.4, U19, U20 |
| quality-034-invalid-1 | related_but_invalid | 지금 강남역 승강장에 사람이 엄청 많은가요? 저는 아직 집에 있습니다. | UNKNOWN / 0 | 없음 | 5.1, U19, U18 |
| quality-035-invalid-2 | related_but_invalid | 현재 종로3가역은 줄이 긴가요? 가기 전에 상황을 물어봅니다. | UNKNOWN / 0 | 없음 | 5.1, U19, U18 |
| quality-036-invalid-3 | related_but_invalid | 친구가 지금 사당역에 사람이 너무 많다고 알려줬습니다. 저는 회사 안에 있어요. | UNKNOWN / 0 | 없음 | 5.1, U19, U18 |
| quality-037-invalid-4 | related_but_invalid | 동료에게 지금 신촌역이 한산하다는 말을 들었습니다. 제가 확인한 건 아닙니다. | UNKNOWN / 0 | 없음 | 5.1, U19, U18 |
| quality-038-invalid-5 | related_but_invalid | 내일 공연이 끝나면 올림픽공원역에 사람이 엄청 많아질 것 같습니다. | UNKNOWN / 0 | 없음 | 5.1, U19, U18 |
| quality-039-invalid-6 | related_but_invalid | 저녁에는 퇴근하는 인파 때문에 여의도역의 줄이 길어질 것 같아요. | UNKNOWN / 0 | 없음 | 5.1, U19, U18 |
| quality-040-invalid-7 | related_but_invalid | 어제 제가 본 왕십리역 승강장은 사람이 너무 많았습니다. 오늘 상황은 모릅니다. | UNKNOWN / 0 | 없음 | 5.1, U19, U18 |
| quality-041-invalid-8 | related_but_invalid | 10분 전에 제가 본 합정역 입구는 한산했습니다. 지금은 집에 있어서 알 수 없어요. | UNKNOWN / 0 | 없음 | 5.1, U19, U18 |
| quality-042-unrelated-parent-related-comment | parent_comment | 건대입구역 인근에서 노트북 충전기를 빌릴 수 있을까요? | HIGH / 1 | UNKNOWN:0 | 5.3, 6.2 |
| quality-043-comment-own-stage | parent_comment | 지금 선릉역에 와 있는데 사람이 너무 많아요. | UNKNOWN / 2 | UNKNOWN:0 | 5.2, 5.3, 6.2 |
| quality-044-comment-no-parent-cause-copy | parent_comment | 현재 뚝섬역입니다. 행사 인파 때문에 사람이 너무 많아요. | HIGH / 2 | EVENT:1 | 5.3, 5.4, 6.3 |
| quality-045-comment-explicit-cause | parent_comment | 지금 광화문역에 있습니다. 집회 인파 때문에 대기줄이 길어요. | MEDIUM / 2 | DEMONSTRATION:2 | 5.3, 5.4, 6.3 |
| quality-046-comment-independent-cause | parent_comment | 현재 청량리역입니다. 열차 운행 지연 때문에 대기줄이 길어요. | MEDIUM / 2 | CONSTRUCTION:1, DELAY:1 | 5.3, 5.4, 6.3 |
| quality-047-same-author-post-comment | parent_comment | 현재 불광역에 서 있는데 사람이 너무 많아요. | LOW / 1 | UNKNOWN:0 | 5.3, 6.2 |
| quality-048-latest-author-stage | aggregation | 지금 연신내역에 있는데 사람이 엄청 많아요. | LOW / 1 | UNKNOWN:0 | 6.2 |
| quality-049-latest-stage-independent-cause | aggregation | 지금 대림역입니다. 설비 고장 때문에 대기줄이 길어요. | LOW / 1 | UNKNOWN:0 (v1.1, 이전 BREAKDOWN:1) | 6.2, 6.3, U12 |
| quality-050-two-author-tie | aggregation | 현재 노량진역에 있는데 사람이 엄청 많습니다. | UNKNOWN / 2 | UNKNOWN:0 | 6.2, 6.3 |
| quality-051-same-time-stage-conflict | aggregation | 지금 이수역에 있는데 사람이 너무 많습니다. | UNKNOWN / 2 (v1.1, 이전 MEDIUM / 1) | UNKNOWN:0 | 6.2, U11 |
| quality-052-same-time-cause-conflict | aggregation | 현재 구로역입니다. 행사 인파 때문에 대기줄이 길어요. | MEDIUM / 1 | BREAKDOWN:1 (v1.1, 이전 UNKNOWN:0) | 6.2, 6.3, U11 |
| quality-053-same-time-identical-votes | aggregation | 지금 신설동역에 와 있는데 사람이 너무 많아요. | HIGH / 1 | UNKNOWN:0 | 6.2, U11 |
| quality-054-unique-author-majority | aggregation | 지금 회기역에 있습니다. 한산해서 여유 있게 걷고 있어요. | LOW / 3 | UNKNOWN:0 | 6.2 |
| quality-055-cause-only-no-stage | aggregation | 지금 동묘앞역입니다. 신호기 고장 때문에 사람들이 기다리고 있습니다. 혼잡 정도는 모르겠습니다. | UNKNOWN / 0 | BREAKDOWN:1 | 5.4, 6.2, 6.3, U15 |
| quality-056-ten-minute-outside | time_window | 현재 문래역입니다. 사람이 너무 많습니다. | UNKNOWN / 0 | 없음 | U09, U10, U34 |
| quality-057-sixty-minute-inside | time_window | 지금 영등포구청역입니다. 탑승 대기줄이 길어요. | MEDIUM / 1 | UNKNOWN:0 | U10, U34 |
| quality-058-old-parent-current-comment | time_window | 용산역 근처에서 분실한 우산을 찾습니다. | HIGH / 1 | UNKNOWN:0 | 5.3, U10, U34 |
| quality-059-open-lower-closed-upper | time_window | 현재 상봉역에 있습니다. 사람이 엄청 많아요. | LOW / 1 | UNKNOWN:0 | U10, U34 |
| quality-060-empty-spot | empty | (빈 스팟) | UNKNOWN / 0 | 없음 | U20 |
