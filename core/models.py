from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Cell:
    id: str
    azimuth: float
    beamwidth: float = 120.0
    status: str = "unknown"  # healthy | warning | critical | offline | unknown
    tech: Optional[str] = None
    frequency: Optional[str] = None


@dataclass
class Site:
    id: str
    name: str
    lat: float
    lng: float
    cells: List[Cell] = field(default_factory=list)
    is_event_site: bool = True
    status: str = "unknown"


@dataclass
class VIP:
    name: str
    task_id: Optional[int] = None  # id da task de Signaling Trace dedicada no iManager
    serving_cell: Optional[str] = None
    rsrp: Optional[float] = None
    rsrq: Optional[float] = None
    in_event: bool = False
    status: str = "unknown"  # ok | warning | critical | unknown
    oss: Optional[str] = None


@dataclass
class Thresholds:
    rsrp_warning: float = -100.0
    rsrp_critical: float = -110.0
    rsrq_warning: float = -12.0
    rsrq_critical: float = -15.0
    utilization_warning: float = 80.0
    utilization_critical: float = 95.0
    availability_critical: float = 95.0


@dataclass
class OssConfig:
    base_url: str = ""
    region: str = ""
    import_folder: str = ""  # pasta onde exports CSV chegam (fase 1)
    # Tipos de alarme (nomes do catálogo) coletados para este evento. Default =
    # VSWR + Cell Unavailable. Ver core.collector._DEFAULT_ALARM_NAMES.
    alarm_filter: List[str] = field(default_factory=lambda: [
        "RF Unit VSWR Threshold Crossed", "Cell Unavailable"
    ])


@dataclass
class EventConfig:
    id: str
    name: str
    status: str  # SCHEDULED | ACTIVE | ENDED | ARCHIVED
    start_time: str
    end_time: str
    polygon: list
    sites: List[Site]
    vips: List[VIP]
    thresholds: Thresholds
    oss: OssConfig = field(default_factory=OssConfig)


@dataclass
class KpiMeasurement:
    site_id: str
    cell_id: str
    event_id: str
    timestamp: str
    metric: str
    value: float


@dataclass
class VipMeasurement:
    vip_name: str
    event_id: str
    timestamp: str
    serving_cell: str
    rsrp: float
    rsrq: float
    in_event: bool


@dataclass
class Alert:
    id: Optional[int]
    event_id: str
    level: str       # GLOBAL | EVENT | INSTANCE
    severity: str    # WARNING | CRITICAL
    site_id: str
    cell_id: str
    message: str
    timestamp: str
    acknowledged: bool = False
