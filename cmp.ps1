# cleanyourphone entry point for Windows: .\cmp.ps1 <step>
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$py = if (Get-Command python3 -ErrorAction SilentlyContinue) { "python3" } else { "python" }
& $py -I tools/cmp.py @args
exit $LASTEXITCODE
