"""공용 트랜잭션 컨텍스트의 커밋, 롤백 동작을 검증"""

import sqlite3
import unittest

from src.service.sql_transaction import transaction


class TransactionTests(unittest.TestCase):
    """BEGIN IMMEDIATE 트랜잭션 컨텍스트"""

    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:", isolation_level=None)
        self.addCleanup(self.conn.close)
        self.conn.execute("CREATE TABLE t (v INTEGER)")

    def _values(self) -> list[int]:
        return [int(row[0]) for row in self.conn.execute("SELECT v FROM t")]

    def _in_transaction(self) -> bool:
        return self.conn.in_transaction

    def test_commits_when_the_block_succeeds(self) -> None:
        with transaction(self.conn):
            self.assertTrue(self._in_transaction())
            self.conn.execute("INSERT INTO t (v) VALUES (1)")
        self.assertFalse(self._in_transaction())
        self.assertEqual(self._values(), [1])

    def test_rolls_back_when_the_block_raises(self) -> None:
        with self.assertRaises(RuntimeError), transaction(self.conn):
            self.conn.execute("INSERT INTO t (v) VALUES (1)")
            raise RuntimeError("stop")
        self.assertFalse(self._in_transaction())
        self.assertEqual(self._values(), [])

    def test_nested_begin_immediate_is_rejected(self) -> None:
        with transaction(self.conn), self.assertRaises(sqlite3.OperationalError):
            self.conn.execute("BEGIN IMMEDIATE")


if __name__ == "__main__":
    unittest.main()
