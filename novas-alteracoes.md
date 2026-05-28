# Alterações Pendentes

## 1. Erro na sincronização
A sincronização não está sendo executada efeitivamente. Foi a primeira vez que abri o sistema no dia e apareceu a seguinte mensagem, o que aparentemente cancelou a sincronização dos dados dos VIPs:
2026-05-28 09:02:35,598 [INFO] core.collector: [renew/monitoring] Iniciando Playwright headless (timeout=120s) — script: get_session.py
2026-05-28 09:02:57,657 [INFO] core.collector: [renew/monitoring] Playwright renovou a sessão com sucesso.
**2026-05-28 09:02:57,658 [INFO] core.collector: [renew/trace] Sessão já renovada por outra thread. Recarregando sessão local sem rodar Playwright**
