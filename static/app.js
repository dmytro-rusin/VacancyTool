(() => {
  const statusSelected = new Set(JSON.parse(sessionStorage.getItem('statuses') || '[]'));
  const scoreSelected = new Set(JSON.parse(sessionStorage.getItem('scores') || '[]'));
  const platformSelected = new Set(JSON.parse(sessionStorage.getItem('platforms') || '[]'));
  const cards = [...document.querySelectorAll('.vacancy')];
  const statusButtons = [...document.querySelectorAll('[data-status]')];
  const scoreButtons = [...document.querySelectorAll('[data-score]')];
  const platformButtons = [...document.querySelectorAll('[data-platform]')];
  const statusOrder = ['New', 'Interested', 'Applied', 'Viewed', 'Postponed', 'Rejected', 'Deleted', 'Irrelevant'];
  let nextRun = null;
  let lastRunId = null;

  function scoreMatches(score) {
    if (!scoreSelected.size) return true;
    return [...scoreSelected].some(min => score >= min && score <= (min === 75 ? 100 : min + 25));
  }
  function platformMatches(card) {
    if (!platformSelected.size) return true;
    const platforms = new Set((card.dataset.platforms || '').split('|').filter(Boolean));
    return [...platformSelected].some(platform => platforms.has(platform));
  }
  function sortCards() {
    const list = document.querySelector('#vacancies');
    cards.sort((a, b) => statusOrder.indexOf(a.dataset.status) - statusOrder.indexOf(b.dataset.status) ||
      Number(b.dataset.score) - Number(a.dataset.score) ||
      (Date.parse(b.dataset.published) || 0) - (Date.parse(a.dataset.published) || 0) ||
      Number(b.dataset.id) - Number(a.dataset.id));
    cards.forEach(card => list.append(card));
  }
  function applyFilters() {
    sortCards();
    let visible = 0;
    for (const card of cards) {
      const ok = (!statusSelected.size || statusSelected.has(card.dataset.status)) &&
        scoreMatches(Number(card.dataset.score)) && platformMatches(card);
      card.hidden = !ok;
      if (ok) visible++;
    }
    statusButtons.forEach(b => b.classList.toggle('active', statusSelected.has(b.dataset.status)));
    scoreButtons.forEach(b => b.classList.toggle('active', scoreSelected.has(Number(b.dataset.score))));
    platformButtons.forEach(b => b.classList.toggle('active', platformSelected.has(b.dataset.platform)));
    document.querySelector('[data-all="status"]').classList.toggle('active', !statusSelected.size);
    document.querySelector('[data-all="score"]').classList.toggle('active', !scoreSelected.size);
    document.querySelector('[data-all="platform"]').classList.toggle('active', !platformSelected.size);
    document.querySelector('#visible-count').textContent = `Показано: ${visible}`;
    document.querySelector('#empty').hidden = visible !== 0;
    sessionStorage.setItem('statuses', JSON.stringify([...statusSelected]));
    sessionStorage.setItem('scores', JSON.stringify([...scoreSelected]));
    sessionStorage.setItem('platforms', JSON.stringify([...platformSelected]));
  }
  document.querySelector('#status-filters').addEventListener('click', e => {
    const button = e.target.closest('button'); if (!button) return;
    if (button.dataset.all) statusSelected.clear();
    else if (statusSelected.has(button.dataset.status)) statusSelected.delete(button.dataset.status);
    else statusSelected.add(button.dataset.status);
    applyFilters();
  });
  document.querySelector('#score-filters').addEventListener('click', e => {
    const button = e.target.closest('button'); if (!button) return;
    if (button.dataset.all) scoreSelected.clear();
    else { const score = Number(button.dataset.score); if (scoreSelected.has(score)) scoreSelected.delete(score); else scoreSelected.add(score); }
    applyFilters();
  });
  document.querySelector('#platform-filters').addEventListener('click', e => {
    const button = e.target.closest('button'); if (!button) return;
    if (button.dataset.all) platformSelected.clear();
    else if (platformSelected.has(button.dataset.platform)) platformSelected.delete(button.dataset.platform);
    else platformSelected.add(button.dataset.platform);
    applyFilters();
  });

  function dateText(value) { return value ? new Date(value).toLocaleString('uk-UA', {day:'numeric', month:'short', hour:'2-digit', minute:'2-digit'}) : '—'; }
  document.querySelectorAll('.published').forEach(el => { el.textContent = `Опубліковано ${dateText(el.dataset.date)}`; });
  function countdown() {
    if (!nextRun) return;
    const seconds = Math.max(0, Math.ceil((nextRun.getTime() - Date.now()) / 1000));
    const minutes = Math.floor(seconds / 60);
    document.querySelector('#next-update').textContent = `Наступне: ${dateText(nextRun.toISOString())} · через ${minutes} хв ${seconds % 60} с`;
  }
  setInterval(countdown, 1000);
  async function poll() {
    try {
      const response = await fetch('/api/state'); if (!response.ok) return;
      const data = await response.json();
      nextRun = data.next_run ? new Date(data.next_run) : null;
      const run = data.last_run;
      document.querySelector('#last-update').textContent = `Останнє оновлення: ${run && run.finished_at ? dateText(run.finished_at) : '—'}`;
      document.querySelector('#run-indicator').classList.toggle('running', data.running);
      document.querySelector('#refresh').disabled = data.running;
      document.querySelector('#refresh').textContent = data.running ? 'Оновлюємо…' : 'Оновити зараз';
      document.querySelector('#run-note').textContent = run && run.finished_at && run.error_count ? `· помилок: ${run.error_count}` : '';
      document.querySelector('#mail-note').textContent = data.mail_pending ?
        `· ${data.mail_pending} очікують листа${data.mail_configured ? '' : ' (налаштуйте пошту)'}` : '';
      for (const [status, count] of Object.entries(data.counts)) {
        const el = document.querySelector(`[data-count="${status}"]`); if (el) el.textContent = `(${count})`;
      }
      document.querySelector('#count-all').textContent = `(${Object.values(data.counts).reduce((a,b)=>a+b,0)})`;
      if (lastRunId !== null && run && run.id !== lastRunId && run.finished_at) location.reload();
      if (lastRunId === null && run) lastRunId = run.id;
      countdown();
    } catch (_) { document.querySelector('#run-note').textContent = '· Немає зв’язку із сервером'; }
  }
  document.querySelector('#refresh').addEventListener('click', async () => {
    const response = await fetch('/api/refresh', {method:'POST'});
    if (!response.ok) document.querySelector('#run-note').textContent = '· Оновлення вже виконується';
    await poll();
  });
  for (const card of cards) {
    card.querySelector('select').addEventListener('change', async e => {
      const select = e.target, old = card.dataset.status, status = select.value;
      select.disabled = true;
      try {
        const response = await fetch(`/api/vacancies/${card.dataset.id}/status`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({status})});
        if (!response.ok) throw Error('Не вдалося зберегти');
        card.dataset.status = status;
        applyFilters();
        await poll();
      } catch (error) { select.value = old; alert(error.message); }
      finally { select.disabled = false; }
    });
  }
  const panel = document.querySelector('#schedule-panel');
  document.querySelector('#schedule-toggle').addEventListener('click', e => {
    panel.hidden = !panel.hidden; e.target.setAttribute('aria-expanded', String(!panel.hidden));
  });
  document.querySelector('#save-schedule').addEventListener('click', async () => {
    const schedule = [...document.querySelectorAll('.schedule-row')].map(row => ({
      start:row.querySelector('.start').value, end:row.querySelector('.end').value,
      minutes:Number(row.querySelector('.minutes').value)
    }));
    const message = document.querySelector('#schedule-message');
    try {
      const response = await fetch('/api/schedule', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({schedule})});
      const data = await response.json(); if (!response.ok) throw Error(data.error || 'Не вдалося зберегти');
      message.textContent = 'Збережено'; await poll();
    } catch (error) { message.textContent = error.message; }
  });
  applyFilters(); poll(); setInterval(poll, 10000);
})();
