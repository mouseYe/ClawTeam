"""Tests for spawn backend environment propagation."""

from __future__ import annotations

import sys

from clawteam.spawn.cli_env import build_spawn_path, resolve_clawteam_executable
from clawteam.spawn.subprocess_backend import SubprocessBackend
from clawteam.spawn.tmux_backend import TmuxBackend


class DummyProcess:
    def __init__(self, pid: int = 4321):
        self.pid = pid

    def poll(self):
        return None


def test_subprocess_backend_prepends_current_clawteam_bin_to_path(monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    clawteam_bin = tmp_path / "venv" / "bin" / "clawteam"
    clawteam_bin.parent.mkdir(parents=True)
    clawteam_bin.write_text("#!/bin/sh\n")
    monkeypatch.setattr(sys, "argv", [str(clawteam_bin)])

    captured: dict[str, object] = {}

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = kwargs["env"]
        return DummyProcess()

    monkeypatch.setattr("clawteam.spawn.subprocess_backend.subprocess.Popen", fake_popen)
    monkeypatch.setattr("clawteam.spawn.registry.register_agent", lambda **_: None)

    backend = SubprocessBackend()
    backend.spawn(
        command=["codex"],
        agent_name="worker1",
        agent_id="agent-1",
        agent_type="general-purpose",
        team_name="demo-team",
        prompt="do work",
        cwd="/tmp/demo",
        skip_permissions=True,
    )

    env = captured["env"]
    assert env["PATH"].startswith(f"{clawteam_bin.parent}:")
    assert env["CLAWTEAM_BIN"] == str(clawteam_bin)


def test_tmux_backend_exports_spawn_path_for_agent_commands(monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    clawteam_bin = tmp_path / "venv" / "bin" / "clawteam"
    clawteam_bin.parent.mkdir(parents=True)
    clawteam_bin.write_text("#!/bin/sh\n")
    monkeypatch.setattr(sys, "argv", [str(clawteam_bin)])

    run_calls: list[list[str]] = []

    class Result:
        def __init__(self, returncode: int = 0, stdout: str = ""):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = ""

    def fake_run(args, **kwargs):
        run_calls.append(args)
        if args[:3] == ["tmux", "has-session", "-t"]:
            return Result(returncode=1)
        if args[:3] == ["tmux", "list-panes", "-t"]:
            return Result(returncode=0, stdout="9876\n")
        return Result(returncode=0)

    original_which = __import__("shutil").which
    monkeypatch.setattr(
        "clawteam.spawn.tmux_backend.shutil.which",
        lambda name: "/opt/homebrew/bin/tmux" if name == "tmux" else original_which(name),
    )
    monkeypatch.setattr("clawteam.spawn.tmux_backend.subprocess.run", fake_run)
    monkeypatch.setattr("clawteam.spawn.tmux_backend.time.sleep", lambda *_: None)
    monkeypatch.setattr("clawteam.spawn.registry.register_agent", lambda **_: None)

    backend = TmuxBackend()
    backend.spawn(
        command=["codex"],
        agent_name="worker1",
        agent_id="agent-1",
        agent_type="general-purpose",
        team_name="demo-team",
        prompt="do work",
        cwd="/tmp/demo",
        skip_permissions=True,
    )

    new_session = next(call for call in run_calls if call[:3] == ["tmux", "new-session", "-d"])
    full_cmd = new_session[-1]
    assert f"export PATH={clawteam_bin.parent}:/usr/bin:/bin" in full_cmd
    assert f"export CLAWTEAM_BIN={clawteam_bin}" in full_cmd
    assert f"{clawteam_bin} lifecycle on-exit --team demo-team --agent worker1" in full_cmd


def test_resolve_clawteam_executable_ignores_unrelated_argv0(monkeypatch, tmp_path):
    unrelated = tmp_path / "not-clawteam-review"
    unrelated.write_text("#!/bin/sh\n")
    resolved_bin = tmp_path / "bin" / "clawteam"
    resolved_bin.parent.mkdir(parents=True)
    resolved_bin.write_text("#!/bin/sh\n")

    monkeypatch.setattr(sys, "argv", [str(unrelated)])
    monkeypatch.setattr("clawteam.spawn.cli_env.shutil.which", lambda name: str(resolved_bin))

    assert resolve_clawteam_executable() == str(resolved_bin)
    assert build_spawn_path("/usr/bin:/bin").startswith(f"{resolved_bin.parent}:")


def test_resolve_clawteam_executable_ignores_relative_argv0_even_if_local_file_exists(
    monkeypatch, tmp_path
):
    local_shadow = tmp_path / "clawteam"
    local_shadow.write_text("#!/bin/sh\n")
    resolved_bin = tmp_path / "venv" / "bin" / "clawteam"
    resolved_bin.parent.mkdir(parents=True)
    resolved_bin.write_text("#!/bin/sh\n")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["clawteam"])
    monkeypatch.setattr("clawteam.spawn.cli_env.shutil.which", lambda name: str(resolved_bin))

    assert resolve_clawteam_executable() == str(resolved_bin)
    assert build_spawn_path("/usr/bin:/bin").startswith(f"{resolved_bin.parent}:")


def test_resolve_clawteam_executable_accepts_relative_path_with_explicit_directory(
    monkeypatch, tmp_path
):
    relative_bin = tmp_path / ".venv" / "bin" / "clawteam"
    relative_bin.parent.mkdir(parents=True)
    relative_bin.write_text("#!/bin/sh\n")
    fallback_bin = tmp_path / "fallback" / "clawteam"
    fallback_bin.parent.mkdir(parents=True)
    fallback_bin.write_text("#!/bin/sh\n")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["./.venv/bin/clawteam"])
    monkeypatch.setattr("clawteam.spawn.cli_env.shutil.which", lambda name: str(fallback_bin))

    assert resolve_clawteam_executable() == str(relative_bin.resolve())
    assert build_spawn_path("/usr/bin:/bin").startswith(f"{relative_bin.parent.resolve()}:")


def test_subprocess_backend_injects_system_prompt_for_claude(monkeypatch, tmp_path):
    """--append-system-prompt is added to the command when system_prompt is given."""
    clawteam_bin = tmp_path / "bin" / "clawteam"
    clawteam_bin.parent.mkdir(parents=True)
    clawteam_bin.write_text("#!/bin/sh\n")
    monkeypatch.setattr(sys, "argv", [str(clawteam_bin)])

    captured: dict[str, object] = {}

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = kwargs["env"]
        return DummyProcess()

    monkeypatch.setattr("clawteam.spawn.subprocess_backend.subprocess.Popen", fake_popen)
    monkeypatch.setattr("clawteam.spawn.registry.register_agent", lambda **_: None)

    backend = SubprocessBackend()
    backend.spawn(
        command=["claude"],
        agent_name="worker1",
        agent_id="agent-1",
        agent_type="general-purpose",
        team_name="demo-team",
        prompt="do work",
        skip_permissions=True,
        system_prompt="You are an expert coder.",
    )

    cmd = captured["cmd"]
    assert "--append-system-prompt" in cmd
    assert "You are an expert coder." in cmd
    # system prompt must appear before -p
    assert cmd.index("--append-system-prompt") < cmd.index(" -p ")


def test_subprocess_backend_skips_system_prompt_for_non_claude(monkeypatch, tmp_path):
    """--append-system-prompt is NOT added for non-claude commands (e.g. codex)."""
    clawteam_bin = tmp_path / "bin" / "clawteam"
    clawteam_bin.parent.mkdir(parents=True)
    clawteam_bin.write_text("#!/bin/sh\n")
    monkeypatch.setattr(sys, "argv", [str(clawteam_bin)])

    captured: dict[str, object] = {}

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        return DummyProcess()

    monkeypatch.setattr("clawteam.spawn.subprocess_backend.subprocess.Popen", fake_popen)
    monkeypatch.setattr("clawteam.spawn.registry.register_agent", lambda **_: None)

    backend = SubprocessBackend()
    backend.spawn(
        command=["codex"],
        agent_name="worker1",
        agent_id="agent-1",
        agent_type="general-purpose",
        team_name="demo-team",
        prompt="do work",
        system_prompt="some system text",
    )

    assert "--append-system-prompt" not in captured["cmd"]


def test_subprocess_backend_sets_utf8_locale(monkeypatch, tmp_path):
    """LANG and LC_CTYPE are set to UTF-8 so Chinese prompts are not mangled."""
    clawteam_bin = tmp_path / "bin" / "clawteam"
    clawteam_bin.parent.mkdir(parents=True)
    clawteam_bin.write_text("#!/bin/sh\n")
    monkeypatch.setattr(sys, "argv", [str(clawteam_bin)])
    monkeypatch.delenv("LANG", raising=False)
    monkeypatch.delenv("LC_CTYPE", raising=False)

    captured: dict[str, object] = {}

    def fake_popen(cmd, **kwargs):
        captured["env"] = kwargs["env"]
        return DummyProcess()

    monkeypatch.setattr("clawteam.spawn.subprocess_backend.subprocess.Popen", fake_popen)
    monkeypatch.setattr("clawteam.spawn.registry.register_agent", lambda **_: None)

    backend = SubprocessBackend()
    backend.spawn(
        command=["claude"],
        agent_name="worker1",
        agent_id="agent-1",
        agent_type="general-purpose",
        team_name="demo-team",
        prompt="使用技能",
    )

    env = captured["env"]
    assert env.get("LANG") == "en_US.UTF-8"
    assert env.get("LC_CTYPE") == "UTF-8"


def test_load_skill_content_directory_format(tmp_path, monkeypatch):
    """Skills stored as directories with SKILL.md are loaded correctly."""
    from clawteam.cli.commands import _load_skill_content

    skills_root = tmp_path / ".claude" / "skills"
    skill_dir = skills_root / "my-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("skill content here", encoding="utf-8")

    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

    result = _load_skill_content("my-skill")
    assert result == "skill content here"


def test_load_skill_content_single_file_format(tmp_path, monkeypatch):
    """Skills stored as a single .md file are loaded correctly."""
    from clawteam.cli.commands import _load_skill_content

    skills_root = tmp_path / ".claude" / "skills"
    skills_root.mkdir(parents=True)
    (skills_root / "gsd-health.md").write_text("# GSD Health\ncheck it", encoding="utf-8")

    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

    result = _load_skill_content("gsd-health")
    assert result == "# GSD Health\ncheck it"


def test_load_skill_content_returns_none_for_missing(tmp_path, monkeypatch):
    """Returns None when a skill does not exist."""
    from clawteam.cli.commands import _load_skill_content

    (tmp_path / ".claude" / "skills").mkdir(parents=True)
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

    assert _load_skill_content("nonexistent") is None
