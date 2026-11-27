# Android·iPhone 앱 구조와 자동 스캔 검토

- 작성일: 2026-11-27 (Asia/Seoul)
- 상태: **설계안·조사 기록, 구현 전·결정 전**. [기획서 v1.2](spec-v1.2.md)는 앱스토어 출시·멀티유저를 범위 밖으로 두고, 프로젝트 규칙은 게임 자동 조작을 금지한다. 출시 배경은 [market-research.md](market-research.md), iPhone 단독 앱 초안은 [iphone-offline-scan.md](iphone-offline-scan.md).
- 방법: 공식 문서·공개 자료 검색. 시제품은 만들지 않았다. 아래 "가능"은 문서상 가능하다는 뜻이며 실기기 확인 전이다.

## 1. "자동 스캔"의 두 가지 뜻과 결론

| 뜻 | Android | iPhone | 판단 |
| --- | --- | --- | --- |
| A. **앱이 게임 화면을 대신 넘김** (사용자는 손 놓고 박스 전체를 자동으로 훑음) | 기술적으로 가능(접근성 서비스의 제스처 보내기) | **불가능**. iOS 앱은 다른 앱에 터치를 보낼 수 없음 | **만들지 않는 것을 권장.** 게임 정지 위험, Google Play 정책, 프로젝트 규칙 모두 걸림(2장) |
| B. **사용자가 넘기는 동안 앱이 알아서 읽음** (녹화·업로드 없이, 다 읽으면 알림) | **가능.** 화면 캡처(MediaProjection) + 다른 앱 위에 표시(오버레이). Calcy IV·Poke Genie가 이 방식 | **제한적.** 화면 방송 확장(ReplayKit)은 메모리 약 50MB 한도·화면 표시 불가. 현실적인 경로는 "화면 기록 → 앱에서 고르면 전체 자동 처리" | **권장.** Mac의 `실시간 캡처`(체크리스트·소리 알림)를 휴대폰으로 옮기는 것 |

결론: 자동 스캔은 **B로 만든다.** Android는 게임 위에 체크리스트를 띄우는 실시간 판독, iPhone은 녹화 파일을 고르면 처음부터 끝까지 자동 처리하는 방식으로 시작한다. iPhone 실시간 판독은 작은 프레임(332×720)으로 확장 안 OCR이 메모리 안에 드는지 시제품으로 확인한 뒤 결정한다.

## 2. A(자동으로 넘기기) 상세 검토

2026-11-27 사용자 요청으로 A를 따로 검토했다. 결론은 그대로 **공개 앱에는 넣지 않는 것을 권장**이다. 이유가 1차 검토 때보다 분명해졌다(2.1).

**결정(2026-11-27, 사용자): 자동 넘기기는 넣지 않는다.** 개인용 실험도 하지 않는다. 자동 스캔은 B(넘기는 동안 자동 판독)로 간다.

### 2.1 게임 약관 (2026-11-25 개정본 확인)

약관 운영 주체는 이제 Scopely(구 Niantic)다. 약관 주소 `nianticlabs.com/terms`는 `explore.scopely.com/terms`로 넘어가며, 한국어 약관의 최종 수정일은 2026-11-25다. A와 관련된 조항(짧게 인용):

| 조항 | 내용 | A에 대한 의미 |
| --- | --- | --- |
| 6장 행동 규범 | 표준 브라우저 외의 수단, 명시적으로 "자동화 소프트웨어, 봇(bots), 스파이더(spiders), 크롤러(crawlers), 데이터 마이닝 툴" 등으로 서비스·콘텐츠에 접근·조회하는 행위 금지 | 자동으로 화면을 넘기며 정보를 모으는 것이 이 문구에 그대로 걸린다 |
| 6장 행동 규범 | "서비스 또는 콘텐츠(사용자 또는 게임플레이 관련 정보 포함)를 발췌, 스크랩 또는 색인하는 행위" 금지 | 자동 넘기기와 결합하면 "스크랩"으로 보일 여지가 더 크다. (B와 이 앱 전체에도 해당될 수 있는 문구다. 2.5) |
| 3.1 부정행위 | "수정되거나 비공식적인 제3자 소프트웨어의 사용"을 포함한 무단 접근 금지. 사용자는 해킹·무단 소프트웨어 확인을 위한 기기 검사에 동의 | 게임이 기기에서 켜진 접근성 서비스·자동 입력 도구를 감지할 수 있다는 뜻으로 읽힌다(실제 감지 여부는 확인 못 함) |
| 3.5 / 1장 | 위반·부정행위 의심 시 사전 통보 없이 정지·해지 가능 | |
| 3진 아웃 정책 | 1차 경고(약 7일, 희귀 포켓몬 미출현 등), 2차 약 30일 정지, 3차 영구 정지. 일부 부정행위는 경고 없이 즉시 정지 | 사용자 계정이 위험을 진다 |

과거 집행 사례는 위치 조작·봇·로그인 방식 도구에 집중됐고, 화면만 읽는 도구(Calcy IV·Poke Genie)는 수년간 공개적으로 운영되고 있다. 자동 입력은 그 "화면만 읽는" 범주를 벗어난다.

### 2.2 기술 경로

| 경로 | 플랫폼 | 방법 | 문제 |
| --- | --- | --- | --- |
| 앱 안 접근성 서비스 | Android | `AccessibilityService`의 제스처 보내기로 다음 포켓몬 스와이프, 기술 화면 스크롤, 조사 버튼 탭 | Google Play 정책상 "자동화 도구"는 접근성 도구가 아니므로 고지·동의·Play Console 신고 필요, 거절 사례 다수. 게임의 기기 검사에 걸릴 수 있음 |
| ADB·Shizuku 입력 | Android | 개발자 옵션·무선 디버깅으로 `input swipe` 실행 | 초보자 대상 앱에 맞지 않는 설정 난도. 약관 문제는 같음 |
| Mac + iPhone 미러링 | iPhone(Mac 필요) | Mac이 iPhone 미러링 창에 마우스 끌기 이벤트를 보내고, 지금의 실시간 캡처로 읽음 | Mac 손쉬운 사용 권한 필요. 미러링 창이 합성 이벤트를 받는지, Mac App Store 샌드박스에서 되는지 미확인. Mac 사용자만 가능. 약관 문제는 같음 |
| 앱 안에서 iPhone 조작 | iPhone | 없음 | iOS 앱은 다른 앱에 입력을 보낼 수 없음 |
| OS 기본 보조 기능 | 둘 다 | iPhone 스위치 제어·AssistiveTouch 사용자 제스처, Android 자동 클릭 앱 | 앱이 아니라 사용자가 직접 설정하는 것. 앱이 사용법을 안내하면 사실상 자동 조작을 권하는 셈이라 안내하지 않음 |

### 2.3 판단

- 공개 앱: 넣지 않는다. 약관 6장이 자동화 소프트웨어·봇을 명시적으로 금지하고, 피해(계정 정지)는 앱이 아니라 사용자가 진다. 초보자 대상 앱에서 특히 맞지 않는다. 스토어 심사(Google Play 접근성 정책) 위험도 있다.
- 개인 사용 실험(Mac + iPhone 미러링): 기술적으로 시도해 볼 수는 있으나 약관 위반 소지는 같고 CLAUDE.md·기획서의 자동 조작 금지를 바꿔야 한다. 사용자 본인 계정 위험을 감수하는 명시적 결정이 있을 때만 다룬다.

### 2.4 자동 넘기기 없이 손을 덜 쓰는 방법

| 방법 | 내용 |
| --- | --- |
| 바뀐 개체만 다시 스캔 | 앱이 게임 검색어를 만들어 준다(예: 최근 며칠 안에 잡은 포켓몬, 박스에 없는 종). 사용자는 그 결과만 넘긴다. 검색어 문법은 게임 화면에서 확인 필요 |
| 빠르게 넘겨도 되는 판독 | 지금처럼 화면마다 1~2초씩 넘기면 되고, 빠진 화면만 알림(B의 체크리스트) |
| 한 마리당 필요한 화면 줄이기 | 이미 박스에 있는 개체는 별명 관리 번호로 찾아 CP·기술만 확인하고 IV 조사 화면은 건너뜀(IV는 바뀌지 않음) |

### 2.5 B와 앱 전체에 대한 참고

6장의 "게임플레이 관련 정보를 발췌, 스크랩" 금지 문구는 화면을 읽어 정보를 모으는 이 앱 자체에도 적용될 수 있다. 화면만 읽는 경쟁 앱이 공개 운영 중이고 알려진 집행은 없지만, 공개 출시 전에 이 문구의 위험을 [market-research.md](market-research.md) 6장과 함께 검토한다.

## 3. B(넘기는 동안 자동 판독) 플랫폼별 방법

### 3.1 Android

- **화면 캡처**: MediaProjection. 캡처 세션마다 사용자 동의 창이 뜬다(Android 14부터 동의 토큰 재사용 불가). `mediaProjection` 형식의 포그라운드 서비스와 권한을 선언해야 한다. Android 14부터 화면 전체 대신 앱 하나만 공유하는 선택지가 있어, 사용자가 포켓몬 GO만 고르게 안내할 수 있다.
- **게임 위 표시**: "다른 앱 위에 표시" 권한으로 작은 창에 체크리스트(상세·기술·조사·맥스기술)와 다 읽음 표시, 소리 알림을 띄운다. Mac 실시간 캡처와 같은 흐름.
- **OCR**: Google ML Kit 글자 인식 v2 한국어 모델(기기 안). 글자당 16×16픽셀 이상 권장.
- **녹화 파일 처리**: 화면 녹화 파일을 고르면 iPhone과 같은 일괄 처리.

### 3.2 iPhone

- **기본 경로**: 기본 화면 기록 → 앱에서 고르기(PhotosPicker) → 디코딩(AVAssetReader) → 1초 프레임 OCR + 24fps IV 막대 판독 → 판독 후보. 사용자가 녹화만 하면 나머지는 자동이다. [iphone-offline-scan.md](iphone-offline-scan.md)의 설계와 같고, 차이는 판독 규칙까지 앱 안에서 돈다는 점이다(공통 코어, 4장).
- **실시간(시제품 후 결정)**: ReplayKit 방송 확장. 약 50MB 메모리 한도와 확장에 화면이 없다는 제약 때문에 [iphone-offline-scan.md 8장](iphone-offline-scan.md)에서 채택하지 않았다. 확장 안에서 프레임을 332×720(약 1MB)으로 바로 줄이고 빠른 OCR만 돌리면 한도 안에 들 가능성은 있다. 알림은 소리·진동·알림 배너 정도로만 가능하다.
- **OCR**: Apple Vision(지금 Mac과 같은 설정) 또는 ML Kit iOS 한국어 모델(4.3).

## 4. 공통 코어와 플랫폼 모듈 분리

### 4.1 나누는 기준

화면·기기에 닿는 것은 플랫폼 모듈, 숫자와 규칙은 공통 코어에 둔다. 경계는 **이미 있는 데이터 형식**으로 정한다.

- OCR 결과: `{width, height, lines: [{text, confidence, box: [x, y, w, h]}]}` (지금 `vision_ocr.swift` 출력과 `ocr.jsonl` 형식)
- IV 판독 입력: 332×720로 줄인 프레임의 픽셀 배열과 시각
- 그 밖에는 코어가 정의한 데이터 구조(개체, 박스, 보스, 추천 결과)

### 4.2 모듈 표

| 모듈 | 공통 / 플랫폼 | 담는 것 | 지금 Python 파일 |
| --- | --- | --- | --- |
| `core-reference` | 공통 | 기준 데이터 읽기·검증·버전(종족값, CPM, 기술, 강화 비용, 한국어 이름) | `reference.py`, `species.py`, `forms.py` |
| `core-recognition` | 공통 | OCR 줄 → 화면 구분·값 추출·개체 묶기, 기술 대조, IV 막대 판정(픽셀 배열 입력) | `fields.py`, `analysis.py`의 규칙 부분, `moves.py`, `ivbars.py`의 판정 부분 |
| `core-box` | 공통 | 레벨 계산, 저장 값 검증, 박스·이력·관리 번호, 사탕 기록, 백업 형식 | `level.py`, `review.py`, `box.py`, `backup.py` |
| `core-battle` | 공통 | 레이드·맥스배틀 엔진, 보스 목록 매핑, 육성 추천 엔진(새로) | `raid.py`, `max_battle.py`, `raid_bosses.py`의 매핑 부분 |
| `core-guide` | 공통(인터페이스) | AI 가이드에 넘길 엔진 결과 구조, AI 문장의 숫자·이름 검증 | 없음(새로) |
| `platform-capture` | 플랫폼 | iPhone: 사진 앱 영상 디코딩, (선택) ReplayKit. Android: MediaProjection 실시간, 영상 디코딩 | `video.py`, `live.py`, `window_capture.swift` 역할 |
| `platform-ocr` | 플랫폼 | iPhone: Apple Vision 또는 ML Kit. Android: ML Kit | `ocr.py`, `vision_ocr.swift` 역할 |
| `platform-ai` | 플랫폼 | iPhone: Apple Foundation Models. Android: Gemini Nano(지원 기기·한국어 미확인). 또는 공통 외부 API | 없음(결정 전, market-research.md 5장) |
| `platform-ui` | 플랫폼 | iPhone: SwiftUI. Android: Jetpack Compose. Android 오버레이 체크리스트 | `web/` 역할 |
| `platform-storage` | 공통 가능 | SQLite 박스 DB(공통 라이브러리로 두 플랫폼 같은 스키마) | `store.py`, `box.py`의 DB 부분 |

### 4.3 OCR을 하나로 할지

판독 규칙(`fields.py`)은 Apple Vision 결과의 줄 나눔·상자 위치에 맞춰져 있다. 플랫폼마다 다른 OCR을 쓰면 같은 화면에서 줄이 다르게 나뉘어 공통 규칙이 다르게 동작할 수 있다.

| 선택지 | 장점 | 단점 |
| --- | --- | --- |
| 양쪽 ML Kit | 같은 엔진이라 공통 규칙이 같게 동작. ML Kit은 iOS 한국어 모델도 제공 | 지금 규칙을 ML Kit 결과로 다시 맞춰야 함. 정확도 비교 전 |
| iPhone Vision, Android ML Kit | iPhone은 지금 규칙을 그대로 씀 | 규칙이 두 OCR 모두에서 맞는지 계속 확인해야 함 |

결정 방법: 같은 실제 녹화 프레임을 Vision과 ML Kit으로 읽어 판독 후보 차이를 비교한 뒤 정한다.

### 4.4 공통 코어를 무엇으로 만들지

Python은 휴대폰 앱에 넣기 무겁다. 후보:

| 선택지 | 장점 | 단점 |
| --- | --- | --- |
| **Kotlin Multiplatform (권장)** | Google이 공식 지원하는 공통 로직 방식. Android는 그대로, iPhone은 프레임워크로 가져다 씀. 화면은 SwiftUI·Compose로 각자 | iPhone에서 Objective-C 헤더를 거쳐 쓰므로 API를 단순하게 유지해야 함(Swift 직접 내보내기는 안정 여부 확인 필요) |
| Rust + UniFFI | 성능, 두 플랫폼 모두 바인딩 | 이 프로젝트에 새 언어. 빌드 설정 부담 |
| Compose Multiplatform으로 화면까지 공통 | 화면 코드 한 벌(iOS용 2025-05 안정) | 오버레이·Live Activity 등 플랫폼 기능은 어차피 따로 |

### 4.5 Mac Python 버전의 역할

- 지금 코드는 **기준 구현**으로 남긴다. 규칙을 Kotlin으로 옮길 때 같은 입력에 같은 결과가 나오는지 비교하는 기준이다.
- 저장소에 **합성 골든 데이터**(OCR JSON 입력 → 기대 판독 후보, 박스 값 → 기대 레벨·추천)를 두고 Python 테스트와 Kotlin 테스트가 같은 파일을 읽는다. 실제 사용자 영상·프레임은 지금처럼 넣지 않는다.
- 규칙을 고칠 때 두 구현을 같이 고치는 부담이 생긴다. 이식이 끝나면 Mac 버전도 공통 코어를 쓰게 할지(Kotlin/JVM 또는 Mac용 앱) 따로 정한다.

## 5. 작업 순서(안)

1. **골든 데이터 만들기**(이 저장소, 합성): 지금 테스트의 합성 OCR 입력과 기대 결과를 언어에 상관없는 JSON으로 정리.
2. **OCR 비교**: 같은 실제 녹화 프레임을 Apple Vision과 ML Kit으로 읽어 판독 후보 차이 기록(로컬 데이터, 결과 수치만 문서화).
3. **공통 코어 이식**: `core-reference` → `core-recognition` → `core-box` → `core-battle` 순서. 단계마다 골든 데이터 통과.
4. **Android 시제품**: MediaProjection + 오버레이 체크리스트 + ML Kit. 실기기에서 판독 지연·발열·배터리 측정.
5. **iPhone 시제품**: 녹화 파일 자동 처리. 이어서 ReplayKit 확장의 메모리 실측.
6. 비공개 테스트(Google Play는 개인 계정이면 12명·14일 필요, TestFlight).

## 6. 필요한 결정

| 항목 | 선택지 |
| --- | --- |
| 자동 스캔 범위 | B만(권장) / A도 검토 |
| 공통 코어 언어 | Kotlin Multiplatform(권장) / Rust / 기타 |
| OCR | 양쪽 ML Kit / iPhone Vision + Android ML Kit (비교 후) |
| 화면 | 플랫폼별 네이티브 / Compose Multiplatform |
| 기획서 | 앱스토어 출시·멀티플랫폼을 범위에 넣는 개정 |

## 출처

- [Google Play 접근성 서비스 정책](https://support.google.com/googleplay/android-developer/answer/10964491?hl=en)
- [XDA — 접근성 서비스 사용 제한](https://www.xda-developers.com/google-trying-limit-apps-accessibility-service/)
- [Android — Media projection](https://developer.android.com/media/grow/media-projection)
- [Android — MediaProjectionManager](https://developer.android.com/reference/android/media/projection/MediaProjectionManager)
- [Android — 포그라운드 서비스 형식](https://developer.android.com/guide/components/fg-service-types)
- [ML Kit 글자 인식 v2](https://developers.google.com/ml-kit/vision/text-recognition/v2?hl=ko)
- [ML Kit Android 한국어](https://developers.google.com/ml-kit/vision/text-recognition/v2/android?hl=ko)
- [ML Kit iOS 한국어](https://developers.google.com/ml-kit/vision/text-recognition/v2/ios?hl=ko)
- [Kotlin Multiplatform for iOS](https://kotlinlang.org/docs/multiplatform/kmp-for-ios.html)
- [Android — Kotlin Multiplatform](https://developer.android.com/kmp)
- [JetBrains — Compose Multiplatform 1.8.0 iOS 안정](https://blog.jetbrains.com/kotlin/2025/05/compose-multiplatform-1-8-0-released-compose-multiplatform-for-ios-is-stable-and-production-ready/)
- [Calcy IV 안내(제3자)](https://www.pokeep.com/pokemon-go/calcy-iv.html)
- [Poke Genie 안내(제3자)](https://anyto.imyfone.com/pokemon-go/pokegenie/)
- [TechCrunch — Niantic 정지 기준(2018)](https://techcrunch.com/2018/07/20/niantic-explains-how-and-why-it-bans-players-in-pokemon-go)
- [Scopely Explore 서비스 약관(2026-11-25 수정)](https://explore.scopely.com/terms)
- [Scopely Explore 플레이어 가이드라인](https://explore.scopely.com/guidelines)
- [플레이어 가이드라인 적용](https://explore.scopely.com/safety/player-guidelines)
- [게임 플레이 공정성 정책(3진 아웃)](https://scopelyexplore.helpshift.com/hc/ko/5-pokemon-go/faq/1305-gameplay-fairness-policy/)
- [GamesRadar — 자동 클릭 앱 사례](https://www.gamesradar.com/pokemon-go-cheats-are-preventing-players-from-beating-gyms)
- ReplayKit 메모리 한도: [iphone-offline-scan.md 8장](iphone-offline-scan.md)의 출처
