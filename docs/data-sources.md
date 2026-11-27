# 외부 자료 출처

이 앱이 인터넷에서 받아 오는 자료를 한곳에 모았습니다. Mac에서 처음 설치하거나 자료를 갱신할 때, 무엇을 어디서 받아 어디에 두는지 이 문서만 보면 알 수 있게 했습니다.

## 한눈에 보기

| 자료 | 받는 곳 | 받는 때 | 저장 위치 | Git에 넣는가 |
| --- | --- | --- | --- | --- |
| 기준 데이터(종족값·CP 배율·기술·한국어 이름) | alexelgt/game_masters, PoGo API, PokeAPI | `reference update` 명령 | `data/reference/<버전>/` | 아니요 |
| 현재 레이드·맥스배틀 보스 | pokemon-go-api | `레이드·이벤트`의 `보스 목록·이벤트 갱신`, 또는 `raids update` | `data/raid-bosses/<UTC 시각>-pokemon-go-api/` | 아니요 |
| 이벤트 달력 | ScrapedDuck(LeekDuck 정리) | 위와 같은 버튼·명령(보스 목록과 함께) | `data/events/<UTC 시각>/` | 아니요 |
| 포켓몬 이미지 | Pokémon Showdown 픽셀 스프라이트 | `내 박스`나 `레이드·이벤트`의 `포켓몬 이미지 받기` | `data/sprites/` | 아니요(개인 사용만) |

인터넷 접속은 위 네 곳뿐이고 모두 사용자가 명령을 실행하거나 버튼을 누를 때만 합니다. 코드에서는 `boxscan/reference.py`, `boxscan/raid_bosses.py`, `boxscan/events.py`, `boxscan/sprites.py`의 `urlopen` 호출 네 곳이 전부입니다. 영상 처리, OCR(Apple Vision), 레벨 계산, 레이드 추천, 화면 표시는 받은 자료만 쓰고 외부에 접속하지 않습니다. 웹 페이지도 외부 글꼴·스크립트·이미지를 불러오지 않습니다.

외부로 보내는 것은 받을 파일의 주소뿐입니다. 박스 값, 영상, 판독 결과, 별명은 보내지 않습니다. 포켓몬 이미지를 받을 때는 어떤 포켓몬 이미지를 요청했는지가 출처 사이트에 보입니다.

`data/` 폴더 전체는 `.gitignore`에 들어 있어 커밋되지 않습니다.

## Mac에서 한 번에 받기

```sh
uv run --locked python -m boxscan reference update   # 기준 데이터, 약 20MB
uv run --locked python -m boxscan raids update       # 보스 목록과 이벤트 달력
uv run --locked python -m boxscan serve
```

포켓몬 이미지는 명령이 없고 화면에서 받습니다. 박스를 저장하고 보스 목록을 받은 뒤 `내 박스`나 `레이드·이벤트`에서 `포켓몬 이미지 받기`를 누릅니다. 박스에 있는 포켓몬과 보스 목록·이벤트 달력의 보스만 받으므로, 박스가 늘면 다시 누르면 됩니다(이미 받은 파일은 다시 받지 않습니다).

설치 단계의 `brew install`, `git clone`, `uv sync`는 Homebrew·GitHub·PyPI에서 도구와 패키지를 받습니다. 앱이 실행 중에 접속하는 곳은 아닙니다.

## 자료별 상세

### 기준 데이터

| 파일 | 주소 | 쓰는 곳 |
| --- | --- | --- |
| `game_master.json` | https://raw.githubusercontent.com/alexelgt/game_masters/master/GAME_MASTER.json | 종족값, 정수 레벨 CP 배율, 기술 위력·에너지, 배울 수 있는 기술, 메가·원시회귀, 타입 상성, 회피 설정 |
| `pokemon_stats.json` | https://pogoapi.net/api/v1/pokemon_stats.json | 게임 마스터에 없는 종의 종족값, 교차 검사 |
| `cp_multiplier.json` | https://pogoapi.net/api/v1/cp_multiplier.json | CP 배율 교차 검사 |
| `pokemon_species_names.csv` | https://raw.githubusercontent.com/PokeAPI/pokeapi/master/data/v2/csv/pokemon_species_names.csv | 한국어·영어 종 이름 ↔ 도감 번호 |
| `move_names.csv` | https://raw.githubusercontent.com/PokeAPI/pokeapi/master/data/v2/csv/move_names.csv | 한국어 기술 이름 |

- 받은 원본과 정리한 `reference.json`, 받은 시각·주소·SHA-256이 담긴 `manifest.json`을 버전 폴더에 두고 `data/reference/current`가 쓰는 버전을 가리킵니다. 새 자료가 검사를 통과하지 못하면 이전 버전을 계속 씁니다.
- 앱을 업데이트한 뒤에는 내려받지 않고 정리 파일만 다시 만드는 `reference rebuild`로 충분한 경우가 많습니다(메가 설정, 회피 설정처럼 새로 읽는 값이 생겼을 때).
- 게임 마스터는 게임 파일에서 추출한 비공식 자료이고 저장소에는 교육용이라고만 적혀 있습니다. PokeMiners 게임 마스터가 2026-08-29 이후 갱신되지 않아 2026-11-15에 alexelgt로 바꿨습니다.
- 재배포 조건은 확인하지 않았습니다. 그래서 저장소에 넣지 않고 사용자가 직접 받게 합니다.

### 현재 레이드·맥스배틀 보스

| 파일 | 주소 |
| --- | --- |
| `raidboss.json` | https://pokemon-go-api.github.io/pokemon-go-api/api/raidboss.json |
| `maxbattles.json` | https://pokemon-go-api.github.io/pokemon-go-api/api/maxbattles.json |

- 출처: [pokemon-go-api](https://github.com/pokemon-go-api/pokemon-go-api). LeekDuck 레이드 보스 페이지와 Snacknap 맥스배틀 목록을 다시 정리한 자료이며, 하루 약 8번 갱신됩니다.
- 최근 20개 버전을 남기고, 실패하면 이전 목록을 씁니다. 다른 공급원과의 비교는 README의 `레이드·이벤트`에 있습니다.
- 재배포·상업 이용 조건은 확인하지 않았습니다.

### 이벤트 달력

| 파일 | 주소 |
| --- | --- |
| `events.json` | https://raw.githubusercontent.com/bigfoott/ScrapedDuck/data/events.json |

- 출처: [ScrapedDuck](https://github.com/bigfoott/ScrapedDuck). LeekDuck 이벤트 목록을 LeekDuck의 허락을 받아 정리한 자료입니다.
- 이용 조건: ScrapedDuck·LeekDuck 출처 표시, 유료화·광고 수익 금지. 화면 아래에 출처를 표시합니다. 개인 사용에는 문제가 없지만 유료 앱에는 쓸 수 없습니다.
- 최근 10개 버전을 남기고, 실패하면 이전 달력을 씁니다. 달력 항목을 누르면 LeekDuck 안내 페이지가 브라우저 새 탭으로 열립니다(사용자가 누를 때만).

### 포켓몬 이미지

| 종류 | 주소 형식 | 저장 이름 |
| --- | --- | --- |
| 기본 | `https://play.pokemonshowdown.com/sprites/gen5/<이름>.png` | `<도감 번호>.png` |
| 모습·메가 | `.../gen5/typhlosion-hisui.png`, `.../gen5/charizard-megax.png` | `157-hisui.png`, `6-megax.png` |
| 이로치 | `https://play.pokemonshowdown.com/sprites/gen5-shiny/<이름>.png` | `<도감 번호>[-모습].s.png` |

- 출처: [Pokémon Showdown sprites](https://play.pokemonshowdown.com/sprites/)의 5세대 스타일 픽셀 스프라이트(96×96, 배경 투명, 장당 약 1KB).
- 이름은 기준 데이터의 영어 종 이름(글자만, 소문자)과 모습으로 만듭니다. 지역 모습은 `alola`·`galar`·`hisui`·`paldea`, 메가는 `mega`·`megax`·`megay`, 원시회귀는 `primal`, 나머지 폼은 게임 데이터 모습 이름(`therian` 등)을 씁니다. 기본 모습(`Normal`, 화신폼 등)은 모습 없이 받습니다.
- 출처에 없는 파일은 `<이름>.png.none`으로 기억해 다시 요청하지 않고, 기본 모습 이미지를 대신 보여 줍니다. 받기 전에는 이름 첫 글자를 흐리게 보여 줍니다.
- `data/sprites/manifest.json`에 출처와 받은 시각, 파일 수를 남깁니다.
- 게임 회사의 저작물입니다. 내 컴퓨터에서 개인적으로 보는 용도로만 받고 저장소에 넣거나 다시 배포하지 않습니다. 공개 출시 때는 쓸 수 없습니다. 이용 조건은 따로 확인하지 않았습니다.
- 2026-11-27 Windows에서 합성 데이터와 가짜 내려받기로만 확인했고, Mac에서 실제로 받아 보지는 않았습니다.

## 다시 받기와 지우기

| 하고 싶은 일 | 방법 |
| --- | --- |
| 기준 데이터 갱신 | `reference update` (이전 버전도 남고, `저장 공간 정리`에서 최근 두 버전만 남김) |
| 보스 목록·이벤트 갱신 | 버튼 또는 `raids update` |
| 포켓몬 이미지를 처음부터 다시 받기 | `data/sprites/` 폴더를 지우고 `포켓몬 이미지 받기` |
| 출처에 없다고 기억한 이미지만 다시 시도 | `data/sprites/`의 `*.none` 파일만 지우기 |
| 받은 자료가 언제 어디서 왔는지 확인 | 각 폴더의 `manifest.json` |

## 계산에 쓰지만 받지 않는 값

레이드·맥스배틀 등급표(보스 HP, CP 배율), 그림자 레이드 격앙 수치처럼 게임 마스터에 없는 값은 공개 자료를 보고 코드에 적었습니다. 참고한 페이지와 확인 날짜는 README의 `레이드 추천`, `맥스배틀 추천`과 `boxscan/raid.py`·`boxscan/max_battle.py` 등급표의 `sources`와 `raid.py`의 `SHADOW_SOURCES`에 있습니다. 앱이 이 페이지에 접속하지는 않습니다.
