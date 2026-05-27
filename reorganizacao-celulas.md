### 1. Métricas de Desempenho / Qualidade (Acessibilidade, RSRP, RSRQ, Utilização, Throughput)

Para essas métricas, o valor do site na lista deve refletir o **pior cenário do grupo de células** (ou a média ponderada, se preferir), pois o foco é identificar falhas de engenharia e degradação de sinal.

* **O que exibir na lista:** O valor consolidado do site (ex: Acessibilidade de 94%).
* **Regra de Alerta:** Bolinha vermelha/amarela se *qualquer* célula filha cruzar o limite crítico.

### 2. Métricas de Volume / Capacidade (Usuários Ativos, Volume de Tráfego DL/UL)

Aqui entra a sugestão do seu chefe. Em vez de mostrar que o site tem "14.230 usuários" (o que exigiria criar colunas largas e dificultaria a comparação rápida de relevância), você exibe o **Share (participação)** daquele site em relação à rede inteira ou à região filtrada.

* **O que exibir na lista:** A porcentagem de participação.
* **Cálculo:** 
$$\text{Share de Usuários do Site X} = \left( \frac{\sum \text{Usuários de todas as células do Site X}}{\text{Total de usuários de todos os sites listados}} \right) \times 100$$



> **Como ficaria visualmente na lista da esquerda:**
> * 🟢 **SR-SPCBJ6**          `12.4% do tráfego total`
> * 🟢 **SR-SPSMC1**          `8.1% do tráfego total`
> 
> 

---

## O Design Ideal: UI Dinâmica e Contextual

Para o operador do sistema não se perder, a lista da esquerda e o painel da direita precisam andar em perfeita sincronia.

### Fluxo de Interação Recomendado:

1. **O usuário altera o Dropdown no painel da direita:** Ele muda de *Utilização DL* para *Usuários Ativos*.
2. **A coluna de dados da lista (esquerda) muda o cabeçalho e o valor:** O título da coluna passa a ser "Share de Usuários" (ou apenas "Participação") e recalcula os valores para a porcentagem de peso de cada site.
3. **Ordenação Automática (Opcional, mas altamente recomendada):** A lista pode se reordenar para trazer os sites com maior peso (mais usuários ou mais tráfego) para o topo. Assim, os sites mais importantes da sua rede ganham destaque imediato.

---

## Resumo da estrutura de dados para o Front-end

Para colocar a ideia do seu chefe em prática sem quebrar o layout, use a seguinte convenção visual na coluna de valores da lista da esquerda:

| Se o Dropdown da direita for... | A lista da esquerda mostra... | Tipo de Indicador (Bolinha) |
| --- | --- | --- |
| **Utilização DL / UL** | Média ou Pior Célula (%) | Status de Alerta (Verde/Amarelo/Vermelho) |
| **Throughput DL / UL** | Média do Site (Mbps) | Status de Alerta baseado em thresholds |
| **Acessibilidade** | Pior Célula do Site (%) | Status de Alerta (Grave se < 95%, por exemplo) |
| **RSRP / RSRQ Médio** | Média de Sinal (dBm / dB) | Nível de cobertura (Excelente a Ruim) |
| **Usuários Ativos** | **% de participação no total** | Indicador neutro (ou degradado se houver queda abrupta) |
| **Volume de Tráfego DL / UL** | **% de participação no tráfego total** | Indicador neutro (foco em volumetria) |
