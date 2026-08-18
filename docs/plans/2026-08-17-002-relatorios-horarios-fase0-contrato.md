# Fase 0 — Contrato de Dados e Paridade (Relatórios Horários)

**Objetivo:** Comprovar equivalência numérica dos KPIs entre Smart Events e as ferramentas oficiais (Excel/MAOS) antes de iniciar a implementação do layout (Fase 1 e 2).

## 1. Insumos Pendentes
Para concluir a Fase 0 de forma definitiva, precisamos que a operação nos forneça:

- [ ] Arquivo Excel real com macro e abas de indicadores.
- [ ] Export MAE que alimenta a planilha (referente ao mesmo período).
- [ ] Export MAOS do mesmo período (se utilizado).
- [ ] Imagem/Dashboard final de referência correspondente ao período (para guiar o escopo visual no futuro).
- [ ] Confirmação de thresholds, regras de arredondamento oficiais e direção de cada KPI.
- [ ] Relação das tasks PM 4G, NR Cell e NR DU Cell por regional.
- [ ] Inventário das células que compõem cada evento.
- [ ] Assets de marca do evento (logo, banner, cor principal).

## 2. Catálogo de Fórmulas e Regras de Agregação

Com base na base de código atual (`core/kpi_formulas.py`) e nos arquivos de texto fornecidos no projeto, detalhamos a regra de cada KPI.

> **Regra de agregação horária (EVENT/HOUR):**
> KPIs calculados a partir de divisões (taxas de sucesso, drop, disponibilidade, PRB) **NÃO** podem ser agregados fazendo média dos valores de KPI já persistidos. Eles exigem o **recálculo** a partir dos contadores brutos (numerador e denominador) somados durante o período de 1 hora.

### 2.1 4G
| Métrica | Nome | Unidade | Regra de Agregação Horária | Contadores Necessários |
|---------|------|---------|--------------------|------------------------|
| `accessibility` | Acessibilidade de Dados | % | Recálculo | L.RRC.ConnReq.Succ, L.RRC.ConnReq.Att, L.E-RAB.SuccEst, L.E-RAB.AttEst, L.S1Sig.ConnEst.Succ, L.S1Sig.ConnEst.Att |
| `availability` | Availability | % | Recálculo | L.Cell.Unavail.Dur.Sys, L.Cell.Unavail.Dur.Manual |
| `drop_rate` | Drop Dados | % | Recálculo | L.E-RAB.AbnormRel, L.E-RAB.AbnormRel.MME, L.E-RAB.NormRel, L.E-RAB.Rel.MME |
| `utilization_dl`| DL PRB Utility | % | Recálculo | L.ChMeas.PRB.DL.Used.Avg, L.ChMeas.PRB.DL.Avail |
| `utilization_ul`| UL PRB Utility | % | Recálculo | L.ChMeas.PRB.UL.Used.Avg, L.ChMeas.PRB.UL.Avail |
| `interference_ul`| Interferência | dBm | Média ponderada / Média | L.UL.Interference.Avg |
| `throughput_dl` | Throughput DL | Mbit/s | Recálculo / Média | L.Thrp.bits.DL, L.Thrp.bits.DL.LastTTI, L.Thrp.Time.DL.RmvLastTTI |
| `throughput_ul` | Throughput UL | Mbit/s | Recálculo / Média | L.Thrp.bits.UL, L.Thrp.bits.UE.UL.LastTTI, L.Thrp.Time.UE.UL.RmvLastTTI |
| `user_count` | UE médio | usuários | Média no tempo, Soma no espaço | L.Traffic.User.Avg |

*\*Nota de validação:* O throughput horário precisará de uma verificação extra para garantir que a soma volumétrica dividida pelo tempo da hora bata com a métrica de throughput desejada pelo cliente. O `user_count` também demanda validação da forma oficial de agregação (média no tempo e soma nas células).

### 2.2 5G (SA e NSA)
| Métrica | Nome | Unidade | Regra de Agregação Horária | Contadores Necessários |
|---------|------|---------|--------------------|------------------------|
| `accessibility` | Acessibilidade cons. RRC Inactive | % | Recálculo | N.RRC.SetupReq.Succ, N.RRC.ResumeReq.Succ, N.RRC.SetupReq.Att... (NR Cell) |
| `drop_rate` | Drop cons. RRC Inactive | % | Recálculo | N.QosFlow.AbnormRel, N.QosFlow.NormRel... (NR Cell) |
| `utilization_dl`| DL PRB Utility | % | Recálculo | N.PRB.DL.Used.Avg, N.PRB.DL.Avail.Avg (NR DU Cell) |
| `utilization_ul`| UL PRB Utility | % | Recálculo | N.PRB.UL.Used.Avg, N.PRB.UL.Avail.Avg (NR DU Cell) |
| `throughput_dl` | Throughput DL | *pendente* | Recálculo / Média | N.ThpVol.DL, N.ThpVol.DL.LastSlot, N.ThpTime.DL.RmvLastSlot |
| `throughput_ul` | Throughput UL | *pendente* | Recálculo / Média | N.ThpVol.UL, N.ThpVol.UE.UL.SmallPkt, N.ThpTime.UE.UL.RmvSmallPkt |
| `user_count` | User Médio | usuários | Média no tempo, Soma no espaço | N.User.RRCConn.Avg |
| `availability` | Availability | % | Recálculo | N.Cell.Avail.Dur |
| `interference_ul`| UL Interference Médio | dBm | Média ponderada / Média | N.UL.NI.Avg |

*\*Nota de validação:* Unidades de Throughput DL/UL e Volume de Tráfego 5G requerem revisão. Lacunas apontadas no plano mestre devem ser testadas aqui.

## 3. Estrutura dos Casos de Teste de Paridade

Assim que os insumos forem recebidos, criaremos um dataset de referência para executar as comparações a seguir.

**Procedimento Planejado:**
1. Ingerir o *Export MAE* recebido no Smart Events (simulando a coleta histórica de pelo menos 3 horas).
2. Realizar a sumarização (agrupamento `EVENT/HOUR`) extraindo os contadores brutos necessários.
3. Aplicar as regras definidas na Seção 2 e comparar com o respectivo valor na Planilha Excel oficial para as mesmas 3 horas.
4. Validar os critérios de aceitação:
   - *Contagens (Volumetria)*: Igualdade exata.
   - *Percentuais (Acessibilidade, Drop, Disponibilidade)*: Tolerância de `0,01` ponto percentual.
   - *Throughput/PRB/Interferência*: Conferir truncamento e arredondamentos aplicados no relatório final.

## 4. Próximos Passos
Aguardamos o upload/envio dos insumos pendentes (`Excel real com macro`, `Export MAE/MAOS`, `Dashboard final` e `Lista de Células/Tasks`) para realizar os testes de paridade descritos na Seção 3. Após validação bem-sucedida, a Fase 0 estará finalizada e podemos avançar para a Fase 1 (Persistência e consolidação horária).
