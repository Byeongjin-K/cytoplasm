# 리눅스도 한 줄로 설치하고, 처음 설치에서 막히던 곳을 고칩니다

## 바뀐 것

- 리눅스(·macOS): `curl -fsSL https://raw.githubusercontent.com/orientpine/cytoplasm/main/install.sh | bash` 한 줄이 git·Python 3.11+·bun·omo 를 찾거나 깔고, 공지 지문과 같은 업데이트 신뢰키로 최신 릴리스 태그의 서명을 확인한 뒤 도리 기본 설치를 합니다. 같은 줄이 업데이트입니다.
- 윈도우 한 줄: 파이썬 런처가 없는 PC에서 `The term 'C' is not recognized` 로 멈추던 버그를 고쳤고, omo 가 없으면 omo 설치기로 깔아 리드 탭에 omo 가 바로 뜹니다.
- 도리 설치기: `~/.dori/dori.env` 예시를 0600 으로 만들어 네 줄(봇 토큰·서버·채널·소유자 id)만 채우면 되게 했고, 리눅스는 디스코드 수신 같은 상시 루프를 사용자 유닛 `dori-serve.service` 로 켭니다. 방금 깐 bun 을 `~/.bun/bin` 에서 찾고, 빠진 도구는 까는 명령을 보여 줍니다.

## 사용자에게 보이는 변화

- README 「빠른 시작」 맨 앞에 리눅스 한 줄이 생겼습니다. 설치가 끝나면 할 일은 `dori.env` 네 줄, 리드 탭 `/login`, "Dori mode" 셋입니다.

## 깨지는 변경·운영자 할 일

- 없음. 루프를 유닛 하나씩(`dori-inbound.service` 등) 돌리는 기존 계정에는 `dori-serve.service` 를 만들지 않고, 이미 있는 `dori.env` 는 그대로 둡니다.
