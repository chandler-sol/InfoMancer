(() => {
  const form = document.getElementById('scheduled-fingerprint-form');
  if (!form) return;

  const frequency = form.querySelector('[data-schedule-frequency]');
  const dayValue = form.querySelector('[data-schedule-day-value]');
  const daySelect = form.querySelector('[data-schedule-day]');
  const dayHelp = form.querySelector('[data-schedule-day-help]');
  const timeValue = form.querySelector('[data-schedule-time-value]');
  const hourSelect = form.querySelector('[data-schedule-hour]');
  const minuteSelect = form.querySelector('[data-schedule-minute]');
  const periodSelect = form.querySelector('[data-schedule-period]');

  const weekdays = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];
  const option = (value, label, selected = false) => {
    const item = document.createElement('option');
    item.value = String(value);
    item.textContent = label;
    item.selected = selected;
    return item;
  };

  const renderDays = () => {
    if (!frequency || !dayValue || !daySelect) return;
    const mode = frequency.value;
    let current = Number.parseInt(dayValue.value || '0', 10);
    daySelect.replaceChildren();

    if (mode === 'daily') {
      daySelect.append(option(current, 'Every day', true));
      daySelect.disabled = true;
      if (dayHelp) dayHelp.textContent = 'Daily schedules run every day.';
      return;
    }

    daySelect.disabled = false;
    if (mode === 'weekly') {
      if (!Number.isInteger(current) || current < 0 || current > 6) current = 0;
      weekdays.forEach((name, index) => daySelect.append(option(index, name, index === current)));
      dayValue.value = String(current);
      if (dayHelp) dayHelp.textContent = 'Choose the weekday for the scheduled run.';
      return;
    }

    if (!Number.isInteger(current) || current < 1 || current > 28) current = 1;
    for (let day = 1; day <= 28; day += 1) {
      daySelect.append(option(day, `Day ${day}`, day === current));
    }
    dayValue.value = String(current);
    if (dayHelp) dayHelp.textContent = 'Choose the calendar day for the scheduled run.';
  };

  const fillMinutes = () => {
    if (!minuteSelect || !timeValue) return;
    const match = /^(\d{1,2}):(\d{2})$/.exec(timeValue.value || '');
    const current = match ? Number.parseInt(match[2], 10) : 0;
    const values = new Set([current]);
    for (let minute = 0; minute < 60; minute += 5) values.add(minute);
    minuteSelect.replaceChildren();
    [...values].sort((a, b) => a - b).forEach((minute) => {
      const padded = String(minute).padStart(2, '0');
      minuteSelect.append(option(padded, padded, minute === current));
    });
  };

  const syncTime = () => {
    if (!timeValue || !hourSelect || !minuteSelect || !periodSelect) return;
    let hour = Number.parseInt(hourSelect.value, 10);
    const minute = String(minuteSelect.value || '00').padStart(2, '0');
    if (periodSelect.value === 'AM') {
      if (hour === 12) hour = 0;
    } else if (hour !== 12) {
      hour += 12;
    }
    timeValue.value = `${String(hour).padStart(2, '0')}:${minute}`;
  };

  frequency?.addEventListener('change', renderDays);
  daySelect?.addEventListener('change', () => {
    if (!daySelect.disabled && dayValue) dayValue.value = daySelect.value;
  });
  [hourSelect, minuteSelect, periodSelect].forEach((control) => control?.addEventListener('change', syncTime));
  form.addEventListener('submit', () => {
    if (daySelect && !daySelect.disabled && dayValue) dayValue.value = daySelect.value;
    syncTime();
  });

  fillMinutes();
  renderDays();
  syncTime();
})();
