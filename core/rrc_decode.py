"""Decodificação local de RRC_MEAS_RPRT (LTE) a partir do ``messageBody`` do FARS.

O iManager expõe o PDU bruto em ``messageBody`` de cada linha devolvida por
``traceresult/query/filter-by-cols``. Decodificar aqui evita uma chamada
``msg-explain-info`` por mensagem — cujo contrato de ``rowNo`` nunca foi
capturado no Network — e mantém o custo do ciclo em uma única requisição.

Referência: 3GPP TS 36.331, ``UL-DCCH-Message`` / ``MeasurementReport``,
codificação ASN.1 UNALIGNED PER.

O ``messageBody`` do FARS carrega um byte de cabeçalho proprietário antes do
PDU. Nas 386 mensagens da captura de referência esse prefixo tem sempre 1 byte,
mas o decodificador testa os deslocamentos conhecidos e aceita apenas o que
satisfizer toda a estrutura, então um prefixo diferente não produz valor errado.
"""

import logging

logger = logging.getLogger(__name__)

# Deslocamentos de cabeçalho tentados, em ordem de probabilidade.
_HEADER_OFFSETS = (1, 0, 2)

# Limites dos inteiros restritos de TS 36.331 usados na validação estrutural.
_RSRP_MAX = 97   # RSRP-Range  ::= INTEGER (0..97)
_RSRQ_MAX = 34   # RSRQ-Range  ::= INTEGER (0..34)
_MEAS_ID_MAX = 32  # MeasId     ::= INTEGER (1..maxMeasId)


class _BitReader:
    """Leitor de bits big-endian sobre ``bytes``, suficiente para UPER."""

    def __init__(self, data: bytes) -> None:
        self._data = data
        self._pos = 0

    def read(self, count: int) -> int:
        if self._pos + count > len(self._data) * 8:
            raise EOFError("PDU terminou antes do esperado")
        value = 0
        for _ in range(count):
            byte = self._data[self._pos >> 3]
            value = (value << 1) | ((byte >> (7 - (self._pos & 7))) & 1)
            self._pos += 1
        return value


def parse_message_body(message_body: str) -> bytes:
    """Converte ``"02 08 36 AD"`` no ``bytes`` correspondente."""
    return bytes(int(token, 16) for token in str(message_body).split())


def _decode_at(data: bytes) -> dict | None:
    """Tenta ler um MeasurementReport a partir do primeiro byte de ``data``."""
    reader = _BitReader(data)

    # UL-DCCH-MessageType ::= CHOICE { c1 CHOICE {...16 alternativas...}, ... }
    if reader.read(1) != 0:          # c1, não messageClassExtension
        return None
    if reader.read(4) != 1:          # índice 1 = measurementReport
        return None
    # MeasurementReport ::= SEQUENCE { criticalExtensions CHOICE { c1, future } }
    if reader.read(1) != 0:          # c1
        return None
    if reader.read(3) != 0:          # índice 0 = measurementReport-r8
        return None
    reader.read(1)                   # nonCriticalExtension: presente/ausente

    # MeasResults ::= SEQUENCE { measId, measResultPCell, measResultNeighCells OPTIONAL, ... }
    reader.read(1)                   # marcador de extensão
    reader.read(1)                   # bitmap opcional: measResultNeighCells

    meas_id = reader.read(5) + 1     # MeasId (1..32) → 5 bits
    rsrp_index = reader.read(7)      # RSRP-Range (0..97) → 7 bits
    rsrq_index = reader.read(6)      # RSRQ-Range (0..34) → 6 bits

    # Valores acima do limite provam que o alinhamento testado está errado.
    if rsrp_index > _RSRP_MAX or rsrq_index > _RSRQ_MAX or meas_id > _MEAS_ID_MAX:
        return None

    return {
        "meas_id": meas_id,
        "rsrp_index": rsrp_index,
        "rsrq_index": rsrq_index,
        # TS 36.133: índice 0 = < -140 dBm, cada passo = 1 dB.
        "rsrp": rsrp_index - 140.0,
        # TS 36.133: índice 0 = < -19.5 dB, cada passo = 0,5 dB.
        "rsrq": (rsrq_index / 2.0) - 19.5,
    }


def decode_meas_report(message_body: str) -> dict | None:
    """Decodifica um RRC_MEAS_RPRT LTE e devolve ``meas_id``, ``rsrp`` e ``rsrq``.

    Devolve ``None`` quando o corpo não é um MeasurementReport reconhecível —
    o chamador deve tratar isso como mensagem não decodificada, nunca como zero.
    """
    if not message_body:
        return None
    try:
        data = parse_message_body(message_body)
    except ValueError:
        return None
    for offset in _HEADER_OFFSETS:
        if offset >= len(data):
            continue
        try:
            decoded = _decode_at(data[offset:])
        except EOFError:
            continue
        if decoded is not None:
            decoded["header_offset"] = offset
            return decoded
    return None
