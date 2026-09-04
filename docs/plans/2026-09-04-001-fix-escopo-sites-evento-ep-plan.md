# Plano — Correção do escopo de sites do evento versus inventário da EP

**Data:** 2026-09-04  
**Status:** planejado  
**Tipo:** correção funcional e endurecimento de contrato  
**Escopo:** cadastro de sites, APIs do desktop, coleta, portadoras, KPIs, VIPs, alarmes,
alertas, dashboard, Visão Geral, Servidor Central, exportação e testes

## Objetivo

Separar de forma explícita e consistente dois conjuntos que hoje são tratados como se fossem o
mesmo:

- **inventário da EP:** todos os sites importados, incluindo vizinhos usados como contexto;
- **escopo operacional do evento:** somente os sites marcados com `is_event_site != false` e os
  respectivos sites físicos fundidos, células, portadoras, KPIs, VIPs, alarmes e alertas.

A correção deve garantir que qualquer texto, contagem, filtro ou gráfico apresentado como sendo
“do evento” use exclusivamente o escopo operacional. Os vizinhos da EP continuarão preservados
para contexto geográfico, auditoria e exportação, mas não poderão contaminar indicadores,
agregações ou classificações de presença.

## Evidência que motiva a correção

Na configuração atual do Rock in Rio TIM existem 2.479 registros de site na EP, mas somente 69
estão marcados como pertencentes ao evento. Depois da fusão 4G/5G usada pelo dashboard, são 1.454
sites físicos na EP contra 37 sites físicos do evento.

No Rock in Rio Vivo existem 2.648 registros de site na EP, 30 marcados como pertencentes ao
evento, 1.550 sites físicos fundidos na EP e 23 sites físicos fundidos no evento.

O efeito mais visível está nas portadoras:

| Evento | Portadora | Contagem atual | Contagem do evento |
|---|---:|---:|---:|
| Rock in Rio TIM | 636666 | 1.026 sites / 3.026 células | 37 sites / 90 células |
| Rock in Rio TIM | 100 | 1.092 sites / 5.446 células | 32 sites / 312 células |
| Rock in Rio Vivo | 630000 | 1.198 sites / 3.628 células | 15 sites / 68 células |
| Rock in Rio Vivo | 3350 | 1.326 sites / 4.260 células | 15 sites / 186 células |

Também foi confirmado em dados persistidos que o filtro de alarmes classifica vizinhos da EP
como se estivessem no evento: 25 dos últimos 500 alarmes do Rock in Rio TIM e 81 dos 236 alarmes
do Rock in Rio Vivo carregados pela tela seriam marcados como `in_event`, embora os sites
resolvidos tenham `is_event_site=false`.

## Decisão sobre fases

A implementação será dividida em **três fases**.

A divisão é necessária porque existem três fronteiras de risco diferentes:

1. o contrato de domínio e as agregações de KPI precisam ser corrigidos sem perder a EP usada no
   mapa;
2. coleta, VIPs, alarmes e alertas possuem persistência e efeitos operacionais próprios;
3. as interfaces precisam passar a consumir o contrato corrigido sem voltar a implementar regras
   locais divergentes.

Cada fase deve terminar utilizável, testada e com um commit próprio. Não iniciar a fase seguinte
enquanto os testes e a validação manual da fase atual não estiverem aprovados.

## Decisões que valem para toda a implementação

### 1. `is_event_site` é a fonte de verdade

Depois que um evento é salvo, o pertencimento ao evento é definido por
`site.get("is_event_site", True) is not False`.

- Campo ausente continua significando `true`, preservando eventos legados.
- Coordenadas e polígono não recalculam pertencimento em tempo de consulta.
- O polígono pode ser usado para enquadramento, desenho e validação visual, mas não substitui a
  marcação persistida.
- A Visão Geral deixa de decidir pertencimento apenas por `pointInPolygon`.

### 2. EP e evento são escopos diferentes e devem ter nomes diferentes

Usar estes termos no código e na interface:

- `ep_sites` / “sites da EP”: inventário completo;
- `event_sites` / “sites do evento”: recorte operacional;
- `ep_neighbors` / “vizinhos da EP”: itens com `is_event_site=false`.

Evitar variáveis genéricas como `sites` quando o escopo não estiver evidente. APIs públicas que
aceitem mais de um escopo devem receber um parâmetro explícito e validado, nunca inferido pelo
chamador.

### 3. Fusão 4G/5G preserva a regra de pertencimento por site físico

Um site físico fundido pertence ao evento quando **ao menos um** de seus membros crus pertence ao
evento. Quando isso acontecer, todos os membros 4G/5G daquele site físico entram no escopo
operacional.

Essa regra preserva cobertura quando duas linhas gêmeas da EP divergem no preenchimento de
`is_event_site`. A divergência deve gerar diagnóstico, mas não pode remover silenciosamente uma
tecnologia do site físico.

O agrupamento e a normalização usados para essa decisão devem ser compartilhados pelo backend da
API e pelo coletor. Não duplicar novamente a regra em JavaScript ou em outra classe Python.

### 4. Escopo operacional é aplicado no backend

Filtros na interface são uma segunda barreira, não a fonte de correção. Contagens, membros de
portadora, células, séries e classificações de presença devem chegar corretos do Python.

Uma chamada direta à API para um site, célula ou portadora fora do evento deve devolver resultado
vazio ou erro de escopo previsível; ela não pode expor dados operacionais apenas porque o frontend
normalmente esconderia a opção.

### 5. Clusters manuais são intersectados com o evento em tempo de consulta

Não reescrever silenciosamente `event.clusters` nem apagar membros já salvos. Para uso operacional:

- considerar somente membros pertencentes aos sites físicos do evento;
- calcular `site_count` e `cell_count` sobre essa interseção;
- informar `excluded_ep_site_count` quando um cluster salvo referenciar vizinhos;
- manter o cadastro original intacto para auditoria e eventual correção manual.

### 6. Dados históricos não serão apagados

KPIs, alarmes, VIPs e alertas já persistidos para vizinhos permanecem no SQLite. A correção atua na
consulta e na apresentação:

- dados externos deixam de participar das telas e agregações operacionais;
- exportações completas continuam podendo incluí-los com classificação explícita;
- nenhuma migração destrutiva ou limpeza automática será executada.

### 7. Exportação completa continua preservando a EP

O pacote de dados do evento é também um artefato de auditoria. Portanto, configuração, sites e
células da EP continuam completos, com `is_event_site` presente. O manifesto e os resumos passam a
distinguir `ep_*` de `event_*` para não chamar inventário completo de escopo operacional.

### 8. Compatibilidade e cache

- A assinatura de APIs usadas pelo JavaScript pode ganhar `scope="event"`, mas o padrão deve ser o
  comportamento seguro: evento.
- O mapa pode pedir explicitamente `scope="ep"` para obter vizinhos.
- Chaves de cache devem incluir o escopo e a família tecnológica.
- Fallbacks de cache não podem devolver a EP inteira quando a chamada pediu evento.
- Mocks do bridge devem reproduzir a mesma separação.

### 9. O botão “Sites do evento” controla mapa e painel esquerdo

O botão “Sites do evento”, localizado sobre o mapa, deve ser a única fonte de verdade para a
visibilidade de sites no dashboard. O mesmo estado precisa controlar simultaneamente os marcadores
do mapa e os itens exibidos no painel esquerdo.

- Ao abrir ou trocar de evento, o botão começa ativo e somente os sites do evento aparecem no mapa
  e no painel esquerdo.
- Com o botão ativo, busca, ordenação, contagem e estado vazio do painel esquerdo consideram apenas
  os sites do evento.
- Ao desativar o botão, mapa e painel esquerdo passam juntos a mostrar a EP completa; vizinhos devem
  ser identificados visualmente como “fora do evento”.
- A alternância precisa ser atômica: nunca pode existir um estado em que o mapa esteja filtrado e o
  painel esquerdo não, ou vice-versa.
- Mostrar a EP completa é uma escolha de visualização e não muda o escopo de portadoras, KPIs,
  alarmes, alertas, cobertura ou clusters operacionais.
- O estado não deve ser reaproveitado silenciosamente entre eventos. Abrir outro evento restaura o
  padrão seguro de somente sites do evento.

## Critérios de aceite globais

Com um cenário mínimo contendo um site interno e um vizinho, ambos na mesma EARFCN:

1. a portadora deve informar `1 site` e somente as células do site interno;
2. a série da portadora deve ignorar medições do vizinho;
3. a lista, os resumos e a Visão Geral devem oferecer somente o site interno como escopo
   operacional;
4. ao abrir o evento, o botão “Sites do evento” deve estar ativo e tanto o mapa quanto o painel
   esquerdo devem mostrar somente o site interno;
5. ao desativar o botão, o vizinho deve aparecer simultaneamente no mapa e no painel esquerdo,
   claramente identificado como fora do evento e sem contaminar os KPIs;
6. alarme ou célula servidora do vizinho deve resultar em `in_event=false`;
7. o vizinho não deve gerar alerta de KPI ou RSRP do evento;
8. a cobertura do Monitoring deve usar somente as células operacionais;
9. a exportação deve manter os dois sites e declarar separadamente as contagens da EP e do evento.

---

## Fase 1 — Fonte única de escopo e correção de sites, portadoras e KPIs

### Explicação simples

Nesta fase será criada uma única regra de backend para responder “quais sites e células pertencem
ao evento?”. Portadoras, clusters, listas de células e séries de KPI passarão a usar essa regra.
A EP completa continuará disponível apenas quando um consumidor pedir explicitamente contexto da
EP.

### Escopo detalhado

#### Código

**Novo módulo de domínio, preferencialmente `core/site_scope.py`**

- Extrair ou compartilhar a normalização de nome físico hoje concentrada em `Api`.
- Criar funções puras e tipadas para:
  - decidir se uma linha crua está marcada como evento;
  - agrupar membros de um mesmo site físico usando exatamente a regra de fusão vigente;
  - obter grupos físicos da EP;
  - obter grupos físicos do evento pela regra OR;
  - obter ids crus, ids fundidos e ids de células do evento;
  - classificar um site/célula como `event` ou `ep_neighbor`;
  - detectar grupos físicos com marcações divergentes.
- As funções não devem alterar a configuração recebida.
- Manter o default legado: ausência de `is_event_site` equivale a `true`.

**`api/api.py`**

- Fazer `_build_merged_sites` reutilizar a regra compartilhada, sem mudar nomes, ids, ordem ou
  comportamento de fusão fora do escopo desta correção.
- Introduzir acesso explícito a `merged_ep_sites` e `merged_event_sites`; não usar
  `_merged_sites(config)` genericamente em cálculos operacionais.
- Tornar `get_site_layout`, `get_site_status` e `get_sites` conscientes de escopo:
  - `scope="event"` como padrão seguro;
  - `scope="ep"` apenas para o mapa e ferramentas de auditoria;
  - incluir o escopo nas chaves de `_sites_cache` e `_site_status_cache`.
- Fazer `_earfcn_clusters` percorrer somente os membros operacionais do evento.
- Fazer `_build_clusters`, `_cluster_raw_selections` e `_cluster_merged_site_ids` intersectarem
  clusters manuais com o escopo operacional quando usados por dashboard/KPI.
- Em `get_clusters`:
  - calcular `site_count` e `cell_count` somente sobre sites/células do evento;
  - manter `source`, `family`, seleção parcial e cores;
  - adicionar `excluded_ep_site_count` somente quando maior que zero;
  - não retornar portadora que exista apenas em vizinhos da EP.
- Fazer `_site_cluster_membership` ignorar associações externas no payload operacional.
- Fazer `get_event_cells` retornar somente células dos sites físicos do evento.
- Fazer `get_site_cells`, `site_carrier`, `cell`, `site` e `cluster` validarem o escopo no servidor.
- Rejeitar ou devolver vazio estável para ids de vizinhos, sem cair em lookup aproximado de outro
  site.
- Garantir que `get_kpi_series`, `get_kpi_overview` e `get_kpi_overview_multi` nunca agreguem uma
  seleção externa, mesmo se o chamador montar manualmente o payload.
- Para `user_count` e `traffic_volume_*`, calcular o denominador da participação somente com os
  sites operacionais do evento. Não calcular o total antes do recorte.
- Incluir nos metadados retornados, quando útil, `scope: "event"` e contagens explícitas para
  permitir asserts do frontend sem inferência.

**`frontend/js/bridge.js`**

- Acrescentar o parâmetro de escopo às chamadas afetadas.
- Atualizar mocks para possuir pelo menos um vizinho da EP compartilhando portadora com um site do
  evento.
- Fazer o mock falhar em teste se uma chamada operacional devolver o vizinho.

**Documentação técnica**

- Atualizar `docs/evento-cadastro-e-petalas.md`, `docs/coleta-de-dados.md` e
  `docs/smart-events.html` com a distinção entre EP e evento.
- Documentar a regra OR de sites físicos fundidos e o default legado.

#### Interface

- As contagens das opções de cluster e portadora do dashboard passam a representar somente sites
  e células do evento.
- Portadoras presentes apenas em vizinhos deixam de aparecer.
- Não alterar ainda o desenho geral dos seletores; a reorganização completa da experiência fica
  para a Fase 3.
- Se um cluster manual tiver membros externos, mostrar sua contagem operacional e um aviso discreto
  como “N vizinhos da EP ignorados”, sem expor esses vizinhos como séries selecionáveis.

#### Testes

**Novo `tests/test_site_scope.py`**

- `test_campo_ausente_continua_pertencendo_ao_evento`.
- `test_false_remove_site_do_escopo_operacional_sem_remove_lo_da_ep`.
- `test_poligono_nao_recalcula_pertencimento`.
- `test_site_fisico_misto_entra_por_or_e_preserva_4g_5g`.
- `test_grupo_misto_gera_diagnostico_sem_mutar_configuracao`.
- `test_ids_e_celulas_do_evento_nao_incluem_vizinhos`.

**`tests/test_api.py`**

- Fixture padrão com site interno e vizinho na mesma portadora.
- `test_get_sites_evento_exclui_vizinho_e_scope_ep_preserva`.
- `test_cache_de_evento_nunca_devolve_payload_da_ep`.
- `test_portadora_conta_somente_sites_e_celulas_do_evento`.
- `test_portadora_exclusiva_de_vizinho_nao_e_listada`.
- `test_serie_da_portadora_ignora_medicao_do_vizinho`.
- `test_cluster_manual_intersecta_membros_com_evento`.
- `test_cluster_informa_quantidade_de_vizinhos_ignorados`.
- `test_get_event_cells_nao_lista_celula_de_vizinho`.
- `test_escopo_direto_de_vizinho_nao_retorna_kpi` para site, célula e `site_carrier`.
- `test_participacao_de_volume_usa_denominador_dos_sites_do_evento`.
- Casos 4G, 5G e site físico fundido com marcações divergentes.

**Regressão**

- Manter os testes existentes de fusão de homônimos, seleção parcial, famílias tecnológicas,
  clusters manuais e portadoras.
- Atualizar asserts antigos que chamavam todos os registros da EP de “evento”.
- Não usar as EPs reais como fixture unitária; criar fixtures pequenas que reproduzam as mesmas
  proporções lógicas.

### Como validar e testar

#### Validação visual (`python main.py --mock`)

1. Abrir o mock contendo um site interno e um vizinho na mesma EARFCN.
2. Conferir no seletor de clusters que a portadora informa `1 site`, não `2`.
3. Selecionar a portadora e verificar que o gráfico contém somente a série/célula interna.
4. Trocar entre 4G e 5G e conferir que nenhuma portadora exclusiva do vizinho aparece.
5. Abrir um cluster manual contaminado e conferir a contagem operacional e o aviso de membro
   externo ignorado.
6. Trocar para modo histórico e repetir as mesmas contagens.

#### Validação com eventos reais

1. Rock in Rio TIM:
   - Portadora 636666 deve mudar de `1.026 sites / 3.026 células` para
     `37 sites / 90 células`.
   - Portadora 100 deve mudar de `1.092 / 5.446` para `32 / 312`.
2. Rock in Rio Vivo:
   - Portadora 630000 deve mudar de `1.198 / 3.628` para `15 / 68`.
   - Portadora 3350 deve mudar de `1.326 / 4.260` para `15 / 186`.
3. Comparar uma série de portadora antes/depois e confirmar que timestamps continuam alinhados e
   que somente os membros externos foram removidos.
4. Conferir que clusters manuais atualmente válidos mantêm as mesmas contagens e séries.

#### Suíte

```bash
pytest tests/test_site_scope.py tests/test_api.py -q
pytest tests/test_frontend_cluster_filter_ui.py tests/test_frontend_kpi_overview_ui.py -q
pytest tests/ -m "not vpn" -q
```

#### Gate da fase

- Nenhuma API operacional retorna vizinho em fixture com `is_event_site=false`.
- As quatro contagens reais acima batem exatamente.
- Nenhuma regressão nos testes de fusão, portadoras e gráficos.

### Sugestão de commit

```text
feat: scope sites carriers and KPIs to the event
```

---

## Fase 2 — Correção da coleta, presença, alarmes e alertas

### Explicação simples

Nesta fase o recorte correto será levado para o caminho operacional. O Monitoring esperará apenas
as células do evento, VIPs e alarmes só serão classificados como “no evento” quando resolverem
para um site operacional, e nenhum vizinho poderá gerar alerta de KPI ou RSRP do evento.

### Escopo detalhado

#### Código

**`core/collector.py`**

- Construir `site_ids`, `cell_ids`, `_cell_to_site_index`, `_cell_to_site`, `_cell_metadata`,
  `_static_obj_nos` e `_obj_to_cell` a partir dos membros operacionais fornecidos pelo módulo
  compartilhado da Fase 1.
- Respeitar a regra OR do site físico fundido: se um membro tornar o site físico operacional,
  preservar as células dos demais membros 4G/5G daquele grupo.
- Fazer `_cell_in_event` consultar o índice canônico em vez de percorrer toda a EP.
- Manter comparação exata, `obj_no`, identidades LTE/NR e nomes normalizados, mas somente dentro do
  índice operacional.
- Fazer `_expected_pm_cell_ids` e `cells_expected` refletirem apenas as células do evento.
- Fazer descoberta aberta do Monitoring descartar objetos resolvidos exclusivamente para
  vizinhos. Contabilizá-los separadamente como `ep_neighbor_objects_ignored`, não como
  `unmapped_objects`.
- Evitar que vizinhos ignorados façam a coleta ficar `partial`.
- Atualizar mensagens de log que dizem “células do evento” para usar o total operacional real.
- `MockCollector` deve escolher células de VIP e gerar KPIs/alarmes sintéticos somente dentro do
  escopo correspondente; vizinhos podem existir no mapa mock, mas não no conjunto operacional.

**`api/api.py` — VIPs e alarmes**

- `get_vips` e `get_vip_series` devem criar o resolvedor somente com sites físicos do evento.
- Quando uma célula pertencer a um vizinho conhecido da EP:
  - `in_event=false`;
  - `serving_site=null` no contrato operacional;
  - preservar um `serving_site_name` inferido ou `ep_neighbor_name` apenas como contexto textual;
  - nunca permitir que esse contexto seja usado para selecionar um site operacional.
- `get_alarms` deve resolver primeiro contra sites do evento.
- Opcionalmente resolver uma segunda vez contra a EP apenas para enriquecer o rótulo do vizinho,
  mantendo obrigatoriamente `in_event=false` e `serving_site=null`.
- O filtro “Somente sites do evento” deve depender do resultado operacional, não da simples
  existência do site na EP.
- Manter a coleta de alarmes da rede inteira; a correção é de classificação e apresentação, não de
  redução do endpoint FM.

**`core/scheduler.py` — alertas**

- Adicionar defesa de escopo antes de `_evaluate_kpi_alerts` inserir alertas.
- Ignorar medições de ids externos, inclusive dados históricos ou linhas inseridas por versões
  antigas.
- `_evaluate_vip_alerts` deve usar a classificação canônica atual, sem confiar exclusivamente no
  `in_event` persistido pelo coletor.
- Alertas `GLOBAL` de sessão, VPN e coleta continuam visíveis e não dependem de site.

**Persistência e leitura de alertas**

- Não apagar alertas antigos de vizinhos.
- Enriquecer alertas com `in_event`/`scope_role` em tempo de leitura.
- `get_alerts` deve devolver por padrão alertas globais e alertas dos sites do evento; um modo de
  auditoria/exportação pode pedir todos.
- Reconhecer, excluir e baixar logs deve deixar claro se opera sobre alertas visíveis ou sobre todo
  o histórico persistido.
- Preferência: ações do drawer operam sobre o conjunto operacional; a exportação completa preserva
  tudo.

**`core/event_export.py`**

- Preservar linhas históricas externas.
- Adicionar ou preencher `scope_role`/`is_event_site` nas linhas exportadas quando a associação for
  determinável.
- Incluir no manifesto:
  - `ep_site_count`;
  - `event_site_count`;
  - `ep_neighbor_site_count`;
  - `event_cell_count`;
  - quantidade de KPI, VIP, alarme e alerta classificada como externa.
- Não alterar o princípio de “todos os dados persistidos” do pacote.

#### Interface

- O painel VIP deve corrigir “N no evento · M fora”, agrupamento, coroa, barras de sinal e badges.
- Um VIP em vizinho pode mostrar o nome da estação como contexto, mas deve permanecer na seção
  “Fora do evento” e não focar/selecionar um site operacional.
- O drawer de alarmes deve corrigir:
  - resumo “N no evento · M na rede”;
  - checkbox “Somente sites do evento”;
  - badge crítico do cabeçalho;
  - clique para foco no mapa apenas quando houver `serving_site` operacional.
- O drawer de alertas deve mostrar somente alertas globais ou do evento e nunca contar vizinhos no
  badge.
- O modal de sincronismo deve exibir cobertura com o número de células do evento e, se houver,
  “N objetos de vizinhos ignorados” como informação, não como falha.

#### Testes

**`tests/test_collector.py` e `tests/test_kpi_monitoring.py`**

- `test_indices_do_coletor_excluem_vizinhos_da_ep`.
- `test_site_fisico_misto_preserva_as_duas_tecnologias`.
- `test_cobertura_espera_somente_celulas_do_evento`.
- `test_objeto_de_vizinho_e_ignorado_sem_tornar_coleta_parcial`.
- `test_cell_in_event_rejeita_match_exato_obj_no_prefixo_e_eci_de_vizinho`.
- `test_mock_collector_nao_gera_kpi_ou_vip_em_vizinho`.

**`tests/test_api.py`**

- `test_vip_em_vizinho_da_ep_fica_fora_do_evento`.
- `test_vip_series_preserva_rotulo_externo_sem_serving_site_operacional`.
- `test_alarme_de_vizinho_nao_e_marcado_in_event`.
- `test_alarme_de_vizinho_preserva_contexto_sem_permitir_foco`.
- `test_get_alerts_exclui_alerta_de_vizinho_e_preserva_global`.
- Testar homônimos, prefixos e ids numéricos para impedir falso positivo aproximado.

**`tests/test_scheduler.py`**

- `test_kpi_de_vizinho_nao_gera_alerta`.
- `test_vip_em_vizinho_nao_gera_alerta_rsrp`.
- `test_alerta_global_continua_visivel`.
- `test_linha_historica_com_in_event_antigo_e_revalidada`.

**Frontend**

- `tests/test_frontend_alerts_alarms_filter_ui.py`:
  - checkbox de alarmes exclui vizinho conhecido da EP;
  - resumo e badge usam somente sites do evento;
  - alerta global permanece;
  - clique em alarme externo não altera `State.selectedSite`.
- Teste de VIP com vizinho conhecido garantindo seção “Fora do evento”, ausência de coroa e de
  badge no mapa.

**Exportação**

- `tests/test_event_export.py` deve comprovar que dados externos continuam no pacote, marcados como
  externos, e que as contagens do manifesto fecham.

### Como validar e testar

#### Validação visual (`python main.py --mock`)

1. Configurar no mock um VIP com célula pertencente ao vizinho.
2. Conferir que ele aparece em “Fora do evento”, sem coroa, sem barra premium e sem badge no site.
3. Inserir um alarme para o vizinho:
   - com “Somente sites do evento” marcado, ele não aparece;
   - ao desmarcar, aparece como item da rede/EP, sem ser contado no evento;
   - clicar nele não seleciona um site operacional.
4. Inserir KPI acima do limiar no vizinho e confirmar que nenhum alerta de evento é criado.
5. Abrir o modal de sincronismo e verificar a cobertura apenas das células internas.
6. Exportar os dados e conferir que o vizinho continua presente e marcado como externo.

#### Validação com dados reais

1. Rock in Rio TIM: nos últimos 500 alarmes, os 25 resolvidos exclusivamente para vizinhos devem
   deixar de compor “no evento”.
2. Rock in Rio Vivo: os 81 alarmes externos devem deixar de compor “no evento”.
3. Confirmar que alarmes de sites internos continuam resolvendo e focando o mapa.
4. Comparar `cells_expected` com a soma de células operacionais:
   - TIM: 975 células cruas marcadas no evento antes da expansão de grupos físicos;
   - Vivo: 1.008 células cruas marcadas no evento antes da expansão de grupos físicos.
   O número final deve respeitar a regra OR dos grupos fundidos e ser registrado no teste/relatório
   da implementação.
5. Deixar a coleta rodar por ao menos três ciclos e confirmar ausência de falso `partial` causado
   apenas por vizinhos da EP.

#### Suíte

```bash
pytest tests/test_collector.py tests/test_kpi_monitoring.py tests/test_scheduler.py -q
pytest tests/test_api.py tests/test_frontend_alerts_alarms_filter_ui.py -q
pytest tests/test_event_export.py -q
pytest tests/ -m "not vpn" -q
```

Executar testes com VPN somente em ambiente autorizado:

```bash
pytest tests/test_http_vpn.py --vpn -q
```

#### Gate da fase

- Nenhum vizinho é classificado como `in_event` em VIP ou alarme.
- Nenhum vizinho gera alerta de KPI/RSRP.
- Cobertura do Monitoring não considera vizinhos ausentes da PM Task.
- Exportação preserva os dados sem reintroduzi-los nas telas operacionais.

### Sugestão de commit

```text
feat: enforce event scope in collection alarms and VIPs
```

---

## Fase 3 — Coerência visual no dashboard, Visão Geral e Servidor Central

### Explicação simples

Nesta fase todas as telas passarão a comunicar claramente quando mostram o evento e quando mostram
a EP. Ao abrir um evento, o dashboard mostrará somente os sites do evento. O botão “Sites do
evento”, sobre o mapa, controlará em conjunto os marcadores do mapa e os sites do painel esquerdo;
ao desativá-lo, os dois passarão a mostrar a EP completa sem ampliar o escopo operacional dos KPIs.
A Visão Geral deixará de aplicar um filtro apenas ao seletor de sites, e o Servidor Central exibirá
contagens separadas e impedirá que “Selecionar todos” inclua vizinhos em clusters operacionais.

### Escopo detalhado

#### Código

**`frontend/js/state.js`**

- Separar o estado em coleções explícitas, por exemplo:
  - `sites`: sites operacionais do evento;
  - `epSites` ou `mapSites`: inventário completo usado no mapa;
  - `epNeighbors`: derivado somente para apresentação;
  - `eventSitesOnly`: estado compartilhado do botão “Sites do evento”, inicializado como `true`;
  - `visibleSites`: coleção derivada usada pelo mapa e pelo painel esquerdo, sem duplicar o filtro.
- `siteCounts`, `selectedScopeIds` e qualquer helper operacional devem usar `sites`, nunca
  `epSites`.
- Nenhum vizinho pode entrar em `selectedScopeIds` por clique, restauração de estado ou cluster.
- Toda mudança de `eventSitesOnly` deve publicar uma única atualização de estado consumida pelo
  mapa e pelo painel esquerdo, evitando filtros ou flags locais independentes.

**`frontend/js/app.js`**

- Pedir o escopo correto para cada consumidor:
  - evento para status e gráficos;
  - EP para formar a coleção visual compartilhada entre mapa e painel esquerdo;
  - evento para histórico operacional.
- Separar o payload uma única vez ao carregar/trocar evento e tecnologia.
- Ao abrir ou trocar de evento, definir `eventSitesOnly=true` antes da primeira renderização para
  impedir que a EP completa pisque no mapa ou no painel esquerdo durante o carregamento.
- Não restaurar de `localStorage`, cache ou estado do evento anterior o modo de EP completa. O
  padrão de uma nova abertura é sempre “Sites do evento” ativo.
- `preferredInitialSiteId` deve escolher somente entre sites operacionais.
- Ao trocar de evento, purgar seleção de site, cluster, célula ou portadora que não exista no novo
  escopo operacional.
- Se o operador ativar “Sites do evento” enquanto um vizinho estiver selecionado no modo de
  contexto, limpar a seleção externa e retornar o foco ao evento sem escolher outro site
  arbitrariamente.

**`frontend/js/map.js`**

- Manter a possibilidade de mostrar vizinhos, mas tornar o contrato visual explícito.
- Estado inicial obrigatório: botão “Sites do evento” ativo e somente sites do evento visíveis.
- Manter o botão com o rótulo “Sites do evento” e representar seu estado de forma inequívoca
  (`aria-pressed`, `aria-checked` ou controle equivalente).
- O manipulador do botão deve alterar apenas `State.eventSitesOnly`; mapa e painel esquerdo devem
  reagir ao mesmo estado, sem chamadas ou regras de filtro independentes.
- Quando o botão for desativado, exibir a EP completa simultaneamente no mapa e no painel esquerdo.
- Vizinhos devem permanecer esmaecidos e com popup “Vizinho da EP — fora do evento”.
- Clicar em vizinho não deve alterar `State.selectedSite`, abrir gráfico operacional nem adicionar
  badge de VIP/alarme do evento.
- `fitToEvent` deve enquadrar o polígono/sites do evento por padrão, independentemente do tamanho
  total da EP. Desativar “Sites do evento” não deve reenquadrar automaticamente o mapa.

**`frontend/js/kpi.js`**

- A lista do painel esquerdo, sua busca, ordenação, contagem e estado vazio devem consumir
  `State.visibleSites`, ficando sincronizados com o botão “Sites do evento”.
- Escala de métrica, resumo saudável/crítico, séries e seletor operacional “Todos os sites” devem
  continuar consumindo somente `State.sites`, mesmo quando `visibleSites` contiver a EP completa.
- Ajustar o texto para “Todos os sites do evento”.
- Clusters e portadoras devem usar as contagens corrigidas do backend.
- Quando a EP completa estiver visível, renderizar vizinhos no painel esquerdo com identificação
  “Fora do evento” e sem ações que os incluam em KPI, cluster, portadora ou alertas do evento.

**`frontend/js/kpi_overview.js`**

- Remover a geometria como fonte de verdade de pertencimento.
- O filtro inicial deve consumir `is_event_site`/escopo já resolvido pelo backend.
- Preferência de UX: eliminar o checkbox redundante “Apenas sites do polígono”, pois a Visão Geral
  é operacional e deve sempre trabalhar com o evento.
- Se houver necessidade comprovada de auditoria, substituir por um modo explicitamente chamado
  “Incluir vizinhos da EP”, desmarcado por padrão e sem alterar agregações operacionais; não usar o
  termo “evento” para esse modo.
- `_scopeSites`, `_scopeClusters` e `_scopeCells` devem chegar todos do mesmo escopo.
- “Todos os clusters”, portadoras, site×portadora e células devem respeitar o mesmo recorte.
- `_cellScopeSiteIds` não pode reintroduzir vizinhos por `cluster_ids` de portadoras automáticas.
- Resumos “Nenhum no evento” devem consultar o conjunto filtrado, não o tamanho da EP.

**`server_frontend/index.html` e `server.py`**

- No upload, exibir separadamente:
  - registros/sites marcados como evento;
  - vizinhos da EP;
  - total da EP;
  - células operacionais e células totais.
- Nos cartões de evento, substituir “Sites: total” por rótulos inequívocos, por exemplo:
  “37 sites do evento · 1.417 vizinhos (1.454 na EP)”.
- Quando a tela usar registros crus e não sites físicos fundidos, escrever “registros da EP” para
  evitar comparar números de granularidades diferentes.
- Na seção “Clusters do Evento”:
  - a árvore e a busca devem listar por padrão somente sites operacionais;
  - “Selecionar todos” deve marcar somente sites/células do evento;
  - vizinhos já referenciados por cluster legado devem aparecer numa área de aviso/auditoria, não
    como seleção operacional ativa;
  - seleção por mapa e laço deve ignorar vizinhos;
  - o resumo deve informar membros externos ignorados quando existirem.
- O backend do Servidor Central deve fornecer ou validar as contagens; não confiar apenas numa
  contagem JavaScript divergente da regra Python.
- Ao salvar, preservar o cluster legado no primeiro carregamento, mas exigir ação explícita do
  operador para adicionar novamente um vizinho. Novos clusters não podem incluir vizinhos.

**Exportação e distribuição**

- Resumos de preview/pacote devem usar `ep_site_count` e `event_site_count`, não o campo ambíguo
  `sites`.
- Não alterar a lista efetivamente empacotada: a EP completa continua necessária para contexto e
  auditoria.

#### Interface

- Dashboard e Visão Geral mostram sempre informações operacionais do evento.
- Ao abrir um evento, o botão “Sites do evento” começa ativo; mapa e painel esquerdo exibem apenas
  o recorte do evento já na primeira renderização.
- Desativar o botão mostra a EP completa ao mesmo tempo no mapa e no painel esquerdo; ativá-lo
  novamente remove os vizinhos dos dois locais.
- Textos deixam claro “evento”, “vizinhos” e “EP”.
- Contagens exibidas em telas diferentes devem usar a mesma granularidade ou declarar quando são
  registros crus versus sites físicos fundidos.
- O operador não deve conseguir selecionar acidentalmente um vizinho para KPI, cluster, alerta ou
  foco operacional.
- Estados vazio, loading, histórico e troca de tecnologia devem manter a mesma regra.

#### Testes

**`tests/test_frontend_site_render.py` e `tests/test_frontend_collection_ui.py`**

- `test_dashboard_lista_somente_sites_do_evento`.
- `test_resumo_de_status_ignora_vizinhos`.
- `test_mapa_inicia_sem_vizinhos`.
- `test_botao_sites_do_evento_inicia_ativo_ao_abrir_evento`.
- `test_botao_sites_do_evento_sincroniza_mapa_e_painel_esquerdo`.
- `test_desativar_botao_expoe_vizinhos_no_mapa_e_painel_sem_alterar_resumo`.
- `test_abrir_outro_evento_restaura_filtro_padrao`.
- `test_primeira_renderizacao_nao_exibe_ep_completa`.
- `test_clique_em_vizinho_nao_seleciona_escopo_kpi`.
- `test_fit_inicial_ignora_tamanho_total_da_ep`.
- `test_troca_de_evento_purga_selecao_externa`.

**`tests/test_frontend_kpi_overview_ui.py`**

- Substituir o teste que usa apenas geometria por teste de `is_event_site`.
- `test_visao_geral_nao_lista_vizinho_mesmo_dentro_do_poligono`.
- `test_visao_geral_mantem_site_do_evento_mesmo_com_coordenada_fora`.
- `test_cluster_picker_e_site_picker_usam_o_mesmo_escopo`.
- `test_portadora_na_visao_geral_exibe_contagem_do_evento`.
- `test_selecionar_portadora_nao_expoe_celulas_de_vizinhos`.
- `test_todos_os_clusters_nao_marca_portadora_externa`.
- `test_site_carrier_so_existe_para_site_operacional`.

**`tests/test_server_frontend_clusters_ui.py` e `tests/test_server_frontend_polygon_map.py`**

- `test_cartao_separa_sites_do_evento_vizinhos_e_total_ep`.
- `test_arvore_de_cluster_lista_somente_sites_do_evento`.
- `test_selecionar_todos_nao_adiciona_vizinhos`.
- `test_lasso_e_clique_no_mapa_ignoram_vizinho`.
- `test_cluster_legado_com_vizinho_mostra_aviso`.
- `test_poligono_continua_usando_sites_marcados_sem_redefinir_pertencimento`.

**Mocks e acessibilidade**

- Cobrir `title`, `aria-label` e `aria-pressed`/`aria-checked` do botão “Sites do evento”.
- Garantir navegação por teclado e foco previsível nos seletores.
- Atualizar snapshots/asserts de textos antigos como “Todos os sites” e “Sites e células do
  evento”.

### Como validar e testar

#### Validação visual (`python main.py --mock`)

1. Abrir o evento mock:
   - botão “Sites do evento” está ativo desde a primeira renderização;
   - lista lateral contém somente sites do evento;
   - resumo saudável/crítico fecha com o total dessa lista;
   - mapa inicia enquadrado no evento e sem vizinhos.
2. Desativar “Sites do evento”:
   - vizinhos aparecem simultaneamente e sem atraso perceptível no mapa e no painel esquerdo;
   - os vizinhos aparecem esmaecidos no mapa e identificados como “Fora do evento” na lista;
   - resumo, clusters, portadoras e gráficos não mudam;
   - clicar num vizinho não abre KPI nem muda a seleção.
3. Ativar novamente “Sites do evento” e conferir que os vizinhos desaparecem simultaneamente do
   mapa e do painel esquerdo.
4. Abrir outro evento depois de deixar o botão desativado e confirmar que o novo evento abre com o
   botão ativo, sem exibir momentaneamente a EP completa.
5. Abrir a Visão Geral:
   - seletores de sites, clusters, portadoras e células fecham no mesmo escopo;
   - selecionar “Todos os clusters” não cria série externa;
   - contagens coincidem com o dashboard.
6. Abrir o Servidor Central:
   - upload e cartão do evento mostram evento/vizinhos/EP separadamente;
   - “Selecionar todos” no cluster não marca vizinhos;
   - mapa e cálculo automático do polígono continuam funcionais.
7. Repetir em modo histórico e após trocar 4G/5G.

#### Validação com eventos reais

1. Rock in Rio TIM:
   - dashboard apresenta 37 sites físicos operacionais;
   - o botão começa ativo e mapa/painel esquerdo apresentam somente os 37 sites do evento;
   - ao desativá-lo, a EP completa de 1.454 sites físicos aparece no mapa e no painel esquerdo;
   - nenhum dos 1.417 vizinhos altera resumo ou seleção.
2. Rock in Rio Vivo:
   - dashboard apresenta 23 sites físicos operacionais;
   - os demais aparecem no mapa e no painel esquerdo somente após desativar o botão.
3. Conferir que as contagens de portadoras batem com a Fase 1 nas duas telas.
4. Fazer busca por um vizinho conhecido:
   - com o botão ativo, ele não aparece no painel esquerdo nem no mapa;
   - com o botão desativado, ele aparece nos dois como “Fora do evento”;
   - ele continua ausente dos seletores operacionais da Visão Geral.
5. Editar e salvar um evento sem alterar clusters e confirmar que nenhum membro é perdido
   silenciosamente.

#### Suíte

```bash
pytest tests/test_frontend_site_render.py tests/test_frontend_collection_ui.py -q
pytest tests/test_frontend_kpi_overview_ui.py tests/test_frontend_cluster_filter_ui.py -q
pytest tests/test_server_frontend_clusters_ui.py tests/test_server_frontend_polygon_map.py -q
pytest tests/ -m "not vpn" -q
```

#### Gate da fase

- Nenhuma tela usa “do evento” para uma contagem da EP.
- O botão “Sites do evento” inicia ativo em toda abertura de evento.
- Mapa e painel esquerdo nunca divergem quanto ao conjunto de sites visíveis.
- Mostrar vizinhos altera somente a visualização contextual, sem ampliar o escopo operacional.
- Todos os seletores operacionais compartilham o mesmo conjunto de sites.
- Os números reais de TIM e Vivo permanecem coerentes entre dashboard, Visão Geral e Servidor
  Central.

### Sugestão de commit

```text
feat: align event site scope across all interfaces
```

---

## Validação final integrada

Depois das três fases, executar uma rodada única cobrindo todo o fluxo:

1. criar um evento novo com um site interno, um vizinho, uma portadora compartilhada e uma
   portadora exclusiva do vizinho;
2. salvar, sincronizar e abrir o evento no desktop;
3. conferir cadastro, mapa, lista, clusters, portadoras, células e gráficos;
4. simular KPI crítico, VIP e alarme nos dois sites;
5. conferir presença, badges, alertas e filtros;
6. alternar modo ativo/histórico e família 4G/5G;
7. exportar o pacote e reconciliar as contagens do manifesto;
8. reiniciar o aplicativo para validar caches, persistência e seleção inicial;
9. executar a suíte offline completa;
10. executar smoke com VPN somente no ambiente autorizado.

## Matriz mínima de não regressão

| Área | Deve usar evento | Pode usar EP completa |
|---|---:|---:|
| Lista e resumo de sites | Sim | Não |
| Status e participação de KPI | Sim | Não |
| Portadoras e clusters operacionais | Sim | Não |
| Séries e Visão Geral | Sim | Não |
| Cobertura do Monitoring | Sim | Não |
| Presença de VIP | Sim | Apenas rótulo contextual externo |
| Filtro de alarmes | Sim | Lista de rede quando filtro é desligado |
| Alertas operacionais | Sim | Apenas alertas globais |
| Mapa | Sim por padrão | Sim, opcionalmente como contexto |
| Cadastro/preview da EP | Contagem separada | Sim |
| Exportação completa | Classificação separada | Sim |

## Riscos e prevenção

- **Perder 4G ou 5G em site fundido:** cobrir grupo com flags divergentes e aplicar a regra OR antes
  de formar ids/células operacionais.
- **Cache cruzar EP e evento:** incluir `scope` em todas as chaves e testar chamadas alternadas.
- **Frontend esconder sem backend recortar:** testar APIs diretamente com ids externos forjados.
- **Clusters legados mudarem silenciosamente:** intersectar em consulta, informar excluídos e não
  reescrever a configuração automaticamente.
- **Dados históricos desaparecerem da auditoria:** filtrar telas, não apagar SQLite; testar o ZIP.
- **Alarmes aproximados casarem o site errado:** procurar primeiro no índice operacional e testar
  homônimos/prefixos/ids numéricos.
- **Contagens cruas e fundidas divergirem:** rotular a granularidade na interface e documentar a
  origem de cada número.
- **Desempenho piorar com dois escopos:** construir índices uma vez por digest da configuração e
  medir as EPs grandes antes/depois.

## Medições obrigatórias antes e depois

Registrar para os dois eventos grandes:

- tempo e tamanho de `get_site_layout(scope="ep")`;
- tempo e tamanho de `get_site_layout(scope="event")`;
- tempo de `get_clusters` e quantidade de portadoras;
- contagens de sites/células por portadora;
- tempo de resolução de 500 alarmes;
- `cells_expected`, `cells_mapped` e estado final da coleta;
- quantidade de marcadores inicialmente no DOM;
- tempo de abertura do dashboard e da Visão Geral.

A correção funcional não deve regredir de forma relevante os ganhos de desempenho implementados
no plano de 2026-09-02. Se a construção simultânea dos dois escopos aumentar o tempo de abertura,
cachear a visão derivada pelo par `(event_id, config_digest)` em vez de recalcular a cada chamada.

## Encerramento de cada fase

- Registrar em `MEMORY.md` a decisão consolidada, números antes/depois e resultado da suíte.
- Registrar em `ERRORS.md` qualquer tentativa falha, causa raiz e regra de prevenção.
- Não incluir arquivos de runtime, bancos, sessões ou EPs reais no commit.
- Revisar `git diff` e confirmar que cada commit contém somente o escopo da fase.
- Só considerar o plano concluído quando os critérios de aceite globais e a validação integrada
  tiverem sido executados.
