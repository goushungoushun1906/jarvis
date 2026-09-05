"""Vision tools for JARVIS — screenshot understanding and visual agent.

Phase 16 P2 implementation. Builds on top of the existing screenshot/OCR
infrastructure and adds LLM-based visual understanding plus a simple
visual-agent loop for cross-application automation.
"""

from __future__ import annotations

import base64
import io
import logging
import re
from typing import Any

from .tools import BaseTool, ToolResult, registry

logger = logging.getLogger("jarvis")

# ------------------------------------------------------------------
# Lazy imports / helpers
# ------------------------------------------------------------------


def _get_pil():
    try:
        from PIL import Image, ImageGrab
        return Image, ImageGrab
    except ImportError as e:
        logger.warning("Pillow not available: %s", e)
        return None, None


def _get_pyautogui():
    try:
        import pyautogui
        # Fail-safe: moving to corner aborts operations; disable for agent use
        pyautogui.FAILSAFE = True
        return pyautogui
    except ImportError:
        return None


def _get_ocr():
    """Lazy-load RapidOCR engine (downloads models on first use)."""
    try:
        from rapidocr_onnxruntime import RapidOCR
        return RapidOCR()
    except Exception as e:
        logger.warning("RapidOCR not available: %s", e)
        return None


def _capture_screenshot_base64(mode: str = "fullscreen", region: dict | None = None) -> tuple[str, tuple[int, int]]:
    """Capture a screenshot and return (base64_png, (width, height))."""
    Image, ImageGrab = _get_pil()
    if Image is None:
        raise RuntimeError("Screenshot tool requires Pillow.")

    if mode == "fullscreen":
        img = ImageGrab.grab()
    elif mode == "region":
        if not region:
            raise ValueError("region mode requires 'region' object")
        box = (
            region["left"],
            region["top"],
            region["left"] + region["width"],
            region["top"] + region["height"],
        )
        img = ImageGrab.grab(bbox=box)
    else:
        raise ValueError(f"Unknown screenshot mode: {mode}")

    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    b64 = base64.b64encode(buffer.getvalue()).decode("utf-8")
    return b64, img.size


def _image_message(b64: str, detail: str = "auto") -> dict:
    """Build an OpenAI-compatible image_url message part."""
    return {
        "type": "image_url",
        "image_url": {"url": f"data:image/png;base64,{b64}", "detail": detail},
    }


async def _call_vision_llm(messages: list[dict], max_tokens: int = 2048) -> str:
    """Call a vision-capable LLM and return the text response."""
    # Import here to avoid circular imports at module load time
    from .llm import call_llm

    # Force mid tier: vision tasks benefit from capable models (gpt-4o, claude, doubao)
    result = await call_llm(messages, stream=False, explicit_tier="mid")
    if isinstance(result, dict):
        # call_llm returns a plain string in non-streaming mode
        content = result.get("content", "")
    else:
        content = str(result)
    return content


# ------------------------------------------------------------------
# Vision Describe Tool
# ------------------------------------------------------------------


class VisionDescribeTool(BaseTool):
    name = "vision_describe"
    description = (
        "Capture the current screen (or a region) and return a structured "
        "description of what is visible: UI elements, text, layout, icons, "
        "charts, and interactive controls. Use this to understand the screen "
        "before taking action."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "mode": {
                "type": "string",
                "enum": ["fullscreen", "region"],
                "description": "Screenshot mode",
                "default": "fullscreen",
            },
            "region": {
                "type": "object",
                "properties": {
                    "left": {"type": "integer"},
                    "top": {"type": "integer"},
                    "width": {"type": "integer"},
                    "height": {"type": "integer"},
                },
                "description": "Region to capture when mode=region",
            },
            "question": {
                "type": "string",
                "description": "Optional specific question about the screenshot",
                "default": "",
            },
        },
        "required": [],
    }

    async def execute(
        self,
        mode: str = "fullscreen",
        region: dict | None = None,
        question: str = "",
    ) -> ToolResult:
        try:
            b64, size = _capture_screenshot_base64(mode, region)
        except Exception as e:
            logger.error("Vision describe screenshot error: %s", e)
            return ToolResult(f"Failed to capture screenshot: {e}", success=False)

        prompt = (
            "You are a screen-reading assistant. Describe the screenshot in a "
            "structured way. Include:\n"
            "1. Overall layout and current application/window\n"
            "2. Visible text (headings, labels, buttons, input fields)\n"
            "3. UI elements and their approximate positions\n"
            "4. Interactive controls (buttons, links, menus, text boxes)\n"
            "5. Any charts, images, tables, or alerts\n"
            "Be concise but complete. If coordinates are useful, describe them "
            "as percentages of screen width/height."
        )
        if question:
            prompt += f"\n\nSpecifically answer this question: {question}"

        messages = [
            {"role": "system", "content": "You are a helpful vision assistant that describes screenshots."},
            {"role": "user", "content": [ {"type": "text", "text": prompt}, _image_message(b64) ]},
        ]

        try:
            description = await _call_vision_llm(messages, max_tokens=2048)
            return ToolResult(
                description,
                metadata={"width": size[0], "height": size[1], "mode": mode},
            )
        except Exception as e:
            logger.error("Vision describe LLM error: %s", e)
            return ToolResult(f"Failed to describe screenshot: {e}", success=False)


# ------------------------------------------------------------------
# Vision Agent Tool
# ------------------------------------------------------------------


class VisionAgentTool(BaseTool):
    name = "vision_agent"
    description = (
        "Automate tasks by looking at the screen and interacting with it. "
        "Given a goal (e.g. 'open Notepad and type hello', 'click the Submit button'), "
        "the agent repeatedly screenshots, plans, and executes safe UI actions "
        "until the goal is achieved or the step limit is reached. "
        "Windows only; requires pyautogui."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "goal": {
                "type": "string",
                "description": "High-level task to accomplish on screen",
            },
            "max_steps": {
                "type": "integer",
                "description": "Maximum number of actions to attempt (default: 10)",
                "default": 10,
            },
        },
        "required": ["goal"],
    }

    async def execute(self, goal: str, max_steps: int = 10) -> ToolResult:
        pyautogui = _get_pyautogui()
        if pyautogui is None:
            return ToolResult("vision_agent requires pyautogui (pip install pyautogui).", success=False)

        max_steps = max(1, min(max_steps, 20))
        screen_size = pyautogui.size()
        actions_taken: list[str] = []

        for step in range(max_steps):
            try:
                b64, _ = _capture_screenshot_base64("fullscreen")
            except Exception as e:
                return ToolResult(f"Screenshot failed at step {step + 1}: {e}", success=False)

            plan_prompt = (
                f"You are a GUI automation agent. The user's goal is: '{goal}'.\n"
                f"Screen size: {screen_size.width}x{screen_size.height}.\n"
                f"Actions already taken: {actions_taken or 'none'}.\n\n"
                "Look at the screenshot and decide the next single action. "
                "Reply with exactly one line in this format:\n"
                "ACTION: <action_type> | <arguments>\n\n"
                "Supported actions:\n"
                "- click_text | <exact visible text on the target element>\n"
                "- click_coords | <x> | <y>\n"
                "- type_text | <text to type>\n"
                "- press_key | <key or combo, e.g. ctrl+a, enter, tab>\n"
                "- scroll | <up|down> | <clicks>\n"
                "- done | <optional summary>\n"
                "- fail | <reason>\n\n"
                "Choose coordinates as absolute pixel values. Be precise."
            )

            messages = [
                {"role": "system", "content": "You are a GUI automation planner. Only output one ACTION line."},
                {"role": "user", "content": [
                    {"type": "text", "text": plan_prompt},
                    _image_message(b64),
                ]},
            ]

            try:
                plan = await _call_vision_llm(messages, max_tokens=256)
            except Exception as e:
                return ToolResult(f"Planning failed at step {step + 1}: {e}", success=False)

            action_line = self._extract_action_line(plan)
            if action_line is None:
                actions_taken.append(f"step {step + 1}: no valid action (raw: {plan[:80]})")
                continue

            action_type, args = action_line
            actions_taken.append(f"step {step + 1}: {action_type}({', '.join(args)})")

            if action_type == "done":
                summary = args[0] if args else "Task completed."
                return ToolResult(f"Goal achieved.\n\nSummary: {summary}\n\nActions:\n" + "\n".join(actions_taken))

            if action_type == "fail":
                reason = args[0] if args else "Unknown reason."
                return ToolResult(f"Agent stopped: {reason}\n\nActions:\n" + "\n".join(actions_taken), success=False)

            result = self._execute_action(pyautogui, action_type, args, screen_size)
            if not result:
                return ToolResult(
                    f"Action execution failed at step {step + 1}: {action_type}({args})\n\nActions:\n" + "\n".join(actions_taken),
                    success=False,
                )

            # Short pause between actions to let UI respond
            import asyncio
            await asyncio.sleep(0.8)

        return ToolResult(
            f"Goal not achieved within {max_steps} steps.\n\nActions:\n" + "\n".join(actions_taken),
            success=False,
        )

    def _extract_action_line(self, plan: str) -> tuple[str, list[str]] | None:
        """Parse the ACTION: line from the LLM response."""
        for line in plan.splitlines():
            line = line.strip()
            if line.startswith("ACTION:"):
                parts = [p.strip() for p in line[len("ACTION:"):].split("|")]
                parts = [p for p in parts if p]
                if not parts:
                    return None
                return parts[0].lower(), parts[1:]
        return None

    def _execute_action(self, pyautogui, action_type: str, args: list[str], screen_size) -> bool:
        """Execute a single pyautogui action. Returns True on success."""
        try:
            if action_type == "click_text":
                if not args:
                    return False
                target_text = args[0]
                try:
                    # Capture fresh screenshot for OCR text localization
                    _, _ = _capture_screenshot_base64("fullscreen")
                    # Save to temp file for OCR engine
                    import tempfile
                    import os
                    temp_path = os.path.join(tempfile.gettempdir(), "jarvis_vision_agent_click.png")
                    Image, ImageGrab = _get_pil()
                    ImageGrab.grab().save(temp_path, "PNG")

                    engine = _get_ocr()
                    if engine is None:
                        return False

                    result = engine(temp_path)
                    detections: list[tuple[list, str, float]] = []
                    if hasattr(result, "txts") and hasattr(result, "boxes"):
                        for box, txt in zip(result.boxes, result.txts):
                            detections.append((box, txt, 1.0))
                    elif isinstance(result, (list, tuple)) and result:
                        raw = result[0] if isinstance(result[0], list) else result
                        detections = [
                            line for line in raw
                            if isinstance(line, (list, tuple)) and len(line) >= 2
                        ]

                    best = None
                    best_score = 0.0
                    target_lower = target_text.lower()
                    for det in detections:
                        box, text, score = det[0], det[1], (det[2] if len(det) > 2 else 1.0)
                        text_str = str(text)
                        if target_lower in text_str.lower():
                            # Prefer shorter/more exact matches
                            match_score = score / max(1, len(text_str))
                            if match_score > best_score:
                                best_score = match_score
                                best = box

                    if best is None:
                        logger.warning("click_text: could not find '%s' on screen", target_text)
                        return False

                    # box is usually [[x1,y1],[x2,y1],[x2,y2],[x1,y2]]
                    xs = [p[0] for p in best]
                    ys = [p[1] for p in best]
                    cx = int((min(xs) + max(xs)) / 2)
                    cy = int((min(ys) + max(ys)) / 2)
                    pyautogui.click(cx, cy)
                    return True
                except Exception as e:
                    logger.error("click_text error: %s", e)
                    return False

            if action_type == "click_coords":
                if len(args) < 2:
                    return False
                x, y = int(args[0]), int(args[1])
                pyautogui.click(x, y)
                return True

            if action_type == "type_text":
                if not args:
                    return False
                pyautogui.typewrite(args[0], interval=0.01)
                return True

            if action_type == "press_key":
                if not args:
                    return False
                combo = args[0].lower()
                keys = [k.strip() for k in combo.split("+")]
                if len(keys) == 1:
                    pyautogui.press(keys[0])
                else:
                    pyautogui.hotkey(*keys)
                return True

            if action_type == "scroll":
                direction = args[0].lower() if args else "down"
                clicks = int(args[1]) if len(args) > 1 else 3
                pyautogui.scroll(clicks if direction == "up" else -clicks)
                return True

            logger.warning("Unknown vision agent action: %s", action_type)
            return False
        except Exception as e:
            logger.error("Vision agent action error (%s): %s", action_type, e)
            return False


# ------------------------------------------------------------------
# Register
# ------------------------------------------------------------------


def register_vision_tools() -> None:
    """Register vision tools with the global registry."""
    registry.register(VisionDescribeTool())
    registry.register(VisionAgentTool())
