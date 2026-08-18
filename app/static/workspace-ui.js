(() => {
  const current = document.currentScript;
  const version = current?.src ? new URL(current.src).search : "";

  const styles = document.createElement("link");
  styles.rel = "stylesheet";
  styles.href = `/static/task-widget.css${version}`;

  const navigationStyles = document.createElement("link");
  navigationStyles.rel = "stylesheet";
  navigationStyles.href = `/static/app-navigation.css${version}`;

  document.head.append(styles, navigationStyles);

  const core = document.createElement("script");
  core.src = `/static/workspace-ui-core.js${version}`;
  core.async = false;

  const tasks = document.createElement("script");
  tasks.src = `/static/task-widget.js${version}`;
  tasks.async = false;

  const navigation = document.createElement("script");
  navigation.src = `/static/app-navigation.js${version}`;
  navigation.async = false;

  document.head.append(core, tasks, navigation);

  if (document.querySelector('.settings-section-nav')) {
    const settingsStyles = document.createElement('link');
    settingsStyles.rel = 'stylesheet';
    settingsStyles.href = `/static/settings-polish.css${version}`;

    const settingsPolish = document.createElement('script');
    settingsPolish.src = `/static/settings-polish.js${version}`;
    settingsPolish.async = false;

    document.head.append(settingsStyles, settingsPolish);
  }

  if (document.querySelector('.library-table') && document.getElementById('cover-library')) {
    const libraryStyles = document.createElement('link');
    libraryStyles.rel = 'stylesheet';
    libraryStyles.href = `/static/library-performance.css${version}`;

    const librarySurface = document.createElement('script');
    librarySurface.src = `/static/library-surface-lazy.js${version}`;
    librarySurface.async = false;
    document.head.append(libraryStyles, librarySurface);
  }
})();
