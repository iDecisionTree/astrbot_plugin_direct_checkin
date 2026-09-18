from pathlib import Path

import pytest
from astrbot_plugin_direct_checkin.utils.path_utils import (
    safe_join,
    sanitize_filename,
    sha256_bytes,
    sha256_file,
)


def test_sanitize_filename_strips_path_traversal():
    assert sanitize_filename("../../etc/passwd") == "passwd"
    assert sanitize_filename("..\\..\\windows\\system32\\x.docx") == "x.docx"
    assert sanitize_filename("a/b/c/report.docx") == "report.docx"


def test_sanitize_filename_removes_illegal_characters():
    result = sanitize_filename('we:ird<>"?|*name.docx')
    assert result.endswith(".docx")
    assert not set('<>:"/\\|?*') & set(result)
    assert sanitize_filename("   ") == "file"


def test_sanitize_filename_truncates_but_keeps_suffix():
    result = sanitize_filename("x" * 300 + ".docx", max_length=20)
    assert result.endswith(".docx")
    assert len(result) <= 20


def test_safe_join_within_base(tmp_path: Path):
    target = safe_join(tmp_path, "2026123456", "a.docx")
    assert str(target).startswith(str(tmp_path.resolve()))


def test_safe_join_rejects_escape(tmp_path: Path):
    with pytest.raises(ValueError):
        safe_join(tmp_path, "..", "outside.txt")


def test_sha256_file(tmp_path: Path):
    path = tmp_path / "a.bin"
    path.write_bytes(b"hello")
    assert sha256_file(path) == sha256_bytes(b"hello")
