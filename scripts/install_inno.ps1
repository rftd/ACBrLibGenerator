$ErrorActionPreference = 'Stop'
$config = Get-Content "$PSScriptRoot/../config/toolchain.json" -Raw | ConvertFrom-Json
$destination = Join-Path $env:RUNNER_TEMP 'innosetup.exe'
Invoke-WebRequest -Uri $config.inno_setup.url -OutFile $destination
$actual = (Get-FileHash $destination -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actual -ne $config.inno_setup.sha256) { throw 'SHA256 inválido para Inno Setup' }
$install = Start-Process -FilePath $destination -ArgumentList '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/SP-' -Wait -PassThru
if ($install.ExitCode -ne 0) { throw "Inno Setup: instalação terminou com código $($install.ExitCode)" }
$compiler = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
if (!(Test-Path $compiler)) { throw "ISCC não encontrado em $compiler" }
"ISCC_PATH=$compiler" | Out-File -FilePath $env:GITHUB_ENV -Encoding utf8 -Append
