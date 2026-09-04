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

1. **OSS/Trace/HTTP REST**: Dados de telemetria coletados por arquivos CSV locais (Fase 1) ou consultados diretamente no iManager do cliente via chamadas HTTP REST com tratamento automático de expiração e renovação de sessão via script Playwright em background (Fase 2). Suporta conexões a múltiplos iManagers regionais (SP e RJ) com isolamento automático de arquivos de sessão (`session_*.json`) baseado no campo `oss.region` ou override de URL.
2. **Collector & Scheduler**: Duas threads em segundo plano no cliente. A thread de KPI faz requisições a cada 120s e a de VIP/Trace faz a cada 60s.

3. **SQLite**: Banco local central (`smart_events.db`) para metadados globais (como o cadastro global de VIPs com a respectiva regional/OSS) e bancos locais dinâmicos por evento (`smart_events_<event_id>.db`) que armazenam as séries temporais de métricas e alarmes de forma isolada.
4. **Api & PyWebView**: Ponte Python-JS segura que gerencia dados (sanitizando informações confidenciais de IMSI) e renderiza a janela nativa.
5. **JS Frontend**: Interface moderna e offline construída sem bundlers (Vanilla JS + ES Modules + CSS Custom Properties).

---

## 📂 Estrutura de Diretórios e Arquivos

Abaixo está o mapa completo da organização do projeto:

```
SmartEvents/
├── main.py                  # Ponto de entrada do executável Desktop (cria janela PyWebView, injeta Api e sobe o servidor embarcado)
├── server.py                # Servidor API central FastAPI para coordenação de eventos na rede
├── build.py / main.spec     # Geração do executável via PyInstaller
├── clear_demo_event.py      # Utilitário standalone que remove o evento de demo dos bancos locais (data/)
├── requirements.txt         # Arquivo de dependências Python do projeto
├── pytest.ini               # Configuração do pytest (testpaths, marker `vpn` de integração)
├── sample_event.json        # JSON modelo de cadastro/configuração de evento
│
├── api/
│   └── api.py               # Classe Api que faz a comunicação e serialização segura entre Python e JavaScript
│
├── core/
│   ├── models.py            # Dataclasses de domínio (Site, Cell, VIP, EventConfig, Alert)
│   ├── database.py          # Conexão e queries SQLite (isolamento de concorrência com thread-local)
│   ├── collector.py         # Drivers de coleta de dados (CSV iManager, HTTP REST e Mock)
│   ├── scheduler.py         # Threads de background que agendam coletas e analisam limites para gerar alertas
│   ├── session_renew.py     # Renovação de sessão do iManager via Playwright (login headless/interativo, SSO/CAPTCHA)
│   └── log_buffer.py        # Ring buffer de logs em memória para o painel de desenvolvedor
│
├── frontend/                # Interface do Cliente Desktop
│   ├── index.html           # Shell HTML5 que carrega os scripts locais (preparado para funcionamento offline)
│   ├── css/
│   │   └── main.css         # Design system escuro (Dark Mode premium, variáveis CSS e transições)
│   ├── js/
│   │   ├── app.js           # Orquestrador da interface (ciclo de vida da tela, inicialização e polling)
│   │   ├── bridge.js        # Wrapper da API Python (com fallback automático de mocks no navegador)
│   │   ├── state.js         # Estado global reativo com padrão Publish/Subscribe
│   │   ├── map.js           # Integração com Leaflet e plotagem geométrica de pétalas/setores via SVG
│   │   ├── vip.js           # Painel direito com status de sinal e localização dos VIPs
│   │   ├── kpi.js           # Painel inferior com tabelas de sites e gráficos temporais via Chart.js
│   │   ├── alerts.js        # Gerenciamento do painel de alertas, ações de leitura/exclusão e download de logs
│   │   └── logs.js          # Painel de logs do desenvolvedor (consome o ring buffer do backend)
│   └── lib/                 # Bibliotecas vendored (Chart.js + Leaflet) para operação 100% offline
│
├── server_frontend/
│   └── index.html           # Interface web do Servidor Central para cadastrar e exportar eventos JSON
│
├── server_data/             # Configuração compartilhada (fonte da verdade do Servidor Central; gitignored)
│   ├── events/              # Arquivos JSON de eventos gerenciados pelo Servidor Central
│   └── vips/                # Arquivos JSON do cadastro global de VIPs
│
├── data/                    # Diretório gerado em tempo de execução (bancos SQLite locais e cache; gitignored)
│   ├── smart_events.db      # Banco de dados SQLite central criado automaticamente (IGNORAR no git)
│   ├── smart_events_*.db    # Bancos de dados SQLite específicos de cada evento (IGNORAR no git)
│   ├── session*.json        # Cookies + token roarand por regional (IGNORAR no git)
│   ├── settings.json        # server_url e preferências locais (IGNORAR no git)
│   ├── clientes.json        # catálogo Cliente→Regional→IP (editável; semeado do bundle)
│   └── credentials.json     # credenciais por Cliente/Regional, com _shared por cliente (IGNORAR no git; criado vazio no 1º acesso)
│
├── tests/                   # Suíte pytest (unitários + integração marcada com `vpn`)
│   ├── conftest.py
│   ├── test_api.py  test_collector.py  test_database.py
│   ├── test_models.py  test_scheduler.py
│   └── test_http_vpn.py     # Integração: requer VPN ativa com o iManager (marker `vpn`)
│
├── tools/
│   └── oss_validate.py      # Validador standalone da coleta HTTP (KPI/VIP) sem subir o app
│
├── scratch/
│   ├── get_session.py       # Renovação de sessão via Playwright (invocado pelo collector em modo dev)
│   └── get_session_regional.py
│
└── docs/                    # Documentação técnica viva do projeto
    ├── ORGANIZACAO.md       # Mapa da organização do repositório
    ├── coleta-de-dados.md   # Documento canônico do fluxo de coleta (KPI/VIP, sessão, alertas)
    ├── evento-cadastro-e-petalas.md  # Fórmulas de plotagem das pétalas e campos do JSON de evento
    ├── guia-vm-servidor.md  # Guia passo a passo para instalar o servidor em uma VM Linux no VirtualBox
    ├── smart-events.html    # Documentação técnica completa em HTML (visão mais detalhada da arquitetura)
    └── references/          # Material de referência usado como base (gitignored: espelhos pesados do NPSmart)
        ├── npsmart/         # Cópias offline das páginas do NPSmart/OSS + script que as gerou
        ├── requests/        # Traces HTTP reais capturados do iManager (PM e FARS)
        ├── prototipo/       # Protótipo visual standalone + design brief
        ├── get-info.md      # Elementos da tela de login do iManager usados na automação Playwright
        ├── sample.md        # Amostra do formato de importação de sites do Servidor Central
        └── vpn.md           # Referência do mecanismo de checagem de VPN
```

*(Documentação técnica detalhada: [docs/ORGANIZACAO.md](docs/ORGANIZACAO.md), [docs/coleta-de-dados.md](docs/coleta-de-dados.md), [docs/evento-cadastro-e-petalas.md](docs/evento-cadastro-e-petalas.md), [docs/guia-vm-servidor.md](docs/guia-vm-servidor.md) e [docs/smart-events.html](docs/smart-events.html))*

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
- **Tratamento de Alertas**: Painel lateral de alertas (sem notificações toast intrusivas) com suporte para confirmação individual, marcação em lote como lido (acknowledge), exclusão permanente do banco de dados (lixeira) e download consolidado dos logs em formato `.log` diretamente na pasta Downloads.
- **Exclusão Segura de Histórico**: O indicador interativo de gravação ("REC X MB") ativa um fluxo com modal de dupla confirmação para apagar medições locais antigas do evento ativo, seguido por uma rotina de `VACUUM` para reclamar espaço físico no SQLite.
- **Exportação completa do evento**: O mesmo indicador `REC`/`DADOS` permite gerar um pacote ZIP auditável com KPIs, VIPs opcionais, alarmes, alertas, checkpoints, configuração e informações normalizadas da EP. Os KPIs podem ficar consolidados ou separados por dia de Brasília e por tecnologia. Consulte o [contrato da exportação](docs/exportacao-de-dados.md).

### 🌐 Servidor Central de Sincronização
- **FastAPI Core**: Provê sincronização REST centralizada para múltiplos terminais de monitoramento na mesma rede local.
- **Delimitação da Área do Evento**: Tela com mapa Leaflet integrado contendo:
  - **Cálculo Automático de Polígono**: Criação de uma bounding box englobando todas as ERBs importadas, com controle de padding ajustável (100m a 2500m) por controle deslizante (slider).
  - **Desenho Manual**: Permite clicar no mapa para definir vértices de polígonos irregulares personalizados.
  - Contador de vértices do polígono.
- **Importador de Sites inteligente**: Upload e processamento automático de planilhas de ERBs nos formatos Excel (`.xlsx`, `.xls`), CSV, TSV e texto plano, normalizando aliases de colunas (`enodebid`, `cellid`, `latitude`, `longitude`, `azimuth`) e agrupando células sob seus respectivos sites. As colunas opcionais `band`/`frequency` e `tech`/`technology` alimentam diretamente a cor e o raio das pétalas, sem alterar a compatibilidade com planilhas antigas.
- **Cadastro Global de VIPs**: Gerenciamento de VIPs (nome, cargo, regional/OSS, task_id do trace) centralizado, permitindo que o aplicativo filtre e rastreie VIPs automaticamente de acordo com o OSS do evento selecionado.

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
> **Desenvolvimento Rápido do Frontend:** Você também pode abrir o arquivo `frontend/index.html` diretamente em qualquer navegador padrão. O script [bridge.js](frontend/js/bridge.js) identificará a ausência do PyWebView e mudará automaticamente para o modo de simulação no frontend, permitindo ajustar layouts CSS e lógicas JavaScript rapidamente.

---

### 2.1. Barra de Título Integrada (Windows)

No Windows, o SmartEvents abre com a **barra de título desenhada em HTML** ([window_chrome.js](frontend/js/window_chrome.js)) no lugar da moldura padrão do sistema. A moldura nativa é removida com um hook Win32 isolado em [core/window_chrome.py](core/window_chrome.py), que devolve ao Windows todos os comportamentos esperados de uma janela: arraste, redimensionamento pelas oito direções, Aero Snap, Snap Layouts (`Win+Z`), duplo clique para maximizar, `Alt+Space` e minimizar/restaurar pela barra de tarefas.

**Flags de execução e recuperação:**

| Flag | Efeito |
|---|---|
| *(nenhuma)* | Barra personalizada no Windows; moldura nativa nos demais sistemas. |
| `--native-titlebar` | **Rollback operacional:** força a moldura nativa do Windows. Tem precedência sobre qualquer outra flag. |
| `--custom-titlebar` | Força a barra personalizada mesmo quando o padrão a desativaria (uso técnico/diagnóstico). |

```bash
python main.py --native-titlebar   # abre a versão de recuperação, sem reinstalar nada
```

**Quando o modo nativo entra sozinho:**

*   fora do Windows;
*   runtime WebView2 anterior a `1.0.2210.55` (sem `IsNonClientRegionSupportEnabled`, a barra não conseguiria entregar arraste e Snap ao Windows);
*   falha ao instalar o hook — nesse caso a janela abre com a moldura nativa e **sem** a faixa HTML vazia.

**Limitações conhecidas:**

*   a largura mínima permanece em **1024 × 600**, então o Windows 11 aplica apenas os layouts de Snap compatíveis com essa largura;
*   a implementação é específica para Windows 10 e Windows 11; macOS e Linux continuam com a moldura nativa;
*   o perfil persistente do WebView2 exige que alterações em CSS/JS da barra venham acompanhadas de novo cache-buster em [index.html](frontend/index.html).

**Diagnóstico:** cada inicialização registra em `data/logs/smart_events.log` o modo solicitado, o modo efetivo, o motivo da decisão, a versão do Windows, o DPI inicial, a versão do WebView2 e o resultado do `attach` (com o motivo de eventual fallback). Para provar o ciclo completo numa janela WebView2 real:

```powershell
python main.py --self-test-window-chrome   # attach → consulta → detach, saída JSON
python main.py --self-test                 # diagnóstico completo, inclui a prova acima
```

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
4. Um passo a passo completo e detalhado com todos os comandos Linux necessários está documentado no arquivo [guia-vm-servidor.md](docs/guia-vm-servidor.md).

---

### 5. Rodando os Testes
O projeto possui uma suíte de testes automatizados em [tests/](tests/) configurada via [pytest.ini](pytest.ini).

1. Com o ambiente virtual ativo, instale o pytest (caso ainda não esteja presente) e rode a suíte:
   ```bash
   pip install pytest
   pytest
   ```
2. **Testes de integração com VPN:** O arquivo `tests/test_http_vpn.py` faz chamadas reais ao iManager e está marcado com o marker `vpn`. Esses testes **só passam conectado à VPN do cliente**. Por padrão eles rodam junto; para executá-los isoladamente ou pulá-los:
   ```bash
   pytest -m vpn        # roda SOMENTE os testes que exigem VPN
   pytest -m "not vpn"  # pula os testes de VPN (desenvolvimento offline)
   ```

> [!TIP]
> Para uma validação rápida da coleta HTTP (KPI/VIP) sem subir a interface, use o validador standalone [tools/oss_validate.py](tools/oss_validate.py) (ex.: `python tools/oss_validate.py --kind both --region RJ`), também dependente da VPN.

---

### 6. Gerando o Executável (`.exe`)
A distribuição em campo é um executável portátil gerado com **PyInstaller**. Desde a Fase 3, o
ciclo do **programa** (build-base, roda o PyInstaller) é separado do ciclo do **conteúdo**
(seleção de eventos, nunca dispara o PyInstaller):

1. Com o ambiente virtual ativo, garanta que os navegadores do Playwright estão instalados (são empacotados no `.exe` para a renovação de sessão):
   ```bash
   playwright install chromium
   ```
2. Gere o build-base **uma vez por versão** (roda o PyInstaller; genérico, sem evento/cliente):
   ```bash
   python build.py base
   ```
3. Combine o build-base com uma seleção de eventos — sem recompilar o programa:
   ```bash
   python build.py distribution --events evento-a evento-b --format package  # .sepack
   python build.py distribution --events evento-a evento-b --format setup    # Setup.exe completo
   ```
4. O fluxo histórico por perfil (`python build.py legacy-profile --profile <id>`, um `AppId`/pasta
   de dados dedicados por cliente) continua disponível como **fallback**, mas é considerado
   legado: `build_profiles/` não recebe novos perfis, e o caminho recomendado para qualquer
   distribuição nova é `base` + `distribution`.

O executável final fica em `dist/base/<versão>/SmartEvents/` (ou `dist/<perfil>/` no fluxo
legado). A pasta de dados do operador (`%LOCALAPPDATA%\SmartEvents` no instalado, `data/` em
dev) é externa ao bundle e sobrevive a atualização/reinstalação.

> [!NOTE]
> O `main.spec` empacota o Chromium completo do Playwright (necessário para o fluxo de reautenticação interativa com CAPTCHA), o que torna o bundle grande (~880 MiB descompactado). A pasta de dados do operador é sempre externa ao bundle.

**Assinatura (Fase 4, opcional).** Sem nenhuma variável de ambiente configurada, os artefatos
saem sem assinatura e o manifesto declara isso honestamente (`"signed": false`) — desenvolvimento
não é bloqueado pela ausência de certificado. Numa máquina de release com um certificado
Authenticode real:

```bash
set SMARTEVENTS_SIGNTOOL_PATH=C:\...\signtool.exe
set SMARTEVENTS_CODE_SIGN_PFX=C:\segredo\release.pfx
set SMARTEVENTS_CODE_SIGN_PFX_PASSWORD=...
python build.py base                                        # assina SmartEvents.exe
python build.py distribution --events evento-a --format setup  # assina Setup.exe + desinstalador
```

A chave **privada** nunca entra no repositório nem é lida de um caminho fixo — só existe via essas
variáveis, na máquina de quem faz o release. Veja [core/authenticode.py](core/authenticode.py).

### Credenciais de acesso (Cliente → Regional)

O catálogo de clientes e regionais (`Cliente → Regional → IP`) fica em `data/clientes.json`, **editável sem recompilar** (semeado do bundle na 1ª execução). As credenciais de cada usuário ficam em `data/credentials.json` (texto puro, **não embutido** no `.exe` — criado vazio no 1º acesso).

- **1º acesso:** ao ativar um evento cuja regional ainda não tem credencial, o app abre um modal pedindo usuário/senha daquele Cliente/Regional.
- **Atualizar:** botão de credenciais no cabeçalho abre o gerenciador por Cliente. Marque **"usar a mesma credencial para todas as regionais deste cliente"** para uma conta única (compartilhada), ou preencha por regional (override). A credencial por regional tem precedência sobre a compartilhada.

### 7. Distribution Studio (ferramenta interna de distribuição)

Gerar pacotes de eventos (`.sepack`) é operação de **quem distribui**, não do usuário final. Por isso ela não vive no Smart Events Central nem no `.exe`: fica numa ferramenta separada, servida em outro processo e em outra porta.

```bash
python -m tools.distribution_studio            # http://127.0.0.1:8010/distribution-studio
python -m tools.distribution_studio --port 8020
```

- A tela lista os eventos do mesmo `server_data` lido pela Central (somente leitura — cadastrar e editar continua sendo função da Central), permite selecionar vários, revisar clientes/regionais/VIPs/logos incluídos e baixar o `.sepack` com o SHA-256 e o manifesto.
- **Não há rota em `/`**: sem o link completo não existe tela. O bind é fixo em `127.0.0.1` e não há CORS — ao contrário da Central, que serve a rede local.
- O `server.py` e o `server_frontend/index.html` **não** conhecem nada disso, e o `main.spec` não empacota o estúdio: o executável distribuído sabe apenas **importar** um `.sepack` (`SmartEvents.exe --import-event-package <arquivo> --show-dialog`).
- As saídas ficam em `data/distributions/<job-id>/`; o estado de cada job em `data/distributions/jobs/<job-id>.json`, para o resultado sobreviver a um F5.
- Para inspecionar ou importar um pacote pela linha de comando, veja [tools/event_package.py](tools/event_package.py) (`preview`, `build`, `inspect`, `import`).

**Assinatura do `.sepack` (Fase 4).** Quando a máquina que roda o estúdio tem
`SMARTEVENTS_SEPACK_SIGNING_KEY` apontando para uma chave privada (`.iskey`, gerada com
`ISSigTool.exe`), todo pacote gerado sai assinado; sem a variável, sai sem assinatura, como nas
Fases 1–3. A importação (CLI, `main.py --import-event-package` e a Central) sempre **verifica**
uma assinatura presente contra `keys/sepack_signing/registry.json` — pacote adulterado, de chave
desconhecida ou revogada nunca é aceito, em nenhum modo. Por padrão, pacote **sem** assinatura
ainda é aceito com aviso (`"Não assinado — desenvolvimento"`); `--require-signature` (CLI/`main.py`)
ou `SMARTEVENTS_SEPACK_SIGNATURE_POLICY=production` (Central) endurecem para exigir assinatura
válida. Detalhes e rotação de chave em [core/package_signing.py](core/package_signing.py).

**Histórico de distribuições (Fase 4).** A seção "Distribuições geradas" do estúdio lista todo job
já concluído (gerador, commit de origem, hash, status de assinatura), com filtros por evento,
cliente, formato e estado. Excluir remove só o binário — o registro de auditoria nunca é apagado.

**Migrando uma instalação isolada (`AppId`/pasta de dados próprios do fluxo `legacy-profile`) para
o SmartEvents genérico.** Não existe conversão automática — os dois usam `AppId` diferentes e o
Windows os trata como aplicações distintas. Procedimento manual:

1. No SmartEvents antigo, exporte os eventos: `python -m tools.event_package build --source
   <pasta-de-dados-antiga>\server_data --events <ids> --output migracao.sepack` (ou gere pelo
   Distribution Studio, apontando `--source` para a pasta de dados da instalação antiga).
2. Instale o SmartEvents genérico (`Setup.exe` gerado por `build.py distribution --format setup`)
   normalmente — ele não conflita com a instalação antiga, já que os `AppId` são diferentes.
3. Importe `migracao.sepack` na instalação nova (duplo clique, ou `SmartEvents.exe
   --import-event-package migracao.sepack --show-dialog`).
4. Confira os eventos, peça a credencial de cada cliente/regional (nunca é transportada) e só
   então desinstale a instalação antiga.

O plano completo (formato do `.sepack`, conciliação de cadastros, build-base e assinatura) está em [docs/plans/2026-08-31-001-feat-distribuicao-automatizada-eventos-plan.md](docs/plans/2026-08-31-001-feat-distribuicao-automatizada-eventos-plan.md).

---

## 📈 Especificações Técnicas e de Design

### Plotagem de Pétalas
O SmartEvents plota os setores das antenas celulares (pétalas) no mapa Leaflet de duas formas dependendo da tecnologia:
*   **Técnica de Marcadores SVG (Padrão do SmartEvents):** Desenha dinamicamente arcos SVG `<path>` baseados no azimute e abertura da antena diretamente na viewport do Leaflet. Desta forma, as pétalas possuem tamanho fixo em pixels e mantêm a leitura ideal em qualquer nível de zoom.
*   **Técnica SemiCircle (NPSmart Legado):** Utiliza projeções geográficas em metros na tela.
*   Mais informações sobre a matemática trigonométrica empregada estão disponíveis em [evento-cadastro-e-petalas.md](docs/evento-cadastro-e-petalas.md).

### Regras de Negócio e Segurança
*   **Sanitização de Dados Pessoais:** Por questões de segurança e privacidade (LGPD), informações críticas como o IMSI (ID de chip do cliente) do VIP cadastrado no JSON nunca são expostos no frontend JavaScript. A higienização é realizada diretamente na API Python ([api.py](api/api.py)) antes que o objeto do evento seja serializado para a interface gráfica.
*   **Padrões de Estado JS:** O frontend é inteiramente desacoplado. Módulos como [map.js](frontend/js/map.js) e [kpi.js](frontend/js/kpi.js) não importam um ao outro; em vez disso, comunicam-se de forma assíncrona por meio do barramento de eventos em [state.js](frontend/js/state.js).
