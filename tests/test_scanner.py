"""Tests for scanner orchestration."""

from poker.scanner import scan_path


def test_scan_path_scans_single_file(tmp_path) -> None:
    target = tmp_path / "settings.env"
    target.write_text("SECRET=abcdefghijklmnopqrstuvwxyz123456", encoding="utf-8")

    findings = scan_path(target)

    assert len(findings) == 1
    assert findings[0].path == "settings.env"


def test_scan_path_scans_directory(tmp_path) -> None:
    app_file = tmp_path / "app.py"
    app_file.write_text("api_key='abcdefghijklmnopqrstuvwxyz123456'", encoding="utf-8")

    findings = scan_path(tmp_path)

    assert len(findings) == 1
    assert findings[0].path == "app.py"


def test_scan_path_skips_cache_directories(tmp_path) -> None:
    cache_dir = tmp_path / "__pycache__"
    cache_dir.mkdir()
    cache_file = cache_dir / "cached.py"
    cache_file.write_text("API_KEY=abcdefghijklmnopqrstuvwxyz123456", encoding="utf-8")

    findings = scan_path(tmp_path)

    assert findings == []
