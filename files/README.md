# Smart Events

Plataforma desktop de monitoramento de RF em tempo real para grandes eventos.

## Stack

- **Frontend**: HTML + CSS + JavaScript (ES Modules)
- **Desktop**: PyWebView (janela nativa, sem servidor dedicado)
- **Backend**: Python 3.10+
- **Banco**: SQLite (arquivo local em `data/smart_events.db`)
- **Mapa**: Leaflet.js
- **Gráficos**: Chart.js

## Setup

```bash
# 1. Criar ambiente virtual
python -m venv .venv
.venv\Scripts\activate       # Windows
source .venv/bin/activate    # Mac/Linux

# 2. Instalar dependências Python
pip install -r requirements.txt

# 3. (Opcional) Instalar Playwright para automação futura
playwright install chromium
```

## Executar

```bash
# Produção (requer VPN do cliente ativa)
python main.py

# Desenvolvimento com dados mock (sem VPN)
python main.py --mock

# Desenvolvimento com DevTools aberto
python main.py --mock --dev
```

## Estrutura

```
smart-events/
├── main.py                  # Entrada PyWebView
├── requirements.txt
├── api/
│   └── api.py               # Métodos Python → JS (window.pywebview.api)
├── core/
│   ├── models.py            # Dataclasses
│   ├── database.py          # SQLite: schema + CRUD
│   ├── collector.py         # Coleta OSS/Trace (CSV fase 1, HTTP fase 2)
│   └── scheduler.py         # Polling em background thread
├── frontend/
│   ├── index.html
│   ├── css/main.css
│   └── js/
│       ├── app.js           # Orquestrador principal
│       ├── bridge.js        # Wrapper JS → Python API
│       ├── state.js         # Estado global (pub/sub)
│       ├── map.js           # Leaflet + marcadores de setor
│       ├── vip.js           # Painel VIP
│       ├── kpi.js           # Lista de sites + Chart.js
│       └── alerts.js        # Toasts + drawer de alertas
├── events/
│   └── sample_event.json    # Evento de exemplo (GP SP)
└── data/                    # Banco SQLite (gerado automaticamente)
```

## Carregar um evento

1. Iniciar o programa (`python main.py --mock` para dev)
2. Clicar em **Carregar evento**
3. Selecionar um arquivo `.json` de configuração de evento
4. O dashboard ativa automaticamente e inicia a gravação

## Formato do arquivo de evento

Veja `events/sample_event.json` como referência.
Campos obrigatórios: `id`, `name`, `sites`, `vips`, `thresholds`.

## Coleta de dados (Fase 1 — CSV)

1. No iManager, exportar a tabela de KPIs para `kpi_YYYYMMDD_HHMM.csv`
2. No Signaling Trace, exportar para `trace_YYYYMMDD_HHMM.csv`
3. Salvar ambos na pasta configurada em `oss.import_folder` do JSON do evento
4. O coletor detecta automaticamente e processa os novos arquivos

## Dependências externas (CDN)

Para ambientes sem internet, baixar e hospedar localmente:
- Leaflet 1.9.4: https://unpkg.com/leaflet@1.9.4/dist/
- Chart.js 4.4.0: https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/
- Chart.js Annotation: https://cdn.jsdelivr.net/npm/chartjs-plugin-annotation@3.0.1/dist/

Substituir os `<script src="...">` no `index.html` pelos caminhos locais.
