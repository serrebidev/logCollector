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
# already wrote.  Fix 8 below widens that same translation to every position,
# for the callers that retry with POSITION_FIRST the moment the caret lookup
# reports there is no caret.
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
#
# Fix 7: the phantom "OSError: [WinError 0] The operation completed
# successfully" from bdDetect's background USB scan.
# Seen on NVDA alpha-57645, twelve minutes into the session, out of
#   extensionPoints.Chain.iter -> bdDetect._Detector._bgScanUsb
#   -> getDriversForConnectedUsbDevices -> hwPortUtils.listUsbDevices
#   -> hwPortUtils._listDevices
#   OSError: [WinError 0] The operation completed successfully.
# _listDevices probes each device interface by calling
# SetupDiGetDeviceInterfaceDetail with a null buffer, which by design always
# fails with ERROR_INSUFFICIENT_BUFFER (122); the guard around it tolerates
# exactly that code and re-raises anything else.  But the error is read with
# ctypes.GetLastError(), which returns the *live* thread last-error, and the
# binding (winBindings/setupapi.py) is a plain windll without
# use_last_error=True, so no snapshot is taken at the call.  Any succeeding
# Win32 call that lands on that ThreadPoolExecutor worker thread between the
# failed probe and the check resets the last error to 0 -- the interpreter
# itself, or an APC queued by unrelated overlapped I/O.  The guard then sees
# 0 != 122 and raises WinError(0), aborting the whole scan round: braille
# display auto-detection silently loses that pass.
# The fix rewraps the four setupapi functions _listDevices (and its sibling
# listers, which share the same racy pattern) call after a *failed* call:
# each wrapper snapshots the error through a use_last_error=True binding,
# where ctypes swaps the system last-error with a thread-private copy at C
# level, immune to later clobbers.  A consume-once GetLastError/WinError pair
# exposed to the module hands the snapshot to core's guards; a second read
# falls back to the live value so no other ctypes user on the module is
# changed.  Verified against a live clobber: today's pattern raises
# WinError(0), the wrapped pattern still reads 122.
#
# Fix 8: the same failed Mozilla lookup, on the paths Fix 4 did not cover.
# Fix 4 translated a wire failure in `_findContentDescendant` only for
# POSITION_CARET, reasoning that FIRST/LAST/ALL have no "no caret" convention
# to borrow.  In practice that moved the crash one line down.
# `review.getObjectPosition` asks for the caret, catches the RuntimeError("No
# caret") Fix 4 now produces, and immediately retries with POSITION_FIRST
# (review.py:32).  `speech.getObjectSpeech` has the same shape: POSITION_
# SELECTION inside a try, then an unguarded POSITION_FIRST (speech.py:908).
# A document broken enough to fail the caret lookup fails the FIRST lookup in
# the same breath, and that COMError reaches eventHandler:
#   error executing event: gainFocus on <...EditableTextWithAutoSelect...>
#     speech\speech.pyc, line 908, in getObjectSpeech
#     NVDAObjects\IAccessible\ia2TextMozilla.pyc, line 132, in __init__
#     ia2TextMozilla.pyc, line 267, in _findContentDescendant
#   _ctypes.COMError: (-2147467259, 'Unspecified error', ...)
# The focused control is then not announced at all: the whole gainFocus
# handler is abandoned, so nothing after speakObject runs either.
#
# So the translation is widened to every position and given a type that fits
# both conventions core already has.  _ContentLookupFailed derives from
# LookupError *and* RuntimeError.  As a LookupError it keeps every handler Fix
# 4 relied on -- __init__:114 falls back to the embedding, __init__:143 turns
# it into RuntimeError("No caret"), _getSelectionBase:197 starts from nothing.
# As a RuntimeError it is caught by the callers that guard makeTextInfo with
# `except (NotImplementedError, RuntimeError)`: both of
# review.getObjectPosition's attempts, and speech's POSITION_SELECTION
# attempt.  That is what finally lets review fall back to NVDAObjectTextInfo
# instead of dying on its second try.
#
# speech.py:908 is guarded by nothing at all, so it needs the one caller-side
# patch.  getObjectSpeech retries without text content when the text content
# is what failed, rebuilding the properties-only sequence exactly as core
# builds it for an object that reports no navigable text (speech.py:881).
# The user hears the control's name, role and state -- everything except the
# line of document text that was unreadable in that instant -- instead of
# silence.
#
# Fix 9: reading from a virtual buffer that has already been unloaded.
#   ERROR - queueHandler.flushQueue: Error in func _TextReader.nextLine
#     speech\sayAll.pyc, line 263, in nextLineImpl
#     textInfos\offsets.pyc, line 838, in move
#     virtualBuffers\__init__.pyc, line 276, in _getStoryLength
#   OSError: [WinError 1775] A null context handle was passed from the client
#   to the host during a remote procedure call
# and the same error out of _getSelectionOffsets (line 259) when an arrow key
# builds a TextInfo:
#     cursorManager.pyc, line 257, in _caretMovementScriptHelper
#     virtualBuffers\__init__.pyc, line 259, in _getSelectionOffsets
# VirtualBuffer.unloadBuffer destroys the buffer and sets `self.VBufHandle =
# None` (virtualBuffers/__init__.py:635), but a say-all reader -- or any
# script already in flight -- is still holding a VirtualBufferTextInfo whose
# `obj` is that buffer.  Its next call hands the None straight to
# NVDAHelper.localLib.VBuf_getTextLength as a context handle, and the RPC
# runtime rejects the null handle with WinError 1775.  Navigating or closing
# a tab mid-say-all is all it takes.
#
# An unloaded buffer holds no text, so the guard answers as an empty buffer
# would: length 0, selection (0, 0), empty range.  say-all's `move` then
# returns delta <= 0, nextLineImpl reports "no more text" and the reader
# finishes cleanly instead of dying in flushQueue; the arrow key reports a
# blank line rather than aborting the script.  Either way the buffer is left
# to be rebuilt by the next loadComplete, which is what happens regardless.
# The handle is checked before the call and the RPC codes caught around it,
# for the race where the buffer is unloaded between those two moments; every
# other OSError is re-raised untouched.
#
# Fix 10: row and column number lookups assume an IAccessibleTable that
# modern providers do not implement.
#   error executing event: focusEntered on <NVDAObjects...Ia2Web object>
#     speech\speech.pyc, line 741, in getObjectPropertiesSpeech
#     NVDAObjects\IAccessible\__init__.pyc, line 1478, in _get_rowNumber
#   AttributeError: 'Ia2Web' object has no attribute 'IAccessibleTableObject'.
#   Did you mean: 'IAccessibleTable2Object'?
# IAccessible.__init__ queries IAccessibleTable2 first and only falls back to
# the deprecated IAccessibleTable when that fails (__init__.py:850-855), so on
# a Chromium or Gecko document the ancestor table has IAccessibleTable2Object
# and no IAccessibleTableObject at all.  `_get_table` knows this -- it accepts
# an ancestor exposing either interface (line 1731) -- and so do rowCount,
# columnCount and selectedCellCount, which all test with hasattr.
# `_get_rowNumber` and `_get_columnNumber` are the two that forgot: they reach
# straight for `table.IAccessibleTableObject` and catch only COMError, so the
# missing attribute raises AttributeError out of a cached property and takes
# down whichever event asked for the cell's position.
# Both functions already have a "this cell cannot say" answer -- they raise
# NotImplementedError when the table-cell-index attribute is missing, and
# getObjectPropertiesSpeech responds by leaving the position out of the
# announcement.  The guard gives the AttributeError that same answer, so the
# cell is announced without its coordinates instead of not at all.
#
# Fix 11: an ERROR logged for a process that has simply exited.
#   ERROR - appModuleHandler.AppModule._get_isRunningUnderDifferentLogonSession
#   Couldn't compare logon session ID for AppModule(appName='bitwarden', ...)
#     systemUtils.pyc, line 130, in getProcessLogonSessionId
#   OSError: [WinError 6] The handle is invalid.
# Core already treats this as recoverable: it answers False and carries on
# (appModuleHandler.py:731-733).  But it reports it with log.error, and
# WinError 6 on a process handle means only that the process died between the
# appModule being built and the property being read -- twice here, for two
# different bitwarden PIDs.  Nothing is wrong and nothing needs doing, so the
# guard logs the dead-process codes at DEBUGWARNING and leaves every other
# OSError to core's original ERROR, where a genuine failure still shows.

import ctypes
import functools
import logging
import threading
import types

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


class _ContentLookupFailed(LookupError, RuntimeError):
    """A Mozilla content-descendant lookup that failed in the wire, not in the document.

    Fix 4 and Fix 8: `MozillaCompoundTextInfo._findContentDescendant` asks
    NVDA's in-process helper, over RPC, which descendant of a document holds a
    given position.  When the document changes underneath that call the answer
    comes back E_FAIL as a COMError, which no caller expects.

    Both exceptions this derives from are already handled on that path, for
    different positions and by different callers, and the failure honestly fits
    each: there is no descendant to be found (LookupError, which ia2TextMozilla
    itself raises when a document has no text descendant), and the request
    could not be served (RuntimeError, which is what makeTextInfo's callers
    guard against).  Deriving from both means neither caller has to learn a new
    exception.
    """


#: RPC failures that mean the buffer behind a VirtualBufferTextInfo is gone,
#: rather than that a live buffer refused the call.  1775 is the one seen in
#: the wild, raised when the handle passed was NULL; the two call-failed codes
#: cover the content process dying with the call in flight.
_DEAD_VBUF_WINERRORS = frozenset((1775, 1726, 1727))

#: Win32 errors from a process handle that mean the process has exited, as
#: opposed to something being wrong with NVDA's request.
_DEAD_PROCESS_WINERRORS = frozenset((5, 6))


def _vbufIsGone(info):
    """-> whether the virtual buffer behind `info` has already been unloaded.

    `VirtualBuffer.unloadBuffer` sets `VBufHandle` to None, and every
    VirtualBufferTextInfo method passes that attribute straight to the helper
    DLL as a context handle.  A TextInfo outliving its buffer is normal --
    say-all holds one across page turns -- so this is a question worth asking
    before each call, not a fault.
    """
    obj = getattr(info, "obj", None)
    if obj is None:
        return True
    try:
        return not obj.VBufHandle
    except AttributeError:
        # Not a virtual buffer after all; let the call speak for itself.
        return False


def _deadVBufSafe(empty):
    """Build a decorator answering `empty` once the buffer is gone.

    `empty` is whatever that method would return for a buffer holding no text,
    so callers see an empty document rather than an exception: say-all stops at
    the end of the text, and caret movement reports a blank line.
    """

    def decorate(orig):
        @functools.wraps(orig)
        def wrapper(info, *args, **kwargs):
            if _vbufIsGone(info):
                log.debugWarning(
                    "serrebiFixes: %s on an unloaded virtual buffer;"
                    " reporting empty" % orig.__name__,
                )
                return empty
            try:
                return orig(info, *args, **kwargs)
            except OSError as e:
                if getattr(e, "winerror", None) not in _DEAD_VBUF_WINERRORS:
                    raise
                # Unloaded between the check above and the call itself.
                log.debugWarning(
                    "serrebiFixes: %s failed with WinError %s on a dying"
                    " virtual buffer; reporting empty" % (orig.__name__, e.winerror),
                )
                return empty

        return wrapper

    return decorate

class _WinError0Patch:
    """Fix 7: snapshot setupapi last-errors so hwPortUtils' guards stop racing.

    See the Fix 7 notes above the module for the full diagnosis.  Everything
    this patch does is confined to the hwPortUtils module object:

    - The four setupapi functions hwPortUtils calls through racy guards are
      rewrapped through a ``WinDLL("setupapi", use_last_error=True)``
      binding.  On a *failed* call the wrapper stores
      ``ctypes.get_last_error()`` (the thread-private copy ctypes swapped in
      at C level, immune to later clobbers) into a thread-local slot.
      Successful calls clear the slot, so a stale snapshot cannot leak into
      a later guard.
    - ``GetLastError``/``WinError`` on a small ctypes shim module replace
      hwPortUtils' own ``ctypes`` reference.  They read the slot
      consume-once, then fall back to the live values, so any ctypes use in
      hwPortUtils this patch never read about behaves exactly as before.
    - Everything else on the shim (byref, sizeof, Structure, ...) passes
      through to the real ctypes via PEP 562.
    """

    def __init__(self):
        self._store = threading.local()
        self._origAttrs = {}

    def _capture(self, err):
        # pending: consumed by the first GetLastError/WinError() read, so a
        # snapshot can never leak into a later guard (e.g. the unwrapped
        # CreateFile read in _getHidInfo must still see the live value).
        # recent: sticky until the next wrapped call, so a no-arg
        # WinError() right after a consumed GetLastError() still reports the
        # captured code instead of the racy live one.
        self._store.pending = err
        self._store.recent = err

    def apply(self, hwPortUtils):
        from ctypes.wintypes import BOOL, DWORD, PDWORD

        from comtypes import GUID
        from winBindings.setupapi import (
            DEVPROPKEY,
            HDEVINFO,
            PSP_DEVINFO_DATA,
            PSP_DEVICE_INTERFACE_DATA,
            PSP_DEVICE_INTERFACE_DETAIL_DATA,
        )

        c_void_p = ctypes.c_void_p
        # Attribute access on a use_last_error DLL applies the flag; the
        # tuple form WINFUNCTYPE(...)(("name", dll)) that the core binding
        # builds its functions with does not, so the rebound functions must
        # come from getattr on the DLL instance.
        ueDll = ctypes.WinDLL("setupapi", use_last_error=True)
        specs = {
            "SetupDiEnumDeviceInterfaces": (
                (
                    HDEVINFO,
                    PSP_DEVINFO_DATA,
                    ctypes.POINTER(GUID),
                    DWORD,
                    PSP_DEVICE_INTERFACE_DATA,
                ),
                BOOL,
            ),
            "SetupDiGetDeviceInterfaceDetail": (
                (
                    HDEVINFO,
                    PSP_DEVICE_INTERFACE_DATA,
                    PSP_DEVICE_INTERFACE_DETAIL_DATA,
                    DWORD,
                    PDWORD,
                    PSP_DEVINFO_DATA,
                ),
                BOOL,
            ),
            "SetupDiGetDeviceRegistryProperty": (
                (
                    HDEVINFO,
                    PSP_DEVINFO_DATA,
                    DWORD,
                    PDWORD,
                    c_void_p,
                    DWORD,
                    PDWORD,
                ),
                BOOL,
            ),
            "SetupDiGetDeviceProperty": (
                (
                    HDEVINFO,
                    PSP_DEVINFO_DATA,
                    ctypes.POINTER(DEVPROPKEY),
                    PDWORD,
                    c_void_p,
                    DWORD,
                    PDWORD,
                    DWORD,
                ),
                BOOL,
            ),
        }

        def make(funcName, argtypes, restype):
            # setupapi exports its Unicode entry points with a W suffix.
            try:
                fn = getattr(ueDll, funcName)
            except AttributeError:
                fn = getattr(ueDll, funcName + "W")
            fn.argtypes = argtypes
            fn.restype = restype

            def wrapper(*args, **kwargs):
                res = fn(*args, **kwargs)
                self._capture(None if res else ctypes.get_last_error())
                return res

            wrapper.__name__ = funcName
            return wrapper

        wrapped = {name: make(name, at, rt) for name, (at, rt) in specs.items()}

        # Sanity probe before touching anything: a call that is guaranteed
        # to fail must produce a nonzero snapshot, or the mechanism is dead
        # and the patch aborts without half-applying.
        res = wrapped["SetupDiGetDeviceInterfaceDetail"](None, None, None, 0, None, None)
        captured = getattr(self._store, "pending", None)
        self._store.pending = None
        self._store.recent = None
        if res or not captured:
            raise RuntimeError(
                "use_last_error snapshot not working (res=%r, captured=%r)" % (res, captured)
            )

        realCtypes = ctypes
        store = self._store

        def shimGetLastError():
            err = getattr(store, "pending", None)
            if err is not None:
                store.pending = None
                return err
            return realCtypes.GetLastError()

        def shimWinError(code=None):
            if code is None:
                code = getattr(store, "pending", None)
                if code is not None:
                    store.pending = None
                else:
                    code = getattr(store, "recent", None)
                    if code is None:
                        code = realCtypes.GetLastError()
            return realCtypes.WinError(code)

        shimMod = types.ModuleType("serrebiFixes_ctypes_shim")
        shimMod.GetLastError = shimGetLastError
        shimMod.WinError = shimWinError
        shimMod.__getattr__ = lambda name: getattr(realCtypes, name)

        # Swap only inside hwPortUtils: its ctypes reference (what the
        # guards read) and its four imported binding references (the
        # guarded calls).  winBindings.setupapi itself stays untouched for
        # every other module.
        self._origAttrs = {
            "ctypes": hwPortUtils.__dict__.get("ctypes"),
            "_SetupDiEnumDeviceInterfaces": hwPortUtils.__dict__.get(
                "_SetupDiEnumDeviceInterfaces"
            ),
            "_SetupDiGetDeviceInterfaceDetail": hwPortUtils.__dict__.get(
                "_SetupDiGetDeviceInterfaceDetail"
            ),
            "_SetupDiGetDeviceRegistryProperty": hwPortUtils.__dict__.get(
                "_SetupDiGetDeviceRegistryProperty"
            ),
            "_SetupDiGetDeviceProperty": hwPortUtils.__dict__.get(
                "_SetupDiGetDeviceProperty"
            ),
        }
        hwPortUtils.ctypes = shimMod
        hwPortUtils._SetupDiEnumDeviceInterfaces = wrapped["SetupDiEnumDeviceInterfaces"]
        hwPortUtils._SetupDiGetDeviceInterfaceDetail = wrapped["SetupDiGetDeviceInterfaceDetail"]
        hwPortUtils._SetupDiGetDeviceRegistryProperty = wrapped[
            "SetupDiGetDeviceRegistryProperty"
        ]
        hwPortUtils._SetupDiGetDeviceProperty = wrapped["SetupDiGetDeviceProperty"]

    def revert(self, hwPortUtils):
        for name, orig in self._origAttrs.items():
            if orig is None:
                continue
            setattr(hwPortUtils, name, orig)
        self._origAttrs = {}


class GlobalPlugin(globalPluginHandler.GlobalPlugin):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._origModes = None
        self._origFuncs = {}
        self._origAssertMode = None
        #: (owner, attribute name, original) for every method patched onto a
        #: core class, so terminate() can put each one back.
        self._patchedMethods = []
        #: (descriptor, original fget) for every AutoPropertyObject getter
        #: patched on a core class, so terminate() can put each one back.
        self._patchedGetters = []
        #: original classmethod object for VirusTotalScanResults.fromDict.
        self._scanResultsOrig = None
        self._comProxyLeakFilter = _ComProxyLeakFilter()
        self._winError0Patch = _WinError0Patch()
        try:
            logHandler.addFilter(self._comProxyLeakFilter)
        except Exception:
            log.error("serrebiFixes: failed to add COM proxy leak log filter", exc_info=True)
        for label, apply in (
            ("review None-guard", self._applyReviewNoneGuard),
            ("wx assert mode fix", self._applyWxAssertModeFix),
            ("settings panel refresh guard", self._applyRefreshGuiGuard),
            ("Mozilla content lookup guard", self._applyMozillaContentGuard),
            ("object speech content guard", self._applyObjectSpeechContentGuard),
            ("unloaded virtual buffer guard", self._applyDeadVBufGuard),
            ("IAccessible table cell position guard", self._applyTableCellPositionGuard),
            ("logon session comparison guard", self._applyLogonSessionGuard),
            ("VirusTotal scan results null guard", self._applyScanResultsNoneGuard),
            ("hwPortUtils last-error guard", self._applyWinError0Guard),
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

    def _patchGetter(self, owner, propName, make):
        """Wrap the getter behind an AutoPropertyObject property.

        AutoPropertyType binds `_get_x` into the descriptor for `x` when the
        class is created (baseObject.py:107), and `CachingGetter.__get__` calls
        that captured function rather than looking the class attribute up
        again.  Replacing `_get_x` afterwards therefore changes nothing that
        actually runs: the descriptor's `fget` is the only live reference, so
        that is what gets swapped -- and swapped back in terminate().
        """
        desc = None
        for cls in owner.__mro__:
            desc = cls.__dict__.get(propName)
            if desc is not None:
                break
        orig = getattr(desc, "fget", None)
        if not callable(orig):
            log.debugWarning(
                "serrebiFixes: %s.%s is not an auto property; left alone"
                % (owner.__name__, propName),
            )
            return False
        try:
            desc.fget = make(orig)
        except AttributeError:
            # A plain property(); its fget is read-only.
            log.debugWarning(
                "serrebiFixes: %s.%s getter is not replaceable; left alone"
                % (owner.__name__, propName),
            )
            return False
        self._patchedGetters.append((desc, orig))
        return True

    def _applyMozillaContentGuard(self):
        from comtypes import COMError
        from NVDAObjects.IAccessible.ia2TextMozilla import MozillaCompoundTextInfo

        def make(orig):
            @functools.wraps(orig)
            def _findContentDescendant(info, obj, position):
                try:
                    return orig(info, obj, position)
                except COMError as e:
                    # The same signal this function raises when the document
                    # has no text descendant to offer, in a type the callers
                    # that guard makeTextInfo also recognise.  See Fix 8.
                    log.debugWarning(
                        "serrebiFixes: content descendant lookup for %r failed"
                        " with COMError %s; reporting no descendant"
                        % (position, getattr(e, "hresult", e)),
                    )
                    raise _ContentLookupFailed("Content descendant lookup failed") from e

            return _findContentDescendant

        self._patchMethod(MozillaCompoundTextInfo, "_findContentDescendant", make)
        log.debug("serrebiFixes: Mozilla content lookup guard applied")

    def _applyObjectSpeechContentGuard(self):
        import speech
        from controlTypes import OutputReason
        from speech import speech as speechModule

        calcProps = getattr(speechModule, "_objectSpeech_calculateAllowedProps", None)
        getProps = getattr(speechModule, "getObjectPropertiesSpeech", None)
        if not callable(calcProps) or not callable(getProps):
            log.debugWarning(
                "serrebiFixes: speech internals not as expected; object speech"
                " content guard left off",
            )
            return

        def make(orig):
            @functools.wraps(orig)
            def getObjectSpeech(obj, reason=OutputReason.QUERY, _prefixSpeechCommand=None):
                try:
                    return orig(obj, reason, _prefixSpeechCommand)
                except _ContentLookupFailed:
                    # speech.py:908 fetches POSITION_FIRST with no guard at
                    # all, so a document that cannot produce its text takes
                    # the entire announcement -- name, role, states -- down
                    # with it.  Rebuild what core builds for an object with no
                    # navigable text, which is the truth in this instant.  The
                    # lock screen check at the top of the original has already
                    # passed, or it would have returned instead of raising.
                    log.debugWarning(
                        "serrebiFixes: text content unavailable for %r;"
                        " speaking properties only" % obj,
                    )
                    # Core calculates the allowed properties from the reason it
                    # was called with and remaps FOCUSENTERED only afterwards
                    # (speech.py:884-888); the order decides which properties
                    # survive, so it is kept.
                    allowProperties = calcProps(reason, False, obj.role)
                    if reason == OutputReason.FOCUSENTERED:
                        reason = OutputReason.FOCUS
                    return getProps(
                        obj,
                        reason=reason,
                        _prefixSpeechCommand=_prefixSpeechCommand,
                        **allowProperties,
                    )

            return getObjectSpeech

        self._patchMethod(speechModule, "getObjectSpeech", make)
        # speech/__init__.py re-exports the function by value, so anything
        # importing it from the package would keep the original.  Point that
        # name at the wrapper the line above just installed.
        if getattr(speech, "getObjectSpeech", None) is not None:
            wrapper = speechModule.getObjectSpeech
            self._patchMethod(speech, "getObjectSpeech", lambda orig: wrapper)
        log.debug("serrebiFixes: object speech content guard applied")

    def _applyDeadVBufGuard(self):
        from virtualBuffers import VirtualBufferTextInfo

        for name, empty in (
            ("_getStoryLength", 0),
            ("_getSelectionOffsets", (0, 0)),
            ("_getTextRange", ""),
        ):
            self._patchMethod(VirtualBufferTextInfo, name, _deadVBufSafe(empty))
        log.debug("serrebiFixes: unloaded virtual buffer guard applied")

    def _applyTableCellPositionGuard(self):
        from NVDAObjects.IAccessible import IAccessible

        def make(orig):
            @functools.wraps(orig)
            def getter(obj):
                try:
                    return orig(obj)
                except AttributeError:
                    # `table` was accepted for exposing IAccessibleTable2
                    # alone, which this getter then asks for IAccessibleTable
                    # on.  NotImplementedError is the answer core already
                    # gives when a cell cannot report its position.
                    log.debugWarning(
                        "serrebiFixes: %s unavailable on this table;"
                        " reporting not implemented" % orig.__name__,
                        exc_info=True,
                    )
                    raise NotImplementedError

            return getter

        for propName in ("rowNumber", "columnNumber"):
            self._patchGetter(IAccessible, propName, make)
        log.debug("serrebiFixes: IAccessible table cell position guard applied")

    def _applyLogonSessionGuard(self):
        import appModuleHandler
        from systemUtils import getCurrentProcessLogonSessionId, getProcessLogonSessionId

        def make(orig):
            @functools.wraps(orig)
            def getter(appModule):
                # A copy of core's body (appModuleHandler.py:727-734) with the
                # log level split by error code.  It has to be a copy rather
                # than a wrapper: the only thing being changed is how the
                # exception core already swallows gets reported, and a wrapper
                # never sees it.
                try:
                    appModule.isRunningUnderDifferentLogonSession = (
                        getCurrentProcessLogonSessionId()
                        != getProcessLogonSessionId(appModule.processHandle)
                    )
                except OSError as e:
                    if getattr(e, "winerror", None) in _DEAD_PROCESS_WINERRORS:
                        log.debugWarning(
                            "serrebiFixes: %s has gone (WinError %s); assuming"
                            " the same logon session" % (appModule, e.winerror),
                        )
                    else:
                        log.error(
                            "Couldn't compare logon session ID for %s" % appModule,
                            exc_info=True,
                        )
                    appModule.isRunningUnderDifferentLogonSession = False
                return appModule.isRunningUnderDifferentLogonSession

            return getter

        self._patchGetter(
            appModuleHandler.AppModule,
            "isRunningUnderDifferentLogonSession",
            make,
        )
        log.debug("serrebiFixes: logon session comparison guard applied")

    def _applyWinError0Guard(self):
        # Patch the module namespace rather than individual functions:
        # bdDetect's DeviceInfoFetcher calls hwPortUtils.listUsbDevices and
        # siblings as module attributes at call time, and those listers read
        # ctypes.GetLastError / ctypes.WinError through the module's own
        # ``ctypes`` reference, so one namespace swap covers every racy guard
        # in the file (listComPorts 139, _listDevices 271/286/316,
        # listUsbDevices 342, listHidDevices 483).
        import hwPortUtils

        self._winError0Patch.apply(hwPortUtils)
        log.debug("serrebiFixes: hwPortUtils last-error guard applied")

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
        for desc, orig in reversed(self._patchedGetters):
            try:
                desc.fget = orig
            except Exception:
                log.error(
                    "serrebiFixes: failed to restore an auto property getter",
                    exc_info=True,
                )
        self._patchedGetters = []
        try:
            self._winError0Patch.revert(__import__("hwPortUtils"))
        except Exception:
            log.error("serrebiFixes: failed to revert hwPortUtils patch", exc_info=True)
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
