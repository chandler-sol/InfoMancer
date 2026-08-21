(() => {
  const ready = (callback) => {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", callback, {once: true});
    } else {
      callback();
    }
  };

  ready(() => {
    const dossier = document.querySelector(".media-dossier");
    if (!dossier) return;

    const more = dossier.querySelector("#credit-more");
    const extra = dossier.querySelector("#additional-cast");
    const creditRow = more?.closest(".movie-credits > div");
    if (!more || !extra || !creditRow) return;

    const castLinks = [...creditRow.querySelectorAll("a")];
    if (castLinks.length <= 3) return;

    const label = creditRow.querySelector(":scope > strong");
    if (label) label.textContent = "Top billed:";

    more.textContent = `See cast (${castLinks.length})`;
    more.setAttribute("aria-haspopup", "dialog");
    more.removeAttribute("aria-controls");

    const dialog = document.createElement("dialog");
    dialog.className = "workspace-confirm-dialog title-cast-dialog";
    dialog.setAttribute("aria-labelledby", "title-cast-dialog-title");

    const card = document.createElement("section");
    card.className = "title-cast-dialog-card";

    const header = document.createElement("header");
    header.className = "title-cast-dialog-head";

    const heading = document.createElement("div");
    const eyebrow = document.createElement("p");
    eyebrow.className = "eyebrow";
    eyebrow.textContent = "CAST";
    const title = document.createElement("h2");
    title.id = "title-cast-dialog-title";
    title.textContent = dossier.querySelector(".detail-copy h1")?.textContent?.trim() || "Cast";
    const summary = document.createElement("p");
    summary.className = "title-cast-dialog-summary";
    summary.textContent = `${castLinks.length} cached cast credits, ordered by billing.`;
    heading.append(eyebrow, title, summary);

    const closeTop = document.createElement("button");
    closeTop.type = "button";
    closeTop.className = "title-cast-dialog-close";
    closeTop.setAttribute("aria-label", "Close cast");
    closeTop.textContent = "×";
    header.append(heading, closeTop);

    const list = document.createElement("div");
    list.className = "title-cast-dialog-list";
    castLinks.forEach((source) => {
      const link = document.createElement("a");
      link.href = source.href;
      link.textContent = source.textContent.trim();
      list.append(link);
    });

    const footer = document.createElement("footer");
    footer.className = "title-cast-dialog-footer";
    const closeBottom = document.createElement("button");
    closeBottom.type = "button";
    closeBottom.className = "button";
    closeBottom.textContent = "Close";
    footer.append(closeBottom);

    card.append(header, list, footer);
    dialog.append(card);
    document.body.append(dialog);

    const close = () => dialog.close();
    closeTop.addEventListener("click", close);
    closeBottom.addEventListener("click", close);
    dialog.addEventListener("click", (event) => {
      if (event.target === dialog) close();
    });

    /* The legacy detail template expands #additional-cast inline. Own this click in
       capture phase so the full list stays out of the hero layout and opens here. */
    more.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopImmediatePropagation();
      extra.hidden = true;
      more.setAttribute("aria-expanded", "true");
      dialog.showModal?.();
    }, {capture: true});

    dialog.addEventListener("close", () => {
      more.setAttribute("aria-expanded", "false");
      extra.hidden = true;
    });
  });
})();
