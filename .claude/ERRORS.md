# ERRORS.md

Log de falhas e erros encontrados durante o desenvolvimento do projeto SmartEvents, incluindo causa raiz, lição aprendida e regra de prevenção.

## Histórico de Erros e Falhas

### [2026-06-01] Erro 500 no iManager FARS ao buscar resultados de Trace com ordenação
- **Problema:** Chamadas para `/traceresult/query/sort` retornam erro HTTP 500 com a mensagem `"server communication linkage of task"`.
- **Causa Raiz:** O endpoint `/traceresult/query/sort` serve apenas para reordenar uma sessão de busca já ativa no iManager FARS. Se disparado sem realizar o fluxo de inicialização (`pre-check`) na mesma sessão, o servidor perde o vínculo e falha.
- **Solução / Regra de Prevenção:** Nunca use `/traceresult/query/sort` para buscar os dados de trace. Em vez disso, implemente o fluxo obrigatório de 4 passos no `HttpCollector`:
  1. `GET /rest/oss/access/fars/v1/traceresult/pre-check?taskId={id}&queryType=0` para iniciar a sessão.
  2. `GET /rest/oss/access/fars/v1/traceresult/query/result?startRow=0&pageSize=1&taskId={id}&msgId=1` para obter o `msgId` interno.
  3. `POST /rest/oss/access/fars/v1/traceresult/query/filter-by-cols` enviando o payload JSON adequado (filtrando por `Message Type` = `RRC_MEAS_RPRT` e `isAscend` = `false`).
  4. Para obter os detalhes dos relatórios de medição, consuma `GET /rest/oss/access/fars/v1/traceresult/query/msg-explain-info` passando o `rowNo` correto (1-based index em relação à visualização filtrada).

### [2026-06-01] Loop infinito de renovação de sessão — coletas (KPI e VIP) param de atualizar
- **Problema:** Nenhuma coleta atualizava. Logs mostravam `[renew] Sessão renovada com sucesso` seguido imediatamente de `Redirecionamento para SSO detectado` (monitoring) / `Sessão trace expirada (pre-check)` (trace), repetindo a cada ~30s sem nunca inserir uma medição.
- **Causa Raiz (dupla):**
  1. **Falso sucesso na renovação.** `core/session_renew.py` declarava sucesso (return 0) sempre que o cookie `bspsession` e o `roarand` fossem não-nulos. A própria página de login SSO já seta `bspsession` e expõe `u2020Showedrand`, então um login que NÃO autenticou (bloqueado por **CAPTCHA** de "não sou robô" no OSS do RJ) era certificado como sucesso. Não havia verificação pós-login.
  2. **Circuit breaker morto.** O backoff em `collector.py` só contava falha quando `returncode != 0`. Como a renovação sempre "tinha sucesso", `_renew_failures` era zerado a cada ciclo e nada pausava o loop.
- **Solução / Regra de Prevenção:**
  - **Nunca** considerar uma renovação de sessão bem-sucedida sem **provar autenticação com uma sonda REST real** (resposta JSON, não HTML/SSO). Ver `session_renew._probe_authenticated` e os exit codes `EXIT_SUCCESS=0 / EXIT_GENERIC_FAIL=1 / EXIT_NEEDS_INTERACTIVE=2`.
  - Um **CAPTCHA por imagem é irresolvível em modo headless** — a renovação 100% automática não é possível para OSS com CAPTCHA. Detecte (`_still_on_login`) e retorne `EXIT_NEEDS_INTERACTIVE`; o collector abre um navegador VISÍVEL (`HttpCollector.run_interactive_reauth`, single-flight + cooldown) para o operador resolver o CAPTCHA, e emite **um** alerta acionável (`_raise_reauth_alert`).
  - Sempre tratar o caso **"renovou mas continua inválido"**: se a sessão recém-renovada ainda falha a validação, engatar backoff em vez de re-renovar (evita o loop). Ver `_engage_backoff`.
  - Preferir o `roarand` capturado do **header de uma requisição REST autenticada**; usar `sessionStorage['u2020Showedrand']` apenas como fallback.
