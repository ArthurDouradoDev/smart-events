"""
Coletor de alarmes correntes do iMaster MAE (FM website).

Reproduz a chamada que alimenta a tabela "Current Alarms":
    POST https://<host>/rest/fmwebsite/v1/commands?_cmd=1103
O corpo pede uma janela paginada (from/to). O script percorre o `total`
inteiro, achata os campos uteis e exporta CSV + Excel.

Rodar dentro da rede/VPN onde o iMaster responde.
Dependencias: pip install requests pandas openpyxl
"""

import time
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

# ============================================================
# 2) TOKENS VOLATEIS  <-- atualize a cada nova sessao do navegador
#    (copie de uma requisicao fresca no DevTools / "Copy as cURL")
# ============================================================
BSPSESSION = ("x-9j493t44sbbvmmsaem2pdgimbtbw860484rvanvztilgam2m7v9c853t3vg5"
              "emkapg0as6mpdilinuaolftgeroa9jfw07rsmqvummvxsb3y2oo75iur7zqr497yo76n")
ROARAND = "cb808029abd800f9cf3ce5f0e5433b1de7563a0a230bb222"
MODEL_ID = ("000.55-30-88-9912610826-71801283-25-106-11762807193-75-35-2-66-645-"
            "57-6865-117-109-75-60-92@260816")
JOB_ID = ("000.44-60-901018927-34082-26-40-54-105117787166114115104-95999894-"
          "247868110-105-115-8699")
BSP_SESSION_ID = "260816"

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


def build_session():
    s = requests.Session()
    s.headers.update(HEADERS)
    s.cookies.update(COOKIES)
    return s


# ============================================================
# 4) CHAMADA DO COMANDO 1103
# ============================================================
def fetch_page(session, frm, to, auto_refresh=False):
    params = {"_t": int(time.time() * 1000), "_cmd": 1103}
    body = {
        "cmd": 1103,
        "parameters": {
            "modelID": MODEL_ID,
            "jobId": JOB_ID,
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
    r.raise_for_status()

    # alguns ambientes Huawei rotacionam o roarand; reaproveita se vier um novo
    new_rand = r.headers.get("roarand") or session.cookies.get("roarand")
    if new_rand:
        session.headers["roarand"] = new_rand

    payload = r.json()
    # a resposta vem como {"parameters": {"result":..., "total":..., "data":[...]}}
    return payload.get("parameters", payload)


# ============================================================
# 5) ACHATAMENTO DOS CAMPOS UTEIS
# ============================================================
def flatten(a):
    ext = a.get("extParams") or {}
    return {
        "csn": a.get("csn"),
        "alarm_id": a.get("alarmId"),
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
# 6) LOOP DE PAGINACAO
# ============================================================
def collect_all(session):
    first = fetch_page(session, 1, PAGE)
    total = int(first.get("total", 0))
    rows = list(first.get("data", []))

    # resumo dos contadores (Critical/Major/Minor/Warning)
    result = first.get("result", {})
    for lvl in result.get("levelResults", []):
        print(f"  nivel {lvl.get('level')}: {lvl.get('value')}")
    print(f"  total de alarmes: {total}")

    frm = PAGE + 1
    while len(rows) < total:
        to = frm + PAGE - 1
        page = fetch_page(session, frm, to)
        batch = page.get("data", [])
        if not batch:
            break
        rows.extend(batch)
        print(f"  coletados {len(rows)}/{total}")
        frm += PAGE
        time.sleep(SLEEP)

    return rows


def main():
    session = build_session()
    print("Coletando alarmes...")
    raw = collect_all(session)

    df = pd.DataFrame(flatten(a) for a in raw)
    df = df.drop_duplicates(subset="csn")  # lista viva pode repetir linhas

    stamp = time.strftime("%Y%m%d_%H%M%S")
    csv_path = f"alarmes_{stamp}.csv"
    xlsx_path = f"alarmes_{stamp}.xlsx"
    df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    df.to_excel(xlsx_path, index=False)

    print(f"\n{len(df)} alarmes salvos em:")
    print(f"  {csv_path}")
    print(f"  {xlsx_path}")


if __name__ == "__main__":
    main()