"""
core/log_buffer.py — Buffer em memória dos logs de coleta.

Captura os registros dos módulos de coleta (collector, scheduler, renovação de sessão) num
ring buffer acessível pela Api, alimentando o painel de logs do desenvolvedor (botão </>) e o
download de logs. Não persiste em disco — é diagnóstico em tempo real da sessão atual.
"""

import logging
import threading
from collections import deque
from datetime import datetime

# Apenas logs destes módulos entram no buffer (diagnóstico de coleta).
_CAPTURED_PREFIXES = ("core.collector", "core.scheduler", "core.session_renew")


class _BufferHandler(logging.Handler):
    def __init__(self, maxlen: int = 3000):
        super().__init__()
        self._records = deque(maxlen=maxlen)
        self._lock = threading.Lock()

    def emit(self, record: logging.LogRecord):
        try:
            if not record.name.startswith(_CAPTURED_PREFIXES):
                return
            try:
                msg = record.getMessage()
            except Exception:
                msg = str(record.msg)
            entry = {
                "ts": datetime.fromtimestamp(record.created).strftime("%Y-%m-%d %H:%M:%S"),
                "level": record.levelname,
                "logger": record.name,
                "msg": msg,
            }
            with self._lock:
                self._records.append(entry)
        except Exception:
            pass

    def get_records(self, limit: int = 800) -> list:
        with self._lock:
            items = list(self._records)
        return items[-limit:] if limit else items

    def clear(self):
        with self._lock:
            self._records.clear()


# Instância única usada pela Api.
log_buffer = _BufferHandler()


def install():
    """Anexa o buffer ao root logger (idempotente). Os registros de coleta são filtrados em emit()."""
    log_buffer.setLevel(logging.DEBUG)
    root = logging.getLogger()
    if log_buffer not in root.handlers:
        root.addHandler(log_buffer)
