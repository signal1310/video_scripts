"""프로그램 버전 상수 모듈(인코딩 상세 값과 인코딩 이력에 기록)

- 단일 출처는 pyproject.toml의 version이며, 두 값이 같은지는 테스트가 검사
- importlib.metadata를 쓰지 않음: 개발 환경은 Python 3.10이고 설치 없이 소스로 실행하므로 설치 메타데이터가 없음
"""

APP_VERSION: str = "0.1.0"
