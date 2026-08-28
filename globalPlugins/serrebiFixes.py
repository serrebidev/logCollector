# -*- coding: UTF-8 -*-
# NVDA Add-on: Serrebi Fixes
# Monkeypatches for NVDA core bugs observed in the wild.
#
# Fix 1: review-mode position functions crash when the navigator object is None.
# Seen on NVDA 2026.2beta7 pressing NVDA+space during a focus transition:
#   scriptHandler.executeScript
#   -> globalCommands.script_toggleVirtualBufferPassThrough
#   -> treeInterceptorHandler._set_passThrough
#   -> review.setCurrentMode -> review.getObjectPosition
#   AttributeError: 'NoneType' object has no attribute 'makeTextInfo'
# Core's setCurrentMode already treats a None position result as "mode change
# unavailable" (it checks `if pos:`), so returning None when the object is None
# degrades gracefully: the browse/focus mode toggle still happens, only the
# review-position update is skipped for that keypress.
#
# Fix 2: wxPython assertion failures poison unrelated Python callbacks.
# wx.App's default assert mode includes wxAPP_ASSERT_EXCEPTION. In that mode
# wxPython reports a failed C++ assertion by calling PyErr_SetObject from the
# C++ event loop, where there is no Python frame to receive it. The exception
# just sits pending until the next piece of Python code returns to the
# interpreter, which then blames that innocent code:
#   wx._core.wxAssertionError: C++ assertion "m_menuDepth > 0" failed at
#   ..\..\src\msw\frame.cpp(434) in wxFrame::DoSendMenuOpenCloseEvent()
#   The above exception was the direct cause of the following exception:
#     IAccessibleHandler\internalWinEventHandler.pyc, line 80, winEventCallback
#     ...
#     SystemError: <function AggregatedSection.__getitem__ ...> returned a
#     result with an exception set
# The menu assertion itself is a benign wxWidgets/Win32 menu-tracking
# imbalance, but the stray exception aborts whichever call it lands in - here a
# winEvent callback, so NVDA silently drops an accessibility event and may miss
# an announcement. Clearing only the EXCEPTION bit stops the leak; assertions
# still reach the log through wxAPP_ASSERT_LOG (and NVDA's own App.OnAssert),
# and the DIALOG bit is never set, so nothing can pop up a message box.
#
# Fix 3: changing synthesizer refreshes a settings panel that wx has destroyed.
# Seen on NVDA alpha-57493 the moment a new synthesizer finished loading:
#   INFO - synthDriverHandler.setSynth: Loaded synthDriver eloquence
#   ERROR - unhandled exception
#     wx\core.pyc, line 3425, in <lambda>
#     gui\settingsDialogs.pyc, line 1607, in refreshGui
#   RuntimeError: wrapped C/C++ object of type BoxSizer has been deleted
# `AutoSettingsMixin.refreshGui` rebuilds a driver's controls when the driver
# object it drew them from goes away.  It arranges that by hanging a weakref
# callback on the driver:
#     self._currentSettingsRef = weakref.ref(
#         self.getSettings(),
#         lambda ref: wx.CallAfter(self.refreshGui),
#     )
# Switching synthesizer drops the last reference to the old one, so the
# callback fires and posts `refreshGui` to the next idle turn.  Nothing ties
# that posted call to the panel's lifetime.  If the dialog was closed in the
# same breath -- which is exactly what choosing a synthesizer does -- wx has
# already destroyed the panel and its `settingsSizer` by the time the idle turn
# arrives, and line 1607, `self.settingsSizer.Clear(delete_windows=True)`,
# raises on a C++ object that no longer exists.
#
# The work is pointless as well as impossible: the panel it wants to redraw is
# gone, and the next Voice panel is built from scratch anyway.  So the fix is
# to notice the destroyed sizer and return.  It has to be a check rather than a
# `try`/`except RuntimeError` around the whole body, because by the time the
# exception is raised `self.sizerDict.clear()` on the line above has already
# run: harmless on a dead panel, but there is no reason to touch it at all.
#
# Fix 4: a failed cross-process caret lookup in Firefox and Thunderbird aborts
# the keypress instead of being handled.
# Seen on NVDA alpha-57493 pressing enter in a Thunderbird message body, three
# times in five minutes:
#   ERROR - scriptHandler.executeScript: error executing script:
#     EditableText.script_caret_newLine ... with gesture 'enter'
#     editableText.pyc, line 216, in script_caret_newLine
#     editableText.pyc, line 124, in _hasCaretMoved
#     documentBase.pyc, line 76, in makeTextInfo
#     NVDAObjects\IAccessible\ia2TextMozilla.pyc, line 149, in __init__
#     NVDAObjects\IAccessible\ia2TextMozilla.pyc, line 274, in
#       _findContentDescendant
#   _ctypes.COMError: (-2147467259, 'Unspecified error', ...)
# `_findContentDescendant` asks NVDA's in-process helper, over RPC, which
# descendant of a Mozilla document holds the caret.  When the document changes
# underneath that call -- which pressing enter is a fine way to arrange -- the
# call comes back E_FAIL (0x80004005) as a COMError.
#
# Every caller of this function is already written for the case where the caret
# cannot be found.  `__init__` catches LookupError and re-raises it as
# `RuntimeError("No caret")`; `_hasCaretMoved` catches exactly
# `(RuntimeError, NotImplementedError)` around this call and carries on looking
# for the caret by other means; `_getSelectionBase` catches LookupError and
# starts from nothing instead.  COMError is in none of those tuples, so it
# sails past all of it and kills the script -- and the new line is never
# announced, which is the part the user actually notices.
#
# So the fix is a translation, not new behaviour: report a caret lookup that
# failed in the wire as the LookupError that this same function already raises
# when there is no caret to find.  Every handler above is then the one core
# already wrote.  Only POSITION_CARET is translated; the FIRST/LAST/ALL lookups
# have no such convention and are left to raise as before.
#
# Not fixed here, deliberately: the UIA_E-style COMErrors logged from
# `UIAHandler.IUIAutomationEventHandler_HandleAutomationEvent`.  comtypes binds
# that method into the COM vtable as a *bound method*, captured when the
# UIAHandler COMObject is constructed at NVDA startup -- long before add-ons
# load -- so patching the class or the instance afterwards changes nothing that
# is actually called.  Silencing it would mean rebuilding a live vtable, and
# the event is dropped either way; comtypes already catches the error and NVDA
# carries on.  It is log noise, and it stays.
#
# Fix 5: the COM proxy registration leak warnings written when a UWP app host
# (ApplicationFrameHost.exe) exits.  See _ComProxyLeakFilter.  These arrive
# from the injected IAccessible2Proxy.dll / ISimpleDOM.dll over the RPC log
# path, so they bypass core's "expected error" demotion and land at ERROR; a
# logHandler filter drops them at the sink, which is the only place add-on code
# runs after they have been formatted.
#
# Fix 6: "Malformed add-on scan results" spam from
# addonStore.models.scanResults.VirusTotalScanResults.fromDict.
# Core's fromDict returns None when the "scanResults" key is absent, but treats
# an explicit JSON null as a TypeError and logs an ERROR.  That happens for
# every add-on installed without VirusTotal data, because
# AddonStoreModel.asdict() serializes the scanResults=None field as
# "scanResults": null, and the cached per-addon JSON (<addonId>.json) round-
# trips that null back into fromDict on every startup.  The mirror
# (serrebidev/nvda-addon-mirror) already omits the key, so the null only ever
# comes from NVDA's own cache write.  The guard treats an explicit null (or
# empty mapping) the same as an absent key: no scan data, no log spam.

import functools
import logging

import globalPluginHandler
import review
import wx
from logHandler import log, logHandler


_COM_PROXY_LEAK_MARKERS = (
    "Interface proxy backup cache is not empty",
    "Cached backup for interface",
    "COM proxy registration cache is not empty",
    "Cached CLSID",
)


class _ComProxyLeakFilter(logging.Filter):
    """Drop NVDA's own benign COM proxy registration leak warnings.

    When a UWP/store app host process (ApplicationFrameHost.exe) exits, the
    IAccessible2Proxy.dll / ISimpleDOM.dll that NVDA injects into it logs, at
    ERROR level, that its interface-proxy backup cache and COM proxy
    registration cache were not empty when cleared.  This is expected teardown
    noise from the process dying, not a fault: NVDA's
    ``nvdaControllerInternal_logMessage`` relays the remote DLL's messages
    verbatim, so they land in the log at ERROR severity without ever going
    through core's "expected error -> DEBUGWARNING" translation.  Each such
    message is marked by one of the phrases above, so the filter matches them
    and lets every other message through untouched.
    """

    def filter(self, record):
        msg = record.getMessage()
        if msg and any(marker in msg for marker in _COM_PROXY_LEAK_MARKERS):
            return False
        return True


def _noneSafe(func):
    @functools.wraps(func)
    def wrapper(obj, *args, **kwargs):
        if obj is None:
            log.debugWarning(
                "serrebiFixes: review.%s called with None object; skipping"
                % getattr(func, "__name__", "?"),
            )
            return None
        return func(obj, *args, **kwargs)

    return wrapper


def _sizerIsAlive(sizer):
    """-> whether the C++ object behind `sizer` still exists.

    A destroyed wx object keeps its Python wrapper alive and raises
    RuntimeError from every method, so calling one is the only way to ask.
    `GetItemCount` is chosen because it changes nothing.
    """
    if sizer is None:
        return False
    try:
        sizer.GetItemCount()
    except RuntimeError:
        return False
    return True


class GlobalPlugin(globalPluginHandler.GlobalPlugin):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._origModes = None
        self._origFuncs = {}
        self._origAssertMode = None
        #: (owner, attribute name, original) for every method patched onto a
        #: core class, so terminate() can put each one back.
        self._patchedMethods = []
        #: original classmethod object for VirusTotalScanResults.fromDict.
        self._scanResultsOrig = None
        self._comProxyLeakFilter = _ComProxyLeakFilter()
        try:
            logHandler.addFilter(self._comProxyLeakFilter)
        except Exception:
            log.error("serrebiFixes: failed to add COM proxy leak log filter", exc_info=True)
        for label, apply in (
            ("review None-guard", self._applyReviewNoneGuard),
            ("wx assert mode fix", self._applyWxAssertModeFix),
            ("settings panel refresh guard", self._applyRefreshGuiGuard),
            ("Mozilla caret lookup guard", self._applyMozillaCaretGuard),
            ("VirusTotal scan results null guard", self._applyScanResultsNoneGuard),
        ):
            try:
                apply()
            except Exception:
                log.error("serrebiFixes: failed to apply %s" % label, exc_info=True)

    def _patchMethod(self, owner, name, make):
        """Replace `owner.name` with `make(original)` and remember how to undo it."""
        orig = getattr(owner, name)
        setattr(owner, name, make(orig))
        self._patchedMethods.append((owner, name, orig))

    def _applyWxAssertModeFix(self):
        # GetAssertMode/SetAssertMode live on wx.PyApp, which wx's type stubs do
        # not describe, so go through getattr; that also keeps a wx build
        # without them from breaking add-on startup.
        app: object = wx.GetApp()
        getMode = getattr(app, "GetAssertMode", None)
        setMode = getattr(app, "SetAssertMode", None)
        if app is None or getMode is None or setMode is None:
            log.debugWarning("serrebiFixes: wx assert mode unavailable; left alone")
            return
        mode = getMode()
        if not mode & wx.APP_ASSERT_EXCEPTION:
            log.debug(
                "serrebiFixes: wx assert mode %d already excludes EXCEPTION" % mode,
            )
            return
        newMode = (mode & ~wx.APP_ASSERT_EXCEPTION) | wx.APP_ASSERT_LOG
        setMode(newMode)
        self._origAssertMode = mode
        log.debug(
            "serrebiFixes: wx assert mode %d -> %d (EXCEPTION cleared)" % (mode, newMode),
        )

    def _applyReviewNoneGuard(self):
        # review.modes holds its own references to the position functions,
        # captured at module definition time; setCurrentMode and
        # getPositionForCurrentMode both dispatch through it, so the entries
        # must be wrapped, not just the module attributes.
        self._origModes = list(review.modes)
        for index, (modeId, label, func) in enumerate(review.modes):
            review.modes[index] = (modeId, label, _noneSafe(func))

        # Also wrap the module attributes for anything that calls them directly.
        for name in ("getObjectPosition", "getDocumentPosition", "getScreenPosition"):
            orig = getattr(review, name, None)
            if callable(orig):
                self._origFuncs[name] = orig
                setattr(review, name, _noneSafe(orig))

        log.debug("serrebiFixes: review None-guard applied")

    def _applyRefreshGuiGuard(self):
        # Patch the mixin rather than VoiceSettingsPanel: every driver panel
        # (synthesizer, braille display, vision provider) inherits this one
        # implementation, and all of them are reachable the same way.
        from gui.settingsDialogs import AutoSettingsMixin

        def make(orig):
            @functools.wraps(orig)
            def refreshGui(panel, *args, **kwargs):
                if not _sizerIsAlive(getattr(panel, "settingsSizer", None)):
                    log.debugWarning(
                        "serrebiFixes: refreshGui on a destroyed %s; skipping"
                        % type(panel).__name__,
                    )
                    return None
                return orig(panel, *args, **kwargs)

            return refreshGui

        self._patchMethod(AutoSettingsMixin, "refreshGui", make)
        log.debug("serrebiFixes: settings panel refresh guard applied")

    def _applyMozillaCaretGuard(self):
        import textInfos
        from comtypes import COMError
        from NVDAObjects.IAccessible.ia2TextMozilla import MozillaCompoundTextInfo

        def make(orig):
            @functools.wraps(orig)
            def _findContentDescendant(info, obj, position):
                try:
                    return orig(info, obj, position)
                except COMError as e:
                    if position != textInfos.POSITION_CARET:
                        raise
                    # The same signal this function raises when the document
                    # has no text descendant to offer.  Callers handle it.
                    log.debugWarning(
                        "serrebiFixes: caret lookup failed with COMError %s;"
                        " reporting no caret" % (getattr(e, "hresult", e),),
                    )
                    raise LookupError("Caret lookup failed") from e

            return _findContentDescendant

        self._patchMethod(MozillaCompoundTextInfo, "_findContentDescendant", make)
        log.debug("serrebiFixes: Mozilla caret lookup guard applied")

    def _applyScanResultsNoneGuard(self):
        from addonStore.models.scanResults import VirusTotalScanResults

        raw = VirusTotalScanResults.__dict__.get("fromDict")
        if not isinstance(raw, classmethod):
            log.debugWarning(
                "serrebiFixes: scanResults.fromDict is not a classmethod; left alone",
            )
            return
        orig = raw.__func__

        @classmethod
        @functools.wraps(orig)
        def fromDict(cls, addon):
            if not isinstance(addon, dict) or not addon.get("scanResults"):
                # Explicit null (or empty) scan results mean "not scanned",
                # identical to the key being absent. Core returns None for a
                # missing key but treats an explicit None as a TypeError and
                # logs "Malformed add-on scan results" on every startup,
                # because AddonStoreModel.asdict() serializes the
                # scanResults=None field as JSON null and the round-trip
                # re-raises on the next load.
                return None
            return orig(cls, addon)

        setattr(VirusTotalScanResults, "fromDict", fromDict)
        self._scanResultsOrig = raw
        log.debug("serrebiFixes: VirusTotal scan results null guard applied")

    def terminate(self):
        try:
            logHandler.removeFilter(self._comProxyLeakFilter)
        except Exception:
            log.error("serrebiFixes: failed to remove COM proxy leak log filter", exc_info=True)
        try:
            if self._origModes is not None:
                review.modes[:] = self._origModes
                self._origModes = None
            for name, func in self._origFuncs.items():
                setattr(review, name, func)
            self._origFuncs = {}
        except Exception:
            log.error("serrebiFixes: failed to restore review patches", exc_info=True)
        # Undo in reverse, so a class patched twice ends up with the original.
        for owner, name, orig in reversed(self._patchedMethods):
            try:
                setattr(owner, name, orig)
            except Exception:
                log.error(
                    "serrebiFixes: failed to restore %s.%s" % (owner.__name__, name),
                    exc_info=True,
                )
        self._patchedMethods = []
        try:
            if self._scanResultsOrig is not None:
                from addonStore.models.scanResults import VirusTotalScanResults

                setattr(VirusTotalScanResults, "fromDict", self._scanResultsOrig)
                self._scanResultsOrig = None
        except Exception:
            log.error(
                "serrebiFixes: failed to restore VirusTotal scan results patch",
                exc_info=True,
            )
        try:
            if self._origAssertMode is not None:
                app: object = wx.GetApp()
                setMode = getattr(app, "SetAssertMode", None)
                if setMode is not None:
                    setMode(self._origAssertMode)
                self._origAssertMode = None
        except Exception:
            log.error("serrebiFixes: failed to restore wx assert mode", exc_info=True)
        super().terminate()
