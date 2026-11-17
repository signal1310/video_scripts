---
name: gui-design
description: >-
  Use this skill when building a new PyQt GUI screen/dialog/widget or reshaping an
  existing one ("UI 디자인", "화면 개선", "다이얼로그 추가", "레이아웃 변경").
  Guides deliberate layout, typography, hierarchy, and UI copy within the theme
  palette tokens and AGENTS.md UI rules.
---

# GUI 디자인 (`gui-design`)

불변식은 `AGENTS.md` 1절, 3절을 따르고, 이 스킬은 설계 순서와 판단 기준만 정한다. 웹 전용 요소(히어로, CSS 셀렉터, 웹폰트)는 다루지 않는다.

## 1. 대상 파악

- 화면의 사용자, 주 작업 1개, 다루는 데이터(영상 파일, 분류 결과, 설정)를 먼저 한 줄로 정함
- 맥락이 모호하면 가정을 명시하고 확인받은 뒤 진행(`AGENTS.md` 7절)
- 기존 화면은 `src/gui/main_window.py`, `src/gui/window_layout.py`, `src/gui/dialogs/`, `src/gui/widgets/`의 패턴을 먼저 읽고 맞춤

## 2. 설계안 먼저

구현 전에 짧은 설계안을 제시하고 요청 범위와 대조한다.

- 레이아웃: 한 문장 설명과 ASCII 와이어프레임, 정렬 기준(좌측 정렬 기본)
- 위계: 가장 중요한 컨트롤 1개를 정하고 나머지는 조용하게 유지
- 색상: 새 색을 정하지 않고 `src/gui/styles/palettes.py`의 팔레트 토큰만 사용(하드코딩 금지)
- 타이포: 새 글꼴 도입 대신 기존 폰트 정책에 맞춰 크기, 굵기로 위계를 표현
- 기본값처럼 보이는 구성(동일한 카드 반복, 모든 영역에 같은 여백과 테두리)이면 해당 화면의 내용에 맞게 수정

## 3. 구조는 정보

- 테두리, 구분선, 그룹 박스, 번호는 내용에 실제 의미가 있을 때만 사용(번호는 실제 순서가 있는 절차에만)
- 장식용 컨트롤, 백엔드에 연결되지 않은 요소, 근거 없는 초기 문구 금지(`AGENTS.md` 3절)
- 애니메이션은 사용자 동작에 대한 응답(열기, 펼치기, 확정)에만 쓰고, 상시 장식 모션은 넣지 않음

## 4. UI 문구

문체는 `AGENTS.md` 3절이 우선한다.

- 버튼, 열 헤더, 배지: 간결한 명사구, 툴팁, 설정 설명, 오류 안내, 확인 대화상자: 존댓말
- 사용자 관점 용어 사용: 내부 구현이 아니라 사용자가 이해하는 이름으로 부름
- 새 용어가 필요하면 자연스러운 한국어 후보와 추천안을 제시해 선택받은 뒤 `TERMINOLOGY.md`(11절)에 추가하고 사용. 임의로 확정하지 않음
- 버튼은 아이콘만 표시하고 기능 이름은 접근성 이름(명사구)과 툴팁(존댓말)으로 제공(`AGENTS.md` 3절)
- 같은 동작은 흐름 전체에서 같은 이름 유지(접근성 이름 "내보내기"는 툴팁, 메뉴, 결과 안내도 "내보내기")
- 오류는 무엇이 잘못됐고 어떻게 해결하는지 구체적으로 안내하고, 빈 상태는 다음 행동을 안내
- 가운뎃점, 화살표 등 ASCII 밖 기호 금지(나열은 쉼표, 짝을 이루는 이름은 `/`)

## 5. 품질 기준

- 다크/라이트/시스템 전환과 양쪽 모드의 대비 확인(`src/gui/theme.py`, `src/gui/styles/` 변경 시 필수)
- 키보드 탐색(탭 순서, 포커스 표시)과 창 크기 축소 시 잘림 여부 확인
- 아이콘은 임포트 시점 디스크 쓰기 없이 지연 생성
- 가능하면 `QT_QPA_PLATFORM=offscreen`으로 화면을 렌더링해 스크린샷으로 자체 점검
- 완성 후 장식 1개를 덜어낼 수 있는지 점검

## 6. 마무리

GUI 변경은 `tests/test_gui_*.py`에 반영한다([`test`](../test/SKILL.md)). 버튼, 단축키, 용어를 바꿨다면 [`sync`](../sync/SKILL.md)를 이어서 수행하고, `check.ps1` 통과 후 [`review`](../review/SKILL.md) 또는 [`commit`](../commit/SKILL.md)으로 이어간다.
