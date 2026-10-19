"""판정별 조치 절차 — 누가, 어디서, 무엇을 승인하는지. 절차의 상세 정본은 연결-승인-점검 가이드다."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Final

GUIDE_DOC: Final = "docs/guide/연결-승인-점검.md"

GWS_SCOPES: Final = ",".join((
    "openid", "email", "profile",
    *(f"https://www.googleapis.com/auth/{name}" for name in (
        "userinfo.email", "userinfo.profile", "calendar", "drive", "spreadsheets",
        "gmail.send", "gmail.readonly", "tasks", "meetings.space.created",
    )),
))

_STEPS: Final[Mapping[str, tuple[str, ...]]] = {
    "yoodori-bot": (
        "유돌 봇의 개인 서버 초대·Message Content Intent와 대화·통지 채널 읽기/쓰기 권한을 확인한다",
        "릴리스·공급망 카드도 발신 봇 채널을 따른다(Q236). notice-bot override의 채널 id와 유돌 봇 접근 권한을 확인한다",
        "발신 자격증명은 notice-bot 릴리스 전환으로 배치한다(발신-봇-유돌 소개 참조). 멈춘 Hermes 게이트웨이를 재시동하지 않는다",
    ),
    "hermes-cli": (
        "이 계정에 Hermes 를 설치한다 — 버전 핀과 설치 명령: docs/guide/third-party-runtime-prereqs.md §3",
    ),
    "hermes-gateway": (
        "(운영자, 저장소 체크아웃에서) automation/provision-agent.sh {account}",
        "상태: sudo -u {account} -H env XDG_RUNTIME_DIR=/run/user/$(id -u {account}) systemctl --user status {unit}",
    ),
    "primary-model": (
        "주 모델은 {home}/.hermes/config.yaml 의 model.provider 다 — 기본(openai-codex)이면 ChatGPT 구독 계정으로 로그인한다: sudo -u {account} -H {home}/.local/bin/hermes auth add openai-codex --type oauth",
        "화면의 주소를 브라우저에서 열고 표시된 코드를 입력해 승인한다",
        "확인: sudo -u {account} -H {home}/.local/bin/hermes auth status openai-codex  → logged in",
        "다른 공급자면 hermes auth add <공급자> 로 로그인하거나, custom:<이름> 이면 custom_providers 에 그 이름을 둔다(configs/routing-policy.md)",
    ),
    "model-fallback": (
        "(선택) 폴백 로그인: sudo -u {account} -H {home}/.local/bin/hermes auth add xai-oauth --type oauth",
        "폴백 목록은 {home}/.hermes/config.yaml 의 fallback_providers 하나뿐이다(configs/routing-policy.md)",
    ),
    "discord-bot": (
        "Discord Developer Portal → 앱 → Bot: 토큰을 발급하고 Message Content Intent 를 켠다",
        "{home}/.env.secrets(0600, 소유자 {account}) 에 DISCORD_BOT_TOKEN=<토큰> 한 줄을 둔다 — 채팅·화면에 붙여넣지 않는다",
        "봇을 개인 서버에 초대한다(권한·초대 URL: docs/guide/third-party-runtime-prereqs.md §1.3)",
        "토큰을 바꿨으면 게이트웨이를 재시동한다(agent·peer 함께 — docs/guide/install.md §6.1)",
    ),
    "approval-surface": (
        "개인 서버의 #agent-chat 채널 id 를 {home}/.hermes/interop/config.json 의 agent_chat_channel_id 에 적는다",
        "소유자 Discord 사용자 id 를 owner_id 에 적는다 — 이 id 의 ✅/⛔ 만 승인으로 인정된다",
        "정기 통지·doctor 알람용 #notifications 채널 id 를 owner_notice_channel_id 에 적는다(비우면 DM)",
    ),
    "google-workspace": (
        "브라우저가 있는 PC 에 gws 를 설치하고 Google Cloud 의 Desktop OAuth 클라이언트 파일을 ~/.config/gws/client_secret.json 으로 둔다",
        "필요한 권한만 요청해 동의한다(Select all 금지): gws auth login --scopes \"" + GWS_SCOPES + "\"",
        "생성된 credentials.enc · .encryption_key · client_secret.json 을 노드 {home}/.config/gws/ 로 옮긴다(디렉터리 0700·파일 0600, 소유자 {account})",
        "확인: sudo -u {account} -H {home}/.local/bin/gws auth status  → token_valid: true",
    ),
    "drive-publish": (
        "{home}/.env.secrets 에 DRIVE_PUBLISH_ENABLED=1 을 추가하면 산출물이 Drive autophagy/ 트리에 발행된다(docs/guide/drive-publish.md)",
    ),
    "institutional-mail": (
        "기관메일을 쓸 때만: {home}/.env.secrets 에 MAILON_ID · MAILON_PW · MAILON_TOTP_SECRET 세 줄을 모두 둔다",
    ),
    "speech-to-text": (
        "whisper.cpp 를 빌드하고 모델을 받은 뒤 {home}/.env.secrets 에 SPEECHTOTEXT_WHISPER_BIN · SPEECHTOTEXT_WHISPER_MODEL 을 실제 경로로 적는다(skills/speechtotext/SKILL.md)",
    ),
    "agent-chat-free-response": (
        "게이트웨이를 재시동하면 시작 단계가 interop config 의 agent_chat_channel_id 를 {home}/.hermes/config.yaml 의 discord.free_response_channels 에 자동으로 더한다(agent·peer 함께 재시동)",
        "지금 바로 더하려면: sudo -u {account} -H env PYTHONPATH={home}/.hermes/autophagy-import python3 -m automation.interop.free_response --apply 뒤 게이트웨이 재시동",
        "ignored_channels 에 든 채널은 일부러 막은 것이라 더하지 않는다 — 풀려면 그 목록에서 먼저 뺀다",
    ),
    "scheduled-jobs": (
        "실패 문구: sudo -u {account} -H {home}/.local/bin/hermes cron list --all",
        "원인 재현: sudo -u {account} -H python3 {home}/.hermes/scripts/<스크립트>",
        "주 모델 변경 뒤 건너뜀(drift_skip)이면 모델을 고정하지 말고 같은 정의로 다시 만든다: configs/routing-policy.md 「Changing the main model」",
        "고치면 다음 실행에서 통과로 바뀌고, doctor 알람이 해결을 알린다",
    ),
    "cron-required-env": (
        "빠진 이름마다 {home}/.hermes/.env(0600, 소유자 {account}) 에 <이름>=<값> 한 줄을 둔다 — 값은 소유자가 정하고 채팅에 붙여넣지 않는다",
        "Hermes 가 no-agent 작업 직전에 그 파일을 다시 읽으므로 게이트웨이 재시동은 필요 없다",
        "값의 뜻은 그 작업의 가이드가 정한다",
        "무엇을 요구하는지의 정본: 각 배포기 옆 deploy-manifest.txt 의 v2:cron 행 env= 값",
    ),
}


def steps(key: str, *, account: str, home: str, unit: str) -> tuple[str, ...]:
    lines = _STEPS.get(key, ())
    rendered = tuple(line.format(account=account, home=home, unit=unit) for line in lines)
    return (*rendered, f"자세한 절차: {GUIDE_DOC}")
