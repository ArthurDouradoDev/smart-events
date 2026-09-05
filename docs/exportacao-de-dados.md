# Exportação de dados do evento

O SmartEvents exporta os dados persistidos de um evento em um único arquivo ZIP. A geração ocorre
em segundo plano a partir de snapshots consistentes dos bancos SQLite, portanto a coleta pode
continuar enquanto o pacote é montado.

O acesso fica no indicador `REC` de um evento ativo ou `DADOS` de um evento histórico. A ação
`Exportar dados` permite escolher a organização dos KPIs e incluir ou não os dados pessoais de
VIPs. O arquivo final é gravado na pasta Downloads do usuário e nunca substitui uma exportação
existente.

## Conteúdo do ZIP

```text
smart-events_<evento>_<data>-bsb.zip
├── manifest.json
├── LEIA-ME.txt
├── dados/
│   ├── kpis.csv                         # modo consolidado e tecnologias juntas
│   ├── kpis/                            # presente quando há particionamento
│   ├── vips.csv
│   ├── alarmes.csv
│   └── alertas.csv
├── metadados/
│   ├── evento.json
│   ├── ep_normalizada.csv
│   ├── sites.csv
│   ├── celulas.csv
│   ├── clusters.csv
│   └── dicionario_de_dados.csv
└── auditoria/
    ├── checkpoints_de_coleta.csv
    └── resumo.json
```

Todos os CSVs usam UTF-8 com BOM, vírgula como separador e sempre possuem cabeçalho, mesmo quando
não há registros. Textos iniciados por caracteres interpretados como fórmula por planilhas são
neutralizados com apóstrofo. O manifesto informa quantas células foram neutralizadas por arquivo.

## Opções de organização dos KPIs

### Tempo

- `Consolidado`: todos os KPIs ficam no mesmo conjunto de arquivos.
- `Separar por dia`: cria partições usando a data no horário de Brasília. Por exemplo,
  `2026-08-21T02:59:59Z` pertence ao dia `2026-08-20`, enquanto `2026-08-21T03:00:00Z` pertence a
  `2026-08-21`.

### Tecnologia

- `Manter juntas`: não cria partição tecnológica.
- `Separar em 4G e 5G`: agrupa LTE/4G em `4g` e NR, NRCELL e NRDUCELL em `5g`.
- `Separar por tecnologia de coleta`: mantém separados `4g`, `5g`, `5g_nrcell` e
  `5g_nrducell`.

Tecnologias vazias ou não reconhecidas sempre vão para a partição `unknown`; nenhuma linha é
descartada. Quando tempo e tecnologia são combinados, os caminhos seguem estes exemplos:

```text
dados/kpis/kpis_4g.csv
dados/kpis/kpis_2026-08-20.csv
dados/kpis/2026-08-20/kpis_5g.csv
dados/kpis/data-invalida/kpis_unknown.csv
```

## Horários

O timezone contratual é sempre `America/Sao_Paulo`, independentemente da configuração do Windows.
Cada registro temporal exporta o valor normalizado em UTC, o valor convertido para Brasília e a
data de Brasília usada no particionamento. Timestamps históricos sem offset são interpretados como
UTC para preservar a convenção usada pela coleta. Um timestamp inválido não elimina a linha: o
valor original permanece e um aviso agregado é incluído no manifesto.

## Schemas principais

### `dados/kpis*.csv`

```text
timestamp_utc, timestamp_brasilia, data_brasilia, event_id,
site_id, site_name_ep, cell_id, cell_id_ep, cell_name_ep,
metric, value_stored, value, unit, oss_unit, conversion_applied,
scope, technology, technology_family, technology_ep,
frequency_mhz, earfcn, is_event_site
```

`value_stored` é o valor exato persistido no SQLite. `value` é sua conversão para a unidade
canônica usada pelo aplicativo; `oss_unit`, `unit` e `conversion_applied` deixam essa transformação
explícita.

### `dados/vips.csv`

```text
timestamp_utc, timestamp_brasilia, data_brasilia, event_id,
vip_id, vip_name, task_id, serial_no, serving_cell, rsrp, rsrq, in_event
```

Esse arquivo contém dados pessoais e só recebe linhas quando `Incluir VIPs` está marcado. A seleção
é feita estritamente por `event_id`. O cadastro de VIPs e seus vínculos não são inferidos a partir
de outro evento.

### `dados/alarmes.csv`

Contém todos os alarmes persistidos do evento, sem o limite de exibição da interface. Preserva as
colunas de origem (`csn`, identificadores, severidade, origem, localização, horários e informações
adicionais) e acrescenta `collected_at_utc`, `collected_at_brasilia` e `data_brasilia`.

### `dados/alertas.csv`

Contém alertas reconhecidos e não reconhecidos, com seus identificadores, nível, severidade, site,
célula, mensagem, timestamp e estado `acknowledged`. Acrescenta as representações UTC e Brasília.

### Metadados e EP

`metadados/ep_normalizada.csv` tem uma linha por célula com:

```text
source_row, enodebid, nename, cellid, cellname, latitude, longitude,
azimuth, technology, frequency_mhz, dlearfcn, is_event_site, clusters, source_quality
```

Novos eventos preservam separadamente os identificadores normalizados recebidos da EP e usam
`source_quality=preserved`. Eventos antigos são reconstruídos a partir da configuração de sites e
células, com `source_quality=reconstructed`. As mesmas informações enriquecem os CSVs de KPI sem
remover medições cuja célula não esteja na EP.

`metadados/evento.json` contém a configuração do evento capturada no snapshot, mas remove URL do
OSS, pasta de importação, usuário, senha, token e cookies. Os CSVs de sites, células e clusters
facilitam consumo tabular; `dicionario_de_dados.csv` registra as principais definições.

## Manifesto e integridade

`manifest.json` é o contrato auditável do pacote. Ele registra versão do schema, evento, opções,
timezone, instantes dos snapshots, contagens, período dos KPIs, qualidade da EP, avisos e, para cada
arquivo de conteúdo, tamanho e SHA-256. Para CSVs também registra quantidade de linhas e células
neutralizadas.

No PowerShell, um hash pode ser conferido depois de extrair o ZIP:

```powershell
Get-FileHash -Algorithm SHA256 .\dados\kpis.csv
```

No Linux ou macOS:

```bash
sha256sum dados/kpis.csv
```

O valor calculado deve ser igual ao campo `files["dados/kpis.csv"].sha256` do manifesto. Antes de
publicar o ZIP, o próprio SmartEvents relê o pacote e valida versão, caminhos internos e todos os
hashes.

## Escopo e limitações

O pacote inclui tudo o que foi persistido para o evento nos conjuntos de KPIs, VIPs (opcional),
alarmes, alertas e checkpoints, além da configuração e da EP normalizada. Não inclui credenciais,
cookies, URL interna do OSS, paths locais, arquivos temporários nem respostas brutas do OSS que não
tenham sido gravadas no banco.

O arquivo original usado para importar a EP não é retido pelas versões atuais; por isso a
exportação fornece sua representação normalizada, não a planilha original. Registros coletados por
versões antigas podem não ter `vip_id` ou campos EP preservados, e esses casos permanecem vazios ou
marcados como reconstruídos em vez de receber valores inventados.

Falha ou cancelamento remove o staging e não publica ZIP parcial. O status da última exportação
concluída só é apresentado enquanto o arquivo final ainda existir no computador.


## Família autoritativa da EP

Em `kpis.csv`, `technology` mantém o tipo da task (`4G`, `5G_NRCELL` ou
`5G_NRDUCELL`), inclusive para escolher a conversão de unidade. `technology_ep`
contém a declaração do inventário e `technology_family` prefere essa declaração,
localizando a célula por site e identificador. Sem declaração, usa a tecnologia
persistida e depois o fallback legado. Assim, uma medição histórica de task 5G
associada a uma célula declarada 4G mantém `technology=5G_NRCELL` e apresenta
`technology_family=4G`. A exportação não modifica o banco nem as fórmulas.
