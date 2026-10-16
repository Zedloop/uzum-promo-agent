$scriptDir = "C:\Users\VICTUS\Desktop\uzum-promo-agent"
$env:PYTHONUTF8 = "1"
$env:PYTHONUNBUFFERED = "1"

Set-Location $scriptDir
New-Item -ItemType Directory -Force -Path "$scriptDir\data" | Out-Null

& "C:\Users\VICTUS\Desktop\uzum-review-agent\uzum-review-agent\venv\Scripts\python.exe" -u bot_listener.py *>> "$scriptDir\data\listener_log.txt"
