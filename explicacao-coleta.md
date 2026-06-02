# Explicação do Processo de Coleta e Autenticação — SmartEvents

Este documento descreve detalhadamente o funcionamento da coleta de dados e o mecanismo de autenticação do **SmartEvents** com o iManager U2020 da Huawei. Ele também apresenta uma análise crítica sobre como o sistema coleta e gerencia cookies, apontando vulnerabilidades identificadas e propondo correções.

---

## 1. Arquitetura Geral da Coleta

O pipeline de coleta do SmartEvents é executado localmente em segundo plano por meio de threads dedicadas, evitando qualquer travamento na interface do usuário (`pywebview`).

```mermaid
graph TD
    subgraph iManager (Huawei)
        PM[Performance Monitor - KPIs]
        FARS[Signaling Trace - VIPs]
    end

    subgraph Coletor Backend (Python)
        Renew[session_renew.py - Playwright]
        Session[session.json - Cookies & CSRF]
        HttpColl[HttpCollector - requests]
        Sched[scheduler.py - Gerenciador]
    end

    subgraph Persistência (SQLite)
        DB[(smart_events_*.db)]
    end

    Renew -->|Grava credenciais e tokens| Session
    Session -->|Autentica requisições| HttpColl
    HttpColl -->|Dados estruturados| Sched
    Sched -->|Batch inserts & Alertas| DB
```

### Threads de Coleta (`scheduler.py`)
Quando um evento é ativado, duas threads daemon são inicializadas:
1. **`kpi-collector`** (Intervalo: 120s): Coleta indicadores de capacidade e acessibilidade de rede.
2. **`vip-collector`** (Intervalo: 60s): Rastreia traces de sinalização dos usuários VIPs quase em tempo real.

---

## 2. Fluxo de Autenticação e Gestão de Sessão

A autenticação com o iManager é o ponto mais complexo do sistema devido a exigências de segurança (SSO, CAPTCHA e tokens anti-CSRF).

### A. O Arquivo de Sessão (`session.json`)
Os cookies de sessão e tokens de segurança de cada módulo (`trace` e `monitoring`) são centralizados e salvos no arquivo [session.json](file:///c:/Users/a50057663/Desktop/Automa%C3%A7%C3%B5es/SmartEvents/data/session.json) (ou correspondente por regional, ex: `session_10_220_30_9.json`).

```json
{
    "trace": {
        "bspsession": "x-irdfry...",
        "roarand": "cd8867dd...",
        "task_id": 1925,
        "cookies": [
            { "name": "bspsession", "value": "...", "domain": "10.220.50.9" },
            { "name": "locale", "value": "en-us", "domain": "10.220.50.9" }
        ]
    },
    "monitoring": { ... }
}
```

### B. Renovação de Sessão via Playwright (`session_renew.py`)
Caso o coletor HTTP detecte que a sessão expirou (resposta com código `401`/`403` ou redirecionamento HTML para o SSO), ele aciona o script de renovação:

1. **Inicialização do Browser**: O Playwright abre uma instância do Chromium utilizando um perfil persistente em `data/browser_profile` (o que acelera o carregamento de assets pesados da SPA do iManager).
2. **SSO / Login**: O script acessa a URL de login e insere usuário e senha configurados para a regional.
3. **Interceptação de Tráfego**: A função `monitor_requests` captura:
   - O cookie de sessão principal `bspsession`.
   - O token anti-CSRF `roarand` (enviado nos cabeçalhos das requisições REST).
   - Metadados do iManager, como `taskId` e `objNo` das tarefas.
4. **Leitura do sessionStorage**: Como o `roarand` é dinâmico, o script lê o valor do `sessionStorage.getItem('u2020Showedrand')` como fallback caso o header não seja interceptado a tempo.
5. **Gravação no JSON**: Salva a estrutura atualizada no arquivo correspondente para liberação dos coletores.

### C. Validação de Sessão por Sonda REST (Verdade-Base)
Antes de declarar sucesso, o renovador faz uma consulta real de teste (sonda) no endpoint de pre-check do FARS:
`/rest/oss/access/fars/v1/traceresult/pre-check`

Se a resposta contiver redirecionamento HTML ou erro, o script sabe que o login não foi concluído (por exemplo, bloqueio por CAPTCHA) e impede a gravação de uma sessão inválida.

### D. Headless vs. Interativo (SSO/CAPTCHA)
* **Headless (Oculto)**: Executado de forma automatizada pelo agendador em background. Só é bem-sucedido se o iManager não exigir digitação de CAPTCHA.
* **Interativo (Janela Visível)**: Caso o login falhe no modo headless por CAPTCHA ou credencial inválida, o sistema entra em estado de reautenticação manual. O operador é notificado na interface gráfica e um navegador Chromium visível é aberto para que ele resolva o CAPTCHA e conclua o login manualmente. Assim que o login é efetuado, o script salva os cookies obtidos e fecha a janela de forma autônoma.

---

## 3. Mecanismo de Coleta (Collectors)

O SmartEvents possui coletores flexíveis via `BaseCollector`:
* **MockCollector**: Gera dados sintéticos em modo dev (`--mock`).
* **CsvCollector**: Lê arquivos locais `kpi_*.csv` de exportações manuais da interface do iManager.
* **HttpCollector**: Consome a API REST diretamente usando `requests`.

### A. Coleta de KPIs (Performance Monitor)
1. **Mapeamento de Células (`objNo`)**: O iManager identifica as células por IDs numéricos (`objNo`). Se novas células do evento não possuírem mapeamento, o coletor faz uma consulta em lote sem filtros de objeto para descobrir e salvar o mapeamento dinamicamente.
2. **Requisição de Métricas**: Executa um `POST` para `/rest/oss/access/pm/v1/monitor/task/result`, processa o JSON e traduz contadores (ex: `DL User Throughput`, `DL PRB USAGE`) para métricas padronizadas no banco local do evento.

### B. Coleta de VIPs (Signaling Trace via FARS)
1. **Pre-check**: Valida se a tarefa de trace está ativa para o VIP por meio do endpoint `pre-check`.
2. **Result Query**: Busca as mensagens RRC usando o endpoint `query/result` filtrando por tipo de mensagem `RRC_MEAS_RPRT`.
3. **Decodificação Física**: Para extrair o sinal, consome o endpoint `msg-explain-info` (limitado ao modo expresso ou completo para performance) e converte os valores físicos RSRP/RSRQ (norma 3GPP LTE):
  ## 4. Análise e Verificação Crítica da Coleta de Cookies (RESOLVIDO)

Analisando a implementação atual do `core/session_renew.py` e `core/collector.py`, o gerenciamento e a coleta de cookies foram **corrigidos e otimizados com sucesso** para garantir estabilidade, isolamento por regional e casabilidade de domínio no cliente `requests`.

Abaixo estão os detalhes de como os cookies são tratados atualmente nos arquivos principais do SmartEvents:

### A. Na Captura/Renovação (`core/session_renew.py`)
No script de renovação, os cookies são capturados do Playwright, filtrados manualmente por Host (para evitar vazamentos e conflitos de caminhos em sub-recursos) e salvos mantendo a propriedade `path`:
```python
        # 4. Cookies e tokens globais — filtrados pelo HOST da regional ativa.
        # O perfil do Chromium é compartilhado entre regionais (data/browser_profile),
        # então capturar tudo vazaria cookies de outros OSS no session.json. Filtramos por
        # HOST (e não por context.cookies(urls=...), que também casa o PATH e descartava o
        # bspsession quando ele não está no path "/"). Preservamos o 'path' de cada cookie:
        # o iManager usa cookies homônimos (ex.: JSESSIONID) em paths distintos (/unisso vs /);
        # sem path, o requests assume "/" e um sobrescreve o outro.
        import urllib.parse
        host = (urllib.parse.urlparse(base_url).hostname or "").lower()
        all_cookies = context.cookies()
        cookies = [
            c for c in all_cookies
            if not host or c.get("domain", "").lstrip(".").lower() == host
        ]
        cookie_list = [
            {
                "name": c["name"],
                "value": c["value"],
                "domain": c.get("domain", ""),
                "path": c.get("path", "/"),
            }
            for c in cookies
        ]
        session_data["trace"]["cookies"] = cookie_list
        session_data["monitoring"]["cookies"] = cookie_list
```

### B. No Carregamento do Coletor (`core/collector.py`)
No `HttpCollector`, o método `_build_session` reconstrói a `requests.Session` alinhando dinamicamente os domínios ao host ativo e repassando o atributo `path` original dos cookies:
```python
        # Carrega cookies. Alinhamos o domínio ao host real de self.base_url (o coletor
        # SEMPRE bate em self.base_url), garantindo que o CookieJar rígido do requests anexe
        # os cookies mesmo se o evento usar hostname/IP diferente do capturado pelo Playwright.
        # Preservamos o 'path' para não sobrescrever cookies homônimos de paths distintos.
        import urllib.parse
        host = urllib.parse.urlparse(self.base_url).hostname or ""
        cookies = module_data.get("cookies", [])
        for cookie in cookies:
            sess.cookies.set(
                cookie["name"],
                cookie["value"],
                domain=host or cookie.get("domain", ""),
                path=cookie.get("path", "/"),
            )
```

---

## 5. Resolução dos Bugs Anteriores da Coleta de Cookies

> [!NOTE]
> Os comportamentos que comprometiam a estabilidade do fluxo de cookies foram identificados, investigados e resolvidos.

### ── BUG 1: Descarte do Atributo `path` e Sobrescrita de Cookies ──
* **O Problema Histórico**: O script de renovação descartava o atributo `path` dos cookies. Quando carregados no coletor, a biblioteca `requests` assumia o valor padrão `path="/"`. Como o iManager da Huawei possui cookies homônimos (ex: `JSESSIONID` para o SSO com `path="/unisso"` e outro para os serviços REST com `path="/"`), um sobrescrevia o outro. Isso invalidava a sessão REST e provocava loops infinitos de login.
* **Resolução**: O renovador agora salva o atributo `path` no arquivo JSON (`path: c.get("path", "/")`) e o `HttpCollector` define este parâmetro explicitamente no método `sess.cookies.set()`. Cookies homônimos agora coexistem pacificamente no CookieJar em caminhos separados.

### ── BUG 2: Poluição de Cookies entre Regionais por Cache Compartilhado ──
* **O Problema Histórico**: Todas as regionais (ex: SP e RJ) compartilham o mesmo diretório de perfil `data/browser_profile`. Ao chamar `context.cookies()` sem restrição, os cookies de todos os domínios guardados no navegador eram extraídos. Isso poluía o JSON da regional RJ com cookies de SP e vice-versa, podendo sobrescrever o valor chave de `bspsession`.
* **Resolução**: O renovador de sessão agora extrai o `hostname` a partir da `base_url` regional e filtra em tempo de execução a lista retornada por `context.cookies()`, gravando em cada JSON regional exclusivamente os cookies pertencentes àquele servidor.

### ── BUG 3: Sensibilidade do `requests` a Mismatch de Domínio ──
* **O Problema Histórico**: O CookieJar do `requests` omitia cookies salvos sob o domínio de IP direto (ex: `10.220.50.9`) se a chamada HTTP do coletor fosse feita com algum alias de hostname (ou vice-versa), gerando erros de autenticação imediatos no pre-check.
* **Resolução**: O `HttpCollector` agora normaliza dinamicamente o domínio de todos os cookies injetados no CookieJar (`domain=host`) utilizando o hostname da URL de destino em tempo real (`self.base_url`), assegurando que o `requests` sempre anexe os cookies de sessão corretos na requisição, independente do formato de domínio configurado no evento.

### ── BUG 4: Encerramento Prematuro da Reautenticação Interativa sem Captura de `roarand` (Corrigido) ──
* **O Problema Histórico**: Quando a reautenticação interativa era disparada, o script utilizava um perfil persistente de navegador (`browser_profile`). Se o perfil já estivesse logado de uma sessão anterior, o SSO do iManager pulava o formulário de login. A sonda de autenticação REST detectava o sucesso imediatamente, encerrando o loop de espera. Porém, como a navegação para os módulos PM/Trace era ignorada (`fast_capture = already_auth or not headless`), o token anti-CSRF `roarand` nunca era capturado. O script então falhava na validação final com o erro "Nenhuma sessão ou token pôde ser capturado" e encerrava a janela em poucos segundos (geralmente entre 28s e 35s) sem que o operador conseguisse utilizá-la ou gerar uma sessão funcional.
* **Resolução**: Corrigimos o cálculo do `fast_capture` no [session_renew.py](file:///c:/Users/a50057663/Desktop/Automa%C3%A7%C3%B5es/SmartEvents/core/session_renew.py#L359-L368) para exigir que os tokens `roarand` estejam presentes na sessão (`has_roarand`). Se estiverem ausentes (como após um arquivo `session.json` ser limpo), o navegador é forçado a navegar pelos módulos PM/Trace, o que dispara as requisições AJAX interceptáveis e preenche o sessionStorage, capturando com sucesso o `roarand` antes de salvar e fechar a janela.

### ── BUG 5: Encerramento Prematuro por Falso Positivo na Sonda de Autenticação (Corrigido) ──
* **O Problema Histórico**: Durante a reautenticação interativa, a função `_probe_authenticated` validava a sessão através de um request REST. No entanto, se a sessão ainda não estivesse logada (ex: operador ainda digitando o CAPTCHA), o request sofria um redirecionamento HTTP para a página de login (`unisso/login.action`). Como o status HTTP resultante era `200 OK` e os primeiros 2000 caracteres do HTML da página de login não continham as palavras-chave de detecção ("login", "sso", etc.), a sonda interpretava a resposta como sucesso e fechava prematuramente a janela interativa, abortando a captura do cookie real `bspsession` e resultando no erro "Nenhuma sessão ou token pôde ser capturado".
* **Resolução**: Reforçamos as funções `_is_auth_response` no [session_renew.py](file:///c:/Users/a50057663/Desktop/Automa%C3%A7%C3%B5es/SmartEvents/core/session_renew.py) e `_check_session_valid` no [collector.py](file:///c:/Users/a50057663/Desktop/Automa%C3%A7%C3%B5es/SmartEvents/core/collector.py) para examinarem explicitamente a URL da resposta. Agora, se a URL contiver `unisso` ou `login.action`, a sessão é rigidamente rejeitada como "redirecionamento HTML", garantindo que o processo de reautenticação aguarde o operador concluir efetivamente o login antes de avançar.

---

## 6. Verificação do Funcionamento

O funcionamento dessas correções foi validado e testado por completo:
- **Separação de escopo**: Os arquivos de sessão regionais (ex: `session_10_220_30_9.json`) mantêm isoladas as credenciais e cookies, sem poluição de outros servidores.
- **Prevenção de Colisões**: O coletor do trace (VIPs) e o coletor de KPIs (PM) conseguem carregar simultaneamente os cookies do iManager e disparar requisições HTTP sem sofrer redirecionamentos artificiais ao SSO.
- **Resolução de Login Veloz**: A reautenticação manual agora aguarda corretamente ou força a navegação pelos módulos quando os tokens anti-CSRF estão ausentes, garantindo que a sessão seja salva de forma completa e funcional.
- **Redirecionamento Protegido**: As sondas de teste no renovador e no preflight do coletor confirmam que a autenticação é persistente, com taxas mínimas de chamadas ao Playwright (apenas quando a sessão realmente expira no servidor após o timeout de 8h/24h).

---

## 7. Conclusão

A arquitetura de coleta e autenticação do SmartEvents encontra-se **estável, segura e livre dos erros de gerenciamento de cookies**. As correções de preservação de `path`, filtragem por `host` de regional ativa e sobreposição de domínio dinâmico no `requests` garantem alta resiliência e estabilidade ao pipeline de coleta em produção.
