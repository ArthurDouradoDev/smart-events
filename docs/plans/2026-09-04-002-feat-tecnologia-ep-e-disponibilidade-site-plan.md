# Plano — Tecnologia da EP como fonte da verdade e disponibilidade por site

**Data:** 2026-09-04  
**Status:** implementação 2 concluída em 2026-09-04
**Tipo:** evolução funcional e consolidação de contrato  
**Escopo:** importação da EP, domínio de tecnologia, coleta Monitoring, APIs de sites/KPIs,
dashboard, Servidor Central, exportação, compatibilidade e testes

## Objetivo

Entregar duas mudanças relacionadas:

1. mostrar, ao lado de cada site no painel esquerdo, quais tecnologias possuem dado válido para a
   métrica atual, diferenciando visualmente as tecnologias sem dado;
2. tornar a coluna `tecnologia` da EP a fonte autoritativa para dizer se cada célula pertence à
   família 4G ou 5G.

No cenário de referência, `RJ9991  4G · 5G` continuará informando que o site possui as duas
tecnologias, mas `4G` ficará normal e `5G` ficará esmaecido e riscado quando não houver coleta 5G
recente para a métrica selecionada. O texto de ajuda explicará o motivo, para que a distinção não
dependa apenas de cor ou opacidade.

## Diagnóstico do estado atual

O projeto já contém parte da infraestrutura necessária, mas ainda não possui o contrato completo:

- `server.py` já reconhece os aliases `tech`, `technology`, `tecnologia`, `tecnologia móvel` e
  `rat`, normaliza o valor e grava `cell.tech` e `cell.ep.technology`;
- essa coluna ainda é opcional e, quando está ausente ou inválida, a importação infere tecnologia
  pela banda;
- `api/api.py` e `core/collector.py` ainda concatenam a tecnologia declarada com o nome da célula
  antes de classificar. Se os dois se contradisserem, a família vira indeterminada, portanto a EP
  ainda não é realmente autoritativa;
- `frontend/js/map.js`, `frontend/js/kpi.js` e o preview do Servidor Central ainda possuem
  inferências locais pelo identificador da célula;
- a lista de sites recebe `tech_families`, mas renderiza as famílias como um único texto
  (`4G · 5G`) e não recebe disponibilidade por tecnologia;
- o gráfico já recebe `reasons` por família (`ok`, `no_traffic`, `no_data`) e mostra mensagens como
  `5G sem dado nesta métrica`. Essa semântica deve ser reaproveitada na lista, sem criar uma segunda
  definição de disponibilidade;
- o template `ep_default.xlsx` ainda não contém a coluna `tecnologia`.

Portanto, a segunda implementação não começa do zero: ela deve transformar o suporte parcial já
existente em uma regra única e obrigatória para novas EPs, preservando apenas um caminho explícito
de compatibilidade para eventos antigos.

## Decisões de produto e domínio

### 1. Inventário e disponibilidade são conceitos diferentes

- `tech_families` responde: **quais tecnologias o site possui segundo a EP?**
- `technology_reasons` responde: **qual é a situação da métrica atual em cada tecnologia?**

Uma tecnologia não deve desaparecer do rótulo apenas porque não entregou dados. Ela permanece
visível, porém com estado visual diferente. Isso evita transformar falha de coleta em falsa ausência
de infraestrutura.

### 2. Estados de disponibilidade

O payload dinâmico de cada site passará a trazer:

```json
{
  "technology_reasons": {
    "4G": "ok",
    "5G": "no_data"
  }
}
```

Os estados terão a mesma definição usada pelo gráfico:

| Estado | Regra | Apresentação no painel |
|---|---|---|
| `ok` | existe ao menos um valor válido da métrica para a família na janela vigente | texto normal |
| `no_traffic` | houve coleta da família, mas a métrica não pôde ser calculada no período | opacidade reduzida, sem risco, com ajuda “sem tráfego” |
| `no_data` | não houve coleta recente da família | opacidade reduzida e texto riscado, com ajuda “sem dado nesta métrica” |
| ausente/desconhecido | a API não conseguiu classificar com segurança | aparência neutra; nunca acusar falha por suposição |

O estado será calculado para cada site físico fundido e para cada família declarada na EP. Valor
zero é dado válido e deve resultar em `ok`.

### 3. Janela temporal

Na lista de sites, disponibilidade seguirá a mesma janela do estado atual já usada por
`get_site_status`: 15 minutos, ancorados na medição mais recente do evento. Em modo histórico, a
âncora será o timestamp histórico recebido pela API.

Os botões `15 min`, `30 min`, `60 min` e `Evento` continuam controlando o gráfico; eles não devem
mudar silenciosamente a semântica da lista. O gráfico mantém seus próprios `reasons` na janela
selecionada.

### 4. Coluna `tecnologia` em novas importações

Para toda nova EP importada depois desta mudança:

- a coluna canônica será `tecnologia`;
- os aliases já aceitos poderão continuar funcionando;
- cada linha importada deverá possuir um valor reconhecido como 4G ou 5G;
- serão aceitos, no mínimo, `4G`, `LTE`, `5G` e `NR`, normalizados para `4G` ou `5G`;
- valor vazio ou inválido deverá rejeitar a importação, informando as linhas afetadas e os valores
  aceitos;
- banda, frequência, nome da célula, nome do site e tecnologia da task nunca substituirão um valor
  válido da EP;
- células duplicadas com tecnologias divergentes deverão gerar erro, e não “primeira linha vence”.

O retorno de erro deve ser acionável e limitado a uma amostra de linhas, acompanhado da quantidade
total de erros, para continuar legível em EPs grandes.

### 5. Compatibilidade com eventos existentes

Eventos já salvos sem `cell.tech` não serão bloqueados nem reescritos automaticamente.

Para eles, o sistema manterá um fallback legado, nesta ordem:

1. `cell.ep.technology`, quando existir;
2. tecnologia persistida junto ao dado coletado;
3. inferência por identificador/frequência, somente quando as opções anteriores estiverem ausentes.

O fallback deverá ser identificável em log/diagnóstico. Ao editar um evento legado e importar uma
nova EP, a validação nova passa a valer. Não haverá migração destrutiva nem alteração retroativa dos
KPIs já armazenados.

### 6. Limite da autoridade da EP

A EP é a fonte da **família da célula** (`4G` ou `5G`). A tecnologia da task continua sendo a fonte
do **tipo de coleta e da fórmula** (`4G`, `5G_NRCELL` ou `5G_NRDUCELL`), pois NR Cell e NR DU Cell
possuem contadores diferentes.

Assim:

- uma célula marcada como 4G na EP não pode ser associada a uma task 5G;
- uma célula marcada como 5G pode ser coletada por uma task NR Cell ou NR DU Cell;
- `kpi_measurements.technology` continua guardando a tecnologia da task para preservar fórmula,
  proveniência e exportação;
- a família apresentada ao usuário deve preferir a EP quando a célula estiver no inventário.

## Arquitetura proposta

### Núcleo compartilhado de tecnologia

Criar um módulo puro, preferencialmente `core/technology.py`, para eliminar regras divergentes.
Ele deverá oferecer funções tipadas para:

- normalizar valor da EP para `4G`/`5G`;
- converter tecnologia de task para família;
- resolver a família de uma célula com precedência explícita e modo legado controlado;
- informar a origem da resolução (`ep`, `measurement`, `legacy_id`, `legacy_frequency`, `unknown`);
- validar conflito entre EP, task e dado persistido.

`server.py`, `api/api.py`, `core/collector.py` e `core/event_export.py` devem reutilizar essas
funções. JavaScript não deve recriar a regra de domínio quando a API puder enviar a família pronta.

### Contrato estático dos sites

`get_site_layout` continuará entregando `tech_families` e células anotadas com `family`, mas essas
informações passarão a ser derivadas prioritariamente da EP. A fusão de sites, os membros, os
clusters, as portadoras e os filtros 4G/5G deverão usar o mesmo resolvedor.

### Contrato dinâmico dos sites

`get_site_status(event_id, metric, timestamp, technology_family)` incluirá
`technology_reasons`. O cálculo deve:

1. partir das famílias esperadas em `tech_families`;
2. usar as leituras já feitas em lote por `_build_site_status`, sem consulta por site;
3. marcar `ok` quando houver valor válido da métrica na janela;
4. marcar `no_traffic` quando houver qualquer coleta recente da família, mas não a métrica;
5. marcar `no_data` quando não houver coleta recente da família;
6. respeitar site fundido, filtro de família e timestamp histórico.

A regra de classificação deve ser extraída e compartilhada com `_attach_series_reasons`, para lista
e gráfico não discordarem.

Não é necessário criar tabela nova. `get_latest_kpi` já carrega as linhas recentes usadas no
status e pode alimentar o índice de famílias coletadas. Se uma consulta adicional se mostrar
necessária, ela deve ser única por evento/poll e coberta por teste de desempenho — nunca N+1.

### Contrato das séries e remoção de inferência no navegador

Para o modo `Site completo`, o retorno de `get_kpi_series` deverá acrescentar um mapa como:

```json
{
  "cell_families": {
    "18NLRJPE41A": "5G"
  }
}
```

`frontend/js/kpi.js` usará esse mapa para rótulos e agrupamentos, eliminando
`_cellFamilyFromId` como regra principal. A inferência local poderá existir somente para payloads
legados sem `cell_families`, marcada como compatibilidade.

O mapa principal e o preview do Servidor Central já preferem `cell.tech`; ambos devem manter
inferência apenas quando o campo autoritativo estiver ausente. Um valor explícito nunca poderá ser
anulado por um nome de célula contraditório.

## Implementação 1 — Indicador visual no painel de sites

### Backend

**`api/api.py`**

- extrair uma função única que produza os estados `ok`, `no_traffic` e `no_data`;
- indexar linhas recentes por `(site_fundido, família)`;
- incluir `technology_reasons` nas linhas de `_build_site_status`;
- preservar o campo no fallback de cache e na composição layout + status;
- quando houver filtro explícito 4G ou 5G, devolver somente o estado da família solicitada;
- garantir que sites sem tecnologia conhecida não recebam um falso `no_data`.

**`core/database.py`**

- preferir as consultas em lote já existentes;
- somente adicionar uma consulta de presença se os dados de `get_latest_kpi` não cobrirem a
  distinção necessária;
- manter a mesma âncora temporal usada pelo valor/status do site.

**`frontend/js/bridge.js`**

- atualizar mocks e cenários de demonstração com `technology_reasons`;
- garantir paridade entre o bridge real e o mock.

### Frontend

**`frontend/js/kpi.js`**

- substituir o texto único por um elemento individual para cada família;
- aplicar classes `is-no-data` e `is-no-traffic` de acordo com `technology_reasons`;
- manter ordem determinística `4G`, depois `5G`;
- manter o separador fora do texto riscado;
- adicionar `title` e `aria-label` com o motivo completo;
- atualizar naturalmente a cada poll, pois o dado ficará no payload dinâmico de status;
- não esconder nem desabilitar o site; a mudança é informativa.

**`frontend/css/main.css`**

- `ok`: aparência atual;
- `no_traffic`: opacidade moderada, sem `line-through`;
- `no_data`: opacidade menor e `text-decoration: line-through`;
- manter contraste legível no tema escuro e foco/tooltip acessíveis.

Resultado esperado para o exemplo:

```text
RJ9991  4G · 5G
        ──   ───
        normal  esmaecido + riscado
```

### Critérios de aceite

1. Site com 4G e 5G na EP, mas somente KPI 4G recente, mostra 4G normal e 5G esmaecido/riscado.
2. Site com dados nas duas famílias mostra ambos normais.
3. Site com coleta 5G, porém métrica não calculável por ausência de tráfego, mostra o estado
   intermediário e a ajuda “5G sem tráfego no período”.
4. Valor `0` mantém a tecnologia normal.
5. Trocar a métrica recalcula os estados sem recarregar o layout estático.
6. O poll atualiza os estados quando uma tecnologia volta a coletar.
7. No histórico, o estado reflete a âncora histórica.
8. Falha da API ou payload legado não risca tecnologia por engano.
9. Leitor de tela consegue distinguir os estados sem depender da aparência.

## Implementação 2 — `tecnologia` da EP como fonte da verdade

### Importação e persistência

**`server.py`**

- mover a normalização para o módulo compartilhado;
- exigir tecnologia válida em todas as linhas importadas por novas EPs;
- manter os aliases de cabeçalho, com `tecnologia` como nome documentado;
- validar antes de montar/salvar os sites, evitando importação parcial;
- detectar duplicidades contraditórias;
- persistir a forma canônica em `cell.tech` e a proveniência em `cell.ep.technology`;
- retornar erros com número da linha, célula e valor recebido.

**`ep_default.xlsx` e documentação**

- adicionar a coluna `tecnologia` ao template;
- preencher o exemplo com `4G`;
- atualizar README e instruções do operador com valores válidos e exemplo 4G/5G;
- explicar que banda e DLEARFCN não determinam mais tecnologia em novas importações.

### Coleta

**`core/collector.py`**

- fazer `_normalize_cell_technology` respeitar primeiro a tecnologia declarada;
- registrar no metadado estático a família e a origem da classificação;
- limitar cada task às células cuja família da EP é compatível;
- impedir que o nome da célula contradiga a EP;
- produzir diagnóstico específico para conflito task × EP, incluindo task, célula e famílias;
- manter inferência somente para eventos legados sem tecnologia.

O campo de tecnologia gravado em cada medição continuará sendo o tipo da task, não uma cópia da
EP. Isso é necessário para selecionar a fórmula correta e distinguir NR Cell de NR DU Cell.

### APIs, mapa, clusters e séries

**`api/api.py`**

- fazer `_cell_technology_family` dar precedência absoluta a `cell.tech`;
- usar a regra compartilhada em fusão de sites, filtros, membros, portadoras, clusters, células e
  séries;
- ao classificar uma linha persistida, consultar primeiro o índice da EP por célula; usar
  `row.technology` apenas para legado ou para a distinção interna de fórmula;
- enviar `family`/`cell_families` ao frontend para remover decisões por nome no navegador.

**`frontend/js/map.js`, `frontend/js/kpi.js` e `server_frontend/index.html`**

- consumir `tech`/`family` enviados pelo backend;
- manter heurísticas por nome/frequência somente em modo de compatibilidade quando o campo estiver
  ausente;
- nunca combinar o texto declarado com o identificador para decidir a família.

**`core/event_export.py`**

- preservar a distinção já existente:
  - `technology`: tecnologia da coleta/task;
  - `technology_family`: família resolvida;
  - `technology_ep`: valor autoritativo da EP;
- fazer `technology_family` preferir a EP quando a célula puder ser localizada no inventário;
- não reclassificar nem apagar medições históricas.

### Critérios de aceite

1. Uma célula com nome neutro e `tecnologia=5G` aparece e é filtrada como 5G em todas as telas.
2. Uma célula cujo nome sugere 5G, mas cuja EP declara 4G, é tratada como 4G.
3. Banda 2100 ou 3500 não sobrescreve uma tecnologia explícita.
4. Uma task 5G não coleta célula marcada como 4G; o conflito fica diagnosticado.
5. NR Cell e NR DU Cell continuam escolhendo fórmulas distintas, embora ambas pertençam à família
   5G.
6. Nova EP sem a coluna, com valor vazio ou inválido é rejeitada antes de salvar o evento.
7. Evento antigo sem `cell.tech` continua abrindo por fallback legado e gera aviso observável.
8. Fusão de sites, clusters, portadoras, mapa, lista, gráfico e Visão Geral concordam sobre a
   família da célula.
9. A exportação mantém separadas tecnologia da task e tecnologia da EP.

## Ordem de execução recomendada

### Fase 1 — Contrato autoritativo da EP

- criar `core/technology.py` e testes unitários;
- endurecer o parser e atualizar o template;
- corrigir coletor e APIs para usar a precedência nova;
- enviar família explícita em todos os payloads relevantes;
- manter e testar o fallback legado.

Essa fase deve ser concluída primeiro, porque a disponibilidade visual só é confiável quando a
família esperada de cada célula/site também é confiável.

### Fase 2 — Disponibilidade dinâmica por site

- compartilhar a regra de `reasons` entre gráfico e lista;
- acrescentar `technology_reasons` ao status;
- atualizar bridge/mocks;
- renderizar estados individuais no painel e adicionar estilos acessíveis.

### Fase 3 — Validação integrada e documentação

- executar testes Python e Playwright direcionados;
- validar cenário 4G normal + 5G sem dado;
- medir o tempo de `get_site_status` com evento grande;
- revisar README, template e documentação de exportação;
- validar pacote desktop/Servidor Central para evitar divergência entre as duas interfaces.

Cada fase deve terminar com um commit próprio e testes verdes. As alterações locais já existentes
no worktree devem ser preservadas e não misturadas nesses commits.

## Matriz mínima de testes

### Unitários Python

- normalização de aliases e caixa/espaços;
- declaração da EP contra nome e frequência contraditórios;
- rejeição de coluna ausente, valor inválido e duplicidade conflitante;
- fallback de evento legado;
- compatibilidade task × família da EP;
- `technology_reasons` para `ok`, `no_traffic`, `no_data`, zero e desconhecido;
- site físico fundido com 4G/5G e apenas uma família com dado;
- timestamp histórico;
- `cell_families` nas séries.

Arquivos principais: `tests/test_server_parse_sites.py`, `tests/test_kpi_monitoring.py` e
`tests/test_api.py`.

### Interface com Playwright

- `4G` normal e `5G` esmaecido/riscado no mesmo site;
- texto acessível e tooltip corretos;
- atualização após troca de métrica e após poll;
- estado `no_traffic` distinto de `no_data`;
- filtro 4G/5G e seleção do site continuam funcionando;
- payload legado não produz falso negativo.

Arquivos principais: `tests/test_frontend_site_render.py` e
`tests/test_frontend_kpi_chart_ui.py`.

### Regressão e desempenho

- executar a suíte de API, coleta, mapa, gráfico e Visão Geral;
- assegurar que `get_site_status` não faz consultas por site;
- manter o orçamento atual de leitura para eventos grandes;
- validar que a mudança de template não altera identificadores com zeros à esquerda.

## Riscos e mitigação

| Risco | Mitigação |
|---|---|
| EPs antigas deixam de importar | exigência só para novas importações após atualização do template; eventos já persistidos usam fallback legado |
| Nome da célula contradiz a coluna | coluna vence e o conflito gera diagnóstico/teste |
| “Sem tráfego” parece falha de coleta | estado visual e tooltip diferentes de `no_data` |
| Status da lista diverge do gráfico | mesma função de classificação; janelas diferentes ficam explicitamente documentadas |
| Poll fica mais lento | índices em memória e consultas em lote; teste de orçamento |
| Tecnologia da EP substitui indevidamente a fórmula | manter `kpi_measurements.technology` como tecnologia da task |
| Opacidade não é acessível | combinar estilo, risco apenas em `no_data`, `title` e `aria-label` |

## Fora de escopo

- esconder completamente a tecnologia sem dado;
- desabilitar o clique no site ou no seletor de tecnologia;
- alterar automaticamente as tasks PM com base na EP;
- apagar ou regravar KPIs históricos;
- transformar ausência de dado em alarme operacional novo;
- mudar as fórmulas de KPI.

## Definição de pronto

A entrega estará pronta quando uma EP nova e validada controlar de forma consistente a família de
cada célula desde a importação até a tela, enquanto o painel de sites diferenciar por família a
presença de dados da métrica sem confundir inventário, ausência de tráfego e falha de coleta.

No caso da imagem de referência, `RJ9991` deverá permanecer identificado como site 4G/5G, o `4G`
ficará normal, o `5G` ficará esmaecido e riscado, e o usuário poderá descobrir pelo texto acessível
que o motivo é `5G sem dado nesta métrica`.


## Entrega da implementação 2 — 2026-09-04, auditada e fechada em 2026-09-05

- Resolvedor compartilhado em `core/technology.py`: EP antes da medição e das heurísticas legadas.
- Novas importações exigem tecnologia válida e rejeitam duplicidades contraditórias antes de montar os sites.
- Coletor limita a associação por família e registra origem/falhas de compatibilidade, mantendo a tecnologia da task nas medições.
- Layout, status, filtros, portadoras, clusters, séries e Visão Geral usam a família da EP; séries incluem `cell_families`.
- Mapa, gráfico detalhado e editor de clusters do Servidor Central respeitam declarações explícitas.
- Template, README, instruções do operador e contrato de exportação atualizados.
- Sem migração ou regravação de KPIs históricos. A implementação 1 não foi ampliada nesta entrega.

### Correções da auditoria contra este plano (2026-09-05)

- **Três testes de Playwright quebrados** pelo bump de versão dos módulos: importavam `map.js` com
  a query de cache antiga e carregavam uma segunda instância, com o mapa nulo. Agora derivam o
  especificador de `app.js`.
- **Orçamento de leitura violado por CPU, não por consulta** (§"Riscos": *poll fica mais lento*).
  O índice do inventário era remontado a cada membro. `build_family_index(config)` foi separado de
  `annotate_rows(rows, index)` e o índice desce por parâmetro: série de cluster com 140 membros em
  evento de 1.500 sites, 4,1 s → 0,05 s. `_single_configured_family` foi içada de todos os laços
  (`_site_carriers` passou a receber a família resolvida): `get_sites` em evento legado de 1.500
  sites, 51,7 s → 0,49 s. Os dois casos ganharam teste de orçamento, validado por mutação.
- **Validação de importação severa demais** (§"Riscos": *EPs antigas deixam de importar*): a
  checagem cobrava tecnologia até das linhas que o parser descarta. Passou para o mesmo laço que
  monta os sites, cobrando só das linhas que viram célula, mantendo a rejeição do arquivo inteiro
  antes de devolver qualquer site.
- Órfãos criados pela mudança removidos (`server._normalize_technology`,
  `BaseCollector._normalize_cell_technology`) e imports de `core.technology` reposicionados.

Validação: suíte completa executada até o fim (**1.004 passed, 10 skipped**) — domínio,
importação, API (incluindo os orçamentos com 1.500 sites), coleta, exportação, banco, fórmulas,
contrato de empacotamento e Playwright. Não inclui conexão ao OSS real nem geração de um novo
executável de distribuição.
