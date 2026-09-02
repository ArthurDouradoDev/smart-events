# Fusão e validação de sites homônimos

## Regra ativa

Desde 1º de setembro de 2026, todos os sites com o mesmo nome normalizado são
unificados, independentemente da distância entre suas coordenadas. A normalização
é feita por `Api._normalize_site_name` e remove espaços nas extremidades,
diferenças entre maiúsculas/minúsculas e os prefixos de tecnologia reconhecidos
(`4G-`, `5G-`, `5D-`, `SD-` e `SR-`).

A fonte única dessa fusão é `Api._merged_sites`, em `api/api.py`. O site resultante:

- recebe o nome normalizado como id quando há mais de um membro;
- reúne células e famílias de tecnologia de todos os membros;
- mantém os ids originais em `members`;
- usa a média das coordenadas válidas dos membros;
- pertence ao evento quando ao menos um membro tem `is_event_site=true`.

A distância não é consultada e não é emitido aviso de divergência no fluxo ativo.

## Validação de 50 m preservada, mas inativa

O processo anterior continua salvo em dois métodos de `Api`:

- `_distance_clusters`: reproduz o agrupamento histórico por distância de até 50 m;
- `_validate_distant_homonyms`: identifica grupos de mesmo nome separados por mais
  de 50 m, considerando apenas os sites que pertencem ao polígono do evento.

`_validate_distant_homonyms` retorna os pares conflitantes e mantém o warning legado
`Sites homônimos não fundidos (coordenada > 50m)`. Nenhum fluxo de produção chama
essa rotina atualmente; portanto, ela não interfere na fusão nem gera warnings.

Para uma análise manual ou futura reativação, a chamada é:

```python
conflicts = Api._validate_distant_homonyms(config_do_evento)
```

Qualquer reativação no fluxo normal deve ser uma decisão explícita: a rotina pode
ser usada apenas como diagnóstico, sem voltar a impedir a unificação por nome.
Os testes correspondentes ficam em `tests/test_api.py`.

Os planos e registros históricos anteriores a esta decisão ainda descrevem a
distância como trava ativa; eles são mantidos como histórico e não representam a
regra vigente.
