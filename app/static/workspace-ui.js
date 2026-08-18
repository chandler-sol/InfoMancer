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
})();
