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

    event.preventDefault();
    const submitter = event.submitter;
    const data = new FormData(form);
    if (submitter?.name) data.append(submitter.name, submitter.value || "");
    const action = submitter?.formAction || form.action || window.location.href;
    const button = submitter instanceof HTMLButtonElement ? submitter : null;
    if (button) button.disabled = true;

    try {
      const response = await fetch(action, {
        method: "POST",
        body: data,
        credentials: "same-origin",
        headers: {"X-CSRF-Token": csrfToken},
        redirect: "follow",
      });
      if (response.redirected) {
        window.location.assign(response.url);
        return;
      }
      const html = await response.text();
      window.history.replaceState({}, "", response.url);
      document.open();
      document.write(html);
      document.close();
    } catch (_error) {
      if (button) button.disabled = false;
      window.alert("The upload could not be submitted. Check your connection and try again.");
    }
  });
})();
