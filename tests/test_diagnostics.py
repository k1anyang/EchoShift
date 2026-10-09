"""Persistent diagnostics must not leak secrets or user directory layouts."""

from echoshift.core.diagnostics import sanitize_diagnostic


def test_diagnostic_sanitizer_redacts_ekeys_and_windows_paths() -> None:
    original = "ekey=secret-token 输出：E:\\music\\Artist\\Track.flac"
    cleaned = sanitize_diagnostic(original)

    assert "secret-token" not in cleaned
    assert "Artist" not in cleaned
    assert "<REDACTED>" in cleaned
    assert "<PATH>" in cleaned
