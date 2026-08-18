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
    const titleMatch = window.location.pathname.match(/^\/titles\/(\d+)\/?$/);
    if (!dossier || !titleMatch) return;

    const titleId = titleMatch[1];
    const workflowDialog = document.getElementById("organize-dialog");
    const workflowBody = document.getElementById("organize-dialog-body");
    let workflowKind = "";
    let workflowOpener = null;
    let metadataPoll = 0;

    const toast = document.createElement("div");
    toast.className = "title-action-toast";
    toast.setAttribute("role", "status");
    toast.setAttribute("aria-live", "polite");
    document.body.append(toast);
    let toastTimer = 0;

    const showToast = (message, tone = "good") => {
      window.clearTimeout(toastTimer);
      toast.textContent = message;
      toast.className = `title-action-toast ${tone}`;
      requestAnimationFrame(() => toast.classList.add("show"));
      toastTimer = window.setTimeout(() => toast.classList.remove("show"), 2800);
    };

    window.addEventListener("infomancer:title-toast", (event) => {
      if (event.detail?.message) showToast(event.detail.message, event.detail.tone || "good");
    });

    const workflowPath = (pathname) => (
      new RegExp(`^/titles/${titleId}/(?:cover|collections|tvdb)/?$`).test(pathname)
      || /^\/files\/\d+\/rename-movie\/?$/.test(pathname)
    );

    const workflowKindFor = (pathname) => {
      if (pathname.endsWith("/cover")) return "cover";
      if (pathname.endsWith("/collections")) return "collections";
      if (pathname.endsWith("/tvdb")) return "match";
      if (pathname.includes("/rename-movie")) return "rename";
      return "workflow";
    };

    const cleanMenuSeparators = (popover) => {
      if (!popover) return;
      const children = [...popover.children];
      children.forEach((child, index) => {
        if (child.tagName !== "HR") return;
        const previous = children[index - 1];
        const next = children[index + 1];
        if (!previous || !next || previous.tagName === "HR" || next.tagName === "HR") child.remove();
      });
    };

    const decorateTitleActions = () => {
      const detailCopy = dossier.querySelector(".detail-page-head .detail-copy");
      const posterColumn = dossier.querySelector(".detail-poster-column");
      const menu = dossier.querySelector(
        ".workspace-detail-title-actions .item-action-menu, .movie-detail-menu, .dossier-on-disk > .panel-head .series-controls > .series-menu.item-action-menu"
      );
      if (!detailCopy || !menu) return false;

      const isMovie = menu.classList.contains("movie-detail-menu");
      dossier.classList.toggle("detail-kind-movie", isMovie);
      dossier.classList.toggle("detail-kind-tv", !isMovie);

      let quick = detailCopy.querySelector(".title-quick-actions");
      if (!quick) {
        quick = document.createElement("div");
        quick.className = "title-quick-actions";
        quick.setAttribute("aria-label", "Quick title actions");
        detailCopy.append(quick);
      }

      const popover = menu.querySelector(".series-menu-popover") || menu.querySelector(":scope > div");
      const favoriteForm = popover?.querySelector(`form[action="/titles/${titleId}/favorite"]`);
      if (favoriteForm && !quick.contains(favoriteForm)) {
        quick.append(favoriteForm);
      }

      const existingFavoriteSummary = detailCopy.querySelector(".hero-organization form .favorite-summary")?.closest("form");
      existingFavoriteSummary?.remove();

      const collectionLink = popover?.querySelector(`a[href="/titles/${titleId}/collections"]`);
      if (collectionLink && !quick.contains(collectionLink)) {
        collectionLink.classList.add("title-quick-action");
        collectionLink.dataset.titleWorkflow = "";
        collectionLink.textContent = "+ Collection";
        quick.append(collectionLink);
      }

      const coverLink = popover?.querySelector(`a[href="/titles/${titleId}/cover"]`);
      if (coverLink && posterColumn && !posterColumn.querySelector(".detail-cover-action")) {
        coverLink.className = "detail-cover-action";
        coverLink.dataset.titleWorkflow = "";
        coverLink.textContent = "Change cover";
        posterColumn.append(coverLink);
      }

      popover?.querySelectorAll("a").forEach((link) => {
        try {
          const url = new URL(link.href, window.location.href);
          if (url.origin === window.location.origin && workflowPath(url.pathname)) {
            link.dataset.titleWorkflow = "";
          }
        } catch (_error) {}
      });

      cleanMenuSeparators(popover);
      return true;
    };

    // workspace.js moves the title menu into the hero. Give it one frame to do so,
    // then retry briefly if this enhancement loaded first from cache.
    if (!decorateTitleActions()) {
      let retries = 0;
      const retry = () => {
        retries += 1;
        if (!decorateTitleActions() && retries < 8) window.setTimeout(retry, 40);
      };
      window.setTimeout(retry, 0);
    }

    const wireAsideControls = () => {
      const more = dossier.querySelector("#credit-more");
      const extra = dossier.querySelector("#additional-cast");
      if (more && extra && !more.dataset.detailWired) {
        more.dataset.detailWired = "1";
        more.addEventListener("click", () => {
          const opening = extra.hidden;
          extra.hidden = !opening;
          more.setAttribute("aria-expanded", String(opening));
          more.textContent = opening ? "See less" : "See more";
        });
      }

      const overview = dossier.querySelector("#title-overview");
      const overviewMore = dossier.querySelector("#overview-more");
      const overviewDialog = dossier.querySelector("#overview-dialog");
      if (overview && overviewMore) {
        requestAnimationFrame(() => {
          overviewMore.hidden = overview.scrollHeight <= overview.clientHeight + 2;
        });
      }
      if (overviewMore && overviewDialog && !overviewMore.dataset.detailWired) {
        overviewMore.dataset.detailWired = "1";
        overviewMore.addEventListener("click", () => overviewDialog.showModal?.());
        overviewDialog.querySelectorAll("[data-overview-close]").forEach((button) => {
          button.addEventListener("click", () => overviewDialog.close());
        });
      }
    };
    wireAsideControls();

    const patchDetailFromDocument = (parsed, {hero = true, onDisk = false} = {}) => {
      const freshDossier = parsed.querySelector(".media-dossier");
      if (!freshDossier) return false;

      if (hero) {
        const oldHead = dossier.querySelector(".detail-page-head");
        const newHead = freshDossier.querySelector(".detail-page-head");
        if (oldHead && newHead) {
          const oldPoster = oldHead.querySelector(".detail-poster-column");
          const newPoster = newHead.querySelector(".detail-poster-column");
          const currentCoverAction = oldPoster?.querySelector(".detail-cover-action");
          const oldPosterMedia = oldPoster?.querySelector(".detail-poster, .detail-poster-placeholder");
          const newPosterMedia = newPoster?.querySelector(".detail-poster, .detail-poster-placeholder");
          if (oldPosterMedia && newPosterMedia) {
            oldPosterMedia.replaceWith(document.importNode(newPosterMedia, true));
          }
          if (currentCoverAction && !oldPoster?.contains(currentCoverAction)) oldPoster?.append(currentCoverAction);

          const oldCopy = oldHead.querySelector(".detail-copy");
          const newCopy = newHead.querySelector(".detail-copy");
          if (oldCopy && newCopy) {
            const menuHost = oldCopy.querySelector(".workspace-detail-title-actions");
            const quick = oldCopy.querySelector(".title-quick-actions");
            const imported = document.importNode(newCopy, true);
            oldCopy.replaceChildren(...imported.childNodes);
            if (quick) oldCopy.append(quick);
            if (menuHost) oldCopy.append(menuHost);
          }

          const oldAside = oldHead.querySelector(".title-hero-aside");
          const newAside = newHead.querySelector(".title-hero-aside");
          if (oldAside && newAside) oldAside.replaceWith(document.importNode(newAside, true));
        }
      }

      if (onDisk) {
        const oldOnDisk = dossier.querySelector(".dossier-on-disk");
        const newOnDisk = freshDossier.querySelector(".dossier-on-disk");
        if (oldOnDisk && newOnDisk) {
          newOnDisk.querySelector(".movie-detail-menu")?.remove();
          newOnDisk.querySelector(":scope > .panel-head .series-controls > .series-menu.item-action-menu")?.remove();
          oldOnDisk.replaceWith(document.importNode(newOnDisk, true));
        }
      }

      decorateTitleActions();
      wireAsideControls();
      return true;
    };

    const refreshDetail = async (options = {}) => {
      const response = await fetch(window.location.pathname + window.location.search, {
        credentials: "same-origin",
        cache: "no-store",
        headers: {"X-InfoMancer-Hot-Refresh": "1"},
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const html = await response.text();
      const parsed = new DOMParser().parseFromString(html, "text/html");
      if (!patchDetailFromDocument(parsed, options)) throw new Error("Detail fragment missing");
    };

    document.addEventListener("submit", async (event) => {
      const form = event.target.closest(`.title-quick-actions form[action="/titles/${titleId}/favorite"]`);
      if (!form) return;
      event.preventDefault();
      const button = form.querySelector("button");
      const wasFavorite = Boolean(button?.classList.contains("active"));
      if (button) button.disabled = true;
      try {
        const response = await fetch(form.action, {
          method: "POST",
          credentials: "same-origin",
          body: new FormData(form),
        });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const favorite = !wasFavorite;
        if (button) {
          button.classList.toggle("active", favorite);
          button.innerHTML = `<span>★</span>${favorite ? "Remove favorite" : "Add favorite"}`;
        }
        showToast(favorite ? "Added to Favorites." : "Removed from Favorites.");
      } catch (_error) {
        showToast("Favorite could not be updated.", "error");
      } finally {
        if (button) button.disabled = false;
      }
    });

    const metadataForm = () => dossier.querySelector(`form[action="/titles/${titleId}/imdb-refresh"]`);

    const stopMetadataPoll = () => {
      window.clearTimeout(metadataPoll);
      metadataPoll = 0;
    };

    const pollMetadata = async (button, originalLabel) => {
      try {
        const response = await fetch(`/api/titles/${titleId}/metadata-refresh-state`, {
          credentials: "same-origin",
          cache: "no-store",
          headers: {"Accept": "application/json"},
        });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const data = await response.json();
        const status = data.task?.status || data.queue?.status || "idle";
        if (["complete", "error", "failed"].includes(status)) {
          stopMetadataPoll();
          if (status === "complete") {
            await refreshDetail({hero: true});
            showToast("Metadata refreshed.");
          } else {
            showToast(data.task?.error || data.metadata_refresh_error || "Metadata refresh stopped.", "error");
          }
          if (button) {
            button.disabled = false;
            button.textContent = originalLabel;
          }
          return;
        }
      } catch (_error) {
        // Keep the task widget authoritative and retry a transient state request.
      }
      metadataPoll = window.setTimeout(() => pollMetadata(button, originalLabel), 900);
    };

    document.addEventListener("submit", async (event) => {
      const form = event.target.closest(`form[action="/titles/${titleId}/imdb-refresh"]`);
      if (!form || workflowBody?.contains(form)) return;
      event.preventDefault();
      form.closest("details")?.removeAttribute("open");
      const button = form.querySelector("button");
      const originalLabel = button?.textContent.trim() || "Refresh IMDb Metadata";
      if (button) {
        button.disabled = true;
        button.textContent = "Starting refresh…";
      }
      try {
        const response = await fetch(form.action, {
          method: "POST",
          credentials: "same-origin",
          body: new FormData(form),
          headers: {"Accept": "application/json", "X-InfoMancer-Async": "1"},
        });
        const data = await response.json().catch(() => null);
        if (!response.ok || !data?.started) throw new Error(data?.detail || `HTTP ${response.status}`);
        if (button) button.textContent = "Refreshing metadata…";
        pollMetadata(button, originalLabel);
      } catch (error) {
        if (button) {
          button.disabled = false;
          button.textContent = originalLabel;
        }
        showToast(error.message || "Metadata refresh could not start.", "error");
      }
    });

    const closeWorkflow = () => {
      if (!workflowDialog?.open) return;
      workflowDialog.close();
      workflowDialog.classList.remove("title-workflow-dialog", "loading");
      workflowBody?.replaceChildren();
      workflowOpener?.focus?.();
      workflowOpener = null;
      workflowKind = "";
    };

    const normalizeWorkflowContent = () => {
      if (!workflowBody) return;
      workflowBody.querySelectorAll(".back").forEach((node) => node.remove());
      workflowBody.querySelectorAll('input[name="return_to"]').forEach((input) => {
        input.value = `/titles/${titleId}`;
      });
    };

    const renderWorkflowResponse = async (response) => {
      const html = await response.text();
      const parsed = new DOMParser().parseFromString(html, "text/html");
      if (parsed.querySelector(".media-dossier") || new URL(response.url, window.location.href).pathname === `/titles/${titleId}`) {
        const options = workflowKind === "rename"
          ? {hero: false, onDisk: true}
          : {hero: workflowKind !== "collections", onDisk: false};
        await refreshDetail(options);
        closeWorkflow();
        showToast(
          workflowKind === "cover" ? "Cover updated."
            : workflowKind === "match" ? "Match updated."
            : workflowKind === "rename" ? "File information updated."
            : "Collection membership updated.",
        );
        return true;
      }

      const main = parsed.querySelector("main.shell") || parsed.querySelector("main");
      if (!main || !workflowBody) return false;
      workflowBody.replaceChildren(...[...main.childNodes].map((node) => document.importNode(node, true)));
      normalizeWorkflowContent();
      workflowDialog?.classList.remove("loading");
      workflowBody.scrollTop = 0;
      return true;
    };

    const openWorkflow = async (url, trigger = null) => {
      if (!workflowDialog || !workflowBody || typeof workflowDialog.showModal !== "function") {
        window.location.assign(url);
        return;
      }
      const parsedUrl = new URL(url, window.location.href);
      workflowKind = workflowKindFor(parsedUrl.pathname);
      workflowOpener = trigger;
      workflowDialog.classList.add("title-workflow-dialog", "loading");
      workflowBody.innerHTML = '<div class="empty">Loading…</div>';
      if (!workflowDialog.open) workflowDialog.showModal();
      try {
        const response = await fetch(parsedUrl.href, {
          credentials: "same-origin",
          cache: "no-store",
          headers: {"X-Requested-With": "InfoMancerDialog"},
        });
        if (!response.ok || !(await renderWorkflowResponse(response))) throw new Error(`HTTP ${response.status}`);
      } catch (_error) {
        closeWorkflow();
        window.location.assign(parsedUrl.href);
      }
    };

    document.addEventListener("click", (event) => {
      const link = event.target.closest("a");
      if (!link || event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      if (!dossier.contains(link) && !workflowBody?.contains(link)) return;
      let url;
      try { url = new URL(link.href, window.location.href); } catch (_error) { return; }
      if (url.origin !== window.location.origin || !workflowPath(url.pathname)) return;
      event.preventDefault();
      link.closest("details")?.removeAttribute("open");
      openWorkflow(url.href, link);
    }, true);

    workflowBody?.addEventListener("submit", async (event) => {
      const form = event.target.closest("form");
      if (!form) return;
      event.preventDefault();
      const submitter = event.submitter;
      submitter?.setAttribute("disabled", "");
      workflowDialog?.classList.add("loading");
      try {
        const method = (form.method || "get").toUpperCase();
        const action = new URL(form.action || window.location.href, window.location.href);
        let response;
        if (method === "GET") {
          const query = new URLSearchParams(new FormData(form));
          action.search = query.toString();
          response = await fetch(action.href, {
            credentials: "same-origin",
            cache: "no-store",
            headers: {"X-Requested-With": "InfoMancerDialog"},
          });
        } else {
          response = await fetch(action.href, {
            method,
            credentials: "same-origin",
            body: new FormData(form),
            headers: {"X-Requested-With": "InfoMancerDialog"},
          });
        }
        if (!response.ok || !(await renderWorkflowResponse(response))) throw new Error(`HTTP ${response.status}`);
      } catch (_error) {
        showToast("That workflow could not finish in the overlay. Opening the full page instead.", "error");
        const action = form.action;
        closeWorkflow();
        window.location.assign(action);
      } finally {
        submitter?.removeAttribute("disabled");
      }
    });
  });
})();
