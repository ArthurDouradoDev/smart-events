# Registro de Alterações — SmartEvents

> Sessão de desenvolvimento: 2026-05-27  
> Arquivos modificados: `core/collector.py`, `core/database.py`, `api/api.py`, `frontend/js/vip.js`, `frontend/index.html`  
> Arquivo criado: `planos/plano-coleta-vips.md`

---

## 1. `core/collector.py`

### 1.1 Novo endpoint de coleta de trace VIP — `filter-by-cols`

**Problema:** O fluxo anterior usava `GET /query/result` com `pageSize=500`, que retorna mensagens em ordem de inserção (mais antigas primeiro). Com o limite de `_MAX_MEAS_DECODES_PER_CYCLE = 50`, os primeiros 50 `RRC_MEAS_RPRT` decodificados eram os **mais antigos** da task — não os mais recentes. Isso causava dados desatualizados no painel de VIPs.

**Solução:** Adicionado o endpoint `POST /query/filter-by-cols` como etapa 3 do fluxo de coleta:

```
Etapa 1: GET /pre-check?taskId={id}&queryType=0
         → sem mudança

Etapa 2: GET /query/result?pageSize=10&taskId={id}&msgId=1
         → pageSize reduzido de 500 para 10 (só precisa do sess_msg_id da resposta)

Etapa 3: POST /query/filter-by-cols  ← NOVO
         → filtra apenas RRC_MEAS_RPRT no servidor
         → isAscend: false → mensagens mais recentes primeiro
         → pageSize: 1000
         → requer o msgId (sess_msg_id) retornado pelo passo 2

Etapa 4: msg-explain-info (sem mudança) para cada row, agora decodifica os
         _MAX_MEAS_DECODES_PER_CYCLE mais RECENTES em vez dos mais antigos
```

**Nota sobre `startTime`/`endTime`:** O iManager deserializa esses campos server-side antes de checar as flags `hasStartTime`/`hasEndTime`. Enviar `""` causava `ROA_EXFRAME_EXCEPTION` (HTTP 500). Corrigido para usar datas placeholder válidas (`"2000-01-01 00:00:00"` e datetime atual).

**Método novo:** `_parse_filtered_trace_response` — processa a resposta do `filter-by-cols` (todos os rows já são `RRC_MEAS_RPRT`, sem filtro de tipo no loop).

---

### 1.2 Correção de timezone nos timestamps do trace

**Problema:** O iManager retorna timestamps em horário local (America/Sao_Paulo, UTC−3). O código anterior os armazenava como UTC, fazendo o filtro `now − 60 minutos` do SQLite excluir dados recentes (o banco via `14:00` como hora, mas o SQLite sabia que "agora" é `17:00 UTC`).

**Solução:** `_parse_trace_timestamp` agora aplica o offset do OSS para converter para UTC antes de armazenar:

```python
dt_utc = dt_local - timedelta(minutes=self._oss_tz_offset_min)
# _oss_tz_offset_min default = -180 (UTC-3, Brasil)
# dt_utc = dt_local - (-180 min) = dt_local + 3h → UTC correto
```

Atributo `_oss_tz_offset_min` configurável via `event_config.oss.timezone_offset_min` (default: `−180`).

---

### 1.3 Tratamento de timeout de conexão em `collect_kpis` e `collect_vips`

**Problema:** Quando o servidor `10.220.50.9` estava inacessível (VPN desconectada), a exceção genérica `Exception` capturava o `ConnectTimeoutError` sem inserir alerta na UI.

**Solução:** Adicionado `except (requests.exceptions.ConnectTimeout, requests.exceptions.ConnectionError, requests.exceptions.Timeout)` explícito nos dois métodos, com inserção de alerta `CRITICAL` no frontend e `break` para não retentativa (servidor inacessível = inútil tentar novamente até o próximo ciclo).

---

### 1.4 Reescrita completa de `_renew_session` — backoff + logs úteis

**Problema (três bugs):**

1. **Código morto:** `sess_data_before = self._load_session_data()` era capturado mas nunca comparado — se a thread de KPI e a de trace expirarem juntas, ambas rodavam o Playwright sequencialmente em vez de a segunda detectar que a primeira já renovou.

2. **Log invisível:** O `get_session.py` escreve erros no `stdout` (não `stderr`). O código só logava `result.stderr`, então na falha o log mostrava `"Playwright falhou ao renovar sessão: "` (string vazia).

3. **Sem backoff:** Cada falha do Playwright travava a thread por 120s. Com 3 VIPs, podia travar 360s por ciclo. 60s depois, reiniciava — loop infinito que paralisa a aplicação.

**Solução:**

```
Backoff exponencial por módulo (class-level, persiste entre instâncias):
  Falha #1 → aguarda  60s antes de tentar novamente
  Falha #2 → aguarda 120s
  Falha #3 → aguarda 180s
  Falha #4+ → aguarda 300s (máximo)
  Sucesso → zera contador e remove backoff

Detecção de renovação concorrente:
  Captura roarand ANTES de adquirir o lock.
  Após adquirir o lock, compara com o roarand atual.
  Se mudou → outra thread já renovou → apenas recarrega sessão local, sem Playwright.

Log completo:
  Loga stdout E stderr do subprocesso.
  Diferencia TimeoutExpired (120s) de outros erros.
  Inclui número da tentativa e tempo do próximo retry.
```

**Variáveis de classe adicionadas:**
```python
HttpCollector._renew_failures: dict      # módulo → nº de falhas consecutivas
HttpCollector._renew_backoff_until: dict # módulo → datetime do próximo retry
```

---

## 2. `core/database.py`

### 2.1 Migração de `vip_measurements` para banco global

**Motivação:** As medições de VIP estavam no banco específico de cada evento (`smart_events_{id}.db`). Ao trocar de evento, o novo evento não tinha histórico de VIPs — causando "divergência" (VIP aparecia como desconhecido mesmo estando sendo monitorado ativamente).

**Decisão de design:** VIPs são monitorados globalmente (independente do evento ativo). A coluna `event_id` no registro indica qual evento estava ativo no momento da coleta — usada no modo histórico para filtrar o período correto e no cálculo de `in_event`.

**Mudanças no schema:**

| Tabela | Antes | Depois |
|--------|-------|--------|
| `vip_measurements` | Por banco de evento (`smart_events_{id}.db`) | Banco global (`smart_events.db`) |
| `UNIQUE(vip_name, timestamp)` | Não existia | Adicionado — deduplicação entre ciclos |
| `vip_measurements` em `init_event_db` | Criada | Removida — novos eventos não criam a tabela |

**Deduplicação na migração do schema:** O `CREATE UNIQUE INDEX` falharia se já houvesse duplicatas. Adicionado `DELETE WHERE id NOT IN (SELECT MAX(id) GROUP BY vip_name, timestamp)` antes de criar o índice.

---

### 2.2 `insert_vip_batch` — escrita no banco global

```python
# Antes:
event_id = measurements[0]["event_id"]
conn = get_event_conn(event_id)
conn.executemany("INSERT INTO vip_measurements ...", measurements)

# Depois:
conn = get_conn()  # banco global
conn.executemany("INSERT OR IGNORE INTO vip_measurements ...", measurements)
#                 ↑ ignora duplicatas pelo UNIQUE index
```

---

### 2.3 `get_vip_latest` — leitura global, sem filtro de evento

```python
# Antes: filtrava por event_id no banco do evento
# Depois: banco global, sem filtro de evento
# - Live (max_timestamp=None): medição mais recente de cada VIP globalmente
# - Histórico (max_timestamp set): mais recente até aquele instante (globalmente)
```

Assinatura alterada: `get_vip_latest(event_id, max_timestamp)` → `get_vip_latest(max_timestamp=None)`.

---

### 2.4 `get_vip_series` — leitura global + fallback temporal

```python
# Antes: banco do evento, filtro WHERE event_id = ?
# Depois: banco global, sem filtro de evento
# Fallback: se janela de 60 min retornar vazio → retorna últimos 120 registros
#           (cobre o caso de timezone incorreto ou poucos dados)
```

---

### 2.5 `get_event_timestamps` — VIP timestamps do banco global

```python
# Antes: UNION de kpi_measurements e vip_measurements no mesmo banco de evento
# Depois:
#   kpi_measurements → banco específico do evento (sem mudança)
#   vip_measurements → banco global filtrado por event_id
```

---

### 2.6 `clear_event_history` — não apaga VIP measurements

```python
# Antes: apagava kpi_measurements, vip_measurements e alerts do banco do evento
# Depois: apaga kpi_measurements e alerts. VIP measurements são globais e persistentes.
```

---

### 2.7 `migrate_event_vip_measurements` — migração automática de dados legados

**Nova função** chamada automaticamente por `api.get_vips()` ao acessar qualquer evento.

```
- Executa apenas uma vez por evento por sessão Python (_migrated_events set)
- Lê vip_measurements do banco específico do evento (pode não existir → trata silenciosamente)
- Copia para banco global com INSERT OR IGNORE (deduplicação pelo UNIQUE index)
- Loga quantos registros foram efetivamente inseridos (count before/after)
```

---

## 3. `api/api.py`

### 3.1 `get_vips` — migração automática + `in_event` dinâmico

**Mudanças:**

```python
# 1. Aciona migração de dados legados (roda uma vez por evento por sessão)
db.migrate_event_vip_measurements(event_id)

# 2. Leitura global sem event_id
latest = {r["vip_name"]: r for r in db.get_vip_latest(timestamp)}
#                                                       ↑ sem event_id

# 3. in_event calculado dinamicamente contra os sites do evento atual
# Antes: in_event = bool(row["in_event"])   ← valor gravado na coleta (evento errado ao trocar)
# Depois:
serving_site = resolve_site_id(serving)
in_event = bool(serving_site)              # sempre correto para o evento visualizado
```

**Por que `in_event` dinâmico:** O valor gravado no banco reflete o evento que estava ativo **quando a medição foi coletada**. Ao trocar para um evento diferente, esse valor seria errado. Calculando em tempo real a partir de `resolve_site_id` (que usa os sites do evento em contexto), o `in_event` é sempre preciso independente do evento visualizado.

---

## 4. `frontend/js/vip.js`

### 4.1 Gráfico dual-axis no modal de VIP

**Antes:** Gráfico com único dataset RSRP.

**Depois:** Dois datasets com dois eixos Y independentes:

| Dataset | Eixo | Cor | Unidade |
|---------|------|-----|---------|
| RSRP | Y esquerdo (`yRsrp`) | Cor do status do VIP | dBm |
| RSRQ | Y direito (`yRsrq`) | `#58A6FF` (azul) | dB |

- Linhas de threshold (warning/critical) ancoradas no eixo `yRsrp` via `yScaleID`
- Tooltip unificado (`interaction.mode: "index"`) com unidades corretas por dataset
- Legenda visível para diferenciar os dois datasets

---

## 5. `frontend/index.html`

### 5.1 Modal de detalhes do VIP redimensionado

| Propriedade | Antes | Depois |
|-------------|-------|--------|
| `modal-box` width | `420px` | `660px` |
| `vip-modal-chart-wrapper` height | `140px` | `200px` |

---

## 6. `planos/plano-coleta-vips.md` (criado)

Documento de análise e planejamento das mudanças acima, incluindo:
- Diagnóstico dos três problemas identificados (dados desatualizados, "sem medições", gráfico parcial)
- Causa raiz de cada problema com trechos de código
- Detalhamento de cada mudança com pseudocódigo e justificativa
- Tabela de critérios de sucesso
- Pontos de atenção e riscos (verificação do `rowNo` no `filter-by-cols`, timezone)
