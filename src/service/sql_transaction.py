"""SQLite 쓰기를 BEGIN IMMEDIATE 트랜잭션으로 감싸는 공용 컨텍스트 모듈

- 분석 캐시 저장소와 작업 기록 테이블이 같은 규칙(잠금 대기 시작, 예외 시 롤백)을 써야 하므로 정의를 한 곳에 둠
"""

import sqlite3


class transaction:
    """BEGIN IMMEDIATE로 시작해 정상 종료 시 COMMIT, 예외 시 ROLLBACK 하는 컨텍스트"""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn: sqlite3.Connection = conn

    def __enter__(self) -> None:
        self._conn.execute("BEGIN IMMEDIATE")

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        if exc_type is None:
            self._conn.execute("COMMIT")
        else:
            self._conn.execute("ROLLBACK")
