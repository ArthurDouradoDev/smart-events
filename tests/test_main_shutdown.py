import main


class _FakeTimer:
    instances = []

    def __init__(self, delay, callback):
        self.delay = delay
        self.callback = callback
        self.daemon = False
        self.started = False
        self.cancelled = False
        self.joined = False
        self.__class__.instances.append(self)

    def start(self):
        self.started = True

    def cancel(self):
        self.cancelled = True

    def join(self, timeout=None):
        self.joined = True


class _FakeScheduler:
    def __init__(self):
        self.callbacks = []
        self.stop_count = 0

    def set_update_callback(self, callback):
        self.callbacks.append(callback)

    def stop(self):
        self.stop_count += 1


class _FakeChrome:
    def __init__(self):
        self.repaint_count = 0
        self.close_count = 0

    def force_repaint(self):
        self.repaint_count += 1

    def close(self):
        self.close_count += 1


class _FakePurge:
    """Limpeza de alarmes injetada no coordenador.

    Anota o ``stop_count`` do scheduler no instante da chamada: e assim que a
    ordem "parar a coleta antes de apagar" fica provada, e nao por inspecao."""

    def __init__(self, scheduler, raises=None):
        self._scheduler = scheduler
        self._raises = raises
        self.calls = 0
        self.stop_count_ao_chamar = []

    def __call__(self):
        self.calls += 1
        self.stop_count_ao_chamar.append(self._scheduler.stop_count)
        if self._raises is not None:
            raise self._raises
        return 7


def _coordinator():
    _FakeTimer.instances = []
    scheduler = _FakeScheduler()
    chrome = _FakeChrome()
    coordinator = main._ShutdownCoordinator(
        scheduler, chrome, timer_factory=_FakeTimer
    )
    return coordinator, scheduler, chrome


def test_shutdown_cancela_repaint_e_para_scheduler_uma_vez():
    coordinator, scheduler, _chrome = _coordinator()

    assert coordinator.schedule_repaint(8.0) is True
    repaint_timer = _FakeTimer.instances[0]
    coordinator.shutdown()
    coordinator.shutdown()

    assert repaint_timer.started is True
    assert repaint_timer.daemon is True
    assert repaint_timer.cancelled is True
    assert repaint_timer.joined is True
    assert scheduler.callbacks == [None]
    assert scheduler.stop_count == 1


def test_request_close_devolve_antes_de_destruir_janela_e_nao_duplica():
    coordinator, scheduler, chrome = _coordinator()

    assert coordinator.request_close() is True
    assert coordinator.request_close() is True

    close_timer = _FakeTimer.instances[0]
    assert close_timer.delay == main.WINDOW_CLOSE_BRIDGE_DELAY
    assert close_timer.started is True
    assert close_timer.daemon is True
    assert chrome.close_count == 0
    assert scheduler.stop_count == 1

    close_timer.callback()

    assert chrome.close_count == 1


def test_repaint_nao_e_agendado_depois_do_shutdown():
    coordinator, _scheduler, _chrome = _coordinator()

    coordinator.shutdown()

    assert coordinator.schedule_repaint(8.0) is False
    assert _FakeTimer.instances == []


# ── Limpeza dos alarmes no encerramento ──────────────────────────────

def _coordinator_com_purge(raises=None):
    _FakeTimer.instances = []
    scheduler = _FakeScheduler()
    chrome = _FakeChrome()
    purge = _FakePurge(scheduler, raises=raises)
    coordinator = main._ShutdownCoordinator(
        scheduler, chrome, timer_factory=_FakeTimer, purge_alarms=purge
    )
    return coordinator, scheduler, purge


def test_shutdown_limpa_alarmes_depois_de_parar_o_scheduler():
    coordinator, scheduler, purge = _coordinator_com_purge()

    coordinator.shutdown()

    assert scheduler.stop_count == 1
    assert purge.calls == 1
    # Um ciclo de coleta em voo regravaria a tabela logo apos o DELETE: a
    # limpeza so pode acontecer com o scheduler ja parado.
    assert purge.stop_count_ao_chamar == [1]


def test_shutdown_nao_limpa_duas_vezes():
    coordinator, _scheduler, purge = _coordinator_com_purge()

    coordinator.shutdown()
    coordinator.shutdown()
    coordinator.request_close()

    assert purge.calls == 1


def test_shutdown_conclui_mesmo_se_a_limpeza_falhar():
    coordinator, scheduler, purge = _coordinator_com_purge(raises=RuntimeError("banco travado"))

    coordinator.shutdown()  # nao pode propagar: a janela precisa fechar

    assert purge.calls == 1
    assert scheduler.stop_count == 1
    # O coordenador ficou completo: nada mais e agendado depois disso.
    assert coordinator.schedule_repaint(8.0) is False
    assert coordinator.request_close() is True


def test_shutdown_sem_purge_continua_funcionando():
    """A injecao e opcional; o coordenador default nao depende do banco."""
    coordinator, scheduler, _chrome = _coordinator()

    coordinator.shutdown()

    assert scheduler.stop_count == 1
