# Análise da Captura A — `msg-explain-info` e o contrato do `rowNo`

**Fonte:** `har-atualizado-filtrado-ordenado-completo.har` (13,1 MB, 70 entradas), task **2072**
(VIP Baldin), capturado em 12/08/2026 14:03:29 → 14:05:00.
**Objetivo:** resolver a semântica do `rowNo` antes de liberar o `msg-explain-info` para produção.

**Veredito: `rowNo` resolvido, sem ambiguidade, com 4 confirmações independentes.**
A captura respondeu todas as perguntas da seção "Captura A" da `pendencia-har.md` — exceto a
estrutura de uma mensagem NR proprietária, que se revelou desnecessária (ver §6).

---

## 1. A sequência capturada

O usuário confirmou o roteiro: acessar o trace → filtrar a coluna → ordenar duas vezes
(crescente, depois decrescente) → clicar em linhas distintas. O HAR bate exatamente com isso.

| # | Requisição | `msgId` entra | `msgId` sai | Resultado |
|---|---|---|---|---|
| 3 | `GET pre-check?taskId=2072&queryType=0` | — | — | `{"checkState":true,"checkResult":[]}` |
| 7 | `GET query/result` `msgId=1&pageSize=1000` | 1 | **7861005** | `recordCount: 459072` |
| 15 | `GET query/fetch-field-values` | 7861005 | — | janela + `filterMap` (80 Message Types) |
| 21 | `POST query/filter-by-cols` `RRC_MEAS_RPRT` | 7861005 | **7866005** | `recordCount: 619` |
| 29 | `POST query/sort` `Time` `isAscend:true` | 7866005 | **7867005** | 619 linhas, ascendente |
| 32 | `POST query/sort` `Time` `isAscend:false` | 7867005 | **7868005** | 619 linhas, descendente |
| 37/42/49/62 | `GET query/msg-explain-info` | 7868005 | — | `rowNo` = 1, 36, 618, 500 |

Entre os cliques, o navegador chamou `GET query/result-paging` com `startRow` = 588, 558, 546,
504, 492 — é a rolagem da tabela. **Esse detalhe é o que torna a prova possível.**

### Descoberta estrutural: todo passo que muda o conjunto aloca um `msgId` novo

A `MEMORY.md` já registra que `query/result` com `msgId=1` aloca um handle. A captura mostra que
a regra é mais ampla: **`filter-by-cols` e `sort` também alocam**, devolvendo o novo handle em
`data.msgId`. A cadeia `7861005 → 7866005 → 7867005 → 7868005` é uma sequência de snapshots
imutáveis, cada um com sua própria ordenação.

Isso reposiciona o `msgId` no modelo mental: não é "a sessão de consulta", é **"este conjunto de
resultados, nesta ordem"**. É a chave para entender o `rowNo`.

---

## 2. `rowNo` — a resposta

> **`rowNo` é o índice 1-based da linha dentro do conjunto vinculado ao `msgId` enviado,
> considerando a ordenação daquele `msgId`. Não é relativo à página. Não é o `serialNo`.**

### Como foi provado

A resposta do `msg-explain-info` traz os **bytes brutos da mensagem** em `tableData` (lista de
chunks de hex). Isso permite casar cada resposta com uma linha específica por comparação exata de
bytes, sem depender de nenhuma suposição. Cruzei as 4 respostas contra os três conjuntos:

| `rowNo` | bytes | posição no `7866005` (filtrado) | no `7867005` (asc) | no **`7868005` (desc, o enviado)** | `serialNo` | Time |
|---|---|---|---|---|---|---|
| **1** | `02 08 32 A5 56 …` | 619 | 619 | **1** ✅ | 458303 | 2026-08-12 07:21:44 (460) |
| **36** | `02 08 37 96 3C …` | 534 | 584 | **36** ✅ | 422121 | 2026-08-11 22:32:04 (241) |
| **618** | `02 08 33 B2 84 …` | 28 | 2 | **618** ✅ | 64457 | 2026-08-11 12:28:34 (416) |
| **500** | `02 08 32 A3 6A …` | 118 | 120 | **500** ✅ | 148089 | 2026-08-11 15:20:05 (363) |

**4 de 4 casam com o índice 1-based no conjunto do `msgId` enviado.** Nenhum casa com o índice
nos outros dois conjuntos. Cada mensagem é única no conjunto — não houve match ambíguo.

### As duas hipóteses perigosas ficam eliminadas

1. **Não é índice de página.** O clique de `rowNo=618` veio logo após `result-paging startRow=588`;
   se fosse relativo à página, teria sido `30`. O de `rowNo=500` veio após `startRow=492`; seria `8`.
   Esta era "a diferença mais provável e a mais perigosa" prevista na `pendencia-har.md` — e está
   desempatada exatamente como o documento pediu.
2. **Não é o `serialNo`.** Os seriais são 458303, 422121, 64457 e 148089 — nenhuma relação com
   1, 36, 618, 500.

### A consequência operacional

O `rowNo` **só tem sentido acompanhado do `msgId` que produziu aquela ordenação**. Um `rowNo`
obtido de um conjunto filtrado e aplicado a um `msgId` de outra ordenação lê a mensagem errada —
silenciosamente, com bytes válidos e valores plausíveis. É precisamente o bug da Fase 3 registrado
no `ERRORS.md`, agora com a causa exata identificada: não era "índice do lote vs. índice da
página", era **índice num espaço de ordenação diferente do handle usado**.

---

## 3. Contrato do `msg-explain-info`

### Requisição — os parâmetros herdados estavam certos

```
GET /rest/oss/access/fars/v1/traceresult/query/msg-explain-info
    ?nocache=<epoch_ms>
    &taskId=2072
    &msgId=7868005          ← o handle que define a ordenação
    &rowNo=36               ← 1-based, global, naquele handle
    &tabularFlag=y
    &isSubscribe=false
    &isSecondDecode=false
    &isPlayback=false
```

`tabularFlag=y`, `isSubscribe=false`, `isSecondDecode=false`, `isPlayback=false` — **idênticos aos
que o coletor já envia.** A dúvida da `pendencia-har.md` sobre esses valores está encerrada:
estavam corretos. Headers: `roarand` (obrigatório) e `Accept: application/json, text/plain, */*`.

### Resposta

```jsonc
{
  "serialNo": null,              // sempre null — NÃO serve para correlacionar
  "binMsgExplain": { "filedTree": { … } },
  "textMsgExplain": null,
  "tabular": "…",                // render textual, redundante com filedTree
  "tableData": [["02","08","37",…], ["20"]],   // bytes brutos, em chunks
  "isSecondDecode": false,
  "traceTypeId": "GUL_Union_Trace",
  "signalType": "rrc"
}
```

Nó do `filedTree`:

```jsonc
{ "name": "rsrpResult",
  "val": ": ---- 0x16(22) ---- *0010110",   // chave é "val", não "value"
  "attrType": 1,                            // 1 = folha com valor; 3 = container
  "offset": 25, "len": 7,                   // deslocamento em BITS dentro do PDU
  "children": [] }
```

Confirmações contra o que a `pendencia-har.md` levantou:

- A chave é **`val`** (a suposição do código antigo estava certa).
- O formato `: ---- 0x16(22) ---- *0010110` se confirma; **o decimal entre parênteses é o valor**
  (regex `0x[0-9a-fA-F]+\((\d+)\)`).
- Aninhamento por **`children`**; distinguir folha de container por **`attrType`** (1 vs 3), não
  por `children` vazio.
- `serialNo` na raiz vem **`null`** — a única forma de correlacionar resposta com linha é
  `tableData` (bytes) ou a confiança no par `(msgId, rowNo)`.
- Células vizinhas ficam em `measResults/measResultNeighCells` (§6).

> **Atenção:** `tableData` é uma **lista de listas** de bytes hex. É preciso concatenar os chunks
> antes de comparar com o `messageBody` da linha (que vem como string única separada por espaço).

---

## 4. Validação cruzada — decodificador local × dissector do servidor

Este era o item 3 da validação com VPN da seção 7 do plano, em aberto desde a Fase 3. **Fechado.**

Comparei `core/rrc_decode.py` contra o `filedTree` do servidor nas 4 mensagens, nos índices crus
(antes de qualquer conversão para dBm/dB):

| `rowNo` | `measId` srv/local | `rsrpResult` srv/local | `rsrqResult` srv/local | Resultado |
|---|---|---|---|---|
| 1 | 6 / 6 | 37 / 37 | 21 / 21 | ✅ RSRP −103,0 dBm · RSRQ −9,0 dB |
| 36 | 16 / 16 | 22 / 22 | 15 / 15 | ✅ RSRP −118,0 dBm · RSRQ −12,0 dB |
| 618 | 8 / 8 | 50 / 50 | 33 / 33 | ✅ RSRP −90,0 dBm · RSRQ −3,0 dB |
| 500 | 6 / 6 | 35 / 35 | 26 / 26 | ✅ RSRP −105,0 dBm · RSRQ −6,5 dB |

**4/4 exatos.** O alinhamento de bits do decodificador local está correto, incluindo o bitmap de
opcionais — e a confirmação vale para mensagens de estruturas bem diferentes (duas com vizinhas
LTE, duas com vizinhas NR).

Além disso, rodei o decodificador sobre as **619 linhas** do conjunto filtrado:

```
decodificadas 619/619   falhas 0   header offset: {1: 619}
```

Cobertura total, e o cabeçalho proprietário é **sempre de 1 byte** — confirmado agora em três
capturas independentes (386 + 616 + 619). O fallback `_HEADER_OFFSETS = (1, 0, 2)` nunca precisou
sair do primeiro valor.

**Conclusão: o decode local não precisa do `msg-explain-info` para RSRP/RSRQ da serving cell.**
A decisão travada na `MEMORY.md` continua correta, agora com prova direta contra o servidor.

---

## 5. Ordenação — o conjunto filtrado vem embaralhado (confirmado e quantificado)

A `MEMORY.md` já dizia "a resposta de `filter-by-cols` não vem ordenada; ordenar no cliente".
A captura quantifica, sobre as 619 linhas:

| Conjunto | violações de ordem crescente | de decrescente |
|---|---|---|
| `filter-by-cols` (sem sort) | **127** | 436 |
| `sort` ASC `Time` | **0** | 559 |
| `sort` DESC `Time` | 559 | **0** |

O conjunto não ordenado tem saltos grandes e reais — ex.: `serialNo` 64898 (12:29:05) seguido de
64476 (12:28:36). Não é ruído de formatação: é desordem de verdade. **Ordenar no cliente não é
opcional.**

Duas notas finas:

- **`serialNo` é monotônico com o tempo, com empates.** No conjunto ASC o `serialNo` anda para
  trás 4 vezes, e verifiquei que **todas as 4 ocorrem em timestamps idênticos**. A afirmação da
  `MEMORY.md` se sustenta; o cursor por `serialNo` é válido. Só não se deve assumir ordem estrita
  entre mensagens do mesmo milissegundo.
- **Comparar `Time` como string é um bug latente.** Os milissegundos não são zero-preenchidos
  (`(98)` vs `(179)`), então `"(98)" > "(179)"` lexicograficamente. Toda ordenação por tempo tem
  que passar pelo parse com `zfill(3)` — a mesma armadilha do `ERRORS.md`, agora afetando
  *comparação* e não só *conversão*. Minha primeira checagem de monotonicidade caiu nela.

---

## 6. Descoberta não prevista: o 5G do VIP já está dentro das mensagens LTE que coletamos

Esta é a descoberta de maior valor da captura, e ela **reescreve a premissa da Captura A**.

A `pendencia-har.md` assumia que a experiência 5G do VIP só sairia das mensagens NR proprietárias
da Huawei (`PERIOD_PRIVATE_*`), decodificáveis apenas pelo servidor. **Não é o caso.** Duas das
quatro mensagens amostradas (`rowNo=1` e `rowNo=500`) são EN-DC/NSA e carregam a perna 5G dentro do
próprio `RRC_MEAS_RPRT` LTE — em campos **ASN.1 padrão 3GPP** (TS 36.331 rel-15), no mesmo
`messageBody` que já baixamos hoje:

```
measResultNeighCells/measResultNeighCellListNR-r15/MeasResultCellNR-r15/
    pci-r15, measResultCell-r15/rsrpResult-r15, measResultRS-IndexList-r15/…/ssb-Index-r15

measResultServFreqListNR-r15/MeasResultServFreqNR-r15/
    carrierFreq-r15
    measResultSCell-r15/pci-r15
    measResultSCell-r15/measResultCell-r15/{rsrpResult-r15, rsrqResult-r15, rs-sinr-Result-r15}
    measResultSCell-r15/measResultRS-IndexList-r15/MeasResultSSB-Index-r15/…
```

Valores crus observados (`rowNo=500`): `rsrpResult-r15=77`, `rsrqResult-r15=65`,
`rs-sinr-Result-r15=107`, `carrierFreq-r15=634080`.

**Corroboração independente:** NR-ARFCN 634080 → 3000 + (634080 − 600000) × 0,015 = **3511,2 MHz**,
banda **n78** — exatamente o 5G de 3,5 GHz brasileiro. É um dado real, não um artefato de
alinhamento.

As duas mensagens LTE-puras (`rowNo=36` e `618`) trazem, no lugar, agregação LTE:
`measResultServFreqList-r10` com `rsrpResultSCell-r10`/`rsrqResultSCell-r10` (2 SCells), mais
vizinha LTE em `measResultListEUTRA` (`physCellId`, `rsrpResult`).

### O que isso significa para o plano

**A prioridade "B antes de A" continua correta, mas a Captura A deixou de ser pré-requisito para o
5G do VIP.** O caminho passa a ser estender `core/rrc_decode.py` para continuar o parse além de
`measResultPCell` — não chamar `msg-explain-info` por mensagem. Vantagens: nenhuma requisição
extra por mensagem, nenhuma dependência do par `(msgId, rowNo)`, e o mesmo custo de ciclo atual.

Ressalvas honestas, para não repetir o erro de chutar contrato:

- As conversões NR (TS 38.133 — RSRP = idx − 156 dBm; RSRQ = (idx − 87)/2 dB;
  SINR = (idx − 46)/2 dB) produzem valores plausíveis (`rowNo=500` → −79 dBm / −11 dB / +30,5 dB;
  `rowNo=1` → −107 dBm / −13,5 dB / +1 dB), mas **ainda não foram conferidas contra a tela**.
  Precisam do mesmo tratamento que o RSRP LTE recebeu antes de virar dado gravado.
- **Não sei quantas das 619 linhas têm perna NR.** O comprimento do `messageBody` é só um indício
  (394 linhas ≤ 20 bytes, 225 > 20 bytes; as duas com NR tinham 34 e 37 bytes, as LTE-puras 17).
  Isso **não** é uma medição — só sai do parse completo. Não usar esse número como estimativa.
- Estender o decoder exige tratar `measResultNeighCells` como CHOICE e os campos rel-10/rel-15 como
  extensões — mais delicado que o parse atual, que para antes de tudo isso.

---

## 7. O que a captura **não** respondeu

**A estrutura de uma mensagem NR proprietária.** Os 4 cliques têm `signalType: "rrc"` — todos são
`RRC_MEAS_RPRT` LTE, porque o filtro estava ativo o tempo todo. O passo 6 do roteiro da
`pendencia-har.md` ("clicar também numa linha NR, removendo o filtro se necessário") não foi
executado.

**Recomendação: não recapturar por enquanto.** O que aquele clique destravaria era o RSRP/SINR 5G
do VIP — e a §6 mostra um caminho melhor para o mesmo dado, sem depender do dissector proprietário.
O que restaria de exclusivo das mensagens NR é throughput por UE
(`PERIOD_PRIVATE_UE_THROUGHPUT_MEASUREMENT`), que não é regressão e não bloqueia nenhuma fase.
Se um dia for necessário, agora é barato: com o `rowNo` resolvido, basta um clique.

**A validação cruzada de tela** (`serialNo`, timestamp e RSRP/RSRQ como o iManager exibe) também
não veio. Mas ela ficou **redundante**: o §4 comparou o decodificador local contra o dissector do
próprio servidor, na mesma mensagem, byte a byte — que é uma evidência mais forte do que a leitura
de tela.

---

## 8. Ações concretas que a captura habilita

Em ordem de valor. **Os itens 1, 2 e 5 foram implementados em 12/08/2026** (ver `MEMORY.md`,
"Paginação do VIP", e a entrada correspondente no `ERRORS.md`). O item 3 está arquivado: o filtro
`RRC_MEAS_RPRT` devolve só 4G e a coleta da perna NR ficou fora de escopo por decisão do usuário.

1. **Paginar com `result-paging`, não re-POSTando `filter-by-cols`.** *(correção de risco real)*
   Hoje `collect_vips` repete o `POST filter-by-cols` com `startRow` crescente sobre o mesmo
   `msg_id` de entrada ([collector.py:1767](core/collector.py#L1767)). Como cada `filter-by-cols`
   aloca um handle novo e **re-executa o filtro contra os dados vivos**, duas páginas do mesmo
   ciclo podem vir de snapshots diferentes — numa task ainda coletando, isso pula ou duplica
   linhas. O navegador não faz assim: filtra **uma vez**, guarda o `data.msgId` devolvido, e pagina
   com `GET query/result-paging?msgId=<novo>&startRow=…&pageSize=…`. Um snapshot, paginação
   estável, e sem vazar um handle por página.
   *Condição de parada confirmada:* `startRow` além do fim devolve `tableData: []` com HTTP 200,
   sem erro (observado com `startRow=1000` num conjunto de 619 e `startRow=460000` num de 459072).

2. **Ordenar no servidor com `POST query/sort`** (`sqlColumnName: "Time"`, **`isAscend: true`**)
   em vez de ordenar no cliente. Devolve o conjunto já ordenado e um `msgId` estável para paginar.
   Consome o `msgId` do `filter-by-cols` e devolve outro; a cadeia continua no mesmo ciclo.

   **Ascendente, não descendente** — ao contrário do que uma versão anterior deste documento
   sugeriu. Com dados novos chegando ao fim, a ordem ascendente mantém o prefixo `[0, k)` estável,
   que é o que dá sentido a um cursor por offset. Descendente insere no início e desloca **todos**
   os offsets a cada ciclo, quebrando o cursor `row`. Descendente serve à tela (mais recentes
   primeiro), não a um coletor que retoma de onde parou.

3. **Estender `core/rrc_decode.py` para a perna NR** (§6) — destrava o 5G do VIP sem a Captura A.
   Gate: conferir as conversões TS 38.133 contra a tela antes de gravar.

4. **`pageSize=1000` é aceito** em `query/result`, `filter-by-cols`, `sort` e `result-paging` — o
   navegador usa 1000 em todos. Vale conferir contra `_VIP_PAGE_SIZE`.

5. **Registrar o contrato do `rowNo`** na `MEMORY.md` e o modelo "`msgId` = conjunto + ordenação"
   — é o fato que impede o bug da Fase 3 de voltar.

---

## 8-bis. Dimensionamento do risco de cursor (análise posterior)

Investigação feita depois de fechar a decisão de **não coletar a perna NR por enquanto** — o filtro
`Message Type = RRC_MEAS_RPRT` devolve só 4G, então §6 fica arquivada como conhecimento, não como
plano.

### O conjunto NÃO filtrado é ordenado e estável; o FILTRADO não é

Duas chamadas independentes de `query/result` (entradas 7 e 10, ~2 s de intervalo, `msgId`
diferentes) devolveram **as mesmas 100 primeiras linhas, na mesma ordem**. E as 1000 linhas da
entrada 7 têm **0 violações** de `serialNo` crescente.

Ou seja: o resultado bruto da task é ordenado por `serialNo` e estável entre materializações.
**Mas o conjunto que sai do `filter-by-cols` não herda isso** — 127 violações em 619 linhas.
A desordem é fina e arbitrária: **128 runs crescentes, média 4,8 linhas**, e **não** acompanha o
`source`/NE (24 dos 128 runs misturam NEs; 9 dos 10 NEs estão fora de ordem internamente).

Isso é o alerta: um filtro determinístico sobre um conjunto ordenado deveria produzir uma
subsequência ordenada. Não produz. O servidor faz algo não trivial (varredura paralela, ou índice
por outra chave) — e **daí não se pode assumir que a ordem se repita entre chamadas.** Com uma só
chamada de `filter-by-cols` no HAR, isso **não é testável** com esta captura.

### A consequência: `safe_serial` pode descartar linhas não lidas

`_build_vip_measurements` avança a marca d'água para `max(serialNo)` do lote
([collector.py:1713](core/collector.py#L1713)) e descarta `serial <= last_serial`
([collector.py:1693](core/collector.py#L1693)). Sobre um conjunto **não ordenado por serial**, um
ciclo que pare no meio grava uma marca d'água alta e **apaga permanentemente** as linhas de serial
menor que ainda não foram lidas. Simulando a parada em cada offset das 619 linhas reais:

| offset de parada `k` | linhas restantes | perdidas para sempre | % |
|---|---|---|---|
| 100 | 519 | 12 | 2,3 % |
| 400 | 219 | 28 | 12,8 % |
| 500 | 119 | 29 | 24,4 % |
| **534 (pior caso)** | 85 | **50** | 58,8 % |
| 550 | 69 | 41 | 59,4 % |
| 619 (conjunto inteiro) | 0 | 0 | **0 %** |

**A última linha é a chave: ler o conjunto inteiro num ciclo é seguro. Parar no meio não é.**

### Por que isso não está causando perda hoje

`_VIP_PAGE_SIZE = 1000` e o conjunto filtrado tem **619 linhas** — cabe numa requisição. O laço de
paginação ([collector.py:1765](core/collector.py#L1765)) nunca roda, `backlog` é `False`, e
`safe_serial` é o máximo do conjunto completo. **O risco é latente, não ativo.** Ele arma quando o
conjunto filtrado passa de 1000 linhas: aqui foram 619 em ~19 h (~33/h), então uma task rodando
~30 h já chega lá — e `_VIP_MAX_PAGES_PER_CYCLE = 5` garante uma parada em offset a cada 5000
linhas.

**Regra que resume tudo: sempre que `backlog` for `True`, linhas estão sendo perdidas em silêncio.**

### Um segundo risco, esse não testável com este HAR

Mesmo sem backlog, o cursor `row` atravessa ciclos: o próximo ciclo materializa um conjunto novo e
aplica o offset antigo. Isso só é correto se a ordem natural for **append-estável** (linhas novas
sempre no fim). A validação de 616/616 ao vivo sugere que é o caso na prática, mas a desordem fina
descrita acima impede afirmar isso. Ordenar no servidor elimina a dúvida em vez de apostar nela.

---

## 9. Fixtures sugeridas (sanitizadas)

Nada aqui contém cookie, `roarand` ou IMSI:

| Arquivo | Conteúdo |
|---|---|
| `vip_msg_explain_info.json` | as 4 respostas reais, indexadas por `rowNo` — **substitui a fixture sintética** que o `ERRORS.md` marcou como inventada |
| `vip_sort_time_desc.json` | as 619 linhas ordenadas, para testar paginação e cursor |
| `rrc_meas_report_nr_vectors.json` | os 2 vetores EN-DC + índices esperados, para o decoder NR |

Um teste de regressão que vale a pena: **`rowNo` casa com o índice 1-based do conjunto do
`msgId`** — travando por bytes, como fiz aqui. É o teste que teria pegado o bug da Fase 3.

---

## 10. Segurança

O HAR contém **cookie `bspsession` e header `roarand` vivos**. Os dois `.har` estão na raiz e
não commitados — mas **o `.gitignore` não tem regra para `*.har`**. Hoje eles só estão fora do
índice porque ninguém deu `git add`. Vale fechar isso; posso adicionar a regra se quiser.
