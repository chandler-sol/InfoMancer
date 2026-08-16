from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, content: str) -> None:
    (ROOT / path).write_text(content, encoding="utf-8")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


def replace_between(text: str, start_marker: str, end_marker: str, replacement: str, label: str) -> str:
    start = text.find(start_marker)
    if start < 0:
        raise RuntimeError(f"{label}: start marker not found")
    end = text.find(end_marker, start)
    if end < 0:
        raise RuntimeError(f"{label}: end marker not found")
    return text[:start] + replacement + text[end:]


def patch_detail_template() -> None:
    path = "app/templates/detail.html"
    text = read(path)

    text = replace_once(
        text,
        "<select id=\"season-filter\"><option value=\"all\">All seasons</option>{% for season in seasons %}<option value=\"{{ season }}\">Season {{ '%02d'|format(season) }}</option>{% endfor %}</select>",
        "<select id=\"season-filter\"><option value=\"all\">All seasons</option>{% for season in seasons %}<option value=\"{{ season }}\">{% if season == 0 %}Specials{% else %}Season {{ '%02d'|format(season) }}{% endif %}</option>{% endfor %}</select>",
        "season filter specials label",
    )

    old_nav = "{% if title.kind == 'tv' and seasons %}<nav class=\"season-nav\" aria-label=\"Filter episodes by season\"><button type=\"button\" class=\"active\" data-season-choice=\"all\">All</button>{% for season in seasons %}<button type=\"button\" data-season-choice=\"{{ season }}\">{% if season == 0 %}Specials{% else %}Season {{ '%02d'|format(season) }}{% endif %}</button>{% endfor %}</nav>{% endif %}"
    new_nav = "{% if title.kind == 'tv' and seasons %}<nav class=\"season-nav\" aria-label=\"Filter episodes by season\"><button type=\"button\" class=\"active\" data-season-choice=\"all\">All</button>{% for season in seasons %}<button type=\"button\" data-season-choice=\"{{ season }}\">{% if season == 0 %}Specials{% else %}Season {{ '%02d'|format(season) }}{% endif %}</button>{% endfor %}</nav><div class=\"season-collapse-toolbar\" aria-label=\"Season display controls\"><span>Season groups start collapsed</span><button type=\"button\" id=\"expand-all-seasons\">Expand all</button><button type=\"button\" id=\"collapse-all-seasons\">Collapse all</button></div>{% endif %}"
    text = replace_once(text, old_nav, new_nav, "season navigation toolbar")

    old_heading = "<div class=\"season-heading\" id=\"season-{{ file.season }}\" data-season-heading=\"{{ file.season }}\">{% if file.season == 0 %}Specials{% else %}Season {{ '%02d'|format(file.season) }}{% endif %}</div>"
    new_heading = "<button type=\"button\" class=\"season-heading\" id=\"season-{{ file.season }}\" data-season-heading=\"{{ file.season }}\" aria-expanded=\"false\"><span>{% if file.season == 0 %}Specials{% else %}Season {{ '%02d'|format(file.season) }}{% endif %}</span><span class=\"season-heading-meta\"><span data-season-count=\"{{ file.season }}\"></span><span class=\"season-heading-chevron\" aria-hidden=\"true\">⌄</span></span></button>"
    text = replace_once(text, old_heading, new_heading, "season heading button")

    new_js = '''  const seasonFilter = document.getElementById("season-filter");
  const seasonChoices = Array.from(document.querySelectorAll("[data-season-choice]"));
  const seasonHeadings = Array.from(document.querySelectorAll("[data-season-heading]"));
  const episodeRows = Array.from(document.querySelectorAll("[data-episode-row]"));
  const visibleCount = document.getElementById("visible-file-count");
  const expandAllSeasons = document.getElementById("expand-all-seasons");
  const collapseAllSeasons = document.getElementById("collapse-all-seasons");
  const expandedSeasons = new Set();
  let activeSeason = "all";

  const rowsForSeason = (season) => episodeRows.filter((row) => row.dataset.season === season);
  const updateSeasonView = () => {
    let filteredCount = 0;
    episodeRows.forEach((row) => {
      const season = row.dataset.season;
      const inFilter = activeSeason === "all" || season === activeSeason;
      if (inFilter) filteredCount += 1;
      const expanded = season === "other" || expandedSeasons.has(season);
      row.hidden = !(inFilter && expanded);
    });
    seasonHeadings.forEach((heading) => {
      const season = heading.dataset.seasonHeading;
      const inFilter = activeSeason === "all" || season === activeSeason;
      const expanded = expandedSeasons.has(season);
      heading.hidden = !inFilter;
      heading.setAttribute("aria-expanded", String(expanded));
      const count = heading.querySelector("[data-season-count]");
      if (count) {
        const files = rowsForSeason(season).length;
        count.textContent = `${files} ${files === 1 ? "file" : "files"}`;
      }
    });
    seasonChoices.forEach((choice) => {
      choice.classList.toggle("active", choice.dataset.seasonChoice === activeSeason);
    });
    if (seasonFilter) seasonFilter.value = activeSeason;
    if (visibleCount) visibleCount.textContent = filteredCount;
  };

  const applySeason = (season) => {
    activeSeason = season;
    // Filtering directly to one season should reveal its episodes rather than
    // leaving the user with an apparently empty filtered view.
    if (season !== "all") expandedSeasons.add(season);
    updateSeasonView();
  };

  seasonFilter?.addEventListener("change", () => applySeason(seasonFilter.value));
  seasonChoices.forEach((choice) => {
    choice.addEventListener("click", () => applySeason(choice.dataset.seasonChoice));
  });
  seasonHeadings.forEach((heading) => {
    heading.addEventListener("click", () => {
      const season = heading.dataset.seasonHeading;
      if (expandedSeasons.has(season)) expandedSeasons.delete(season);
      else expandedSeasons.add(season);
      updateSeasonView();
    });
  });
  expandAllSeasons?.addEventListener("click", () => {
    seasonHeadings.forEach((heading) => expandedSeasons.add(heading.dataset.seasonHeading));
    updateSeasonView();
  });
  collapseAllSeasons?.addEventListener("click", () => {
    expandedSeasons.clear();
    updateSeasonView();
  });
  // Chandler's installation preference: season groups are collapsed until the
  // viewer explicitly opens one or chooses Expand all.
  updateSeasonView();
'''
    text = replace_between(
        text,
        '  const seasonFilter = document.getElementById("season-filter");\n',
        '\n\n  const creditMore = document.getElementById("credit-more");',
        new_js,
        "season interaction script",
    )
    write(path, text)


def patch_library_css() -> None:
    path = "app/static/library.css"
    text = read(path)
    marker = "/* Collapsible season groups: full TV title view */"
    if marker in text:
        raise RuntimeError("collapsible season CSS already exists")
    text += '''

/* Collapsible season groups: full TV title view */
.season-collapse-toolbar {
  align-items: center;
  display: flex;
  gap: 7px;
  justify-content: flex-end;
  margin: 4px 0 10px;
}
.season-collapse-toolbar > span {
  color: var(--muted);
  font-size: 11px;
  margin-right: 3px;
}
.season-collapse-toolbar button {
  background: transparent;
  border: 1px solid var(--line);
  border-radius: 4px;
  color: var(--muted);
  cursor: pointer;
  font: inherit;
  font-size: 11px;
  padding: 5px 9px;
}
.season-collapse-toolbar button:hover,
.season-collapse-toolbar button:focus-visible {
  border-color: var(--lime);
  color: var(--lime);
  outline: none;
}
button.season-heading {
  -webkit-appearance: none;
  appearance: none;
  align-items: center;
  background: #101820;
  border: 1px solid var(--line);
  border-radius: 4px;
  color: var(--text);
  cursor: pointer;
  display: flex;
  font: inherit;
  font-weight: 700;
  justify-content: space-between;
  text-align: left;
  width: 100%;
}
button.season-heading:hover,
button.season-heading:focus-visible {
  background: #141e27;
  border-color: var(--lime);
  color: var(--lime);
  outline: none;
}
.season-heading-meta {
  align-items: center;
  color: var(--muted);
  display: inline-flex;
  font-size: 11px;
  font-weight: 500;
  gap: 9px;
  white-space: nowrap;
}
.season-heading-chevron {
  color: var(--lime);
  display: inline-block;
  font-size: 16px;
  line-height: 1;
  transition: transform .16s ease;
}
.season-heading[aria-expanded="true"] .season-heading-chevron {
  transform: rotate(180deg);
}
@media (max-width: 680px) {
  .season-collapse-toolbar {
    justify-content: flex-start;
    flex-wrap: wrap;
  }
  .season-collapse-toolbar > span {
    flex-basis: 100%;
  }
}
'''
    write(path, text)


def patch_packaging_docs() -> None:
    path = "docs/PACKAGING.md"
    text = read(path)
    anchor = "- Sign both the launcher and installer with a trusted code-signing certificate.\n\nTest Windows 11 first"
    replacement = '''- Sign both the launcher and installer with a trusted code-signing certificate.

### Windows uninstall contract

The Windows uninstaller must leave no InfoMancer-owned state behind unless the
user explicitly chooses to save a recovery package. Before destructive removal,
offer **Create recovery backup & uninstall**, **Uninstall everything**, and
**Cancel**. A requested recovery package must be written to a user-selected
location outside InfoMancer-managed directories and verified before uninstall
continues; if creation or verification fails, keep InfoMancer installed unless
the user explicitly chooses to proceed without the backup.

A complete uninstall removes application binaries, databases, configuration,
provider-secret/encryption-key files, artwork, caches, logs, updater data,
crash data created by InfoMancer, shortcuts, services, scheduled tasks, file or
protocol associations, firewall rules, and registry values created by
InfoMancer. The cleanup implementation should use an explicit ownership
manifest rather than searching the whole machine by product name. **Media
files and user-selected recovery packages are never deleted.** Installer tests
must create representative state, uninstall, and assert that every registered
InfoMancer-owned resource is gone.

Test Windows 11 first'''
    text = replace_once(text, anchor, replacement, "Windows uninstall contract")
    write(path, text)


def add_tests() -> None:
    path = ROOT / "tests" / "test_collapsible_seasons.py"
    if path.exists():
        raise RuntimeError("tests/test_collapsible_seasons.py already exists")
    path.write_text('''import unittest\nfrom pathlib import Path\n\n\nROOT = Path(__file__).resolve().parents[1]\n\n\nclass CollapsibleSeasonContractTests(unittest.TestCase):\n    def test_full_tv_detail_starts_seasons_collapsed_with_bulk_controls(self):\n        template = (ROOT / "app/templates/detail.html").read_text(encoding="utf-8")\n        self.assertIn('class="season-heading"', template)\n        self.assertIn('aria-expanded="false"', template)\n        self.assertIn('id="expand-all-seasons"', template)\n        self.assertIn('id="collapse-all-seasons"', template)\n        self.assertIn('const expandedSeasons = new Set();', template)\n        self.assertIn('expandedSeasons.clear();', template)\n        self.assertIn('heading.setAttribute("aria-expanded", String(expanded));', template)\n\n    def test_direct_season_filter_expands_selected_season(self):\n        template = (ROOT / "app/templates/detail.html").read_text(encoding="utf-8")\n        self.assertIn('if (season !== "all") expandedSeasons.add(season);', template)\n        self.assertIn('row.hidden = !(inFilter && expanded);', template)\n        self.assertIn('Specials{% else %}Season', template)\n\n    def test_windows_packaging_requires_zero_residue_uninstall_and_recovery_offer(self):\n        packaging = (ROOT / "docs/PACKAGING.md").read_text(encoding="utf-8")\n        self.assertIn("Windows uninstall contract", packaging)\n        self.assertIn("Create recovery backup & uninstall", packaging)\n        self.assertIn("explicit ownership", packaging)\n        self.assertIn("Media files and user-selected recovery packages are never deleted", packaging)\n\n\nif __name__ == "__main__":\n    unittest.main()\n''', encoding="utf-8")


def main() -> None:
    patch_detail_template()
    patch_library_css()
    patch_packaging_docs()
    add_tests()


if __name__ == "__main__":
    main()
