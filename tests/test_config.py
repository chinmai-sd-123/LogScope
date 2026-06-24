import os

from logscope.config import load_env


def test_load_env_sets_missing_vars(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text(
        "# a comment\n"
        "\n"
        'FOO_KEY="quoted value"\n'
        "BAR_KEY=plain\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("FOO_KEY", raising=False)
    monkeypatch.delenv("BAR_KEY", raising=False)

    load_env(env)
    assert os.environ["FOO_KEY"] == "quoted value"
    assert os.environ["BAR_KEY"] == "plain"


def test_existing_env_wins(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("ALREADY=from_file\n", encoding="utf-8")
    monkeypatch.setenv("ALREADY", "from_real_env")

    load_env(env)
    assert os.environ["ALREADY"] == "from_real_env"  # not overridden


def test_missing_file_is_noop(tmp_path):
    load_env(tmp_path / "does-not-exist.env")  # must not raise
