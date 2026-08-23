(() => {
  const tour = document.getElementById("onboarding-tour");
  const tourTitle = document.getElementById("tour-title");
  if (!tour || !tourTitle) return;

  let preview = null;

  const isInspectorStep = () => {
    if (window.location.pathname !== "/library") return false;
    const step = new URLSearchParams(window.location.search).get("tour_step");
    return step === "5" && tourTitle.textContent.trim() === "Inspect first, act second";
  };

  const selectedTitleName = () => {
    const candidate = document.querySelector(
      ".library-title-row .title-link strong, .cover-card .cover-card-link strong, .tour-demo-list article strong",
    );
    return candidate?.textContent?.trim() || "Midnight Signal";
  };

  const makeFact = (label, value) => {
    const fact = document.createElement("div");
    const small = document.createElement("small");
    const strong = document.createElement("strong");
    small.textContent = label;
    strong.textContent = value;
    fact.append(small, strong);
    return fact;
  };

  const openPreview = () => {
    if (preview || !isInspectorStep()) return;

    preview = document.createElement("aside");
    preview.className = "workspace-inspector tour-inspector-preview";
    preview.setAttribute("aria-hidden", "true");

    const head = document.createElement("div");
    head.className = "workspace-inspector-head";
    const heading = document.createElement("span");
    heading.textContent = "Inspector";
    const close = document.createElement("span");
    close.className = "tour-inspector-preview-close";
    close.setAttribute("aria-hidden", "true");
    close.textContent = "×";
    head.append(heading, close);

    const body = document.createElement("div");
    body.className = "workspace-inspector-body tour-inspector-preview-body";

    const identity = document.createElement("section");
    identity.className = "tour-inspector-preview-identity";
    const eyebrow = document.createElement("p");
    eyebrow.className = "eyebrow";
    eyebrow.textContent = "TOUR PREVIEW";
    const name = document.createElement("h2");
    name.textContent = selectedTitleName();
    const note = document.createElement("p");
    note.textContent = "Inspector keeps the selected title beside the Library while you review its evidence and tools.";
    const badges = document.createElement("div");
    badges.className = "tour-inspector-preview-badges";
    ["Metadata", "Media", "Organization"].forEach((text) => {
      const badge = document.createElement("span");
      badge.textContent = text;
      badges.append(badge);
    });
    identity.append(eyebrow, name, note, badges);

    const facts = document.createElement("section");
    facts.className = "tour-inspector-preview-facts";
    facts.append(
      makeFact("MATCH", "Metadata state"),
      makeFact("MEDIA", "Technical facts"),
      makeFact("LIBRARY", "Tags & organization"),
      makeFact("TOOLS", "Quick actions"),
    );

    const actions = document.createElement("section");
    actions.className = "tour-inspector-preview-actions";
    const actionLabel = document.createElement("small");
    actionLabel.textContent = "QUICK TOOLS";
    const actionRow = document.createElement("div");
    ["Organize", "Refresh metadata", "Open details"].forEach((text) => {
      const button = document.createElement("span");
      button.className = "button small";
      button.textContent = text;
      actionRow.append(button);
    });
    actions.append(actionLabel, actionRow);

    body.append(identity, facts, actions);
    preview.append(head, body);
    tour.append(preview);
    tour.classList.add("tour-inspector-active");
  };

  const closePreview = () => {
    preview?.remove();
    preview = null;
    tour.classList.remove("tour-inspector-active");
  };

  const syncPreview = () => {
    if (isInspectorStep()) openPreview();
    else closePreview();
  };

  const observer = new MutationObserver(syncPreview);
  observer.observe(tourTitle, {childList: true, characterData: true, subtree: true});
  window.addEventListener("popstate", syncPreview);
  window.addEventListener("pagehide", closePreview);

  syncPreview();
})();
