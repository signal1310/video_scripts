"""테스트 패키지 로드 시 프로세스 전역 외부 환경 격리 가드를 자동 활성화"""

from tests._sandbox import install_test_sandbox

install_test_sandbox()
