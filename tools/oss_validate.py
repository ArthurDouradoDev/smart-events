"""
tools/oss_validate.py — Validador standalone da conexão com a OSS (iManager).

Valida uma coleta (KPI e/ou VIP) de ponta a ponta SEM subir o app desktop:

  1. Busca o evento (task id + IP/regional) do SERVIDOR FastAPI (/api/events, /api/vips).
  2. Resolve a regional/IP (SP 10.220.50.9 / RJ 10.220.30.9) — usa a do evento, ou --region.
  3. Roda o login HEADLESS (Playwright). CAPTCHA é ignorado por ora: se bloquear, o
     validador segue usando a sessão já existente (data/session*.json), se houver.
  4. Executa a coleta real (HttpCollector) com DIAGNÓSTICO HTTP ligado e imprime um
     relatório PASS/WARN/FAIL por etapa — incluindo URL/headers/cookies/corpo nos 404
     (ver relatorio-erro-404.md). Exit code 0 = tudo OK, 1 = alguma etapa falhou.

Pré-requisitos: VPN do cliente ativa (para alcançar o iManager) e servidor FastAPI no ar
com ao menos 1 evento + VIPs. O validador NÃO precisa do pywebview nem do scheduler.

Exemplos:
  python tools/oss_validate.py --kind both --region SP
  python tools/oss_validate.py --kind vip  --region RJ
  python tools/oss_validate.py --kind kpi  --event <id> --server http://127.0.0.1:8000
  python tools/oss_validate.py --session-only --region RJ      # só login/sessão
  python tools/oss_validate.py --no-login --kind kpi           # usa sessão existente
"""

import argparse
import json
import logging
import sys
from pathlib import Path

# Permite rodar como script solto a partir de qualquer cwd.
sys.path.insert(0, str(Path(__file__).parent.parent))

import requests
import urllib3

from core import database as db
from core import session_renew
from core.session_renew import EXIT_SUCCESS, EXIT_GENERIC_FAIL, EXIT_NEEDS_INTERACTIVE
from core.collector import (
    HttpCollector,
    _REGIONAL_BASE_URLS,
    _DEFAULT_BASE_URL,
)

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger("oss_validate")


# ── Relatório ────────────────────────────────────────────────────────
class Report:
    """Acumula etapas (PASS/WARN/FAIL) e imprime um resumo no final."""

    PASS, WARN, FAIL = "PASS", "WARN", "FAIL"
    _MARK = {"PASS": "[ OK ]", "WARN": "[WARN]", "FAIL": "[FALHA]"}

    def __init__(self):
        self.steps: list[tuple[str, str, str]] = []

    def add(self, status: str, name: str, detail: str = ""):
        self.steps.append((status, name, detail))
        line = f"  {self._MARK.get(status, status)} {name}"
        if detail:
            line += f" — {detail}"
        print(line)

    def ok(self) -> bool:
        """True se nenhuma etapa falhou (WARN não reprova)."""
        return all(s != self.FAIL for s, _, _ in self.steps)

    def summary(self):
        n_fail = sum(1 for s, _, _ in self.steps if s == self.FAIL)
        n_warn = sum(1 for s, _, _ in self.steps if s == self.WARN)
        print("\n" + "=" * 64)
        if n_fail:
            print(f"RESULTADO: FALHOU — {n_fail} etapa(s) com falha, {n_warn} aviso(s).")
        elif n_warn:
            print(f"RESULTADO: OK COM AVISOS — {n_warn} aviso(s).")
        else:
            print("RESULTADO: OK — todas as etapas passaram.")
        print("=" * 64)


def _header(text: str):
    print("\n" + "-" * 64)
    print(text)
    print("-" * 64)


# ── Etapas ───────────────────────────────────────────────────────────
def _pick_event(rep: Report, event_id: str | None) -> str | None:
    """Escolhe o evento: o --event informado; senão o 1º ATIVO; senão o 1º cadastrado."""
    events = db.get_events()
    if not events:
        rep.add(Report.FAIL, "Selecionar evento",
                "nenhum evento no banco após o sync. Cadastre um evento no servidor.")
        return None
    if event_id:
        match = next((e for e in events if e.get("id") == event_id), None)
        if not match:
            ids = ", ".join(e.get("id", "?") for e in events)
            rep.add(Report.FAIL, "Selecionar evento",
                    f"evento '{event_id}' não encontrado. Disponíveis: {ids}")
            return None
        chosen = match
    else:
        active = [e for e in events if (e.get("status") or "").upper() == "ACTIVE"]
        chosen = active[0] if active else events[0]
    rep.add(Report.PASS, "Selecionar evento",
            f"id={chosen.get('id')} nome={chosen.get('name')!r} status={chosen.get('status')}")
    return chosen.get("id")


def _resolve_target(oss: dict, region_override: str | None) -> tuple[str, str]:
    """Resolve (region, base_url). Precedência: --region > oss.base_url > mapa[oss.region]."""
    if region_override:
        region = region_override.upper()
        base_url = _REGIONAL_BASE_URLS.get(region, _DEFAULT_BASE_URL)
        return region, base_url
    region = (oss.get("region") or "").upper()
    base_url = oss.get("base_url") or _REGIONAL_BASE_URLS.get(region, _DEFAULT_BASE_URL)
    return region, base_url


def _connectivity_check(rep: Report, base_url: str):
    """TLS GET rápido na base_url para distinguir falha de VPN de falha do app."""
    try:
        resp = requests.get(base_url, verify=False, timeout=8)
        rep.add(Report.PASS, "Conectividade (VPN)",
                f"{base_url} respondeu HTTP {resp.status_code}")
    except requests.exceptions.Timeout:
        rep.add(Report.FAIL, "Conectividade (VPN)",
                f"timeout ao acessar {base_url}. VPN do cliente conectada?")
    except requests.exceptions.ConnectionError as e:
        rep.add(Report.FAIL, "Conectividade (VPN)",
                f"sem rota até {base_url} ({type(e).__name__}). Verifique a VPN.")
    except Exception as e:
        rep.add(Report.WARN, "Conectividade (VPN)", f"{type(e).__name__}: {e}")


def _report_session_file(rep: Report, session_file: Path):
    """Informa o estado do session.json (cookies/roarand por módulo) antes da coleta."""
    if not session_file.exists():
        rep.add(Report.WARN, "Arquivo de sessão",
                f"{session_file.name} não existe — faça o login (sem --no-login) ou reauth.")
        return
    try:
        data = json.loads(session_file.read_text(encoding="utf-8"))
    except Exception as e:
        rep.add(Report.WARN, "Arquivo de sessão", f"ilegível: {e}")
        return
    parts = []
    for mod in ("monitoring", "trace"):
        m = data.get(mod, {}) or {}
        has_cookies = bool(m.get("cookies"))
        has_roarand = bool(m.get("roarand"))
        parts.append(f"{mod}: cookies={'sim' if has_cookies else 'NAO'} "
                     f"roarand={'sim' if has_roarand else 'NAO'}")
    ok = all((data.get(m, {}) or {}).get("cookies") and (data.get(m, {}) or {}).get("roarand")
             for m in ("monitoring", "trace"))
    rep.add(Report.PASS if ok else Report.WARN, "Arquivo de sessão",
            f"{session_file.name} — " + " | ".join(parts))


def _do_login(rep: Report, base_url: str, region: str, session_file: Path) -> bool:
    """Login headless. Retorna True se a coleta pode prosseguir (sessão utilizável)."""
    logger.info("Iniciando login headless em %s (regional=%s)...", base_url, region or "?")
    try:
        rc = session_renew.run(
            headless=True, module="both",
            base_url=base_url, session_file=str(session_file), region=region,
        )
    except Exception as e:
        rep.add(Report.FAIL, "Login headless", f"exceção: {type(e).__name__}: {e}")
        return session_file.exists()

    if rc == EXIT_SUCCESS:
        rep.add(Report.PASS, "Login headless", "sessão renovada e autenticada (sonda REST OK)")
        return True
    if rc == EXIT_NEEDS_INTERACTIVE:
        rep.add(Report.WARN, "Login headless",
                "bloqueado por CAPTCHA/SSO (esperado p/ regionais com captcha). "
                "Seguindo com a sessão existente, se houver. "
                "Para semear a sessão 1x: rode em modo visível "
                "(python scratch/get_session.py --module both "
                f"--base-url {base_url} --region {region or ''}).")
        return session_file.exists()
    rep.add(Report.FAIL, "Login headless", f"falha genérica (exit={rc}). Veja os logs acima.")
    return session_file.exists()


def _validate_kpi(rep: Report, collector: HttpCollector):
    try:
        result = collector.collect_kpis()
    except Exception as e:
        rep.add(Report.FAIL, "Coleta KPI", f"exceção: {type(e).__name__}: {e}")
        return
    rows = result.measurements
    if rows:
        cells = len({r.get("cell_id") for r in rows})
        rep.add(Report.PASS, "Coleta KPI",
                f"{len(rows)} medições em {cells} célula(s).")
    else:
        rep.add(Report.FAIL, "Coleta KPI",
                f"estado={result.state}; 0 medições. "
                f"{result.cause or 'Veja o diagnóstico [http/monitoring] acima'} "
                "(status/URL/corpo) — 404 indica rota/task id inválidos ou sessão bloqueada.")


def _validate_vip(rep: Report, collector: HttpCollector):
    tasks = collector._load_vips_by_task()
    if not tasks:
        rep.add(Report.WARN, "Coleta VIP",
                "nenhum VIP com task_id para esta regional. "
                "Cadastre VIPs (com task_id) para este OSS no servidor.")
        return
    try:
        rows = collector.collect_vips(mode="full")
    except Exception as e:
        rep.add(Report.FAIL, "Coleta VIP", f"exceção: {type(e).__name__}: {e}")
        return
    if rows.measurements:
        vips = len({r.get("vip_name") for r in rows.measurements})
        in_event = sum(1 for r in rows.measurements if r.get("in_event"))
        details = getattr(collector, "_last_vip_subscription", [])
        trace = "; ".join(
            f"task {d['task_id']} ({d['vip']}) msgId={d['msg_id']} "
            f"serial={d['serial_initial']}->{d['serial_final']} "
            f"linhas={d['row_initial']}->{d['row_final']}/{d['record_count']} "
            f"RRC={d['rrc_measurements']} decodificadas={d['decoded']} "
            f"indecifráveis={d['undecoded']}{' BACKLOG' if d['backlog'] else ''}"
            for d in details
        )
        rep.add(Report.PASS, "Coleta VIP",
                f"{len(rows.measurements)} medições de {vips}/{len(tasks)} VIP(s), {in_event} no evento. {trace}")
    else:
        rep.add(Report.FAIL, "Coleta VIP",
                f"estado={rows.state}; 0 medições de {len(tasks)} task(s). "
                f"{rows.cause or 'Veja o diagnóstico [http/trace] acima (pre-check/query/result/filter-by-cols).'}")


# ── Main ─────────────────────────────────────────────────────────────
def main() -> int:
    parser = argparse.ArgumentParser(
        description="Valida a conexão e a coleta (KPI/VIP) com a OSS sem subir o app.")
    parser.add_argument("--event", default=None,
                        help="ID do evento (default: 1º ATIVO, senão o 1º cadastrado)")
    parser.add_argument("--region", default=None, choices=["SP", "RJ", "sp", "rj"],
                        help="Força a regional/IP (sobrescreve a do evento)")
    parser.add_argument("--kind", default="both", choices=["kpi", "vip", "both"],
                        help="O que validar (default: both)")
    parser.add_argument("--server", default=None,
                        help="URL do servidor FastAPI (default: data/settings.json)")
    parser.add_argument("--no-login", action="store_true",
                        help="Não faz login; usa a sessão existente (data/session*.json)")
    parser.add_argument("--session-only", action="store_true",
                        help="Só valida conectividade + login/sessão; não coleta")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    region_override = args.region.upper() if args.region else None
    rep = Report()

    # 1) Preparação: DB + servidor.
    _header("1. Preparação (banco + servidor)")
    db.init_db()
    if args.server:
        settings = db.get_settings()
        settings["server_url"] = args.server.rstrip("/")
        db.save_settings(settings)
    server_url = (db.get_settings().get("server_url") or "").strip()
    if not server_url:
        rep.add(Report.FAIL, "Servidor configurado",
                "server_url vazio. Use --server http://IP:8000 ou configure data/settings.json.")
        rep.summary()
        return 1
    rep.add(Report.PASS, "Servidor configurado", server_url)

    sync = db.sync_events_from_server()
    if sync.get("erros"):
        rep.add(Report.WARN, "Sync de eventos", json.dumps(sync, ensure_ascii=False))
    else:
        rep.add(Report.PASS, "Sync de eventos", json.dumps(sync, ensure_ascii=False))

    # 2) Evento + alvo (regional/IP).
    _header("2. Evento e regional/IP")
    event_id = _pick_event(rep, args.event)
    if not event_id:
        rep.summary()
        return 1
    event_config = db.get_event(event_id)
    if not event_config:
        rep.add(Report.FAIL, "Carregar config do evento", f"config_json vazio p/ {event_id}")
        rep.summary()
        return 1

    oss = event_config.get("oss", {}) or {}
    region, base_url = _resolve_target(oss, region_override)
    session_file = HttpCollector._resolve_session_file(base_url)
    rep.add(Report.PASS, "Resolver regional/IP",
            f"regional={region or '(n/d)'} base_url={base_url} sessão={session_file.name}")

    # VIPs da regional (popula event_vips/vips p/ a coleta de VIP).
    if args.kind in ("vip", "both"):
        vsync = db.sync_vips_from_server(region or None)
        rep.add(Report.WARN if vsync.get("erros") else Report.PASS,
                "Sync de VIPs", json.dumps(vsync, ensure_ascii=False))

    # 3) Conectividade + login/sessão.
    _header("3. Conectividade e login")
    _connectivity_check(rep, base_url)
    if args.no_login:
        rep.add(Report.WARN, "Login headless", "pulado (--no-login): usando sessão existente.")
    else:
        _do_login(rep, base_url, region, session_file)
    _report_session_file(rep, session_file)

    if args.session_only:
        rep.summary()
        return 0 if rep.ok() else 1

    # 4) Coleta com diagnóstico HTTP ligado.
    _header("4. Coleta (diagnóstico HTTP ligado)")
    HttpCollector.http_debug = True
    collector = HttpCollector(event_config, base_url)
    if args.kind in ("kpi", "both"):
        _validate_kpi(rep, collector)
    if args.kind in ("vip", "both"):
        _validate_vip(rep, collector)

    rep.summary()
    return 0 if rep.ok() else 1


if __name__ == "__main__":
    sys.exit(main())
