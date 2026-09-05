# Resumo da implementação 2 — Tecnologia da EP

Implementação 2 concluída no worktree, em duas etapas: a construção inicial e uma auditoria
contra o plano que corrigiu quatro pendências antes do fecho.

## Alterações principais

- Criado [`core/technology.py`](../core/technology.py), centralizando normalização, precedência da EP, fallback legado, origem da classificação e conflitos entre task e EP.
- Atualizado [`server.py`](../server.py): novas EPs exigem a coluna `tecnologia`, aceitam `4G`, `LTE`, `5G` e `NR`, rejeitam valores inválidos e duplicidades conflitantes e persistem a forma canônica.
- Atualizado o coletor para limitar tasks à família compatível, preservar a distinção entre `NR Cell` e `NR DU Cell` e registrar conflitos e classificações legadas.
- Atualizadas API, fusão de sites, filtros, clusters, portadoras, séries e Visão Geral para priorizar a família declarada pela EP.
- Séries passaram a incluir `cell_families` para o frontend.
- Atualizados mapa, gráfico e Servidor Central para respeitar `family`/`tech` explícitos e usar heurísticas somente como compatibilidade.
- Atualizada a exportação para manter separados `technology` da task, `technology_ep` da EP e `technology_family` resolvida.
- Atualizado [`ep_default.xlsx`](../ep_default.xlsx) com a coluna `tecnologia` e exemplo `4G`.
- Atualizados README, instruções do operador e documentação de exportação.
- Adicionados testes unitários, de API, coleta, importação, exportação e Playwright.

## Correções da auditoria

1. **Testes de Playwright quebrados pelo bump de versão dos módulos.** Três testes importavam
   `map.js` com a query de cache antiga escrita à mão e passaram a carregar uma segunda instância
   do módulo, com o mapa interno nulo. Agora derivam o especificador do próprio `app.js`.
2. **Inventário remontado por membro.** `annotate_rows` recebia o `config` e reconstruía o índice
   do evento a cada chamada, inclusive dentro dos laços de membros de cluster. Foi separado em
   `build_family_index(config)` + `annotate_rows(rows, index)`, com o índice montado uma vez por
   chamada de API. Série de cluster com 140 membros em evento de 1.500 sites: 4,1 s → 0,05 s.
3. **Fallback de família custando O(inventário) dentro de laços.** `_single_configured_family`
   passou a varrer todas as células e continuava sendo chamada por célula e por site. Todas as
   chamadas foram içadas, e `_site_carriers` passou a receber a família já resolvida.
   `get_sites` em evento legado de 1.500 sites: 51,7 s → 0,49 s.
4. **Validação da EP severa demais.** A checagem rodava num laço próprio e cobrava tecnologia até
   de linhas que o parser descarta (sem coordenada, sem id), o que faria uma EP válida com sobras
   na planilha parar de importar. A validação foi movida para o mesmo laço que monta os sites e
   só cobra das linhas que viram célula; o arquivo continua rejeitado por inteiro, antes de
   devolver qualquer site.

Também foram removidos os órfãos que a mudança criou (`server._normalize_technology`,
`BaseCollector._normalize_cell_technology`), fundida a varredura duplicada de diagnóstico no
coletor e reposicionados os imports de `core.technology` no grupo local de cada módulo.

## Comportamento resultante

- Uma declaração `tecnologia=4G` permanece 4G mesmo que o nome sugira 5G ou a banda seja 3500.
- Uma declaração `tecnologia=5G` permanece 5G mesmo que o nome seja neutro ou a banda seja 2100.
- Tasks de família incompatível não são associadas à célula e geram diagnóstico.
- Eventos antigos sem tecnologia continuam abrindo pelo fallback legado, com origem observável no log/metadados.
- KPIs históricos não são regravados nem reclassificados no banco.

## Validação

- Suíte completa executada até o fim: **1.004 passed, 10 skipped**, exit code 0.
- Testes de orçamento novos/ampliados: contagem de construções do índice na série de cluster e
  `get_sites` parametrizado por nome de célula (resolvível e neutro).
- Ambas as correções de desempenho foram confirmadas por mutação: revertendo o código, o teste
  correspondente falha.
- Fora da suíte: medições de tempo em evento sintético de 1.500 sites, citadas acima.
- Não inclui conexão ao OSS real nem geração de executável de distribuição.

As alterações permanecem locais e ainda não foram commitadas.
