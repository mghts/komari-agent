# This fork publishes Linux amd64/arm64 only. Fail before downloads or service changes.
Write-Error "Windows packages are not published by mghts/komari-agent. See https://github.com/mghts/komari-agent/blob/komari-agent-1.2.60/FORK.md for supported installation methods."
exit 1
