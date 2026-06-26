# Relatório de Inspeção HAR: `10.220.50.9.har` (Sem Filtros de Alarme)

Este relatório detalha a inspeção do tráfego de rede gravado no arquivo `10.220.50.9.har`, com o objetivo de analisar como os alarmes definidos em `lista-alarmes.csv` são carregados e exibidos na tela do sistema.

---

## 1. Visão Geral do Tráfego
* **Total de Requisições Gravadas:** 115
* **Endpoint Principal de APIs:** `https://10.220.50.9:31943/rest/fmwebsite/v1/commands`
* **Método Utilizado:** `POST`
* **Protocolo de Comunicação:** Os dados são requisitados através de chamadas de comando REST codificados por um parâmetro de comando `cmd` no corpo JSON da requisição.

---

## 2. Fluxo de Requisição de Alarmes Sem Filtro

Neste arquivo HAR, a tela de alarmes é carregada em seu estado padrão, sem qualquer filtro específico por tipo de alarme ativo. O fluxo segue as seguintes etapas:

### Passo A: Definição da Condição de Consulta (`cmd: 1102`)
A requisição `POST` em `/commands?_cmd=1102` (entrada nº 59 no arquivo HAR) é responsável por registrar o filtro e a condição de busca ativos para a sessão atual (identificada por um `modelID`).

* **Payload da Requisição (`cmd: 1102`):**
  ```json
  {
    "cmd": 1102,
    "parameters": {
      "modelID": null,
      "bspSessionId": "260847",
      "showStatistic": false,
      "additionalCondition": "{\"alarmGroupId\":{\"operation\":\"in\",\"value\":[]},\"soundInfoCond\":[{\"severity\":1,\"alarmStatus\":\"1\",\"duration\":60},{\"severity\":2,\"alarmStatus\":\"1\",\"duration\":60},{\"severity\":3,\"alarmStatus\":\"1\",\"duration\":60},{\"severity\":4,\"alarmStatus\":\"1\",\"duration\":60}]}",
      "timeMode": 3,
      "urlCondition": null,
      "expression": "",
      "autoRefresh": true,
      "isScrollLock": false,
      "condition": "{\"alarmLevel\":[\"CRITICAL\",\"MAJOR\",\"MINOR\",\"WARNING\"],\"alarmStatus\":[12,10,11,13],\"eventType\":{\"value\":[\"1\",\"2\",\"3\",\"4\",\"5\",\"6\",\"7\",\"8\",\"9\",\"10\",\"11\",\"12\",\"13\",\"14\",\"15\",\"16\"],\"operation\":\"in\"},\"specialAlarmStatus\":{\"value\":[\"0\"],\"operation\":\"in\"},\"orders\":[{\"field\":\"ColArriveUtc\",\"order\":1}]}"
    }
  }
  ```
  > [!NOTE]
  > Observe que o campo `condition` **não contém** nenhuma restrição para `alarmGroupId` ou `alarmId` específicos. Ele solicita todos os alarmes que tenham níveis de severidade críticos/maiores/menores/avisos e status ativos.

### Passo B: Obtenção dos Dados dos Alarmes (`cmd: 1103`)
Logo em seguida, o frontend faz requisições repetidas para `cmd: 1103` passando o `modelID` gerado para obter a lista de alarmes páginada (ex: `from: 1, to: 148`).

* **Exemplo de Resposta do Servidor (`cmd: 1103`):**
  Como não há filtros, a resposta retorna a lista de todos os alarmes ativos no sistema (total de **350+ alarmes**). A estrutura retornada no campo `parameters.data` contém registros com diferentes `alarmId` mapeados na `lista-alarmes.csv`.
  
  Exemplos de alarmes presentes no retorno:
  * **ID `65201`**: `PORTA ABERTA` (Grupo: BTS5900 - `268390521`)
  * **ID `26234`**: `BBU CPRI Interface Error` (Grupo: BTS5900 - `268390521`)
  * **ID `65203`**: `BATERIA EM DESCARGA` (Grupo: BTS5900 - `268390521`)
  * **ID `21825`**: `CSL Fault` (Grupo: GBTS - `8193`)
  * **ID `21392`**: `Adjacent Node IP Address Ping Failure` (Grupo: BSC6910GSM - `268390408`)

---

## 3. Conclusão da Inspeção
No arquivo HAR original (`10.220.50.9.har`), **não existe filtragem ativa de alarmes**. O sistema realiza a busca padrão de todos os eventos ativos, retornando uma lista heterogênea baseada apenas em severidade e status de ativação.
