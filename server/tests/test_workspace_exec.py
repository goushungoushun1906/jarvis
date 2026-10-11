"""workspace_run_command 的 argv 化执行。

以前是 `subprocess.run(command, shell=True)`：整条命令交给 cmd.exe，管道、
重定向、`&&`、`;` 全都会被解释——模型只要能往参数里塞一个分号，就能在审批
同意的"npm test"后面接任意内容。改成 argv + shell=False 后没有 shell 参与
解释，分号只是一个奇怪的文件名。

代价是需要 shell 的命令（管道/重定向）会失败。这里用测试把"默认拒绝、显式
开启才放行"这两条都钉住，避免以后有人为了省事把它悄悄改回 shell=True。
"""

from __future__ import annotations

import sys

import pytest

from app import workspace_tools as wt
from app.tools import ToolResult


@pytest.fixture()
def env_shell(monkeypatch):
    """控制 JARVIS_WORKSPACE_ALLOW_SHELL 的开关。"""

    def _set(value: str | None) -> None:
        if value is None:
            monkeypatch.delenv("JARVIS_WORKSPACE_ALLOW_SHELL", raising=False)
        else:
            monkeypatch.setenv("JARVIS_WORKSPACE_ALLOW_SHELL", value)

    return _set


@pytest.fixture()
def workspace(tmp_path, monkeypatch):
    wt._set_workspace(str(tmp_path))
    yield tmp_path
    wt._set_workspace(str(tmp_path))  # keep state local to this test
    monkeypatch.setattr(wt, "_active_workspace", None)


# ---------------------------------------------------------------------------
# 切分
# ---------------------------------------------------------------------------


def test_split_keeps_windows_paths():
    argv = wt._split_command(r"C:\Users\me\app.exe --flag")
    assert argv[0] == r"C:\Users\me\app.exe"
    assert argv[1] == "--flag"


def test_split_honours_quotes():
    assert wt._split_command('echo "a b"') == ["echo", "a b"]


def test_split_simple_command():
    assert wt._split_command("python -m pytest tests/") == [
        "python",
        "-m",
        "pytest",
        "tests/",
    ]


# ---------------------------------------------------------------------------
# 是否需要 shell
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        "ls | grep x",
        "pytest > out.txt",
        "npm test && npm run build",
        "cd foo; rm bar",
        "echo $(whoami)",
    ],
)
def test_shell_syntax_is_detected(command):
    assert wt._needs_shell(command) is True


@pytest.mark.parametrize("command", ["npm test", "python -m pytest", "git status"])
def test_plain_commands_need_no_shell(command):
    assert wt._needs_shell(command) is False


# ---------------------------------------------------------------------------
# 默认拒绝 shell
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_shell_command_refused_by_default(workspace, env_shell):
    env_shell(None)
    tool = wt.WorkspaceRunCommandTool()
    result = await tool.execute("pytest > out.txt")
    assert isinstance(result, ToolResult)
    assert result.success is False
    assert "JARVIS_WORKSPACE_ALLOW_SHELL" in result.content


@pytest.mark.anyio
async def test_shell_builtin_refused(workspace, env_shell):
    env_shell(None)
    tool = wt.WorkspaceRunCommandTool()
    result = await tool.execute("cd subdir")
    assert result.success is False
    assert "builtin" in result.content


# ---------------------------------------------------------------------------
# argv 执行真的能跑
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_plain_command_runs_without_shell(workspace, env_shell):
    env_shell(None)
    tool = wt.WorkspaceRunCommandTool()
    result = await tool.execute(f'"{sys.executable}" -c "print(1+1)"')
    assert result.success is True, result.content
    assert "2" in result.content


@pytest.mark.anyio
async def test_command_output_is_captured(workspace, env_shell):
    env_shell(None)
    tool = wt.WorkspaceRunCommandTool()
    result = await tool.execute(f'"{sys.executable}" -c "print(\'hello-argv\')"')
    assert "hello-argv" in result.content


# ---------------------------------------------------------------------------
# 显式开启才放行
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_shell_allowed_when_env_enabled(workspace, env_shell):
    env_shell("1")
    tool = wt.WorkspaceRunCommandTool()
    result = await tool.execute(f'"{sys.executable}" -c "print(1)" > out.txt')
    # 开启后不再因为"需要 shell"被拒；是否成功取决于系统 shell，只断言没被拦
    assert "JARVIS_WORKSPACE_ALLOW_SHELL" not in result.content


def test_dangerous_filter_runs_before_execution():
    """黑名单必须还在 argv 化之前生效，两者不能互相替代。"""
    assert wt._is_dangerous_command("shutdown /s") is True
    assert wt._is_dangerous_command("npm test") is False
