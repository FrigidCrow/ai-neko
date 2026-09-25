ai-neko — M1 text and guide preview / Windows x64

1. Extract the entire ZIP to a separate folder. Keep _internal beside ai-neko.exe.
2. Double-click Start ai-neko.cmd. Your browser opens the local chat page.
3. Configure your OpenAI-compatible model endpoint/name/key. For guide search,
   also configure a Tavily key. Provider charges and network availability apply.
4. Choose text chat or guide search. Inspect source cards and read-status evidence.
5. Use the page's Quit application button or Stop service.cmd to stop the backend.
   Closing only the browser tab leaves the backend running.

Python, Node and uv are not required. Check foundation.cmd runs a disposable
synthetic diagnostic; it does not test real cloud models or search quality.

Conversation/config data live in %LOCALAPPDATA%\ai-neko, separate from this ZIP.
Windows keys use the application's isolated Windows Credential Manager entries.
Do not share your runtime/connection.json: it contains the local access token.
The model receives submitted conversation context; the search provider receives
queries. Public page fetching does not use browser accounts or cookies.

M1 provides text chat, session history, cancellation and public guide tools.
Long-term fact memory, voice, desktop avatar, tray and computer control are absent.
Synthetic CI and frozen-executable probes are separate from real-provider quality
acceptance and Windows 11 physical-machine testing. See Release notes for status.
This preview is unsigned. Build-info and SHA256SUMS identify exact source/artifacts.

Old releases remain available. Keep the old program folder for rollback; do not
remove your data directory when replacing the program. Automatic data migration
or cross-application imports are not provided in this preview.
