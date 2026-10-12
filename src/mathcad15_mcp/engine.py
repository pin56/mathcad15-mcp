"""Mathcad 15 over COM, serialized through one STA thread.

The instance is started lazily with DispatchEx. It is "ours" when its PID did not
exist before the call; only ours is ever quit or terminated. A call that runs past
the timeout (an endless program loop) terminates our instance and the next call
starts a fresh one; a foreign instance is left alone and the call just fails.
"""
from __future__ import annotations

import atexit
import os
import queue
import shutil
import tempfile
import threading
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, field

import pythoncom
import pywintypes
import win32api
import win32com.client
import win32con
import win32event
import win32process

from .worksheet import PT_PER_PX

FORMATS = {"xmcd": 20, "xmcdz": 21, "rtf": 16, "html": 3}
_GONE = {-2147023174, -2147417848, -2147023170, -2147418111}  # RPC unavailable, disconnected, call failed, rejected
_WD_PDF = 17


class EngineError(Exception):
    pass


class MathcadTimeout(EngineError):
    pass


@dataclass
class ComRegion:
    type: int        # 0 text, 1 math (plots too), 2 bitmap, 3 metafile, 4 OLE, -1 area
    x_pt: float      # left, points
    y_pt: float      # top, points
    error: str = ""  # COM ErrorMsg, e.g. 'bad_variable\tdoc_mc_Wx\t…'


@dataclass
class CalcResult:
    xml: str
    com: list[ComRegion] = field(default_factory=list)


class _Worker(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True, name="mathcad-sta")
        self.q: queue.Queue = queue.Queue()
        self.start()

    def run(self):
        pythoncom.CoInitialize()
        try:
            while True:
                item = self.q.get()
                if item is None:
                    return
                fn, fut = item
                if not fut.set_running_or_notify_cancel():
                    continue
                try:
                    fut.set_result(fn())
                except BaseException as e:  # noqa: BLE001 - handed to the caller
                    fut.set_exception(e)
        finally:
            pythoncom.CoUninitialize()


def _hresult(e: BaseException) -> int | None:
    return e.args[0] if isinstance(e, pywintypes.com_error) and e.args else None


def _com_text(e: BaseException) -> str:
    if isinstance(e, pywintypes.com_error):
        exc = e.args[2] if len(e.args) > 2 else None
        if exc and len(exc) > 2 and exc[2]:
            return str(exc[2]).strip()
        return str(e.args[1]) if len(e.args) > 1 else str(e)
    return str(e)


class Engine:
    def __init__(self, timeout: float | None = None):
        self.timeout = float(timeout or os.environ.get("MATHCAD_TIMEOUT") or 120)
        self._lock = threading.Lock()
        self._worker = _Worker()
        self.app = None
        self.pid: int | None = None
        self.owned = False
        self._handle = None
        self._killed: tuple[int | None, bool] = (None, False)
        atexit.register(self.close)

    # ------------------------------------------------------------ plumbing

    def _submit(self, fn, timeout: float):
        w, fut = self._worker, Future()
        w.q.put((fn, fut))
        try:
            return fut.result(timeout)
        except FutureTimeout:
            self._abandon(w)
            raise

    def _abandon(self, w: _Worker):
        """The STA thread is stuck in a call: kill our instance, start a new thread."""
        w.q.put(None)
        self._worker = _Worker()
        pid, owned = self.pid, self.owned
        if owned and self._handle is not None:
            try:
                win32api.TerminateProcess(self._handle, 1)
            except pywintypes.error:
                pass
        self.app, self.pid, self.owned, self._handle = None, None, False, None
        self._killed = (pid, owned)

    def _ensure(self):
        if self.app is not None:
            return self.app
        before = set(win32process.EnumProcesses())
        try:
            app = win32com.client.DispatchEx("Mathcad.Application")
        except pywintypes.com_error as e:
            raise EngineError(f"Mathcad 15 не запускается через COM (Mathcad.Application): {_com_text(e)}") from None
        pid = win32process.GetWindowThreadProcessId(app.HWND)[1]
        try:
            app.SetOption(0, False)   # mcShowMessageBoxes
        except pywintypes.com_error:
            pass
        self.app, self.pid, self.owned = app, pid, pid not in before
        if self.owned:
            try:
                self._handle = win32api.OpenProcess(
                    win32con.PROCESS_TERMINATE | win32con.SYNCHRONIZE, False, pid)
            except pywintypes.error:
                self._handle = None
        return app

    def _call(self, fn):
        app = self._ensure()
        try:
            return fn(app)
        except pywintypes.com_error as e:
            if _hresult(e) not in _GONE:
                raise
        self.app = None                          # instance died (closed by hand?) - one retry
        return fn(self._ensure())

    def run(self, fn, timeout: float | None = None):
        """fn(app) on the STA thread; MathcadTimeout after `timeout` seconds."""
        t = timeout or self.timeout
        with self._lock:
            try:
                return self._submit(lambda: self._call(fn), t)
            except FutureTimeout:
                pid, owned = self._killed
                if owned:
                    raise MathcadTimeout(f"Mathcad не ответил за {t:g} с (бесконечный цикл или очень долгий "
                                         f"расчёт?) — свой процесс {pid} завершён, следующий вызов запустит новый") from None
                raise MathcadTimeout(f"Mathcad не ответил за {t:g} с; экземпляр (PID {pid}) запущен не этим "
                                     f"сервером, поэтому не завершён — закройте его вручную") from None
            except pywintypes.com_error as e:
                raise EngineError(f"Mathcad: {_com_text(e)}") from None

    def close(self):
        """Quit our instance (or just let go of a foreign one)."""
        if self.app is None:
            return
        owned, handle = self.owned, self._handle

        def bye():
            if self.owned and self.app is not None:
                try:
                    self.app.Quit(2)             # mcDiscardChanges
                except pywintypes.com_error:
                    pass
            self.app = None

        with self._lock:
            try:
                self._submit(bye, 15)
            except FutureTimeout:
                pass
            if owned and handle is not None:
                if win32event.WaitForSingleObject(handle, 10_000) == win32event.WAIT_TIMEOUT:
                    try:
                        win32api.TerminateProcess(handle, 1)
                    except pywintypes.error:
                        pass
            self.pid, self.owned, self._handle = None, False, None

    # ------------------------------------------------------------ operations

    def calc_save(self, src: str, dst: str, timeout: float | None = None) -> CalcResult:
        """Open src, recalculate, collect per-region COM errors, save as .xmcd to dst."""
        src, dst = os.path.abspath(src), os.path.abspath(dst)

        def go(app):
            ws = app.Worksheets.Open(src)
            try:
                ws.Recalculate()
                com = []
                regs = ws.Regions
                for k in range(regs.Count):
                    r = regs.Item(k)
                    t, err = r.Type, ""
                    if t == 1:
                        try:
                            mi = r.MathInterface
                            if mi.HasError:
                                err = mi.ErrorMsg or "error"
                        except pywintypes.com_error:
                            pass
                    com.append(ComRegion(t, r.X * PT_PER_PX, r.Y * PT_PER_PX, err))
                ws.SaveAs(dst, FORMATS["xmcd"])
            finally:
                ws.Close(2)
            return com

        com = self.run(go, timeout)
        try:
            with open(dst, encoding="utf-8-sig") as fh:
                return CalcResult(fh.read(), com)
        except OSError as e:
            raise EngineError(f"Mathcad не сохранил {dst}: {e}") from None

    def export(self, src: str, out: str, fmt: str, timeout: float | None = None) -> str:
        """Recalculate src and save it as fmt (xmcd, xmcdz, rtf, html, pdf) to out."""
        src, out = os.path.abspath(src), os.path.abspath(out)
        fmt = fmt.lower()
        if fmt != "pdf" and fmt not in FORMATS:
            raise EngineError(f"формат {fmt}: доступны {', '.join([*FORMATS, 'pdf'])}")
        tmp = tempfile.mkdtemp(prefix="mc15pdf_") if fmt == "pdf" else None
        target = out if tmp is None else os.path.join(tmp, "sheet.rtf")

        def go(app):
            ws = app.Worksheets.Open(src)
            try:
                ws.Recalculate()
                ws.SaveAs(target, FORMATS["rtf" if fmt == "pdf" else fmt])
            finally:
                ws.Close(2)

        try:
            self.run(go, timeout)
            if not os.path.exists(target):
                raise EngineError(f"Mathcad не создал {target}")
            if tmp is not None:
                self.run(lambda _app: _word_pdf(target, out), timeout)
                if not os.path.exists(out):
                    raise EngineError(f"Word не создал {out}")
        finally:
            if tmp is not None:
                shutil.rmtree(tmp, ignore_errors=True)
        return out


def _word_pdf(rtf: str, pdf: str):
    """RTF -> PDF through Word; quits Word only if this call started it."""
    try:
        word = win32com.client.DispatchEx("Word.Application")
    except pywintypes.com_error as e:
        raise EngineError(f"PDF делается через Microsoft Word, а он недоступен ({_com_text(e)}); "
                          f"экспортируйте в rtf или html") from None
    ours = False
    try:
        ours = word.Documents.Count == 0 and not word.Visible
        word.DisplayAlerts = 0
    except pywintypes.com_error:
        pass
    doc = None
    try:
        doc = word.Documents.Open(rtf, False, True, False)   # ConfirmConversions, ReadOnly, AddToRecentFiles
        doc.ExportAsFixedFormat(pdf, _WD_PDF)
    finally:
        if doc is not None:
            try:
                doc.Close(0)
            except pywintypes.com_error:
                pass
        if ours:
            try:
                word.Quit(0)
            except pywintypes.com_error:
                pass
