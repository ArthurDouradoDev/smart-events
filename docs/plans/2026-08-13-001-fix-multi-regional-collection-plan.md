---
title: "Coleta multi-regional: contratos regionais, sessões e isolamento por OSS - Plan"
type: fix
date: 2026-08-13
status: em_execucao
---

# Coleta multi-regional: contratos regionais, sessões e isolamento por OSS - Plan

## Objetivo

Fazer o evento **Teste Curitiba** (`10.220.30.9`, regional `OUTRAS`) coletar KPIs e VIPs sem
regredir **TesteSantoAmaro** (`10.220.50.9`, regional `SP`) e sem permitir que uma coleta iniciada
para um evento grave dados ou checkpoints no evento seguinte.

Este plano foi reestruturado após a análise dos arquivos:

- `har-oss-outros/har-monitoring-oss-tsl.har`;
- `har-oss-outros/har-vips-oss-tsl.har`;
- `data/logs/smart_events.log`;
- `data/smart_events.db` e bancos específicos dos dois eventos.

As antigas Fases 1 e 2 permanecem válidas e já foram executadas. A antiga Fase 3, centrada em criar
novos resolvedores de nomes, foi retirada do caminho crítico: os nomes reais da task PM 2225 já
casam exatamente com o inventário do evento.

---

## Quando o sistema deve voltar a funcionar

| Marco | Resultado esperado |
|---|---|
| Estado atual, depois das Fases 1 e 2 | Alarmes funcionam; KPI e VIP de Curitiba ainda não |
| **Fim da Fase 3** | Troca SP ↔ Curitiba isolada, sem workers e checkpoints cruzados; ainda não garante dados de Curitiba |
| **Fim da Fase 4** | **KPIs de Curitiba devem aparecer** e continuar funcionando em SP |
| **Fim da Fase 5** | **KPIs + VIPs + alarmes devem funcionar em Curitiba e SP** — primeiro marco funcional completo |
| **Fim da Fase 6** | Solução endurecida, dados contaminados tratados e rollout pronto para produção |

Portanto, a resposta objetiva é: **os KPIs são esperados a partir do fim da Fase 4; o sistema
completo, incluindo VIP, é esperado a partir do fim da Fase 5**. A Fase 6 não deve ser necessária
para fazer os dados aparecerem; ela fecha riscos operacionais e corrige o histórico contaminado.

---

## Evidências consolidadas

### Estado dos bancos em 13/08/2026

| Item | Teste Curitiba | TesteSantoAmaro |
|---|---:|---:|
| sites cadastrados | 431 | 5 |
| células cadastradas | 6.242 | 33 |
| `kpi_measurements` | **0** | 128.735 |
| `vip_measurements` no banco global | **0** | 50.315 |
| alarmes no banco do evento | 2.503 | 3.297 |
| objetos PM configurados na task | 116 | 29 objetos ativos no último checkpoint |

### Monitoring / KPI — o mapeamento da task 2225 já funciona

O HAR de Monitoring mostra que a task 2225:

- existe e abre com HTTP 200;
- chama-se `TESTE FERRAMENTA CURITIBA (2225)`;
- é `Measurement of Cell Performance`, período de 5 minutos;
- contém 116 objetos e 24 contadores LTE usados pelas fórmulas do aplicativo;
- executou dois `POST /rest/oss/access/pm/v1/monitor/task/result` com HTTP 200 no navegador;
- possui sete sites: `CTFA01`, `CTFD01`, `CTFD60`, `CTFE01`, `CTFG99`, `CTFR99` e `CTFZ01`.

As 116 ocorrências de `Cell Name` foram comparadas com as células do evento:

- correspondência exata: **116/116**;
- execução do resolvedor atual com os nomes reais: **116 mapeadas, 0 descartadas**;
- não existe necessidade comprovada de remover prefixo, usar substring ampla ou cadastrar `obj_no`
  para essa task.

Uma NE estava desconectada: `SR-CTFD60`, com 12 objetos. Por isso o segundo ciclo do navegador
consultou 104 objetos ativos. Essa indisponibilidade explica cobertura parcial, mas não explica zero
KPIs — as outras 104 células deveriam produzir dados.

O bloqueio observado no aplicativo acontece antes do parser: a sessão de Monitoring recebe 401,
o processo interpreta um `roarand` diferente no arquivo como renovação suficiente, recarrega a
sessão e recebe 401 novamente. O navegador, no mesmo OSS, recebe HTTP 200.

### VIP — o OSS de Curitiba usa outro contrato FARS

A task 14837 não está vazia nem parada:

- `pre-check`: `checkState=true`;
- estado: `Running`;
- aproximadamente 388.997 mensagens no momento da captura;
- janela observada: `2026-08-13 09:37:02` até aproximadamente `18:15`;
- a interface conseguiu filtrar `RRC_MEAS_RPRT`, ordenar e abrir mensagens decodificadas.

SP usa o contrato síncrono já implementado:

1. `GET query/result`;
2. `GET query/fetch-field-values`;
3. `POST query/filter-by-cols`;
4. `POST query/sort`;
5. `GET query/result-paging`.

Curitiba usa o contrato assíncrono:

1. `GET query/result`;
2. `GET query/fetch-field`;
3. polling em `GET query/fetch-field-values-result`;
4. `POST query/filter-by-cols-start`;
5. polling em `POST query/filter-by-cols-result`;
6. `POST query/sort`;
7. `GET query/result-paging`.

O coletor chama `fetch-field-values`, que não é o contrato dessa regional, e recebe HTTP 500 em
todos os ciclos. Ele nem chega à filtragem, paginação ou decodificação. Este é o bloqueio direto do
VIP de Curitiba.

### Troca de projeto — coleta antiga grava no evento novo

Há evidência temporal direta:

- `18:29:19`: o scheduler registra a coleta anterior como encerrada e ativa Curitiba;
- `18:29:32`: a coleta VIP antiga de SP ainda termina com cinco medições da task 2073;
- no mesmo segundo, o checkpoint da task SP é gravado no banco de Curitiba com `oss='OUTRAS'`.

A causa é a combinação de:

- `stop()` faz `join(timeout=5)`, embora uma requisição possa durar 30–120 segundos;
- `start()` limpa o mesmo `_stop_event` logo depois;
- `_apply_result()` usa `_event_config` mutável, que já aponta para o evento novo quando o resultado
  antigo termina.

Também foram encontrados checkpoints PM 2225 sob `SP` e `OUTRAS` no mesmo banco, checkpoints VIP
2072/2073 de SP sob `OUTRAS`, dois eventos simultaneamente `ACTIVE` e várias ocorrências de
`database is locked` durante sobreposição de workers.

### Limitação das capturas HAR

O DevTools preservou metadados, payloads e rotas, mas não preservou todos os corpos comprimidos:

- Monitoring: 22 respostas sem `content.text`, incluindo as duas respostas de `monitor/task/result`;
- VIP: 19 respostas sem `content.text`, incluindo as respostas finais dos dois pollings assíncronos.

A mensagem `Failed to load response data / No data found for resource with given identifier` indica
que o DevTools já não possui o corpo associado àquela entrada. Salvar novamente o mesmo log como HAR
não recupera esse conteúdo. Por isso o novo plano não depende de obter esses corpos manualmente pelo
navegador.

---

## Regras de segurança e regressão

1. Cada fase deve ser um commit isolado.
2. Antes de cada mudança, registrar o baseline atual da suíte; o gate é **nenhuma falha nova**, em
   vez de depender de uma contagem fixa que pode mudar com os testes adicionados.
3. Validar sempre SP antes de Curitiba em cada rollout.
4. Nunca apagar checkpoints ou bancos antes de criar backup e validar os alvos exatos.
5. Adaptadores regionais devem ser detectados por contrato/capacidade, não por IP hardcoded.
6. Logs e fixtures não podem conter `bspsession`, `roarand`, cookies, IMSI ou credenciais.
7. Um resultado deve carregar identidade imutável de evento, OSS e geração desde o início da coleta
   até a persistência. Estado global mutável não pode decidir onde gravá-lo.

---

## Fase 1 — Observabilidade persistente — CONCLUÍDA

### Entregas

- log rotativo em `data/logs/smart_events.log`;
- captura crua opt-in e sanitizada em `data/diagnostics/`;
- log individual para cada task VIP, inclusive vazia ou com falha;
- cobertura de Monitoring visível na interface;
- botão de captura de diagnóstico;
- testes de log, dump e interface.

### Resultado obtido

A fase revelou o erro repetido de VIP em `fetch-field-values`, os 401 de Monitoring, a coleta antiga
continuando após a troca e as ocorrências de bloqueio do SQLite.

---

## Fase 2 — Cursor não avança sobre dado descartado — CONCLUÍDA

### Entregas

- cursor por objeto somente quando o objeto produz pelo menos uma medição válida;
- cursor geral da task retido quando existe descarte;
- scheduler não confirma checkpoint de um ciclo totalmente descartado;
- persistência idempotente para permitir replay.

### Limite da fase

Ela protege respostas que chegaram ao parser. Não pode corrigir:

- HTTP 401 antes de o Monitoring devolver JSON;
- HTTP 500 do VIP antes de o filtro;
- resultado de worker antigo aplicado ao evento novo.

---

## Fase 3 — Isolamento determinístico ao trocar de evento

### Objetivo

Eliminar a contaminação cruzada antes de continuar os testes de regional. Sem isso, qualquer
validação de sessão ou API pode gravar no banco errado e produzir conclusões falsas.

### Código

- `core/scheduler.py`:
  - introduzir um token/geração imutável por `start()`;
  - cada worker captura `event_id`, regional, coletor e geração na criação;
  - antes de persistir ou atualizar status, descartar resultados de geração obsoleta;
  - `stop()` sinaliza a geração e não reutiliza o mesmo evento de parada para a próxima;
  - substituir a dependência de `_event_config` mutável dentro de `_apply_result` por contexto do
    próprio ciclo;
  - garantir no máximo um worker vivo por tipo e geração.
- `api/api.py::activate_event`:
  - encerrar o evento anterior antes de ativar o novo;
  - manter somente um evento `ACTIVE`;
  - não iniciar B até a geração de A estar invalidada.
- `core/collector.py` e `core/session_renew.py`:
  - chavear `_needs_interactive`, backoff, falhas e cooldown por `(host, módulo)`;
  - usar `browser_profile_<host>` em vez de um perfil compartilhado entre regionais.

### Testes obrigatórios

- resultado de A terminado depois de ativar B não grava medição, alerta ou checkpoint em B;
- `stop()` + `start()` não reanima thread da geração anterior;
- ativar B encerra A no banco;
- backoff/CAPTCHA de SP não bloqueia Curitiba;
- perfis e session files permanecem separados por host.

### Validação ao vivo

Executar SP → Curitiba → SP, verificando no log que nenhuma task da regional anterior termina
aplicada após a ativação da nova. Conferir os checkpoints dos dois bancos.

### Critério de saída

Nenhum estado cruzado em três trocas consecutivas. Esta fase torna os testes confiáveis, mas ainda
não promete KPI ou VIP de Curitiba.

### Commit sugerido

`fix: isolate collection generations and sessions across OSS switches`

---

## Fase 4 — Sessão de Monitoring validada por host e retomada dos KPIs

### Objetivo

Fazer a PM task 2225 chegar ao parser com uma sessão realmente autenticada. O resolvedor existente
já deve mapear os objetos.

### Código

- `core/collector.py`:
  - não considerar mudança de `roarand` prova suficiente de renovação;
  - após recarregar ou renovar, executar um probe autenticado do módulo e só retornar sucesso após
    HTTP 200 com contrato JSON esperado;
  - se o probe falhar, continuar a renovação daquele `(host, módulo)` em vez de aceitar a sessão;
  - manter Monitoring e Trace independentes, embora compartilhem cookies quando válido;
  - distinguir nos logs `arquivo recarregado`, `probe aceito`, `probe recusado` e `Playwright usado`.
- `core/session_renew.py`:
  - gravar atomicamente o session file específico do host;
  - confirmar que o módulo solicitado capturou cookies e `roarand` válidos antes de retornar sucesso.
- preservar `_resolve_monitoring_cell` como está; não adicionar fallback amplo sem uma célula real
  que falhe no resolvedor exato.

### Testes obrigatórios

- `roarand` mudou, mas probe retorna 401 → renovação ainda não é sucesso;
- probe HTTP 200/JSON válido → sessão aceita;
- renovação de Trace não marca Monitoring inválido como renovado;
- os 116 objetos reais extraídos da abertura da task mapeiam 116/116;
- 12 objetos indisponíveis não impedem as outras 104 células de produzir medições;
- resposta vazia legítima mantém a semântica de cursor da Fase 2.

### Validação ao vivo

1. SP por dois ciclos: KPIs continuam entrando.
2. Curitiba por até dois ciclos PM:
   - HTTP 200 no log;
   - `recebidos > 0`, `mapeados > 0`;
   - linhas em `kpi_measurements`;
   - cursor volta a avançar apenas para objetos persistidos.
3. Confirmar que `SR-CTFD60` aparece como indisponibilidade parcial, não como falha total.

### Critério de saída — PRIMEIRO MARCO FUNCIONAL

**KPIs de Curitiba aparecem no mapa e no banco, sem regressão em SP.** Se o JSON real revelar uma
forma de `objName` diferente da resposta de abertura da task, abrir uma correção mínima apoiada na
captura — não reativar automaticamente toda a antiga Fase 3.

### Commit sugerido

`fix: validate monitoring sessions per OSS before collecting PM data`

---

## Fase 5 — Adaptador FARS síncrono/assíncrono e retomada dos VIPs

### Objetivo

Consumir a task 14837 no contrato assíncrono de Curitiba sem alterar o caminho síncrono que funciona
em SP.

### Passo 5A — probe interno, sem depender do DevTools

Criar um diagnóstico temporário/reutilizável que, usando a sessão Trace já salva pelo aplicativo:

1. abra um `msgId` novo;
2. tente o contrato síncrono;
3. quando o endpoint indicar incompatibilidade, use `fetch-field` e faça polling em
   `fetch-field-values-result`;
4. grave apenas as respostas finais sanitizadas em `data/diagnostics/`;
5. execute `filter-by-cols-start`, faça polling em `filter-by-cols-result` e grave o envelope final;
6. nunca grave headers, cookies, `roarand`, IMSI ou conteúdo identificável desnecessário.

O objetivo do probe é congelar o envelope exato que faltou no HAR. A captura deve ocorrer pelo
cliente HTTP do aplicativo, que recebe o corpo diretamente, e não pelo cache do DevTools.

### Passo 5B — adaptador de contrato

- modelar operações comuns: abrir consulta, obter janela, filtrar, ordenar e paginar;
- preservar o adaptador síncrono para SP;
- adicionar adaptador assíncrono:
  - polling com intervalo curto, prazo total e cancelamento pela geração da Fase 3;
  - interpretar estados `em progresso`, `concluído` e `erro` pelos envelopes reais do probe;
  - extrair o novo `msgId` e `recordCount` somente na conclusão;
  - não repetir `filter-by-cols-start` durante o mesmo ciclo;
- selecionar o adaptador por capacidade/resposta do endpoint e manter a decisão em cache por host e
  versão da sessão;
- continuar ordenando por `Time` ascendente e paginando um único snapshot;
- manter os cursores `row` e `serial` isolados por evento, host e task.

### Testes obrigatórios

- contrato síncrono de SP continua fazendo a mesma sequência atual;
- contrato assíncrono percorre progresso até conclusão;
- polling com erro, timeout ou cancelamento não grava cursor;
- somente uma inicialização do filtro por ciclo;
- task 14837 passa por filtro, sort, paginação e decoder;
- nenhum valor é gravado a partir de resposta parcial;
- fixtures dos dois contratos são sanitizadas.

### Validação ao vivo

1. SP: tasks 2072 e 2073 continuam coletando.
2. Curitiba:
   - task 14837 passa do passo de janela sem HTTP 500;
   - `recordCount` filtrado aparece no diagnóstico;
   - `linhas_lidas > 0` e `linhas_decodificadas > 0`;
   - medições são inseridas e aparecem no painel VIP.
3. Trocar de evento durante um polling e confirmar que o ciclo cancelado não persiste nada.

### Critério de saída — SISTEMA FUNCIONAL COMPLETO

**Alarmes, KPIs e VIPs funcionam em SP e Curitiba.** Este é o primeiro ponto em que a coleta
multi-regional completa deve estar operacional.

### Commit sugerido

`fix: support synchronous and asynchronous FARS query contracts`

---

## Fase 6 — Endurecimento, saneamento e rollout

### Objetivo

Tratar o histórico contaminado e fechar os riscos restantes depois que a coleta já estiver
funcionando.

### Ações

- criar backups datados dos bancos antes de qualquer limpeza;
- produzir relatório exato dos checkpoints incompatíveis, sem apagá-los automaticamente;
- remover, após validação explícita:
  - tasks VIP 2072/2073 sob `OUTRAS` no banco de Curitiba;
  - registros PM 2225 sob `SP` no banco de Curitiba;
  - outros checkpoints cuja task não pertence ao evento/OSS;
- manter medições históricas válidas e usar as chaves únicas para replay seguro;
- resolver os `database is locked` restantes:
  - transações curtas;
  - rollback garantido;
  - WAL/busy timeout se confirmado necessário pelos testes;
- fazer `get_event_vips` falhar fechado quando cliente/regional não estiver resolvido;
- remover fallback silencioso de `resolve_base_url` para SP;
- exibir na interface regional/host ativo, contrato FARS selecionado e causa por task;
- atualizar `MEMORY.md` e `ERRORS.md` com os dois contratos regionais.

### Validação final

1. Rodar SP por três ciclos completos.
2. Rodar Curitiba por três ciclos completos.
3. Repetir SP → Curitiba → SP.
4. Confirmar:
   - somente um evento `ACTIVE`;
   - nenhum worker antigo vivo;
   - nenhum checkpoint cruzado novo;
   - KPIs e VIPs avançando sem lacunas ou duplicação;
   - alarmes continuam chegando;
   - nenhum novo `database is locked`.

### Commit sugerido

`fix: harden multi-regional persistence and clean invalid checkpoints`

---

## Como recapturar no DevTools, se ainda for útil

Isso deixa de ser bloqueador, mas uma nova captura pode ajudar na contraprova:

1. abrir o DevTools **antes** de abrir a task;
2. em Network, ativar gravação, `Preserve log` e `Disable cache`;
3. limpar a lista;
4. filtrar por `fetch-field-values-result`, executar a ação e abrir a entrada mais nova assim que
   concluir;
5. repetir para `filter-by-cols-result`;
6. salvar imediatamente com **Save all as HAR with content**.

Se a aba Response já mostrar `No data found for resource with given identifier`, aquele corpo já foi
perdido e não há configuração que o recupere retroativamente. Não é necessário insistir: o probe da
Fase 5A é a fonte preferencial e mais confiável.

---

## Decisões que este plano substitui

- Não tratar a antiga Fase 3 de mapeamento como correção principal da PM 2225: os 116 nomes reais já
  casam exatamente.
- Não esperar que a Fase 2 faça os dados aparecerem: ela protege cursores depois do parser, enquanto
  os bloqueios atuais acontecem antes dele.
- Não deixar isolamento de threads e sessões para o final: a contaminação já aconteceu e invalida
  testes intermediários.
- Não depender de HAR com corpos grandes para implementar o FARS assíncrono: capturar diretamente no
  cliente HTTP do aplicativo.
