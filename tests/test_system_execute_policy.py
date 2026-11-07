from skills.system_execute.skill import _read_file, _redact_secrets


def test_secret_values_are_redacted_from_command_and_file_results(tmp_path):
    raw = "OPENAI_API_KEY=sk-testvalue\nAuthorization: Bearer token-123456789\nvisible=value\n"
    assert "sk-testvalue" not in _redact_secrets(raw)
    assert "token-123456789" not in _redact_secrets(raw)
    assert "visible=value" in _redact_secrets(raw)

    env_file = tmp_path / ".env"
    env_file.write_text(raw, encoding="utf-8")
    result = _read_file(str(env_file))
    assert result["success"]
    assert "sk-testvalue" not in result["content"]
    assert "token-123456789" not in result["content"]
