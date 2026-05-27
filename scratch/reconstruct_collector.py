import json
import re
from pathlib import Path

TRANSCRIPT_PATH = Path(r"C:\Users\a50057663\\.gemini\antigravity-ide\brain\7992c32b-3b17-42cc-aac2-b69f8f912bbe\.system_generated\logs\transcript.jsonl")
COLLECTOR_FILE = Path(r"c:\Users\a50057663\Desktop\Automações\SmartEvents\core\collector.py")

def clean_lines(text):
    lines = text.split("\n")
    cleaned = []
    for line in lines:
        match = re.match(r"^\s*(\d+):\s?(.*)$", line)
        if match:
            cleaned.append(match.group(2))
    return "\n".join(cleaned)

def reconstruct():
    print("=== Reconstructing core/collector.py ===")
    
    if not TRANSCRIPT_PATH.exists():
        print("Transcript file not found!")
        return

    parts = {}
    with open(TRANSCRIPT_PATH, "r", encoding="utf-8") as f:
        for line in f:
            step = json.loads(line)
            idx = step.get("step_index")
            if idx in (282, 284, 286):
                parts[idx] = clean_lines(step.get("content", ""))

    if len(parts) < 3:
        print("Error: Could not retrieve all collector.py file parts from logs!")
        print(f"Parts found: {list(parts.keys())}")
        return

    # Stitch the parts together
    # Part 282 is lines 1-250
    # Part 284 is lines 251-500
    # Part 286 is lines 501-705
    full_code = parts[282] + "\n" + parts[284] + "\n" + parts[286]
    
    print(f"Stitched together code. Total length: {len(full_code)} characters.")

    # Apply our 4 improvements:
    
    # 1. 5G NR counter candidates in HttpCollector.KPI_COLUMN_MAP
    old_kpi_map = """    KPI_COLUMN_MAP = {
        "utilization_ul": ["{BRDC} Traffic Volume UL LTE", "UL Traffic Volume", "Traffic Volume UL LTE"],
        "utilization_dl": ["{BRDC} Traffic Volume DL LTE", "DL Traffic Volume", "Traffic Volume DL LTE"],
        "throughput_dl":  ["{BRDC} DL User Throughput",    "DL User Throughput"],
        "throughput_ul":  ["{BRDC} UL User Throughput",    "UL User Throughput"],
    }"""
    
    new_kpi_map = """    KPI_COLUMN_MAP = {
        "utilization_ul": ["{BRDC} Traffic Volume UL LTE", "UL Traffic Volume", "Traffic Volume UL LTE", "{BRDC} NR UL Traffic Volume", "NR UL Traffic Volume"],
        "utilization_dl": ["{BRDC} Traffic Volume DL LTE", "DL Traffic Volume", "Traffic Volume DL LTE", "{BRDC} NR DL Traffic Volume", "NR DL Traffic Volume"],
        "throughput_dl":  ["{BRDC} DL User Throughput",    "DL User Throughput",    "{BRDC} NR DL User Throughput",    "NR DL User Throughput"],
        "throughput_ul":  ["{BRDC} UL User Throughput",    "UL User Throughput",    "{BRDC} NR UL User Throughput",    "NR UL User Throughput"],
    }"""
    
    if old_kpi_map in full_code:
        full_code = full_code.replace(old_kpi_map, new_kpi_map)
        print("-> Applied 5G NR KPI candidates.")
    else:
        # Fallback search if spacing is slightly different
        print("Warning: Could not find old_kpi_map text exactly.")

    # 2. collect_kpis to allow empty obj_nos
    old_collect_kpis = """    def collect_kpis(self) -> List[dict]:
        integration = self.event.get("integration", {})
        sess_data = self._load_session_data()
        task_id = sess_data.get("monitoring", {}).get("task_id") or integration.get("pm_task_id") or 374
        
        obj_nos = sess_data.get("monitoring", {}).get("obj_nos")
        if not obj_nos:
            obj_nos = list(self._obj_to_cell.keys())

        if not obj_nos:
            logger.warning("Nenhum obj_no configurado para busca de KPIs.")
            return []

        now_ms = int(time.time() * 1000)
        payload = [{
            "taskId": task_id,
            "preExecTime": now_ms,
            "objNoExecTimes": [{"preExecTime": now_ms, "objNo": obj_no} for obj_no in obj_nos]
        }]"""
        
    new_collect_kpis = """    def collect_kpis(self) -> List[dict]:
        integration = self.event.get("integration", {})
        sess_data = self._load_session_data()
        task_id = sess_data.get("monitoring", {}).get("task_id") or integration.get("pm_task_id") or 374
        
        obj_nos = sess_data.get("monitoring", {}).get("obj_nos")
        if not obj_nos:
            obj_nos = list(self._obj_to_cell.keys())

        now_ms = int(time.time() * 1000)
        payload = [{
            "taskId": task_id,
            "preExecTime": now_ms,
            "objNoExecTimes": [{"preExecTime": now_ms, "objNo": obj_no} for obj_no in obj_nos] if obj_nos else []
        }]"""

    if old_collect_kpis in full_code:
        full_code = full_code.replace(old_collect_kpis, new_collect_kpis)
        print("-> Applied empty obj_nos support to collect_kpis.")
    else:
        print("Warning: Could not find old_collect_kpis text exactly.")

    # 3. _parse_kpi_response to iterate over nested objRes
    old_parse_kpi = """    def _parse_kpi_response(self, response_json: dict) -> List[dict]:
        rows = []
        data_list = response_json.get("data", [])
        if not data_list:
            return rows

        for task_data in data_list:
            results = task_data.get("results", [])
            for res_item in results:
                obj_data = res_item.get("obj") or {}
                obj_no = res_item.get("objNo") or obj_data.get("objNo")
                if not obj_no:
                    continue
                
                try:
                    obj_no_int = int(obj_no)
                except ValueError:
                    continue
                
                cell_info = self._obj_to_cell.get(obj_no_int)
                
                if not cell_info:
                    obj_name = res_item.get("objName") or obj_data.get("objName") or ""
                    if obj_name:
                        obj_name_clean = obj_name.replace("-", "").replace(" ", "").upper()
                        for c_id, s_id in self._cell_to_site.items():
                            c_id_clean = c_id.replace("-", "").replace(" ", "").upper()
                            if c_id in obj_name or c_id_clean in obj_name_clean:
                                cell_info = {
                                    "cell_id": c_id,
                                    "site_id": s_id
                                }
                                self._obj_to_cell[obj_no_int] = cell_info
                                logger.info(f"Mapeado dinamicamente objNo {obj_no_int} ({obj_name}) -> Celula {c_id}")
                                break
                
                if not cell_info:
                    continue
                
                exec_time = res_item.get("execTime") or task_data.get("execTime")
                if exec_time:
                    timestamp = datetime.utcfromtimestamp(exec_time / 1000.0).isoformat()
                else:
                    timestamp = datetime.utcnow().isoformat()
                
                for metric, candidates in self.KPI_COLUMN_MAP.items():
                    val = self._find_metric_value(res_item, candidates)
                    if val is not None:
                        try:
                            rows.append({
                                "site_id":   cell_info["site_id"],
                                "cell_id":   cell_info["cell_id"],
                                "event_id":  self.event_id,
                                "timestamp": timestamp,
                                "metric":    metric,
                                "value":     float(val),
                            })
                        except (ValueError, TypeError):
                            pass
        return rows"""

    new_parse_kpi = """    def _parse_kpi_response(self, response_json: dict) -> List[dict]:
        rows = []
        data_list = response_json.get("data", [])
        if not data_list:
            return rows

        for task_data in data_list:
            results = task_data.get("results", [])
            for res_item in results:
                # If the response has "objRes", iterate over the items inside it.
                # Otherwise, treat the res_item itself as the object measurement.
                items_to_process = res_item.get("objRes", []) if "objRes" in res_item else [res_item]
                
                exec_time = res_item.get("execTime") or task_data.get("execTime")
                if exec_time:
                    timestamp = datetime.utcfromtimestamp(exec_time / 1000.0).isoformat()
                else:
                    timestamp = datetime.utcnow().isoformat()

                for item in items_to_process:
                    obj_data = item.get("obj") or {}
                    obj_no = item.get("objNo") or obj_data.get("objNo") or (item.get("obj", {}).get("objNo") if isinstance(item.get("obj"), dict) else None)
                    if not obj_no:
                        continue
                    
                    try:
                        obj_no_int = int(obj_no)
                    except ValueError:
                        continue
                    
                    cell_info = self._obj_to_cell.get(obj_no_int)
                    
                    if not cell_info:
                        obj_name = item.get("objName") or obj_data.get("objName") or ""
                        if obj_name:
                            obj_name_clean = obj_name.replace("-", "").replace(" ", "").upper()
                            for c_id, s_id in self._cell_to_site.items():
                                c_id_clean = c_id.replace("-", "").replace(" ", "").upper()
                                if c_id in obj_name or c_id_clean in obj_name_clean:
                                    cell_info = {
                                        "cell_id": c_id,
                                        "site_id": s_id
                                    }
                                    self._obj_to_cell[obj_no_int] = cell_info
                                    logger.info(f"Mapeado dinamicamente objNo {obj_no_int} ({obj_name}) -> Celula {c_id}")
                                    break
                    
                    if not cell_info:
                        continue
                    
                    for metric, candidates in self.KPI_COLUMN_MAP.items():
                        val = self._find_metric_value(item, candidates)
                        if val is not None:
                            try:
                                rows.append({
                                    "site_id":   cell_info["site_id"],
                                    "cell_id":   cell_info["cell_id"],
                                    "event_id":  self.event_id,
                                    "timestamp": timestamp,
                                    "metric":    metric,
                                    "value":     float(val),
                                })
                            except (ValueError, TypeError):
                                pass
        return rows"""

    if old_parse_kpi in full_code:
        full_code = full_code.replace(old_parse_kpi, new_parse_kpi)
        print("-> Applied nested objRes structure parsing.")
    else:
        print("Warning: Could not find old_parse_kpi text exactly.")

    # 4. _find_metric_value to support key/value list items
    old_find_val = """    def _find_metric_value(self, d, candidates):
        if not isinstance(d, dict):
            return None
        for k, v in d.items():
            if k in candidates:
                return v
        for k, v in d.items():
            if isinstance(v, dict):
                val = self._find_metric_value(v, candidates)
                if val is not None:
                    return val
            elif isinstance(v, list):
                for item in v:
                    if isinstance(item, dict):
                        val = self._find_metric_value(item, candidates)
                        if val is not None:
                            return val
        return None"""

    new_find_val = """    def _find_metric_value(self, d, candidates):
        if not isinstance(d, dict):
            return None
        
        # Support iManager counterRes structures: {"name": "...", "value": "..."}
        if d.get("name") in candidates and "value" in d:
            return d.get("value")

        for k, v in d.items():
            if k in candidates:
                return v
        for k, v in d.items():
            if isinstance(v, dict):
                val = self._find_metric_value(v, candidates)
                if val is not None:
                    return val
            elif isinstance(v, list):
                for item in v:
                    if isinstance(item, dict):
                        val = self._find_metric_value(item, candidates)
                        if val is not None:
                            return val
        return None"""

    if old_find_val in full_code:
        full_code = full_code.replace(old_find_val, new_find_val)
        print("-> Applied metric list value extraction support.")
    else:
        print("Warning: Could not find old_find_val text exactly.")

    # 5. NullCollector and build_collector update
    old_factory = """# ── Factory ──────────────────────────────────────────────────────────

def build_collector(event_config: dict, mock: bool = False) -> BaseCollector:
    if mock:
        return MockCollector(event_config)

    oss = event_config.get("oss", {})
    import_folder = oss.get("import_folder", "")

    if import_folder and Path(import_folder).exists():
        return CsvCollector(event_config, import_folder)

    base_url = oss.get("base_url", "")
    if base_url:
        return HttpCollector(event_config, base_url)

    logger.warning("Nenhuma fonte de dados configurada — usando MockCollector")
    return MockCollector(event_config)"""

    new_factory = """class NullCollector(BaseCollector):
    \"\"\"Retorna listas vazias para evitar geração de dados mockados em produção.\"\"\"
    def collect_kpis(self) -> List[dict]:
        return []

    def collect_vips(self) -> List[dict]:
        return []


# ── Factory ──────────────────────────────────────────────────────────

def build_collector(event_config: dict, mock: bool = False) -> BaseCollector:
    if mock:
        return MockCollector(event_config)

    oss = event_config.get("oss", {})
    import_folder = oss.get("import_folder", "")

    if import_folder and Path(import_folder).exists():
        return CsvCollector(event_config, import_folder)

    base_url = oss.get("base_url", "")
    if base_url:
        return HttpCollector(event_config, base_url)

    logger.warning("Nenhuma fonte de dados configurada — a coleta real está desativada.")
    return NullCollector(event_config)"""

    if old_factory in full_code:
        full_code = full_code.replace(old_factory, new_factory)
        print("-> Applied NullCollector and build_collector update.")
    else:
        print("Warning: Could not find old_factory text exactly.")

    # Save to collector.py
    COLLECTOR_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(COLLECTOR_FILE, "w", encoding="utf-8") as f:
        f.write(full_code)
        
    print(f"\n[SUCCESS] Reconstructed core/collector.py has been written to: {COLLECTOR_FILE}")

if __name__ == "__main__":
    reconstruct()
