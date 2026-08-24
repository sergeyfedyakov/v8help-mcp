# -*- powershell -*-
# Быстрый старт v8help с эмбеддингами через Ollama.
# Устанавливает Ollama (если нет), поднимает serve, скачивает модель эмбеддингов
# и выводит готовый конфиг для v8help.toml / MCP-тула config_set.
#
# Использование:
#   .\scripts\setup-ollama.ps1
#   .\scripts\setup-ollama.ps1 -Model nomic-embed-text -Dims 768
param(
    [string]$Model = "bge-m3",
    [int]$Dims = 1024,
    [int]$Port = 11434,
    [int]$BatchSize = 64
)

$ErrorActionPreference = "Stop"
$BaseUrl = "http://localhost:$Port/v1"

function Test-OllamaInstalled {
    if (Get-Command ollama -ErrorAction SilentlyContinue) { return $true }
    foreach ($p in @(
        "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe",
        "$env:ProgramFiles\Ollama\ollama.exe"
    )) {
        if (Test-Path $p) { return $true }
    }
    return $false
}

Write-Host "== v8help: настройка Ollama ==" -ForegroundColor Cyan

# 1. Установка Ollama
if (-not (Test-OllamaInstalled)) {
    Write-Host "[1/3] Ollama не найден. Устанавливаю через winget..."
    winget install -e --id Ollama.Ollama --accept-source-agreements --accept-package-agreements
    if ($LASTEXITCODE -ne 0) {
        throw "Не удалось установить Ollama. Поставьте вручную: https://ollama.com"
    }
} else {
    Write-Host "[1/3] Ollama найден."
}

# 2. Запуск сервера
$running = $false
try {
    Invoke-WebRequest -Uri "$BaseUrl/version" -TimeoutSec 3 -UseBasicParsing | Out-Null
    $running = $true
} catch { }

if (-not $running) {
    Write-Host "[2/3] Запускаю 'ollama serve'..."
    Start-Process ollama -ArgumentList "serve" -WindowStyle Hidden
    Start-Sleep -Seconds 3
} else {
    Write-Host "[2/3] Сервер Ollama уже работает ($BaseUrl)."
}

# 3. Модель эмбеддингов
Write-Host "[3/3] Скачиваю модель эмбеддингов '$Model'..."
ollama pull $Model

# 4. Вывод рекомендованного конфига
$snippet = @"
[embedder.index]
model = "$Model"
base_url = "$BaseUrl"
api_key = ""
dims = $Dims
batch_size = $BatchSize

[embedder.query]
model = "$Model"
base_url = "$BaseUrl"
api_key = ""
dims = $Dims
batch_size = $BatchSize
"@

Write-Host ""
Write-Host "Готово. Добавьте в v8help.toml:" -ForegroundColor Green
Write-Host "-----"
Write-Host $snippet
Write-Host "-----"
Write-Host ""
Write-Host "Либо через MCP-тул config_set:" -ForegroundColor Green
Write-Host ('config_set {{ values: {{ "search.backend": "hybrid", "embedder.index.model": "{0}", "embedder.index.base_url": "{1}", "embedder.index.dims": {2} }} }}' -f $Model, $BaseUrl, $Dims)
Write-Host ""
Write-Host "После этого пересоберите индекс: v8help build" -ForegroundColor Cyan
