# Plano de Construção — Projeto Smart Events

Este documento descreve o plano completo para a construção da plataforma **Smart Events**, baseado nos detalhes de arquitetura do [CLAUDE.md](file:///c:/Users/a50057663/Desktop/Automa%C3%A7%C3%B5es/SmartEvents/CLAUDE.md), nas diretrizes de design do [prototype.md](file:///c:/Users/a50057663/Desktop/Automa%C3%A7%C3%B5es/SmartEvents/design/prototype.md) e no protótipo visual contido em `SmartEvents Dashboard _Standalone_.html`.

O projeto é uma plataforma desktop de monitoramento de RF em tempo real para grandes eventos, utilizando **HTML, CSS e JS (ES Modules)** no frontend, **Python + PyWebView** para a casca desktop e **SQLite** como banco de dados local unificado.

---

## 1. Inventário e Aproveitamento de Rascunhos (`files/`)

A pasta `files/` contém rascunhos de altíssima qualidade de quase todos os arquivos do sistema. Eles cobrem a maior parte da lógica necessária e serão aproveitados quase que em sua totalidade, necessitando apenas de organização na estrutura de pastas, pequenos ajustes de caminhos e refinamentos de estilo.

Abaixo está o mapeamento detalhado de como cada rascunho será aproveitado:

### Backend (Python)
*   [main.py](file:///c:/Users/a50057663/Desktop/Automa%C3%A7%C3%B5es/SmartEvents/files/main.py) → **Aproveitamento: 95%**. Ponto de entrada do programa. Configura a janela do PyWebView, inicializa o banco SQLite e expõe a classe `Api`. Adicionaremos apenas um tratamento robusto de erros no encerramento de threads e um logo no console.
*   [models.py](file:///c:/Users/a50057663/Desktop/Automa%C3%A7%C3%B5es/SmartEvents/files/models.py) → **Aproveitamento: 100%**. Dataclasses limpas mapeando `Site`, `Cell`, `VIP`, `Thresholds` e medições.
*   [database.py](file:///c:/Users/a50057663/Desktop/Automa%C3%A7%C3%B5es/SmartEvents/files/database.py) → **Aproveitamento: 95%**. Gerencia tabelas, índices cruciais de séries temporais e conexão thread-local. O código está excelente e será movido diretamente para `core/database.py`.
*   [collector.py](file:///c:/Users/a50057663/Desktop/Automa%C3%A7%C3%B5es/SmartEvents/files/collector.py) → **Aproveitamento: 90%**. Contém a lógica do `CsvCollector` (Fase 1) e o esqueleto do `HttpCollector` (Fase 2). Precisamos apenas refinar as funções de decodificação das mensagens de RSRP/RSRQ dos traces do iManager.
*   [scheduler.py](file:///c:/Users/a50057663/Desktop/Automa%C3%A7%C3%B5es/SmartEvents/files/scheduler.py) → **Aproveitamento: 90%**. Executa a coleta em background threads (KPIs a cada 60s, VIPs a cada 15s) e avalia alertas. Adicionaremos tratamento para silenciar alertas recorrentes.
*   [api.py](file:///c:/Users/a50057663/Desktop/Automa%C3%A7%C3%B5es/SmartEvents/files/api.py) → **Aproveitamento: 95%**. Único ponto de contato Python ↔ JS. Garante sanitização de dados de VIPs (removendo IMSIs antes do envio ao JS). Será movido para `api/api.py`.
*   [requirements.txt](file:///c:/Users/a50057663/Desktop/Automa%C3%A7%C3%B5es/SmartEvents/files/requirements.txt) → Contém as dependências básicas (`pywebview`, `requests`, `watchdog`, etc.).

### Frontend (HTML/CSS/JS)
*   [index.html](file:///c:/Users/a50057663/Desktop/Automa%C3%A7%C3%B5es/SmartEvents/files/index.html) → **Aproveitamento: 90%**. Estrutura limpa do Shell do dashboard. Faremos a substituição das dependências via CDN por arquivos locais para permitir o uso 100% offline em ambientes sem acesso à internet.
*   [main.css](file:///c:/Users/a50057663/Desktop/Automa%C3%A7%C3%B5es/SmartEvents/files/main.css) → **Aproveitamento: 85%**. Contém os tokens CSS e estilo base. Faremos ajustes pontuais de alinhamento e as micro-animações pulsantes para o indicador de gravação (`REC`).
*   [app.js](file:///c:/Users/a50057663/Desktop/Automa%C3%A7%C3%B5es/SmartEvents/files/app.js) → **Aproveitamento: 95%**. Orquestrador do frontend, gerencia polling de 30s e transições de tela (Standby / Dashboard / Histórico).
*   [bridge.js](file:///c:/Users/a50057663/Desktop/Automa%C3%A7%C3%B5es/SmartEvents/files/bridge.js) → **Aproveitamento: 95%**. Wrapper seguro da API Python. Possui fallback automático para dados mock caso o frontend seja aberto diretamente no navegador.
*   [state.js](file:///c:/Users/a50057663/Desktop/Automa%C3%A7%C3%B5es/SmartEvents/files/state.js) → **Aproveitamento: 100%**. Gerenciamento de estado global com padrão pub/sub para evitar acoplamento circular de módulos.
*   [map.js](file:///c:/Users/a50057663/Desktop/Automa%C3%A7%C3%B5es/SmartEvents/files/map.js) → **Aproveitamento: 90%**. Desenha o mapa com Leaflet e os marcadores de setor/pétala baseados em SVG dinâmico.
*   [vip.js](file:///c:/Users/a50057663/Desktop/Automa%C3%A7%C3%B5es/SmartEvents/files/vip.js) → **Aproveitamento: 95%**. Renderiza os cards de VIPs com barras de intensidade de sinal e separação física por proximidade (dentro/fora do evento).
*   [kpi.js](file:///c:/Users/a50057663/Desktop/Automa%C3%A7%C3%B5es/SmartEvents/files/kpi.js) → **Aproveitamento: 90%**. Lista de sites dinâmica (jogando os críticos para o topo) e renderização do gráfico de linha temporal com Chart.js, linhas de limite e zonas de gap de coleta.
*   [alerts.js](file:///c:/Users/a50057663/Desktop/Automa%C3%A7%C3%B5es/SmartEvents/files/alerts.js) → **Aproveitamento: 95%**. Lida com as notificações do tipo toast no canto da tela e com a gaveta de alertas ativos.

---

## 2. Cronograma de Construção por Fases

A construção será dividida em **6 fases lógicas**. Cada fase é incremental e testável de forma independente.

### Fase 1: Setup do Ambiente e Estrutura de Diretórios
**Objetivo:** Criar o esqueleto do projeto e isolar as dependências offline do frontend.
1.  Criar a estrutura de pastas indicada no padrão crítico:
    *   `api/`
    *   `core/`
    *   `frontend/` (com `css/`, `js/` e `lib/` para as bibliotecas locais)
    *   `events/`
    *   `data/` (adicionar ao `.gitignore`)
2.  Criar o ambiente virtual (`.venv`) e instalar as dependências do `requirements.txt`.
3.  Baixar localmente os arquivos JS/CSS externos (Leaflet, Chart.js e Chart.js Annotation Plugin) e colocá-los na pasta `frontend/lib/`.
4.  Copiar o arquivo `events/sample_event.json` com configurações de exemplo.

### Fase 2: Banco de Dados e Modelos
**Objetivo:** Estabelecer a camada de persistência local em SQLite.
1.  Criar `core/models.py` copiando a estrutura de dataclasses do rascunho.
2.  Criar `core/database.py` e rodar a criação de tabelas e índices.
3.  Criar um script de testes simples na pasta `scratch/` para verificar se:
    *   As tabelas são geradas corretamente em `data/smart_events.db`.
    *   Os índices de série temporal funcionam.
    *   O isolamento de conexões por thread (`threading.local`) impede travamento em acessos simultâneos.

### Fase 3: Casca Desktop e Interface de Entrada (Python + PyWebView)
**Objetivo:** Expor os métodos Python ao JavaScript e abrir a janela nativa.
1.  Copiar o rascunho de `main.py` para a raiz.
2.  Copiar o rascunho de `api/api.py` (antigo `api.py`).
3.  Verificar o método de higienização de VIPs `_sanitize_event` para garantir que campos críticos (como IMSI) nunca vazem para o frontend.
4.  Testar a abertura da janela rodando `python main.py --mock --dev`.

### Fase 4: Coleta de Dados em Background (Scheduler e Collectors)
**Objetivo:** Implementar o loop de coleta periódica de KPIs e Traces de VIPs.
1.  Mover o rascunho de `collector.py` para `core/collector.py`.
2.  Mover o rascunho de `scheduler.py` para `core/scheduler.py`.
3.  Garantir o funcionamento do `MockCollector` em desenvolvimento para alimentar o painel de forma automática com desvios normais de comportamento.
4.  Implementar o monitoramento de arquivos na pasta de importações no `CsvCollector`:
    *   Processamento automático ao colar arquivos `kpi_*.csv` e `trace_*.csv`.
    *   Inserção em lote no SQLite via `insert_kpi_batch` e `insert_vip_batch`.
    *   Geração automática de alertas de limiar (`_evaluate_kpi_alerts` e `_evaluate_vip_alerts`).

### Fase 5: Frontend e Visualização
**Objetivo:** Montar a interface rica de monitoramento baseada no Design System escuro.
1.  Mover `index.html` para `frontend/index.html`. Ajustar os caminhos das tags `<link>` e `<script>` para as versões locais baixadas na Fase 1.
2.  Mover `main.css` para `frontend/css/main.css`.
3.  Mover os scripts JS (`app.js`, `bridge.js`, `state.js`, `map.js`, `vip.js`, `kpi.js`, `alerts.js`) para `frontend/js/`.
4.  Substituir o uso de `innerHTML` por escaping de strings (usando `_esc()`) em todos os componentes visuais para mitigar riscos de XSS.
5.  Ajustar o carregamento dinâmico do mapa do Leaflet: garantir que os polígonos sejam traçados na área do evento de forma pontilhada (#388BFD).
6.  Garantir a representação dos marcadores de site no mapa no formato de pétalas / setores direcionados (azimuths), aplicando cores baseadas nos thresholds configurados.
7.  Ajustar a exibição de gaps de coleta no gráfico temporal do Chart.js aplicando faixas hachuradas nos períodos sem registros.

### Fase 6: Testes Integrados e Homologação
**Objetivo:** Validar o fluxo ponta a ponta do sistema sob estresse e preparar o executável final.
1.  **Teste de Standby:** Executar sem evento ativo, conferir a exibição do card de carregamento e dos eventos agendados.
2.  **Fluxo de Importação manual (CSV):** Criar arquivos CSV fictícios baseados no formato iManager na pasta configurada no JSON do evento e confirmar o processamento automático, atualização instantânea do mapa e atualização de VIPs na barra lateral.
3.  **Homologação de Alertas:** Forçar medições com valores além do limite configurado (ex: utilização de site acima de 95% ou RSRP de VIP abaixo de -110 dBm) e validar a chegada do toast, atualização do badge do sino no header, inserção de alerta na gaveta e funcionamento do botão "OK" (ack) e silenciamento permanente da sessão.
4.  **Modo Histórico:** Testar a entrada no modo histórico, navegação temporal usando o slider inferior e verificação gráfica das quebras de linha nos gaps de coleta.
5.  **Geração do executável (.exe):** Utilizar `pyinstaller` para empacotar o projeto em uma aplicação portátil autônoma de arquivo único (`--onefile`).

---

## 3. Próximos Passos e Validação

Para iniciar a execução, as seguintes etapas devem ser seguidas de imediato:
1.  **Aprovação do Plano:** O plano deve ser aprovado pelo desenvolvedor/usuário.
2.  **Criação de Diretórios:** Inicializar as pastas físicas no workspace para receber os arquivos da pasta `files/`.
3.  **Movimentação Controlada:** Mover os arquivos mantendo o isolamento lógico das camadas conforme a arquitetura padrão em uma linha:
    `OSS/Trace (VPN cliente) → Collector (Python) → SQLite → Api.py → PyWebView → JS Frontend`
