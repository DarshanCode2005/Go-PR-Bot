import pytest

from go_agent.config import Settings, clear_settings_cache, get_settings


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    clear_settings_cache()
    yield
    clear_settings_cache()


def test_settings_defaults(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GO_AGENT_LOG_LEVEL", raising=False)
    settings = Settings()
    assert settings.log_level == "INFO"
    assert isinstance(settings.work_dir, type(settings.artifacts_dir))
    assert settings.max_fix_iterations == 5


def test_log_level_from_env(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GO_AGENT_LOG_LEVEL", "debug")
    settings = Settings()
    assert settings.log_level == "DEBUG"
    assert settings.logging_level == 10


def test_invalid_log_level(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GO_AGENT_LOG_LEVEL", "verbose")
    with pytest.raises(ValueError, match="invalid log level"):
        Settings()


def test_get_settings_cached(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    first = get_settings()
    second = get_settings()
    assert first is second


def test_paths_from_env(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    artifacts = tmp_path / "art"
    workspaces = tmp_path / "ws"
    monkeypatch.setenv("GO_AGENT_ARTIFACTS_DIR", str(artifacts))
    monkeypatch.setenv("GO_AGENT_WORK_DIR", str(workspaces))
    settings = Settings()
    assert settings.artifacts_dir == artifacts.resolve()
    assert settings.work_dir == workspaces.resolve()
