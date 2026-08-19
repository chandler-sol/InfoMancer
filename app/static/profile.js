(() => {
  const form = document.getElementById("profile-form");
  if (!form) return;

  const displayName = document.getElementById("profile-display-name");
  const email = form.querySelector('input[name="email"]');
  const showHomeHero = form.querySelector('input[name="show_home_hero"]');
  const highContrast = form.querySelector('input[name="high_contrast"]');
  const previewName = document.querySelector("[data-profile-preview-name]");
  const previewGlyph = document.querySelector("[data-profile-preview-glyph]");
  const previewImage = document.querySelector("[data-profile-preview-image]");
  const iconChoices = [...form.querySelectorAll('input[name="profile_icon"]')];
  const customChoice = form.querySelector('input[name="profile_icon"][value="custom"]');
  const customChoiceImage = document.querySelector("[data-profile-custom-choice-image]");
  const customChoiceGlyph = document.querySelector("[data-profile-custom-choice-glyph]");
  const accountAvatar = document.querySelector(".account-avatar");
  const saveArea = document.querySelector("[data-profile-save-area]");
  const saveStatus = document.querySelector("[data-profile-save-status]");
  const discardButton = document.querySelector("[data-profile-discard]");
  const saveButtons = [...document.querySelectorAll("[data-profile-save]")];
  const mobileSave = document.querySelector("[data-profile-mobile-save]");
  const mobileStatus = document.querySelector("[data-profile-mobile-status]");
  const dialog = document.getElementById("profile-avatar-dialog");
  const openAvatar = document.querySelector("[data-profile-avatar-open]");
  const closeAvatarButtons = [...document.querySelectorAll("[data-profile-avatar-close]")];
  const dropZone = document.querySelector("[data-profile-avatar-drop]");
  const fileInput = document.getElementById("profile-avatar-file");
  const canvas = document.getElementById("profile-avatar-canvas");
  const useAvatar = document.querySelector("[data-profile-avatar-use]");
  const avatarStatus = document.querySelector("[data-profile-avatar-status]");
  const csrf = form.querySelector('input[name="csrf_token"]')?.value || "";
  const context = canvas?.getContext("2d", {alpha: false});
  const sidebarSymbols = {
    film: "◆",
    television: "▣",
    star: "★",
    library: "▤",
    disc: "◎",
    camera: "▰",
    headphones: "∩",
    folder: "▱",
    server: "≡",
    heart: "♥",
    clapperboard: "▥",
  };
  let customAvatarUrl = form.dataset.customAvatar === "1"
    ? `/account/avatar/current?v=${Date.now()}`
    : "";
  let prepared = false;
  let initialState = null;

  const selectedChoice = () => iconChoices.find(choice => choice.checked) || iconChoices[0];
  const choiceLabel = (choice) => choice?.closest(".profile-icon-choice");
  const initialFor = () => (displayName?.value.trim().slice(0, 1) || "?").toUpperCase();
  const choiceSvg = (choice) => choiceLabel(choice)?.querySelector(".profile-icon-glyph svg") || null;

  const profileState = () => ({
    displayName: displayName?.value ?? "",
    email: email?.value ?? "",
    profileIcon: selectedChoice()?.value || "initials",
    showHomeHero: Boolean(showHomeHero?.checked),
    highContrast: Boolean(highContrast?.checked),
  });

  const sameState = (left, right) => (
    left.displayName === right.displayName
    && left.email === right.email
    && left.profileIcon === right.profileIcon
    && left.showHomeHero === right.showHomeHero
    && left.highContrast === right.highContrast
  );

  const setSaveState = (state) => {
    const dirty = state === "dirty";
    const saved = state === "saved";
    if (saveArea) saveArea.dataset.state = state;
    if (saveStatus) {
      saveStatus.textContent = dirty
        ? "Unsaved changes"
        : saved ? "Changes saved" : "Profile is up to date";
    }
    if (mobileStatus) mobileStatus.textContent = dirty ? "Unsaved changes" : "Profile is up to date";
    if (discardButton) discardButton.hidden = !dirty;
    saveButtons.forEach(button => {
      button.disabled = !dirty;
      if (!dirty) button.textContent = "Save Profile";
    });
    if (mobileSave) mobileSave.hidden = !dirty;
  };

  const syncDirtyState = () => {
    if (!initialState) return;
    setSaveState(sameState(profileState(), initialState) ? "clean" : "dirty");
  };

  const setPreviewImage = (url) => {
    if (!previewImage || !previewGlyph) return;
    previewImage.src = url;
    previewImage.hidden = false;
    previewGlyph.hidden = true;
  };

  const setPreviewMark = (choice) => {
    if (!previewImage || !previewGlyph) return;
    previewGlyph.replaceChildren();
    const selectedIcon = choice?.value || "initials";
    const icon = selectedIcon !== "initials" && selectedIcon !== "custom" ? choiceSvg(choice) : null;
    if (icon) {
      previewGlyph.append(icon.cloneNode(true));
      previewGlyph.dataset.profileIcon = selectedIcon;
    } else {
      previewGlyph.textContent = initialFor();
      previewGlyph.dataset.profileIcon = "initials";
    }
    previewGlyph.hidden = false;
    previewImage.hidden = true;
    previewImage.removeAttribute("src");
  };

  const setAccountAvatar = (choice) => {
    if (!accountAvatar) return;
    const selectedIcon = choice?.value || "initials";
    if (selectedIcon === "custom" && customAvatarUrl) {
      accountAvatar.textContent = "";
      accountAvatar.style.backgroundImage = `url("${customAvatarUrl}")`;
      accountAvatar.dataset.profileAvatarKind = "image";
      return;
    }

    /* Initials and built-in marks are text glyphs in the persistent account rail.
       Do not turn them into a CSS data-image. A failed background image combined
       with the image-mode transparency rule is what produced the blank green
       circle on the Profile page. */
    accountAvatar.style.removeProperty("background-image");
    delete accountAvatar.dataset.profileAvatarKind;
    accountAvatar.textContent = selectedIcon === "initials"
      ? initialFor()
      : (sidebarSymbols[selectedIcon] || initialFor());
  };

  const updatePreview = () => {
    const choice = selectedChoice();
    if (!choice) return;
    if (previewName) previewName.textContent = displayName?.value.trim() || "Unnamed user";
    if (choice.value === "custom" && customAvatarUrl) {
      setPreviewImage(customAvatarUrl);
    } else {
      setPreviewMark(choice);
    }
    setAccountAvatar(choice);
  };

  const status = (message = "", error = false) => {
    if (!avatarStatus) return;
    avatarStatus.textContent = message;
    avatarStatus.classList.toggle("error", error);
  };

  const openDialog = () => {
    if (!dialog) return;
    status();
    if (typeof dialog.showModal === "function") dialog.showModal();
    else dialog.setAttribute("open", "");
  };

  const closeDialog = () => {
    if (!dialog) return;
    if (typeof dialog.close === "function") dialog.close();
    else dialog.removeAttribute("open");
  };

  const prepareFile = async (file) => {
    prepared = false;
    if (useAvatar) useAvatar.disabled = true;
    status();
    if (!file) return;
    if (!["image/png", "image/jpeg", "image/webp"].includes(file.type)) {
      status("Choose a PNG, JPEG, or WebP image.", true);
      return;
    }
    if (file.size > 2 * 1024 * 1024) {
      status("That file is larger than the 2 MB upload limit.", true);
      return;
    }

    let bitmap;
    try {
      bitmap = await createImageBitmap(file);
    } catch (_error) {
      status("The selected image could not be decoded.", true);
      return;
    }
    if (bitmap.width < 128 || bitmap.height < 128) {
      bitmap.close?.();
      status("Choose an image at least 128 × 128 pixels.", true);
      return;
    }
    if (!canvas || !context) {
      bitmap.close?.();
      status("The image editor is unavailable in this browser.", true);
      return;
    }

    const edge = Math.min(bitmap.width, bitmap.height);
    const sourceX = Math.round((bitmap.width - edge) / 2);
    const sourceY = Math.round((bitmap.height - edge) / 2);
    context.fillStyle = "#0b0e12";
    context.fillRect(0, 0, 256, 256);
    context.drawImage(bitmap, sourceX, sourceY, edge, edge, 0, 0, 256, 256);
    bitmap.close?.();
    prepared = true;
    if (useAvatar) useAvatar.disabled = false;
    status("Centered square crop ready. InfoMancer will store only this 256 × 256 preview.");
  };

  const uploadPrepared = async () => {
    if (!prepared || !canvas || !useAvatar) return;
    useAvatar.disabled = true;
    status("Saving your processed profile image…");
    const blob = await new Promise(resolve => canvas.toBlob(resolve, "image/png"));
    if (!blob) {
      status("The processed profile image could not be created.", true);
      useAvatar.disabled = false;
      return;
    }
    try {
      const response = await fetch("/account/profile/avatar", {
        method: "POST",
        credentials: "same-origin",
        headers: {
          "Content-Type": "image/png",
          ...(csrf ? {"X-CSRF-Token": csrf} : {}),
        },
        body: blob,
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(result.error || `Upload failed with HTTP ${response.status}.`);
      customAvatarUrl = result.avatar_url || `/account/avatar/current?preview=1&v=${Date.now()}`;
      if (customChoice) {
        customChoice.checked = true;
        customChoice.dispatchEvent(new Event("change", {bubbles: true}));
      }
      if (customChoiceImage && customChoiceGlyph) {
        customChoiceImage.src = customAvatarUrl;
        customChoiceImage.hidden = false;
        customChoiceGlyph.hidden = true;
      }
      if (openAvatar) openAvatar.textContent = "Change image";
      updatePreview();
      syncDirtyState();
      status("Custom icon ready. Save Profile to use it for this account.");
      window.setTimeout(closeDialog, 450);
    } catch (error) {
      status(error.message || "The custom icon could not be saved.", true);
      useAvatar.disabled = false;
    }
  };

  const restoreInitialState = () => {
    if (!initialState) return;
    if (displayName) displayName.value = initialState.displayName;
    if (email) email.value = initialState.email;
    if (showHomeHero) showHomeHero.checked = initialState.showHomeHero;
    if (highContrast) highContrast.checked = initialState.highContrast;
    const originalChoice = iconChoices.find(choice => choice.value === initialState.profileIcon);
    if (originalChoice) originalChoice.checked = true;
    updatePreview();
    syncDirtyState();
  };

  iconChoices.forEach(choice => choice.addEventListener("change", () => {
    updatePreview();
    syncDirtyState();
  }));
  displayName?.addEventListener("input", () => {
    updatePreview();
    syncDirtyState();
  });
  email?.addEventListener("input", syncDirtyState);
  showHomeHero?.addEventListener("change", syncDirtyState);
  highContrast?.addEventListener("change", syncDirtyState);
  discardButton?.addEventListener("click", restoreInitialState);
  openAvatar?.addEventListener("click", openDialog);
  closeAvatarButtons.forEach(button => button.addEventListener("click", closeDialog));
  fileInput?.addEventListener("change", () => prepareFile(fileInput.files?.[0]));
  useAvatar?.addEventListener("click", uploadPrepared);
  dropZone?.addEventListener("dragover", event => {
    event.preventDefault();
    dropZone.classList.add("dragging");
  });
  dropZone?.addEventListener("dragleave", () => dropZone.classList.remove("dragging"));
  dropZone?.addEventListener("drop", event => {
    event.preventDefault();
    dropZone.classList.remove("dragging");
    const file = event.dataTransfer?.files?.[0];
    if (fileInput && file && typeof DataTransfer === "function") {
      const transfer = new DataTransfer();
      transfer.items.add(file);
      fileInput.files = transfer.files;
    }
    prepareFile(file);
  });
  dialog?.addEventListener("click", event => {
    if (event.target === dialog) closeDialog();
  });
  form.addEventListener("submit", () => {
    if (saveStatus) saveStatus.textContent = "Saving changes…";
    if (mobileStatus) mobileStatus.textContent = "Saving changes…";
    saveButtons.forEach(button => {
      button.disabled = true;
      button.textContent = "Saving…";
    });
  });

  updatePreview();
  initialState = profileState();
  setSaveState("clean");

  const savedMessage = new URLSearchParams(window.location.search).get("message") || "";
  if (!form.querySelector(".form-error") && savedMessage.toLowerCase() === "profile saved") {
    setSaveState("saved");
    window.setTimeout(() => setSaveState("clean"), 1800);
  }
})();
