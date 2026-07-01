# Processo de Coleta de Alarmes (Current Alarms) — SmartEvents

> **Nota (2026-06-30):** documento canônico das descobertas que viabilizaram a captura de alarmes
> do iMaster MAE filtrados por tipo. Base de código: [`imaster_alarms.py`](../imaster_alarms.py)
> (coletor standalone validado ao vivo) e o plano de integração ao app em
> [`plano-integracao-alarmes.md`](../plano-integracao-alarmes.md). Especificação original de negócio em
> [`implementacao-filtro-alarmes.md`](../implementacao-filtro-alarmes.md). Evidências brutas: os HAR e
> relatórios em [`alarms/`](../alarms/). Mudanças posteriores relevantes ficam em `.claude/MEMORY.md`.

Este documento descreve **como** o SmartEvents captura, em texto puro, apenas os tipos de alarme
escolhidos (ex.: "RF Unit VSWR Threshold Crossed" e "Cell Unavailable"), reproduzindo o que a tela
"Current Alarms" do iMaster faz quando um filtro é aplicado manualmente. O foco é nas **descobertas
técnicas** (validadas ao vivo na VPN em 2026-06-30) que tornaram a coleta possível.

---

## 1. O princípio central: o filtro é uma "visão" no servidor, não um filtro na listagem

O iMaster **não filtra os dados na hora de listar**. Ele filtra na hora de **criar uma visão** no
servidor e só depois pagina o conteúdo dessa visão. Essa visão é identificada por um **`modelID`**
(o "número do ingresso"): quem tem o modelID certo lê exatamente aquele conjunto pré-reservado de
alarmes. Não dá para filtrar "depois de sentado" — a reserva já aconteceu na criação do modelID.

Consequência prática: capturar alarmes filtrados é um fluxo de **dois passos** — criar o modelID com o
filtro, depois paginar por ele.

---

## 2. Os comandos (endpoint único `POST /rest/fmwebsite/v1/commands`)

O que muda entre as chamadas é o campo `cmd` no corpo JSON.

| `cmd` | Papel | Uso na automação |
|---|---|---|
| **1102** | **Cria o filtro/visão.** Recebe a `condition` (string JSON) com os pares `{alarmId, alarmGroupId}` e devolve um `modelID` novo. | Obrigatório. |
| **1103** | **Leitura paginada.** Recebe o `modelID` do 1102 e devolve os dados em janelas (`from`/`to`) + resumo por severidade. Não tem lógica de filtro. | Obrigatório (em loop). |
| **2909** | Traduz pares `{alarmId, alarmGroupId}` em nomes legíveis de grupo (BTS5900, GBTS…). | **Ignorado** — o `alarmName` já vem no 1103. |

---

## 3. Por que o filtro exige **pares** `{alarmId, alarmGroupId}`, não só `alarmId`

O campo `condition.alarmGroupId` do 1102 **não aceita uma lista simples de IDs de alarme**. Ele exige
pares `{alarmId, alarmGroupId}`. O motivo: o mesmo alarme (ex.: "RF Unit VSWR Threshold Crossed",
`alarmId` 26529) existe cadastrado em **várias famílias de equipamento**, uma por `alarmGroupId`
(BTS5900, BTS3900, GBTS, BTS5900 5G…), porque o mesmo problema pode ocorrer em hardwares diferentes.
O mesmo **nome** pode até ter `alarmId` diferente conforme o grupo.

Para filtrar por um alarme é preciso, então, o conjunto de pares `{alarmId, alarmGroupId}` em que
aquele alarme existe. Pares que não existem de fato simplesmente **não retornam nada** (sem erro).

### 3.1. Fonte dos pares: o catálogo exportado do próprio sistema

O operador exporta da tela "Alarm Name" um CSV com as colunas
`Alarm Group ID, Alarm Group Name, Alarm ID, Alarm Name, Alarm Severity`
(salvo no repositório como [`alarms/catalogo-alarmes.csv`](../alarms/catalogo-alarmes.csv):
~7 mil linhas, **2308 nomes distintos, 23 grupos**). Ele dá, para cada nome, **exatamente** quais
pares `{alarmId, alarmGroupId}` existem na rede.

> **Descoberta:** este catálogo rico **substitui** a estratégia inicial de "produto cartesiano por
> chute" (cruzar o alarme com uma lista fixa de ~10 grupos vistos nos HAR), que era incompleta —
> faltavam grupos como BSC6910UMTS, MICRO BTS3900, os Pools e ECNS. Ex.: o VSWR tem **11 pares reais**
> no catálogo (o HAR só mostrava 10). `imaster_alarms.py` monta os pares exatos por nome.

---

## 4. Formato da `condition` do 1102 (validado)

Enviada como **string JSON** dentro de `parameters.condition`. Estrutura fixa da tela "Current Alarms"
+ a lista de pares:

```json
{
  "alarmLevel": ["CRITICAL","MAJOR","MINOR","WARNING"],
  "alarmStatus": [12,10,11,13],
  "eventType": {"value": ["1","2", "...", "16"], "operation": "in"},
  "specialAlarmStatus": {"value": ["0"], "operation": "in"},
  "alarmGroupId": {"operation": "in", "value": [
     {"alarmId": "26529", "alarmGroupId": "268390521"},
     {"alarmId": "26529", "alarmGroupId": "125"}
     /* ... um par por (alarmId × grupo real do catálogo) ... */
  ]},
  "orders": [{"field": "ColArriveUtc", "order": 1}]
}
```

Sem a chave `alarmGroupId` (lista de pares), a busca vira "todos os alarmes ativos" (tela sem filtro).
O `parameters.additionalCondition` é uma segunda string JSON constante (som/severidade), igual nos HAR
com e sem filtro.

---

## 5. Descobertas da validação ao vivo (VPN, 2026-06-30)

Estas são as descobertas que fizeram a captura **funcionar de fato** (teste ponta a ponta: 697 alarmes,
só os 2 tipos pedidos, 0 `csn` duplicado):

1. **Autenticação mínima:** a coleta precisa de **apenas dois valores** — o cookie **`bspsession`** e o
   header **`roarand`**. Não é necessário `roarand` como cookie nem `bspSessionId` real. (O cURL de
   DevTools do `cmd 8100` sequer traz `bspSessionId` e funciona.)
2. **`bspSessionId` pode ir vazio (`""`):** o servidor apenas o **ecoa no sufixo `@` do `modelID`**, que
   é criado e consumido na mesma execução. Não precisa de um id "real".
3. **Caminho do `modelID` na resposta do 1102:** fica em `parameters.modelID`. Resposta típica:
   `{"parameters":{"jobId":"…","modelID":"000.…@","bspSessionId":""},"status":1}`.
4. **Mesmo host de KPI/Trace:** `fmwebsite`, `pm/oss` e `fars` estão no mesmo host
   (ex.: `https://10.220.50.9:31943`). O `core/session_renew.py` já grava o mesmo `bspsession` +
   `roarand` nos módulos `monitoring`/`trace` do `session.json`. **→ Na integração ao app, a coleta de
   alarmes reaproveita a sessão existente, sem tocar no `session_renew.py`.**
5. **Lista "viva":** a tela original tem `autoRefresh` ligado, então a lista muda enquanto se pagina.
   Mitigação (aplicada): pedir as páginas com **`autoRefresh: false`** (foto mais estável) e
   **deduplicar por `csn`** (identificador único de cada ocorrência) ao final.

---

## 6. Fluxo completo (do filtro à planilha/gravação)

```
nomes escolhidos  ──►  resolve_pairs (catalogo-alarmes.csv)  ──►  pares {alarmId, alarmGroupId}
                                                                         │
                                          build_condition (string JSON)  │
                                                                         ▼
                          cmd 1102 (condition, bspSessionId="")  ──►  modelID  (parameters.modelID)
                                                                         │
                          loop cmd 1103 (modelID, from/to, autoRefresh=false)  ──►  linhas
                                                                         │
                                       flatten (csn, alarmId, nome, severidade, source, tempos…)
                                                                         │
                                    dedup por csn  ──►  CSV/Excel (standalone)  ou  tabela `alarms` (app)
```

No standalone `imaster_alarms.py` os blocos que implementam isso são: `load_catalog`, `resolve_pairs`,
`build_condition`, `create_model` (1102, com `_extract_model_id`), `fetch_page`/`collect_all` (1103),
`flatten`, `pull_alarms` (ponto de entrada) e `harvest_groups` (utilitário para listar os grupos reais
da rede sem filtro).

---

## 7. Sessão expirada (como o coletor avisa)

A sessão depende de valores voláteis (`bspsession`, `roarand`). Sintomas de expiração e como são
tratados (em `_check_session_alive`, chamado **antes** de `raise_for_status`):

- **HTTP 401/403** → token recusado.
- **Redirecionamento para o login SSO** (`unisso` / `login.action`) — a página de login pode voltar
  **HTTP 200 com HTML longo**, então a detecção confiável é inspecionar a **URL final** da resposta
  (convenção do projeto, ver `.claude/CLAUDE.md`), não palavras-chave no corpo.

Nos dois casos o standalone para com mensagem clara pedindo para atualizar os tokens (colar do
DevTools). Dentro do app, a renovação é automática via `session_renew.py`.

---

## 8. Referências

- Código: [`imaster_alarms.py`](../imaster_alarms.py) — coletor standalone (fonte da verdade do fluxo).
- Plano de integração ao app desktop: [`plano-integracao-alarmes.md`](../plano-integracao-alarmes.md).
- Especificação de negócio original: [`implementacao-filtro-alarmes.md`](../implementacao-filtro-alarmes.md).
- Catálogo de pares: [`alarms/catalogo-alarmes.csv`](../alarms/catalogo-alarmes.csv) (re-exportar do
  sistema e sobrescrever quando a base de alarmes mudar).
- Evidências brutas (HAR + relatórios com/sem filtro): [`alarms/`](../alarms/).
- Registro de decisões e datas: `.claude/MEMORY.md` (sessão 2026-06-30).
