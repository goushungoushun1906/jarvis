"""System-level tools for JARVIS — screenshot/OCR, clipboard, monitoring, control.

Phase 10 implementation. These tools are Windows-aware but degrade gracefully
on other platforms where possible.
"""

from __future__ import annotations

import base64
import io
import logging
import os
import platform
import subprocess
import tempfile
from datetime import datetime, timezone
from typing import Any

from .tools import BaseTool, ToolResult, registry

logger = logging.getLogger("jarvis")

# ------------------------------------------------------------------
# Lazy imports / optional dependencies
# ------------------------------------------------------------------


def _get_pil():
    try:
        from PIL import Image, ImageGrab
        return Image, ImageGrab
    except ImportError as e:
        logger.warning("Pillow not available: %s", e)
        return None, None


def _get_ocr():
    """Lazy-load RapidOCR engine (downloads models on first use)."""
    try:
        from rapidocr_onnxruntime import RapidOCR
        logger.info("RapidOCR imported successfully")
        return RapidOCR()
    except Exception as e:
        logger.warning("RapidOCR not available: %s", e, exc_info=True)
        return None


def _get_pyautogui():
    try:
        import pyautogui
        return pyautogui
    except ImportError:
        return None


def _get_psutil():
    try:
        import psutil
        return psutil
    except ImportError:
        return None


def _get_pyperclip():
    try:
        import pyperclip
        return pyperclip
    except ImportError:
        return None


def _get_win32clipboard():
    try:
        import win32clipboard
        from io import BytesIO
        return win32clipboard, BytesIO
    except ImportError:
        return None, None


# ------------------------------------------------------------------
# Screenshot + OCR
# ------------------------------------------------------------------


class ScreenshotTool(BaseTool):
    name = "screenshot"
    description = (
        "Capture a screenshot of the screen. "
        "By default OCR is enabled so any visible text is extracted. "
        "Set 'ocr' to false only when the user explicitly asks for a plain image without text recognition. "
        "Supports full screen, active window, or a region. "
        "Returns image data and recognized text."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "mode": {
                "type": "string",
                "enum": ["fullscreen", "active_window", "region"],
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
                "description": "Region to capture (left, top, width, height) when mode=region",
            },
            "ocr": {
                "type": "boolean",
                "description": "Whether to run OCR and extract text from the screenshot. Defaults to true.",
                "default": True,
            },
        },
        "required": [],
    }

    async def execute(
        self,
        mode: str = "fullscreen",
        region: dict[str, int] | None = None,
        ocr: bool = True,
    ) -> ToolResult:
        Image, ImageGrab = _get_pil()
        if Image is None:
            return ToolResult("Screenshot tool requires Pillow (pip install pillow).", success=False)

        try:
            if mode == "fullscreen":
                img = ImageGrab.grab()
            elif mode == "active_window":
                pyautogui = _get_pyautogui()
                if pyautogui is None:
                    return ToolResult("active_window mode requires pyautogui.", success=False)
                # pyautogui screenshot of active window is not native; fallback to fullscreen
                window = pyautogui.getActiveWindow()
                if window is not None:
                    box = (window.left, window.top, window.right, window.bottom)
                    img = ImageGrab.grab(bbox=box)
                else:
                    img = ImageGrab.grab()
            elif mode == "region":
                if not region:
                    return ToolResult("region mode requires 'region' object.", success=False)
                box = (
                    region["left"],
                    region["top"],
                    region["left"] + region["width"],
                    region["top"] + region["height"],
                )
                img = ImageGrab.grab(bbox=box)
            else:
                return ToolResult(f"Unknown screenshot mode: {mode}", success=False)
        except Exception as e:
            logger.error("Screenshot capture error: %s", e)
            return ToolResult(f"Failed to capture screenshot: {e}", success=False)

        # Save to temp PNG
        temp_dir = tempfile.gettempdir()
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        filename = f"jarvis_screenshot_{timestamp}.png"
        filepath = os.path.join(temp_dir, filename)
        try:
            img.save(filepath, "PNG")
        except Exception as e:
            return ToolResult(f"Failed to save screenshot: {e}", success=False)

        # Encode to base64 for inline use
        try:
            buffer = io.BytesIO()
            img.save(buffer, format="PNG")
            b64 = base64.b64encode(buffer.getvalue()).decode("utf-8")
        except Exception as e:
            b64 = ""
            logger.warning("Failed to base64 encode screenshot: %s", e)

        result_parts = [
            f"Screenshot captured ({img.size[0]}x{img.size[1]}).",
            f"Saved to: {filepath}",
        ]
        if b64:
            result_parts.append(f"data:image/png;base64,{b64}")

        # Optional OCR
        ocr_text = ""
        logger.info("Screenshot OCR requested: %s", ocr)
        if ocr:
            engine = _get_ocr()
            logger.info("RapidOCR engine: %s", engine)
            if engine is None:
                ocr_text = "OCR requested but RapidOCR is not available."
            else:
                try:
                    result = engine(filepath)
                    # RapidOCR >= 3.x returns RapidOCROutput object with .txts
                    if hasattr(result, "txts"):
                        texts = [t for t in result.txts if t]
                    elif isinstance(result, (list, tuple)) and result:
                        # RapidOCR returns (detections, total_time); detections is a list of [box, text, score]
                        detections = result[0] if isinstance(result[0], list) else result
                        texts = [
                            line[1]
                            for line in detections
                            if isinstance(line, (list, tuple)) and len(line) > 1 and isinstance(line[1], str)
                        ]
                    else:
                        texts = []
                    ocr_text = "\n".join(texts) if texts else "(No text recognized)"
                except Exception as e:
                    logger.error("OCR error: %s", e, exc_info=True)
                    ocr_text = f"OCR failed: {e}"
            result_parts.append(f"OCR text:\n{ocr_text}")

        return ToolResult("\n\n".join(result_parts), metadata={
            "width": img.size[0],
            "height": img.size[1],
            "path": filepath,
            "base64_length": len(b64),
        })


# ------------------------------------------------------------------
# Clipboard
# ------------------------------------------------------------------


class ClipboardTool(BaseTool):
    name = "clipboard"
    description = (
        "Read from or write to the system clipboard. "
        "Supports text operations and reading image clipboard as base64."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["get_text", "set_text", "get_image"],
                "description": "Action to perform",
            },
            "text": {
                "type": "string",
                "description": "Text to write when action=set_text",
            },
        },
        "required": ["action"],
    }

    async def execute(self, action: str, text: str = "") -> ToolResult:
        if action == "get_text":
            pyperclip = _get_pyperclip()
            if pyperclip is None:
                return ToolResult("pyperclip not available.", success=False)
            try:
                value = pyperclip.paste()
                return ToolResult(value or "(clipboard is empty)")
            except Exception as e:
                return ToolResult(f"Failed to read clipboard: {e}", success=False)

        if action == "set_text":
            if not text:
                return ToolResult("Missing 'text' for set_text action.", success=False)
            pyperclip = _get_pyperclip()
            if pyperclip is None:
                return ToolResult("pyperclip not available.", success=False)
            try:
                pyperclip.copy(text)
                return ToolResult(f"Copied to clipboard: {text[:200]}{'...' if len(text) > 200 else ''}")
            except Exception as e:
                return ToolResult(f"Failed to write clipboard: {e}", success=False)

        if action == "get_image":
            return await self._get_image_clipboard()

        return ToolResult(f"Unknown action: {action}", success=False)

    async def _get_image_clipboard(self) -> ToolResult:
        if platform.system() != "Windows":
            return ToolResult("Reading image clipboard is only supported on Windows.", success=False)

        win32clipboard, BytesIO = _get_win32clipboard()
        if win32clipboard is None:
            return ToolResult("pywin32 not available for image clipboard.", success=False)

        try:
            win32clipboard.OpenClipboard()
            try:
                if not win32clipboard.IsClipboardFormatAvailable(win32clipboard.CF_DIB):
                    return ToolResult("No image data in clipboard.", success=False)
                data = win32clipboard.GetClipboardData(win32clipboard.CF_DIB)
            finally:
                win32clipboard.CloseClipboard()

            Image, _ = _get_pil()
            if Image is None:
                return ToolResult("Pillow not available.", success=False)

            # CF_DIB is a BITMAPINFOHEADER followed by pixel data
            image = Image.open(BytesIO(data))
            buffer = BytesIO()
            image.save(buffer, format="PNG")
            b64 = base64.b64encode(buffer.getvalue()).decode("utf-8")
            return ToolResult(
                f"Image clipboard ({image.size[0]}x{image.size[1]}).\ndata:image/png;base64,{b64}",
                metadata={"width": image.size[0], "height": image.size[1]},
            )
        except Exception as e:
            logger.error("Image clipboard error: %s", e)
            return ToolResult(f"Failed to read image clipboard: {e}", success=False)


# ------------------------------------------------------------------
# System Monitor
# ------------------------------------------------------------------


class SystemMonitorTool(BaseTool):
    name = "system_monitor"
    description = (
        "Get real-time system resource usage: CPU, RAM, disk, network, battery. "
        "Use this when the user asks about computer performance or resources."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "metrics": {
                "type": "array",
                "items": {
                    "type": "string",
                    "enum": ["cpu", "ram", "disk", "network", "battery", "all"],
                },
                "description": "Which metrics to return (default: all)",
                "default": ["all"],
            },
        },
        "required": [],
    }

    async def execute(self, metrics: list[str] | None = None) -> ToolResult:
        psutil = _get_psutil()
        if psutil is None:
            return ToolResult("system_monitor requires psutil (pip install psutil).", success=False)

        metrics = metrics or ["all"]
        if "all" in metrics:
            metrics = ["cpu", "ram", "disk", "network", "battery"]

        parts: list[str] = []
        meta: dict[str, Any] = {}

        try:
            if "cpu" in metrics:
                # interval=0 avoids blocking the event loop; accuracy improves on repeated calls
                cpu_percent = psutil.cpu_percent(interval=0.0)
                cpu_count = psutil.cpu_count(logical=True)
                freq = psutil.cpu_freq()
                parts.append(
                    f"CPU: {cpu_percent:.1f}% used across {cpu_count} logical cores"
                    + (f", frequency {freq.current:.0f} MHz" if freq else "")
                )
                meta["cpu_percent"] = cpu_percent
                meta["cpu_count"] = cpu_count

            if "ram" in metrics:
                mem = psutil.virtual_memory()
                parts.append(
                    f"RAM: {mem.percent:.1f}% used ({mem.used // (1024**3)} GB / {mem.total // (1024**3)} GB)"
                )
                meta["ram_percent"] = mem.percent
                meta["ram_used_gb"] = mem.used // (1024**3)
                meta["ram_total_gb"] = mem.total // (1024**3)

            if "disk" in metrics:
                disk = psutil.disk_usage("/")
                parts.append(
                    f"Disk: {disk.percent:.1f}% used ({disk.used // (1024**3)} GB / {disk.total // (1024**3)} GB)"
                )
                meta["disk_percent"] = disk.percent

            if "network" in metrics:
                net = psutil.net_io_counters()
                parts.append(
                    f"Network: sent {net.bytes_sent // 1024} KB, received {net.bytes_recv // 1024} KB"
                )
                meta["net_sent_kb"] = net.bytes_sent // 1024
                meta["net_recv_kb"] = net.bytes_recv // 1024

            if "battery" in metrics and hasattr(psutil, "sensors_battery"):
                batt = psutil.sensors_battery()
                if batt is not None:
                    status = "charging" if batt.power_plugged else "discharging"
                    parts.append(f"Battery: {batt.percent:.0f}% ({status})")
                    meta["battery_percent"] = batt.percent
                    meta["battery_plugged"] = batt.power_plugged
        except Exception as e:
            logger.error("System monitor error: %s", e)
            return ToolResult(f"Failed to read system metrics: {e}", success=False)

        return ToolResult("\n".join(parts), metadata=meta)


# ------------------------------------------------------------------
# System Control (Windows-centric)
# ------------------------------------------------------------------


class SystemControlTool(BaseTool):
    name = "system_control"
    description = (
        "Control basic system functions: adjust volume/mute, launch applications, "
        "lock screen, or shut down/restart/sleep. Windows platform features are used "
        "when available."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "set_volume",
                    "mute",
                    "unmute",
                    "launch_app",
                    "close_app",
                    "lock_screen",
                    "sleep",
                    "shutdown",
                    "restart",
                ],
                "description": "System control action",
            },
            "value": {
                "type": "integer",
                "description": "Volume level 0-100 for set_volume",
            },
            "target": {
                "type": "string",
                "description": "Application name/path for launch_app/close_app",
            },
        },
        "required": ["action"],
    }

    async def execute(
        self,
        action: str,
        value: int | None = None,
        target: str = "",
    ) -> ToolResult:
        if platform.system() != "Windows":
            # Degrade gracefully: launch_app works via subprocess everywhere
            if action == "launch_app" and target:
                return await self._launch_app(target)
            return ToolResult(f"Action '{action}' is only supported on Windows.", success=False)

        try:
            if action == "set_volume":
                if value is None or not 0 <= value <= 100:
                    return ToolResult("set_volume requires 'value' between 0 and 100.", success=False)
                return self._set_volume(value)
            if action == "mute":
                return self._set_volume(0, mute=True)
            if action == "unmute":
                return self._set_volume(None, mute=False)
            if action == "launch_app":
                if not target:
                    return ToolResult("launch_app requires 'target' path/name.", success=False)
                return await self._launch_app(target)
            if action == "close_app":
                if not target:
                    return ToolResult("close_app requires 'target' process name.", success=False)
                return await self._close_app(target)
            if action == "lock_screen":
                return self._lock_screen()
            if action == "sleep":
                return self._sleep()
            if action in ("shutdown", "restart"):
                return ToolResult(
                    f"Action '{action}' is disabled for safety. Use system settings manually.",
                    success=False,
                )
            return ToolResult(f"Unknown action: {action}", success=False)
        except Exception as e:
            logger.error("System control error (%s): %s", action, e)
            return ToolResult(f"System control failed: {e}", success=False)

    def _set_volume(self, value: int | None, mute: bool | None = None) -> ToolResult:
        try:
            from pycaw.pycaw import AudioUtilities
        except ImportError:
            # Fallback: use Windows nircmd or powershell
            return self._set_volume_powershell(value, mute)

        try:
            device = AudioUtilities.GetSpeakers()
            # pycaw >= 20251023 exposes EndpointVolume directly on AudioDevice
            if hasattr(device, "EndpointVolume"):
                volume = device.EndpointVolume
            else:
                from ctypes import cast, POINTER
                from comtypes import CLSCTX_ALL
                from pycaw.pycaw import IAudioEndpointVolume

                interface = device.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
                volume = cast(interface, POINTER(IAudioEndpointVolume))

            if mute is not None:
                volume.SetMute(1 if mute else 0, None)
                return ToolResult(f"System {'muted' if mute else 'unmuted'}.")
            if value is not None:
                volume.SetMasterVolumeLevelScalar(value / 100.0, None)
                return ToolResult(f"System volume set to {value}%.")
            return ToolResult("No volume change requested.")
        except Exception as e:
            return ToolResult(f"Volume control failed: {e}", success=False)

    def _set_volume_powershell(self, value: int | None, mute: bool | None = None) -> ToolResult:
        try:
            if mute is not None:
                cmd = f"(Get-WmiObject -Class Win32_SoundDevice).Mute = ${str(mute).lower()}"
                # Actually use simple approach via Windows key shortcut is not scriptable
                return ToolResult("pycaw not installed; cannot control volume via PowerShell reliably.", success=False)
            if value is not None:
                # Use Windows CoreAudio via powershell is complex; inform user
                return ToolResult("pycaw not installed; install pycaw for volume control.", success=False)
            return ToolResult("No volume change requested.")
        except Exception as e:
            return ToolResult(f"Volume control failed: {e}", success=False)

    async def _launch_app(self, target: str) -> ToolResult:
        import shutil

        original_target = target
        target_lower = target.lower()

        # Common app name -> executable name mappings
        aliases = {
            "腾讯会议": "wemeetapp.exe",
            "腾讯会议(uwp)": "wemeetapp.exe",
            "微信": "wechat.exe",
            "wechat": "wechat.exe",
            "qq": "qq.exe",
            "tim": "tim.exe",
            "chrome": "chrome.exe",
            "谷歌浏览器": "chrome.exe",
            "edge": "msedge.exe",
            "firefox": "firefox.exe",
            "火狐": "firefox.exe",
            "notepad": "notepad.exe",
            "记事本": "notepad.exe",
            "calc": "calc.exe",
            "计算器": "calc.exe",
            "explorer": "explorer.exe",
            "文件资源管理器": "explorer.exe",
        }
        executable_name = aliases.get(target_lower, target)

        # 1. Direct file path
        if os.path.isfile(executable_name):
            try:
                subprocess.Popen([executable_name], shell=False)
                return ToolResult(f"Launched: {original_target}")
            except Exception as e:
                return ToolResult(f"Failed to launch {original_target}: {e}", success=False)

        # 2. Executable available in PATH
        exe_path = shutil.which(executable_name)
        if exe_path:
            try:
                subprocess.Popen([exe_path], shell=False)
                return ToolResult(f"Launched: {original_target} ({exe_path})")
            except Exception as e:
                return ToolResult(f"Failed to launch {original_target}: {e}", success=False)

        # 3. Try os.startfile for shortcuts/documents/registered apps
        try:
            os.startfile(original_target)
            return ToolResult(f"Launched: {original_target}")
        except Exception:
            pass

        # 4. Search common install locations and Start Menu via PowerShell
        found_path = await self._find_app_via_powershell(target_lower, executable_name)
        if found_path:
            try:
                subprocess.Popen([found_path], shell=False)
                return ToolResult(f"Launched: {original_target} ({found_path})")
            except Exception as e:
                return ToolResult(f"Found but failed to launch {original_target}: {e}", success=False)

        return ToolResult(
            f"Could not find '{original_target}'. Please provide the full path to the executable.",
            success=False,
        )

    async def _find_app_via_powershell(self, display_name: str, exe_name: str | None = None) -> str | None:
        """Search Start Menu shortcuts and common install directories for the target app."""
        # Build targeted keyword list from display name and executable name
        keywords: list[str] = [display_name]
        if exe_name and exe_name != display_name:
            keywords.append(exe_name)
        alias_map = {
            "腾讯会议": ["腾讯会议", "WeMeet", "Tencent Meeting"],
            "微信": ["微信", "WeChat"],
            "wechat": ["微信", "WeChat"],
            "qq": ["QQ", "腾讯 QQ"],
            "tim": ["TIM"],
            "chrome": ["Chrome", "Google Chrome", "谷歌浏览器"],
            "谷歌浏览器": ["Chrome", "Google Chrome", "谷歌浏览器"],
            "edge": ["Edge", "Microsoft Edge"],
            "firefox": ["Firefox", "Mozilla Firefox", "火狐"],
            "火狐": ["Firefox", "Mozilla Firefox", "火狐"],
        }
        if display_name in alias_map:
            keywords.extend(alias_map[display_name])
        # Clean up keywords for PowerShell (remove .exe, escape)
        keywords = [k.replace(".exe", "").strip() for k in keywords if k.strip()]
        seen = set()
        clean_keywords = []
        for k in keywords:
            if k.lower() not in seen:
                seen.add(k.lower())
                clean_keywords.append(k)
        if not clean_keywords:
            clean_keywords = [display_name]
        ps_keywords = ", ".join(f'"{k.replace("\\", "\\\\").replace('"', '`"')}"' for k in clean_keywords)

        try:
            ps_script = f'''
$keywords = @({ps_keywords})

# Helper: resolve a .lnk file to its target
function Resolve-ShortcutTarget($lnkPath) {{
    try {{
        $shell = New-Object -ComObject WScript.Shell
        $target = $shell.CreateShortcut($lnkPath).TargetPath
        if ($target -and (Test-Path $target)) {{ return $target }}
    }} catch {{}}
    return $null
}}

# 1. Search Start Menu shortcuts first (fastest and most reliable)
$shortcuts = @(
    "$env:APPDATA\\Microsoft\\Windows\\Start Menu\\Programs",
    "$env:PROGRAMDATA\\Microsoft\\Windows\\Start Menu\\Programs"
)
foreach ($dir in $shortcuts) {{
    if (-not (Test-Path $dir)) {{ continue }}
    $lnks = Get-ChildItem -Path $dir -Recurse -Filter "*.lnk" -ErrorAction SilentlyContinue
    foreach ($lnk in $lnks) {{
        foreach ($kw in $keywords) {{
            if ($lnk.BaseName -like "*$kw*") {{
                $target = Resolve-ShortcutTarget $lnk.FullName
                if ($target) {{ Write-Output $target; exit }}
            }}
        }}
    }}
}}

# 2. Search common install directories on all fixed drives
$fixed_drives = Get-CimInstance Win32_LogicalDisk | Where-Object {{ $_.DriveType -eq 3 }} | Select-Object -ExpandProperty DeviceID
$common_paths = @(
    "Tencent\\WeMeet",
    "Tencent\\WeChat",
    "Tencent\\QQ",
    "Tencent\\TIM",
    "Google\\Chrome\\Application",
    "Mozilla Firefox",
    "Microsoft\\Edge\\Application",
    "Microsoft\\Edge"
)
foreach ($drive in $fixed_drives) {{
    foreach ($rel in $common_paths) {{
        $roots = @(
            "$drive\\$rel",
            "$drive\\Program Files\\$rel",
            "$drive\\Program Files (x86)\\$rel",
            "$drive\\$rel\\Application"
        )
        foreach ($root in $roots) {{
            if (Test-Path $root) {{
                $found = Get-ChildItem -Path $root -Recurse -Filter $name -ErrorAction SilentlyContinue | Select-Object -First 1
                if ($found) {{ Write-Output $found.FullName; exit }}
            }}
        }}
    }}
}}

# 3. Search WindowsApps folders
$windows_apps = @(
    "$env:LOCALAPPDATA\\Microsoft\\WindowsApps",
    "$env:PROGRAMFILES\\Microsoft\\WindowsApps"
)
foreach ($root in $windows_apps) {{
    if (Test-Path $root) {{
        $found = Get-ChildItem -Path $root -Recurse -Filter $name -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($found) {{ Write-Output $found.FullName; exit }}
    }}
}}
'''
            proc = subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps_script],
                capture_output=True,
                text=True,
                timeout=20,
            )
            path = proc.stdout.strip()
            if path and os.path.exists(path):
                return path
            if proc.stderr.strip():
                logger.debug("PowerShell app search stderr: %s", proc.stderr.strip())
        except Exception as e:
            logger.warning("PowerShell app search failed: %s", e)
        return None

    async def _close_app(self, target: str) -> ToolResult:
        psutil = _get_psutil()
        if psutil is None:
            return ToolResult("close_app requires psutil.", success=False)
        try:
            killed = 0
            name_lower = target.lower()
            for proc in psutil.process_iter(["pid", "name"]):
                if proc.info["name"] and proc.info["name"].lower() == name_lower:
                    proc.kill()
                    killed += 1
            if killed:
                return ToolResult(f"Closed {killed} instance(s) of {target}.")
            return ToolResult(f"No running process named {target} found.", success=False)
        except Exception as e:
            return ToolResult(f"Failed to close {target}: {e}", success=False)

    def _lock_screen(self) -> ToolResult:
        try:
            import ctypes
            ctypes.windll.user32.LockWorkStation()
            return ToolResult("Screen locked.")
        except Exception as e:
            return ToolResult(f"Failed to lock screen: {e}", success=False)

    def _sleep(self) -> ToolResult:
        try:
            subprocess.run(["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"], check=False)
            return ToolResult("Sleep initiated.")
        except Exception as e:
            return ToolResult(f"Failed to sleep: {e}", success=False)


# ------------------------------------------------------------------
# Register
# ------------------------------------------------------------------


def register_system_tools() -> None:
    """Register all system control tools."""
    registry.register(ScreenshotTool())
    registry.register(ClipboardTool())
    registry.register(SystemMonitorTool())
    registry.register(SystemControlTool())
