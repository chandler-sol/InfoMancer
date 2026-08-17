# Native desktop integration notes

## Open Path

Native InfoMancer builds should expose an **Open Path** action from title/detail and inspector views when the application is running in a local desktop context and the indexed media path is available to that machine.

Expected behavior:

- Windows: open the series/movie directory in File Explorer.
- macOS: reveal/open the directory in Finder.
- Linux: open the directory in the user's default file manager.
- Prefer the title root folder for TV series and the containing folder for a movie file.
- Hide or disable the action when the path is unavailable, offline, or does not exist locally.
- Do not expose the action in ordinary browser sessions. A web page should not be given arbitrary local filesystem-launch privileges.
- Do not assume a desktop client connected to a remote InfoMancer server can open the server's filesystem path. Only enable this when the native shell can verify that the path belongs to the local machine.

The current Tauri launcher intentionally does not grant shell IPC permissions to the HTTP application after it navigates into InfoMancer. Implement this feature through a narrowly scoped native bridge or another local-only mechanism rather than granting broad shell access to remote web content.
