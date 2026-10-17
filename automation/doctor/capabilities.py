"""관측 → 기능별 판정. 설치 마법사의 「승인할 것」 미리보기와 doctor 가 같은 레지스트리를 읽는다.

판정은 셋이다: PASS(통과) · WARN(확인 — 선택 기능 미구성·부분 권한·판정 불가) ·
FAIL(고장 — 필수 연결이 없거나, 구성했는데 동작하지 않는다). 순수 로직이라 I/O 가 없다.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

from automation.doctor import cron_env, guides
from automation.doctor.facts import DRIVE_KEY, GWS_FILES, PATH_KEYS, TOKEN_KEY, CronJob, Facts, GwsStatus, Role
from automation.install.checks import CheckResult, Status

#: Hermes credential_pool 의 일시 쿨다운 상태(영구 인증 실패는 "dead").
COOLDOWN: Final = "exhausted"
#: 이보다 오래 남은 한도만 소유자에게 알린다 — Hermes 기본 쿨다운(최대 1시간)은 저절로 풀린다.
COOLDOWN_NOTICE_SECONDS: Final = 60 * 60

CODEX: Final = "openai-codex"
CUSTOM_PREFIX: Final = "custom:"
MAILON_KEYS: Final = ("MAILON_ID", "MAILON_PW", "MAILON_TOTP_SECRET")
STREAK_FAIL: Final = 3
#: Hermes 가 모델 고정 없는 agent 모드 잡을 주 모델 변경 뒤 건너뛸 때 오류에 남기는 표식.
DRIFT_SKIP: Final = "drift_skip"
_G: Final = "https://www.googleapis.com/auth/"
GWS_SERVICES: Final = (
    ("캘린더", (f"{_G}calendar",)),
    ("메일 발송", (f"{_G}gmail.send", "https://mail.google.com/")),
    ("메일 읽기", (f"{_G}gmail.readonly", f"{_G}gmail.modify", "https://mail.google.com/")),
    ("Drive", (f"{_G}drive",)),
    ("과제비 시트", (f"{_G}spreadsheets",)),
    ("할 일", (f"{_G}tasks",)),
    ("회의 주소(Meet)", (f"{_G}meetings.space.created",)),
)


@dataclass(frozen=True, slots=True)
class Verdict:
    status: Status
    detail: str
    subjects: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Capability:
    key: str
    label: str
    roles: frozenset[Role]
    approve_at: str
    affects: str
    judge: Callable[[Facts], Verdict]


@dataclass(frozen=True, slots=True)
class Finding:
    key: str
    label: str
    status: Status
    detail: str
    affects: str
    steps: tuple[str, ...]
    subjects: tuple[str, ...] = ()


def _ok(detail: str) -> Verdict:
    return Verdict(Status.PASS, detail)


def _warn(detail: str, subjects: tuple[str, ...] = ()) -> Verdict:
    return Verdict(Status.WARN, detail, subjects)


def _fail(detail: str, subjects: tuple[str, ...] = ()) -> Verdict:
    return Verdict(Status.FAIL, detail, subjects)


def _hermes(f: Facts) -> Verdict:
    return _ok("~/.local/bin/hermes 있음") if f.hermes else _fail("Hermes 실행 파일이 없다")


def _gateway(f: Facts) -> Verdict:
    if f.gateway == "active":
        return _ok(f"{f.gateway_unit} 실행 중")
    if f.gateway == "unknown":
        return _warn(f"{f.gateway_unit} 상태를 알 수 없다(systemctl --user 에 닿지 못함)")
    return _fail(f"{f.gateway_unit} 가 {f.gateway} — 봇이 Discord 에 응답하지 않는다")


def _custom_name(provider: str) -> str | None:
    return provider.removeprefix(CUSTOM_PREFIX) if provider.startswith(CUSTOM_PREFIX) else None


def _primary(f: Facts) -> Verdict:
    provider = f.model_provider or CODEX
    custom = _custom_name(provider)
    if custom is not None:
        if custom in f.custom_providers:
            return _ok(f"{provider} — 사용자 정의 공급자라 로그인 대상이 아니다(연결 자체는 확인하지 않는다)")
        return _fail(f"{provider} 를 쓰도록 돼 있으나 custom_providers 에 {custom} 가 없다", (provider,))
    statuses = (f.auth or {}).get(provider, ())
    if not statuses:
        return _fail(f"{provider} 로그인이 없다 — 대화·요약·배치 모델 호출이 전부 실패한다", (provider,))
    named = f"{provider}/{f.model_default}" if f.model_default else provider
    if any(status in ("", "ok") for status in statuses):
        return _ok(f"주 모델 {named} — 로그인 {len(statuses)}건, 마지막 상태 정상")
    if set(statuses) == {COOLDOWN}:
        return _cooldown(f, provider, named)
    states = ", ".join(sorted(set(statuses)))
    return _fail(f"{provider} 로그인은 있으나 마지막 상태가 {states} — 다시 로그인한다", (provider,))


def _usable_fallbacks(f: Facts) -> tuple[str, ...]:
    return tuple(provider for provider in f.fallback
                 if _custom_name(provider) is not None or (f.auth or {}).get(provider))


def _cooldown(f: Facts, provider: str, named: str) -> Verdict:
    """`exhausted` 는 고장이 아니라 Hermes 의 일시 쿨다운이다 — 다시 로그인해서 풀리는 것이 없다.

    제한에 한 번 걸리면 Hermes 가 그 자격을 잠깐(혼자인 자격은 1분, 길어야 1시간) 쉬게 하고 그동안 폴백이 받는다.
    영구 인증 실패는 `dead` 로 따로 남으므로 여기서 고장을 내지 않는다(2026-10-15 매시 점검이 이미 풀린 쿨다운을
    「고장 · 다시 로그인」으로 1시간 내보냈다). 알리는 것은 폴백이 없거나 한도가 1시간을 넘겨 남을 때뿐이다.
    """
    remaining = (f.cooldowns or {}).get(provider)
    if remaining is not None and remaining <= 0:
        return _ok(f"주 모델 {named} — 직전 사용 한도로 쉬던 시간이 끝났다(다음 호출에서 정상으로 돌아온다)")
    left = "남은 시간 미상" if remaining is None else f"약 {max(1, round(remaining / 60))}분 남음"
    fallbacks = _usable_fallbacks(f)
    if not fallbacks:
        return _warn(f"주 모델 {named} 이 사용 한도로 쉬는 중({left}) — 폴백이 없어 그동안 응답이 실패한다",
                     (provider,))
    if remaining is not None and remaining <= COOLDOWN_NOTICE_SECONDS:
        return _ok(f"주 모델 {named} 이 사용 한도로 잠깐 쉬는 중({left}) — 그동안 폴백 {', '.join(fallbacks)} 가 받는다")
    return _warn(f"주 모델 {named} 이 사용 한도로 쉬는 중({left}) — 그동안 폴백 {', '.join(fallbacks)} 가 받는다. "
                 "다시 로그인할 필요는 없다", (provider,))


def _fallback(f: Facts) -> Verdict:
    if not f.fallback:
        return _warn("폴백 모델이 없다 — 주 모델이 막히면 곧바로 실패한다")
    missing = tuple(provider for provider in f.fallback
                    if _custom_name(provider) is None and not (f.auth or {}).get(provider))
    if missing:
        return _warn(f"폴백 {', '.join(missing)} 로그인이 없다 — 폴백이 실제로는 동작하지 않는다", missing)
    named = (
        [f"{provider}/{model}" for provider, model in zip(f.fallback, f.fallback_models, strict=True)]
        if len(f.fallback) == len(f.fallback_models) else list(f.fallback)
    )
    return _ok(f"폴백 {', '.join(named)} 로그인 있음")


def _by_design(result: CheckResult) -> bool:
    """설치 검사가 알리는 것 중 런타임에서는 결함이 아닌 둘을 뺀다.

    DM 승인 표면은 읽기 전용으로 확인할 수 없다는 선언일 뿐이고, 개인 서버 #approvals 는 id 가 없으면
    런타임이 채널 이름으로 찾는다(approval_directory 의 이름 검색 폴백) — 그래서 id 부재는 고장이 아니다.
    """
    return result.name.startswith("surface[") or (
        result.name == "channel[approvals]" and result.detail.startswith("MISSING-CHANNEL-ID")
    )


def _discord(f: Facts) -> Verdict:
    if f.secret_names is None:
        return _fail("~/.env.secrets 가 없다 — 봇 토큰을 둘 곳이 없다")
    if TOKEN_KEY not in f.secret_names:
        return _fail("DISCORD_BOT_TOKEN 이 비었다 — 봇이 접속하지 못한다")
    match f.discord:
        case None:
            return _ok("토큰 있음(온라인 검사 생략)")
        case str() as reason:
            return _warn(f"Discord API 로 확인하지 못했다: {reason}")
        case results:
            relevant = [result for result in results if not _by_design(result)]
            failed = [result for result in relevant if result.status is Status.FAIL]
            warned = [result for result in relevant if result.status is Status.WARN]
            if failed:
                detail = "; ".join(f"{result.name}: {result.detail[:140]}" for result in failed[:3])
                return _fail(detail, tuple(result.name for result in failed))
            if warned:
                return _warn("; ".join(f"{result.name}: {result.detail[:140]}" for result in warned[:3]),
                             tuple(result.name for result in warned))
            return _ok(f"토큰·인텐트·권한·채널 {len(relevant)}건 확인")


def _surface(f: Facts) -> Verdict:
    if f.interop is None:
        return _fail("~/.hermes/interop/config.json 이 없다 — 승인 카드를 게시할 곳이 없다")
    missing = tuple(key for key in ("agent_chat_channel_id", "owner_id") if key not in f.interop)
    if missing:
        return _fail(f"{', '.join(missing)} 가 비었다 — 승인 요청이 게시되지 않는다(fail-closed)", missing)
    if "owner_notice_channel_id" not in f.interop:
        return _warn("owner_notice_channel_id 가 비었다 — 정기 통지·doctor 알람이 DM 으로 간다")
    return _ok("승인 채널·소유자·통지 채널 설정됨")


def _free_response(f: Facts) -> Verdict:
    channel = f.agent_chat_channel
    if not channel:
        return _ok("해당 없음 — agent-chat 채널 채널 id 가 아직 없다(승인 채널 점검 참조)")
    if f.discord_channels is None:
        return _warn("~/.hermes/config.yaml 의 discord 블록을 읽지 못했다 — 무멘션 응답 여부 판정 불가")
    free, ignored = f.discord_channels
    if channel in ignored:
        return _ok("agent-chat 채널은 유돌이 맡는다(이 게이트웨이는 무시)")
    if channel in free:
        return _ok("agent-chat 채널 은 @ 없이도 답한다")
    return _fail("agent-chat 채널 이 discord.free_response_channels 에 없다 — @ 를 달아야만 답한다",
                 ("free_response_channels",))


def _gws(f: Facts) -> Verdict:
    if not f.gws_files:
        return _warn("미연결 — 메일 발송·캘린더·Drive·과제비·할 일 기능이 동작하지 않는다")
    if not f.gws_installed:
        return _fail("자격증명은 있으나 gws 실행 파일이 없다")
    absent = tuple(name for name in GWS_FILES if name not in f.gws_files)
    if absent:
        return _fail(f"자격증명 파일 없음: {', '.join(absent)} — Google 승인을 다시 옮긴다", absent)
    match f.gws:
        case None:
            return _ok("자격증명 파일 있음(토큰 검사 생략)")
        case str() as reason:
            return _fail(f"{reason} — 토큰을 확인하지 못했다")
        case GwsStatus(token_valid=False):
            return _fail("토큰이 유효하지 않다(만료·폐기) — Google 승인을 다시 한다")
        case GwsStatus(scopes=scopes):
            missing = tuple(name for name, accepted in GWS_SERVICES if not set(accepted) & set(scopes))
            if missing:
                return _warn(f"권한 없음: {', '.join(missing)} — 그 기능만 실패한다", missing)
            return _ok(f"토큰 유효, {len(GWS_SERVICES)}개 서비스 권한")


def _drive(f: Facts) -> Verdict:
    if f.drive_publish:
        return _ok(f"{DRIVE_KEY}=1 — 산출물을 Drive 에 발행한다")
    return _warn("꺼짐 — 회의록·문서 산출물이 Drive 에 올라가지 않고 로컬에만 남는다")


def _mailon(f: Facts) -> Verdict:
    have = tuple(key for key in MAILON_KEYS if key in (f.secret_names or frozenset()))
    if not have:
        return _warn("미구성 — 기관메일을 쓰지 않으면 무시해도 된다")
    missing = tuple(key for key in MAILON_KEYS if key not in have)
    if missing:
        return _fail(f"일부만 설정됨, 빠진 값: {', '.join(missing)}", missing)
    return _ok("기관메일 자격증명 3종 있음")


def _stt(f: Facts) -> Verdict:
    if not f.paths:
        return _warn("미구성 — 로컬 음성 전사(whisper.cpp) 경로가 없다")
    broken = tuple(key for key, exists in f.paths.items() if not exists)
    if broken:
        return _fail(f"설정된 경로가 없다: {', '.join(broken)}", broken)
    unset = tuple(key for key in PATH_KEYS if key not in f.paths)
    if unset:
        return _warn(f"설정 안 됨: {', '.join(unset)}", unset)
    return _ok("whisper.cpp 실행 파일·모델 있음")


def _broken_detail(job: CronJob) -> str:
    if DRIFT_SKIP in job.last_error:
        # Hermes 는 이 건너뜀을 한 번만 알리고 잡을 고칠 때까지 계속 건너뛴다 — 저절로 낫지 않는다.
        return f"{job.name}(주 모델 변경 뒤 건너뜀, {job.failure_streak}회 연속 — 모델 고정 없이 같은 정의로 다시 만든다)"
    return f"{job.name}({job.failure_streak}회 연속: {job.last_error})"


def _cron(f: Facts) -> Verdict:
    if f.cron is None:
        return _warn("정기 작업 목록이 없다 — 워처가 아직 배포되지 않았다")
    active = [job for job in f.cron if job.enabled and not job.paused]
    # 사용자 타이머로 옮긴 잡(runner=systemd)은 Hermes 쪽이 일부러 일시정지돼 있다 — 중지가 아니다.
    # 그 잡은 타이머가 결과를 남기고, 타이머도 꺼져 있고 Hermes 도 멈췄으면 아무도 돌리지 않는 것이다.
    moved = {timer.name for timer in f.timers}
    running = [timer for timer in f.timers if timer.timer == "active"]
    paused = {job.name for job in f.cron if job.paused}
    orphaned = [timer.name for timer in f.timers if timer.timer != "active" and timer.name in paused]
    timer_jobs = [CronJob(t.name, "", True, False, t.last_status, t.failure_streak, t.last_error) for t in running]
    failing = [job for job in (*active, *timer_jobs) if job.last_status == "error"]
    broken = [job for job in failing if job.failure_streak >= STREAK_FAIL or DRIFT_SKIP in job.last_error]
    if broken or orphaned:
        detail = "; ".join([*(_broken_detail(job) for job in broken),
                            *(f"{name}(타이머 꺼짐·Hermes 일시정지 — 아무도 돌리지 않는다)" for name in orphaned)])
        return _fail(f"{len(broken) + len(orphaned)}개 반복 실패·멈춤 — {detail}",
                     (*(job.name for job in broken), *orphaned))
    stopped = [job for job in f.cron if (not job.enabled or job.paused) and job.name not in moved]
    if failing or stopped:
        names = tuple(job.name for job in (*failing, *stopped))
        return _warn(f"최근 실패·중지: {', '.join(names)}", names)
    if running:
        return _ok(f"{len(active) + len(running)}개 정상(사용자 타이머 {len(running)}개)")
    return _ok(f"{len(active)}개 정상")


def _cron_env(f: Facts) -> Verdict:
    return Verdict(*cron_env.judge(f.cron_env))


_BOTH: Final[frozenset[Role]] = frozenset(("agent", "peer"))
_AGENT: Final[frozenset[Role]] = frozenset(("agent",))

CAPABILITIES: Final = (
    Capability("hermes-cli", "Hermes 런타임", _BOTH, "이 계정에 Hermes 설치", "에이전트 전체", _hermes),
    Capability("hermes-gateway", "Hermes 게이트웨이", _BOTH, "", "Discord 대화·승인 버튼", _gateway),
    Capability("primary-model", "주 모델(~/.hermes/config.yaml 의 model:)", _BOTH,
               "ChatGPT 구독 계정의 기기 코드 승인", "모든 대화·요약·배치 모델 호출", _primary),
    Capability("model-fallback", "폴백 모델 로그인", _BOTH,
               "xAI 계정의 기기 코드 승인(선택)", "주 모델이 막혔을 때의 대체 응답", _fallback),
    Capability("discord-bot", "Discord 봇", _BOTH,
               "Discord Developer Portal(토큰·Message Content 인텐트)과 개인 서버 초대",
               "대화·승인 카드·결과 통지 전체", _discord),
    Capability("approval-surface", "승인 채널", _AGENT,
               "개인 서버 agent-chat 채널·#notifications 채널 id 와 소유자 id",
               "메일·캘린더·과제비·할 일 등 모든 승인 요청", _surface),
    Capability("agent-chat-free-response", "agent-chat 채널 무멘션 응답", _AGENT,
               "", "agent-chat 채널 에서 @ 없이 에이전트에게 말하기", _free_response),
    Capability("google-workspace", "Google Workspace", _AGENT,
               "Google 동의 화면(OAuth) — 캘린더·Gmail·Drive·Sheets·Tasks",
               "메일 발송·캘린더·Drive 발행·과제비·할 일", _gws),
    Capability("drive-publish", "Drive 산출물 발행", _AGENT,
               "~/.env.secrets 의 DRIVE_PUBLISH_ENABLED=1", "회의록·문서가 Drive 에 올라가는 것", _drive),
    Capability("institutional-mail", "기관메일(MailOn, 선택)", _AGENT,
               "기관 계정 ID·비밀번호·OTP 비밀", "기관메일 조회·발송", _mailon),
    Capability("speech-to-text", "로컬 음성 전사", _AGENT,
               "whisper.cpp 설치와 모델 경로", "녹음 → 전사본 → 회의록", _stt),
    Capability("scheduled-jobs", "정기 작업(워처)", _BOTH, "",
               "승인 리액션 처리·리마인더·일일 보고 등 백그라운드 작업", _cron),
    Capability("cron-required-env", "정기 작업 필수 설정값", _BOTH, "",
               "그 값을 요구하는 정기 작업(예: 일일 지출 보고)", _cron_env),
)


def evaluate(facts: Facts) -> tuple[Finding, ...]:
    home = str(facts.home)
    findings: list[Finding] = []
    for capability in CAPABILITIES:
        if facts.role not in capability.roles:
            continue
        verdict = capability.judge(facts)
        steps = () if verdict.status is Status.PASS else guides.steps(
            capability.key, account=facts.account, home=home, unit=facts.gateway_unit,
        )
        findings.append(Finding(capability.key, capability.label, verdict.status, verdict.detail,
                                capability.affects, steps, verdict.subjects))
    return tuple(findings)
