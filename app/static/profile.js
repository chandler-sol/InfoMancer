(() => {
  const form = document.getElementById("profile-form");
  if (!form) return;

  const displayName = document.getElementById("profile-display-name");
  const previewName = document.querySelector("[data-profile-preview-name]");
  const previewGlyph = document.querySelector("[data-profile-preview-glyph]");
  const previewImage = document.querySelector("[data-profile-preview-image]");
  const iconChoices = [...form.querySelectorAll('input[name="profile_icon"]')];
  const customChoice = form.querySelector('input[name="profile_icon"][value="custom"]');
  const customChoiceImage = document.querySelector("[data-profile-custom-choice-image]");
  const customChoiceGlyph = document.querySelector("[data-profile-custom-choice-glyph]");
  const accountAvatar = document.querySelector(".account-avatar");
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
  let customAvatarUrl = form.dataset.customAvatar === "1"
    ? `/account/avatar/current?v=${Date.now()}`
    : "";
  let prepared = false;

  const selectedChoice = () => iconChoices.find(choice => choice.checked) || iconChoices[0];
  const choiceLabel = (choice) => choice?.closest(".profile-icon-choice");
  const symbolFor = (choice) => {
    if (!choice) return "?";
    if (choice.value === "initials") return (displayName?.value.trim().slice(0, 1) || "?").toUpperCase();
    return choiceLabel(choice)?.dataset.symbol || "?";
  };

  const avatarSvg = (symbol) => {
    const safe = String(symbol).replace(/[&<>"']/g, character => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[character]));
    const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="256" height="256" viewBox="0 0 256 256"><rect width="256" height="256" rx="128" fill="#b9f542"/><text x="128" y="139" text-anchor="middle" dominant-baseline="middle" font-family="Inter,Segoe UI,sans-serif" font-size="112" font-weight="800" fill="#0b1009">${safe}</text></svg>`;
    return `url("data:image/svg+xml,${encodeURIComponent(svg)}")`;
  };

  const setPreviewImage = (url) => {
    if (!previewImage || !previewGlyph) return;
    previewImage.src = url;
    previewImage.hidden = false;
    previewGlyph.hidden = true;
  };

  const setPreviewGlyph = (symbol) => {
    if (!previewImage || !previewGlyph) return;
    previewGlyph.textContent = symbol;
    previewGlyph.hidden = false;
    previewImage.hidden = true;
    previewImage.removeAttribute("src");
  };

  const updatePreview = () => {
    const choice = selectedChoice();
    if (!choice) return;
    if (previewName) previewName.textContent = displayName?.value.trim() || "Unnamed user";
    if (choice.value === "custom" && customAvatarUrl) {
      setPreviewImage(customAvatarUrl);
      if (accountAvatar) accountAvatar.style.backgroundImage = `url("${customAvatarUrl}")`;
    } else {
      const symbol = symbolFor(choice);
      setPreviewGlyph(symbol);
      if (accountAvatar) accountAvatar.style.backgroundImage = avatarSvg(symbol);
    }
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
      updatePreview();
      status("Custom icon ready. Save Profile to make it your account icon.");
      window.setTimeout(closeDialog, 450);
    } catch (error) {
      status(error.message || "The custom icon could not be saved.", true);
      useAvatar.disabled = false;
    }
  };

  iconChoices.forEach(choice => choice.addEventListener("change", updatePreview));
  displayName?.addEventListener("input", updatePreview);
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

  updatePreview();
})();
