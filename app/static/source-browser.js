(() => {
  const dialog = document.getElementById("source-browser");
  if (!dialog) return;
  const list = document.getElementById("source-browser-list");
  const back = document.getElementById("source-browser-back");
  const crumbs = document.getElementById("source-browser-crumbs");
  const error = document.getElementById("source-browser-error");
  const currentPanel = document.getElementById("source-current");
  const currentName = document.getElementById("source-current-name");
  const currentPath = document.getElementById("source-current-path");
  const useCurrent = document.getElementById("source-use-current");
  const preview = document.getElementById("source-preview");
  const previewTitle = document.getElementById("source-preview-title");
  const recommendation = document.getElementById("source-recommendation");
  const stats = document.getElementById("source-preview-stats");
  const warning = document.getElementById("source-preview-warning");
  const kindChoice = document.getElementById("source-kind-choice");
  const finalPath = document.getElementById("source-final-path");
  const finalKind = document.getElementById("source-final-kind");
  const finalLabel = document.getElementById("source-final-label");
  const submit = document.getElementById("source-add-submit");
  let current = "";
  let previewData = null;

  const showError = (message = "") => {
    error.hidden = !message;
    error.textContent = message;
  };
  const fetchJson = async (url) => {
    const response = await fetch(url, {cache: "no-store"});
    const text = await response.text();
    let data = null;
    if (text) {
      try { data = JSON.parse(text); }
      catch (_) {
        if (!response.ok) {
          throw new Error("InfoMancer could not read that folder. One of the available drives may be disconnected, unavailable, or blocked by Windows permissions.");
        }
        throw new Error("InfoMancer received an invalid response while reading that folder. Refresh the page and try again.");
      }
    }
    if (!response.ok) {
      throw new Error(data?.detail || "InfoMancer could not read that folder. Check the server permissions and try again.");
    }
    if (!data || typeof data !== "object") {
      throw new Error("InfoMancer received an empty response while reading that folder. Refresh the page and try again.");
    }
    return data;
  };
  const folderButton = (folder, location = false) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "source-folder";
    const icon = document.createElement("span");
    icon.className = "source-folder-icon";
    icon.textContent = location ? "◉" : "▰";
    const text = document.createElement("span");
    const name = document.createElement("strong");
    name.textContent = folder.name;
    const path = document.createElement("small");
    path.textContent = folder.path;
    text.append(name, path);
    const action = document.createElement("span");
    action.textContent = folder.accessible === false ? "Unavailable" : "Open →";
    button.append(icon, text, action);
    button.disabled = folder.accessible === false;
    button.addEventListener("click", () => load(folder.path));
    return button;
  };
  const load = async (path = "") => {
    showError();
    preview.hidden = true;
    previewData = null;
    list.innerHTML = '<div class="source-browser-loading">Reading folders…</div>';
    try {
      const data = await fetchJson(`/api/source-browser?path=${encodeURIComponent(path)}`);
      current = data.current || "";
      back.disabled = !data.parent;
      back.onclick = data.parent ? () => load(data.parent) : null;
      crumbs.replaceChildren();
      for (const crumb of data.breadcrumbs || []) {
        const button = document.createElement("button");
        button.type = "button";
        button.textContent = crumb.name;
        button.addEventListener("click", () => load(crumb.path));
        crumbs.append(button);
      }
      const choices = data.locations?.length ? data.locations : data.folders;
      list.replaceChildren(...choices.map(item => folderButton(item, Boolean(data.locations?.length))));
      if (!choices.length) list.innerHTML = '<div class="empty">No accessible media locations are available here.</div>';
      currentPanel.hidden = !current;
      if (current) {
        currentName.textContent = data.name;
        currentPath.textContent = current;
      }
    } catch (reason) {
      list.replaceChildren();
      currentPanel.hidden = true;
      showError(reason.message);
    }
  };
  const renderPreview = () => {
    if (!previewData) return;
    const choice = kindChoice.value;
    const detected = previewData.recommended_kind;
    const kind = choice === "auto" ? detected : choice;
    finalKind.value = ["movie", "tv"].includes(kind) ? kind : "";
    submit.disabled = !finalKind.value;
    recommendation.className = `source-recommendation ${detected}`;
    recommendation.textContent = detected === "movie" ? "Movies detected" : detected === "tv" ? "TV Shows detected" : detected === "mixed" ? "Mixed media" : "Choose a type";
    stats.replaceChildren();
    const values = kind === "tv"
      ? [[previewData.show_count, "series folders"], [previewData.episode_count, "recognized episodes"], [previewData.video_count, "video files"]]
      : [[previewData.movie_count, "movie titles"], [previewData.video_count, "video files"], [previewData.bucket_count, "A–Z / number buckets"]];
    for (const [value, label] of values) {
      const card = document.createElement("div");
      const strong = document.createElement("strong");
      strong.textContent = Number(value).toLocaleString();
      const span = document.createElement("span");
      span.textContent = label;
      card.append(strong, span);
      stats.append(card);
    }
    let message = previewData.warning || "";
    if (choice === "auto" && !finalKind.value) message ||= "Choose Movies or TV Shows to continue.";
    warning.hidden = !message;
    warning.textContent = message;
  };
  useCurrent.addEventListener("click", async () => {
    showError();
    preview.hidden = false;
    previewTitle.textContent = "Analyzing this folder…";
    stats.replaceChildren();
    warning.hidden = true;
    submit.disabled = true;
    try {
      previewData = await fetchJson(`/api/source-preview?path=${encodeURIComponent(current)}`);
      previewTitle.textContent = previewData.name;
      finalPath.value = previewData.path;
      if (!finalLabel.value) finalLabel.value = previewData.name;
      renderPreview();
      preview.scrollIntoView({behavior: "smooth", block: "nearest"});
    } catch (reason) {
      preview.hidden = true;
      showError(reason.message);
    }
  });
  kindChoice.addEventListener("change", renderPreview);
  document.querySelectorAll("[data-open-source-browser]").forEach(button => button.addEventListener("click", () => {
    dialog.showModal();
    load();
  }));
  document.querySelectorAll("[data-close-source-browser]").forEach(button => button.addEventListener("click", () => dialog.close()));
  dialog.addEventListener("click", event => { if (event.target === dialog) dialog.close(); });
})();
