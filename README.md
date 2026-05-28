# 🛰️ SmartEvents — Monitoramento de RF em Tempo Real para Grandes Eventos

O **SmartEvents** é uma plataforma de monitoramento de radiofrequência (RF) em tempo real projetada para equipes de NOC (Network Operations Center) otimizarem e garantirem a qualidade da rede móvel durante grandes eventos (GPs de Fórmula 1, festivais de música, grandes shows e finais de campeonatos).

O sistema monitora a saúde das células locais, rastreia os níveis de cobertura de usuários de alto nível (VIPs) e gera alertas automáticos baseados em limites críticos de qualidade e capacidade.

---

## ⚙️ Arquitetura e Fluxo de Dados

A plataforma foi construída em duas partes principais: um **Cliente Desktop Portátil** (com interface nativa e banco local SQLite) e um **Servidor Central de Sincronização**:

```
[Fontes de Dados]
  ├── Coleta CSV Local: Arquivos de KPI (kpi_*.csv) ─────────────────┐
  ├── Coleta HTTP REST (iManager REST API):                           ├─> [Collector (Python)] ──> [SQLite Local] ──> [Api.py] ──> [PyWebView] ──> [JS Frontend]
  │     ├── KPIs por objNo (/pm/v1/monitor/task/result)             │
  │     └── Trace de VIPs por taskId (/fars/v1/traceresult)          │
  └── Servidor Central (FastAPI): Eventos, VIPs e delimitação área ──┘
```

1. **OSS/Trace/HTTP REST**: Dados de telemetria coletados por arquivos CSV locais (Fase 1) ou consultados diretamente no iManager do cliente via chamadas HTTP REST com tratamento automático de expiração e renovação de sessão via script Playwright em background (Fase 2).
2. **Collector & Scheduler**: Duas threads em segundo plano no cliente. A thread de KPI faz requisições a cada 120s e a de VIP/Trace faz a cada 60s.

3. **SQLite**: Banco local central (`smart_events.db`) para metadados globais e bancos locais dinâmicos por evento (`smart_events_<event_id>.db`) que armazenam as séries temporais de métricas e alarmes de forma isolada.
4. **Api & PyWebView**: Ponte Python-JS segura que gerencia dados (sanitizando informações confidenciais de IMSI) e renderiza a janela nativa.
5. **JS Frontend**: Interface moderna e offline construída sem bundlers (Vanilla JS + ES Modules + CSS Custom Properties).

---

## 📂 Estrutura de Diretórios e Arquivos

Abaixo está o mapa completo da organização do projeto:

```
SmartEvents/
├── main.py                  # Ponto de entrada do executável Desktop (cria janela PyWebView e injeta Api)
├── server.py                # Servidor API central FastAPI para coordenação de eventos na rede
├── requirements.txt         # Arquivo de dependências Python do projeto
├── requirements.txt         # Arquivo de dependências do Python
│
├── api/
│   └── api.py               # Classe Api que faz a comunicação e serialização segura entre Python e JavaScript
│
├── core/
│   ├── models.py            # Dataclasses de domínio (Site, Cell, VIP, EventConfig, Alert)
│   ├── database.py          # Conexão e queries SQLite (isolamento de concorrência com thread-local)
│   ├── collector.py         # Drivers de coleta de dados (CSV iManager, HTTP REST e Mock)
│   └── scheduler.py         # Threads de background que agendam coletas e analisam limites para gerar alertas
│
├── frontend/                # Interface do Cliente Desktop
│   ├── index.html           # Shell HTML5 que carrega os scripts locais (preparado para funcionamento offline)
│   ├── css/
│   │   └── main.css         # Design system escuro (Dark Mode premium, variáveis CSS e transições)
│   └── js/
│       ├── app.js           # Orquestrador da interface (ciclo de vida da tela, inicialização e polling)
│       ├── bridge.js        # Wrapper da API Python (com fallback automático de mocks no navegador)
│       ├── state.js         # Estado global reativo com padrão Publish/Subscribe
│       ├── map.js           # Integração com Leaflet e plotagem geométrica de pétalas/setores via SVG
│       ├── vip.js           # Painel direito com status de sinal e localização dos VIPs
│       ├── kpi.js           # Painel inferior com tabelas de sites e gráficos temporais via Chart.js
│       └── alerts.js        # Gerenciamento de gaveta de alarmes e exibição de alertas popup (Toasts)
│
├── events/
│   └── sample_event.json    # JSON modelo de cadastro/configuração de evento
│
├── server_frontend/
│   └── index.html           # Interface web do Servidor Central para cadastrar e exportar eventos JSON
│
├── server_data/
│   └── events/              # Banco de arquivos JSON de eventos gerenciados pelo Servidor Central
│
├── data/                    # Diretório gerado em tempo de execução (bancos SQLite locais e cache)
│   ├── smart_events.db      # Banco de dados SQLite central criado automaticamente (IGNORAR no git)
│   └── smart_events_*.db    # Bancos de dados SQLite específicos de cada evento (IGNORAR no git)
│
├── design/                  # Documentações visuais de design e protótipo estático standalone
│   └── prototype.md         # Documento com diretrizes visuais e especificações de estilo
│
├── docs/                     # Arquivos adicionais de documentação
│   ├── evento.md            # Guia das fórmulas de plotagem das pétalas e especificações do JSON
│   ├── guiavm.md            # Guia passo a passo para instalar o servidor em uma VM Linux no VirtualBox
│   └── plano.md             # Plano de construção detalhado do projeto
│
└── download_page_assets.py  # Script auxiliar para baixar recursos externos e criar versões offline
```

*(Arquivos de documentação mapeados para referência: [evento.md](file:///c:/Users/a50057663/Desktop/Automa%C3%A7%C3%B5es/SmartEvents/evento.md), [guiavm.md](file:///c:/Users/a50057663/Desktop/Automa%C3%A7%C3%B5es/SmartEvents/guiavm.md) e [plano.md](file:///c:/Users/a50057663/Desktop/Automa%C3%A7%C3%B5es/SmartEvents/plano.md))*

---

## 🌟 Funcionalidades e Recursos

### 🖥️ Cliente Desktop (Dashboard & Monitoramento)
- **Sincronização com Servidor Central**: Busca e atualiza automaticamente eventos e cadastros de VIPs do servidor central. As configurações de `server_url` ficam salvas localmente em `data/settings.json`.
- **Seleção Dinâmica de Eventos**: Menu dropdown no header agrupa os eventos locais por status (Ativos, Agendados e Históricos) facilitando a navegação.
- **Modo Histórico (Timeline Playback)**: Permite inspecionar coletas gravadas de eventos passados com um slider temporal integrado, desativando as chamadas em tempo real e habilitando a navegação por snapshots históricos.
- **Ergonomia Visual**: O timer progressivo do evento em execução é ocultado de forma automática no header caso a duração do evento seja maior que 7 dias.
- **Visualização de Células (Pétalas SVG)**: Plotagem de setores dinâmicos no mapa Leaflet. A escala do zoom adapta o tamanho em pixels dos marcadores de setor, garantindo clareza na visualização das frequências e tecnologias (3G/4G/5G).
- **Acompanhamento e Destaque de VIPs**: Painel direito exibindo status de sinal (RSRP/RSRQ), indicação de presença na área do evento (`in_event`) e barra visual de intensidade. Se o VIP estiver conectado a um site do evento (`serving_site`), o card dele ganha destaque visual premium dourado com uma coroa (`👑`), o site correspondente no mapa Leaflet ganha um badge "V" dourado e a listagem de presença de VIPs é inclusa no popup do mapa. Ao clicar em um VIP (dentro ou fora do evento), abre-se um modal de detalhes com seu histórico em gráfico RSRP e informações de sua conexão mais recente, como o nome do último site conectado / site atual (com suporte a mapeamento de IDs de células compostas e decodificação de identidades globais LTE/NR) e a data/hora do último registro. O clique em um card de VIP também centraliza e aproxima (zoom) o mapa no site conectado, abrindo seu respectivo popup e selecionando-o na lista de sites. Além disso, se houver um VIP ativo conectado a um site do evento, um emoji de coroa (`👑`) é exibido ao lado do nome do site na lista inferior (KPI Panel).
- **Gráfico de KPIs Reativo com Seleção de Célula e Popup de Site Completo**: Exibição em linha temporal (Chart.js) das principais métricas das ERBs do site selecionado. Ao selecionar a métrica de "Site completo" (com curvas individuais para cada célula), o gráfico é exibido automaticamente de forma expandida em um popup premium que cobre 80% da tela (sendo esta a única forma de visualização do site completo para melhor leitura). Além disso, um botão de expansão no canto superior direito do painel de gráficos permite exibir qualquer gráfico de célula individual nesse mesmo popup. Exibe linhas de threshold (limiares de alerta) e realce de períodos sem coleta de dados (gaps de tempo > 90 segundos).
- **Valores Contextuais e Participação (Share) na Lista de Sites**: A listagem de sites adapta sua coluna de valores e cabeçalho dependendo da métrica escolhida. Para métricas de volume (como usuários ou tráfego), os sites são automaticamente ordenados pela sua participação percentual (share %) em relação ao total do evento. Para métricas de cobertura (RSRP/RSRQ) ou throughput, exibe as médias ou piores valores de células de forma contextualizada.
- **Tratamento de Alertas**: Gaveta lateral de alarmes com opção de confirmação (acknowledge) ou silenciamento por chave única (para evitar spam visual).
- **Exclusão Segura de Histórico**: O indicador interativo de gravação ("REC X MB") ativa um fluxo com modal de dupla confirmação para apagar medições locais antigas do evento ativo, seguido por uma rotina de `VACUUM` para reclamar espaço físico no SQLite.

### 🌐 Servidor Central de Sincronização
- **FastAPI Core**: Provê sincronização REST centralizada para múltiplos terminais de monitoramento na mesma rede local.
- **Delimitação da Área do Evento**: Tela com mapa Leaflet integrado contendo:
  - **Cálculo Automático de Polígono**: Criação de uma bounding box englobando todas as ERBs importadas, com controle de padding ajustável (100m a 2500m) por controle deslizante (slider).
  - **Desenho Manual**: Permite clicar no mapa para definir vértices de polígonos irregulares personalizados.
  - Contador de vértices do polígono.
- **Importador de Sites inteligente**: Upload e processamento automático de planilhas de ERBs nos formatos Excel (`.xlsx`, `.xls`), CSV, TSV e texto plano, normalizando aliases de colunas (`enodebid`, `cellid`, `latitude`, `longitude`, `azimuth`) e agrupando células sob seus respectivos sites.
- **Cadastro Global de VIPs**: Gerenciamento de VIPs (nome, cargo, task_id do trace) centralizado, facilitando a atribuição a múltiplos eventos simultâneos.

---

## 🚀 Como Executar o Projeto

### 1. Preparação do Ambiente Local
Certifique-se de possuir o **Python 3.10+** instalado em seu computador.

1. Abra o terminal na pasta raiz do projeto.
2. Crie um ambiente virtual Python:
   ```bash
   python -m venv .venv
   ```
3. Ative o ambiente virtual:
   - **No Windows (PowerShell):**
     ```powershell
     .venv\Scripts\Activate.ps1
     ```
   - **No Windows (Prompt cmd):**
     ```cmd
     .venv\Scripts\activate.bat
     ```
   - **No Linux/macOS:**
     ```bash
     source .venv/bin/activate
     ```
4. Instale as dependências:
   ```bash
   pip install -r requirements.txt
   ```

---

### 2. Rodando o Cliente Desktop (`main.py`)
O cliente desktop pode ser executado em três modos diferentes, dependendo da necessidade:

*   **Modo Produção**: Utilizado em campo, conectado à VPN do cliente de onde receberá arquivos CSV reais em tempo real.
    ```bash
    python main.py
    ```
*   **Modo Desenvolvimento com Mocks**: Popula o banco com histórico realista gerado de forma sintética para demonstrar as funcionalidades e charts sem necessitar de arquivos externos.
    ```bash
    python main.py --mock
    ```
*   **Modo Desenvolvimento com DevTools**: Abre a janela nativa integrada com o console de desenvolvedor (DevTools) habilitado, facilitando a inspeção de elementos e logs do console.
    ```bash
    python main.py --mock --dev
    ```

> [!TIP]
> **Desenvolvimento Rápido do Frontend:** Você também pode abrir o arquivo `frontend/index.html` diretamente em qualquer navegador padrão. O script [bridge.js](file:///c:/Users/a50057663/Desktop/Automa%C3%A7%C3%B5es/SmartEvents/frontend/js/bridge.js) identificará a ausência do PyWebView e mudará automaticamente para o modo de simulação no frontend, permitindo ajustar layouts CSS e lógicas JavaScript rapidamente.

---

### 3. Rodando o Servidor Central de Sincronização (`server.py`)
O servidor central serve para que múltiplos computadores rodando o SmartEvents Desktop sincronizem as configurações do evento ativo na mesma rede.

1. Ative o ambiente virtual e execute o servidor:
   ```bash
   python server.py
   ```
2. O servidor iniciará por padrão na porta `8000`. Acesse a página web em:
   - **Localmente:** `http://localhost:8000`
   - **Na rede local:** `http://<IP_DO_SERVIDOR>:8000`

Na interface do servidor, é possível cadastrar novos eventos de forma visual ou importando arquivos JSON. Ao inicializar, o Cliente Desktop realiza chamadas automáticas para sincronizar esses eventos localmente.

---

### 4. Configuração em VM Linux (VirtualBox)
Para implantar o Servidor Central de forma permanente em um ambiente de produção local:
1. Crie uma VM rodando Ubuntu Server no VirtualBox.
2. Defina a placa de rede em modo **Placa em Ponte (Bridge Adapter)** para que ela ganhe um IP próprio na rede física.
3. Transfira os arquivos do servidor e configure a execução automática utilizando o gerenciador de serviços do Linux `systemd`.
4. Um passo a passo completo e detalhado com todos os comandos Linux necessários está documentado no arquivo [guiavm.md](file:///c:/Users/a50057663/Desktop/Automa%C3%A7%C3%B5es/SmartEvents/guiavm.md).

---

## 📈 Especificações Técnicas e de Design

### Plotagem de Pétalas
O SmartEvents plota os setores das antenas celulares (pétalas) no mapa Leaflet de duas formas dependendo da tecnologia:
*   **Técnica de Marcadores SVG (Padrão do SmartEvents):** Desenha dinamicamente arcos SVG `<path>` baseados no azimute e abertura da antena diretamente na viewport do Leaflet. Desta forma, as pétalas possuem tamanho fixo em pixels e mantêm a leitura ideal em qualquer nível de zoom.
*   **Técnica SemiCircle (NPSmart Legado):** Utiliza projeções geográficas em metros na tela.
*   Mais informações sobre a matemática trigonométrica empregada estão disponíveis em [evento.md](file:///c:/Users/a50057663/Desktop/Automa%C3%A7%C3%B5es/SmartEvents/evento.md).

### Regras de Negócio e Segurança
*   **Sanitização de Dados Pessoais:** Por questões de segurança e privacidade (LGPD), informações críticas como o IMSI (ID de chip do cliente) do VIP cadastrado no JSON nunca são expostos no frontend JavaScript. A higienização é realizada diretamente na API Python ([api.py](file:///c:/Users/a50057663/Desktop/Automa%C3%A7%C3%B5es/SmartEvents/api/api.py)) antes que o objeto do evento seja serializado para a interface gráfica.
*   **Padrões de Estado JS:** O frontend é inteiramente desacoplado. Módulos como [map.js](file:///c:/Users/a50057663/Desktop/Automa%C3%A7%C3%B5es/SmartEvents/frontend/js/map.js) e [kpi.js](file:///c:/Users/a50057663/Desktop/Automa%C3%A7%C3%B5es/SmartEvents/frontend/js/kpi.js) não importam um ao outro; em vez disso, comunicam-se de forma assíncrona por meio do barramento de eventos em [state.js](file:///c:/Users/a50057663/Desktop/Automa%C3%A7%C3%B5es/SmartEvents/frontend/js/state.js).
