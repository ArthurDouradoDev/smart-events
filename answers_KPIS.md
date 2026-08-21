# Respostas pendentes para os KPIS

# Parte A
## A1
Por padrão, essas tasks do monitoring são de 1 minuto mesmo. Mas gostei da sugestão de poder definir o período / intervalo. Sei que existem alguns que podem ser de 15 min ou de 1h, mas o ideal seria permitir definir ao vincular a task. Talvez um input de inteiro + um dropdown de s, min, h. Das tasks que temos do monitoring, todas são de 1 min.

## A2
é a mesma do N.ThpTime.DL, nao me lembro se segundo ou milisegundo

## A3
Faça da forma que achar melhor

## A4
Teoricamente sim, mas na OSS em que a task foi criada há um limite de 25 contadores, então acabei por deixar esse de fora.

## A5
Não, foi um bug do OSS.

# Parte B

## B1
não migrar; declarar e converter na leitura

## B2
acrescentar unit ao lado do threshold no config ({"value": 80, "unit": "%"}) para que essa classe de erro não possa voltar.
## B3
Eixo único

## B4
Acho que pode ser fixa por métrica no evento mesmo.

## B5
Só volume e throughput. user_count raramente passa de milhares.

## B6
Rotular agora e agregar depois

## B7
Sim

## B8
Manter no seletor e consertar a apresentação

## B9
Recalculate

# Parte C

## C1
É configuração intencional.

## C2
Pode corrigir