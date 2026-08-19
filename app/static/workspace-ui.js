(() => {
  const current = document.currentScript;
  const version = current?.src ? new URL(current.src).search : "";

  const styles = document.createElement("link");
  styles.rel = "stylesheet";
  styles.href = `/static/task-widget.css${version}`;

  const navigationStyles = document.createElement("link");
  navigationStyles.rel = "stylesheet";
  navigationStyles.href = `/static/app-navigation.css${version}`;

  const actionMenuStyles = document.createElement("link");
  actionMenuStyles.rel = "stylesheet";
  actionMenuStyles.href = `/static/action-menu.css${version}`;

  document.head.append(styles, navigationStyles, actionMenuStyles);

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

  /* Settings polish now lives in settings.css and the nav labels are rendered
     correctly by the server. Keeping those rules in the render-blocking stylesheet
     prevents the Settings workspace from visibly reflowing after each page load. */

  if (document.querySelector('.saved-view-bar') && document.querySelector('.catalog-tabs')) {
    const savedViewStyles = document.createElement('link');
    savedViewStyles.rel = 'stylesheet';
    savedViewStyles.href = `/static/library-saved-views-polish.css${version}`;

    const savedViewPolish = document.createElement('script');
    savedViewPolish.src = `/static/library-saved-views-polish.js${version}`;
    savedViewPolish.async = false;

    document.head.append(savedViewStyles, savedViewPolish);
  }

  if (document.querySelector('.library-display-toolbar .alphabet')) {
    const letterJumpStyles = document.createElement('link');
    letterJumpStyles.rel = 'stylesheet';
    letterJumpStyles.href = `/static/library-letter-jump.css${version}`;

    const letterJump = document.createElement('script');
    letterJump.src = `/static/library-letter-jump.js${version}`;
    letterJump.async = false;

    document.head.append(letterJumpStyles, letterJump);
  }

  if (document.querySelector('.library-table') && document.getElementById('cover-library')) {
    const libraryStyles = document.createElement('link');
    libraryStyles.rel = 'stylesheet';
    libraryStyles.href = `/static/library-performance.css${version}`;

    const librarySelectionStyles = document.createElement('link');
    librarySelectionStyles.rel = 'stylesheet';
    librarySelectionStyles.href = `/static/library-selection-polish.css${version}`;

    const librarySurface = document.createElement('script');
    librarySurface.src = `/static/library-surface-lazy.js${version}`;
    librarySurface.async = false;

    const librarySelection = document.createElement('script');
    librarySelection.src = `/static/library-selection-polish.js${version}`;
    librarySelection.async = false;

    document.head.append(libraryStyles, librarySelectionStyles, librarySurface, librarySelection);
  }

  if (document.querySelector('.media-dossier')) {
    const detailStyles = document.createElement('link');
    detailStyles.rel = 'stylesheet';
    detailStyles.href = `/static/detail-page-polish.css${version}`;

    const detailPolish = document.createElement('script');
    detailPolish.src = `/static/detail-page-polish.js${version}`;
    detailPolish.async = false;

    document.head.append(detailStyles, detailPolish);
  }

  if (document.querySelector('.review-workspace')) {
    const reviewQueueStyles = document.createElement('link');
    reviewQueueStyles.rel = 'stylesheet';
    reviewQueueStyles.href = `/static/review-queue-polish.css${version}`;
    document.head.append(reviewQueueStyles);
  }
})();
