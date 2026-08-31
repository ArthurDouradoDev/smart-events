"""Geracao, inspecao e importacao de pacotes ``.sepack`` pela linha de comando.

    python -m tools.event_package preview --events evento-a evento-b
    python -m tools.event_package build --events evento-a evento-b --output outputs/distributions
    python -m tools.event_package inspect pacote.sepack
    python -m tools.event_package import pacote.sepack --data-dir .tmp/import-test

Codigos de saida: 0 sucesso, 2 argumento invalido, 3 pacote ou selecao invalida,
4 importacao concluida com conflito preservado, 5 erro interno.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from core import event_package as ep
from core import paths


EXIT_OK = 0
EXIT_ARGUMENTS = 2
EXIT_INVALID_PACKAGE = 3
EXIT_CONFLICT = 4
EXIT_INTERNAL = 5


def _emit(value: dict, as_json: bool, lines: list[str]) -> None:
    if as_json:
        print(json.dumps(value, ensure_ascii=False, indent=2))
    else:
        for line in lines:
            print(line)


def _issue_lines(errors: list[dict], warnings: list[str]) -> list[str]:
    lines = [f"ERRO [{item['code']}] {item['message']}" for item in errors]
    lines.extend(f"AVISO {message}" for message in warnings)
    return lines


def _preview_lines(preview: ep.PackagePreview) -> list[str]:
    counts = preview.counts()
    lines = [
        f"Distribuicao: {preview.name}",
        f"Origem: {preview.source_dir}",
        "Eventos: " + ", ".join(str(event.get("id")) for event in preview.events),
        f"Clientes: {', '.join(preview.clients) or 'nenhum'}",
        f"Regionais: {counts['regions']} | sites: {counts['sites']} | celulas: {counts['cells']}"
        f" | clusters: {counts['clusters']} | tasks PM: {counts['pm_tasks']}",
        f"VIPs: {counts['vips']} (explicitos: {len(preview.explicit_vip_ids)}, "
        f"fallback legado: {len(preview.fallback_vip_ids)})",
        f"Logos: {counts['logos']}",
        "Nao entra no pacote: credenciais, sessao, cookies, bancos, logs e resultados de coleta.",
    ]
    lines.extend(_issue_lines([item.to_dict() for item in preview.errors], preview.warnings))
    return lines


def _build_preview(args) -> ep.PackagePreview:
    return ep.preview_package(
        args.source,
        args.events,
        name=args.name,
        vip_policy=args.vip_policy,
        vip_ids=args.vips,
    )


def _command_preview(args) -> int:
    preview = _build_preview(args)
    _emit(preview.to_dict(), args.json, _preview_lines(preview))
    return EXIT_OK if preview.ok else EXIT_INVALID_PACKAGE


def _command_build(args) -> int:
    preview = _build_preview(args)
    if not preview.ok:
        _emit(preview.to_dict(), args.json, _preview_lines(preview))
        return EXIT_INVALID_PACKAGE

    output = Path(args.output) if args.output else paths.distributions_dir()
    destination = output if output.suffix == ep.PACKAGE_SUFFIX else output / ep.suggested_filename(preview)
    try:
        manifest = ep.build_package(preview, destination, force=args.force)
    except ep.PackageBuildError as exc:
        payload = {"ok": False, "errors": [item.to_dict() for item in exc.errors]}
        _emit(payload, args.json, _issue_lines(payload["errors"], []))
        return EXIT_INVALID_PACKAGE

    payload = {
        "ok": True,
        "package": str(destination),
        "bytes": destination.stat().st_size,
        "sha256": ep.sha256_of(destination),
        "manifest": manifest.to_dict(),
        "warnings": preview.warnings,
    }
    lines = [
        f"Pacote gerado: {destination}",
        f"Tamanho: {payload['bytes']} bytes | SHA-256: {payload['sha256']}",
        f"Eventos: {', '.join(manifest.event_ids)}",
        f"Clientes: {', '.join(manifest.clients) or 'nenhum'}",
    ]
    lines.extend(_issue_lines([], preview.warnings))
    _emit(payload, args.json, lines)
    return EXIT_OK


def _command_inspect(args) -> int:
    report = ep.inspect_package(args.package)
    manifest = report.get("manifest") or {}
    counts = report["counts"]
    lines = [
        f"Pacote: {report['path']}",
        f"Nome: {manifest.get('name', 'N/D')} | id: {manifest.get('package_id', 'N/D')}",
        f"Gerado em: {manifest.get('created_at_utc', 'N/D')} pela versao "
        f"{manifest.get('created_by_app_version', 'N/D')}",
        f"Eventos: {counts['events']} | clientes: {counts['clientes']} | "
        f"VIPs: {counts['vips']} | logos: {counts['logos']}",
        "Assinado: sim (nao verificada nesta versao)" if report["signed"] else "Assinado: nao",
        "Situacao: valido" if report["ok"] else "Situacao: invalido",
    ]
    lines.extend(_issue_lines(report["errors"], report["warnings"]))
    _emit(report, args.json, lines)
    return EXIT_OK if report["ok"] else EXIT_INVALID_PACKAGE


def _command_import(args) -> int:
    result = ep.import_package(args.package, args.data_dir, conflict_policy=args.conflict)
    payload = result.to_dict()
    if args.report:
        report_path = Path(args.report)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    lines = ep.summary_lines(result)
    lines.extend(_issue_lines([], result.warnings))
    if result.report_path:
        lines.append(f"Relatorio: {result.report_path}")
    _emit(payload, args.json, lines)
    if not result.ok:
        return EXIT_INVALID_PACKAGE
    return EXIT_CONFLICT if result.conflicts else EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tools.event_package", description=__doc__)
    parser.add_argument("--json", action="store_true", help="Saida em JSON para pipeline e testes.")
    commands = parser.add_subparsers(dest="command", required=True)

    def add_selection(subparser):
        subparser.add_argument("--events", nargs="+", required=True, metavar="ID")
        subparser.add_argument(
            "--source", type=Path, default=None,
            help="Pasta server_data de origem (padrao: a do aplicativo).",
        )
        subparser.add_argument("--name", default=None, help="Nome da distribuicao.")
        subparser.add_argument(
            "--vip-policy", choices=ep.VIP_POLICIES, default="auto", dest="vip_policy",
        )
        subparser.add_argument(
            "--vips", nargs="*", default=None, metavar="ID",
            help="Restringe os VIPs incluidos aos ids informados.",
        )

    preview = commands.add_parser("preview", help="Mostra o que entraria no pacote.")
    add_selection(preview)
    preview.set_defaults(handler=_command_preview)

    build = commands.add_parser("build", help="Gera o arquivo .sepack.")
    add_selection(build)
    build.add_argument(
        "--output", type=Path, default=None,
        help="Arquivo .sepack ou pasta de saida (padrao: a pasta de distribuicoes).",
    )
    build.add_argument("--force", action="store_true", help="Sobrescreve um arquivo existente.")
    build.set_defaults(handler=_command_build)

    inspect = commands.add_parser("inspect", help="Valida um pacote sem importar.")
    inspect.add_argument("package", type=Path)
    inspect.set_defaults(handler=_command_inspect)

    importer = commands.add_parser("import", help="Importa um pacote na pasta de dados.")
    importer.add_argument("package", type=Path)
    importer.add_argument(
        "--data-dir", type=Path, default=None, dest="data_dir",
        help="Pasta que contem server_data/ (padrao: a do aplicativo).",
    )
    importer.add_argument("--conflict", choices=ep.CONFLICT_POLICIES, default="preserve")
    importer.add_argument("--report", type=Path, default=None, help="Grava o resultado em JSON.")
    importer.set_defaults(handler=_command_import)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except ep.PackageBuildError as exc:
        _emit(
            {"ok": False, "errors": [item.to_dict() for item in exc.errors]},
            args.json,
            _issue_lines([item.to_dict() for item in exc.errors], []),
        )
        return EXIT_INVALID_PACKAGE
    except Exception as exc:  # noqa: BLE001 - a CLI nunca deve vazar traceback cru
        print(f"ERRO interno: {exc}", file=sys.stderr)
        return EXIT_INTERNAL


if __name__ == "__main__":
    raise SystemExit(main())
