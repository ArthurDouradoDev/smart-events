# Relatório de Viabilidade — Regional Alternativa

**IP testado:** `https://10.220.30.9:31943`  
**Data:** 2026-05-29 12:23 UTC  
**KPI task_id:** `2001`  
**Trace task_id:** `14127`

---

## Autenticação
- **Status:** OK
- **Tempo de login:** N/A (--skip-login)s
- **Módulos capturados:** ['monitoring', 'trace']

---

## KPI — Task 2001
- **HTTP Status:** 200
- **Status:** FALHOU
- **Objetos retornados:** 0
- **Erro:** `Sessão expirada ou redirecionamento SSO`

---

## Trace VIP — Task 14127
- **Passo 1 (pre-check):** HTTP 200 — checkState=None
- **Passo 2 (query/result):** HTTP None — msgId=None
- **Passo 3 (filter):** HTTP None — 0 mensagens RRC_MEAS_RPRT
- **Passo 4 (decode):** HTTP None — RSRP=None dBm / RSRQ=None dB
- **Site receptor:** `None` | **Célula:** `None`
- **Última medição:** `None`
- **Erro:** `Sessão trace expirada no pre-check`

---

## Conclusão
- **Infraestrutura reutilizável:** **NÃO**
- **Bloqueadores encontrados:**
  - KPI inacessível: Sessão expirada ou redirecionamento SSO
  - Trace FARS falhou: Sessão trace expirada no pre-check

> Verificar os bloqueadores acima antes de prosseguir.