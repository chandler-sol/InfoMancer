(() => {
  const dialog = document.getElementById("organize-dialog");
  const body = document.getElementById("organize-dialog-body");
  if (!dialog || !body || typeof dialog.showModal !== "function") return;

  let opener = null;
  const organizePath = /^\/titles\/\d+\/organize$/;

  const closeDialog = () => {
    if (!dialog.open || dialog.classList.contains("closing")) return;
    dialog.classList.add("closing");
    window.setTimeout(() => {
      dialog.close();
      dialog.classList.remove("closing", "loading");
      body.replaceChildren();
      opener?.focus();
    }, 180);
  };

  const renderResponse = async (response) => {
    const html = await response.text();
    const parsed = new DOMParser().parseFromString(html, "text/html");
    const content = parsed.querySelector("[data-organize-content]");
    if (!content) return false;
    body.replaceChildren(document.importNode(content, true));
    const heading = body.querySelector("h1");
    if (heading) heading.id = "organize-dialog-title";
    body.querySelector(".back")?.remove();
    body.querySelectorAll('a[href^="/titles/"]').forEach((link) => {
      if (link.textContent.trim() === "Cancel") {
        link.href = "#";
        link.dataset.organizeClose = "";
      }
    });
    dialog.classList.remove("loading");
    return true;
  };

  const openDialog = async (url, trigger) => {
    opener = trigger;
    dialog.classList.add("loading");
    if (!dialog.open) dialog.showModal();
    try {
      const response = await fetch(url, {headers: {"X-Requested-With": "InfoMancerDialog"}});
      if (!response.ok || !(await renderResponse(response))) window.location.assign(url);
    } catch (_) {
      window.location.assign(url);
    }
  };

  document.addEventListener("click", (event) => {
    const close = event.target.closest("[data-organize-close]");
    if (close && dialog.contains(close)) {
      event.preventDefault();
      closeDialog();
      return;
    }
    const link = event.target.closest('a[href*="/organize"]');
    if (!link || event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    const url = new URL(link.href, window.location.href);
    if (url.origin !== window.location.origin || !organizePath.test(url.pathname)) return;
    event.preventDefault();
    link.closest("details")?.removeAttribute("open");
    openDialog(url.href, link);
  });

  body.addEventListener("submit", async (event) => {
    const form = event.target.closest("form.organize-title-form");
    if (!form) return;
    event.preventDefault();
    const submitter = event.submitter;
    submitter?.setAttribute("disabled", "");
    dialog.classList.add("loading");
    try {
      const response = await fetch(form.action, {
        method: "POST",
        body: new FormData(form),
        headers: {"X-Requested-With": "InfoMancerDialog"},
      });
      const destination = new URL(response.url, window.location.href);
      if (organizePath.test(destination.pathname)) {
        await renderResponse(response);
        return;
      }
      const message = destination.searchParams.get("message") || "Organization saved.";
      const current = new URL(window.location.href);
      current.searchParams.set("message", message);
      window.location.assign(current.href);
    } catch (_) {
      form.submit();
    } finally {
      submitter?.removeAttribute("disabled");
    }
  });

  dialog.addEventListener("cancel", (event) => {
    event.preventDefault();
    closeDialog();
  });
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) closeDialog();
  });
})();
