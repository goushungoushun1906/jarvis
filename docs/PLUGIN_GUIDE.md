# JARVIS 插件开发指南

## 概述

JARVIS 插件系统允许你扩展 JARVIS AI 助手的能力。每个插件是一个独立目录，包含清单文件 (`plugin.json`) 和入口模块 (`main.py`)。

## 目录结构

```
plugins/
  my_plugin/
    plugin.json    # 插件清单（元数据、命令定义、配置项）
    main.py        # 插件实现（继承 BasePlugin）
```

## plugin.json 清单

```json
{
  "name": "my_plugin",
  "version": "1.0.0",
  "description": "插件功能描述",
  "author": "作者名",
  "commands": [
    {
      "name": "my_command",
      "description": "命令描述（会被 LLM 读取以决定何时调用）",
      "handler_name": "my_command",
      "parameters": {
        "type": "object",
        "properties": {
          "param1": {
            "type": "string",
            "description": "参数描述"
          }
        },
        "required": ["param1"]
      }
    }
  ],
  "config": [
    {
      "key": "api_key",
      "label": "API Key",
      "type": "string",
      "default": "",
      "description": "外部 API 密钥"
    }
  ]
}
```

### 字段说明

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `name` | string | 是 | 插件唯一标识符，同目录名 |
| `version` | string | 是 | 语义化版本号 |
| `description` | string | 是 | 功能描述 |
| `author` | string | 否 | 作者信息 |
| `commands` | array | 是 | 命令定义列表 |
| `config` | array | 否 | 配置项 schema |

### 命令参数类型

`parameters` 遵循 JSON Schema 格式，支持以下类型：

- `string` — 文本
- `integer` / `number` — 数值
- `boolean` — 布尔值
- `array` — 数组
- `object` — 对象

可使用 `enum` 限制可选值：
```json
{
  "type": "string",
  "enum": ["low", "medium", "high"],
  "description": "优先级"
}
```

### 配置项类型

| type | 说明 | 对应 UI 控件 |
|------|------|-------------|
| `string` | 文本 | 文本输入框 |
| `number` | 数值 | 数字输入框 |
| `select` | 单选 | 下拉选择框（需配合 `options`） |

`select` 类型需要额外的 `options` 字段：
```json
{
  "key": "priority",
  "label": "默认优先级",
  "type": "select",
  "default": "medium",
  "options": ["low", "medium", "high"]
}
```

## main.py 实现

### 最小示例

```python
"""My awesome plugin."""

from __future__ import annotations
import logging
from typing import Any
from app.plugins.base import BasePlugin, ToolResult

logger = logging.getLogger("jarvis")


class MyPlugin(BasePlugin):
    """A plugin that does something useful."""

    async def on_load(self) -> None:
        """插件加载时调用（仅一次）"""
        logger.info("MyPlugin loaded")

    async def on_enable(self) -> None:
        """插件启用时调用"""
        logger.info("MyPlugin enabled")

    async def on_disable(self) -> None:
        """插件禁用时调用"""
        logger.info("MyPlugin disabled")

    async def on_unload(self) -> None:
        """插件卸载时调用"""
        logger.info("MyPlugin unloaded")

    async def execute(self, command_name: str, args: dict[str, Any]) -> ToolResult:
        """命令分发入口"""
        if command_name == "my_command":
            return await self._handle_my_command(args)
        return ToolResult(f"Unknown command: {command_name}", success=False)

    async def _handle_my_command(self, args: dict[str, Any]) -> ToolResult:
        param1 = args.get("param1", "")
        return ToolResult(
            content=f"处理结果: {param1}",
            success=True,
            metadata={"input": param1}
        )
```

### ToolResult

所有命令处理函数必须返回 `ToolResult` 对象：

```python
ToolResult(
    content="展示给用户的文本内容",
    success=True,           # 是否成功
    metadata={"key": "val"} # 结构化数据（可选，供程序读取）
)
```

### 生命周期

```
on_load → on_enable → [execute 调用] → on_disable → on_unload
```

- `on_load`: 插件被加载到内存时调用，适合初始化资源
- `on_enable`: 插件被启用时调用，命令变为可用
- `on_disable`: 插件被禁用时调用，命令隐藏
- `on_unload`: 插件被卸载时调用，适合清理资源

### 读取配置

```python
async def execute(self, command_name: str, args: dict[str, Any]) -> ToolResult:
    config = await self.get_config()
    api_key = config.get("api_key", "")
    # ...
```

### 获取插件名称和版本

```python
async def on_load(self) -> None:
    logger.info("%s v%s loaded", self.name, self.version)
```

## 安装插件

### 方法一：直接放入目录

将插件目录复制到 `server/plugins/` 目录下，然后重启服务或通过 API 重载：

```bash
curl -X POST http://127.0.0.1:18200/api/plugins/my_plugin/reload
```

### 方法二：上传 ZIP 包

打包为 ZIP 文件（包含 plugin.json 和 main.py），通过 Web 界面或 API 上传：

```bash
curl -X POST http://127.0.0.1:18200/api/plugins/install \
  -F "zip_file=@my_plugin.zip"
```

ZIP 结构：
```
my_plugin.zip
  └── my_plugin/
      ├── plugin.json
      └── main.py
```

## API 参考

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/plugins` | 列出所有插件 |
| GET | `/api/plugins/{name}` | 获取插件详情 |
| POST | `/api/plugins/{name}/enable` | 启用插件 |
| POST | `/api/plugins/{name}/disable` | 禁用插件 |
| POST | `/api/plugins/{name}/reload` | 重载插件 |
| POST | `/api/plugins/{name}/uninstall` | 卸载插件 |
| POST | `/api/plugins/install` | 安装新插件（ZIP 上传） |
| GET | `/api/plugins/{name}/config` | 获取插件配置 |
| PUT | `/api/plugins/{name}/config` | 更新插件配置 |

## 最佳实践

1. **命令描述要清晰** — LLM 依赖 `description` 决定何时调用命令，描述越准确越好
2. **参数要有默认值** — 减少必填参数，让 LLM 更容易调用
3. **返回结构化 metadata** — 便于前端或其他程序解析结果
4. **异步处理耗时操作** — 使用 `asyncio` 避免阻塞事件循环
5. **错误处理** — 始终返回 `ToolResult(success=False, content="错误信息")`
6. **日志记录** — 使用 `logger = logging.getLogger("jarvis")` 统一日志格式

## 内置插件参考

查看 `server/plugins/` 目录下的天气、日历、提醒、系统监控、剪贴板插件获取完整示例。
