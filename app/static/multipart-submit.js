(() => {
  document.addEventListener("submit", async (event) => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement)) return;
    if (form.method.toLowerCase() !== "post") return;
    if (form.enctype.toLowerCase() !== "multipart/form-data") return;
    if (event.defaultPrevented) return;

    const csrfInput = form.querySelector('input[name="csrf_token"]');
    const csrfToken = csrfInput?.value || "";
    if (!csrfToken) return;

    const submitter = event.submitter;
    const actionUrl = new URL(submitter?.formAction || form.action || window.location.href, window.location.href);
    if (actionUrl.origin !== window.location.origin) {
      event.preventDefault();
      window.alert("InfoMancer blocked an upload form that tried to leave this server.");
      return;
    }

    event.preventDefault();
    const data = new FormData(form);
    if (submitter?.name) data.append(submitter.name, submitter.value || "");
    const button = submitter instanceof HTMLButtonElement ? submitter : null;
    if (button) button.disabled = true;

    try {
      const response = await fetch(actionUrl.href, {
        method: "POST",
        body: data,
        credentials: "same-origin",
        headers: {"X-CSRF-Token": csrfToken},
        redirect: "follow",
      });
      const responseUrl = new URL(response.url, window.location.href);
      if (responseUrl.origin !== window.location.origin) {
        throw new Error("Unexpected cross-origin upload redirect");
      }
      if (!response.ok) {
        throw new Error(`Upload failed (HTTP ${response.status})`);
      }
      if (response.redirected) {
        window.location.assign(responseUrl.href);
        return;
      }
      const html = await response.text();
      window.history.replaceState({}, "", responseUrl.href);
      document.open();
      document.write(html);
      document.close();
    } catch (_error) {
      if (button) button.disabled = false;
      window.alert("The upload could not be submitted. Check your connection and try again.");
    }
  });
})();
