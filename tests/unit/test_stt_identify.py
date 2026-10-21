"""voice catalog ③ — 대조의 순수 절반.

등록된 목소리와 이 녹음의 화자 군집을 **코사인으로 잰다**. 「녹음 선두에 등록본을 붙여
분리기가 같은 군집으로 묶게 한다」는 방식은 카탈로그가 커지면 군집 예산(상한 8·임계값
0.8)을 먹고 녹음 본체의 군집까지 흔들어 식별하려다 분리를 망친다 — 그래서 붙이지 않고
잰다. 이 모듈은 I/O 를 하지 않는다.
"""

from __future__ import annotations

import math
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "skills" / "speechtotext" / "scripts"))

import stt_catalog  # noqa: E402
import stt_identify  # noqa: E402
import stt_speakers  # noqa: E402


@dataclass(frozen=True, slots=True)
class _Said:
    text: str
    start_ms: int | None
    end_ms: int | None
    speaker: str


def _unit(cosine: float) -> tuple[float, float, float]:
    """`_HERE` 와의 코사인이 정확히 `cosine` 인 단위 벡터 — 셋째 축이 0 인 평면에 둔다."""
    return (cosine, math.sqrt(max(0.0, 1.0 - cosine * cosine)), 0.0)


_HERE = _unit(1.0)
#: 그 평면 밖 — 등록된 누구와도 코사인 0 이라 「닮은 사람이 없는 화자」를 만든다.
_ELSEWHERE = (0.0, 0.0, 1.0)


def test_spans_join_adjacent_sentences_of_one_label_and_ignore_the_others() -> None:
    said = (
        _Said("첫 문장.", 0, 2_000, "화자1"),
        _Said("이어지는 문장.", 2_200, 5_000, "화자1"),
        _Said("다른 화자.", 5_000, 7_000, "화자2"),
        _Said("다시 첫 화자.", 9_000, 11_000, "화자1"),
    )

    # 이 테스트가 재는 것은 이어붙임이지 최소 길이가 아니므로 문턱을 명시한다.
    assert stt_identify.spans(said, "화자1", min_span_ms=1_500) == (
        stt_catalog.Interval(0, 5_000),
        stt_catalog.Interval(9_000, 11_000),
    )


def test_spans_split_when_the_silence_between_sentences_exceeds_the_join_gap() -> None:
    said = (
        _Said("앞.", 0, 2_000, "화자1"),
        _Said("뒤.", 2_400, 4_000, "화자1"),
    )

    assert stt_identify.spans(said, "화자1", min_span_ms=1_500) == (
        stt_catalog.Interval(0, 2_000),
        stt_catalog.Interval(2_400, 4_000),
    )


def test_spans_trim_one_span_to_the_per_span_ceiling() -> None:
    said = (_Said("긴 발화.", 0, 40_000, "화자1"),)

    assert stt_identify.spans(said, "화자1", per_span_max_ms=30_000) == (
        stt_catalog.Interval(0, 30_000),
    )


def test_spans_drop_spans_shorter_than_the_minimum() -> None:
    said = (_Said("짧다.", 0, 1_000, "화자1"),)

    assert stt_identify.spans(said, "화자1") == ()


def test_spans_take_the_longest_first_and_cut_the_last_at_the_total_ceiling() -> None:
    said = (
        _Said("길다.", 0, 50_000, "화자1"),
        _Said("짧다.", 60_000, 80_000, "화자1"),
    )

    assert stt_identify.spans(
        said, "화자1", per_span_max_ms=30_000, total_max_ms=40_000
    ) == (stt_catalog.Interval(0, 30_000), stt_catalog.Interval(60_000, 70_000))


def test_spans_refuse_the_unknown_label_and_sentences_without_both_times() -> None:
    said = (
        _Said("미상.", 0, 30_000, "화자0"),
        _Said("끝을 모른다.", 0, None, "화자1"),
        _Said("시작을 모른다.", None, 30_000, "화자1"),
    )

    assert stt_identify.spans(said, "화자0") == ()
    assert stt_identify.spans(said, "화자1") == ()
    assert stt_identify.spans(said, "화자9") == ()


def test_cosine_is_one_for_the_same_direction_and_zero_for_a_zero_vector() -> None:
    assert stt_identify.cosine(_HERE, _HERE) == pytest.approx(1.0)
    assert stt_identify.cosine(_HERE, _unit(0.0)) == pytest.approx(0.0)
    assert stt_identify.cosine(_HERE, (0.0, 0.0)) == 0.0
    assert stt_identify.cosine((0.0, 0.0), (0.0, 0.0)) == 0.0
    assert stt_identify.cosine(_HERE, (-1.0, 0.0)) == pytest.approx(-1.0)


def test_thresholds_from_env_reads_the_owner_values() -> None:
    thresholds = stt_identify.thresholds_from_env(
        {
            "SPEECHTOTEXT_IDENTIFY_ACCEPT": "0.72",
            "SPEECHTOTEXT_IDENTIFY_SUGGEST": "0.5",
            "SPEECHTOTEXT_IDENTIFY_MARGIN": "0.08",
        }
    )

    assert thresholds == stt_identify.Thresholds(0.72, 0.5, 0.08)


def test_defaults_refuse_to_confirm_the_loudest_measured_impostor() -> None:
    """VC3 실측: 양성 0.8316 · 최고 음성 0.7452. 기본값이 그 사이에 있어야 한다."""
    assert stt_identify.DEFAULT_ACCEPT > 0.7452
    assert stt_identify.DEFAULT_ACCEPT <= 0.8316
    assert stt_identify.DEFAULT_SUGGEST < stt_identify.DEFAULT_ACCEPT


def test_thresholds_from_env_falls_back_to_the_measured_defaults() -> None:
    default = stt_identify.Thresholds(
        stt_identify.DEFAULT_ACCEPT, stt_identify.DEFAULT_SUGGEST, stt_identify.DEFAULT_MARGIN
    )

    assert stt_identify.thresholds_from_env({}) == default
    assert stt_identify.thresholds_from_env({"SPEECHTOTEXT_IDENTIFY_ACCEPT": "높게"}) == default
    assert stt_identify.thresholds_from_env({"SPEECHTOTEXT_IDENTIFY_ACCEPT": "0"}) == default
    assert stt_identify.thresholds_from_env({"SPEECHTOTEXT_IDENTIFY_ACCEPT": "1.5"}) == default
    # 제안 문턱이 확정 문턱보다 높으면 판정 순서가 뒤집힌다 — 한 값만 고쳐 쓰지 않는다.
    assert (
        stt_identify.thresholds_from_env(
            {"SPEECHTOTEXT_IDENTIFY_ACCEPT": "0.4", "SPEECHTOTEXT_IDENTIFY_SUGGEST": "0.9"}
        )
        == default
    )


def test_score_table_keeps_each_persons_best_enrolment() -> None:
    table = stt_identify.score_table(
        {"화자1": (_HERE,)},
        {"김민수": (_unit(0.3), _unit(0.86)), "이영희": (_unit(0.31),)},
    )

    assert table["화자1"]["김민수"] == pytest.approx(0.86)
    assert table["화자1"]["이영희"] == pytest.approx(0.31)


def test_score_table_averages_the_blocks_of_one_label() -> None:
    """노드 실측(VC3): 라벨의 블록을 이어 붙여 한 벡터로 만들면 채널 특성이 화자를 덮어

    확실한 양성(0.8559)이 다른 사람(0.8634)에게 졌다. 블록마다 재서 평균하면 순서가
    바로잡힌다(0.8316 > 0.7452). 그래서 라벨 쪽은 mean 이고 사람 쪽은 max 다.
    """
    table = stt_identify.score_table(
        {"화자1": (_unit(0.9), _unit(0.5))}, {"김민수": (_HERE,)}
    )

    assert table["화자1"]["김민수"] == pytest.approx(0.7)


def test_match_confirms_a_clear_winner_and_orders_labels_numerically() -> None:
    verdicts = stt_identify.match(
        {"화자10": (_HERE,), "화자2": (_ELSEWHERE,)},
        {"김민수": (_unit(0.86),), "이영희": (_unit(0.31),)},
        stt_identify.Thresholds(0.60, 0.45, 0.10),
    )

    assert tuple(verdict.label for verdict in verdicts) == ("화자2", "화자10")
    confirmed = verdicts[1]
    assert (confirmed.name, confirmed.kind) == ("김민수", "확정")
    assert confirmed.score == pytest.approx(0.86)
    assert confirmed.margin == pytest.approx(0.55)
    assert verdicts[0].kind == "미상"


def test_match_suggests_instead_of_confirming_below_the_accept_threshold() -> None:
    (verdict,) = stt_identify.match(
        {"화자1": (_HERE,)},
        {"김민수": (_unit(0.52),)},
        stt_identify.Thresholds(0.60, 0.45, 0.10),
    )

    assert (verdict.name, verdict.kind) == ("김민수", "제안")


def test_match_refuses_to_confirm_when_two_people_are_nearly_tied() -> None:
    (verdict,) = stt_identify.match(
        {"화자1": (_HERE,)},
        {"김민수": (_unit(0.90),), "이영희": (_unit(0.85),)},
        stt_identify.Thresholds(0.60, 0.45, 0.10),
    )

    assert (verdict.name, verdict.kind) == ("김민수", "제안")
    assert verdict.margin == pytest.approx(0.05)


def test_match_confirms_one_label_per_person_and_demotes_the_rest() -> None:
    verdicts = stt_identify.match(
        {"화자1": (_HERE,), "화자2": (_unit(0.95),)},
        {"김민수": (_unit(0.99),)},
        stt_identify.Thresholds(0.60, 0.45, 0.10),
    )

    assert [(verdict.label, verdict.kind) for verdict in verdicts] == [
        ("화자1", "확정"),
        ("화자2", "제안"),
    ]


def test_a_quiet_score_well_above_the_runner_up_still_names_nobody() -> None:
    """노드 실측 10-02 둘째 녹음 화자2(2026-10-21): .753, 같은 녹음 2위 .553 — 소유자가 아니었다.

    2위와의 차이로 확정하던 규칙은 이 화자에게 소유자 이름을 붙여 거두었다. 확정은 .80 한 기준이다.
    """
    verdicts = stt_identify.match(
        {"화자1": (_unit(0.553),), "화자2": (_unit(0.753),)}, {"김민수": (_HERE,)}
    )

    assert [(verdict.label, verdict.kind) for verdict in verdicts] == [
        ("화자1", "미상"),
        ("화자2", "제안"),
    ]
    assert [named.name for named in stt_identify.to_speaker_map(verdicts)] == [""]


def test_match_says_unknown_when_nobody_is_enrolled() -> None:
    (verdict,) = stt_identify.match(
        {"화자1": (_HERE,)}, {}, stt_identify.Thresholds(0.60, 0.45, 0.10)
    )

    assert (verdict.name, verdict.score, verdict.kind) == ("", 0.0, "미상")


def test_to_speaker_map_names_only_the_confirmed_and_keeps_a_suggestion_as_provenance() -> None:
    verdicts = (
        stt_identify.Verdict("화자1", "김민수", 0.8612, 0.55, "확정"),
        stt_identify.Verdict("화자2", "이영희", 0.5231, 0.21, "제안"),
        stt_identify.Verdict("화자3", "박철수", 0.2000, 0.02, "미상"),
    )

    assert stt_identify.to_speaker_map(verdicts) == (
        stt_speakers.SpeakerName("화자1", "김민수", "카탈로그 0.86"),
        stt_speakers.SpeakerName("화자2", "", "카탈로그 제안: 이영희 0.52"),
    )


def test_cache_key_shortens_the_model_digest() -> None:
    assert stt_identify.cache_key("a" * 64) == "a" * 12


def test_spans_stop_at_the_measured_block_count() -> None:
    """실측은 라벨당 6블록으로 순서를 바로잡았다 — 더 많이 담아 평균을 묽히지 않는다."""
    said = tuple(
        _Said(f"{index} 번째 발화.", index * 20_000, index * 20_000 + 10_000, "화자1")
        for index in range(10)
    )

    assert len(stt_identify.spans(said, "화자1")) == stt_identify.DEFAULT_MAX_SPANS
