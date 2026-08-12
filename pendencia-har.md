# Pendência: capturas de HAR necessárias

Documento de contexto para as duas capturas de Network que ainda faltam. Escrito para ser
autossuficiente: quem for analisar o `.har` novo não precisa reconstruir o histórico.

**Data:** 2026-08-12
**Estado atual:** Fase 3 (VIPs) concluída e validada ao vivo. Duas pendências dependem
exclusivamente de captura de navegador.

---

## 0. O que já está resolvido (não recapturar)

O fluxo de VIP está fechado e validado contra a task 2072 (Baldin) e confirmado pelo
usuário na task do Douglas. Sequência por ciclo, toda em `/rest/oss/access/fars/v1`:

1. `GET traceresult/pre-check?taskId=<id>&queryType=0` → valida a task (`checkState: true`)
2. `GET traceresult/query/result?taskId=<id>&msgId=1&startRow=0&pageSize=100` → **aloca um
   `msgId` novo** e devolve `recordCount` do conjunto completo
3. `GET traceresult/query/fetch-field-values?taskId=<id>&msgId=<alocado>` → `startTime`/`endTime`
4. `POST traceresult/query/filter-by-cols` → filtra `Message Type = RRC_MEAS_RPRT` no servidor,
   pagina por `pageDto.startRow`/`pageSize`

Três armadilhas já mapeadas, documentadas em `ERRORS.md`, que **não** precisam ser
reinvestigadas:

- O `msgId` é um handle descartável. Cada `query/result` com `msgId=1` aloca um novo
  (7708005 → 7709005 → 7711005 → 7712005 → 7713005). Precisa ser criado e consumido no mesmo
  ciclo; reaproveitar entre ciclos faz o FARS responder sem linhas.
- `startTime`/`endTime` vazios em `filter-by-cols` → HTTP 500
  `framwork.remote.SystemError`. É obrigatório passar pelo `fetch-field-values`, mesmo com
  `hasStartTime`/`hasEndTime` em `false`.
- O FARS não zero-preenche os milissegundos: `"(98)"` são 98 ms, não 980.

RSRP/RSRQ da serving cell LTE são decodificados localmente em `core/rrc_decode.py`
(ASN.1 UPER, `UL-DCCH-Message` / `MeasurementReport`, TS 36.331), com 1 byte de cabeçalho
proprietário antes do PDU. Cobertura: 386/386 na captura, 616/616 ao vivo.

---

## Captura A — contrato do `msg-explain-info`

### Por que

O trace do VIP é majoritariamente 5G e nós só consumimos a fatia LTE. Composição real da
task 2072, amostra de 1300 linhas das páginas iniciais:

```
NR    1200 linhas — 15 tipos distintos, todos PERIOD_PRIVATE_*
LTE    100 linhas — 1 tipo: RRC_MEAS_RPRT   ← o único que coletamos hoje

  PERIOD_PRIVATE_RADIO_UTILIZATION_MR         222
  PERIOD_PRIVATE_UE_CMAC_DELAY / DRB_CMAC_DELAY  154
  PERIOD_PRIVATE_UE_MEASUREMENT                76
  PERIOD_PRIVATE_DRB_RLC_DELAY                 76
  PERIOD_PRIVATE_DRB_SDAPPDCP_DELAY            74
  PERIOD_UE_PACKET_MEASURE_PDCP                74
  PERIOD_PRIVATE_UE_THROUGHPUT_MEASUREMENT     ...
```

Essas mensagens NR são **proprietárias da Huawei**, não ASN.1 padrão 3GPP. Não existe
especificação pública contra a qual escrever um decodificador local — só o servidor tem o
dissector. O `msg-explain-info` é a única porta de entrada para elas.

O que ele destrava, em ordem de valor: experiência 5G do VIP (RSRP/RSRQ/SINR NR),
throughput por UE, e células vizinhas (nosso decoder para em `measResultPCell`).

### O bloqueio exato

O parâmetro `rowNo` tem semântica desconhecida. A implementação anterior assumiu que era a
posição da linha dentro do lote e decodificava a mensagem errada — silenciosamente, porque a
falha caía em `logger.debug`. **Chutar isso grava RSRP de outro instante no banco.** Por isso
a captura é pré-requisito, não conveniência.

### Como capturar

1. Abrir o FARS, task **2072** (Baldin) ou a do Douglas — qualquer uma serve, desde que seja
   a mesma task do começo ao fim da captura.
2. Abrir o DevTools → Network, **marcar "Preserve log"**, e limpar.
3. Aplicar o filtro `Message Type = RRC_MEAS_RPRT` na tela (é o que dispara o
   `filter-by-cols` e dá o mesmo conjunto que o coletor vê).
4. **Clicar numa linha da tabela para abrir o painel de decode.** Anotar qual linha:
   posição na página (1ª, 5ª, 17ª…) e o `serialNo` que aparece na coluna.
5. Repetir o clique em **mais duas linhas de posições bem distintas** — por exemplo a 1ª, a
   ~50ª e a última da página. É a comparação entre elas que revela a semântica do `rowNo`.
6. **Clicar também numa linha NR** (`PERIOD_PRIVATE_UE_MEASUREMENT` ou
   `PERIOD_PRIVATE_UE_THROUGHPUT_MEASUREMENT`), removendo o filtro se necessário.
7. Salvar como HAR com conteúdo das respostas.

> Se a interface tiver paginação, faça pelo menos um clique numa linha da **segunda** página.
> Isso desempata entre "índice na página" e "índice no resultado inteiro" — que é a diferença
> mais provável e a mais perigosa.

### O que preciso ver no HAR

| Requisição | Para quê |
|---|---|
| `query/result` (aloca `msgId`) | correlacionar o `msgId` de todos os passos seguintes |
| `query/fetch-field-values` | confirmar que a janela ainda é o passo anterior obrigatório |
| `POST query/filter-by-cols` | ter o `tableData` com os `serialNo` na ordem exibida |
| `query/msg-explain-info` × 4 | **o alvo** — parâmetros e resposta completa |

### O que vou extrair

- **`rowNo` vs `serialNo` vs posição**: com 3 cliques em posições distintas, o mapeamento
  fica determinado. Vou verificar se `rowNo` é (a) o `serialNo` da mensagem, (b) o índice
  1-based na página filtrada, (c) o índice no resultado sem filtro, ou (d) outra coisa.
- **Os outros parâmetros**: hoje mandamos `tabularFlag=y`, `isSubscribe=false`,
  `isSecondDecode=false`, `isPlayback=false` — herdados sem confirmação. Preciso ver os
  valores reais que o navegador envia.
- **Formato da resposta**: o código antigo esperava nós
  `{"name":"rsrpResult","val":": ---- 0x35(53) ---- *0110101"}`. Preciso confirmar a chave
  (`val` ou `value`), o aninhamento (`children`) e onde ficam os campos de célula vizinha.
- **Se a resposta depende do filtro ativo**: se `rowNo` for índice da página filtrada, o
  coletor precisa manter filtro e decode consistentes no mesmo `msgId`.
- **Estrutura de uma mensagem NR**: quais campos o dissector Huawei expõe para
  `PERIOD_PRIVATE_UE_MEASUREMENT` — é o que decide se dá para extrair RSRP/SINR 5G do VIP.

### Validação cruzada (peço junto com o HAR)

Para uma das linhas LTE clicadas, anotar o que a **tela** mostra: `serialNo`, timestamp
completo com milissegundos, e os valores de `rsrpResult`/`rsrqResult`. Isso me deixa
conferir o decodificador local contra o do servidor na mesma mensagem — é o item 3 da
validação com VPN da seção 7 do plano, ainda em aberto.

---

## Captura B — task PM 5G (Monitoring / KPIs)

### Por que

É a **única lacuna funcional** que resta; todo o resto das pendências é processo. Estado
atual do evento `testesantoamaro`:

```
integration: { "pm_task_id": 747 }          ← uma task só, 4G

kpi_measurements: 40.456 linhas, 11 métricas, escopo CELL + SITE
  technology 4G : 40.456
  technology 5G :      0
```

As fórmulas 5G estão escritas em `core/kpi_formulas.py` e testadas por fixture, mas nunca
viram um contador real. O código **já aceita** múltiplas tasks — `integration.pm_tasks` como
lista de `{task_id, tech}`, com `pm_task_id` mantido como fallback 4G
(`HttpCollector._configured_pm_tasks`). Falta só descobrir o `task_id` e provar os nomes dos
contadores. Nenhuma mudança de arquitetura está pendente aqui.

### Como capturar

1. No iManager, abrir o **Monitoring** (mesma área do PM que serve a task 747).
2. DevTools → Network, "Preserve log", limpar.
3. Localizar/criar a task de monitoração que cobre as **células 5G** dos sites do evento.
   Pela seção "(DU CELL)" das fórmulas, a maioria dos contadores 5G vive em objetos do tipo
   **DU Cell**, não NR Cell — se houver escolha de tipo de objeto, é esse.
4. Deixar a tela atualizar por **pelo menos 2 ciclos** de resultado, para eu ver o
   `preExecTime` → `execTime` avançar.
5. Salvar como HAR com conteúdo das respostas.

### O que preciso ver no HAR

| Requisição | Para quê |
|---|---|
| listagem de tasks PM (seja qual for a rota) | descobrir o `task_id` 5G e como enumerá-las |
| `POST /rest/oss/access/pm/v1/monitor/task/result` × 2+ | **o alvo** — payload e resposta |

### O que vou extrair

- **O `task_id` 5G** — vai direto para `integration.pm_tasks` do evento.
- **Nomes exatos dos 33 contadores** das fórmulas 5G, conferidos um a um contra o
  `counterRes` da resposta:

  ```
  N.RRC.SetupReq.Succ/.Att        N.RRC.ResumeReq.Succ/.Att
  N.NGSig.ConnEst.Succ/.Att       N.QosFlow.Est.Succ/.Att
  N.QosFlow.Est.Att.EPSFB         N.QosFlow.Est.Att.EmcFB
  N.QosFlow.FailEst.Conflict      N.QosFlow.FailEst.AMF.SyntaxError
  N.QosFlow.Resume.Succ/.Att      N.QosFlow.AbnormRel  N.QosFlow.NormRel
  N.QosFlow.RrcInactiveToIdle.Rel N.QosFlow.RrcConnToInactive.Suspend
  N.PRB.DL.Used.Avg/.Avail.Avg    N.PRB.UL.Used.Avg/.Avail.Avg
  N.ThpVol.DL  N.ThpVol.DL.LastSlot  N.ThpTime.DL.RmvLastSlot
  N.ThpVol.UL  N.ThpVol.UE.UL.SmallPkt  N.ThpTime.UE.UL.RmvSmallPkt
  N.NSA.ThpVol.DL  N.NSA.ThpVol.UL
  N.User.RRCConn.Avg  N.Cell.Avail.Dur  N.UL.NI.Avg
  ```

  Contador ausente vira medição inválida com motivo, nunca zero. Preciso saber quais dos 33
  realmente existem antes de liberar cada KPI.
- **Unidades de throughput e volume 5G** — é um gate declarado do plano. As fórmulas 5G não
  declaram unidade e **não se pode aplicar o fator de conversão do 4G por analogia**. Vou
  comparar o valor bruto do `counterRes` com o que a tela do iManager mostra para o mesmo
  `execTime`, e derivar a conversão daí.
- **Mapeamento objeto → célula**: `objNo` e o nome do objeto, para saber se a normalização de
  nome/site/tecnologia atual funciona nas células 5G ou precisa de regra própria.
- **`period` da resposta**: alimenta o `{SP}` da fórmula de Availability. Precisa vir do
  servidor, não do intervalo local do agendador.
- **Avanço de cursor**: com 2+ ciclos, confirmo que `execTime`/`objNoExecTimes` do primeiro
  aparecem como `preExecTime` do segundo.

### Validação cruzada (peço junto com o HAR)

Print da tela do Monitoring para **uma célula 5G**, com o horário visível e os valores de
Throughput DL, Volume DL e PRB Utility. Sem isso não dá para fechar o gate de unidade — e
sem o gate, Throughput e Volume 5G ficam calculados mas não liberados para produção.

---

## Como me entregar

- Um HAR por captura, nomes sugeridos: `har-msg-explain.har` e `har-pm-5g.har`, na raiz do projeto.
- **Com conteúdo das respostas.** No Chrome/Edge, "Save all as HAR with content" — o HAR sem
  corpo não serve para nada aqui.
- Junto: as anotações de tela pedidas em cada seção (posições/serialNo clicados na A, print
  da célula 5G na B).

### Sobre dados sensíveis

O HAR **contém cookie `bspsession` e header `roarand`** — são credenciais de sessão vivas.
O `har-atualizado-filtrado.har` atual está na raiz e **não está commitado**; mantenha assim.
Fixtures derivadas vão sanitizadas para `tests/fixtures/`, sem cookies, tokens ou IMSI —
foi o que fiz com `vip_filter_by_cols.json` e `rrc_meas_report_vectors.json`.

---

## Prioridade sugerida

**B antes de A.** A captura B destrava a metade 5G da Fase 2 e muda o que o operador vê no
painel hoje. A captura A destrava capacidade nova (5G do VIP, throughput, vizinhas), que é
valiosa mas não é regressão — a coleta LTE de VIP está funcionando e validada.

Nenhuma das duas bloqueia a Fase 4, que pode começar em paralelo com o que já funciona.
