# 검증

```sh
uv sync --locked
uv run --locked python -m unittest discover -s tests -v
git diff --check
```

테스트는 임시 데이터 디렉터리와 합성 영상을 사용합니다. FFmpeg를 설치하면 실제 인코딩, 업로드, 프레임 생성, 조회까지 검사합니다. FFmpeg가 없으면 이 테스트만 건너뜁니다. CI는 FFmpeg를 설치해 전체 테스트를 실행합니다.

공통 에이전트 안내 두 파일은 바이트 단위로 같아야 합니다. 커밋에 사용자 영상, 로컬 상태 및 비밀값이 포함됐는지 확인합니다. 변경 주제별로 파일을 스테이징하고 `type(scope): 요약`으로 커밋합니다.

Safari 실기기와 Pokémon GO 영상의 인식 정확도는 실제 Mac과 iPhone에서 확인합니다. 합성 영상 테스트 결과는 기기나 게임 화면 검증 결과로 기록하지 않습니다.
