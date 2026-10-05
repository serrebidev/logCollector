# -*- coding: UTF-8 -*-
"""Tests for the serrebiFixes guards, runnable without NVDA.

The guards patch NVDA core, so most of them cannot be exercised for real
outside a running NVDA.  What *can* be checked here is the part that is easy
to get wrong and impossible to see by reading: whether each guard degrades the
way the core caller it protects expects, and whether the patch reaches what
actually runs.

`baseObject`'s Getter/CachingGetter/AutoPropertyType are reproduced below from
NVDA's own source, so the descriptor question Fix 10 and Fix 11 depend on --
does replacing `_get_x` reach the function core calls? -- is answered by the
same code NVDA runs, not by a guess about it.
"""

import functools
import os
import sys
import types
import unittest
from abc import ABCMeta

# --- baseObject, as NVDA defines it -----------------------------------------


class Getter:
    def __init__(self, fget, abstract=False):
        self.fget = fget
        if abstract:
            self._abstract = self.__isabstractmethod__ = abstract

    def __get__(self, instance, owner):
        if instance is None:
            return self
        return self.fget(instance)


class CachingGetter(Getter):
    def __get__(self, instance, owner):
        if instance is None:
            return self
        return instance._getPropertyViaCache(self.fget)


class AutoPropertyType(ABCMeta):
    def __init__(self, name, bases, namespace, /, **kwargs):
        super().__init__(name, bases, namespace, **kwargs)
        cacheByDefault = namespace.get(
            "cachePropertiesByDefault",
            any(getattr(b, "cachePropertiesByDefault", False) for b in bases),
        )
        props = {x[5:] for x in namespace if x[0:5] in ("_get_", "_set_", "_del_")}
        for x in props:
            g = namespace.get("_get_%s" % x)
            s = namespace.get("_set_%s" % x)
            d = namespace.get("_del_%s" % x)
            cache = namespace.get("_cache_%s" % x, cacheByDefault)
            if g and not (s or d):
                attr = (CachingGetter if cache else Getter)(g, False)
            else:
                attr = property(fget=g, fset=s, fdel=d)
            setattr(self, x, attr)


class AutoPropertyObject(metaclass=AutoPropertyType):
    cachePropertiesByDefault = True

    def __init__(self):
        self._propertyCache = {}

    def _getPropertyViaCache(self, getterMethod=None):
        try:
            return self._propertyCache[getterMethod]
        except KeyError:
            val = getterMethod(self)
            self._propertyCache[getterMethod] = val
            return val


# --- just enough NVDA for serrebiFixes to import ----------------------------


class _FakeLog:
    def __init__(self):
        self.lines = []

    def __getattr__(self, level):
        def record(msg, *args, **kwargs):
            self.lines.append((level, str(msg)))

        return record


def _installStubs():
    log = _FakeLog()
    stubs = {
        "globalPluginHandler": {
            "GlobalPlugin": type("GlobalPlugin", (), {"terminate": lambda self: None}),
        },
        "review": {"modes": [], "getObjectPosition": lambda obj: None},
        "wx": {"GetApp": lambda: None, "APP_ASSERT_EXCEPTION": 1, "APP_ASSERT_LOG": 2},
        "logHandler": {
            "log": log,
            "logHandler": types.SimpleNamespace(
                addFilter=lambda f: None,
                removeFilter=lambda f: None,
            ),
        },
    }
    for name, attrs in stubs.items():
        mod = types.ModuleType(name)
        mod.__dict__.update(attrs)
        sys.modules.setdefault(name, mod)
    return log


_installStubs()
sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "globalPlugins"),
)

import serrebiFixes as sf  # noqa: E402


# --- Fix 8: Mozilla content lookup ------------------------------------------


class TestContentLookupFailed(unittest.TestCase):
    """The whole point of the type is which `except` clauses catch it."""

    def test_isLookupError(self):
        # ia2TextMozilla.__init__:114 and :143, _getSelectionBase:197.
        self.assertIsInstance(sf._ContentLookupFailed("x"), LookupError)

    def test_isRuntimeError(self):
        # review.getObjectPosition:29/33, speech.getObjectSpeech:901.
        self.assertIsInstance(sf._ContentLookupFailed("x"), RuntimeError)

    def test_reviewFallsBackInsteadOfCrashing(self):
        """review.getObjectPosition, in the shape core wrote it.

        This is the regression Fix 4 left behind: the caret lookup was
        translated, the POSITION_FIRST retry right below it was not.
        """

        def coreCaretBranch():
            # ia2TextMozilla.__init__:141-144.
            try:
                raise sf._ContentLookupFailed("wire")
            except LookupError:
                raise RuntimeError("No caret")

        def getObjectPosition():
            # review.py:27-38.
            try:
                coreCaretBranch()
                return "caret"
            except (NotImplementedError, RuntimeError):
                try:
                    raise sf._ContentLookupFailed("wire")
                except (NotImplementedError, RuntimeError):
                    return "NVDAObjectTextInfo fallback"

        self.assertEqual(getObjectPosition(), "NVDAObjectTextInfo fallback")


# --- Fix 9: unloaded virtual buffers ----------------------------------------


class _FakeVBuf:
    def __init__(self, handle):
        self.VBufHandle = handle


class _FakeInfo:
    def __init__(self, handle):
        self.obj = _FakeVBuf(handle)

    def _getStoryLength(self):
        if not self.obj.VBufHandle:
            raise OSError(22, "null context handle", None, 1775)
        return 42


class TestDeadVBufGuard(unittest.TestCase):
    def test_detectsUnloadedBuffer(self):
        self.assertTrue(sf._vbufIsGone(_FakeInfo(None)))

    def test_livesWithALiveBuffer(self):
        self.assertFalse(sf._vbufIsGone(_FakeInfo(7)))

    def test_deadObjectCountsAsGone(self):
        self.assertTrue(sf._vbufIsGone(types.SimpleNamespace(obj=None)))

    def test_nonVirtualBufferIsLeftAlone(self):
        """No VBufHandle attribute at all: not our case, so do not answer for it."""
        self.assertFalse(sf._vbufIsGone(types.SimpleNamespace(obj=object())))

    def test_unloadedBufferReadsAsEmpty(self):
        guarded = sf._deadVBufSafe(0)(_FakeInfo._getStoryLength)
        self.assertEqual(guarded(_FakeInfo(None)), 0)

    def test_liveBufferStillReadsForReal(self):
        guarded = sf._deadVBufSafe(0)(_FakeInfo._getStoryLength)
        self.assertEqual(guarded(_FakeInfo(7)), 42)

    def test_rpcFailureOnALiveHandleDegrades(self):
        """Unloaded between the handle check and the call itself."""

        def _getSelectionOffsets(info):
            error = OSError(22, "boom")
            error.winerror = 1775
            raise error

        guarded = sf._deadVBufSafe((0, 0))(_getSelectionOffsets)
        self.assertEqual(guarded(_FakeInfo(7)), (0, 0))

    def test_unrelatedOSErrorIsReRaised(self):
        def _getSelectionOffsets(info):
            error = OSError(22, "access denied")
            error.winerror = 5
            raise error

        guarded = sf._deadVBufSafe((0, 0))(_getSelectionOffsets)
        with self.assertRaises(OSError) as caught:
            guarded(_FakeInfo(7))
        self.assertEqual(caught.exception.winerror, 5)


class TestSayAllTerminates(unittest.TestCase):
    """A story length of 0 must end say-all, never spin it.

    Reproduces the loop condition from textInfos/offsets.py:825-850, because
    "return 0" is only a safe answer if that loop treats it as the end.
    """

    @staticmethod
    def _move(startOffset, storyLength, direction=1):
        offset, count, lowLimit, highLimit = startOffset, 0, 0, storyLength
        lastOffset = None
        for _ in range(1000):
            if not (
                count != direction
                and (lastOffset is None or (direction > 0 and offset > lastOffset))
                and (offset < highLimit or direction < 0)
                and (offset > lowLimit or direction > 0)
            ):
                return count
            lastOffset, offset, count = offset, offset + 1, count + 1
        raise AssertionError("move() looped")

    def test_emptyStoryEndsSayAll(self):
        self.assertEqual(self._move(0, 0), 0)

    def test_emptyStoryEndsSayAllFromAStaleOffset(self):
        self.assertEqual(self._move(500, 0), 0)

    def test_realStoryStillAdvances(self):
        self.assertEqual(self._move(0, 42), 1)


# --- Fix 10 / 11: patching an auto property ---------------------------------


def _tableGuard(orig):
    @functools.wraps(orig)
    def getter(obj):
        try:
            return orig(obj)
        except AttributeError:
            raise NotImplementedError

    return getter


class TestPatchGetter(unittest.TestCase):
    def setUp(self):
        class FakeCell(AutoPropertyObject):
            def _get_rowNumber(self):
                # IAccessible.__init__.py:1478 on a Table2-only provider.
                raise AttributeError(
                    "'Ia2Web' object has no attribute 'IAccessibleTableObject'"
                )

            def _get_columnNumber(self):
                return 3

        self.FakeCell = FakeCell
        self.plugin = sf.GlobalPlugin.__new__(sf.GlobalPlugin)
        self.plugin._patchedGetters = []
        self.plugin._patchedMethods = []

    def test_baselineFailureIsReal(self):
        with self.assertRaises(AttributeError):
            self.FakeCell().rowNumber

    def test_replacingTheMethodDoesNotReachWhatRuns(self):
        """Why _patchGetter exists at all.

        AutoPropertyType captured `_get_rowNumber` into the descriptor when the
        class was created, so assigning a new one changes nothing that runs.
        """
        self.FakeCell._get_rowNumber = lambda self: "patched the wrong thing"
        with self.assertRaises(AttributeError):
            self.FakeCell().rowNumber

    def test_patchGetterDegradesToNotImplemented(self):
        self.assertTrue(self.plugin._patchGetter(self.FakeCell, "rowNumber", _tableGuard))
        with self.assertRaises(NotImplementedError):
            self.FakeCell().rowNumber

    def test_siblingPropertyUntouched(self):
        self.plugin._patchGetter(self.FakeCell, "rowNumber", _tableGuard)
        self.assertEqual(self.FakeCell().columnNumber, 3)

    def test_terminateRestoresTheOriginal(self):
        self.plugin._patchGetter(self.FakeCell, "rowNumber", _tableGuard)
        for desc, orig in reversed(self.plugin._patchedGetters):
            desc.fget = orig
        with self.assertRaises(AttributeError):
            self.FakeCell().rowNumber

    def test_readOnlyPropertyIsDeclinedNotCrashed(self):
        class PlainProp:
            @property
            def value(self):
                return 1

        self.assertIs(self.plugin._patchGetter(PlainProp, "value", _tableGuard), False)

    def test_missingPropertyIsDeclined(self):
        class Bare:
            pass

        self.assertIs(self.plugin._patchGetter(Bare, "nosuch", _tableGuard), False)


class TestModuleShape(unittest.TestCase):
    """Every guard the plugin advertises must actually exist on it."""

    def test_allGuardsAreImplemented(self):
        for name in (
            "_applyReviewNoneGuard",
            "_applyWxAssertModeFix",
            "_applyRefreshGuiGuard",
            "_applyMozillaContentGuard",
            "_applyObjectSpeechContentGuard",
            "_applyDeadVBufGuard",
            "_applyTableCellPositionGuard",
            "_applyLogonSessionGuard",
            "_applyScanResultsNoneGuard",
            "_applyWinError0Guard",
        ):
            self.assertTrue(
                callable(getattr(sf.GlobalPlugin, name, None)),
                "%s is registered but missing" % name,
            )

    def test_comProxyLeakFilterDropsOnlyItsOwnNoise(self):
        filt = sf._ComProxyLeakFilter()

        class Rec:
            def __init__(self, msg):
                self.msg = msg

            def getMessage(self):
                return self.msg

        self.assertFalse(filt.filter(Rec("Interface proxy backup cache is not empty")))
        self.assertTrue(filt.filter(Rec("something an add-on author needs to see")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
