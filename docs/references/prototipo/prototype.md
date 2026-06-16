# Design Brief: Plataforma de Monitoramento RF

## 1. Linguagem Visual

**Estilo geral:** Interface operacional densa, tema escuro. O padrão dark é obrigatório em ambientes de Centro de Operações porque reduz fadiga visual em turnos longos e permite leitura imediata de cores de status sem ambiguidade. A referência estética é próxima de ferramentas como Grafana em modo escuro ou painéis de controle aeroespaciais: séria, funcional, sem ornamentos desnecessários.

**Princípios de design:**
- Hierarquia de informação por urgência, não por estética
- Status legível em menos de 2 segundos sem precisar ler texto
- Densidade alta, mas organizada em zonas visuais claras
- Zero elementos decorativos que não carregam informação

---

## 2. Sistema de Cores

```
BACKGROUNDS
  Base (fundo principal):     #0D1117
  Superfície (cards/painéis): #161B22
  Borda/divisor:              #30363D
  Hover/selecionado:          #1C2128

TIPOGRAFIA
  Texto primário:             #E6EDF3
  Texto secundário:           #8B949E
  Texto desativado:           #484F58

STATUS (semáforo operacional)
  Saudável / Ativo:           #3FB950  (verde)
  Atenção / Warning:          #D29922  (âmbar)
  Crítico / Alarme:           #F85149  (vermelho)
  Informação / Seleção:       #388BFD  (azul)

ESPECIAIS
  VIP (destaque ouro):        #F0B429
  REC (gravação ativa):       #F85149  pulsante
  Fora do evento (neutro):    #8B949E
  Gap de coleta (histórico):  #30363D  (faixa hachurada)
```

---

## 3. Tipografia

- **Família:** Inter (sans-serif, excelente legibilidade em densidade alta)
- **Dados numéricos / KPIs:** Variante monoespaçada (Inter Mono ou JetBrains Mono) para alinhamento de valores
- **Hierarquia:**
  - Label de seção: 11px, uppercase, letter-spacing 0.08em, cor secundária
  - Valor de KPI: 24px bold, cor primária
  - Texto de card: 13px regular
  - Alerta: 12px medium, cor de status correspondente

---

## 4. Layout Desktop (1920x1080 referência)

```
┌─────────────────────────────────────────────────────────────────────┐
│  HEADER  (altura: 56px, fundo: #161B22, borda inferior: #30363D)   │
├───────────────────────────────────────┬─────────────────────────────┤
│                                       │                             │
│   MAPA CENTRAL                        │   PAINEL VIP                │
│   (65% da largura, 62% da altura)     │   (35% da largura)          │
│                                       │                             │
│                                       │                             │
├───────────────────────────────────────┴─────────────────────────────┤
│  PAINEL DE KPIs  (100% da largura, 38% da altura)                   │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 5. Componentes em Detalhe

### 5.1 Header

Dividido em três zonas horizontais:

**Zona esquerda:**
- Logotipo / nome da plataforma (texto ou SVG)
- Separador vertical

**Zona central:**
- Nome do evento em destaque: `GP SÃO PAULO 2025` — fonte 15px medium, cor primária
- Badge de status ao lado: pílula arredondada com fundo verde e texto `● ATIVO` ou âmbar `● AGENDADO`
- Cronômetro do evento: `02:34:17` em fonte mono, cor secundária

**Zona direita:**
- Indicador REC: ícone de círculo vermelho pulsante + `● REC  4.2 MB` — aparece apenas quando gravação está ativa
- Contador de alertas ativos: ícone de sino + badge vermelho com número
- Relógio local: `14:32` mono

---

### 5.2 Mapa Central

**Basemap:** Tile escuro estilo Mapbox Dark ou CartoDB Dark Matter. O contraste entre o mapa e os marcadores de site é o principal canal visual da interface.

**Marcadores de site:**
- Formato de setores radiados (fan/setor), idêntico à ferramenta já existente vista nas capturas
- Cada pétala representa uma célula do site
- Cor da pétala indica status:
  - Verde: saudável
  - Âmbar: atenção (threshold de utilização atingido)
  - Vermelho: crítico ou fora do ar
  - Cinza: sem dado / fora de coleta

**Interação:**
- Hover no marcador: tooltip flutuante com nome do site, número de células e utilização atual
- Click no marcador: abre painel lateral de detalhe do site (sobrepõe o painel VIP temporariamente), mostrando lista de células com KPIs individuais

**Controles do mapa (canto superior direito do painel):**
- Botão de zoom in/out
- Toggle "Mostrar apenas sites do evento" (filtra marcadores fora do polígono)
- Toggle de overlay de polígono do evento (borda tracejada na área do evento)

**Polígono do evento:**
- Borda tracejada na cor `#388BFD` com opacidade 40%
- Sem preenchimento sólido para não encobrir o mapa

---

### 5.3 Painel VIP

Header do painel: label `VIPS` uppercase + contador `(7 no evento / 2 fora)`

Dividido em duas seções com separador visual:

**Seção "No Evento"** (topo, fundo levemente mais claro `#1C2128`):

Cada card de VIP:
```
┌────────────────────────────────────────┐
│  ● Ana Silva               Site: ERB-07│
│  RSRP  ████████░░  -88 dBm             │
│  RSRQ  ██████████  -7 dB               │
└────────────────────────────────────────┘
```
- Ponto colorido no nome: verde (sinal ok), âmbar (atenção), vermelho (degradado)
- Barras de sinal: preenchimento proporcional ao threshold do evento, com cor correspondente ao status
- Se RSRP estiver abaixo do threshold: card inteiro recebe borda esquerda vermelha de 3px

**Seção "Fora do Evento"** (abaixo, texto e elementos em cor secundária, mais apagados):

Cards simplificados sem barras de sinal, apenas nome e último site registrado. O apagamento visual comunica que esses VIPs não requerem atenção imediata.

---

### 5.4 Painel de KPIs (Inferior)

Dividido em três colunas:

**Coluna esquerda — Lista de Sites (25% da largura):**
- Lista scrollável de todos os sites do evento
- Cada linha: ícone de status colorido + nome do site + valor de utilização atual em %
- Site selecionado: fundo `#1C2128` + borda esquerda azul
- Sites críticos sobem automaticamente para o topo da lista (ordenação dinâmica)

**Coluna central — Seletor de métricas (10% da largura):**
- Dropdown vertical com as opções: Utilização, Availability, Throughput, RSRP Médio, RSRQ Médio
- A métrica selecionada define o eixo Y do gráfico à direita

**Coluna direita — Gráfico (65% da largura):**
- Gráfico de linha temporal (últimos 60 minutos por padrão)
- Eixo X: tempo com marcações a cada 10 minutos
- Eixo Y: escala automática com linhas de threshold sobrepostas (tracejado âmbar para atenção, tracejado vermelho para crítico)
- Gaps de coleta: faixas verticais cinza-escuro `#30363D` com textura hachurada, indicando períodos sem dados
- Seletor de janela temporal no canto: `15min | 30min | 60min | Evento completo`

---

## 6. Estados da Interface

### Estado: Standby (nenhum evento ativo)

Mapa visível mas sem marcadores. Centro da tela:
```
      ○  Nenhum evento ativo

      Próximo evento:
      GP São Paulo 2025
      Em 3 dias · 12h de duração

      [Abrir histórico]
```
Tipografia centralizada, ícone de relógio outline. Botão de histórico apenas se houver dados coletados localmente.

### Estado: Dois eventos simultâneos

Antes de entrar no dashboard, tela de seleção de evento:
- Dois cards lado a lado com nome do evento, hora de início e contador de sites
- Opção `Visão consolidada` abaixo, que carrega o mapa com marcadores de ambos os eventos diferenciados por cor de borda

### Estado: Histórico

Mesmo layout do dashboard ativo, com duas diferenças visuais:
- Banner fixo no topo do mapa: `MODO HISTÓRICO · GP São Paulo 2025 · 13/11/2024` em âmbar
- Controles de navegação temporal substituem o cronômetro no header: `◄ ►` com slider de tempo

---

## 7. Painel de Detalhe do Site (overlay ao clicar no marcador)

Aparece sobre o painel VIP, com botão X para fechar:

```
ERB-07 · Mooca             [×]
─────────────────────────────
CÉLULA      AVAIL.   UTIL.   THROUGHPUT
Cell A1      100%     78%    42 Mbps  ●
Cell A2      100%     91%    38 Mbps  ▲
Cell A3       0%       -       -      ✕
─────────────────────────────
[Ver gráfico desta célula]
```

Ícones de status na última coluna: círculo verde, triângulo âmbar, X vermelho.

---

## 8. Sistema de Alertas (overlay flutuante)

Alertas aparecem no canto inferior direito, empilhados (toast notifications):

```
┌────────────────────────────────────┐
│  ▲  ERB-07 · Cell A2               │
│     Utilização atingiu 91%         │
│     Há 32 segundos          [×]    │
└────────────────────────────────────┘
```

- Borda esquerda colorida por severidade
- Desaparecem automaticamente após 10 segundos (críticos permanecem até dismiss manual)
- Painel de histórico de alertas acessível pelo ícone de sino no header

---
