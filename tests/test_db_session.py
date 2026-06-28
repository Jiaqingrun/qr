"""M8-2 db.session 提交重试。"""
from __future__ import annotations

import unittest
from unittest import mock

from qr import db


class TestDbSessionRetry(unittest.TestCase):
    def test_session_uses_run_db_retry_on_commit(self):
        conn = mock.MagicMock()
        with mock.patch.object(db, "connect", return_value=conn):
            with mock.patch.object(db, "run_db_retry") as retry:
                with db.session():
                    pass
                retry.assert_called_once()


if __name__ == "__main__":
    unittest.main()
