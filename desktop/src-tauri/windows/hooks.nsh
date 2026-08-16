; InfoMancer Windows lifecycle hooks.
; A normal uninstall is intentionally zero-residue. Updates are explicitly excluded
; so the updater can replace binaries without deleting the user's local catalog.

Var InfoMancerBackupPath

!macro NSIS_HOOK_PREINSTALL
  ; A stale or running PyInstaller sidecar can otherwise survive a same-version
  ; reinstall. The main process is handled by Tauri's normal running-app guard.
  nsExec::Exec 'taskkill /F /IM infomancer-core.exe'
  Sleep 300
  Delete "$INSTDIR\infomancer-core.exe"
!macroend

!macro NSIS_HOOK_PREUNINSTALL
  ${If} $UpdateMode == 1
    Goto infomancer_preuninstall_done
  ${EndIf}

  ; Silent uninstall is used by CI and deployment tooling. It is destructive by
  ; definition and cannot stop for an interactive recovery prompt.
  ${IfNot} ${Silent}
    ${If} $DeleteAppDataCheckboxState != 1
      MessageBox MB_ICONEXCLAMATION|MB_OK "InfoMancer uninstall removes all application-owned data. Check the confirmation box before continuing. Your movie and TV files are never touched."
      SetErrorLevel 1
      Quit
    ${EndIf}

    ; Only offer a recovery package when this installation actually owns a local
    ; database. Server-client-only installs do not have local catalog state to save.
    IfFileExists "$APPDATA\cloud.arsenik.infomancer\infomancer.db" 0 infomancer_preuninstall_done

    MessageBox MB_ICONQUESTION|MB_YESNOCANCEL "Before InfoMancer removes its local data, would you like to create a verified recovery backup? Your media files are not included or modified." IDYES infomancer_backup_choose IDNO infomancer_preuninstall_done
    Goto infomancer_uninstall_cancel

    infomancer_backup_choose:
      nsDialogs::SelectFileDialog save "$DOCUMENTS\InfoMancer-Recovery.infomancer-backup" "InfoMancer recovery package (*.infomancer-backup)|*.infomancer-backup"
      Pop $InfoMancerBackupPath
      StrCmp $InfoMancerBackupPath "" infomancer_uninstall_cancel
      DetailPrint "Creating and verifying InfoMancer recovery package..."
      nsExec::ExecToStack '"$INSTDIR\infomancer-core.exe" --data-dir "$APPDATA\cloud.arsenik.infomancer" --recovery-output "$InfoMancerBackupPath"'
      Pop $0
      Pop $1
      ${If} $0 != 0
        MessageBox MB_ICONSTOP|MB_YESNO "The recovery backup could not be created and verified. InfoMancer has not removed anything yet.$\n$\nContinue uninstalling without a backup?" IDYES infomancer_preuninstall_done IDNO infomancer_uninstall_cancel
      ${EndIf}
      DetailPrint "Recovery package verified: $InfoMancerBackupPath"
      Goto infomancer_preuninstall_done

    infomancer_uninstall_cancel:
      SetErrorLevel 1
      Quit
  ${EndIf}

  infomancer_preuninstall_done:
!macroend

!macro NSIS_HOOK_POSTUNINSTALL
  ${If} $UpdateMode != 1
    SetShellVarContext current

    ; Current Tauri-owned state.
    RMDir /r "$APPDATA\cloud.arsenik.infomancer"
    RMDir /r "$LOCALAPPDATA\cloud.arsenik.infomancer"

    ; Data locations used by the earlier desktop proof of concept. These are
    ; InfoMancer-owned state, not user media.
    RMDir /r "$APPDATA\cloud.arsenik.infomancer.desktop.poc"
    RMDir /r "$LOCALAPPDATA\cloud.arsenik.infomancer.desktop.poc"

    ; Explicit temporary/updater locations owned by the Windows shell.
    RMDir /r "$TEMP\InfoMancer"

    ; Tauri handles normal uninstall registration and shortcuts. These deletes
    ; make our publisher/product bookkeeping fail-closed if a prior installer
    ; revision left it behind.
    DeleteRegKey HKCU "Software\Arsenik\InfoMancer"
    DeleteRegKey /ifempty HKCU "Software\Arsenik"
  ${EndIf}
!macroend
