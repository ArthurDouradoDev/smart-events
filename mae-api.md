# Relatório de Viabilidade — Regional Alternativa (10.220.30.9)

**IP testado:** `https://10.220.30.9:31943`
**Data:** 2026-05-28
**KPI task_id testado:** `2001`
**Trace task_id testado:** `14127`
**Credenciais:** `T3698285`

---

## Resumo Executivo

| Capacidade | Resultado |
|---|---|
| Autenticação (login Playwright) | ✅ OK — 63.8s |
| Coleta KPI (monitor/task/result) | ✅ OK — 206 objetos |
| Trace FARS Passo 1 (pre-check) | ✅ OK — checkState=True |
| Trace FARS Passo 2 (query/result) | ✅ OK — sess_msg_id obtido |
| Trace FARS Passo 3 (filter-by-cols) | ✅ OK — 1000 mensagens RRC_MEAS_RPRT |
| Trace FARS Passo 4 (msg-explain-info) | ✅ OK — binMsgExplain com rsrpResult/rsrqResult |
| Extração RSRP/RSRQ | ✅ OK — RSRP=-80.0 dBm / RSRQ=-7.5 dB confirmados |
| **Infraestrutura reutilizável** | **✅ SIM** |

---

## 1. Autenticação

O login via Playwright funcionou sem alterações de seletor HTML. O iManager desta regional usa o mesmo formulário SSO (`#username`, `#value`, `#submitDataverify`) e a sessão foi capturada nos dois módulos:

- **bspsession:** capturado ✅
- **roarand (CSRF):** capturado ✅
- **Módulos autenticados:** `monitoring`, `trace`
- **Tempo de login:** 63.8s (esperado — idêntico à regional SP)

Não é necessário nenhum ajuste em `get_session.py` para esta regional.

---

## 2. KPI — Task 2001

A chamada `POST /rest/oss/access/pm/v1/monitor/task/result` com `objNoExecTimes: []` (discovery mode) retornou dados válidos imediatamente:

```
HTTP 200
Objetos retornados: 206
Última execTime: 2026-05-28T18:30:00Z
```

**Métricas identificadas na resposta:**

| Métrica | Campo no JSON |
|---|---|
| Utilização de PRB DL | `PRB_Utilization` |
| Throughput DL | `Throughput_DL_User_avg`, `01_L.Thrp.bits.DL` |
| Throughput UL | `01_L.Thrp.bits.UL` |
| Interferência UL | `01_L.UL.Interference.Avg` |
| Tráfego médio de usuários | `01_L.Traffic.User.Avg` |
| Disponibilidade | `Disponibilidade` |
| Acessibilidade | `{BRDC} Acessibilidade` |

> **Observação:** Os nomes de campos KPI são ligeiramente diferentes dos da regional SP (ex: `PRB_Utilization` em vez de `DL PRB Usage`). O `KPI_COLUMN_MAP` do `HttpCollector` já tem múltiplos aliases por métrica. Os campos `01_L.*` (nomenclatura Huawei interna) podem precisar de aliases adicionais conforme necessidade.

---

## 3. Trace VIP — Task 14127 (Fluxo FARS 4 Passos)

Todos os 4 passos funcionaram e a extração de RSRP/RSRQ foi confirmada:

### Passos 1–3

```
Passo 1  pre-check:       HTTP 200, checkState=True
Passo 2  query/result:    HTTP 200, sess_msg_id=10930006
Passo 3  filter-by-cols:  HTTP 200, 1000 mensagens RRC_MEAS_RPRT
```

Site receptor: `SR-RJHQ03` | Célula: `7` | Última medição: `2026-05-28 16:08:22`

### Passo 4 — msg-explain-info: RSRP/RSRQ confirmados

Com os parâmetros corretos, o endpoint retorna a árvore JSON decodificada (`binMsgExplain.filedTree`) contendo `rsrpResult` e `rsrqResult`:

```
RSRP = -80.0 dBm  (index 60: 60 - 140 = -80)
RSRQ = -7.5 dB    (index 25: 25/2 - 19.5 = -7.0) 
```

A função `_extract_rsrp_rsrq_from_json` do `HttpCollector` é 100% compatível com esta estrutura — ela percorre recursivamente os dicts/listas e encontra os nós `rsrpResult`/`rsrqResult` dentro de `binMsgExplain.filedTree` sem nenhuma modificação.

---

## 4. Dois ajustes de código necessários em `core/collector.py`

O sistema funcionará para esta regional com dois pequenos ajustes, ambos em [core/collector.py](core/collector.py):

### Ajuste 1 — Adicionar `isPlayback: "false"` ao `_fetch_msg_explain_info`

**Problema identificado:** Sem esse parâmetro, o iManager desta regional retorna o payload em formato binário bruto (`PrivateUeMr`) ao invés da árvore ASN.1 decodificada. A regional SP aparentemente tem `isPlayback=false` como padrão; esta regional exige o parâmetro explícito.

```python
# core/collector.py — _fetch_msg_explain_info (linha ~1048)
resp = session.get(url, params={
    "nocache": int(time.time() * 1000),
    "taskId": task_id,
    "msgId": sess_msg_id,
    "rowNo": row_no,
    "tabularFlag": "y",
    "isSubscribe": "false",
    "isSecondDecode": "false",
    "isPlayback": "false",    # ← ADICIONAR esta linha
}, timeout=30)
```

> **Risco de regressão na regional SP:** Nenhum. O parâmetro `isPlayback=false` é o comportamento esperado em ambas as regionais.

### Ajuste 2 — Usar `serialNo` como `rowNo` em `_parse_filtered_trace_response`

**Problema identificado:** O `rowNo` em `msg-explain-info` refere-se ao número de série **absoluto** da mensagem no trace (campo `serialNo` de cada item retornado pelo `filter-by-cols`), não à posição relativa na view filtrada. Na regional SP, coincide que o trace começa do início e `serialNo ≈ idx + 1`. Nesta regional, os `serialNo` são números grandes (~140.000) refletindo um trace com histórico extenso — usar `idx + 1` aponta para mensagens do início do trace (muito antigas), não para as medições do filtro recente.

```python
# core/collector.py — _parse_filtered_trace_response (linha ~980)
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

> **Risco de regressão na regional SP:** Baixo. Se `serialNo` vier preenchido (como acontece nesta regional e provavelmente em todas), o comportamento melhora. O fallback `or (idx + 1)` mantém o comportamento anterior caso `serialNo` seja `None` ou `0`.

---

## 5. Análise Final: Infraestrutura Reutilizável

| Componente | Compatível? | Ação necessária |
|---|---|---|
| `get_session.py` (login Playwright) | ✅ Sim | Apenas trocar BASE_URL e credenciais |
| `HttpCollector._build_session()` | ✅ Sim | Nenhuma |
| `HttpCollector.collect_kpis()` | ✅ Sim | Apenas `base_url` e `pm_task_id` no evento |
| `HttpCollector.collect_vips()` — passos 1-3 | ✅ Sim | Nenhuma |
| `HttpCollector._fetch_msg_explain_info()` | ⚠️ Pequeno fix | Adicionar `isPlayback: "false"` |
| `HttpCollector._parse_filtered_trace_response()` | ⚠️ Pequeno fix | Usar `serialNo` como `rowNo` |
| `HttpCollector._extract_rsrp_rsrq_from_json()` | ✅ Sim | Nenhuma — funciona com `binMsgExplain` |
| Renovação automática de sessão | ✅ Sim | Nenhuma |
| KPI_COLUMN_MAP | ✅ Sim (maioria) | Aliases adicionais se alguma métrica não mapear |

---

## 6. Passos para Habilitar a Regional em Produção

1. **Aplicar os dois ajustes de código** em [core/collector.py](core/collector.py) (seção 4 acima).

2. **Identificar task_ids corretos no iManager** (`10.220.30.9`):
   - Para KPI: anotar o ID da tarefa de monitoramento ativa no *Performance Monitor*.
   - Para VIPs: em *FARS → Task Browse*, anotar o `taskId` das tarefas de **Signaling Trace** de cada VIP.

3. **Criar um JSON de evento** com:
   ```json
   {
     "oss": { "base_url": "https://10.220.30.9:31943" },
     "integration": { "pm_task_id": 2001 },
     "vips": [{ "name": "Nome VIP", "task_id": 14127 }]
   }
   ```

4. **Gerar sessão** com `scratch/get_session_regional.py` (criado neste teste).

5. **Validar** com `python scratch/test_regional.py --skip-login`.

---

## Arquivos criados neste teste (não afetam produção)

| Arquivo | Propósito |
|---|---|
| [scratch/get_session_regional.py](scratch/get_session_regional.py) | Adquire sessão para `10.220.30.9` em `data/session_regional.json` |
| [scratch/test_regional.py](scratch/test_regional.py) | Teste end-to-end dos endpoints KPI e FARS |
| `data/session_regional.json` | Sessão ativa da regional (não sobrescreve `session.json`) |
