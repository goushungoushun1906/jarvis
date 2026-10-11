"""插件命令的审批元数据。

审批拦截对内置工具是强制的，但插件命令此前没有任何元数据，等于留了一条旁路：
装个插件就能绕过确认。这里把 requires_approval 做成 PluginCommand 的字段，
由 plugin.json 声明，并由 agent 的审批判定一并读取。
"""

from __future__ import annotations

import pytest

from app import agent as agent_mod
from app import workspace_tools  # noqa: F401  — 让内置工具注册进 registry
from app.plugins.base import BasePlugin, PluginCommand
from app.plugins.registry import PluginRegistry


class _StubPlugin(BasePlugin):
    name = "stub"

    def __init__(self, manifest_commands: list[dict]) -> None:
        self._manifest_commands = manifest_commands

    async def execute(self, command_name: str, args: dict):  # pragma: no cover
        raise NotImplementedError


def _registry_with(commands: list[dict]) -> PluginRegistry:
    reg = PluginRegistry()
    inst = _StubPlugin(commands)
    reg._plugin_instances = {"stub": inst}
    reg._enabled = {"stub"}
    reg._command_map = {c["name"]: ("stub", c["name"]) for c in commands}
    return reg


# ---------------------------------------------------------------------------
# 元数据字段
# ---------------------------------------------------------------------------


def test_command_defaults_to_ungated():
    cmd = PluginCommand(name="read_thing", description="", parameters={})
    assert cmd.requires_approval is False


def test_manifest_declares_gated_command():
    plugin = _StubPlugin(
        [
            {"name": "read_thing", "description": "", "parameters": {}},
            {
                "name": "wipe_thing",
                "description": "",
                "parameters": {},
                "requires_approval": True,
            },
        ]
    )
    flags = {c.name: c.requires_approval for c in plugin.get_commands()}
    assert flags == {"read_thing": False, "wipe_thing": True}


def test_state_exposes_gated_commands():
    plugin = _StubPlugin(
        [{"name": "wipe_thing", "description": "", "parameters": {},
          "requires_approval": True}]
    )
    assert plugin.get_state()["gated_commands"] == ["wipe_thing"]


# ---------------------------------------------------------------------------
# 注册表查询
# ---------------------------------------------------------------------------


def test_registry_reports_gated_and_ungated():
    reg = _registry_with(
        [
            {"name": "read_thing", "description": "", "parameters": {}},
            {"name": "wipe_thing", "description": "", "parameters": {},
             "requires_approval": True},
        ]
    )
    assert reg.command_requires_approval("wipe_thing") is True
    assert reg.command_requires_approval("read_thing") is False


def test_registry_unknown_command_is_not_gated():
    """未知命令返回 False —— 它后面会因为 unknown tool 失败，不会被误放行。"""
    reg = _registry_with([])
    assert reg.command_requires_approval("no_such_command") is False


# ---------------------------------------------------------------------------
# agent 的判定要把插件算进去
# ---------------------------------------------------------------------------


def test_agent_gate_consults_plugin_registry(monkeypatch):
    reg = _registry_with(
        [{"name": "wipe_thing", "description": "", "parameters": {},
          "requires_approval": True}]
    )
    monkeypatch.setattr(agent_mod, "plugin_registry", reg)
    assert agent_mod._tool_needs_approval("wipe_thing") is True


def _ensure_builtin_tools():
    """内置工具要显式注册才进 registry，测试里手动调一次。"""
    workspace_tools.register_workspace_tools()


def test_builtin_tools_still_win(monkeypatch):
    """内置工具的判定不能被插件注册表覆盖。"""
    _ensure_builtin_tools()
    reg = _registry_with([])
    monkeypatch.setattr(agent_mod, "plugin_registry", reg)
    assert agent_mod._tool_needs_approval("workspace_run_command") is True


@pytest.mark.parametrize(
    "plugin_file,gated",
    [("clipboard", "clear_history"), ("reminder", "delete_reminder")],
)
def test_shipped_manifests_mark_irreversible_commands(plugin_file, gated):
    """随包发布的不可逆命令必须真的声明了 requires_approval。"""
    import json
    import os

    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "plugins",
        plugin_file,
        "plugin.json",
    )
    with open(path, encoding="utf-8") as f:
        manifest = json.load(f)
    flags = {
        c["name"]: bool(c.get("requires_approval", False))
        for c in manifest["commands"]
    }
    assert flags[gated] is True
