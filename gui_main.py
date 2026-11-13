"""데스크톱 GUI 애플리케이션 직접 실행 스크립트"""

import sys

from src.gui import run_gui


def main() -> int:
    """Video Classifier Studio 데스크톱 GUI 애플리케이션 진입점"""
    return run_gui()


if __name__ == "__main__":
    sys.exit(main())
