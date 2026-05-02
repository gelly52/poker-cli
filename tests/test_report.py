"""Tests for scan report rendering."""
import json

from poker.capabilities.scan.report import render_json, render_markdown
from poker.models import Finding, Severity


def _finding() -> Finding:
    return Finding(
        rule_id="generic-api-key",
        title="Possible hard-coded secret",
        severity=Severity.HIGH,
        category="secret",
        path="app.py",
        line=3,
        evidence="API_KEY=abcdefghijklmnopqrstuvwxyz",
        recommendation="Move secrets to environment variables.",
    )


def test_render_json_contains_metadata_and_findings() -> None:
    data = json.loads(render_json([_finding()]))

    assert data["tool"] == "poker-cli"
    assert data["summary"]["total"] == 1
    assert data["findings"][0]["rule_id"] == "generic-api-key"


def test_render_markdown_contains_finding_details() -> None:
    report = render_markdown([_finding()])

    assert "# Poker CLI Security Report" in report
    assert "[high] Possible hard-coded secret" in report
    assert "`app.py:3`" in report


def test_render_markdown_handles_empty_findings() -> None:
    assert "No findings detected." in render_markdown([])
