# Limpa o cache em disco do WebView2 usado pela janela da interface (data/EBWebView),
# sem apagar cookies/sessao (Network/Cookies, Login Data, Local Storage) nem o perfil
# de automacao do coletor (data/browser_profile).
# Uso: feche o app antes de rodar, depois: powershell -File tools/clear_ui_cache.ps1

$base = Join-Path $PSScriptRoot "..\data\EBWebView\Default"

foreach ($dir in @("Cache", "Code Cache")) {
    $path = Join-Path $base $dir
    if (Test-Path $path) {
        Remove-Item -Recurse -Force -Confirm:$false $path
        Write-Output "Removido: $path"
    } else {
        Write-Output "Nao encontrado (ja limpo?): $path"
    }
}
