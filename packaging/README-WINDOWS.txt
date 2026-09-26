ai-neko MVP1 desktop catgirl preview (Windows x64)

1. Extract the ENTIRE ZIP to a separate folder. Keep resources and all runtime files.
2. Double-click ai-neko.exe (or Start ai-neko.cmd). No Python/Node/uv installation is needed.
3. On first launch, read the bundled Live2D terms and choose whether to accept.
4. The catgirl appears on your desktop. Click her to open text chat; drag the handle to move.
5. Open Settings beside the catgirl to configure your own model and Tavily search credentials.
6. Replies stream beside her. Stop cancels a reply. On-demand search is enabled by default;
   the model chooses whether it needs public sources. Chat-only disables search tools.
7. Close the chat panel to keep the catgirl on your desktop. Use the tray to find her or quit.

Companion settings:
- Edit the catgirl persona; saved changes apply from the next question.
- Choose and preview a window/screen, then enable observation. Each question captures one new
  frame and sends it to your configured image-capable model. Turning observation off revokes it.
- Configure compatible speech recognition and speech synthesis services separately. Select your
  microphone, press Speak to start/stop recording, and enable spoken replies to hear answers.
  Stop audio clears playback and pending speech; Ctrl+Shift+Space can be enabled for recording.
- Save, inspect, correct or forget facts in Long-term memory. Automatic extraction is opt-in
  and uses the configured model only for subsequent completed conversations.

Your own AI/search/audio services may charge for calls. No cloud keys are bundled.
Data: %LOCALAPPDATA%\ai-neko (or explicit AI_NEKO_DATA_DIR); no original N.E.K.O data is read.
The packaged catgirl is YUI Lolita from Project N.E.K.O., with upstream notices included.
See MVP1-ASSETS.md, mvp1-assets-manifest.json and resources/app/vendor/licenses for notices.
The Electron LICENSE and LICENSES.chromium.html and Python notices are included separately.

Check foundation.cmd runs a synthetic backend check. Start/Stop service.cmd are diagnostic tools.
Do not start a diagnostic backend and the desktop app against the same data directory together.
Multi-character switching, hands-free voice detection and computer control are not included.

Build/CI evidence is attached to the GitHub Actions run or versioned Release. Windows Server CI
is not Windows 11 physical-machine acceptance. Real model/search/audio quality is separate from
synthetic CI tests. New companion features still require real service and game testing.
The application is not code-signed. Old releases remain available; do not overwrite your data root.
