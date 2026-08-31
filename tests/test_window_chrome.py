from __future__ import annotations

import math

import pytest

from core.window_chrome import (
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
    Rect,
    WindowChromeController,
    hit_test,
    point_from_lparam,
    resolve_window_chrome_mode,
    scale_for_dpi,
    validate_regions,
)


WINDOW = Rect(100, 200, 800, 600)
TITLEBAR = Rect(0, 0, 800, 36)


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
        self.restore_during_drag = False
        self.client_size = (800, 600)
        self.dpi = 96
        self.last_callback = None
        self.dwm_result = (False, 0)

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
        self.show_commands.append(command)

    def is_zoomed(self, hwnd):
        return self.zoomed

    def is_iconic(self, hwnd):
        return self.iconic

    def close_window(self, hwnd):
        self.closed = True

    def begin_drag(self, hwnd):
        self.drag_started = True
        if self.restore_during_drag:
            self.zoomed = False

    def call_wndproc(self, proc, hwnd, msg, wparam, lparam):
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


def test_drag_remaximizes_on_the_destination_monitor():
    controller, adapter = attached_controller()
    adapter.zoomed = True
    adapter.restore_during_drag = True

    assert controller.begin_drag() is True

    assert adapter.drag_started is True
    assert adapter.show_commands[-1] == SW_MAXIMIZE


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
