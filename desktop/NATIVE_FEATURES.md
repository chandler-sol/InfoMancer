# Native desktop integration notes

## Open Path

Native InfoMancer builds should expose an **Open Path** action from title/detail and inspector views when the application is running in a local desktop context and the indexed media path is available to that machine.

Expected behavior:

- TV series: open the title's root directory in the platform file manager.
- Movies: reveal the exact indexed movie file and select/highlight it when the platform/file manager supports that operation. Do not merely open a large containing directory and leave the user to locate the file manually.
- If the action is invoked from a specific file/edition row, reveal that exact file. From a movie title-level action, prefer the preferred/primary indexed file.
- Windows: use File Explorer's reveal/select behavior for movie files (for example `explorer.exe /select,<file>`), and open the series directory directly for TV.
- macOS: use Finder reveal for movie files (for example `open -R <file>`), and open the series directory directly for TV.
- Linux: first request reveal/select through the freedesktop file-manager interface when available (for example `org.freedesktop.FileManager1.ShowItems` with a file URI). Linux file managers do not provide one universal highlight command, so if reveal/select is unsupported, fall back to opening the containing directory with the desktop's normal file-manager handler.
- Hide or disable the action when the path is unavailable, offline, or does not exist locally.
- Network paths are valid only when the native machine can resolve and access them. The desktop shell should not assume that a server-side path maps to the same path locally.
- Do not expose the action in ordinary browser sessions. A web page should not be given arbitrary local filesystem-launch privileges.
- Do not assume a desktop client connected to a remote InfoMancer server can open the server's filesystem path. Only enable this when the native shell can verify that the path belongs to or is mounted on the local machine.

The current Tauri launcher intentionally does not grant shell IPC permissions to the HTTP application after it navigates into InfoMancer. Implement this feature through a narrowly scoped native bridge or another local-only mechanism rather than granting broad shell access to remote web content.
