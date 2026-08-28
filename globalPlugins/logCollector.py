# -*- coding: UTF-8 -*-
# NVDA Add-on: Log Collector
# Collects and copies errors/tracebacks from NVDA log files.

import io
import logging
import os
import platform
import re
import sys
import tempfile
import time

import addonHandler
import config
import globalPluginHandler
import gui
import logHandler
import ui
import wx
from gui import guiHelper
from gui.settingsDialogs import SettingsPanel
from logHandler import log
from scriptHandler import script

addonHandler.initTranslation()

MODULE_NAME = "logCollector"
DEFAULT_CONTEXT = 2
MAX_BLOCKS = 500

CAPTURE_HEADER = "=== NVDA Debug Log Capture ==="
CAPTURE_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"

HEADER_RE = re.compile(r"^(DEBUG|INFO|WARNING|ERROR|CRITICAL)\s+-\s+.*", re.M)
ERR_START_RE = re.compile(r"^(ERROR|CRITICAL)\s+-\s+.*", re.M)
WARNING_START_RE = re.compile(r"^WARNING\s+-\s+.*", re.M)
TRACE_START_RE = re.compile(r"^Traceback \(most recent call last\):", re.M)

try:
    import buildVersion
except Exception:
    buildVersion = None


def _initConfiguration():
    try:
        config.conf.spec[MODULE_NAME] = {
            "includeWarnings": "boolean( default=False)",
            "contextLines": "integer( default=2, min=0, max=20)",
            "currentOnly": "boolean( default=False)",
        }
    except Exception:
        pass


_initConfiguration()


def _cfg(key):
    defaults = {
        "includeWarnings": False,
        "contextLines": DEFAULT_CONTEXT,
        "currentOnly": False,
    }
    try:
        return config.conf[MODULE_NAME][key]
    except Exception:
        return defaults[key]


def _get_nvda_version():
    try:
        if hasattr(buildVersion, "version"):
            return str(buildVersion.version)
        parts = []
        for name in ("version_year", "version_major", "version_minor", "releaseName"):
            if hasattr(buildVersion, name):
                parts.append(str(getattr(buildVersion, name)))
        return "/".join([p for p in parts if p]) or "unknown"
    except Exception:
        return "unknown"


def _get_os_summary():
    try:
        win_version = sys.getwindowsversion()
        return f"Windows {win_version.major}.{win_version.minor}.{win_version.build} ({platform.release()})"
    except Exception:
        return platform.platform()


def _get_addons_list():
    items = []
    try:
        get_running = getattr(addonHandler, "getRunningAddons", None)
        if callable(get_running):
            addons = list(get_running())
        else:
            get_available = getattr(addonHandler, "getAvailableAddons", None)
            addons = list(get_available()) if callable(get_available) else []
        for addon in addons:
            try:
                manifest = getattr(addon, "manifest", {}) or {}
                name = manifest.get("name") or getattr(addon, "name", None) or "unknown"
                summary = manifest.get("summary", "")
                version = manifest.get("version", "")
                state = "enabled"
                if getattr(addon, "isDisabled", False):
                    state = "disabled"
                label = f"- {name} {version}".rstrip()
                if summary:
                    label = f"{label} ({summary})"
                items.append(f"{label} [{state}]")
            except Exception:
                items.append(f"- {repr(addon)}")
    except Exception as error:
        items.append(f"(failed to enumerate add-ons: {error})")
    return "\n".join(items)


def _get_log_paths(currentOnly=False):
    paths = []
    try:
        temp_dir = tempfile.gettempdir()
        paths.append(os.path.join(temp_dir, "nvda.log"))
        if not currentOnly:
            paths.append(os.path.join(temp_dir, "nvda-old.log"))
    except Exception:
        pass

    seen = set()
    result = []
    for path in paths:
        if path and path not in seen and os.path.isfile(path):
            result.append(path)
            seen.add(path)
    return result


def _read_text(path):
    try:
        with io.open(path, "r", encoding="utf-8", errors="replace") as handle:
            return handle.read()
    except Exception:
        try:
            with io.open(path, "r", encoding="mbcs", errors="replace") as handle:
                return handle.read()
        except Exception:
            return ""


def _read_log_segment(path, start=0, end=None):
    start = start or 0
    if end is not None and end < 0:
        end = None
    try:
        with open(path, "rb") as handle:
            file_size = handle.seek(0, os.SEEK_END)
            if end is None or end > file_size:
                end = file_size
            if start < 0 or (end is not None and end < start):
                start = 0
            read_len = max(0, (end or 0) - start) if end is not None else max(0, file_size - start)
            handle.seek(start if start else 0)
            data = handle.read(read_len if end is not None else -1)
    except Exception:
        return ""

    if not data:
        return ""

    for encoding in ("utf-8", "mbcs", "latin-1"):
        try:
            return data.decode(encoding, errors="replace")
        except Exception:
            continue

    try:
        return data.decode(errors="replace")
    except Exception:
        return ""


def _format_capture_output(segments, start_ts, end_ts):
    if not segments:
        return ""

    lines = [CAPTURE_HEADER]
    try:
        if start_ts:
            lines.append(f"Start: {time.strftime(CAPTURE_TIME_FORMAT, time.localtime(start_ts))}")
        if end_ts:
            lines.append(f"End:   {time.strftime(CAPTURE_TIME_FORMAT, time.localtime(end_ts))}")
    except Exception:
        pass

    lines.append("")
    for path, text in segments:
        lines.append(f"[{os.path.basename(path) if path else 'log'}]")
        stripped = text.rstrip()
        if stripped:
            lines.append(stripped)
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _collect_blocks(text, includeWarnings=False, context=DEFAULT_CONTEXT):
    lines = text.splitlines(True)
    is_header = [bool(HEADER_RE.match(line)) for line in lines]
    blocks = []
    total_lines = len(lines)

    for index, line in enumerate(lines):
        if not (ERR_START_RE.match(line) or (includeWarnings and WARNING_START_RE.match(line))):
            continue

        end = index + 1
        while end < total_lines and not is_header[end]:
            end += 1

        start = max(0, index - context)
        blocks.append("".join(lines[start:end]).rstrip())

    cursor = 0
    while True:
        match = TRACE_START_RE.search(text, cursor)
        if not match:
            break

        start_line = text.rfind("\n", 0, match.start()) + 1
        end = match.end()
        while True:
            newline = text.find("\n", end)
            if newline == -1:
                end = len(text)
                break
            current_line = text[end:newline]
            if HEADER_RE.match(current_line):
                break
            end = newline + 1

        blocks.append(text[start_line:end].rstrip())
        cursor = end

    seen = set()
    unique = []
    for block in blocks:
        if block and block not in seen:
            unique.append(block)
            seen.add(block)

    return unique[:MAX_BLOCKS]


def _build_report(includeWarnings=None, context=None, currentOnly=None):
    includeWarnings = _cfg("includeWarnings") if includeWarnings is None else includeWarnings
    context = _cfg("contextLines") if context is None else context
    currentOnly = _cfg("currentOnly") if currentOnly is None else currentOnly

    parts = []
    try:
        parts.append("=== NVDA Debug Report ===")
        parts.append(time.strftime("Generated: %Y-%m-%d %H:%M:%S"))
        parts.append(f"NVDA: {_get_nvda_version()}")
        parts.append(f"Python: {sys.version.split()[0]}")
        parts.append(f"OS: {_get_os_summary()}")

        try:
            general = config.conf["general"]
            if hasattr(general, "get"):
                log_level = general.get("logLevel", general.get("loggingLevel", None))
            else:
                log_level = general["logLevel"]
            parts.append(f"Log level: {log_level}")
        except Exception:
            pass

        try:
            synth = config.conf["speech"].get("synth", None)
            parts.append(f"Synth: {synth}")
        except Exception:
            pass

        parts.append("")
        parts.append("-- Add-ons --")
        parts.append(_get_addons_list())
    except Exception:
        parts.append("(failed to gather environment info)")

    parts.append("")
    parts.append("-- Collected log errors and tracebacks --")

    paths = _get_log_paths(currentOnly=currentOnly)
    if not paths:
        parts.append("(no log files found; try Tools -> View log to ensure logging is enabled)")

    for path in paths:
        parts.append("")
        parts.append(f"[Source: {path}]")
        text = _read_text(path)
        if not text:
            parts.append("(file is empty or unreadable)")
            continue

        blocks = _collect_blocks(text, includeWarnings=includeWarnings, context=context)
        if not blocks:
            parts.append("(no ERROR/CRITICAL entries found)")
        else:
            parts.append("\n\n".join(blocks))

    return "\n".join(parts).strip() + "\n"


class _ReportDialog(wx.Dialog):
    def __init__(self, parent, reportText):
        super().__init__(parent, title=_("Log Collector"))
        self.reportText = reportText
        self._buildUi()

    def _buildUi(self):
        mainSizer = wx.BoxSizer(wx.VERTICAL)
        self.textCtrl = wx.TextCtrl(
            self,
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_DONTWRAP,
        )
        self.textCtrl.SetValue(self.reportText)
        mainSizer.Add(self.textCtrl, 1, wx.EXPAND | wx.ALL, 8)

        btnSizer = wx.BoxSizer(wx.HORIZONTAL)
        copyBtn = wx.Button(self, wx.ID_ANY, _("Copy to clipboard"))
        saveBtn = wx.Button(self, wx.ID_SAVE, _("Save..."))
        closeBtn = wx.Button(self, wx.ID_CLOSE, _("Close"))
        btnSizer.Add(copyBtn, 0, wx.RIGHT, 6)
        btnSizer.Add(saveBtn, 0, wx.RIGHT, 6)
        btnSizer.Add(closeBtn, 0)
        mainSizer.Add(btnSizer, 0, wx.ALIGN_RIGHT | wx.ALL, 8)

        self.Bind(wx.EVT_BUTTON, self.onCopy, copyBtn)
        self.Bind(wx.EVT_BUTTON, self.onSave, saveBtn)
        self.Bind(wx.EVT_BUTTON, self.onClose, closeBtn)
        self.Bind(wx.EVT_CLOSE, self.onClose)
        self.Bind(wx.EVT_CHAR_HOOK, self.onCharHook)
        self.SetSizerAndFit(mainSizer)
        self.Maximize(True)

    def onCopy(self, evt):
        try:
            import api

            api.copyToClip(self.reportText)
            ui.message(_("Report copied to clipboard"))
        except Exception:
            ui.message(_("Failed to copy to clipboard"))

    def onSave(self, evt):
        with wx.FileDialog(
            self,
            _("Save report"),
            wildcard=_("Text file (*.txt)|*.txt"),
            style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT,
        ) as dlg:
            if dlg.ShowModal() == wx.ID_CANCEL:
                return
            path = dlg.GetPath()

        try:
            with io.open(path, "w", encoding="utf-8") as handle:
                handle.write(self.reportText)
            ui.message(_("Saved"))
        except Exception:
            ui.message(_("Failed to save"))

    def onCharHook(self, evt):
        if evt.GetKeyCode() == wx.WXK_ESCAPE:
            self.onClose(evt)
            return
        evt.Skip()

    def onClose(self, evt):
        try:
            if self.IsModal():
                self.EndModal(wx.ID_CLOSE)
            else:
                self.Destroy()
        except Exception:
            self.Destroy()


class LogCollectorSettings(SettingsPanel):
    title = _("Log Collector")

    def makeSettings(self, settingsSizer):
        helper = guiHelper.BoxSizerHelper(self, sizer=settingsSizer)
        self.includeWarnings = helper.addItem(wx.CheckBox(self, label=_("Include warnings")))
        self.includeWarnings.Value = bool(_cfg("includeWarnings"))

        self.currentOnly = helper.addItem(
            wx.CheckBox(self, label=_("Only current session (skip previous log)"))
        )
        self.currentOnly.Value = bool(_cfg("currentOnly"))

        self.contextSpin = helper.addLabeledControl(_("Context lines per error"), wx.SpinCtrl)
        self.contextSpin.SetRange(0, 20)
        self.contextSpin.Value = int(_cfg("contextLines"))

    def onSave(self):
        config.conf[MODULE_NAME]["includeWarnings"] = bool(self.includeWarnings.Value)
        config.conf[MODULE_NAME]["currentOnly"] = bool(self.currentOnly.Value)
        config.conf[MODULE_NAME]["contextLines"] = int(self.contextSpin.Value)


class GlobalPlugin(globalPluginHandler.GlobalPlugin):
    scriptCategory = _("Log Collector")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._menuItem = None
        self._menuHost = None
        self._menuRetryCount = 0

        self._debugCaptureActive = False
        self._debugCaptureLoggerStates = []
        self._debugCapturePaths = []
        self._debugCaptureOffsets = {}
        self._debugCaptureConfigLevels = None
        self._debugCaptureStartTime = 0.0

        wx.CallAfter(self._attachMenuSafely)
        try:
            categories = gui.settingsDialogs.NVDASettingsDialog.categoryClasses
            if LogCollectorSettings not in categories:
                categories.append(LogCollectorSettings)
        except Exception:
            pass

    def terminate(self):
        if self._debugCaptureActive:
            self._restore_debug_logging_state()

        try:
            categories = gui.settingsDialogs.NVDASettingsDialog.categoryClasses
            if LogCollectorSettings in categories:
                categories.remove(LogCollectorSettings)
        except Exception:
            pass

        self._detachMenu()

    def _attachMenuSafely(self):
        if self._menuItem is not None:
            return

        try:
            if not hasattr(gui, "mainFrame") or gui.mainFrame is None:
                raise RuntimeError("mainFrame not ready")
            sysTray = getattr(gui.mainFrame, "sysTrayIcon", None)
            if not sysTray or not hasattr(sysTray, "toolsMenu"):
                raise RuntimeError("toolsMenu not ready")

            self._menuItem = sysTray.toolsMenu.Append(wx.ID_ANY, _("Collect log errors."))
            sysTray.Bind(wx.EVT_MENU, self._onCollect, self._menuItem)
            self._menuHost = sysTray
        except Exception:
            self._menuRetryCount += 1
            if self._menuRetryCount < 30:
                wx.CallLater(500, self._attachMenuSafely)

    def _detachMenu(self):
        if self._menuHost is not None and self._menuItem is not None:
            try:
                self._menuHost.Unbind(wx.EVT_MENU, handler=self._onCollect, source=self._menuItem)
            except Exception:
                pass

            tools_menu = getattr(self._menuHost, "toolsMenu", None)
            if tools_menu is not None:
                try:
                    tools_menu.Remove(self._menuItem)
                except Exception:
                    try:
                        tools_menu.Remove(self._menuItem.GetId())
                    except Exception:
                        pass

        self._menuItem = None
        self._menuHost = None

    @staticmethod
    def _showReport(text):
        frame = getattr(gui, "mainFrame", None)
        if frame is None:
            return
        frame.prePopup()
        try:
            dialog = _ReportDialog(frame, text)
            dialog.Show()
        finally:
            frame.postPopup()

    def _onCollect(self, evt):
        self.script_collect(None)

    def _iter_target_loggers(self):
        seen = set()
        candidates = []

        try:
            candidates.append(log)
        except Exception:
            pass

        try:
            candidates.append(logging.getLogger())
        except Exception:
            pass

        for name in ("NVDA", "nvda"):
            try:
                candidates.append(logging.getLogger(name))
            except Exception:
                pass

        for logger in candidates:
            if not logger:
                continue
            identity = id(logger)
            if identity in seen:
                continue
            seen.add(identity)
            yield logger

    @staticmethod
    def _snapshot_logger_state(logger):
        if not logger:
            return None

        try:
            level = getattr(logger, "level", None)
        except Exception:
            level = None

        handler_levels = []
        for handler in getattr(logger, "handlers", []):
            try:
                handler_levels.append((handler, getattr(handler, "level", None)))
            except Exception:
                handler_levels.append((handler, None))

        return (logger, level, handler_levels)

    @staticmethod
    def _apply_logger_level(logger, level):
        if not logger:
            return

        try:
            if hasattr(logger, "setLevel"):
                logger.setLevel(level)
        except Exception:
            pass

        for handler in getattr(logger, "handlers", []):
            try:
                if hasattr(handler, "setLevel"):
                    handler.setLevel(level)
            except Exception:
                continue

    def _enable_debug_loggers(self):
        states = []
        for logger in self._iter_target_loggers():
            state = self._snapshot_logger_state(logger)
            if state:
                states.append(state)
            self._apply_logger_level(logger, logging.DEBUG)
        self._debugCaptureLoggerStates = states

        saved = {}
        general = None
        try:
            general = config.conf["general"]
        except Exception:
            general = None

        if general is not None:
            for key in ("loggingLevel", "logLevel", "consoleLogLevel"):
                had_key = False
                previous = None
                try:
                    if hasattr(general, "get"):
                        if key in general:
                            previous = general.get(key)
                            had_key = True
                    else:
                        previous = general[key]
                        had_key = True
                except Exception:
                    previous = None

                saved[key] = (had_key, previous)
                try:
                    general[key] = "DEBUG"
                except Exception:
                    pass

            try:
                if hasattr(logHandler, "setLogLevelFromConfig"):
                    logHandler.setLogLevelFromConfig()
                elif hasattr(logHandler, "setLogLevel"):
                    try:
                        logHandler.setLogLevel("DEBUG")
                    except Exception:
                        logHandler.setLogLevel(logging.DEBUG)
            except Exception:
                pass

        self._debugCaptureConfigLevels = saved if saved else None

    def _restore_debug_logging_state(self):
        general = None
        try:
            general = config.conf["general"]
        except Exception:
            general = None

        if self._debugCaptureConfigLevels and general is not None:
            for key, (had_key, value) in self._debugCaptureConfigLevels.items():
                try:
                    if had_key:
                        general[key] = value
                    elif key in general:
                        del general[key]
                except Exception:
                    pass

            try:
                if hasattr(logHandler, "setLogLevelFromConfig"):
                    logHandler.setLogLevelFromConfig()
            except Exception:
                pass

        self._debugCaptureConfigLevels = None

        if self._debugCaptureLoggerStates:
            for logger, level, handler_levels in self._debugCaptureLoggerStates:
                try:
                    if hasattr(logger, "setLevel"):
                        logger.setLevel(level if level is not None else logging.NOTSET)
                except Exception:
                    pass

                for handler, handler_level in handler_levels or []:
                    try:
                        if hasattr(handler, "setLevel"):
                            handler.setLevel(handler_level if handler_level is not None else logging.NOTSET)
                    except Exception:
                        continue

        self._debugCaptureLoggerStates = []
        self._debugCaptureActive = False
        self._debugCapturePaths = []
        self._debugCaptureOffsets = {}
        self._debugCaptureStartTime = 0.0

    def _start_debug_capture(self):
        if self._debugCaptureActive:
            return

        try:
            self._enable_debug_loggers()
        except Exception:
            self._restore_debug_logging_state()
            ui.message(_("Failed to enable debug log capture"))
            return

        paths = _get_log_paths(currentOnly=True)
        if not paths:
            try:
                paths = [os.path.join(tempfile.gettempdir(), "nvda.log")]
            except Exception:
                paths = []

        offsets = {}
        for path in paths:
            try:
                offsets[path] = os.path.getsize(path)
            except Exception:
                offsets[path] = 0

        self._debugCapturePaths = paths
        self._debugCaptureOffsets = offsets
        self._debugCaptureStartTime = time.time()
        self._debugCaptureActive = True

        if paths:
            ui.message(_("Debug logging enabled; capture started"))
        else:
            ui.message(_("Debug logging enabled; log file not found yet"))

    def _finish_debug_capture(self):
        if not self._debugCaptureActive:
            ui.message(_("Debug log capture was not active"))
            return

        paths = list(self._debugCapturePaths)
        offsets = dict(self._debugCaptureOffsets)
        start_ts = self._debugCaptureStartTime

        end_offsets = {}
        for path in paths:
            try:
                end_offsets[path] = os.path.getsize(path)
            except Exception:
                end_offsets[path] = None

        end_ts = time.time()
        self._restore_debug_logging_state()

        segments = []
        for path in paths:
            text = _read_log_segment(path, start=offsets.get(path, 0), end=end_offsets.get(path))
            if text:
                segments.append((path, text))

        if not segments:
            try:
                import api

                api.copyToClip("")
            except Exception:
                pass
            ui.message(_("No new log entries captured"))
            return

        output = _format_capture_output(segments, start_ts, end_ts)
        try:
            import api

            api.copyToClip(output)
            ui.message(_("Debug log capture copied to clipboard"))
        except Exception:
            ui.message(_("Failed to copy debug log capture"))

    @script(
        description=_("Collect errors/tracebacks from NVDA logs and show a copyable report"),
        gestures=["kb:NVDA+shift+l"],
    )
    def script_collect(self, gesture):
        try:
            text = _build_report()
            self._showReport(text)
            try:
                import api

                api.copyToClip(text)
                ui.message(_("Report copied to clipboard"))
            except Exception:
                pass
        except Exception:
            log.debugWarning("LogCollector: failed to build report", exc_info=True)
            ui.message(_("Failed to collect log report"))

    @script(
        description=_("Toggle debug log capture; press once to start, once to copy captured debug log to clipboard"),
        gestures=["kb:NVDA+shift+d"],
    )
    def script_toggleDebugCapture(self, gesture):
        if self._debugCaptureActive:
            self._finish_debug_capture()
        else:
            self._start_debug_capture()
