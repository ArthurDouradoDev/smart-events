from __future__ import annotations

import ctypes
import math

import pytest

from core.window_chrome import (
    NATIVE_RECT,
    WM_NCCALCSIZE,
    HTBOTTOM,
    HTBOTTOMLEFT,
    HTBOTTOMRIGHT,
    HTCAPTION,
    HTCLIENT,
    HTLEFT,
    HTMAXBUTTON,
    HTRIGHT,
    HTTOP,
    HTTOPLEFT,
    HTTOPRIGHT,
    REQUIRED_WINDOW_STYLES,
    SW_MAXIMIZE,
    SW_MINIMIZE,
    SW_RESTORE,
    WM_NCDESTROY,
    WM_NCHITTEST,
    WM_GETMINMAXINFO,
    Rect,
    Win32WindowAdapter,
    WindowChromeController,
    default_restored_rect,
    enable_nonclient_region_support,
    hit_test,
    point_from_lparam,
    resolve_window_chrome_mode,
    scale_for_dpi,
    validate_regions,
)


WINDOW = Rect(100, 200, 800, 600)
TITLEBAR = Rect(0, 0, 800, 36)
MONITOR = Rect(0, 0, 1920, 1200)
WORK_AREA = Rect(0, 0, 1920, 1128)


@pytest.mark.parametrize(
    ("point", "expected"),
    [
        ((101, 500), HTLEFT),
        ((899, 500), HTRIGHT),
        ((500, 201), HTTOP),
        ((500, 799), HTBOTTOM),
        ((101, 201), HTTOPLEFT),
        ((899, 201), HTTOPRIGHT),
        ((101, 799), HTBOTTOMLEFT),
        ((899, 799), HTBOTTOMRIGHT),
    ],
)
def test_hit_test_borders_and_corners(point, expected):
    assert hit_test(*point, WINDOW, titlebar=TITLEBAR) == expected


def test_corners_take_priority_over_borders():
    assert hit_test(102, 202, WINDOW, resize_border=12, titlebar=TITLEBAR) == HTTOPLEFT


def test_titlebar_client_and_maximize_button():
    maximize = Rect(708, 0, 46, 36)
    assert hit_test(500, 220, WINDOW, titlebar=TITLEBAR) == HTCAPTION
    assert hit_test(500, 300, WINDOW, titlebar=TITLEBAR) == HTCLIENT
    assert hit_test(100 + 720, 200 + 20, WINDOW, titlebar=TITLEBAR, maximize_button=maximize) == HTMAXBUTTON


def test_explicit_drag_region_does_not_make_buttons_draggable():
    drag = Rect(0, 0, 662, 36)
    assert hit_test(500, 220, WINDOW, titlebar=TITLEBAR, draggable=(drag,)) == HTCAPTION
    assert hit_test(800, 220, WINDOW, titlebar=TITLEBAR, draggable=(drag,)) == HTCLIENT


def test_dom_regions_are_offset_from_the_outer_window_frame():
    maximize = Rect(708, 0, 46, 36)
    # A area cliente comeca 8 px dentro do frame externo.
    assert hit_test(
        100 + 8 + 720,
        200 + 8 + 20,
        WINDOW,
        titlebar=TITLEBAR,
        maximize_button=maximize,
        client_offset=(8, 8),
    ) == HTMAXBUTTON


def test_outside_window_is_never_a_border_or_control():
    maximize = Rect(708, 0, 46, 36)
    for point in ((99, 220), (901, 220), (500, 199), (500, 801)):
        assert hit_test(*point, WINDOW, titlebar=TITLEBAR, maximize_button=maximize) == HTCLIENT


def test_negative_monitor_coordinates_and_lparam_conversion():
    window = Rect(-1500, 100, 1200, 700)
    assert hit_test(-1499, 450, window, titlebar=Rect(0, 0, 1200, 36)) == HTLEFT
    packed = ((-40 & 0xFFFF) << 16) | (-1300 & 0xFFFF)
    assert point_from_lparam(packed) == (-1300, -40)


def test_native_drag_message_carries_real_negative_screen_coordinates():
    class FakeUser32:
        def __init__(self):
            self.released = False
            self.message = None

        def ReleaseCapture(self):
            self.released = True
            return True

        def SendMessageW(self, hwnd, msg, wparam, lparam):
            self.message = (hwnd, msg, wparam, lparam)
            return 0

    adapter = Win32WindowAdapter.__new__(Win32WindowAdapter)
    adapter.user32 = FakeUser32()
    adapter.get_cursor_pos = lambda: (-1300, 24)

    adapter.begin_drag(101)

    assert adapter.user32.released is True
    assert adapter.user32.message[:3] == (101, 0x00A1, HTCAPTION)
    assert point_from_lparam(adapter.user32.message[3]) == (-1300, 24)


def test_default_restored_rect_centers_eighty_percent_of_the_work_area():
    work = Rect(0, 0, 1920, 1128)

    rect = default_restored_rect(work, fraction=0.8)

    assert (rect.width, rect.height) == (1536, 902)
    assert (rect.x, rect.y) == (192, 113)
    # Centralizado: as folgas laterais e verticais sao simetricas.
    assert rect.x - work.x == pytest.approx(work.right - rect.right, abs=1)
    assert rect.y - work.y == pytest.approx(work.bottom - rect.bottom, abs=1)


def test_default_restored_rect_respects_minimum_and_never_exceeds_the_monitor():
    # Monitor pequeno: 80% ficaria abaixo do minimo do dashboard.
    rect = default_restored_rect(
        Rect(0, 0, 1366, 768), fraction=0.8, min_width=1024, min_height=600
    )
    assert (rect.width, rect.height) == (1093, 614)

    # Monitor menor que o proprio minimo: a area util tem prioridade, senao a
    # janela nasceria com parte fora da tela.
    rect = default_restored_rect(
        Rect(0, 0, 1000, 560), fraction=0.8, min_width=1024, min_height=600
    )
    assert (rect.x, rect.y, rect.width, rect.height) == (0, 0, 1000, 560)


def test_default_restored_rect_uses_the_origin_of_a_monitor_at_the_left():
    rect = default_restored_rect(Rect(-1920, -80, 1920, 1040), fraction=0.5)

    assert (rect.x, rect.y, rect.width, rect.height) == (-1440, 180, 960, 520)


@pytest.mark.parametrize("fraction", [0, -0.5, 1.5, math.nan])
def test_default_restored_rect_rejects_invalid_fractions(fraction):
    with pytest.raises(ValueError):
        default_restored_rect(Rect(0, 0, 1920, 1128), fraction=fraction)


@pytest.mark.parametrize(("dpi", "expected"), [(96, 8), (120, 10), (144, 12), (192, 16)])
def test_dpi_conversion(dpi, expected):
    assert scale_for_dpi(8, dpi) == expected


@pytest.mark.parametrize("value", [math.nan, math.inf, -1])
def test_dpi_conversion_rejects_invalid_values(value):
    with pytest.raises(ValueError):
        scale_for_dpi(value, 96)


def test_mode_defaults_to_native_and_native_wins_conflict():
    assert resolve_window_chrome_mode([], is_windows=True).mode == "native"
    custom = resolve_window_chrome_mode(["--custom-titlebar"], is_windows=True)
    assert custom.mode == "custom"
    conflict = resolve_window_chrome_mode(
        ["--custom-titlebar", "--native-titlebar"], is_windows=True
    )
    assert conflict.mode == "native"
    assert conflict.conflict is True


def test_custom_mode_is_rejected_outside_windows():
    decision = resolve_window_chrome_mode(["--custom-titlebar"], is_windows=False)
    assert decision.mode == "native"
    assert "fora do Windows" in decision.reason


class FakeHandle:
    def ToInt64(self):
        return 101


class FakeNative:
    Handle = FakeHandle()


class FakeWindow:
    native = FakeNative()


class FakeAdapter:
    supported = True

    def __init__(self, fail_at: str | None = None):
        self.fail_at = fail_at
        self.style = 0x10
        self.proc = 777
        self.set_proc_calls = []
        self.set_style_calls = []
        self.frame_changes = 0
        self.restored_native = False
        self.show_commands = []
        self.zoomed = False
        self.iconic = False
        self.closed = False
        self.drag_started = False
        self.action_order = []
        self.client_size = (800, 600)
        self.dpi = 96
        self.last_callback = None
        self.dwm_result = (False, 0)
        self.window_rects = []
        self.restored_placement = None
        self.normal_position = None
        self.forwarded = []
        self.minmax_applied = []
        self.nccalcsize_calls = []
        self.borders = (11, 11)

    def _fail(self, name):
        if self.fail_at == name:
            raise OSError(f"falha simulada em {name}")

    def get_hwnd(self, window):
        self._fail("get_hwnd")
        return window.native.Handle.ToInt64()

    def get_style(self, hwnd):
        self._fail("get_style")
        return self.style

    def set_style(self, hwnd, style):
        self._fail("set_style")
        self.set_style_calls.append(style)
        self.style = style

    def get_wndproc(self, hwnd):
        self._fail("get_wndproc")
        return self.proc

    def create_wndproc(self, callback):
        self._fail("create_wndproc")
        self.last_callback = callback
        return callback

    def set_wndproc(self, hwnd, proc):
        self._fail("set_wndproc")
        self.set_proc_calls.append(proc)
        self.proc = proc

    def frame_changed(self, hwnd):
        self._fail("frame_changed")
        self.frame_changes += 1

    def restore_native_frame(self, hwnd, previous_style):
        self.restored_native = True
        self.style = previous_style | REQUIRED_WINDOW_STYLES

    def get_dpi(self, hwnd):
        return self.dpi

    def get_client_size(self, hwnd):
        return self.client_size

    def show_window(self, hwnd, command):
        self.action_order.append(("show", command))
        self.show_commands.append(command)

    def is_zoomed(self, hwnd):
        return self.zoomed

    def is_iconic(self, hwnd):
        return self.iconic

    def close_window(self, hwnd):
        self.closed = True

    def begin_drag(self, hwnd):
        self.action_order.append(("drag", None))
        self.drag_started = True

    def get_monitor_rects(self, hwnd):
        return (MONITOR, WORK_AREA)

    def get_placement(self, hwnd):
        self.action_order.append(("get_placement", None))
        return f"placement@{self.zoomed}"

    def set_placement(self, hwnd, placement):
        self.action_order.append(("set_placement", placement))
        self.restored_placement = placement

    def set_window_rect(self, hwnd, rect):
        self.action_order.append(("set_rect", rect))
        self.window_rects.append(rect)

    def set_normal_position(self, hwnd, rect):
        self.normal_position = rect

    def apply_minmax_info(self, hwnd, lparam, min_track=None):
        self.minmax_applied.append((hwnd, lparam, min_track))

    def frame_borders(self, hwnd):
        return self.borders

    def apply_nccalcsize_borders(self, hwnd, lparam):
        self.nccalcsize_calls.append((hwnd, lparam))

    def call_wndproc(self, proc, hwnd, msg, wparam, lparam):
        self.forwarded.append((msg, wparam, lparam))
        return 1234

    def dwm_def_window_proc(self, hwnd, msg, wparam, lparam):
        return self.dwm_result

    def get_window_rect(self, hwnd):
        return WINDOW

    def get_client_offset(self, hwnd):
        return (0, 0)

    def diagnostic(self):
        return {"supported": True, "pointer_bits": 64}


def attached_controller(adapter=None):
    adapter = adapter or FakeAdapter()
    controller = WindowChromeController(adapter)
    assert controller.attach(FakeWindow()) is True
    return controller, adapter


def test_attach_is_idempotent_and_retains_callback():
    adapter = FakeAdapter()
    controller = WindowChromeController(adapter)
    window = FakeWindow()
    assert controller.attach(window) is True
    callback = controller.callback
    assert callback is not None
    assert controller.attach(window) is True
    assert controller.callback is callback
    assert len(adapter.set_proc_calls) == 1
    assert adapter.style & REQUIRED_WINDOW_STYLES == REQUIRED_WINDOW_STYLES


def test_detach_is_idempotent_and_restores_proc_and_style():
    adapter = FakeAdapter()
    original_style = adapter.style
    original_proc = adapter.proc
    controller, adapter = attached_controller(adapter)
    assert controller.detach() is True
    assert controller.detach() is True
    assert adapter.set_proc_calls[-1] == original_proc
    assert adapter.set_style_calls[-1] == original_style
    assert controller.callback is None


def test_nc_destroy_restores_original_proc_before_forwarding():
    adapter = FakeAdapter()
    original_proc = adapter.proc
    controller, adapter = attached_controller(adapter)
    result = controller._wnd_proc(101, WM_NCDESTROY, 0, 0)
    assert result == 1234
    assert adapter.set_proc_calls[-1] == original_proc
    assert controller.attached is False


def test_dwm_client_result_does_not_mask_custom_drag_region():
    controller, adapter = attached_controller()
    adapter.dwm_result = (True, HTCLIENT)
    packed = ((220 & 0xFFFF) << 16) | (500 & 0xFFFF)
    assert controller._wnd_proc(101, WM_NCHITTEST, 0, packed) == HTCAPTION


def test_attach_failure_falls_back_to_native_frame():
    adapter = FakeAdapter(fail_at="frame_changed")
    controller = WindowChromeController(adapter)
    assert controller.attach(FakeWindow()) is False
    assert controller.attached is False
    assert adapter.restored_native is True
    state = controller.get_state()
    assert state["mode"] == "native"
    assert state["fallback"] is True
    assert "falha simulada" in state["error"]


def test_attach_rejects_unsupported_adapter():
    adapter = FakeAdapter()
    adapter.supported = False
    controller = WindowChromeController(adapter)
    assert controller.attach(FakeWindow()) is False
    assert "indisponivel" in controller.last_error


def test_window_actions_and_state_transitions():
    controller, adapter = attached_controller()
    assert controller.get_state()["state"] == "normal"
    assert controller.minimize() is True
    assert adapter.show_commands[-1] == SW_MINIMIZE
    assert controller.begin_drag() is True
    assert adapter.drag_started is True

    adapter.zoomed = False
    assert controller.toggle_maximize() is True
    assert adapter.show_commands[-1] == SW_MAXIMIZE
    adapter.zoomed = True
    assert controller.get_state()["state"] == "maximized"
    assert controller.toggle_maximize() is True
    assert adapter.show_commands[-1] == SW_RESTORE

    adapter.iconic = True
    assert controller.get_state()["state"] == "minimized"
    assert controller.close() is True
    assert adapter.closed is True


def test_drag_delegates_the_maximized_case_to_the_native_move_loop():
    controller, adapter = attached_controller()
    adapter.zoomed = True

    assert controller.begin_drag() is True

    # Nada de restaurar/reposicionar por conta propria: o move loop do Windows
    # ja faz o drag-to-restore e o Aero Snap no monitor de destino.
    assert adapter.action_order == [("drag", None)]
    assert adapter.show_commands == []
    assert adapter.window_rects == []


def test_fullscreen_toggle_covers_the_monitor_and_restores_the_previous_placement():
    controller, adapter = attached_controller()
    adapter.zoomed = True

    assert controller.toggle_fullscreen() is True
    assert adapter.window_rects == [MONITOR]
    # A janela precisa sair de maximizada antes do SetWindowPos, senao o
    # Windows ignora o novo retangulo.
    assert adapter.show_commands == [SW_RESTORE]
    assert controller.get_state()["state"] == "fullscreen"

    assert controller.toggle_fullscreen() is True
    assert adapter.restored_placement == "placement@True"
    assert controller.get_state()["state"] == "maximized"


def test_maximize_button_leaves_fullscreen_before_toggling():
    controller, adapter = attached_controller()

    assert controller.toggle_fullscreen() is True
    assert controller.toggle_maximize() is True

    assert adapter.restored_placement == "placement@False"
    assert controller.get_state()["state"] == "normal"


def test_detach_forgets_the_fullscreen_state():
    controller, adapter = attached_controller()
    assert controller.toggle_fullscreen() is True

    assert controller.detach() is True
    assert controller.get_state()["state"] == "normal"


def test_default_geometry_reserves_the_frame_on_top_of_the_content_minimum():
    controller, adapter = attached_controller()
    adapter.dpi = 144
    adapter.zoomed = True

    assert controller.apply_default_geometry(min_size=(1024, 600)) is True

    # 80% de 1920x1128 seria 1536x902, mas o minimo do *conteudo* (1024x600 a
    # 144 DPI = 1536x900) mais a moldura (11 px por lado) exige 1558x922 — senao
    # o viewport do dashboard ficaria abaixo dos 1024 px logicos.
    assert adapter.normal_position == Rect(181, 103, 1558, 922)
    # Definir o retangulo restaurado nao pode tirar a janela de maximizada.
    assert adapter.show_commands == []


def test_nccalcsize_reserves_a_resize_border_only_when_the_window_is_restored():
    controller, adapter = attached_controller()

    # Restaurada: o WebView2 precisa ceder a faixa que recebe o WM_NCHITTEST
    # das bordas, senao nao ha por onde redimensionar.
    assert controller._wnd_proc(101, WM_NCCALCSIZE, 1, 4096) == 0
    assert adapter.nccalcsize_calls == [(101, 4096)]

    # Maximizada nao ha o que redimensionar, e a faixa viraria uma borda visivel.
    adapter.zoomed = True
    assert controller._wnd_proc(101, WM_NCCALCSIZE, 1, 4096) == 0
    assert adapter.nccalcsize_calls == [(101, 4096)]

    # Tela cheia segue a mesma regra da maximizada.
    adapter.zoomed = False
    assert controller.toggle_fullscreen() is True
    assert controller._wnd_proc(101, WM_NCCALCSIZE, 1, 4096) == 0
    assert adapter.nccalcsize_calls == [(101, 4096)]


def test_nccalcsize_never_collapses_a_degenerate_rectangle():
    adapter = Win32WindowAdapter.__new__(Win32WindowAdapter)
    adapter.frame_borders = lambda hwnd: (12, 12)
    rect = NATIVE_RECT(left=0, top=0, right=1920, bottom=1128)
    adapter.apply_nccalcsize_borders(101, ctypes.addressof(rect))
    assert (rect.left, rect.top, rect.right, rect.bottom) == (12, 12, 1908, 1116)

    # Retangulo degenerado (o Windows propoe isso ao minimizar): manter como esta.
    tiny = NATIVE_RECT(left=0, top=0, right=10, bottom=1128)
    adapter.apply_nccalcsize_borders(101, ctypes.addressof(tiny))
    assert (tiny.left, tiny.right) == (0, 10)
    assert (tiny.top, tiny.bottom) == (12, 1116)


def test_nonclient_region_support_is_enabled_and_reported_in_the_state():
    class Settings:
        IsNonClientRegionSupportEnabled = False

    settings = Settings()

    class Window:
        native = type("Native", (), {"browser": type("B", (), {"webview": type("W", (), {
            "CoreWebView2": type("C", (), {"Settings": settings})()
        })()})()})()

    window = Window()
    assert enable_nonclient_region_support(window) is True
    assert settings.IsNonClientRegionSupportEnabled is True

    controller, _ = attached_controller()
    assert controller.get_state()["nonclient"] is False
    controller._window = window
    assert controller.enable_nonclient_regions() is True
    assert controller.get_state()["nonclient"] is True
    # Idempotente: uma segunda chamada nao reprocessa nada.
    assert controller.enable_nonclient_regions() is True


def test_nonclient_region_support_is_absent_before_corewebview2_exists():
    class Window:
        native = type("Native", (), {"browser": None})()

    assert enable_nonclient_region_support(Window()) is False

    controller, _ = attached_controller()
    controller._window = Window()
    assert controller.enable_nonclient_regions() is False
    assert controller.get_state()["nonclient"] is False


def test_minmax_info_is_forwarded_and_raises_the_minimum_by_the_frame():
    controller, adapter = attached_controller()
    adapter.dpi = 144
    controller.apply_default_geometry(min_size=(1024, 600))

    assert controller._wnd_proc(101, WM_GETMINMAXINFO, 0, 4096) == 1234

    # O WinForms precisa ver a mensagem para aplicar ``min_size``; so depois
    # limitamos o tamanho maximizado e subimos o minimo pela moldura.
    assert adapter.forwarded == [(WM_GETMINMAXINFO, 0, 4096)]
    assert adapter.minmax_applied == [(101, 4096, (1558, 922))]


def test_minmax_info_leaves_the_minimum_alone_before_any_geometry_is_applied():
    controller, adapter = attached_controller()

    assert controller._wnd_proc(101, WM_GETMINMAXINFO, 0, 4096) == 1234

    assert adapter.minmax_applied == [(101, 4096, None)]


def valid_payload():
    return {
        "titlebar": {"x": 0, "y": 0, "width": 800, "height": 36},
        "draggable": [{"x": 0, "y": 0, "width": 708, "height": 36}],
        "buttons": {
            "minimize": {"x": 708, "y": 0, "width": 46, "height": 36},
            "close": {"x": 754, "y": 0, "width": 46, "height": 36},
        },
    }


def test_regions_are_validated_and_applied_atomically():
    controller, _ = attached_controller()
    assert controller.set_regions(valid_payload()) is True
    assert controller.last_error is None


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload["titlebar"].update(width=801),
        lambda payload: payload["titlebar"].update(x=-1),
        lambda payload: payload["titlebar"].update(width=math.nan),
        lambda payload: payload["buttons"]["close"].update(width=100),
    ],
)
def test_regions_reject_invalid_numbers_and_oversized_rectangles(mutate):
    controller, _ = attached_controller()
    payload = valid_payload()
    mutate(payload)
    assert controller.set_regions(payload) is False
    assert controller.last_error is not None


def test_validate_regions_limits_drag_region_count():
    payload = valid_payload()
    payload["draggable"] = [payload["draggable"][0]] * 17
    with pytest.raises(ValueError, match="entre 1 e 16"):
        validate_regions(payload, 800, 600)


def test_validate_regions_rejects_drag_over_a_control():
    payload = valid_payload()
    payload["draggable"][0]["width"] = 720
    with pytest.raises(ValueError, match="arrastaveis"):
        validate_regions(payload, 800, 600)
