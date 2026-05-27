# Análise de Plotagem de Pétalas e Requisitos de Cadastro de Evento

Este documento detalha o estudo de plotagem de setores/pétalas a partir do arquivo [earth_petal.html](file:///c:/Users/a50057663/Desktop/Automa%C3%A7%C3%B5es/SmartEvents/earth_petal.html) e define todos os dados e campos obrigatórios para o cadastro 100% funcional de um evento na plataforma **Smart Events**.

---

## 1. Análise de Plotagem das Pétalas

No ecossistema de otimização de RF (NPSmart / Smart Events), as células de transmissão celular (setores) são comumente chamadas de **pétalas**. Analisando o código de [earth_petal.html](file:///c:/Users/a50057663/Desktop/Automa%C3%A7%C3%B5es/SmartEvents/earth_petal.html) e suas dependências associadas (como `Semicircle.js` e `functions-map.js.download`), identificamos duas técnicas principais para plotar essas estruturas sobre um mapa Leaflet.

### Técnica A: Leaflet SemiCircle (Utilizada no NPSmart original)
Esta técnica baseia-se na biblioteca externa `Leaflet.SemiCircle` (carregada via script `Semicircle.js`). Ela estende a classe original `L.Circle` do Leaflet para renderizar apenas uma seção angular (um setor circular ou cone).

*   **Código de Exemplo Identificado:**
    ```javascript
    var azimuthMapa = parseInt(azimuth);
    var semiCircle = L.semiCircle([latitude, longitude], {
        radius: 280, // Raio fixo em metros
        startAngle: (azimuthMapa - 23),
        stopAngle: (azimuthMapa + 23),
        color: 'red',
        fillColor: 'red',
        fillOpacity: 0.5,
    }).addTo(map);
    ```
*   **Funcionamento Matemático:**
    *   **Centro:** Coordenadas Geográficas `[latitude, longitude]`.
    *   **Orientação:** O azimute (`azimuth`) define a direção para onde a antena aponta (em graus, de 0° a 360°, onde 0° é o Norte).
    *   **Abertura (Beamwidth):** Calculada dividindo o ângulo total da antena. No exemplo, `startAngle` e `stopAngle` criam uma abertura simétrica de **46°** em torno do azimute (`azimuth ± 23°`).
    *   **Raio:** Determinado em metros (`280` metros no exemplo). Isso significa que as pétalas encolhem ou expandem visualmente conforme o usuário altera o nível de zoom do mapa.
*   **Campos de Dados Necessários:**
    *   `latitude` (float): Coordenada latitudinal da ERB.
    *   `longitude` (float): Coordenada longitudinal da ERB.
    *   `azimuth` (float/int): Direção da célula em graus.
    *   `beamwidth` ou `apertureAngle` (float): Abertura do cone de propagação (ex: 46° ou 120°).
    *   `radius` (int): Alcance de cobertura em metros (opcional, pode ser estático por tecnologia/frequência).

---

### Técnica B: Setores SVG via `L.divIcon` (Implementada no Smart Events)
Implementada no arquivo local [map.js](file:///c:/Users/a50057663/Desktop/Automa%C3%A7%C3%B5es/SmartEvents/frontend/js/map.js), esta técnica utiliza marcadores Leaflet customizados (`L.divIcon`) contendo caminhos SVG (`<path>`) calculados dinamicamente em pixels.

*   **Matemática de Renderização SVG:**
    ```javascript
    function _sectorPath(cx, cy, r, azimuth, sweep) {
      // Converte compass heading (0° = Norte) para coordenadas trigonométricas SVG (0° = Leste, y invertido)
      const startDeg = azimuth - sweep / 2 - 90;
      const endDeg   = azimuth + sweep / 2 - 90;
      const start = _polar(cx, cy, r, startDeg);
      const end   = _polar(cx, cy, r, endDeg);
      const large = sweep > 180 ? 1 : 0;
      return `M${cx},${cy} L${start.x},${start.y} A${r},${r} 0 ${large},1 ${end.x},${end.y} Z`;
    }
    
    function _polar(cx, cy, r, deg) {
      const rad = (deg * Math.PI) / 180;
      return { x: +(cx + r * Math.cos(rad)).toFixed(2), y: +(cy + r * Math.sin(rad)).toFixed(2) };
    }
    ```
*   **Funcionamento:**
    *   Cada site físico é um marcador fixo que renderiza múltiplos caminhos SVG (um para cada célula/setor) agrupados em um círculo central.
    *   O raio `R` é definido em pixels (ex: `22`px). As pétalas mantém o mesmo tamanho legível em tela, independentemente do zoom do mapa.
    *   O ângulo de abertura `sweep` (ângulo total) padrão é configurado em `100` graus para representar células de abertura normalizada (120° teóricos).
*   **Vantagem no Smart Events:** Elimina a dependência de plugins de terceiros (`Semicircle.js`), roda nativamente em qualquer navegador offline e mantém a legibilidade em zooms distantes (onde círculos de 280 metros sumiriam).

---

## 2. Requisitos para Cadastro de Evento 100% Funcional

Para que a plataforma **Smart Events** funcione perfeitamente de ponta a ponta (alimentando o mapa de pétalas, o monitoramento em tempo real de VIPs, o sistema de alertas de thresholds e o processamento de logs), o cadastro/arquivo de configuração do evento em formato JSON precisa conter as seguintes chaves de dados estruturadas:

### 1. Metadados Gerais do Evento
Identificação e controle de ciclo de vida do monitoramento periódicos.
*   `id` (String): ID único e normalizado em formato slug (ex: `rock-in-rio-2026`). Usado para criar chaves primárias e nomear arquivos.
*   `name` (String): Nome amigável do evento mostrado no cabeçalho (ex: `Rock in Rio 2026`).
*   `status` (String): Estado de inicialização. Valores válidos: `SCHEDULED` (agendado), `ACTIVE` (coletando dados), `ENDED` (finalizado), ou `ARCHIVED` (arquivado).
*   `start_time` (String): Timestamp ISO 8601 de início do evento (`YYYY-MM-DDTHH:MM:SS`). O agendador inicia o loop de monitoramento a partir deste momento.
*   `end_time` (String): Timestamp ISO 8601 de encerramento (`YYYY-MM-DDTHH:MM:SS`). Encerra a coleta em background.

### 2. Polígono Geográfico (`polygon`)
*   `polygon` (Array de Arrays de floats): Lista de coordenadas geográficas `[latitude, longitude]` que fecham a área do evento.
*   **Função:** 
    1. Delimita visualmente o local do evento no mapa com linha pontilhada azul.
    2. Serve para enquadramento inicial do mapa (`fitBounds`).
    3. Utilizado no cálculo espacial para classificar se os VIPs estão geograficamente "dentro do evento" baseado nas coordenadas das ERBs servidoras.

### 3. Infraestrutura de Rede (`sites` e `cells`)
Lista de estações rádio base (ERBs) e suas respectivas células que dão cobertura ao evento.
*   **Campos por Site:**
    *   `id` (String): ID único do site/estação (ex: `ERB-07`). Deve coincidir exatamente com o padrão nos CSVs de KPI da operadora.
    *   `name` (String): Nome descritivo da localidade (ex: `ERB-07 Palco Mundo`).
    *   `lat` (Float): Latitude geográfica (ex: `-23.0012`).
    *   `lng` (Float): Longitude geográfica (ex: `-43.3975`).
    *   `is_event_site` (Boolean): Define se o site está dentro do local físico ou na borda imediata (buffer). Permite filtrar o mapa para ver apenas sites internos.
*   **Campos por Célula (dentro de `cells` do site):**
    *   `id` (String): Identificador único da célula/setor (ex: `ERB-07-A1`). Crucial para associar logs de tráfego/KPI.
    *   `azimuth` (Float): Direção da antena em graus (0 a 360).
    *   `beamwidth` (Float): Abertura horizontal do lóbulo (geralmente `120.0` para antenas tri-setoriais padrão, ou `65.0` para antenas diretivas estreitas).

### 4. Monitoramento de VIPs (`vips`)
Lista de usuários críticos (diretoria, produção, segurança ou clientes VIPs) cujas linhas móveis serão monitoradas sob SLA.
*   `name` (String): Nome ou cargo de identificação do VIP (ex: `Diretor de Produção`).
*   `imsi` (String): Código IMSI único do chip SIM (15 dígitos, ex: `724000000000001`). Usado para casar com os traces de sinal gerados pelo OSS. *(Nota: O IMSI é mantido de forma segura no backend SQLite e sanitizado antes de enviar dados ao Javascript do frontend).*

### 5. Limiares de Qualidade e Alerta (`thresholds`)
Configuração dos limites aceitáveis para métricas de desempenho. Ao ultrapassar estes limiares, a plataforma gera alertas visuais e sonoros (toasts e listagem de incidentes).
*   `rsrp_warning` (Float): Limite de cobertura do sinal VIP para estado de Atenção em dBm (ex: `-100.0` dBm).
*   `rsrp_critical` (Float): Limite de cobertura para estado Crítico em dBm (ex: `-110.0` dBm).
*   `rsrq_warning` (Float): Limite de qualidade do sinal VIP para Atenção em dB (ex: `-12.0` dB).
*   `rsrq_critical` (Float): Limite de qualidade para Crítico em dB (ex: `-15.0` dB).
*   `utilization_warning` (Float): Limite de utilização de capacidade do site para Atenção (ex: `80.0` %).
*   `utilization_critical` (Float): Limite de utilização de capacidade para Crítico (ex: `95.0` %).
*   `availability_critical` (Float): Limite mínimo de disponibilidade de serviço aceitável em células (ex: `90.0` %). Abaixo disso, gera alerta crítico de indisponibilidade.

### 6. Configuração de Integração OSS (`oss`)
Parâmetros de leitura automática de dados de rede.
*   `import_folder` (String): Caminho absoluto da pasta local onde o sistema `watchdog` monitorará novos relatórios CSV (KPIs de sites e Traces de VIPs) gerados pelo iManager/U2000.
*   `region` (String): Sigla regional da rede para filtros rápidos de coleta (ex: `SP`, `RJ`).
*   `base_url` (String): Endpoint de API do iManager (Fase 2 - integração ativa).

---

## 3. Modelo Estruturado Completo (Exemplo JSON)

Para registrar um novo evento no sistema, o payload JSON configurado abaixo deve ser carregado ou salvo na pasta `events/`:

```json
{
  "id": "rock-in-rio-2026",
  "name": "Rock in Rio 2026",
  "status": "SCHEDULED",
  "start_time": "2026-09-18T14:00:00",
  "end_time": "2026-09-20T23:59:59",
  "polygon": [
    [-23.0035, -43.3980],
    [-22.9980, -43.3890],
    [-22.9910, -43.3940],
    [-22.9950, -43.4040],
    [-23.0035, -43.3980]
  ],
  "sites": [
    {
      "id": "ERB-07",
      "name": "ERB-07 Palco Mundo",
      "lat": -23.0012,
      "lng": -43.3975,
      "is_event_site": true,
      "cells": [
        { "id": "ERB-07-A1", "azimuth": 0,   "beamwidth": 120 },
        { "id": "ERB-07-A2", "azimuth": 120, "beamwidth": 120 },
        { "id": "ERB-07-A3", "azimuth": 240, "beamwidth": 120 }
      ]
    },
    {
      "id": "ERB-03",
      "name": "ERB-03 Acesso Principal",
      "lat": -22.9958,
      "lng": -43.3940,
      "is_event_site": true,
      "cells": [
        { "id": "ERB-03-A1", "azimuth": 30,  "beamwidth": 120 },
        { "id": "ERB-03-A2", "azimuth": 150, "beamwidth": 120 },
        { "id": "ERB-03-A3", "azimuth": 270, "beamwidth": 120 }
      ]
    }
  ],
  "vips": [
    { "name": "Coordenador de TI", "imsi": "724000000000001" },
    { "name": "Gerente Geral Evento", "imsi": "724000000000002" }
  ],
  "thresholds": {
    "rsrp_warning": -100.0,
    "rsrp_critical": -110.0,
    "rsrq_warning": -12.0,
    "rsrq_critical": -15.0,
    "utilization_warning": 80.0,
    "utilization_critical": 95.0,
    "availability_critical": 90.0
  },
  "oss": {
    "base_url": "",
    "region": "RJ",
    "import_folder": "C:/Users/a50057663/Desktop/Automações/SmartEvents/import"
  }
}
```
