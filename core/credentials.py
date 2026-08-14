"""
core/credentials.py — Fonte única de credenciais e catálogo de clientes/regionais.

Hierarquia: Cliente → Regional → base_url (IP do iManager).
  - Catálogo (cliente→regional→base_url): data/clientes.json — fixo no app, editável por arquivo
    (semeado do bundle na 1ª execução do .exe). Permite cadastrar novos clientes/regionais sem recompilar.
  - Credenciais do usuário: data/credentials.json — texto puro, indexado por cliente, com uma chave
    reservada `_shared` POR cliente (mesma conta para todas as regionais daquele cliente) + overrides
    por regional.

Precedência de credenciais (ver resolve_credentials):
    credentials.json[CLIENTE][REGIÃO] → credentials.json[CLIENTE]["_shared"] → ('', '').

NUNCA há fallback de conta hardcoded — faltando credencial, devolvemos vazio para o operador
digitar (modal sob demanda no app / login manual no navegador). Isso é proposital: o app circula
entre clientes diferentes e não pode carregar segredos embutidos.
"""

import json
import logging
import shutil
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

# Chave reservada dentro de credentials.json[CLIENTE] para a credencial compartilhada
# (mesma conta para todas as regionais daquele cliente).
SHARED_KEY = "_shared"

# Catálogo Cliente→Regional→base_url embutido (fallback quando data/clientes.json não existe).
# Editável em data/clientes.json sem recompilar. Clientes sem regionais ficam para cadastro posterior.
_DEFAULT_CLIENTES: dict[str, dict[str, str]] = {
    "TIM": {
        "SP": "https://10.220.50.9:31943",
        "RJ": "https://10.220.30.9:31943",
    },
    "Vivo": {},
    "Claro": {},
    "Brisanet": {},
}

# Mantida somente para compatibilidade de importações antigas. A resolução de
# eventos não usa mais este valor: cair silenciosamente em SP pode coletar no OSS
# errado quando cliente/regional estão incompletos.
_DEFAULT_BASE_URL = "https://10.220.50.9:31943"


def data_dir() -> Path:
    """Diretório de dados PERSISTENTE (frozen-aware). No .exe fica ao lado do executável —
    NÃO em _MEIPASS, que no onefile é temporário e apagado a cada execução."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent / "data"
    return Path(__file__).parent.parent / "data"


def credentials_file() -> Path:
    return data_dir() / "credentials.json"


def clientes_file() -> Path:
    return data_dir() / "clientes.json"


def _bundled(name: str) -> Path:
    """Caminho da cópia embutida no .exe (_MEIPASS/data/<name>)."""
    base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return base / "data" / name


def seed_files() -> None:
    """1ª execução do .exe: garante data/clientes.json e data/credentials.json graváveis ao lado
    do executável. clientes.json é semeado da cópia embutida (ou do fallback); credentials.json é
    semeado VAZIO — nunca distribuímos segredos no bundle."""
    if not getattr(sys, "frozen", False):
        return
    target = clientes_file()
    if not target.exists():
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            bundled = _bundled("clientes.json")
            if bundled.exists() and bundled.resolve() != target.resolve():
                shutil.copy2(bundled, target)
            else:
                target.write_text(
                    json.dumps(_DEFAULT_CLIENTES, indent=2, ensure_ascii=False), encoding="utf-8"
                )
            logger.info(f"clientes.json semeado -> {target}")
        except Exception as e:
            logger.warning(f"Falha ao semear clientes.json: {e}")
    cred = credentials_file()
    if not cred.exists():
        try:
            cred.parent.mkdir(parents=True, exist_ok=True)
            cred.write_text("{}", encoding="utf-8")
            logger.info(f"credentials.json (vazio) semeado -> {cred}")
        except Exception as e:
            logger.warning(f"Falha ao semear credentials.json: {e}")


# ── Catálogo de clientes/regionais ──────────────────────────────────

def load_clientes() -> dict:
    """Lê o catálogo `data/clientes.json` (cache sincronizado do servidor central).
    Fallback ao catálogo embutido se o arquivo não existir/for inválido.

    Aceita dois formatos por cliente (ver _regionais_map):
      - simples: {"SP": "https://...", "RJ": "..."}
      - rico (sincronizado): {"name": ..., "logo": ..., "regionais": {"SP": "https://..."}}"""
    f = clientes_file()
    try:
        if f.exists():
            data = json.loads(f.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception as e:
        logger.warning(f"Não foi possível ler clientes.json: {e}")
    return dict(_DEFAULT_CLIENTES)


def _regionais_map(entry) -> dict:
    """Extrai o mapa {REGIÃO: base_url} de um item de cliente, em qualquer dos formatos."""
    if not isinstance(entry, dict):
        return {}
    if isinstance(entry.get("regionais"), dict):
        return entry["regionais"]
    # formato simples {region: url} — ignora chaves de metadados conhecidas
    return {k: v for k, v in entry.items() if k not in ("name", "logo", "regionais") and isinstance(v, str)}


def resolve_base_url(oss: dict) -> str:
    """Resolve a base_url por ``oss.base_url`` ou cliente/regional exatos.

    A função falha fechado quando não existe uma correspondência. Assumir SP
    para um evento incompleto pode enviar credenciais e checkpoints ao OSS errado.
    """
    oss = oss or {}
    explicit = (oss.get("base_url") or "").strip()
    if explicit:
        return explicit.rstrip("/")
    cliente = (oss.get("cliente") or "").strip()
    region = (oss.get("region") or "").strip().upper()
    regionais = _regionais_map(load_clientes().get(cliente))
    for rk, url in regionais.items():
        if rk.upper() == region and url:
            return url.rstrip("/")
    identity = f"cliente='{cliente or 'N/D'}' regional='{region or 'N/D'}'"
    raise ValueError(
        f"OSS sem base_url configurada para {identity}. "
        "Cadastre a regional em data/clientes.json ou informe oss.base_url."
    )


def clientes_summary() -> dict:
    """Resumo do catálogo para a UI: {cliente: {"name", "logo", "regionais": [REGIÃO, ...]}}."""
    out = {}
    for cliente, entry in load_clientes().items():
        regionais = _regionais_map(entry)
        logo = entry.get("logo", "") if isinstance(entry, dict) else ""
        out[cliente] = {
            "name": (entry.get("name") if isinstance(entry, dict) else None) or cliente,
            "logo": logo,
            "regionais": sorted(regionais.keys()),
        }
    return out


# ── Credenciais ──────────────────────────────────────────────────────

def load_credentials() -> dict:
    f = credentials_file()
    try:
        if f.exists():
            data = json.loads(f.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception as e:
        logger.warning(f"Não foi possível ler credentials.json: {e}")
    return {}


def _save_credentials(data: dict) -> None:
    f = credentials_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def resolve_credentials(cliente: str = "", region: str = "") -> tuple:
    """Resolve (username, password) para (cliente, regional).
    Precedência: credentials.json[CLIENTE][REGIÃO] → [CLIENTE][_shared] → ('', '')."""
    cliente = (cliente or "").strip()
    region = (region or "").strip().upper()
    bloco = load_credentials().get(cliente) or {}
    for rk, entry in bloco.items():
        if rk == SHARED_KEY or not isinstance(entry, dict):
            continue
        if rk.upper() == region and entry.get("username") and entry.get("password"):
            return entry["username"], entry["password"]
    shared = bloco.get(SHARED_KEY) or {}
    if shared.get("username") and shared.get("password"):
        return shared["username"], shared["password"]
    logger.warning(
        f"Sem credenciais para cliente='{cliente}' regional='{region}'. "
        "Login ficará em branco para preenchimento manual."
    )
    return "", ""


def has_credentials(cliente: str = "", region: str = "") -> bool:
    user, pwd = resolve_credentials(cliente, region)
    return bool(user and pwd)


def save_credential(cliente: str, region: str, username: str, password: str) -> None:
    cliente = (cliente or "").strip()
    region = (region or "").strip().upper()
    data = load_credentials()
    bloco = data.setdefault(cliente, {})
    bloco[region] = {"username": username or "", "password": password or ""}
    _save_credentials(data)


def save_shared(cliente: str, username: str, password: str) -> None:
    cliente = (cliente or "").strip()
    data = load_credentials()
    bloco = data.setdefault(cliente, {})
    bloco[SHARED_KEY] = {"username": username or "", "password": password or ""}
    _save_credentials(data)


def delete_credential(cliente: str, region: str) -> None:
    """Remove o override de uma regional (volta a herdar a compartilhada do cliente)."""
    cliente = (cliente or "").strip()
    region = (region or "").strip().upper()
    data = load_credentials()
    bloco = data.get(cliente) or {}
    for rk in list(bloco.keys()):
        if rk != SHARED_KEY and rk.upper() == region:
            bloco.pop(rk, None)
    _save_credentials(data)


def status_for(cliente: str) -> dict:
    """Status de credenciais de um cliente, SEM expor senhas. Estrutura:
    {shared:{username, configured}, regionais:[{region, base_url, username, configured, uses_shared}]}."""
    cliente = (cliente or "").strip()
    creds = load_credentials().get(cliente) or {}
    shared = creds.get(SHARED_KEY) or {}
    shared_ok = bool(shared.get("username") and shared.get("password"))
    regionais = []
    for region, url in _regionais_map(load_clientes().get(cliente)).items():
        entry = next(
            (e for rk, e in creds.items()
             if rk != SHARED_KEY and rk.upper() == region.upper() and isinstance(e, dict)),
            None,
        )
        own_ok = bool(entry and entry.get("username") and entry.get("password"))
        regionais.append({
            "region": region,
            "base_url": url,
            "username": (entry.get("username") if entry else "") or "",
            "configured": own_ok or shared_ok,
            "uses_shared": (not own_ok) and shared_ok,
        })
    return {
        "shared": {"username": shared.get("username") or "", "configured": shared_ok},
        "regionais": regionais,
    }
