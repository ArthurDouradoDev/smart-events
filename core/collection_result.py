"""Contrato explícito entre coletores, agendador e interface.

Uma lista vazia não informa se não havia dados, se a cobertura foi parcial ou se
uma chamada falhou.  ``CollectionResult`` mantém essas situações distintas até
o status operacional, sem transportar exceções esperadas como sucesso.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


CollectionState = Literal["data", "empty", "partial", "error", "auth_required"]


@dataclass(frozen=True)
class CollectionDiagnostic:
    """Um diagnóstico curto, seguro para exibição e útil para logs."""

    stage: str
    message: str
    code: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "message": self.message,
            "code": self.code,
            "details": dict(self.details),
        }


@dataclass
class CollectionResult:
    """Resultado de um ciclo de coleta, antes da persistência.

    ``inserted`` e ``duplicate`` são completados pelo agendador depois da
    persistência.  Dessa forma cursores poderão ser gravados somente após o
    lote correspondente, nas fases que os introduzem.
    """

    state: CollectionState
    measurements: list[dict[str, Any]] = field(default_factory=list)
    cursors: dict[str, Any] = field(default_factory=dict)
    received: int = 0
    calculated: int = 0
    invalid: int = 0
    # Fórmula que não se aplica ao minuto (contador fora da task, nenhuma
    # tentativa no período) não é defeito e não torna o ciclo parcial.
    not_applicable: int = 0
    duplicate: int = 0
    inserted: int = 0
    coverage: dict[str, Any] = field(default_factory=dict)
    diagnostics: list[CollectionDiagnostic] = field(default_factory=list)
    cause: str | None = None
    latest_data_at: str | None = None

    def __post_init__(self) -> None:
        if self.state not in {"data", "empty", "partial", "error", "auth_required"}:
            raise ValueError(f"Estado de coleta inválido: {self.state}")
        if (self.received < 0 or self.calculated < 0 or self.invalid < 0
                or self.not_applicable < 0):
            raise ValueError("Contadores de coleta não podem ser negativos")
        if self.state == "data" and not self.measurements:
            raise ValueError("Resultado 'data' exige ao menos uma medição")
        if self.state in {"error", "auth_required"} and not self.cause:
            self.cause = "Falha não classificada durante a coleta."
        if not self.calculated:
            self.calculated = len(self.measurements)
        if not self.received:
            self.received = self.calculated + self.invalid

    @classmethod
    def data(cls, measurements: list[dict[str, Any]], **kwargs: Any) -> "CollectionResult":
        return cls("data", measurements=measurements, **kwargs)

    @classmethod
    def empty(cls, cause: str | None = None, **kwargs: Any) -> "CollectionResult":
        return cls("empty", cause=cause or "Nenhum dado novo retornado.", **kwargs)

    @classmethod
    def partial(cls, measurements: list[dict[str, Any]] | None = None, *, cause: str,
                **kwargs: Any) -> "CollectionResult":
        return cls("partial", measurements=measurements or [], cause=cause, **kwargs)

    @classmethod
    def error(cls, cause: str, *, stage: str = "request", code: str | None = None,
              **kwargs: Any) -> "CollectionResult":
        diagnostics = kwargs.pop("diagnostics", [])
        diagnostics.append(CollectionDiagnostic(stage=stage, message=cause, code=code))
        return cls("error", cause=cause, diagnostics=diagnostics, **kwargs)

    @classmethod
    def auth_required(cls, cause: str, **kwargs: Any) -> "CollectionResult":
        diagnostics = kwargs.pop("diagnostics", [])
        diagnostics.append(CollectionDiagnostic(stage="authentication", message=cause, code="auth_required"))
        return cls("auth_required", cause=cause, diagnostics=diagnostics, **kwargs)

    def as_status_fields(self) -> dict[str, Any]:
        return {
            "received": self.received,
            "calculated": self.calculated,
            "invalid": self.invalid,
            "not_applicable": self.not_applicable,
            "duplicate": self.duplicate,
            "inserted": self.inserted,
            "coverage": dict(self.coverage),
            "diagnostics": [item.as_dict() for item in self.diagnostics],
            "cause": self.cause,
            "latest_data_at": self.latest_data_at,
        }
