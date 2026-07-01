"""
Coletor de alarmes correntes do iMaster MAE (FM website) COM filtro por tipo.

Diferente de `alarm.py` (que baixa todos os alarmes ativos), este script reproduz
o que a tela "Current Alarms" faz quando um filtro por tipo de alarme e aplicado:

    1) cmd 1102 -> cria uma "visao" no servidor com a condition filtrada e
                   devolve um modelID novo (a chave dessa visao).
    2) cmd 1103 -> usando esse modelID, pagina o resultado ja filtrado.

A condition do 1102 nao aceita uma lista simples de alarmId: ela exige pares
{alarmId, alarmGroupId}. O mesmo alarme existe cadastrado em varias familias de
equipamento (BTS5900, GBTS, BTS3900...), uma por alarmGroupId. O catalogo
exportado do proprio sistema (`alarms/catalogo-alarmes.csv`) ja traz, para cada
nome de alarme, exatamente quais pares {alarmId, alarmGroupId} existem na rede,
entao montamos os pares reais (sem chutar produto cartesiano).

Rodar dentro da rede/VPN onde o iMaster responde.
Dependencias: pip install requests pandas openpyxl

Uso (linha de comando):
    python imaster_alarms.py                       # alarmes padrao (VSWR + Cell Unavailable)
    python imaster_alarms.py --alarms "RF Unit VSWR Threshold Crossed" "Cell Unavailable"
    python imaster_alarms.py --list                # lista os nomes disponiveis no catalogo
    python imaster_alarms.py --harvest-groups      # descobre os alarmGroupId reais da rede
"""

import argparse
import csv
import json
import time
from pathlib import Path
from typing import Dict, List, Tuple

import requests
import urllib3
import pandas as pd

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ============================================================
# 1) CONFIG FIXA
# ============================================================
BASE = "https://10.220.50.9:31943"
ENDPOINT = "/rest/fmwebsite/v1/commands"
PAGE = 148          # tamanho da janela (mesmo do navegador)
SLEEP = 0.3         # pausa entre paginas, para nao martelar o servidor
VERIFY_SSL = False  # cert interno autoassinado; troque por caminho do CA se tiver

# Catalogo exportado do sistema (tela "Alarm Name"): colunas
# Alarm Group ID, Alarm Group Name, Alarm ID, Alarm Name, Alarm Severity.
# Da, para cada nome de alarme, os pares {alarmId, alarmGroupId} reais da rede.
# Re-exporte do sistema e sobrescreva este arquivo quando a base mudar.
CATALOG_PATH = Path(__file__).resolve().parent / "alarms" / "catalogo-alarmes.csv"

# Alarmes pre-selecionados quando o usuario nao passa nenhum (os mais relevantes hoje).
DEFAULT_ALARM_NAMES = ["RF Unit VSWR Threshold Crossed", "Cell Unavailable"]

# ============================================================
# 2) TOKENS VOLATEIS  <-- atualize a cada nova sessao do navegador
#    (copie de uma requisicao fresca no DevTools / "Copy as cURL")
# ============================================================
BSPSESSION = ("x-rxtj1dar7z07uo9c3wqkju2o6q0amr6ms508aojshchccb6nunbstco709"
              "mlul4a0489g5ddnwo4jvin3wlepddec6mk2mvwemjvpdlig7069imp5idcsbfzs8ry3uru")
ROARAND = "a11b2345c2d0590bfdae77915c06bb543bd68eb134556e3d"
# O servidor aceita vazio: o bspSessionId so e ecoado no sufixo "@" do modelID,
# que e criado e consumido na mesma execucao. Preencha so se o ambiente exigir.
BSP_SESSION_ID = ""

# ============================================================
# 3) SESSAO HTTP
# ============================================================
COOKIES = {
    "locale": "en-us",
    "delimiter": "-",
    "format": "yyyy-MM-dd HH:mm:ss",
    "timezoneoffset": "-180",
    "user_time_show_dst": "1",
    "timezone": "America/Sao_Paulo",
    "timemode": "client",
    "multiLanguage": "false",
    "bspsession": BSPSESSION,
}

HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Content-Type": "application/json",
    "Origin": BASE,
    "Referer": f"{BASE}/eviewwebsite/index.html",
    "X-Non-Renewal-Session": "true",   # nao renova a sessao a cada poll
    "roarand": ROARAND,
    "x-requested-with": "XMLHttpRequest",
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/149.0.0.0 Safari/537.36"),
}

SEVERITY = {1: "Critical", 2: "Major", 3: "Minor", 4: "Warning",
            5: "Indeterminate", 6: "Cleared"}

# condition fixa: niveis/status/eventType que a tela "Current Alarms" sempre envia.
_BASE_CONDITION = {
    "alarmLevel": ["CRITICAL", "MAJOR", "MINOR", "WARNING"],
    "alarmStatus": [12, 10, 11, 13],
    "eventType": {
        "value": [str(i) for i in range(1, 17)],
        "operation": "in",
    },
    "specialAlarmStatus": {"value": ["0"], "operation": "in"},
    "orders": [{"field": "ColArriveUtc", "order": 1}],
}

# additionalCondition fixa (igual nos HAR com e sem filtro).
_ADDITIONAL_CONDITION = {
    "alarmGroupId": {"operation": "in", "value": []},
    "soundInfoCond": [
        {"severity": s, "alarmStatus": "1", "duration": 60} for s in (1, 2, 3, 4)
    ],
}


def build_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    s.cookies.update(COOKIES)
    return s


_SESSION_HINT = ("Atualize BSPSESSION/ROARAND/BSP_SESSION_ID no topo do arquivo "
                 "(copie de uma requisicao fresca no DevTools / 'Copy as cURL').")


def _check_session_alive(resp: requests.Response) -> None:
    """Para de forma clara quando a sessao expirou.

    Cobre os dois sintomas: HTTP 401/403 (token recusado) e o redirecionamento
    para o login SSO, que pode voltar HTTP 200 com HTML longo -- por isso a
    deteccao confiavel inspeciona a URL final da resposta (unisso/login.action),
    conforme a convencao do projeto.
    """
    if resp.status_code in (401, 403):
        raise RuntimeError(f"Sessao expirada (HTTP {resp.status_code}). {_SESSION_HINT}")
    final_url = (resp.url or "").lower()
    if "unisso" in final_url or "login.action" in final_url:
        raise RuntimeError(
            "Sessao expirada: a resposta foi redirecionada para o login SSO. "
            + _SESSION_HINT
        )


# Par (alarmId, alarmGroupId) como existe no catalogo.
Pair = Tuple[str, str]


# ============================================================
# 4) CATALOGO (nome -> pares {alarmId, alarmGroupId}) E RESOLUCAO DE NOMES
# ============================================================
def load_catalog(path: Path = CATALOG_PATH) -> Dict[str, List[Pair]]:
    """Le o catalogo exportado do sistema e devolve {nome: [(alarmId, alarmGroupId), ...]}.

    O CSV tem BOM e cabecalho (Alarm Group ID, Alarm Group Name, Alarm ID,
    Alarm Name, Alarm Severity). Um mesmo nome aparece em varios grupos (e ate
    com alarmId diferente por grupo), entao acumulamos todos os pares por nome.
    Alguns nomes vem com tabs/espacos nas pontas, entao normalizamos com strip().
    """
    catalog: Dict[str, List[Pair]] = {}
    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            name = (row.get("Alarm Name") or "").strip()
            alarm_id = (row.get("Alarm ID") or "").strip()
            group_id = (row.get("Alarm Group ID") or "").strip()
            if not (name and alarm_id and group_id):
                continue
            pair = (alarm_id, group_id)
            pairs = catalog.setdefault(name, [])
            if pair not in pairs:
                pairs.append(pair)
    return catalog


def resolve_pairs(alarm_names: List[str],
                  catalog: Dict[str, List[Pair]]) -> List[Pair]:
    """Traduz os nomes escolhidos nos pares (alarmId, alarmGroupId) do catalogo.

    Valida nomes inexistentes e deduplica pares repetidos entre nomes.
    """
    pairs: List[Pair] = []
    missing: List[str] = []
    for name in alarm_names:
        key = name.strip()
        if key in catalog:
            for pair in catalog[key]:
                if pair not in pairs:
                    pairs.append(pair)
        else:
            missing.append(name)
    if missing:
        raise ValueError(
            "Nome(s) de alarme nao encontrado(s) no catalogo: "
            + ", ".join(repr(m) for m in missing)
        )
    return pairs


# ============================================================
# 5) MONTAGEM DA CONDITION
# ============================================================
def build_condition(pairs: List[Pair]) -> str:
    """Monta a condition (string JSON) do cmd 1102 com os pares filtrados.

    Quando `pairs` esta vazio, devolve a condition sem restricao de alarmGroupId
    (equivalente a tela sem filtro) -- usado pelo harvest_groups.
    """
    condition = dict(_BASE_CONDITION)
    if pairs:
        value = [{"alarmId": aid, "alarmGroupId": gid} for aid, gid in pairs]
        condition["alarmGroupId"] = {"operation": "in", "value": value}
    return json.dumps(condition)


# ============================================================
# 6) cmd 1102 -- CRIACAO DO FILTRO / MODELID
# ============================================================
def _extract_model_id(payload: dict):
    """Procura o modelID novo na resposta do 1102 em varios caminhos possiveis."""
    if not isinstance(payload, dict):
        return None
    params = payload.get("parameters", {})
    for container in (params, payload):
        if not isinstance(container, dict):
            continue
        mid = container.get("modelID") or container.get("modelId")
        if mid:
            return mid
        result = container.get("result")
        if isinstance(result, dict):
            mid = result.get("modelID") or result.get("modelId")
            if mid:
                return mid
    return None


def create_model(session: requests.Session, condition: str) -> str:
    """Executa o cmd 1102 e devolve o modelID que representa o filtro."""
    params = {"_t": int(time.time() * 1000), "_cmd": 1102}
    body = {
        "cmd": 1102,
        "parameters": {
            "modelID": None,
            "bspSessionId": BSP_SESSION_ID,
            "showStatistic": False,
            "additionalCondition": json.dumps(_ADDITIONAL_CONDITION),
            "timeMode": 3,
            "urlCondition": None,
            "expression": "",
            "autoRefresh": False,   # foto estavel
            "isScrollLock": False,
            "condition": condition,
        },
    }
    r = session.post(BASE + ENDPOINT, params=params, json=body,
                     verify=VERIFY_SSL, timeout=30)
    _check_session_alive(r)
    r.raise_for_status()

    new_rand = r.headers.get("roarand") or session.cookies.get("roarand")
    if new_rand:
        session.headers["roarand"] = new_rand

    payload = r.json()
    model_id = _extract_model_id(payload)
    if not model_id:
        raise RuntimeError(
            "modelID nao encontrado na resposta do cmd 1102. "
            f"Resposta: {json.dumps(payload)[:500]}"
        )
    return model_id


# ============================================================
# 7) cmd 1103 -- LEITURA PAGINADA
# ============================================================
def fetch_page(session: requests.Session, model_id: str, frm: int, to: int,
               auto_refresh: bool = False) -> dict:
    params = {"_t": int(time.time() * 1000), "_cmd": 1103}
    body = {
        "cmd": 1103,
        "parameters": {
            "modelID": model_id,
            "bspSessionId": BSP_SESSION_ID,
            "timeMode": 3,
            "versionFlag": False,
            "csns": [],
            "autoRefresh": auto_refresh,  # False = foto estavel para o bulk
            "scrollLock": False,
            "from": frm,
            "to": to,
        },
    }
    r = session.post(BASE + ENDPOINT, params=params, json=body,
                     verify=VERIFY_SSL, timeout=30)
    _check_session_alive(r)
    r.raise_for_status()

    new_rand = r.headers.get("roarand") or session.cookies.get("roarand")
    if new_rand:
        session.headers["roarand"] = new_rand

    payload = r.json()
    return payload.get("parameters", payload)


def collect_all(session: requests.Session, model_id: str) -> List[dict]:
    """Pagina o cmd 1103 ate esgotar o total informado na primeira resposta."""
    first = fetch_page(session, model_id, 1, PAGE)
    total = int(first.get("total", 0))
    rows = list(first.get("data", []))

    result = first.get("result", {})
    for lvl in result.get("levelResults", []):
        print(f"  nivel {lvl.get('level')}: {lvl.get('value')}")
    print(f"  total de alarmes: {total}")

    frm = PAGE + 1
    while len(rows) < total:
        to = frm + PAGE - 1
        page = fetch_page(session, model_id, frm, to)
        batch = page.get("data", [])
        if not batch:
            break
        rows.extend(batch)
        print(f"  coletados {len(rows)}/{total}")
        frm += PAGE
        time.sleep(SLEEP)

    return rows


# ============================================================
# 8) ACHATAMENTO DOS CAMPOS UTEIS
# ============================================================
def flatten(a: dict) -> dict:
    ext = a.get("extParams") or {}
    return {
        "csn": a.get("csn"),
        "alarm_id": a.get("alarmId"),
        "alarm_group_id": a.get("alarmGroupId"),
        "alarm_name": a.get("alarmName"),
        "severity": SEVERITY.get(a.get("severity"), a.get("severity")),
        "cleared": a.get("cleared"),
        "acked": a.get("acked"),
        "source": a.get("meName") or ext.get("alarmSource"),
        "product": a.get("productName"),
        "ip": a.get("address"),
        "ne_dn": a.get("nativeMeDn"),
        "cell_name": ext.get("ColCellName") or a.get("nativeMoName"),
        "enodeb_id": ext.get("ColeNodeBID"),
        "gnodeb_id": ext.get("ColgNodeBID"),
        "location": a.get("subNet"),
        "occur_time": a.get("occurUtc") or a.get("firstOccurUtc"),
        "arrive_time": a.get("arriveUtc"),
        "moi": a.get("moi"),
        "additional_info": a.get("additionalInformation"),
    }


# ============================================================
# 9) PONTO DE ENTRADA DE ALTO NIVEL
# ============================================================
def pull_alarms(session: requests.Session,
                alarm_names: List[str],
                catalog: Dict[str, List[Pair]] = None) -> pd.DataFrame:
    """Coleta os alarmes dos tipos escolhidos e devolve um DataFrame pronto.

    Une todas as etapas: resolve nomes -> pares, monta o filtro, cria o modelID,
    pagina o resultado, achata e deduplica por csn.
    """
    if catalog is None:
        catalog = load_catalog()
    pairs = resolve_pairs(alarm_names, catalog)
    condition = build_condition(pairs)
    model_id = create_model(session, condition)
    raw = collect_all(session, model_id)

    df = pd.DataFrame(flatten(a) for a in raw)
    if "csn" in df.columns:
        df = df.drop_duplicates(subset="csn")  # lista viva pode repetir linhas
    return df


def harvest_groups(session: requests.Session) -> List[str]:
    """Roda uma coleta sem filtro para descobrir os alarmGroupId reais da rede.

    Util para confirmar/atualizar KNOWN_ALARM_GROUP_IDS, caso a estrategia de
    pares "a mais" precise ser refinada.
    """
    model_id = create_model(session, build_condition([]))
    raw = collect_all(session, model_id)
    groups = sorted({str(a.get("alarmGroupId")) for a in raw if a.get("alarmGroupId")})
    return groups


# ============================================================
# 10) CLI
# ============================================================
def _export(df: pd.DataFrame) -> None:
    stamp = time.strftime("%Y%m%d_%H%M%S")
    csv_path = f"alarmes_filtrados_{stamp}.csv"
    xlsx_path = f"alarmes_filtrados_{stamp}.xlsx"
    df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    df.to_excel(xlsx_path, index=False)
    print(f"\n{len(df)} alarmes salvos em:")
    print(f"  {csv_path}")
    print(f"  {xlsx_path}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Coleta alarmes do iMaster MAE filtrando por tipo de alarme."
    )
    parser.add_argument(
        "--alarms", nargs="+", metavar="NOME",
        help="Nomes exatos dos alarmes (como aparecem no catalogo). "
             f"Padrao: {DEFAULT_ALARM_NAMES}",
    )
    parser.add_argument(
        "--list", action="store_true",
        help="Lista os nomes de alarme disponiveis no catalogo e sai.",
    )
    parser.add_argument(
        "--harvest-groups", action="store_true",
        help="Roda uma coleta sem filtro e imprime os alarmGroupId reais da rede.",
    )
    args = parser.parse_args()

    catalog = load_catalog()

    if args.list:
        for name in sorted(catalog):
            n_groups = len(catalog[name])
            print(f"{name}\t({n_groups} grupo(s))")
        print(f"\n{len(catalog)} alarmes distintos no catalogo.")
        return

    session = build_session()

    if args.harvest_groups:
        print("Descobrindo alarmGroupId da rede (coleta sem filtro)...")
        groups = harvest_groups(session)
        print(f"\n{len(groups)} grupos encontrados:")
        for gid in groups:
            print(f"  {gid}")
        return

    alarm_names = args.alarms or DEFAULT_ALARM_NAMES
    print(f"Coletando alarmes filtrados: {alarm_names}")
    df = pull_alarms(session, alarm_names, catalog)
    _export(df)


if __name__ == "__main__":
    main()
