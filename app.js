const sampleQuestions = [
  { title: 'By proceeding, you acknowledge and agree that the responses may be used for academic research purposes.', type: 'Multiple choice', answers: [{ label: 'Yes', value: 90 }, { label: 'No', value: 10 }] },
  { title: '1. Age group', type: 'Multiple choice', answers: [{ label: '18–24 years', value: 13 }, { label: '25–34 years', value: 27 }, { label: '35–44 years', value: 19 }, { label: '45 years and above', value: 41 }] },
  { title: '2. Gender', type: 'Multiple choice', answers: [{ label: 'Male', value: 54 }, { label: 'Female', value: 43 }, { label: 'Prefer not to say', value: 3 }] },
  { title: '3. How often do you use online forms?', type: 'Multiple choice', answers: [{ label: 'Daily', value: 22 }, { label: 'Weekly', value: 38 }, { label: 'Monthly', value: 25 }, { label: 'Rarely', value: 15 }] }
];

let questions = [];
let formName = '';
let responderUri = '';
let isSampleForm = false;
let activeJobId = '';
let runs = [];
let jobHistory = [];
const questionsEl = document.querySelector('#questions');
const emptyState = document.querySelector('#emptyState');
const previewDialog = document.querySelector('#previewDialog');

async function loadRuns() {
  try {
    const [runResponse, jobResponse] = await Promise.all([fetch('/api/runs'), fetch('/api/jobs')]);
    if (!runResponse.ok || !jobResponse.ok) throw new Error('Could not read sandbox workspace');
    [runs, jobHistory] = await Promise.all([runResponse.json(), jobResponse.json()]);
    renderRuns();
  } catch {
    runs = [];
    jobHistory = [];
    renderRuns();
  }
}

function setLoadMessage(message, kind = '') {
  const el = document.querySelector('#loadMessage');
  el.hidden = !message;
  el.className = `load-message ${kind}`;
  el.textContent = message;
}

function loadSample() {
  questions = structuredClone(sampleQuestions);
  assignRandomPercentages();
  formName = 'Sample form · response distribution demo';
  responderUri = '';
  isSampleForm = true;
  document.querySelector('#formUrl').value = '';
  setLoadMessage('Sample form loaded with random percentages. Adjust them or reshuffle before previewing.', 'success');
  renderQuestions();
}

async function loadLink() {
  const input = document.querySelector('#formUrl');
  const value = input.value.trim();
  if (!value) {
    setLoadMessage('Paste a Google Forms link, or choose “Try a sample form” to explore the sandbox.');
    input.focus();
    return;
  }
  let parsed;
  try { parsed = new URL(value); } catch { setLoadMessage('That link does not look valid. Paste the full Google Forms URL.'); return; }
  if (!['docs.google.com', 'forms.gle'].includes(parsed.hostname)) {
    setLoadMessage('Please use a Google Forms link, such as docs.google.com/forms/...');
    return;
  }
  setLoadMessage('Loading questions from Google Forms…');
  try {
    const googleStatus = await fetch('/api/google/status').then(r => r.json());
    if (!googleStatus.configured) throw new Error('One-time setup needed: put your Google OAuth client ID and secret in .env. Follow README.md, then restart run.bat.');
    if (!googleStatus.connected) throw new Error('Connect your Google account first, then load this form.');
    const response = await fetch('/api/forms/load', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ url: value }) });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Could not load this form.');
    if (!result.responderUri) throw new Error('Google did not provide a responder link for this form.');
    questions = result.questions;
    assignRandomPercentages();
    formName = result.title || 'Google Form';
    responderUri = result.responderUri;
    isSampleForm = false;
    setLoadMessage(`Loaded ${questions.length} supported questions from “${formName}” with random percentages. Adjust or reshuffle them before previewing.`, 'success');
    renderQuestions();
  } catch (error) {
    setLoadMessage(error.message || 'Could not load this form. Check the link and Google connection.');
  }
}

function renderQuestions() {
  questionsEl.replaceChildren();
  emptyState.hidden = questions.length > 0;
  document.querySelector('#randomizePercentages').disabled = questions.length === 0;
  questions.forEach((question, qi) => {
    const card = document.createElement('article');
    card.className = 'question-card';
    const head = document.createElement('div');
    head.className = 'question-head';
    const title = document.createElement('div');
    title.innerHTML = `<div class="question-title">${escapeHtml(question.title)}</div><div class="question-type">${escapeHtml(question.type)}</div>`;
    head.append(title);
    const answers = document.createElement('div');
    answers.className = 'question-answers';
    const checkboxType = question.type === 'Checkbox' || question.type === 'Checkbox grid';
    const inputLabel = checkboxType ? 'selection probability' : 'response percentage';
    question.answers.forEach((answer, ai) => {
      const field = document.createElement('div');
      field.className = 'answer-field';
      const label = document.createElement('label');
      label.textContent = answer.label;
      label.htmlFor = `q${qi}a${ai}`;
      const input = document.createElement('input');
      input.id = `q${qi}a${ai}`;
      input.className = 'percent-input';
      input.type = 'number';
      input.min = '0'; input.max = '100'; input.step = '1'; input.value = answer.value;
      input.setAttribute('aria-label', `${answer.label} ${inputLabel} for ${question.title}`);
      input.addEventListener('input', () => {
        question.answers[ai].value = Math.max(0, Math.min(100, Number(input.value) || 0));
        updateQuestionTotal(qi);
        updateSummary();
      });
      const suffix = document.createElement('span');
      suffix.className = 'percent-suffix'; suffix.textContent = '%';
      field.append(label, input, suffix); answers.append(field);
    });
    const totalRow = document.createElement('div');
    totalRow.className = 'total-row';
    const chip = document.createElement('span'); chip.className = 'total-chip'; chip.id = `total${qi}`;
    const status = document.createElement('span'); status.className = 'total-message'; status.id = `status${qi}`;
    const hint = document.createElement('span'); hint.className = 'total-message';
    hint.textContent = checkboxType ? 'Each option is selected independently at this rate.' : 'Choose the share of responses for each option.';
    totalRow.append(chip, status, hint);
    card.append(head, answers, totalRow);
    questionsEl.append(card);
    updateQuestionTotal(qi);
  });
  updateSummary();
}

function updateQuestionTotal(qi) {
  const question = questions[qi];
  const total = question.answers.reduce((sum, answer) => sum + Number(answer.value || 0), 0);
  const independent = question.type === 'Checkbox' || question.type === 'Checkbox grid';
  const chip = document.querySelector(`#total${qi}`);
  const status = document.querySelector(`#status${qi}`);
  if (!chip) return;
  const ready = independent || total === 100;
  chip.textContent = independent ? 'Independent rates' : `Total: ${total}% ${total === 100 ? '✓ OK' : ''}`;
  chip.className = `total-chip ${ready ? 'valid' : 'invalid'}`;
  status.textContent = independent ? 'No 100% total required' : total === 100 ? 'Distribution ready' : `${total < 100 ? `${100-total}% left to assign` : `${total-100}% over`}`;
}

function randomPercentageDistribution(length) {
  const weights = Array.from({ length }, () => 1 + Math.floor(Math.random() * 1000));
  const total = weights.reduce((sum, value) => sum + value, 0);
  const shares = weights.map((weight, index) => {
    const exact = weight * 100 / total;
    return { index, value: Math.floor(exact), remainder: exact - Math.floor(exact) };
  });
  let remaining = 100 - shares.reduce((sum, share) => sum + share.value, 0);
  shares.slice().sort((a, b) => b.remainder - a.remainder || Math.random() - 0.5)
    .slice(0, remaining).forEach(share => { shares[share.index].value += 1; });
  return shares.map(share => share.value);
}

function assignRandomPercentages() {
  questions.forEach(question => {
    const independent = question.type === 'Checkbox' || question.type === 'Checkbox grid';
    if (independent) {
      question.answers.forEach(answer => { answer.value = 10 + Math.floor(Math.random() * 71); });
    } else {
      const values = randomPercentageDistribution(question.answers.length);
      question.answers.forEach((answer, index) => { answer.value = values[index]; });
    }
  });
}

function randomizePercentages() {
  assignRandomPercentages();
  renderQuestions();
  showToast('Random percentages added. Review them before previewing.');
}

function validSetup() {
  return questions.length > 0 && questions.every(q => {
    if (q.type === 'Checkbox' || q.type === 'Checkbox grid') return !q.required || q.answers.some(a => Number(a.value) > 0);
    return q.answers.reduce((sum, a) => sum + Number(a.value || 0), 0) === 100;
  });
}

function updateSummary() {
  document.querySelector('#summaryForm').textContent = formName || 'Not connected';
  document.querySelector('#summaryQuestions').textContent = questions.length ? `${questions.length} questions` : '—';
  document.querySelector('#summaryCount').textContent = `${getResponseCount()}`;
  const ready = validSetup();
  const status = document.querySelector('#summaryStatus');
  status.textContent = questions.length ? (ready ? 'Ready to preview' : 'Needs adjustment') : 'Not set';
  status.className = ready ? 'destination' : 'muted';
  document.querySelector('#previewButton').disabled = !ready;
}

function allocateAnswers(answers, count) {
  const allocations = answers.map((answer, index) => {
    const exact = Number(answer.value) * count / 100;
    return { index, amount: Math.floor(exact), remainder: exact - Math.floor(exact) };
  });
  let left = count - allocations.reduce((sum, item) => sum + item.amount, 0);
  allocations.sort((a, b) => b.remainder - a.remainder || Math.random() - 0.5);
  for (let i = 0; i < left; i++) allocations[i].amount++;
  const result = [];
  allocations.forEach(item => { for (let i = 0; i < item.amount; i++) result.push(answers[item.index].label); });
  for (let i = result.length - 1; i > 0; i--) { const j = Math.floor(Math.random() * (i + 1)); [result[i], result[j]] = [result[j], result[i]]; }
  return result;
}

function generateResponses(count = getResponseCount()) {
  const answerColumns = questions.map(q => {
    if (q.type === 'Checkbox' || q.type === 'Checkbox grid') {
      return Array.from({ length: count }, () => {
        const selected = q.answers.filter(answer => Math.random() * 100 < Number(answer.value)).map(answer => answer.label);
        if (q.required && selected.length === 0) {
          const weighted = q.answers.flatMap(answer => Array.from({ length: Number(answer.value) }, () => answer.label));
          selected.push(weighted[Math.floor(Math.random() * weighted.length)]);
        }
        return selected;
      });
    }
    return allocateAnswers(q.answers, count);
  });
  return Array.from({ length: count }, (_, i) => ({
    id: i + 1,
    answers: answerColumns.map(column => column[i])
  }));
}

function showPreview() {
  const responses = generateResponses();
  document.querySelector('#previewSubtitle').textContent = `${responses.length} generated examples · ${formName} · Review before running`;
  const holder = document.querySelector('#responseTable');
  holder.replaceChildren();
  const scroll = document.createElement('div'); scroll.className = 'table-scroll';
  const table = document.createElement('table');
  const thead = document.createElement('thead'); const headRow = document.createElement('tr');
  const responseHead = document.createElement('th'); responseHead.textContent = 'Sample'; headRow.append(responseHead);
  questions.forEach(q => { const th = document.createElement('th'); th.textContent = q.title; headRow.append(th); });
  thead.append(headRow); table.append(thead);
  const tbody = document.createElement('tbody');
  responses.forEach(response => {
    const tr = document.createElement('tr'); const id = document.createElement('td'); id.textContent = `#${String(response.id).padStart(2, '0')}`; tr.append(id);
    response.answers.forEach(answer => { const td = document.createElement('td'); td.textContent = Array.isArray(answer) ? (answer.length ? answer.join(', ') : '—') : answer; tr.append(td); }); tbody.append(tr);
  });
  table.append(tbody); scroll.append(table); holder.append(scroll);
  const canSubmit = !isSampleForm;
  const confirmWrap = document.querySelector('#submitConfirmWrap');
  confirmWrap.hidden = !canSubmit;
  document.querySelector('#confirmLiveSubmission').checked = false;
  document.querySelector('#previewNotice').innerHTML = canSubmit
    ? '<span class="sandbox-dot"></span> This will create real responses in the selected Google Form'
    : '<span class="sandbox-dot"></span> Preview only — nothing is submitted';
  const runButton = document.querySelector('#runButton');
  runButton.hidden = false;
  runButton.innerHTML = canSubmit ? `Submit ${responses.length} responses <span>→</span>` : 'Save sample run <span>→</span>';
  runButton.onclick = () => canSubmit ? startSubmission(responses) : saveRun(responses);
  runButton.disabled = canSubmit;
  document.querySelector('#confirmLiveSubmission').onchange = canSubmit
    ? e => { runButton.disabled = !e.target.checked; }
    : null;
  previewDialog.showModal();
}

function getResponseCount() {
  const input = document.querySelector('#responseCount');
  const value = Math.round(Number(input?.value) || 10);
  return Math.max(1, Math.min(50, value));
}

async function startSubmission(responses) {
  const runButton = document.querySelector('#runButton');
  runButton.disabled = true;
  runButton.textContent = 'Starting…';
  try {
    const payload = { url: document.querySelector('#formUrl').value.trim(), responderUri, title: formName,
      confirmedTestForm: document.querySelector('#confirmLiveSubmission').checked,
      questions: questions.map(q => ({ title: q.title, type: q.type, required: q.required, groupTitle: q.groupTitle, rowTitle: q.rowTitle })), responses };
    const response = await fetch('/api/forms/submit', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Could not start the test run.');
    activeJobId = result.jobId;
    previewDialog.close();
    openJobDialog(responses.length);
    pollJob();
  } catch (error) {
    showToast(error.message || 'Could not start the test run.');
    runButton.disabled = !document.querySelector('#confirmLiveSubmission').checked;
    runButton.innerHTML = `Submit ${responses.length} responses <span>→</span>`;
  }
}

function openJobDialog(total) {
  document.querySelector('#jobTitle').textContent = 'Submitting responses';
  document.querySelector('#jobSubtitle').textContent = 'Please be patient while FormLab fills and submits each response.';
  document.querySelector('#jobProgressText').textContent = 'Starting the form automation…';
  document.querySelector('#jobProgressCount').textContent = `0 / ${total}`;
  document.querySelector('#jobProgressBar').style.width = '0%';
  document.querySelector('#jobLogs').replaceChildren();
  document.querySelector('#jobDone').hidden = true;
  document.querySelector('#jobDone').className = 'job-done';
  document.querySelector('#closeJobDialog').hidden = true;
  document.querySelector('.spinner').hidden = false;
  document.querySelector('#jobDialog').showModal();
}

async function pollJob() {
  if (!activeJobId) return;
  try {
    const response = await fetch(`/api/jobs/${encodeURIComponent(activeJobId)}`);
    const job = await response.json();
    const logs = document.querySelector('#jobLogs');
    const existing = logs.children.length;
    job.logs.slice(existing).forEach(entry => {
      const li = document.createElement('li');
      if (entry.level) li.classList.add(entry.level);
      const time = document.createElement('time'); time.textContent = entry.time;
      const message = document.createElement('span'); message.textContent = entry.message;
      li.append(time, message); logs.append(li);
    });
    const total = job.total || 0;
    const complete = job.completed || 0;
    const processed = job.processed ?? complete;
    const skipped = job.skipped || 0;
    document.querySelector('#jobProgressCount').textContent = `${complete} / ${total}`;
    document.querySelector('#jobProgressBar').style.width = `${total ? processed / total * 100 : 0}%`;
    document.querySelector('#jobProgressText').textContent = job.current || 'Processing…';
    logs.scrollTop = logs.scrollHeight;
    if (job.status === 'complete' || job.status === 'error') {
      document.querySelector('.spinner').hidden = true;
      const done = document.querySelector('#jobDone');
      done.hidden = false;
      done.className = `job-done ${job.status === 'error' || skipped ? 'error' : ''}`;
      done.textContent = job.status === 'complete'
        ? `Finished — ${complete} of ${total} responses confirmed${skipped ? `, ${skipped} skipped. See the activity log.` : '.'}`
        : `Stopped after ${complete} confirmed submissions. ${job.error || 'See the activity log.'}`;
      document.querySelector('#jobTitle').textContent = job.status === 'complete' ? 'Test run complete' : 'Test run stopped';
      document.querySelector('#jobSubtitle').textContent = 'The activity log has been saved to this project.';
      document.querySelector('#closeJobDialog').hidden = false;
      activeJobId = '';
      loadRuns();
      return;
    }
    setTimeout(pollJob, 700);
  } catch (error) {
    document.querySelector('#jobProgressText').textContent = 'Lost contact with the local server. Check run.bat.';
    setTimeout(pollJob, 1500);
  }
}

async function saveRun(responses) {
  const run = { id: crypto.randomUUID?.() || String(Date.now()), form: formName, count: responses.length, created: new Date().toISOString(), questions: questions.map(q => q.title), responses };
  try {
    const response = await fetch('/api/runs', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(run) });
    if (!response.ok) throw new Error('The local sandbox could not save this run. Start the app using run.bat.');
    runs.unshift(run);
  } catch (error) {
    showToast(error.message || 'The local sandbox could not save this run.');
    return;
  }
  previewDialog.close();
  renderRuns();
  showToast('Sample run saved to this project’s sandbox workspace.');
}

function renderRuns() {
  const list = document.querySelector('#recentRuns'); list.replaceChildren();
  if (!runs.length && !jobHistory.length) { list.className = 'recent-empty'; list.innerHTML = '<span>◷</span><p>No test runs yet</p>'; return; }
  list.className = '';
  runs.slice(0, 4).forEach(run => {
    const item = document.createElement('div'); item.className = 'recent-item'; item.title = run.form;
    item.innerHTML = `<span class="run-icon">✓</span><span class="run-copy"><strong>${escapeHtml(run.form)}</strong><small>${new Date(run.created).toLocaleString()}</small></span><span class="run-count">${run.count}</span>`;
    item.addEventListener('click', () => showSavedRun(run)); list.append(item);
  });
  jobHistory.slice(0, 6).forEach(job => {
    const item = document.createElement('div'); item.className = 'recent-item'; item.title = job.title;
    const resultText = job.status === 'complete'
      ? `${job.completed}/${job.total} submitted${job.skipped ? ` · ${job.skipped} skipped` : ''}`
      : `${job.completed}/${job.total} · ${job.status}`;
    item.innerHTML = `<span class="run-icon">${job.status === 'complete' ? '✓' : '!'}</span><span class="run-copy"><strong>${escapeHtml(job.title || 'Google Form test')}</strong><small>${escapeHtml(resultText)}</small></span><span class="run-count">LOG</span>`;
    item.addEventListener('click', () => showSavedJob(job)); list.append(item);
  });
}

function showSavedJob(job) {
  activeJobId = '';
  document.querySelector('.spinner').hidden = true;
  document.querySelector('#jobTitle').textContent = job.status === 'complete' ? 'Test run complete' : 'Test run log';
  document.querySelector('#jobSubtitle').textContent = `Saved run · ${job.title || 'Google Form'}`;
  document.querySelector('#jobProgressCount').textContent = `${job.completed || 0} / ${job.total || 0}`;
  document.querySelector('#jobProgressBar').style.width = `${job.total ? (job.processed ?? job.completed ?? 0) / job.total * 100 : 0}%`;
  document.querySelector('#jobProgressText').textContent = job.current || job.status;
  const logs = document.querySelector('#jobLogs'); logs.replaceChildren();
  (job.logs || []).forEach(entry => {
    const li = document.createElement('li'); if (entry.level) li.classList.add(entry.level);
    const time = document.createElement('time'); time.textContent = entry.time;
    const message = document.createElement('span'); message.textContent = entry.message;
    li.append(time, message); logs.append(li);
  });
  const done = document.querySelector('#jobDone'); done.hidden = false;
  done.className = `job-done ${job.status !== 'complete' || job.skipped ? 'error' : ''}`;
  done.textContent = job.status === 'complete'
    ? `Finished — ${job.completed || 0} of ${job.total || 0} responses confirmed${job.skipped ? `, ${job.skipped} skipped. See the activity log.` : '.'}`
    : `Stopped after ${job.completed || 0} confirmed submissions. ${job.error || ''}`;
  document.querySelector('#closeJobDialog').hidden = false;
  document.querySelector('#jobDialog').showModal();
}

function showSavedRun(run) {
  document.querySelector('#submitConfirmWrap').hidden = true;
  document.querySelector('#confirmLiveSubmission').checked = false;
  document.querySelector('#previewNotice').innerHTML = '<span class="sandbox-dot"></span> Saved local sample — nothing was submitted';
  document.querySelector('#previewSubtitle').textContent = `${run.count} saved examples · ${run.form} · Sandbox workspace`;
  const holder = document.querySelector('#responseTable'); holder.replaceChildren();
  const scroll = document.createElement('div'); scroll.className = 'table-scroll'; const table = document.createElement('table');
  const head = document.createElement('tr'); ['Sample', ...run.questions].forEach(text => { const th = document.createElement('th'); th.textContent = text; head.append(th); });
  const thead = document.createElement('thead'); thead.append(head); table.append(thead);
  const body = document.createElement('tbody'); run.responses.forEach(row => { const tr = document.createElement('tr'); const id = document.createElement('td'); id.textContent = `#${String(row.id).padStart(2, '0')}`; tr.append(id); row.answers.forEach(answer => { const td = document.createElement('td'); td.textContent = answer; tr.append(td); }); body.append(tr); });
  table.append(body); scroll.append(table); holder.append(scroll);
  document.querySelector('#runButton').hidden = true;
  previewDialog.showModal();
  previewDialog.addEventListener('close', () => { document.querySelector('#runButton').hidden = false; }, { once: true });
}

function showToast(message) { const toast = document.querySelector('#toast'); toast.textContent = message; toast.classList.add('show'); setTimeout(() => toast.classList.remove('show'), 3000); }
function escapeHtml(value) { const d = document.createElement('div'); d.textContent = value; return d.innerHTML; }

document.querySelector('#demoLink').addEventListener('click', loadSample);
document.querySelector('#loadForm').addEventListener('click', loadLink);
document.querySelector('#formUrl').addEventListener('keydown', e => { if (e.key === 'Enter') loadLink(); });
document.querySelector('#connectGoogle').addEventListener('click', async () => {
  try {
    const status = await fetch('/api/google/status').then(r => r.json());
    if (!status.configured) {
      setLoadMessage('One-time setup needed: put your Google OAuth client ID and secret in .env. Follow README.md for steps, then restart run.bat.');
      return;
    }
  } catch {
    setLoadMessage('Could not reach the local backend. Close and restart run.bat.');
    return;
  }
  sessionStorage.setItem('formlab.pendingFormUrl', document.querySelector('#formUrl').value.trim());
  // The OAuth redirect URI registered in this project is bound to port 8000.
  window.location.assign('http://127.0.0.1:8000/api/auth/google');
});
document.querySelector('#previewButton').addEventListener('click', showPreview);
document.querySelector('#randomizePercentages').addEventListener('click', randomizePercentages);
document.querySelector('#closeDialog').addEventListener('click', () => previewDialog.close());
document.querySelector('#countDown').addEventListener('click', () => { const input = document.querySelector('#responseCount'); input.value = Math.max(1, getResponseCount() - 1); updateSummary(); });
document.querySelector('#countUp').addEventListener('click', () => { const input = document.querySelector('#responseCount'); input.value = Math.min(50, getResponseCount() + 1); updateSummary(); });
document.querySelector('#responseCount').addEventListener('input', updateSummary);
document.querySelector('#responseCount').addEventListener('change', () => { document.querySelector('#responseCount').value = getResponseCount(); updateSummary(); });
document.querySelector('#closeJobDialog').addEventListener('click', () => document.querySelector('#jobDialog').close());
document.querySelector('#clearRuns').addEventListener('click', async () => {
  try {
    const response = await fetch('/api/runs', { method: 'DELETE' });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Could not clear recent runs.');
    runs = [];
    jobHistory = jobHistory.filter(job => job.status === 'queued' || job.status === 'running');
    renderRuns();
    showToast('Recent runs cleared.');
  } catch { showToast('Could not reach the local sandbox. Start the app using run.bat.'); }
});
loadRuns();
const linkedForm = new URLSearchParams(location.search).get('formUrl');
if (linkedForm) {
  document.querySelector('#formUrl').value = linkedForm;
  loadLink();
}
fetch('/api/google/status').then(response => response.json()).then(status => {
  const button = document.querySelector('#connectGoogle');
  if (status.connected) button.textContent = 'Reconnect Google';
}).catch(() => {});
if (new URLSearchParams(location.search).has('google')) {
  const pendingUrl = sessionStorage.getItem('formlab.pendingFormUrl');
  sessionStorage.removeItem('formlab.pendingFormUrl');
  const outcome = new URLSearchParams(location.search).get('google');
  history.replaceState(null, '', '/');
  if (outcome === 'error') setLoadMessage('Google sign-in was cancelled or could not be completed. Try Connect Google again.');
  else if (pendingUrl) { document.querySelector('#formUrl').value = pendingUrl; loadLink(); }
  else setLoadMessage('Google account connected. Paste a Google Form link and choose Load form.', 'success');
}
