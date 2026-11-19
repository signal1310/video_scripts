"""저장소 루트 경로 상수. 테스트 파일을 하위 폴더로 옮겨도 같은 값을 얻도록 여기서만 계산"""

import pathlib

REPO_ROOT: pathlib.Path = pathlib.Path(__file__).resolve().parent.parent
