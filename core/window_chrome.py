"""Controlador isolado para uma moldura HTML sobre uma janela Win32.

O modulo e importavel fora do Windows para que a geometria e a resolucao das
flags possam ser testadas. Chamadas nativas ficam concentradas em
``Win32WindowAdapter`` e so sao construidas quando realmente necessarias.
"""

from __future__ import annotations

import ctypes
import logging
import math
import os
import sys
import threading
from ctypes import wintypes
from dataclasses import dataclass
from typing import Any, Mapping, Sequence


logger = logging.getLogger(__name__)

# Window messages
WM_SIZE = 0x0005
WM_CLOSE = 0x0010
WM_GETMINMAXINFO = 0x0024
WM_NCCALCSIZE = 0x0083
WM_NCHITTEST = 0x0084
WM_NCLBUTTONDOWN = 0x00A1
WM_NCDESTROY = 0x0082
WM_DPICHANGED = 0x02E0

# Hit-test values
HTNOWHERE = 0
HTCLIENT = 1
HTCAPTION = 2
HTLEFT = 10
HTRIGHT = 11
HTTOP = 12
HTTOPLEFT = 13
HTTOPRIGHT = 14
HTBOTTOM = 15
HTBOTTOMLEFT = 16
HTBOTTOMRIGHT = 17
HTMAXBUTTON = 9

# Window styles and positioning flags
GWL_STYLE = -16
GWLP_WNDPROC = -4
WS_CAPTION = 0x00C00000
WS_THICKFRAME = 0x00040000
WS_MINIMIZEBOX = 0x00020000
WS_MAXIMIZEBOX = 0x00010000
WS_SYSMENU = 0x00080000
REQUIRED_WINDOW_STYLES = WS_THICKFRAME | WS_MINIMIZEBOX | WS_MAXIMIZEBOX | WS_SYSMENU

SW_HIDE = 0
SW_MINIMIZE = 6
SW_MAXIMIZE = 3
SW_RESTORE = 9
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_FRAMECHANGED = 0x0020
MONITOR_DEFAULTTONEAREST = 2
SPI_GETWORKAREA = 0x0030

# Metricas do frame redimensionavel. SM_CXPADDEDBORDER e a faixa invisivel que
# o Windows acrescenta ao redor da borda visivel e responde pela maior parte da
# area agarravel com o mouse.
SM_CXSIZEFRAME = 32
SM_CYSIZEFRAME = 33
SM_CXPADDEDBORDER = 92

DEFAULT_TITLEBAR_HEIGHT = 36.0
DEFAULT_RESIZE_BORDER = 8.0
MAX_DRAG_REGIONS = 16

# Tamanho restaurado padrao: fracao da area de trabalho do monitor atual. O
# dashboard nao e responsivo abaixo de 1024 px logicos, entao a janela nunca
# abre pequena; o minimo real continua sendo ``min_size`` do pywebview.
DEFAULT_RESTORED_FRACTION = 0.8


@dataclass(frozen=True)
class Rect:
    """Retangulo em coordenadas relativas a janela, com limite direito aberto."""

    x: float
    y: float
    width: float
    height: float

    @property
    def right(self) -> float:
        return self.x + self.width

    @property
    def bottom(self) -> float:
        return self.y + self.height

    def contains(self, x: float, y: float) -> bool:
        return self.x <= x < self.right and self.y <= y < self.bottom

    def intersects(self, other: "Rect") -> bool:
        return (
            self.x < other.right
            and self.right > other.x
            and self.y < other.bottom
            and self.bottom > other.y
        )


@dataclass(frozen=True)
class ChromeRegions:
    titlebar: Rect
    draggable: tuple[Rect, ...]
    minimize_button: Rect | None = None
    maximize_button: Rect | None = None
    close_button: Rect | None = None


@dataclass(frozen=True)
class ChromeModeDecision:
    mode: str
    custom_requested: bool
    native_requested: bool
    conflict: bool
    reason: str


def scale_for_dpi(value: float, dpi: int) -> int:
    """Converte pixels logicos (CSS/96 DPI) para pixels fisicos."""
    if not math.isfinite(float(value)) or value < 0:
        raise ValueError("value deve ser finito e nao negativo")
    if not isinstance(dpi, int) or dpi <= 0:
        raise ValueError("dpi deve ser um inteiro positivo")
    return int(round(float(value) * dpi / 96.0))


def signed_word(value: int) -> int:
    """Interpreta a metade de 16 bits de um LPARAM como coordenada assinada."""
    value &= 0xFFFF
    return value - 0x10000 if value & 0x8000 else value


def point_from_lparam(lparam: int) -> tuple[int, int]:
    return signed_word(lparam), signed_word(lparam >> 16)


def _scaled_rect(rect: Rect, dpi: int) -> Rect:
    return Rect(
        scale_for_dpi(rect.x, dpi),
        scale_for_dpi(rect.y, dpi),
        scale_for_dpi(rect.width, dpi),
        scale_for_dpi(rect.height, dpi),
    )


def hit_test(
    screen_x: int,
    screen_y: int,
    window_rect: Rect,
    *,
    dpi: int = 96,
    resize_border: float = DEFAULT_RESIZE_BORDER,
    titlebar: Rect | None = None,
    draggable: Sequence[Rect] = (),
    maximize_button: Rect | None = None,
    client_offset: tuple[int, int] = (0, 0),
) -> int:
    """Classifica um ponto de tela para ``WM_NCHITTEST``.

    ``window_rect`` usa pixels fisicos de tela. As demais regioes usam pixels
    logicos relativos ao canto superior esquerdo da janela. Cantos e bordas tem
    prioridade, inclusive sobre a barra, como em uma janela nativa.
    """
    if not window_rect.contains(screen_x, screen_y):
        return HTCLIENT

    border = max(1, scale_for_dpi(resize_border, dpi))
    window_x = screen_x - window_rect.x
    window_y = screen_y - window_rect.y
    on_left = window_x < border
    on_right = window_x >= window_rect.width - border
    on_top = window_y < border
    on_bottom = window_y >= window_rect.height - border

    if on_top and on_left:
        return HTTOPLEFT
    if on_top and on_right:
        return HTTOPRIGHT
    if on_bottom and on_left:
        return HTBOTTOMLEFT
    if on_bottom and on_right:
        return HTBOTTOMRIGHT
    if on_left:
        return HTLEFT
    if on_right:
        return HTRIGHT
    if on_top:
        return HTTOP
    if on_bottom:
        return HTBOTTOM

    # Os retangulos do DOM sao relativos ao viewport do WebView (area cliente),
    # que pode comecar alguns pixels dentro do retangulo externo por causa de
    # WS_THICKFRAME. Bordas continuam relativas ao retangulo externo.
    local_x = window_x - client_offset[0]
    local_y = window_y - client_offset[1]

    if maximize_button and _scaled_rect(maximize_button, dpi).contains(local_x, local_y):
        return HTMAXBUTTON

    candidates = tuple(draggable)
    if not candidates and titlebar is not None:
        candidates = (titlebar,)
    if any(_scaled_rect(region, dpi).contains(local_x, local_y) for region in candidates):
        return HTCAPTION
    return HTCLIENT


def default_restored_rect(
    work: Rect,
    *,
    fraction: float = DEFAULT_RESTORED_FRACTION,
    min_width: float = 0.0,
    min_height: float = 0.0,
) -> Rect:
    """Retangulo restaurado padrao: ``fraction`` da area util, centralizado.

    Todos os valores estao em pixels fisicos de tela. O minimo tem prioridade
    sobre a fracao, e a area util tem prioridade sobre o minimo — uma janela
    nunca deve nascer maior que o monitor em que sera exibida.
    """
    if not 0 < fraction <= 1:
        raise ValueError("fraction deve estar em (0, 1]")
    if work.width <= 0 or work.height <= 0:
        raise ValueError("area util invalida")

    width = min(work.width, max(min_width, round(work.width * fraction)))
    height = min(work.height, max(min_height, round(work.height * fraction)))
    return Rect(
        work.x + round((work.width - width) / 2),
        work.y + round((work.height - height) / 2),
        width,
        height,
    )


def resolve_window_chrome_mode(
    argv: Sequence[str] | None = None,
    *,
    is_windows: bool | None = None,
    log: logging.Logger | None = None,
) -> ChromeModeDecision:
    """Resolve as flags de startup; o modo nativo sempre tem precedencia."""
    args = tuple(sys.argv[1:] if argv is None else argv)
    custom = "--custom-titlebar" in args
    native = "--native-titlebar" in args
    conflict = custom and native
    windows = os.name == "nt" if is_windows is None else bool(is_windows)

    if native:
        reason = "--native-titlebar tem precedencia" if conflict else "modo nativo solicitado"
        mode = "native"
    elif custom and windows:
        reason = "modo personalizado solicitado"
        mode = "custom"
    elif custom:
        reason = "modo personalizado indisponivel fora do Windows"
        mode = "native"
    else:
        reason = "modo nativo e o padrao nas fases 1 e 2"
        mode = "native"

    decision = ChromeModeDecision(mode, custom, native, conflict, reason)
    target_log = log or logger
    if conflict:
        target_log.warning(
            "Flags --custom-titlebar e --native-titlebar fornecidas; usando moldura nativa."
        )
    elif custom and not windows:
        target_log.warning("--custom-titlebar ignorado: controlador disponivel somente no Windows.")
    return decision


def resolve_titlebar_mode(argv: Sequence[str] | None = None, **kwargs: Any) -> str:
    """Atalho para consumidores que precisam apenas de ``custom`` ou ``native``."""
    return resolve_window_chrome_mode(argv, **kwargs).mode


class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class NATIVE_RECT(ctypes.Structure):
    _fields_ = [
        ("left", wintypes.LONG),
        ("top", wintypes.LONG),
        ("right", wintypes.LONG),
        ("bottom", wintypes.LONG),
    ]


class MINMAXINFO(ctypes.Structure):
    _fields_ = [
        ("ptReserved", POINT),
        ("ptMaxSize", POINT),
        ("ptMaxPosition", POINT),
        ("ptMinTrackSize", POINT),
        ("ptMaxTrackSize", POINT),
    ]


class MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", NATIVE_RECT),
        ("rcWork", NATIVE_RECT),
        ("dwFlags", wintypes.DWORD),
    ]


class WINDOWPLACEMENT(ctypes.Structure):
    _fields_ = [
        ("length", wintypes.UINT),
        ("flags", wintypes.UINT),
        ("showCmd", wintypes.UINT),
        ("ptMinPosition", POINT),
        ("ptMaxPosition", POINT),
        ("rcNormalPosition", NATIVE_RECT),
    ]


def _rect_from_native(rect: NATIVE_RECT) -> Rect:
    return Rect(rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top)


class Win32WindowAdapter:
    """Fachada fina e mockavel sobre user32/dwmapi."""

    supported = os.name == "nt"

    def __init__(self) -> None:
        if not self.supported:
            raise RuntimeError("Window chrome Win32 disponivel somente no Windows")
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)
        self._configure_signatures()

    def _configure_signatures(self) -> None:
        long_ptr = ctypes.c_ssize_t
        lresult = ctypes.c_ssize_t
        hwnd = wintypes.HWND

        self.user32.GetWindowLongPtrW.argtypes = [hwnd, ctypes.c_int]
        self.user32.GetWindowLongPtrW.restype = long_ptr
        self.user32.SetWindowLongPtrW.argtypes = [hwnd, ctypes.c_int, long_ptr]
        self.user32.SetWindowLongPtrW.restype = long_ptr
        self.user32.CallWindowProcW.argtypes = [long_ptr, hwnd, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        self.user32.CallWindowProcW.restype = lresult
        self.user32.DefWindowProcW.argtypes = [hwnd, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        self.user32.DefWindowProcW.restype = lresult
        self.user32.GetWindowRect.argtypes = [hwnd, ctypes.POINTER(NATIVE_RECT)]
        self.user32.GetWindowRect.restype = wintypes.BOOL
        self.user32.GetClientRect.argtypes = [hwnd, ctypes.POINTER(NATIVE_RECT)]
        self.user32.GetClientRect.restype = wintypes.BOOL
        self.user32.ClientToScreen.argtypes = [hwnd, ctypes.POINTER(POINT)]
        self.user32.ClientToScreen.restype = wintypes.BOOL
        self.user32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
        self.user32.GetCursorPos.restype = wintypes.BOOL
        self.user32.GetDpiForWindow.argtypes = [hwnd]
        self.user32.GetDpiForWindow.restype = wintypes.UINT
        self.user32.SetWindowPos.argtypes = [hwnd, hwnd, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
        self.user32.SetWindowPos.restype = wintypes.BOOL
        self.user32.ShowWindow.argtypes = [hwnd, ctypes.c_int]
        self.user32.ShowWindow.restype = wintypes.BOOL
        self.user32.IsZoomed.argtypes = [hwnd]
        self.user32.IsZoomed.restype = wintypes.BOOL
        self.user32.IsIconic.argtypes = [hwnd]
        self.user32.IsIconic.restype = wintypes.BOOL
        self.user32.PostMessageW.argtypes = [hwnd, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        self.user32.PostMessageW.restype = wintypes.BOOL
        self.user32.SendMessageW.argtypes = [hwnd, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        self.user32.SendMessageW.restype = ctypes.c_ssize_t
        self.user32.ReleaseCapture.argtypes = []
        self.user32.ReleaseCapture.restype = wintypes.BOOL
        self.user32.MonitorFromWindow.argtypes = [hwnd, wintypes.DWORD]
        self.user32.MonitorFromWindow.restype = wintypes.HANDLE
        self.user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MONITORINFO)]
        self.user32.GetMonitorInfoW.restype = wintypes.BOOL
        self.user32.GetWindowPlacement.argtypes = [hwnd, ctypes.POINTER(WINDOWPLACEMENT)]
        self.user32.GetWindowPlacement.restype = wintypes.BOOL
        self.user32.SetWindowPlacement.argtypes = [hwnd, ctypes.POINTER(WINDOWPLACEMENT)]
        self.user32.SetWindowPlacement.restype = wintypes.BOOL
        self.user32.SystemParametersInfoW.argtypes = [
            wintypes.UINT, wintypes.UINT, ctypes.c_void_p, wintypes.UINT
        ]
        self.user32.SystemParametersInfoW.restype = wintypes.BOOL
        self.user32.GetSystemMetrics.argtypes = [ctypes.c_int]
        self.user32.GetSystemMetrics.restype = ctypes.c_int
        try:
            self.user32.GetSystemMetricsForDpi.argtypes = [ctypes.c_int, wintypes.UINT]
            self.user32.GetSystemMetricsForDpi.restype = ctypes.c_int
        except AttributeError:  # Windows anterior ao 10 1607
            pass
        self.dwmapi.DwmDefWindowProc.argtypes = [hwnd, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM, ctypes.POINTER(lresult)]
        self.dwmapi.DwmDefWindowProc.restype = wintypes.BOOL

        self.wndproc_type = ctypes.WINFUNCTYPE(
            lresult, hwnd, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
        )

    @staticmethod
    def _raise_last_error(operation: str) -> None:
        code = ctypes.get_last_error()
        raise OSError(code, f"{operation} falhou", None, code)

    def get_hwnd(self, window: Any) -> int:
        native = getattr(window, "native", None)
        handle = getattr(native, "Handle", None)
        if handle is None:
            raise RuntimeError("window.native.Handle ainda nao esta disponivel")
        if hasattr(handle, "ToInt64"):
            value = int(handle.ToInt64())
        elif hasattr(handle, "ToInt32"):
            value = int(handle.ToInt32())
        else:
            value = int(handle)
        if not value:
            raise RuntimeError("HWND invalido")
        return value

    def get_style(self, hwnd: int) -> int:
        ctypes.set_last_error(0)
        value = int(self.user32.GetWindowLongPtrW(hwnd, GWL_STYLE))
        if value == 0 and ctypes.get_last_error():
            self._raise_last_error("GetWindowLongPtrW(GWL_STYLE)")
        return value

    def set_style(self, hwnd: int, style: int) -> None:
        ctypes.set_last_error(0)
        previous = self.user32.SetWindowLongPtrW(hwnd, GWL_STYLE, style)
        if previous == 0 and ctypes.get_last_error():
            self._raise_last_error("SetWindowLongPtrW(GWL_STYLE)")

    def get_wndproc(self, hwnd: int) -> int:
        ctypes.set_last_error(0)
        value = int(self.user32.GetWindowLongPtrW(hwnd, GWLP_WNDPROC))
        if value == 0 and ctypes.get_last_error():
            self._raise_last_error("GetWindowLongPtrW(GWLP_WNDPROC)")
        if not value:
            raise RuntimeError("WNDPROC original invalido")
        return value

    def create_wndproc(self, function: Any) -> Any:
        return self.wndproc_type(function)

    @staticmethod
    def callback_address(callback: Any) -> int:
        value = ctypes.cast(callback, ctypes.c_void_p).value
        if not value:
            raise RuntimeError("callback WNDPROC invalido")
        return int(value)

    def set_wndproc(self, hwnd: int, proc: int | Any) -> None:
        address = proc if isinstance(proc, int) else self.callback_address(proc)
        ctypes.set_last_error(0)
        previous = self.user32.SetWindowLongPtrW(hwnd, GWLP_WNDPROC, address)
        if previous == 0 and ctypes.get_last_error():
            self._raise_last_error("SetWindowLongPtrW(GWLP_WNDPROC)")

    def call_wndproc(self, proc: int, hwnd: int, msg: int, wparam: int, lparam: int) -> int:
        return int(self.user32.CallWindowProcW(proc, hwnd, msg, wparam, lparam))

    def def_wndproc(self, hwnd: int, msg: int, wparam: int, lparam: int) -> int:
        return int(self.user32.DefWindowProcW(hwnd, msg, wparam, lparam))

    def dwm_def_window_proc(self, hwnd: int, msg: int, wparam: int, lparam: int) -> tuple[bool, int]:
        result = ctypes.c_ssize_t()
        handled = bool(self.dwmapi.DwmDefWindowProc(hwnd, msg, wparam, lparam, ctypes.byref(result)))
        return handled, int(result.value)

    def frame_changed(self, hwnd: int) -> None:
        flags = SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED
        if not self.user32.SetWindowPos(hwnd, None, 0, 0, 0, 0, flags):
            self._raise_last_error("SetWindowPos(SWP_FRAMECHANGED)")

    def get_window_rect(self, hwnd: int) -> Rect:
        rect = NATIVE_RECT()
        if not self.user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            self._raise_last_error("GetWindowRect")
        return _rect_from_native(rect)

    def get_client_size(self, hwnd: int) -> tuple[int, int]:
        rect = NATIVE_RECT()
        if not self.user32.GetClientRect(hwnd, ctypes.byref(rect)):
            self._raise_last_error("GetClientRect")
        return rect.right - rect.left, rect.bottom - rect.top

    def get_client_offset(self, hwnd: int) -> tuple[int, int]:
        origin = POINT(0, 0)
        if not self.user32.ClientToScreen(hwnd, ctypes.byref(origin)):
            self._raise_last_error("ClientToScreen")
        window = self.get_window_rect(hwnd)
        return int(origin.x - window.x), int(origin.y - window.y)

    def get_cursor_pos(self) -> tuple[int, int]:
        point = POINT()
        if not self.user32.GetCursorPos(ctypes.byref(point)):
            self._raise_last_error("GetCursorPos")
        return int(point.x), int(point.y)

    def get_dpi(self, hwnd: int) -> int:
        return int(self.user32.GetDpiForWindow(hwnd) or 96)

    def show_window(self, hwnd: int, command: int) -> None:
        self.user32.ShowWindow(hwnd, command)

    def is_zoomed(self, hwnd: int) -> bool:
        return bool(self.user32.IsZoomed(hwnd))

    def is_iconic(self, hwnd: int) -> bool:
        return bool(self.user32.IsIconic(hwnd))

    def close_window(self, hwnd: int) -> None:
        if not self.user32.PostMessageW(hwnd, WM_CLOSE, 0, 0):
            self._raise_last_error("PostMessageW(WM_CLOSE)")

    def begin_drag(self, hwnd: int) -> None:
        # WebView2 windowed usa um HWND filho e, portanto, recebe o mouse antes
        # do formulario pai. A mensagem precisa carregar a posicao real do
        # cursor; (0, 0) pode ficar fora da janela e ser ignorado pelo WinForms,
        # sobretudo em monitores com coordenadas negativas.
        cursor_x, cursor_y = self.get_cursor_pos()
        lparam = (cursor_x & 0xFFFF) | ((cursor_y & 0xFFFF) << 16)
        self.user32.ReleaseCapture()
        self.user32.SendMessageW(hwnd, WM_NCLBUTTONDOWN, HTCAPTION, lparam)

    def get_monitor_rects(self, hwnd: int) -> tuple[Rect, Rect]:
        """Retangulo total e area util do monitor que contem a janela."""
        monitor = self.user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
        if not monitor:
            raise RuntimeError("MonitorFromWindow nao encontrou um monitor")
        info = MONITORINFO(cbSize=ctypes.sizeof(MONITORINFO))
        if not self.user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            self._raise_last_error("GetMonitorInfoW")
        return (_rect_from_native(info.rcMonitor), _rect_from_native(info.rcWork))

    def get_placement(self, hwnd: int) -> WINDOWPLACEMENT:
        placement = WINDOWPLACEMENT(length=ctypes.sizeof(WINDOWPLACEMENT))
        if not self.user32.GetWindowPlacement(hwnd, ctypes.byref(placement)):
            self._raise_last_error("GetWindowPlacement")
        return placement

    def set_placement(self, hwnd: int, placement: WINDOWPLACEMENT) -> None:
        placement.length = ctypes.sizeof(WINDOWPLACEMENT)
        if not self.user32.SetWindowPlacement(hwnd, ctypes.byref(placement)):
            self._raise_last_error("SetWindowPlacement")

    def _workspace_origin(self) -> tuple[int, int]:
        """Origem das coordenadas de ``rcNormalPosition`` (workspace coordinates).

        WINDOWPLACEMENT nao usa coordenadas de tela: usa a area util do monitor
        primario como origem. Com a barra de tarefas embaixo a diferenca e zero,
        mas com ela a esquerda ou no topo a janela nasceria deslocada.
        """
        work = NATIVE_RECT()
        if not self.user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(work), 0):
            return (0, 0)
        return (int(work.left), int(work.top))

    def set_normal_position(self, hwnd: int, rect: Rect) -> None:
        """Define o retangulo restaurado sem tirar a janela do estado atual."""
        placement = self.get_placement(hwnd)
        offset_x, offset_y = self._workspace_origin()
        placement.rcNormalPosition = NATIVE_RECT(
            left=int(rect.x) - offset_x,
            top=int(rect.y) - offset_y,
            right=int(rect.x + rect.width) - offset_x,
            bottom=int(rect.y + rect.height) - offset_y,
        )
        self.set_placement(hwnd, placement)

    def set_window_rect(self, hwnd: int, rect: Rect) -> None:
        flags = SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED
        if not self.user32.SetWindowPos(
            hwnd, None, int(rect.x), int(rect.y), int(rect.width), int(rect.height), flags
        ):
            self._raise_last_error("SetWindowPos(set_window_rect)")

    def get_system_metric(self, index: int, dpi: int) -> int:
        try:
            return int(self.user32.GetSystemMetricsForDpi(index, dpi))
        except AttributeError:
            return int(self.user32.GetSystemMetrics(index))

    def frame_borders(self, hwnd: int) -> tuple[int, int]:
        """Espessura horizontal e vertical do frame redimensionavel, em pixels fisicos."""
        dpi = self.get_dpi(hwnd)
        padded = self.get_system_metric(SM_CXPADDEDBORDER, dpi)
        horizontal = self.get_system_metric(SM_CXSIZEFRAME, dpi) + padded
        vertical = self.get_system_metric(SM_CYSIZEFRAME, dpi) + padded
        return (max(1, horizontal), max(1, vertical))

    def apply_nccalcsize_borders(self, hwnd: int, lparam: int) -> None:
        rect = ctypes.cast(lparam, ctypes.POINTER(NATIVE_RECT)).contents
        horizontal, vertical = self.frame_borders(hwnd)
        # Nunca recuar a ponto de zerar a area cliente: durante minimizar e
        # restaurar o Windows propoe retangulos degenerados.
        if rect.right - rect.left > 2 * horizontal:
            rect.left += horizontal
            rect.right -= horizontal
        if rect.bottom - rect.top > 2 * vertical:
            rect.top += vertical
            rect.bottom -= vertical

    def apply_dpi_rect(self, hwnd: int, lparam: int) -> None:
        suggested = ctypes.cast(lparam, ctypes.POINTER(NATIVE_RECT)).contents
        width = suggested.right - suggested.left
        height = suggested.bottom - suggested.top
        if width > 0 and height > 0:
            if not self.user32.SetWindowPos(
                hwnd, None, suggested.left, suggested.top, width, height, SWP_NOZORDER | SWP_NOACTIVATE
            ):
                self._raise_last_error("SetWindowPos(WM_DPICHANGED)")

    def apply_minmax_info(
        self, hwnd: int, lparam: int, min_track: tuple[int, int] | None = None
    ) -> None:
        try:
            screen, work = self.get_monitor_rects(hwnd)
        except Exception:
            return
        minmax = ctypes.cast(lparam, ctypes.POINTER(MINMAXINFO)).contents
        minmax.ptMaxPosition.x = int(work.x - screen.x)
        minmax.ptMaxPosition.y = int(work.y - screen.y)
        minmax.ptMaxSize.x = int(work.width)
        minmax.ptMaxSize.y = int(work.height)
        if min_track:
            # O WinForms aplica o minimo a janela inteira. Como a moldura come
            # a espessura do frame de cada lado, sem isto o viewport do
            # dashboard cairia abaixo dos 1024 px logicos exigidos.
            minmax.ptMinTrackSize.x = max(int(minmax.ptMinTrackSize.x), int(min_track[0]))
            minmax.ptMinTrackSize.y = max(int(minmax.ptMinTrackSize.y), int(min_track[1]))

    def restore_native_frame(self, hwnd: int, previous_style: int) -> None:
        self.set_style(hwnd, previous_style | REQUIRED_WINDOW_STYLES | WS_CAPTION)
        self.frame_changed(hwnd)

    def diagnostic(self) -> dict[str, Any]:
        return {
            "supported": True,
            "pointer_bits": ctypes.sizeof(ctypes.c_void_p) * 8,
            "apis": [
                "GetWindowLongPtrW",
                "SetWindowLongPtrW",
                "CallWindowProcW",
                "DwmDefWindowProc",
                "GetDpiForWindow",
            ],
        }


class WindowChromeController:
    """Instala e remove com seguranca o procedimento de janela personalizado."""

    def __init__(self, adapter: Any | None = None, *, log: logging.Logger | None = None) -> None:
        self._adapter = adapter
        self._log = log or logger
        self._lock = threading.RLock()
        self._window: Any | None = None
        self._hwnd: int | None = None
        self._old_style: int | None = None
        self._old_wndproc: int | None = None
        self._callback: Any | None = None
        self._attached = False
        self._fallback = False
        self._last_error: str | None = None
        self._dpi = 96
        self._regions: ChromeRegions | None = None
        # Placement salvo ao entrar em tela cheia; ``None`` significa "nao esta
        # em tela cheia" e e a unica fonte de verdade desse estado.
        self._fullscreen_placement: Any | None = None
        self._nonclient = False
        self._nonclient_handler: Any | None = None
        self._min_client_size: tuple[float, float] | None = None

    @property
    def attached(self) -> bool:
        return self._attached

    @property
    def callback(self) -> Any | None:
        """Referencia viva, exposta apenas para diagnosticos e testes."""
        return self._callback

    @property
    def last_error(self) -> str | None:
        return self._last_error

    def _native(self) -> Any:
        if self._adapter is None:
            self._adapter = Win32WindowAdapter()
        return self._adapter

    def attach(self, window: Any) -> bool:
        """Anexa o hook. Falhas sao registradas e convertem a janela para frame nativo."""
        with self._lock:
            if self._attached:
                if window is self._window:
                    return True
                self.detach()

            adapter = None
            hwnd = None
            old_style = None
            old_proc = None
            proc_installed = False
            try:
                adapter = self._native()
                if not getattr(adapter, "supported", True):
                    raise RuntimeError("Window chrome Win32 indisponivel neste sistema")
                hwnd = adapter.get_hwnd(window)
                old_style = adapter.get_style(hwnd)
                old_proc = adapter.get_wndproc(hwnd)
                callback = adapter.create_wndproc(self._wnd_proc)

                # Preenche o estado antes de trocar o WNDPROC: SetWindowLongPtrW e
                # SWP_FRAMECHANGED podem entregar mensagens de forma sincrona.
                dpi = adapter.get_dpi(hwnd)
                width_px, height_px = adapter.get_client_size(hwnd)
                width = width_px * 96.0 / dpi
                height = height_px * 96.0 / dpi
                title_height = min(DEFAULT_TITLEBAR_HEIGHT, height)
                default_bar = Rect(0, 0, width, title_height)
                self._window = window
                self._hwnd = hwnd
                self._old_style = old_style
                self._old_wndproc = old_proc
                self._callback = callback
                self._dpi = dpi
                self._regions = ChromeRegions(default_bar, (default_bar,))

                adapter.set_style(hwnd, old_style | REQUIRED_WINDOW_STYLES)
                adapter.set_wndproc(hwnd, callback)
                proc_installed = True
                adapter.frame_changed(hwnd)

                self._attached = True
                self._fallback = False
                self._last_error = None
                self._log.info("Window chrome anexado | hwnd=%s | dpi=%s", hwnd, self._dpi)
                return True
            except Exception as exc:
                self._last_error = f"{type(exc).__name__}: {exc}"
                self._log.exception("Falha ao instalar window chrome; restaurando moldura nativa")
                if adapter is not None and hwnd:
                    try:
                        if proc_installed and old_proc:
                            adapter.set_wndproc(hwnd, old_proc)
                        if old_style is not None:
                            adapter.restore_native_frame(hwnd, old_style)
                            self._fallback = True
                    except Exception:
                        self._log.exception("Falha adicional ao restaurar a moldura nativa")
                self._clear_attachment()
                return False

    def _clear_attachment(self) -> None:
        self._window = None
        self._hwnd = None
        self._old_style = None
        self._old_wndproc = None
        self._callback = None
        self._attached = False
        self._regions = None
        self._fullscreen_placement = None
        self._nonclient = False
        self._nonclient_handler = None

    def _restore(self, *, destroying: bool = False) -> None:
        adapter = self._adapter
        hwnd = self._hwnd
        old_style = self._old_style
        old_proc = self._old_wndproc
        if adapter is None or hwnd is None:
            self._clear_attachment()
            return
        errors: list[Exception] = []
        if old_proc:
            try:
                adapter.set_wndproc(hwnd, old_proc)
            except Exception as exc:
                errors.append(exc)
        if not destroying and old_style is not None:
            try:
                adapter.set_style(hwnd, old_style)
                adapter.frame_changed(hwnd)
            except Exception as exc:
                errors.append(exc)
        self._clear_attachment()
        if errors:
            raise RuntimeError("; ".join(str(error) for error in errors))

    def detach(self) -> bool:
        """Restaura o WNDPROC e os estilos originais; chamadas repetidas sao inofensivas."""
        with self._lock:
            if not self._attached:
                return True
            try:
                self._restore()
                self._log.info("Window chrome desanexado")
                return True
            except Exception as exc:
                self._last_error = f"{type(exc).__name__}: {exc}"
                self._log.exception("Falha ao desanexar window chrome")
                return False

    def arm_nonclient_regions(self, window: Any) -> bool:
        """Agenda o ``app-region`` do WebView2 para o primeiro documento.

        ``IsNonClientRegionSupportEnabled`` so vale para documentos navegados
        depois de ela ser ligada: escrever nela com a pagina ja carregada faz a
        propriedade ler ``True`` sem nenhum efeito. Em ``before_show`` o
        CoreWebView2 ainda nao existe, entao a ativacao vai para o
        ``CoreWebView2InitializationCompleted``, que roda na thread de UI antes
        de o documento ser criado. Chamadas repetidas sao inofensivas.
        """
        with self._lock:
            if self._nonclient_handler is not None:
                return True
            control = _webview_control(window)
            if control is None or not hasattr(control, "CoreWebView2InitializationCompleted"):
                self._log.warning(
                    "Controle WebView2 indisponivel: app-region nao sera ativado."
                )
                return False
            if getattr(control, "CoreWebView2", None) is not None:
                # Tarde demais: o evento ja passou e o documento sera criado sem
                # o recurso. Ligar aqui so produziria um falso positivo.
                self._log.warning(
                    "CoreWebView2 ja inicializado: app-region nao pode mais valer "
                    "para este documento."
                )
                return False

            def _on_webview_ready(sender: Any, _args: Any) -> None:
                try:
                    enabled = enable_nonclient_region_support(sender)
                except Exception:
                    self._log.exception("Falha ao ligar app-region no WebView2")
                    enabled = False
                with self._lock:
                    self._nonclient = enabled
                self._log.info(
                    "Regiao nao-cliente do WebView2 (app-region): %s",
                    "ativa" if enabled else "indisponivel",
                )

            # A referencia precisa sobreviver: o delegate .NET aponta para ela.
            self._nonclient_handler = _on_webview_ready
            control.CoreWebView2InitializationCompleted += _on_webview_ready
            return True

    def minimize(self) -> bool:
        return self._run_window_action("minimize", lambda adapter, hwnd: adapter.show_window(hwnd, SW_MINIMIZE))

    def begin_drag(self) -> bool:
        # O move loop nativo ja sabe restaurar uma janela maximizada sob o
        # cursor e re-maximizar no Aero Snap. Nao replicamos esse
        # comportamento aqui: em monitores com DPI diferente, reposicionar a
        # janela por conta propria antes do loop e o que a fazia "abrir torta".
        return self._run_window_action(
            "begin_drag", lambda adapter, hwnd: adapter.begin_drag(hwnd)
        )

    def toggle_maximize(self) -> bool:
        def action(adapter: Any, hwnd: int) -> None:
            if self._exit_fullscreen(adapter, hwnd):
                return
            adapter.show_window(hwnd, SW_RESTORE if adapter.is_zoomed(hwnd) else SW_MAXIMIZE)

        return self._run_window_action("toggle_maximize", action)

    def toggle_fullscreen(self) -> bool:
        """Alterna entre o estado atual e o monitor inteiro, sem barra de tarefas."""
        def action(adapter: Any, hwnd: int) -> None:
            if self._exit_fullscreen(adapter, hwnd):
                return
            placement = adapter.get_placement(hwnd)
            screen, _ = adapter.get_monitor_rects(hwnd)
            if adapter.is_zoomed(hwnd):
                # SetWindowPos e ignorado enquanto a janela esta maximizada; o
                # placement salvo acima ja registra o estado para a volta.
                adapter.show_window(hwnd, SW_RESTORE)
            # Registrar a tela cheia ANTES de mover: o SWP_FRAMECHANGED de
            # set_window_rect dispara o WM_NCCALCSIZE, e ali ``_is_full_surface``
            # decide se a area cliente e recuada pela moldura. Marcando depois, a
            # janela cobre o monitor mas o conteudo nasce recuado — a faixa
            # visivel entre o app e as bordas da tela.
            self._fullscreen_placement = placement
            try:
                adapter.set_window_rect(hwnd, screen)
            except Exception:
                self._fullscreen_placement = None
                raise
            self._log.info("Tela cheia ativada | monitor=%s", screen)

        return self._run_window_action("toggle_fullscreen", action)

    def _exit_fullscreen(self, adapter: Any, hwnd: int) -> bool:
        if self._fullscreen_placement is None:
            return False
        adapter.set_placement(hwnd, self._fullscreen_placement)
        self._fullscreen_placement = None
        self._log.info("Tela cheia desativada")
        return True

    def apply_default_geometry(
        self,
        min_size: tuple[float, float] = (1024.0, 600.0),
        *,
        fraction: float = DEFAULT_RESTORED_FRACTION,
    ) -> bool:
        """Define o tamanho restaurado padrao sem sair do estado maximizado.

        ``min_size`` e o minimo da **area cliente** em pixels logicos — o espaco
        que o dashboard realmente recebe. Fica guardado para o WM_GETMINMAXINFO.
        """
        self._min_client_size = (float(min_size[0]), float(min_size[1]))

        def action(adapter: Any, hwnd: int) -> None:
            dpi = adapter.get_dpi(hwnd)
            _, work = adapter.get_monitor_rects(hwnd)
            min_width, min_height = self._min_window_size(adapter, hwnd) or (0, 0)
            target = default_restored_rect(
                work, fraction=fraction, min_width=min_width, min_height=min_height
            )
            adapter.set_normal_position(hwnd, target)
            self._log.info(
                "Geometria restaurada padrao | dpi=%s | area_util=%s | minimo=%sx%s | alvo=%s",
                dpi, work, min_width, min_height, target,
            )

        return self._run_window_action("apply_default_geometry", action)

    def _min_window_size(self, adapter: Any, hwnd: int) -> tuple[int, int] | None:
        """Minimo da janela = minimo do conteudo + a moldura que ela perde."""
        if self._min_client_size is None:
            return None
        dpi = adapter.get_dpi(hwnd)
        horizontal, vertical = adapter.frame_borders(hwnd)
        return (
            scale_for_dpi(self._min_client_size[0], dpi) + 2 * horizontal,
            scale_for_dpi(self._min_client_size[1], dpi) + 2 * vertical,
        )

    def close(self) -> bool:
        return self._run_window_action("close", lambda adapter, hwnd: adapter.close_window(hwnd))

    def _run_window_action(self, name: str, action: Any) -> bool:
        with self._lock:
            if not self._attached or self._hwnd is None:
                self._last_error = f"{name}: controlador nao anexado"
                return False
            try:
                action(self._native(), self._hwnd)
                self._last_error = None
                return True
            except Exception as exc:
                self._last_error = f"{type(exc).__name__}: {exc}"
                self._log.exception("Falha na acao de janela %s", name)
                return False

    def get_state(self) -> dict[str, Any]:
        with self._lock:
            state = "normal"
            dpi = self._dpi if self._attached else None
            ok = True
            if self._attached and self._hwnd is not None:
                try:
                    adapter = self._native()
                    dpi = adapter.get_dpi(self._hwnd)
                    if adapter.is_iconic(self._hwnd):
                        state = "minimized"
                    elif self._fullscreen_placement is not None:
                        state = "fullscreen"
                    elif adapter.is_zoomed(self._hwnd):
                        state = "maximized"
                except Exception as exc:
                    ok = False
                    self._last_error = f"{type(exc).__name__}: {exc}"
            result: dict[str, Any] = {
                "ok": ok and self._last_error is None,
                "mode": "custom" if self._attached else "native",
                "attached": self._attached,
                "dpi": dpi,
                "state": state,
                # O frontend so recorre ao arraste por mensagem quando o
                # WebView2 nao assume a regiao nao-cliente.
                "nonclient": self._nonclient,
            }
            if self._fallback:
                result["fallback"] = True
            if self._last_error:
                result["error"] = self._last_error
            return result

    def set_regions(self, payload: Mapping[str, Any]) -> bool:
        """Valida regioes CSS e as troca atomicamente; payload invalido nao e aplicado."""
        with self._lock:
            if not self._attached or self._hwnd is None:
                self._last_error = "set_regions: controlador nao anexado"
                return False
            try:
                width_px, height_px = self._native().get_client_size(self._hwnd)
                dpi = self._native().get_dpi(self._hwnd)
                width = width_px * 96.0 / dpi
                height = height_px * 96.0 / dpi
                # WebView2 e WinForms podem divergir por ate quatro pixels
                # fisicos ao arredondar DPI fracionario. A folga so existe
                # acima de 96 DPI e permanece pequena e explicitamente limitada.
                tolerance = 0.01 if dpi == 96 else 4.0 * 96.0 / dpi
                regions = validate_regions(payload, width, height, tolerance=tolerance)
                self._regions = regions
                self._dpi = dpi
                self._last_error = None
                return True
            except Exception as exc:
                self._last_error = f"{type(exc).__name__}: {exc}"
                self._log.warning(
                    "Regioes de window chrome rejeitadas: %s | cliente_logico=%.2fx%.2f | dpi=%s",
                    exc,
                    locals().get("width", -1.0),
                    locals().get("height", -1.0),
                    locals().get("dpi", self._dpi),
                )
                return False

    def _is_full_surface(self, adapter: Any, hwnd: int) -> bool:
        """A janela ocupa o monitor inteiro e nao deve exibir moldura."""
        return self._fullscreen_placement is not None or bool(adapter.is_zoomed(hwnd))

    def _wnd_proc(self, hwnd: int, msg: int, wparam: int, lparam: int) -> int:
        adapter = self._adapter
        old_proc = self._old_wndproc
        if adapter is None or not old_proc:
            return 0
        try:
            # O WebView2 e um HWND filho de outro processo e ocupa toda a area
            # cliente: pontos sobre ele nunca chegam a este WM_NCHITTEST. Se a
            # area cliente for a janela inteira, nao sobra faixa alguma para
            # redimensionar. Por isso devolvemos uma area cliente recuada pela
            # espessura do frame, e o WebView2, ancorado nela, deixa a moldura
            # nativa exposta nos quatro lados. Maximizada ou em tela cheia nao
            # ha o que redimensionar e a faixa viraria uma borda indesejada.
            #
            # Tanto a forma simples (wParam=FALSE) quanto NCCALCSIZE_PARAMS
            # (wParam=TRUE) comecam pelo retangulo proposto, entao o mesmo
            # ponteiro serve para as duas.
            if msg == WM_NCCALCSIZE:
                if lparam and not self._is_full_surface(adapter, hwnd):
                    adapter.apply_nccalcsize_borders(hwnd, lparam)
                return 0
            if msg == WM_NCHITTEST:
                handled, result = adapter.dwm_def_window_proc(hwnd, msg, wparam, lparam)
                # No frame WinForms sem caption, algumas versoes do DWM marcam
                # HTCLIENT como tratado. Essa resposta nao pode impedir nossas
                # regioes de drag/resize; respostas realmente nao-cliente (em
                # especial HTMAXBUTTON no Windows 11) continuam prioritarias.
                if handled and result not in (HTNOWHERE, HTCLIENT):
                    return result
                x, y = point_from_lparam(lparam)
                window_rect = adapter.get_window_rect(hwnd)
                regions = self._regions
                dpi = adapter.get_dpi(hwnd)
                client_offset = (
                    adapter.get_client_offset(hwnd)
                    if hasattr(adapter, "get_client_offset")
                    else (0, 0)
                )
                return hit_test(
                    x,
                    y,
                    window_rect,
                    dpi=dpi,
                    titlebar=regions.titlebar if regions else None,
                    draggable=regions.draggable if regions else (),
                    maximize_button=regions.maximize_button if regions else None,
                    client_offset=client_offset,
                )
            if msg == WM_GETMINMAXINFO and lparam:
                # O WinForms aplica ``MinimumSize`` (o ``min_size`` do
                # pywebview) nesta mensagem. Encaminhar primeiro preserva o
                # limite minimo; so depois limitamos o tamanho maximizado a
                # area util, senao a janela sem moldura vaza para fora do
                # monitor pela espessura do frame.
                result = adapter.call_wndproc(old_proc, hwnd, msg, wparam, lparam)
                adapter.apply_minmax_info(hwnd, lparam, self._min_window_size(adapter, hwnd))
                return result
            if msg == WM_DPICHANGED and lparam:
                self._dpi = (wparam >> 16) & 0xFFFF or wparam & 0xFFFF or 96
                adapter.apply_dpi_rect(hwnd, lparam)
                return 0
            if msg == WM_SIZE:
                self._dpi = adapter.get_dpi(hwnd)
            if msg == WM_NCDESTROY:
                callback = self._callback  # mantem a thunk viva ate CallWindowProc retornar
                original = old_proc
                try:
                    self._restore(destroying=True)
                except Exception:
                    self._log.exception("Falha ao restaurar WNDPROC durante WM_NCDESTROY")
                result = adapter.call_wndproc(original, hwnd, msg, wparam, lparam)
                del callback
                return result
        except Exception:
            self._log.exception("Erro no WNDPROC customizado (msg=0x%04X)", msg)
        return adapter.call_wndproc(old_proc, hwnd, msg, wparam, lparam)

    def diagnostic(self) -> dict[str, Any]:
        """Diagnostico nao destrutivo; a prova com janela real fica para a fase 5."""
        try:
            if os.name != "nt" and self._adapter is None:
                raise RuntimeError("Window chrome Win32 disponivel somente no Windows")
            adapter = self._native()
            detail = adapter.diagnostic() if hasattr(adapter, "diagnostic") else {"supported": True}
            return {
                "ok": True,
                "mode": "custom" if self._attached else "native",
                "attached": self._attached,
                "dpi": self._dpi if self._attached else None,
                "state": self.get_state()["state"],
                **detail,
            }
        except Exception as exc:
            return {
                "ok": False,
                "mode": "native",
                "attached": False,
                "dpi": None,
                "state": "unavailable",
                "error": f"{type(exc).__name__}: {exc}",
            }


def _parse_rect(
    value: Any,
    name: str,
    max_width: float,
    max_height: float,
    tolerance: float = 0.01,
) -> Rect:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} deve ser um objeto")
    numbers: list[float] = []
    for key in ("x", "y", "width", "height"):
        raw = value.get(key)
        if isinstance(raw, bool) or not isinstance(raw, (int, float)) or not math.isfinite(float(raw)):
            raise ValueError(f"{name}.{key} deve ser um numero finito")
        numbers.append(float(raw))
    rect = Rect(*numbers)
    if rect.x < 0 or rect.y < 0 or rect.width <= 0 or rect.height <= 0:
        raise ValueError(f"{name} possui dimensoes ou coordenadas invalidas")
    if rect.right > max_width + tolerance or rect.bottom > max_height + tolerance:
        raise ValueError(f"{name} excede a area cliente")
    return rect


def validate_regions(
    payload: Mapping[str, Any],
    client_width: float,
    client_height: float,
    *,
    tolerance: float = 0.01,
) -> ChromeRegions:
    """Normaliza o contrato atual e alguns aliases previstos para a bridge da fase 3."""
    if not isinstance(payload, Mapping):
        raise ValueError("payload deve ser um objeto")
    if not math.isfinite(client_width) or not math.isfinite(client_height) or client_width <= 0 or client_height <= 0:
        raise ValueError("area cliente invalida")

    bar_value = payload.get("titlebar", payload.get("bar"))
    titlebar = _parse_rect(bar_value, "titlebar", client_width, client_height, tolerance)

    drag_value = payload.get("draggable", payload.get("drag", [bar_value]))
    if isinstance(drag_value, Mapping):
        drag_value = [drag_value]
    if not isinstance(drag_value, Sequence) or isinstance(drag_value, (str, bytes)):
        raise ValueError("draggable deve ser uma lista")
    if not 1 <= len(drag_value) <= MAX_DRAG_REGIONS:
        raise ValueError(f"draggable deve conter entre 1 e {MAX_DRAG_REGIONS} regioes")
    draggable = tuple(
        _parse_rect(value, f"draggable[{index}]", client_width, client_height, tolerance)
        for index, value in enumerate(drag_value)
    )

    buttons = payload.get("buttons", {})
    if buttons is None:
        buttons = {}
    if not isinstance(buttons, Mapping):
        raise ValueError("buttons deve ser um objeto")

    def optional_button(name: str) -> Rect | None:
        value = buttons.get(name, payload.get(name))
        return None if value is None else _parse_rect(
            value, name, client_width, client_height, tolerance
        )

    minimize = optional_button("minimize")
    maximize = optional_button("maximize")
    close = optional_button("close")
    controls = tuple(rect for rect in (minimize, maximize, close) if rect is not None)
    for index, control in enumerate(controls):
        if (
            control.x < titlebar.x - tolerance
            or control.y < titlebar.y - tolerance
            or control.right > titlebar.right + tolerance
            or control.bottom > titlebar.bottom + tolerance
        ):
            raise ValueError("controles devem permanecer dentro da titlebar")
        if any(control.intersects(other) for other in controls[index + 1 :]):
            raise ValueError("controles da titlebar nao podem se sobrepor")
        if any(control.intersects(region) for region in draggable):
            raise ValueError("regioes arrastaveis nao podem englobar controles")

    return ChromeRegions(
        titlebar=titlebar,
        draggable=draggable,
        minimize_button=minimize,
        maximize_button=maximize,
        close_button=close,
    )


def _webview_control(window: Any) -> Any:
    """Controle WinForms do WebView2 dentro da janela do pywebview."""
    native = getattr(window, "native", None)
    browser = getattr(native, "browser", None)
    return getattr(browser, "webview", None)


def enable_nonclient_region_support(control: Any) -> bool:
    """Faz o WebView2 honrar ``app-region: drag`` na barra de titulo.

    Sem isso o arraste depende de converter um ``pointerdown`` HTML numa mensagem
    nao-cliente, o que nao funciona: a chamada da API do pywebview nao roda na
    thread dona do HWND e a captura do mouse pertence ao processo do WebView2.
    Com a opcao ligada e o proprio navegador que classifica a regiao e entrega o
    arraste, o duplo clique e o Aero Snap ao Windows — sem intermediarios.

    ``control`` e o controle WebView2 (o ``sender`` de
    ``CoreWebView2InitializationCompleted``). Requer WebView2 SDK 1.0.2210+ e
    precisa ser chamada antes da navegacao do documento — ver
    ``WindowChromeController.arm_nonclient_regions``.
    """
    core = getattr(control, "CoreWebView2", None)
    settings = getattr(core, "Settings", None)
    if settings is None or not hasattr(settings, "IsNonClientRegionSupportEnabled"):
        return False
    settings.IsNonClientRegionSupportEnabled = True
    return bool(settings.IsNonClientRegionSupportEnabled)


def window_chrome_diagnostic() -> dict[str, Any]:
    return WindowChromeController().diagnostic()


__all__ = [
    "ChromeModeDecision",
    "ChromeRegions",
    "Rect",
    "WindowChromeController",
    "Win32WindowAdapter",
    "default_restored_rect",
    "enable_nonclient_region_support",
    "hit_test",
    "point_from_lparam",
    "resolve_titlebar_mode",
    "resolve_window_chrome_mode",
    "scale_for_dpi",
    "signed_word",
    "validate_regions",
    "window_chrome_diagnostic",
]
