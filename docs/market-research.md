# 공개 출시 검토 — 시장조사

- 작성일: 2026-11-27 (Asia/Seoul)
- 상태: **조사 기록**. 출시 결정 아님. [기획서 v1.2](spec-v1.2.md)는 여전히 공개 서버·앱스토어 출시·육성 가정 추천·외부 AI를 범위 밖으로 둔다. 아래 제안을 진행하려면 기획서 개정이 먼저 필요하다.
- 방법: 공개 웹 검색과 App Store 페이지 확인. 경쟁 앱을 직접 설치해 써 보지는 않았다. 매출·다운로드 수치는 대부분 제3자 추정치다.

## 1. 요약

- 시장은 크다. 포켓몬 GO는 주간 이용자 약 2천만 명(2025-03), 2025년 매출 10억 달러 이상(Scopely 발표)이다.
- "화면을 읽어 내 박스를 만들고 그 박스로 레이드 파티를 추천"하는 기능은 이미 있다. Calcy IV, Poke Genie, Pokebattler가 각자 방식으로 제공한다.
- 남는 차별점 후보는 **내 박스 기준 맥스배틀 추천**, **한국어 판독 정확도**, **영상 한 번으로 박스 전체 스캔**, **전투 시간을 반영한 예상 누적 피해 모델**, **기기 안에서만 처리**, 그리고 **초보자용 육성 안내**(5장: 실제 보유 사탕·별의모래 기준으로 강화·진화·기술머신·두 번째 스페셜어택 중 다음에 할 일을 알려 줌)다.
- 현재 구조(Mac + Python + ffmpeg + Xcode 명령줄 도구)로는 일반 사용자에게 배포할 수 없다. 출시하려면 iPhone 앱이 필요하다.
- 출시 전에 반드시 풀어야 할 문제는 기준 데이터 재배포 조건이다(6장).

## 2. 시장 규모

| 항목 | 값 | 출처 성격 |
| --- | --- | --- |
| 주간 이용자 | 약 2천만 명, 일간 약 1천만 명 (2025-03) | Niantic 발표 |
| 2025년 매출 | 10억 달러 이상, 앱스토어 몫 약 7.45억 달러 | Scopely 발표 / AppMagic 추정 |
| 2026년 7월 매출 | 약 1.415억 달러 (10주년 행사) | AppMagic 추정 |
| 누적 이용자 | 8억 명 이상 | Scopely 발표 |
| 운영사 | 2025-03 Scopely가 Niantic 게임 부문을 35억 달러에 인수 | 보도 |
| 한국 이용자 | **최근 공개 자료 없음.** 2017-07 약 134만 명(와이즈앱)이 마지막으로 찾은 수치 | 확인 못 함 |

## 3. 경쟁 앱

| 앱 | 하는 일 | 규모·가격 (확인 시점) |
| --- | --- | --- |
| Poke Genie | 이미지 인식 IV 계산, PvP IV, 배틀 시뮬레이터, 원격 레이드 자동 매칭, 다이맥스·거다이맥스 원격 매칭. 한국어 포함 10개 언어 | 한국 App Store 4.7점·평가 3만 개 이상, 최신 8.20.0(2026-11 말). 인앱 결제 ₩4,000~₩75,000(Raid VIP ₩4,000, Raid Ultra ₩28,000, Raid Master ₩75,000 등) |
| Calcy IV (iOS) | 화면 녹화로 IV·DPS·PvP 순위를 게임 위 알림으로 표시. "보유 포켓몬 전체에서 레이드 최적 팀" 계산. 다이맥스·거다이맥스 지원 기록 있음 | 무료, Pro 월 $1.49, 배틀 시뮬레이션 $3.99. iOS 평가 263개(4.5점) |
| Pokebattler | 레이드 카운터 시뮬레이션. 구독자는 내 Pokebox 기준 맞춤 카운터. **Raid Advice**: 사용자가 정한 별의모래·사탕 한도 안에서 어떤 개체를 강화할지 추천. Poke Genie 가져오기 지원 | 구독 가격 페이지가 막혀(403) 확인 못 함 |
| GO Hub DB, PokemonDB 등 | 맥스배틀·레이드 보스별 일반 카운터 순위 | 무료 웹, 내 박스 기준 아님 |

매출 구조 참고: 한 추정(DianDian)에 따르면 Poke Genie 매출의 약 60%가 원격 레이드 매칭 구독(Raid Ultra 계열)에서 나온다. 분석 기능만 파는 앱은 Calcy IV처럼 월 $1~4 수준이다. 큰 매출을 기대하기보다 작은 유료 앱으로 보는 편이 현실적이다.

확인하지 못한 것: Poke Genie·Calcy IV가 **내 박스 기준 맥스배틀 파티**를 추천하는지, Pokebattler 구독 가격과 맥스배틀 시뮬레이션 지원 여부.

## 4. 이 앱의 차별점 후보

| 후보 | 근거 | 경쟁 앱 대비 상태 |
| --- | --- | --- |
| 내 박스 기준 맥스배틀 3마리 순서·맥스기술 계획 | `boxscan/max_battle.py` 구현됨 | 경쟁 앱 지원 여부 미확인. 가장 유력하지만 직접 비교 필요 |
| 한국어 판독 | 한국어 기술 이름 대조, 별명 관리 번호, 사탕 라벨 | Poke Genie도 한국어 지원. 정확도 비교는 안 함 |
| 영상 한 번으로 박스 전체 스캔 | 녹화 업로드·실시간 캡처 | Calcy IV도 화면 녹화 방식. 일괄 처리·검토 흐름은 다름 |
| 전투 시간 반영 예상 누적 피해 | 기획서 2장 | 경쟁 앱은 대체로 DPS·TDO 시뮬레이션. 차이를 사용자가 체감할지 미확인 |
| 기기 안에서만 처리, 로그인 없음 | 기획서 1장 | 경쟁 앱도 대부분 로그인 없음. 원격 레이드 앱은 서버 사용 |
| 초보자용 육성 안내(강화·진화·기술머신·두 번째 스페셜어택) | 5장 | Pokebattler Raid Advice와 일부 겹침. 보유 자원을 화면에서 읽고 할 일 목록으로 보여 준다는 점이 다름 |

## 5. 육성(키울 개체) 추천 검토

사용자 제안(2026-11-27): "AI를 이용한 키울 개체 추천"을 차별점으로 넣는 안. 대상은 **초보자**다. 초보자는 사탕을 어디에 써야 하는지(강화·진화·두 번째 스페셜어택 해방), 기술머신으로 어떤 기술을 맞춰야 하는지, 박스에서 무엇을 키워야 하는지 모른다. 경쟁 앱은 IV 숫자나 시뮬레이션 표를 보여 주지만, "지금 내 박스에서 다음에 할 일 하나"를 알려 주지는 않는다.

**초보자에게 보여 줄 형태(안).** 숫자표보다 할 일 목록으로 보여 준다.

- "망나뇽01: 일반 기술머신으로 스페셜어택을 `역린`으로 바꾸세요. 현재 레이드 3종에서 예상 피해 +18%" (예시 문장, 수치는 가상)
- "마기라스02: 사탕 100개와 별의모래 10만으로 두 번째 스페셜어택을 여세요"
- "애버라스 사탕은 마기라스01 강화보다 마기라스02 진화에 쓰는 편이 피해가 더 늘어납니다"
- 각 항목에 근거(어느 보스 기준인지, 비용, 늘어나는 예상 피해)와 "왜 이걸 먼저 하는지"를 한두 문장으로 붙인다.

**추천할 행동 종류.**

| 행동 | 필요한 자료 | 지금 있는가 |
| --- | --- | --- |
| 강화(레벨 올리기) | 레벨별 별의모래·사탕·사탕XL 비용, 보유 자원 | 비용 표와 보유 사탕·별의모래 읽기 있음 |
| 진화 | 진화 계열·진화 사탕 비용, 진화 후 종족값 | 게임 마스터에 있음. 진화 후 기술은 무작위라 기대값으로 계산해야 함 |
| 두 번째 스페셜어택 해방 | 종별 해방 사탕·별의모래 비용 | 게임 마스터에 있는지 확인 필요 |
| 일반 기술머신 | 종이 배울 수 있는 기술 목록(현재 기술 제외 중 무작위) | 기술 목록 있음. 원하는 기술이 나올 때까지 평균 몇 개 필요한지 계산 가능 |
| 대단한 기술머신 | elite 기술 목록 | 있음(`*` 표시에 사용 중). 보유 개수는 읽지 않음 |
| 무엇을 키울지 | 현재·예정 보스 목록, 레이드·맥스배틀 엔진 | 현재 보스 목록과 엔진 있음. 예정 보스 일정 출처는 없음 |

**초보자 대상일 때 주의할 점.**

- 트레이너 레벨 31 미만은 사탕XL이 화면에 나오지 않는다. 지금 판독은 XL이 안 보이면 0으로 기록하는데(README "사탕 기록"), 초보자 계정에서는 이 가정이 틀릴 수 있다. 트레이너 레벨을 입력받거나 읽어야 한다.
- 초보자 박스는 개체가 적고 레벨이 낮아 "아무것도 쓸 만하지 않음"이 정답일 때가 많다. 이때는 "지금 이 포켓몬을 레이드에서 잡아 두세요"처럼 박스 밖 조언이 필요한데, 이는 박스 기준 추천의 범위를 넘는다.
- 초보자는 유료 결제 의향이 낮을 수 있다. 수익 모델과 함께 검토해야 한다.

**이미 있는 것.** Pokebattler Raid Advice가 사용자가 입력한 별의모래·사탕 한도 안에서 레이드 보스별로 강화할 개체를 추천한다. 아이디어 자체는 새롭지 않다. 다만 Pokebattler는 박스를 손으로 입력하거나 다른 앱에서 가져와야 하고, 보유 자원도 사용자가 정한 한도를 쓴다.

**이 앱이 다르게 할 수 있는 것.**

- 박스와 함께 **실제 보유 사탕·사탕XL·별의모래를 화면에서 읽어 이미 저장한다**(README "사탕 기록"). "지금 가진 자원으로 실제로 할 수 있는 강화"만 추천할 수 있다.
- 다음 1회 강화 비용 표가 이미 기준 데이터에 있다(레벨 계산에 사용). 반짝반짝·그림자·정화 비용 보정도 들어 있다.
- 레이드·맥스배틀 엔진이 이미 있으므로 "이 개체를 레벨 X로 올리면 현재 보스 목록에서 예상 피해가 얼마 늘어나는가"를 같은 엔진으로 계산할 수 있다.
- 결과를 "별의모래 10만당 늘어나는 예상 피해"처럼 비용 대비 효과로 정렬할 수 있다. 같은 계열이 사탕을 나눠 쓰는 경우(진화 계열별 사탕)도 계산에 넣을 수 있다.

**AI 가이드 구성(안, 2026-11-27 사용자 방향).** 스캔한 박스로 "지금 박스에서 최선"을 알려 주고, 최종적으로 "무엇을 키워야 하는지" 가이드라인을 AI가 구성한다. 수치는 계산 엔진이 내고, AI는 그 결과를 초보자가 읽을 계획과 설명으로 엮는 역할로 나눈다.

| 층 | 하는 일 | 근거 |
| --- | --- | --- |
| 계산 엔진 (기존 + 추가) | 현재 최선 파티, 행동별(강화·진화·기술머신·두 번째 스페셜어택) 비용과 예상 피해 증가, 타입별 "최종 목표" 개체 순위 | 게임 마스터·보스 목록·박스 값. 미확인 값 생성 금지 유지 |
| AI | 엔진 결과를 우선순위가 있는 가이드라인으로 정리, "왜 이걸 먼저"를 설명, "사탕 어디 써요?" 같은 질문에 답 | 엔진 결과만 인용. 엔진 함수를 도구(tool calling)로 호출 |
| 검증 | AI 문장 속 숫자·종·기술 이름이 엔진 결과와 같은지 확인, 다르면 템플릿 문장으로 대체 | 구조화 출력(정해진 형식)으로 받아 확인 |

"최종 목표"는 AI의 기억(학습된 메타 지식)에 맡기지 않는다. 학습 시점 이후 바뀐 메타·신규 포켓몬을 모르고, 틀려도 그럴듯하게 말한다. 대신 엔진이 게임 마스터 전체 종·기술 조합을 표준 보스 집합에 돌려 타입별 상위 개체를 계산하고, 사용자 박스와의 차이("격투 타입 목표 X, 지금 가장 가까운 개체 Y, 필요한 사탕 Z")를 AI가 설명한다.

**AI 실행 위치 선택지.** 기획서와 프로젝트 규칙은 외부 AI 호출을 금지하므로 어느 쪽이든 기획서 개정이 필요하다(1번은 "외부" 금지는 유지 가능).

| 선택지 | 장점 | 단점 |
| --- | --- | --- |
| 1. Apple 기기 내 모델 (Foundation Models 프레임워크, iOS·macOS 26) | 무료, 박스 데이터가 기기 밖으로 나가지 않음, 구조화 출력·tool calling 지원, Apple Intelligence가 한국어 지원 | Apple Intelligence 지원 기기만, 작은 모델이라 일반 지식은 약함(엔진이 다 계산해 줘야 함), 한국어 품질은 실측 필요 |
| 2. 외부 API (Claude 등) | 문장·대화 품질이 높음 | 사용자당 호출 비용 → 구독 필요, 박스 데이터 외부 전송 → 동의·개인정보 처리방침 필요, 기획서 원칙 정면 변경 |

제안: 1번으로 시작한다. 엔진이 수치를 다 내므로 작은 모델로도 설명 역할은 충분할 가능성이 크고, "로그인·외부 전송 없음"이라는 기존 강점을 지킨다. 한국어 설명 품질이 부족하면 그때 2번을 선택 기능으로 검토한다.

**필요한 결정.**

1. 기획서 1장 "육성: 강화·진화·기술 변경을 앞으로 한다고 가정한 추천 제외"를 바꿀지. 바꾼다면 기존 파티 추천과 분리된 별도 화면으로 두어, 현재 상태 추천과 섞이지 않게 한다.
2. 추천 대상: 강화 / 진화 / 두 번째 스페셜어택 해방 / 기술머신. 초보자 대상이면 기술머신 안내가 핵심이다. 기술머신 결과는 무작위이고 보유 개수를 읽지 못하므로, "어떤 기술을 맞추면 얼마나 좋아지는지"와 "평균 몇 개가 드는지"까지만 보여 준다.
3. 평가 기준: 현재 레이드·맥스배틀 보스 목록만 / 앞으로 나올 보스까지(일정 자료 출처 필요). "최종 목표"용 표준 보스 집합을 어떻게 정할지.
4. AI 실행 위치(기기 내 모델 / 외부 API)와 그에 따른 기획서 1장 "AI" 항목 개정.

## 5-1. 플랫폼: Android와 iPhone

| 항목 | 값 | 출처 성격 |
| --- | --- | --- |
| 한국 스마트폰 | 2025년 갤럭시 72%·아이폰 24%. 2026년 갤럭시 81%·아이폰 19%(조사 방식이 전화→면접으로 바뀌어 직접 비교는 주의) | 한국갤럽 보도 |
| 한국 연령별(2025) | 18~29세 아이폰 60%, 30대 43%, 40대 31%, 50대 이상 갤럭시 82~92% | 한국갤럽 보도 |
| 포켓몬 GO 설치(전 세계) | Android 약 77%, iOS 약 23% | Sensor Tower 계열 추정 |
| 포켓몬 GO 결제(전 세계) | Google Play 약 53%, App Store 약 47% | Sensor Tower 계열 추정 |
| 한국 포켓몬 GO 이용자의 기기·연령 분포 | 찾지 못함 | — |

- 이용자 수는 Android가 3~4배 많다. 결제액은 거의 반반이라, 돈을 내는 사용자 기준으로는 iPhone도 절반 가까이 된다.
- 기술적으로 Android가 더 유리하다. 다른 앱 위에 겹쳐 그리기(오버레이)와 화면 캡처(MediaProjection)가 허용되어, 게임을 하면서 바로 판독하는 흐름을 만들 수 있다. iPhone은 녹화 후 불러오기나 화면 방송 확장(ReplayKit) 방식으로 제한된다.
- AI 실행 위치와 연결된다. 기기 내 모델은 플랫폼마다 다르다(iPhone은 Apple Foundation Models, Android는 일부 기기의 Gemini Nano. Android 쪽 지원 기기·한국어 여부는 확인하지 않음). 두 플랫폼을 다 하면 기기 내 AI를 두 번 구현해야 하고, 외부 API는 한 번이면 된다.
- 현재 코드(Apple Vision OCR, ScreenCaptureKit, swiftc)는 Apple 전용이다. Android를 지원하려면 OCR부터 바꿔야 한다.

## 6. 출시를 막는 것

| 문제 | 내용 | 해결 방향 |
| --- | --- | --- |
| 플랫폼 | 사용자가 Mac·터미널·Xcode 명령줄 도구를 써야 함 | iPhone 앱. 설계 초안은 [iphone-offline-scan.md](iphone-offline-scan.md). 판독·레벨·전투 엔진을 Swift로 옮기는 큰 작업 |
| 기준 데이터 재배포 | 비공식 게임 마스터(alexelgt, "교육용"으로만 표기), PokeAPI 이름 자료, pokemon-go-api 레이드 목록(LeekDuck·Snacknap 정리)의 재배포·상업 이용 조건 미확인 | 각 출처 조건 확인. 앱에 묶지 않고 사용자가 받게 하는 방식도 조건 확인 필요 |
| 상표·저작권 | 앱 이름의 "Pokémon", 공식 이미지·아이콘은 App Store 4.1(Copycats)·5.2(지식재산) 거절 사유가 될 수 있음 | 이름·아이콘에서 제외, 종 이미지 미사용, 비제휴 고지. Poke Genie가 라이선스 없이 운영되는 선례는 있으나 보장은 아님 |
| 게임 이용약관 | Niantic 정지 사례는 로그인·서버 접근·봇·위치 조작·변조 클라이언트. 화면만 읽는 도구는 위험이 가장 낮은 쪽이지만 허용이 명시된 적은 없음. 2026-11-25 개정 약관(Scopely Explore) 6장은 자동화 소프트웨어·봇 사용과 "게임플레이 관련 정보를 발췌, 스크랩 또는 색인하는 행위"를 금지한다. 후자는 화면을 읽는 이 앱에도 해당될 수 있는 문구다([mobile-architecture.md](mobile-architecture.md) 2장) | 현재 설계(입력·로그인·네트워크 전송 없음) 유지. 출시 전 이 문구의 위험을 따로 검토 |
| 판독 검증 범위 | 기기 1종·한국어로만 확인 | 다른 iPhone 해상도·언어 실측 |
| 기획서 | 출시·육성 추천·멀티유저가 범위 밖 | 사용자 결정 후 개정 |

## 7. 제안 순서

1. 기준 데이터 출처별 재배포·상업 이용 조건 확인. 막히면 출시 방식 자체가 바뀐다.
2. Calcy IV·Poke Genie를 실제로 써서 맥스배틀 추천과 한국어 판독 정확도를 이 앱과 비교. 차별점이 실제로 있는지 확인.
3. 육성 추천을 넣을지 결정하고(5장), 넣는다면 Mac 버전에서 먼저 구현해 본인 박스로 확인.
4. iPhone 앱 최소판을 TestFlight로 만들어 국내 커뮤니티에서 소규모 베타. 수집 시간·판독 수정 비율·재사용률을 측정.
5. 반응이 있으면 일회성 구매나 소액 구독으로 출시 검토.

## 출처

- [PocketGamer.biz — 주간 이용자 2천만 명](https://www.pocketgamer.biz/pokmon-go-has-20m-weekly-active-players-nearly-nine-years-after-its-global-launch)
- [PocketGamer.biz — 10주년, 누적 8억 명](https://www.pocketgamer.biz/pokemon-go-marks-10-years-with-800m-players-and-a-trillion-pokemon-caught/)
- [Outlook Respawn — 2026년 7월 매출](https://respawn.outlookindia.com/amp/story/gaming/gaming-news/pok%C3%A9mon-go-anniversary-month-drives-229-spending-surge)
- [Outlook Respawn — 누적 매출·이용자](https://respawn.outlookindia.com/gaming/gaming-news/pok%C3%A9mon-go-crosses-9b-in-mobile-revenue-at-its-10th-anniversary)
- [Hollywood Reporter — Scopely 인수](https://www.hollywoodreporter.com/business/digital/pokemon-go-studio-niantic-sold-scopely-1236161480/)
- [경향신문 — 2017년 국내 이용자](https://www.khan.co.kr/article/201708161400001)
- [SBS Biz — 2025 한국갤럽 연령별 스마트폰](https://biz.sbs.co.kr/amp/article/20000269528)
- [아시아경제 — 2026 갤럭시 81%](https://view.asiae.co.kr/article/2026071509161547507)
- [Cult of Mac — 포켓몬 GO iOS 다운로드·매출 비중](https://www.cultofmac.com/?p=746995)
- [Twinfinite — 플랫폼별 누적 매출](https://twinfinite.net/?p=510356)
- [Sensor Tower — 포켓몬 GO 매출](https://sensortower.com/blog/pokemon-go-160-million-dollars-usage-still-strong)
- [Poke Genie 한국 App Store](https://apps.apple.com/KR/app/id1143920524)
- [Calcy IV App Store](https://apps.apple.com/app/id1526882812)
- [DianDian — Poke Genie 매출 구성 추정](https://vip.diandian.com/news/m4KQ3JL6zvE7Ud4n5Zwa)
- [MeinMMO — Poke Genie 구독 도입(2022)](https://mein-mmo.de/en/pokemon-go-popular-raid-app-suddenly-offers-subscriptions-for-up-to-e60-a-month-what-are-they-supposed-to-bring,916458)
- [Pokebattler 소식](https://pokebattler.com/news)
- [Pokebattler Raid Advice 도움말](https://articles.pokebattler.com/help/raid-advice/)
- [Pokebattler Advice](https://www.pokebattler.com/advice)
- [GO Hub — 맥스배틀 카운터 예시](https://db.pokemongohub.net/pokemon/820-Dynamax/counters-max-battles)
- [TechCrunch — Niantic 정지 기준(2018)](https://techcrunch.com/2018/07/20/niantic-explains-how-and-why-it-bans-players-in-pokemon-go)
- [GO Hub — 서드파티 앱 경고](https://pokemongohub.net/post/news/niantic-issues-third-party-app-warning-message/)
- [Apple 개발자 포럼 — 4.1 Copycats 거절 사례](https://developer.apple.com/forums/thread/768539)
- [Apple App Review Guidelines](https://developer.apple.com/app-store/review/guidelines/)
- [Apple Foundation Models 문서](https://developer.apple.com/documentation/foundationmodels)
- [Apple Newsroom — Foundation Models 프레임워크(2025-09)](https://www.apple.com/au/newsroom/2025/09/apples-foundation-models-framework-unlocks-new-intelligent-app-experiences/)
- [WWDC25 — Foundation Models 세션](https://developer.apple.com/videos/play/wwdc2025/286/)
- [Cult of Mac — Apple Intelligence 지원 언어](https://www.cultofmac.com/news/apple-foundation-models-framework)
