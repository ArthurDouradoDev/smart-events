# Plano de Implementação — Suporte Multi-Regional iManager

**Data:** 2026-05-29  
**Referência:** `mae-api.md` (testes na regional `10.220.30.9`)

---

## Objetivo

O coletor HTTP (`HttpCollector`) hoje usa um único IP de iManager (`10.220.50.9`, regional SP) hard-coded como fallback. O objetivo é fazer o sistema escolher o IP correto com base no campo `oss.region` do JSON do evento, aplicar os dois fixes de compatibilidade identificados em `mae-api.md` e garantir que a sessão autenticada (arquivo `session.json`) seja isolada por regional.

---

## Critérios de aceitação

| # | Critério |
|---|---|
| 1 | Evento com `oss.region: "SP"` → coleta via `10.220.50.9` (comportamento atual) |
| 2 | Evento com `oss.region: "RJ"` (ou qualquer não-SP) → coleta via `10.220.30.9` |
| 3 | `oss.base_url` explícita no evento sobrescreve qualquer mapeamento por regional |
| 4 | Cada regional usa arquivo de sessão isolado (`data/session_sp.json`, `data/session_rj.json`…) |
| 5 | `_renew_session` invoca `get_session.py` passando `--base-url` e `--session-file` corretos |
| 6 | `isPlayback: "false"` presente em todos os calls a `msg-explain-info` |
| 7 | `rowNo` em `_parse_filtered_trace_response` usa `serialNo` do item (com fallback `idx+1`) |
| 8 | Sem regressão na regional SP |

---

## Arquivos alterados

| Arquivo | Natureza |
|---|---|
| [core/collector.py](core/collector.py) | 4 pontos de mudança (detalhados abaixo) |
| [scratch/get_session.py](scratch/get_session.py) | Aceitar `--base-url` e `--session-file` |
| [events/sample_event.json](events/sample_event.json) | Documentar campo `oss.region` |

`core/models.py` já tem `OssConfig.region: str = ""` — sem alterações.

---

## Mudanças em `core/collector.py`

### 1. Mapeamento regional → IP (no topo do arquivo, após os imports)

```python
# Mapeamento de regional para base_url do iManager.
# oss.base_url no evento sempre tem precedência sobre este mapa.
_REGIONAL_BASE_URLS: dict[str, str] = {
    "SP": "https://10.220.50.9:31943",
    "RJ": "https://10.220.30.9:31943",
}
_DEFAULT_BASE_URL = "https://10.220.50.9:31943"  # fallback = SP
```

---

### 2. `build_collector` — resolução de `base_url` por regional

**Localização:** [core/collector.py:1193](core/collector.py#L1193) (função `build_collector`)

```python
# Antes:
base_url = oss.get("base_url", "")
if not base_url:
    base_url = "https://10.220.50.9:31943"

return HttpCollector(event_config, base_url)

# Depois:
base_url = oss.get("base_url", "")
if not base_url:
    region = oss.get("region", "SP").upper()
    base_url = _REGIONAL_BASE_URLS.get(region, _DEFAULT_BASE_URL)

return HttpCollector(event_config, base_url)
```

Precedência explícita: `oss.base_url` > `oss.region` > padrão SP.

---

### 3. `HttpCollector.__init__` — derivar caminho do session file

**Localização:** [core/collector.py:306](core/collector.py#L306)

O `_session_file` é derivado do IP do `base_url`. SP (`10.220.50.9`) mantém o nome `session.json` (compatibilidade com sessões existentes); outros IPs geram `session_<octeto3>_<octeto4>.json`.

```python
def __init__(self, event_config: dict, base_url: str, session_cookie: str = ""):
    super().__init__(event_config)
    self.base_url = base_url.rstrip("/")
    self.session_cookie = session_cookie
    self._session_file = self._resolve_session_file(self.base_url)
    # ... resto inalterado

@staticmethod
def _resolve_session_file(base_url: str) -> Path:
    """Deriva o caminho do session file a partir da base_url."""
    _SP_DEFAULT = "https://10.220.50.9:31943"
    if not base_url or base_url.rstrip("/") == _SP_DEFAULT.rstrip("/"):
        return Path(__file__).parent.parent / "data" / "session.json"
    try:
        import urllib.parse
        host = urllib.parse.urlparse(base_url).hostname or base_url
        slug = host.replace(".", "_")
    except Exception:
        slug = "regional"
    return Path(__file__).parent.parent / "data" / f"session_{slug}.json"
```

Exemplos de mapeamento:
| `base_url` | `_session_file` |
|---|---|
| `https://10.220.50.9:31943` (ou vazio) | `data/session.json` |
| `https://10.220.30.9:31943` | `data/session_10_220_30_9.json` |

---

### 4. `_load_session_data` — usar `self._session_file`

**Localização:** [core/collector.py:334](core/collector.py#L334)

```python
# Antes:
def _load_session_data(self) -> dict:
    session_path = Path(__file__).parent.parent / "data" / "session.json"

# Depois:
def _load_session_data(self) -> dict:
    session_path = self._session_file
```

Uma linha alterada; o resto do método é idêntico.

---

### 5. `_renew_session` — passar `--base-url` e `--session-file` ao script

**Localização:** [core/collector.py:466](core/collector.py#L466) (chamada `subprocess.run`)

```python
# Antes:
result = subprocess.run(
    [str(python_exe), str(script_path), "--headless", "--module", module],
    ...
)

# Depois:
result = subprocess.run(
    [
        str(python_exe), str(script_path),
        "--headless",
        "--module", module,
        "--base-url", self.base_url,
        "--session-file", str(self._session_file),
    ],
    ...
)
```

---

### 6. Fix 1 — `isPlayback: "false"` em `_fetch_msg_explain_info`

**Localização:** [core/collector.py:1048](core/collector.py#L1048)

```python
# Adicionar o parâmetro na chamada GET existente:
resp = session.get(url, params={
    "nocache": int(time.time() * 1000),
    "taskId": task_id,
    "msgId": sess_msg_id,
    "rowNo": row_no,
    "tabularFlag": "y",
    "isSubscribe": "false",
    "isSecondDecode": "false",
    "isPlayback": "false",    # ← ADICIONAR
}, timeout=30)
```

> Sem risco de regressão em SP — o parâmetro apenas torna explícito o comportamento padrão já adotado pela regional SP.

---

### 7. Fix 2 — `serialNo` como `rowNo` em `_parse_filtered_trace_response`

**Localização:** [core/collector.py:980](core/collector.py#L980)

```python
# Antes:
content_json = self._fetch_msg_explain_info(
    session, task_id, sess_msg_id, idx + 1  # FARS usa rowNo 1-indexado
)

# Depois:
row_no = item.get("serialNo") or (idx + 1)  # serialNo = posição absoluta no trace
content_json = self._fetch_msg_explain_info(
    session, task_id, sess_msg_id, row_no
)
```

> Risco mínimo: se `serialNo` for `None` ou `0` (como na regional SP), o `or (idx + 1)` mantém o comportamento anterior exato. Quando `serialNo` vem preenchido (como na nova regional), aponta para a mensagem correta do trace histórico extenso.

---

## Mudanças em `scratch/get_session.py`

O script hoje tem `BASE_URL` e o caminho de saída hard-coded. Adicionar dois argumentos CLI opcionais, mantendo os valores atuais como padrão:

```python
import argparse

parser = argparse.ArgumentParser()
parser.add_argument("--headless", action="store_true")
parser.add_argument("--module", default="both", choices=["both", "monitoring", "trace"])
parser.add_argument("--base-url", default="https://10.220.50.9:31943",
                    help="URL base do iManager")
parser.add_argument("--session-file", default="data/session.json",
                    help="Caminho do arquivo de sessão de saída")
args = parser.parse_args()

BASE_URL = args.base_url
SESSION_FILE = Path(args.session_file)
```

Todas as referências internas a `BASE_URL` e ao caminho de saída passam a usar essas variáveis. O comportamento sem argumentos é idêntico ao atual.

---

## Mudanças em `events/sample_event.json`

Documentar o campo `oss.region` com um exemplo:

```json
"oss": {
  "region": "SP",
  "base_url": "",
  "import_folder": ""
}
```

Valores possíveis de `region`: `"SP"` (padrão) ou `"RJ"`. Para adicionar novas regionais no futuro, basta incluir a entrada em `_REGIONAL_BASE_URLS` em `collector.py`.

---

## Ordem de implementação

1. **Fix 1 e Fix 2** em `collector.py` (baixo risco, isolados, sem dependências)
2. Constante `_REGIONAL_BASE_URLS` e atualização de `build_collector`
3. `_resolve_session_file` + `__init__` + `_load_session_data`
4. `_renew_session` (depende do `_session_file` estar disponível)
5. `get_session.py` (aceitar `--base-url`/`--session-file`)
6. Atualizar `sample_event.json`
7. Testar regional SP (regressão) com `python main.py --mock`
8. Testar regional RJ com `scratch/test_regional.py --skip-login`

---

## Riscos e mitigações

| Risco | Mitigação |
|---|---|
| `session.json` SP deixa de ser encontrado | `_resolve_session_file` retorna `data/session.json` para o IP SP (mesmo caminho anterior) |
| `serialNo` = 0 causa regression em SP | Fallback `or (idx + 1)` preserva comportamento original |
| `isPlayback: "false"` altera resposta em SP | Parâmetro é explicitação do padrão; sem efeito colateral esperado |
| Nova regional sem sessão gerada | `_build_session` já loga warning; comportamento de fallback existente inalterado |
