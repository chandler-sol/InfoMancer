(() => {
  const onReady = (callback) => {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", callback, {once: true});
    } else {
      callback();
    }
  };

  const buildSeasonToolsRow = () => {
    const nav = document.querySelector(".media-dossier .season-nav");
    const toolbar = document.querySelector(".media-dossier .season-collapse-toolbar");
    if (!nav || !toolbar || nav.closest(".season-tools-row")) return;

    const row = document.createElement("div");
    row.className = "season-tools-row";
    nav.before(row);
    row.append(nav, toolbar);
  };

  const enhanceCast = () => {
    const creditRows = Array.from(document.querySelectorAll(".movie-credits > div"));
    const castRow = creditRows.find((row) => row.querySelector("strong")?.textContent.trim().startsWith("Top billed"));
    if (!castRow) return;

    const value = Array.from(castRow.children).find((child) => child.tagName === "SPAN");
    if (!value) return;

    const castLinks = Array.from(value.querySelectorAll("a"));
    if (!castLinks.length) return;

    const visibleLimit = 10;
    const clones = castLinks.map((link) => link.cloneNode(true));
    value.replaceChildren();

    clones.slice(0, visibleLimit).forEach((link, index) => {
      if (index) value.append(document.createTextNode(", "));
      value.append(link);
    });

    if (clones.length <= visibleLimit) return;

    value.append(document.createTextNode(" "));
    const seeAll = document.createElement("button");
    seeAll.type = "button";
    seeAll.className = "credit-more";
    seeAll.textContent = "See all";
    seeAll.setAttribute("aria-haspopup", "dialog");
    value.append(seeAll);

    const dialog = document.createElement("dialog");
    dialog.className = "workspace-confirm-dialog cast-dialog";
    dialog.setAttribute("aria-labelledby", "cast-dialog-title");

    const card = document.createElement("section");
    card.className = "workspace-dialog-card";

    const eyebrow = document.createElement("p");
    eyebrow.className = "eyebrow";
    eyebrow.textContent = "CAST & CREW";

    const heading = document.createElement("div");
    heading.className = "overview-dialog-heading";

    const title = document.createElement("h2");
    title.id = "cast-dialog-title";
    title.textContent = "Top billed cast";

    const x = document.createElement("button");
    x.type = "button";
    x.className = "overview-dialog-close";
    x.setAttribute("aria-label", "Close cast window");
    x.textContent = "×";

    heading.append(title, x);

    const list = document.createElement("div");
    list.className = "cast-dialog-list";
    clones.forEach((link) => list.append(link.cloneNode(true)));

    const actions = document.createElement("div");
    actions.className = "workspace-dialog-actions";
    const close = document.createElement("button");
    close.type = "button";
    close.className = "button";
    close.textContent = "Close";
    actions.append(close);

    card.append(eyebrow, heading, list, actions);
    dialog.append(card);
    document.body.append(dialog);

    const closeDialog = () => dialog.close();
    seeAll.addEventListener("click", () => dialog.showModal());
    x.addEventListener("click", closeDialog);
    close.addEventListener("click", closeDialog);
    dialog.addEventListener("click", (event) => {
      if (event.target === dialog) closeDialog();
    });
  };

  const condenseSeriesMenu = () => {
    const popovers = document.querySelectorAll(".series-menu:not(.movie-detail-menu) > .series-menu-popover");
    popovers.forEach((popover) => {
      if (popover.classList.contains("detail-condensed-menu")) return;

      const groups = {
        maintenance: [],
        files: [],
        matching: []
      };
      const topLevel = [];

      Array.from(popover.children).forEach((node) => {
        if (node.tagName === "HR") return;
        const label = node.textContent.replace(/\s+/g, " ").trim();

        if (/^(Scanned\b|Rescan Series\b|Scan Series\b|Refresh IMDb Metadata\b|Pull IMDb Metadata\b|Search )/.test(label)) {
          groups.maintenance.push(node);
        } else if (/^(Review Episode Renames\b|Organize into Season Folders\b|Preview Folder Rename\b|Apply Folder Rename\b|Restore Original Filenames\b)/.test(label)) {
          groups.files.push(node);
        } else if (/^(Change Match\b|Match$|Unmatch\b|✓?\s*TVDB\b|×?\s*TVDB\b|IMDb Link\b)/.test(label)) {
          groups.matching.push(node);
        } else {
          topLevel.push(node);
        }
      });

      const makeSubmenu = (label, nodes) => {
        if (!nodes.length) return null;

        const submenu = document.createElement("div");
        submenu.className = "series-submenu";

        const trigger = document.createElement("button");
        trigger.type = "button";
        trigger.className = "series-submenu-trigger";
        trigger.setAttribute("aria-expanded", "false");

        const text = document.createElement("span");
        text.textContent = label;
        const arrow = document.createElement("span");
        arrow.className = "series-submenu-arrow";
        arrow.setAttribute("aria-hidden", "true");
        arrow.textContent = "‹";
        trigger.append(text, arrow);

        const panel = document.createElement("div");
        panel.className = "series-submenu-popover";
        nodes.forEach((node) => panel.append(node));
        submenu.append(trigger, panel);

        trigger.addEventListener("click", (event) => {
          event.stopPropagation();
          const nextOpen = !submenu.classList.contains("open");
          popover.querySelectorAll(".series-submenu.open").forEach((item) => {
            if (item !== submenu) {
              item.classList.remove("open");
              item.querySelector(":scope > .series-submenu-trigger")?.setAttribute("aria-expanded", "false");
            }
          });
          submenu.classList.toggle("open", nextOpen);
          trigger.setAttribute("aria-expanded", String(nextOpen));
        });

        return submenu;
      };

      popover.replaceChildren(...topLevel);
      [
        makeSubmenu("Library tools", groups.maintenance),
        makeSubmenu("File tools", groups.files),
        makeSubmenu("Match & links", groups.matching)
      ].filter(Boolean).forEach((submenu) => popover.append(submenu));

      popover.classList.add("detail-condensed-menu");
    });

    document.addEventListener("click", (event) => {
      document.querySelectorAll(".detail-condensed-menu .series-submenu.open").forEach((submenu) => {
        if (submenu.contains(event.target)) return;
        submenu.classList.remove("open");
        submenu.querySelector(":scope > .series-submenu-trigger")?.setAttribute("aria-expanded", "false");
      });
    });
  };

  onReady(() => {
    buildSeasonToolsRow();
    enhanceCast();
    condenseSeriesMenu();
  });
})();
