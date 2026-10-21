"""등록된 목소리와 이 녹음의 화자를 **재는** 순수 절반 — I/O 도 subprocess 도 없다.

원래 아이디어는 녹음 선두에 등록본을 이어 붙여 분리기가 같은 군집으로 묶게 하는 것이었다.
그 방식은 카탈로그가 커지면 무너진다: 붙인 사람 수만큼 군집이 늘어 상한 8 과 임베딩별로 잰
임계값(0.8)을 먹고, 군집은 전역이라 붙인 목소리가 녹음 본체의 군집 경계까지 흔든다 —
식별하려다 분리를 망친다. 그래서 붙이지 않고, 분리가 끝난 뒤 군집마다 임베딩 하나를 뽑아
카탈로그와 코사인으로 잰다. 사람이 1만 명이어도 벡터 곱이라 비용이 늘지 않는다.

판정이 셋인 이유는 틀리는 방향이 다르기 때문이다. **확정**은 이름을 문서에 사실로 적고,
**제안**은 근거만 남겨 소유자가 노트에 이름을 적으면 그 녹음의 등록본이 하나 더 쌓이며,
**미상**은 아무 말도 하지 않는다. 점수가 높아도 2위와의 여유가 좁으면 확정하지 않는다 —
비슷한 두 목소리 사이에서 찍은 이름은 회의록에서 누군가의 발언 귀속이 된다.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Final, Literal, Protocol, TypeAlias

import stt_catalog
import stt_speakers

#: 출처 문구는 범례 문법을 쥔 `stt_speakers` 가 단독 정의한다 — 여기서는 가져다 쓴다.
SOURCE_PREFIX: Final = stt_speakers.CATALOG_SOURCE
SUGGEST_PREFIX: Final = stt_speakers.CATALOG_SUGGEST

#: 노드 실측(docs/qa/VC3, 2026-09-18)에서 나온 자리. 같은 녹음의 등록본 대비 블록 평균
#: 코사인은 양성(등록 구간 밖 같은 라벨) 0.8316, 가장 높은 음성 0.7452 였다 — accept 는 그
#: 둘 사이에서 양성에 가깝게 둔다. 표본이 녹음 1건·등록 1명이므로 실패 방향을 「확정 대신
#: 제안」으로 잡았다. 등록본이 늘면 같은 표로 다시 재고 이 상수를 옮긴다.
DEFAULT_ACCEPT: Final = 0.80
DEFAULT_SUGGEST: Final = 0.65
DEFAULT_MARGIN: Final = 0.05

#: 블록 하나를 자르는 상한과 라벨당 블록 수. 실측에서 3~15초 블록이 라벨 순서를 바로잡았다.
DEFAULT_PER_SPAN_MS: Final = 15_000
DEFAULT_MIN_SPAN_MS: Final = 3_000
DEFAULT_MAX_SPANS: Final = 6
DEFAULT_TOTAL_MS: Final = 60_000
#: 한 사람의 말이 문장 단위로 끊긴 자리. 이만한 침묵은 화자가 바뀐 증거가 아니다.
DEFAULT_JOIN_GAP_MS: Final = 300

Vector: TypeAlias = Sequence[float]
Kind: TypeAlias = Literal["확정", "제안", "미상"]
ScoreTable: TypeAlias = dict[str, dict[str, float]]


class TimedLike(Protocol):
    """식별이 필요한 문장 필드 — 라벨과 양쪽 시각."""

    @property
    def start_ms(self) -> int | None: ...

    @property
    def end_ms(self) -> int | None: ...

    @property
    def speaker(self) -> str: ...


@dataclass(frozen=True, slots=True)
class Thresholds:
    accept: float
    suggest: float
    margin: float


@dataclass(frozen=True, slots=True)
class Verdict:
    label: str
    name: str
    score: float
    margin: float
    kind: Kind


DEFAULTS: Final = Thresholds(DEFAULT_ACCEPT, DEFAULT_SUGGEST, DEFAULT_MARGIN)


def _ratio(raw: str | None, default: float) -> float:
    try:
        value = float(raw) if raw is not None else default
    except ValueError:
        return default
    return value if 0.0 < value <= 1.0 else default


def thresholds_from_env(env: Mapping[str, str]) -> Thresholds:
    """설정이 판정 순서를 뒤집을 수 있으면 한 값만 고쳐 쓰지 않고 전부 기본값으로 돌아간다."""
    accept = _ratio(env.get("SPEECHTOTEXT_IDENTIFY_ACCEPT"), DEFAULT_ACCEPT)
    suggest = _ratio(env.get("SPEECHTOTEXT_IDENTIFY_SUGGEST"), DEFAULT_SUGGEST)
    margin = _ratio(env.get("SPEECHTOTEXT_IDENTIFY_MARGIN"), DEFAULT_MARGIN)
    if suggest > accept:
        return DEFAULTS
    return Thresholds(accept, suggest, margin)


def cosine(left: Vector, right: Vector) -> float:
    """길이 0 인 벡터는 방향이 없다 — 닮았다고도 다르다고도 말하지 않는다(0.0)."""
    dot = sum(a * b for a, b in zip(left, right, strict=False))
    left_norm = sum(a * a for a in left) ** 0.5
    right_norm = sum(b * b for b in right) ** 0.5
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return dot / (left_norm * right_norm)


def spans(
    sentences: Iterable[TimedLike],
    label: str,
    *,
    per_span_max_ms: int = DEFAULT_PER_SPAN_MS,
    total_max_ms: int = DEFAULT_TOTAL_MS,
    min_span_ms: int = DEFAULT_MIN_SPAN_MS,
    max_spans: int = DEFAULT_MAX_SPANS,
    join_gap_ms: int = DEFAULT_JOIN_GAP_MS,
) -> tuple[stt_catalog.Interval, ...]:
    """그 라벨이 **확실히 말한** 구간만 고른다 — 긴 것부터, 상한까지, 시각순으로.

    `stt_catalog.plan_segments` 와 나누어 둔 이유는 입력이 다르기 때문이다. 저쪽은 블록
    헤더뿐인 전사본을 읽어 구간 끝을 다음 블록의 시작으로 **추정**하고, 이쪽은 단어 시각이
    살아 있는 문장을 받아 실제 끝을 쓴다. 추정한 끝에는 다음 화자의 목소리가 섞인다.
    """
    if label == stt_catalog.UNKNOWN_LABEL:
        return ()
    timed = sorted(
        (sentence.start_ms, sentence.end_ms)
        for sentence in sentences
        if sentence.speaker == label
        and sentence.start_ms is not None
        and sentence.end_ms is not None
        and sentence.end_ms > sentence.start_ms
    )
    joined: list[stt_catalog.Interval] = []
    for start, end in timed:
        if joined and start is not None and start - joined[-1].end_ms <= join_gap_ms:
            joined[-1] = stt_catalog.Interval(joined[-1].start_ms, int(end or 0))
            continue
        joined.append(stt_catalog.Interval(int(start or 0), int(end or 0)))
    candidates = [
        stt_catalog.Interval(one.start_ms, one.start_ms + min(one.length_ms, per_span_max_ms))
        for one in joined
        if one.length_ms >= min_span_ms
    ]
    candidates.sort(key=lambda one: (-one.length_ms, one.start_ms))
    chosen: list[stt_catalog.Interval] = []
    remaining = total_max_ms
    for one in candidates[:max_spans]:
        if remaining <= 0:
            break
        take = min(one.length_ms, remaining)
        chosen.append(stt_catalog.Interval(one.start_ms, one.start_ms + take))
        remaining -= take
    return tuple(sorted(chosen))


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def score_table(
    speakers: Mapping[str, Sequence[Vector]], people: Mapping[str, Sequence[Vector]]
) -> ScoreTable:
    """라벨 점수는 **블록마다 재서 평균**, 사람 점수는 등록본 중 가장 닮은 하나.

    비대칭에는 실측 근거가 있다(docs/qa/VC3, 2026-09-18). 한 라벨의 발화를 이어 붙여 벡터
    하나로 만들면 같은 마이크·같은 방의 채널 특성이 화자 특성을 덮어, 확실한 양성(0.8559)이
    다른 사람(0.8634)에게 졌다. 블록마다 재서 평균하면 순서가 바로잡힌다(0.8316 > 0.7452) —
    블록은 같은 세션의 반복 관측이라 평균이 잡음을 줄인다. 반대로 한 사람의 등록본들은 서로
    다른 마이크·방의 **대안**이므로 가장 닮은 하나를 쓴다(평균하면 맞는 등록본이 틀린
    등록본에 끌려 내려간다).
    """
    return {
        label: {
            name: _mean(
                tuple(
                    max((cosine(block, sample) for sample in samples), default=0.0)
                    for block in blocks
                )
            )
            for name, samples in people.items()
        }
        for label, blocks in speakers.items()
    }


def _label_order(label: str) -> tuple[int, str]:
    digits = label[2:]
    return (int(digits), label) if digits.isdigit() else (1 << 30, label)


def _verdict(label: str, scores: Mapping[str, float], thresholds: Thresholds) -> Verdict:
    ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    if not ranked:
        return Verdict(label, "", 0.0, 0.0, "미상")
    name, score = ranked[0]
    margin = score - ranked[1][1] if len(ranked) > 1 else score
    if score >= thresholds.accept and margin >= thresholds.margin:
        return Verdict(label, name, score, margin, "확정")
    if score >= thresholds.suggest:
        return Verdict(label, name, score, margin, "제안")
    return Verdict(label, name, score, margin, "미상")


def match(
    speakers: Mapping[str, Sequence[Vector]],
    people: Mapping[str, Sequence[Vector]],
    thresholds: Thresholds = DEFAULTS,
) -> tuple[Verdict, ...]:
    """라벨마다 한 판정, 사람마다 확정 하나.

    한 사람이 두 라벨에서 확정되면 분리기가 그 사람을 둘로 쪼갠 것이다. 그 사실을 지우지
    않되(제안으로 남긴다) 둘 다 사실로 적지는 않는다 — 회의록에 같은 사람이 두 참석자로
    앉으면 액션아이템 담당이 갈린다.
    """
    table = score_table(speakers, people)
    verdicts = [
        _verdict(label, table[label], thresholds)
        for label in sorted(table, key=_label_order)
    ]
    best: dict[str, float] = {}
    for verdict in verdicts:
        if verdict.kind == "확정":
            best[verdict.name] = max(best.get(verdict.name, 0.0), verdict.score)
    return tuple(
        verdict
        if verdict.kind != "확정" or verdict.score >= best[verdict.name]
        else Verdict(verdict.label, verdict.name, verdict.score, verdict.margin, "제안")
        for verdict in verdicts
    )


def to_speaker_map(verdicts: Iterable[Verdict]) -> stt_speakers.SpeakerMap:
    """확정만 이름이 된다. 제안은 이름 없는 근거로 범례에 남고, 미상은 아무것도 남기지 않는다."""
    named: list[stt_speakers.SpeakerName] = []
    for verdict in verdicts:
        if verdict.kind == "확정":
            named.append(
                stt_speakers.SpeakerName(
                    verdict.label, verdict.name, f"{SOURCE_PREFIX} {verdict.score:.2f}"
                )
            )
        elif verdict.kind == "제안":
            named.append(
                stt_speakers.SpeakerName(
                    verdict.label, "", f"{SUGGEST_PREFIX}{verdict.name} {verdict.score:.2f}"
                )
            )
    return tuple(named)


def cache_key(model_sha256: str) -> str:
    """임베딩은 모델에 묶인다 — 모델이 바뀌면 캐시는 다른 파일이 된다."""
    return model_sha256[:12]
