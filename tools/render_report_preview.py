"""Ferramenta para renderizar e exportar relatórios horários em PNG usando Playwright.

Executa o template HTML em modo snapshot determinístico em resolução 1080x1600 px.
"""

from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path
from playwright.async_api import async_playwright

TEMPLATE_PATH = Path(__file__).resolve().parent.parent / "templates" / "report" / "index.html"
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent.parent / "outputs" / "report_previews"


async def render_report_to_png(
    theme_id: str = "default_purple",
    output_path: Path | None = None,
    width: int = 1080,
    height: int = 1600,
) -> Path:
    """Renderiza uma variante do relatório horário para imagem PNG."""
    if output_path is None:
        DEFAULT_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        output_path = DEFAULT_OUTPUT_DIR / f"report_{theme_id}.png"
    else:
        output_path.parent.mkdir(parents=True, exist_ok=True)

    file_url = TEMPLATE_PATH.as_uri()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        # Configura viewport padrão
        context = await browser.new_context(
            viewport={"width": width, "height": height},
            device_scale_factor=1.0,
        )
        page = await context.new_page()

        # Abre o template local
        await page.goto(file_url, wait_until="networkidle")

        # Seleciona o tema e oculta controles para snapshot
        await page.evaluate(f"""
            () => {{
                document.body.classList.add('export-mode');
                const theme = REPORT_THEMES['{theme_id}'] || REPORT_THEMES.default_purple;
                const dataset = REPORT_DATASETS['{theme_id}'] || REPORT_DATASETS.default_purple;
                window.renderReport(dataset, theme);
            }}
        """)

        # Aguarda 300ms para estabilizar renderização de animações/arcos
        await page.wait_for_timeout(300)

        # Captura exatamente o elemento #report-canvas
        canvas_element = await page.query_selector("#report-canvas")
        if canvas_element:
            await canvas_element.screenshot(path=str(output_path))
        else:
            await page.screenshot(path=str(output_path), full_page=True)

        await browser.close()

    print(f"[OK] Relatorio renderizado com sucesso: {output_path}")
    return output_path


async def render_all_presets(output_dir: Path = DEFAULT_OUTPUT_DIR):
    """Renderiza todas as 6 variantes de referência."""
    presets = [
        "default_purple",
        "romaria_muquem",
        "exposul",
        "moto_week",
        "stock_car",
        "joao_rock",
    ]
    output_dir.mkdir(parents=True, exist_ok=True)

    for preset in presets:
        out_file = output_dir / f"report_{preset}.png"
        await render_report_to_png(theme_id=preset, output_path=out_file)


def main():
    parser = argparse.ArgumentParser(description="Renderizador de Relatórios Horários Smart Events")
    parser.add_argument("--theme", default="all", help="ID do tema (default_purple, romaria_muquem, exposul, moto_week, stock_car, joao_rock ou 'all')")
    parser.add_argument("--output", default=None, help="Caminho de saída para o PNG")
    args = parser.parse_args()

    if args.theme == "all":
        asyncio.run(render_all_presets())
    else:
        out_path = Path(args.output) if args.output else None
        asyncio.run(render_report_to_png(theme_id=args.theme, output_path=out_path))


if __name__ == "__main__":
    main()
