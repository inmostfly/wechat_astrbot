"""Offline regression tests for fatal error reporting."""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from UIA import my_catgirl


class CrashHandlingTests(unittest.TestCase):
    def test_report_contains_error_and_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            try:
                raise PermissionError(13, "测试拒绝访问")
            except PermissionError as error:
                path = my_catgirl.write_crash_report(error, directory)

            self.assertIsNotNone(path)
            report = Path(path).read_text(encoding="utf-8")
            self.assertIn("异常类型：PermissionError", report)
            self.assertIn("测试拒绝访问", report)
            self.assertIn("完整调用栈", report)
            self.assertIn("raise PermissionError", report)

    def test_source_run_does_not_pause_after_crash(self) -> None:
        with patch.object(my_catgirl.sys, "frozen", False, create=True):
            with patch("builtins.input") as mocked_input:
                my_catgirl.wait_for_user_after_crash()
        mocked_input.assert_not_called()

    def test_frozen_executable_waits_for_user(self) -> None:
        with patch.object(my_catgirl.sys, "frozen", True, create=True):
            with patch("builtins.input", return_value="") as mocked_input:
                my_catgirl.wait_for_user_after_crash()
        mocked_input.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
