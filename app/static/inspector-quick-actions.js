(() => {
  const moveQuickActions = (root = document) => {
    root.querySelectorAll?.("[data-workspace-inspector-panel]").forEach((panel) => {
      const summary = panel.querySelector(":scope > .workspace-inspector-summary");
      const actions = panel.querySelector(":scope > .workspace-inspector-footer-actions");
      if (!summary || !actions) return;

      actions.classList.add("workspace-inspector-quick-actions");
      if (summary.nextElementSibling !== actions) summary.after(actions);
    });
  };

  const install = () => {
    const host = document.getElementById("workspace-inspector");
    if (!host) return;

    moveQuickActions(host);
    const observer = new MutationObserver(() => moveQuickActions(host));
    observer.observe(host, {childList: true, subtree: true});
    window.addEventListener("pagehide", () => observer.disconnect(), {once: true});
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", install, {once: true});
  } else {
    install();
  }
})();
