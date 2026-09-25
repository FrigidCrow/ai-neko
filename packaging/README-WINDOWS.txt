ai-neko - Windows x64 portable M0 foundation

Extract the ENTIRE ZIP to a folder. Keep ai-neko.exe and _internal together.
No Python installation is required to run this package.

1. Double-click "Check foundation.cmd" for an offline synthetic check.
   A result containing "status": "passed" means the bundled M0 graph can
   pause, reopen its SQLite checkpoint, resume, and reject another user scope.
   This check uses disposable temporary data and removes it afterwards.
2. "Start service.cmd" starts the authenticated local foundation probe in a
   console. "Stop service.cmd" requests its graceful shutdown. This is a
   diagnostic service, not a chat screen. Default application data live under
   %LOCALAPPDATA%\ai-neko; AI_NEKO_DATA_DIR can select an absolute isolated root.
3. For command-line help: ai-neko.exe --help / ai-neko.exe graph --help

This version does NOT yet offer chat, web/game-guide search, voice, a desktop
avatar, tray controls, or keyboard/mouse automation. M1 is intended to provide
the first chat and real web-search trial. Do not enter API keys for this check.

The program never imports or automatically migrates original N.E.K.O data.
Keep your data directory when replacing the portable program folder; the ZIP
contains program files only. Portable upgrades are not yet accepted on a real
Windows 11 machine. Automated Windows Server CI does not establish that result.

build-info.json records source commit, release and app version, locked dependency
versions, build environment and pending acceptance items. SHA256SUMS.txt beside
the downloaded ZIP verifies the release assets. third-party-licenses/ contains
dependency license texts and a manifest. No N.E.K.O code or assets are bundled.

This early build is unsigned. Download only from the project's GitHub releases
or its CI artifacts. Windows 11 x64 manual acceptance remains a separate step.
