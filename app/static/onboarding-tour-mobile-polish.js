(() => {
  const tour = document.getElementById("onboarding-tour");
  if (!tour) return;

  const card = tour.querySelector(".tour-card");
  const title = document.getElementById("tour-title");
  const next = document.getElementById("tour-next");
  const back = document.getElementById("tour-back");
  if (!card || !title) return;

  const profileTitle = "Your account and preferences";

  const settleCurrentStep = () => {
    window.requestAnimationFrame(() => {
      if (!tour.isConnected) return;
      card.scrollTop = 0;

      if (title.textContent.trim() !== profileTitle) return;
      const account = document.querySelector(".account-menu");
      if (!account) return;

      /* The main tour opens this during the Next click. app-shell's global
         click-outside handler receives that same bubbling click afterward and
         closes it again. Re-open on the next frame, after the click has fully
         finished, so the final walkthrough step can actually show the menu. */
      account.setAttribute("open", "");
      window.requestAnimationFrame(() => window.dispatchEvent(new Event("resize")));
    });
  };

  next?.addEventListener("click", () => {
    if (next.textContent.trim() === "Finish") return;
    settleCurrentStep();
  });
  back?.addEventListener("click", settleCurrentStep);

  /* Also cover direct navigation/reload into a late tour step. */
  settleCurrentStep();
})();
