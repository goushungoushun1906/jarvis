"""Agent tool framework for JARVIS — register, discover, and execute tools."""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod

logger = logging.getLogger("jarvis")


class ToolResult:
    """Result of a tool execution."""

    def __init__(self, content: str, success: bool = True, metadata: dict = None):
        self.content = content
        self.success = success
        self.metadata = metadata or {}

    def to_dict(self) -> dict:
        return {
            "content": self.content,
            "success": self.success,
            "metadata": self.metadata,
        }


class BaseTool(ABC):
    """Base class for all JARVIS tools."""

    name: str = ""
    description: str = ""
    parameters_schema: dict = {}  # JSON Schema

    @abstractmethod
    async def execute(self, **kwargs) -> ToolResult:
        ...

    def get_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters_schema,
            },
        }


class ToolRegistry:
    """Registry for all available tools."""

    def __init__(self):
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        self._tools[tool.name] = tool
        logger.info("Tool registered: %s", tool.name)

    def get(self, name: str) -> BaseTool | None:
        return self._tools.get(name)

    def list_tools(self) -> list[BaseTool]:
        return list(self._tools.values())

    def get_openai_tools(self) -> list[dict]:
        """Return tools in OpenAI function-calling format."""
        return [t.get_schema() for t in self._tools.values()]


# Global registry
registry = ToolRegistry()
