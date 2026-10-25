"""The viewer runs in a fixed local environment, free of the agent's variables."""

from pathlib import Path

from agentcraft2_phoenix.config import apply_environment, server_environment


def test_the_settings_keep_phoenix_on_loopback_and_send_nothing_out(tmp_path: Path) -> None:
    settings = server_environment(tmp_path, 6010)
    assert settings["PHOENIX_HOST"] == "127.0.0.1"
    assert settings["PHOENIX_PORT"] == "6010"
    assert settings["PHOENIX_TELEMETRY_ENABLED"] == "false"
    assert settings["PHOENIX_ALLOW_EXTERNAL_RESOURCES"] == "false"
    assert settings["PHOENIX_ALLOWED_PROVIDERS"] == "NONE"
    assert settings["PHOENIX_ALLOWED_SANDBOX_PROVIDERS"] == "NONE"
    assert settings["PHOENIX_ENABLE_MCP_SERVER"] == "false"
    assert settings["PHOENIX_WORKING_DIR"] == str(tmp_path)
    assert settings["PHOENIX_LOG_MIGRATIONS"] == "false"


def test_the_database_is_a_sqlite_file_in_the_data_folder(tmp_path: Path) -> None:
    url = server_environment(tmp_path, 6006)["PHOENIX_SQL_DATABASE_URL"]
    assert url == "sqlite:///" + (tmp_path / "phoenix.db").as_posix()


def test_the_agents_variables_are_removed_and_the_rest_are_kept(tmp_path: Path) -> None:
    environment = {
        "PATH": "/bin",
        "SystemRoot": "C:/Windows",
        "ANTHROPIC_API_KEY": "sk-private",
        "AWS_PROFILE": "work",
        "AC2_MODEL_PROVIDER": "anthropic",
        "EAP_PROFILE": "aws",
        "OTEL_EXPORTER_OTLP_ENDPOINT": "http://collector",
        "PHOENIX_API_KEY": "key",
        "PHOENIX_COLLECTOR_ENDPOINT": "https://remote.example",
        "PHOENIX_HOST": "0.0.0.0",  # noqa: S104 - an unsafe setting, to be replaced
    }
    apply_environment(environment, tmp_path, 6006)
    assert environment["PATH"] == "/bin"
    assert environment["SystemRoot"] == "C:/Windows"
    for name in ("ANTHROPIC_API_KEY", "AWS_PROFILE", "AC2_MODEL_PROVIDER", "EAP_PROFILE"):
        assert name not in environment
    assert "PHOENIX_API_KEY" not in environment
    assert "PHOENIX_COLLECTOR_ENDPOINT" not in environment
    assert "OTEL_EXPORTER_OTLP_ENDPOINT" not in environment
    assert environment["PHOENIX_HOST"] == "127.0.0.1"
