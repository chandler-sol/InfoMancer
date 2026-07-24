document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll("[data-password-toggle]").forEach((button) => {
    const input = document.getElementById(button.dataset.passwordToggle);
    if (!input) {
      button.disabled = true;
      button.textContent = "Unavailable";
      button.setAttribute("aria-label", "Password visibility control is unavailable");
      return;
    }

    button.addEventListener("click", () => {
      const willShow = input.type === "password";
      input.type = willShow ? "text" : "password";
      button.textContent = willShow ? "Hide" : "Show";
      button.setAttribute("aria-pressed", String(willShow));
      const description = button.getAttribute("aria-label")
        .replace(/^Show /, "")
        .replace(/^Hide /, "");
      button.setAttribute("aria-label", `${willShow ? "Hide" : "Show"} ${description}`);
      input.focus({ preventScroll: true });
    });
  });
});
