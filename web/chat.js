let sessionId = loadSessionId();
let apiContract;
let pendingQuestion = null;
let submitting = false;
let bearerToken = loadBearerToken();
let uploadedAttachments = [];
let cameraObjectUrl = '';
let cameraStreamController = null;
let cameraReconnectTimer = null;
let cameraRetryDelay = 500;
let cameraAvailable = false;
let cameraHasFrame = false;
let cameraClockOffsetMs = 0;
let cameraStreamStartedAt = 0;

const elements = {
  composer: document.getElementById('composer-form'),
  prompt: document.getElementById('prompt-input'),
  send: document.getElementById('send-button'),
  stop: document.getElementById('stop-button'),
  stream: document.getElementById('chat-stream'),
  template: document.getElementById('message-template'),
  activityTemplate: document.getElementById('activity-template'),
  welcome: document.getElementById('welcome-card'),
  connection: document.getElementById('connection-label'),
  camera: document.getElementById('live-camera'),
  cameraStatus: document.getElementById('camera-status'),
  cameraPlaceholder: document.getElementById('camera-placeholder'),
  cameraBadge: document.getElementById('camera-badge'),
  fileInput: document.getElementById('file-input'),
  attachmentList: document.getElementById('attachment-list'),
  composerStatus: document.getElementById('composer-status'),
  dropHint: document.getElementById('drop-hint'),
  tokenForm: document.getElementById('access-token-form'),
  tokenInput: document.getElementById('access-token-input'),
  sidebar: document.getElementById('app-sidebar'),
  cameraSidebar: document.getElementById('camera-sidebar'),
  panelBackdrop: document.getElementById('panel-backdrop'),
  artifactsList: document.getElementById('artifacts-list'),
  activityList: document.getElementById('agent-activity-list'),
};

function safeStorageGet(storage, key) {
  try {
    return window[storage].getItem(key) || '';
  } catch {
    return '';
  }
}

function safeStorageSet(storage, key, value) {
  try {
    window[storage].setItem(key, value);
  } catch {
    return false;
  }
  return true;
}

function makeSessionId() {
  return `web-${globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(16).slice(2)}`}`;
}

function loadSessionId() {
  return safeStorageGet('sessionStorage', 'hsx-session-id') || makeSessionId();
}

function loadBearerToken() {
  return safeStorageGet('sessionStorage', 'hsx-bearer-token');
}

function persistSessionId() {
  safeStorageSet('sessionStorage', 'hsx-session-id', sessionId);
}

function persistDraft() {
  safeStorageSet('sessionStorage', `hsx-draft-${sessionId}`, elements.prompt.value);
}

function persistAttachments() {
  safeStorageSet(
    'sessionStorage',
    `hsx-attachments-${sessionId}`,
    JSON.stringify(uploadedAttachments.filter((item) => item.status === 'ready')),
  );
}

function apiFetch(url, options = {}) {
  const headers = new Headers(options.headers || {});
  if (bearerToken) headers.set('Authorization', `Bearer ${bearerToken}`);
  return fetch(url, { ...options, headers });
}

function renderMarkdown(text, container) {
  const source = String(text || '');
  if (window.marked && window.DOMPurify) {
    const html = window.marked.parse(source, { breaks: true, gfm: true });
    container.innerHTML = window.DOMPurify.sanitize(html);
    container.querySelectorAll('a[href]').forEach((link) => {
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
    });
  } else {
    container.textContent = source;
  }
}

function scrollToLatest() {
  elements.stream.scrollTo({ top: elements.stream.scrollHeight, behavior: 'smooth' });
}

function setConnection(id, labelId, value, state) {
  const dot = document.getElementById(id);
  const label = document.getElementById(labelId);
  if (dot) dot.dataset.state = state;
  if (label) label.textContent = value;
}

function appendMessage(role, text, jobId = '', options = {}) {
  elements.welcome.hidden = true;
  const fragment = elements.template.content.cloneNode(true);
  const article = fragment.querySelector('.message-card');
  const content = fragment.querySelector('.message-content');
  article.dataset.role = role;
  article.dataset.response = String(text || '');
  if (jobId) article.dataset.jobId = jobId;
  fragment.querySelector('.avatar').textContent = role === 'assistant' ? 'H' : 'Y';
  fragment.querySelector('.message-author').textContent = role === 'assistant' ? 'Hsx' : 'You';
  fragment.querySelector('.message-time').textContent = new Date().toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit',
  });
  renderMarkdown(text, content);

  const copyButton = fragment.querySelector('.copy-button');
  copyButton.textContent = role === 'assistant' ? 'Copy' : 'Copy message';
  copyButton.setAttribute('aria-label', role === 'assistant' ? 'Copy response' : 'Copy message');
  copyButton.addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText(article.dataset.response || '');
      copyButton.textContent = 'Copied';
      window.setTimeout(() => { copyButton.textContent = 'Copy'; }, 1400);
    } catch {
      copyButton.textContent = 'Copy unavailable';
    }
  });

  const feedback = fragment.querySelector('.message-feedback');
  if (role === 'assistant' && jobId && !options.noFeedback) {
    feedback.hidden = false;
    feedback.querySelectorAll('[data-feedback]').forEach((button) => {
      button.addEventListener('click', () => submitFeedback(article, button.dataset.feedback));
    });
  } else {
    feedback.remove();
  }
  elements.stream.appendChild(fragment);
  scrollToLatest();
  return article;
}

function appendActivity(label) {
  const fragment = elements.activityTemplate.content.cloneNode(true);
  const activity = fragment.querySelector('.activity-card');
  activity.lastElementChild.textContent = label;
  elements.stream.appendChild(fragment);
  scrollToLatest();
  return activity;
}

async function submitFeedback(article, feedback) {
  const feedbackRow = article.querySelector('.message-feedback');
  const status = feedbackRow.querySelector('.feedback-status');
  const buttons = [...feedbackRow.querySelectorAll('[data-feedback]')];
  if (!article.dataset.jobId || buttons.some((button) => button.disabled)) return;
  buttons.forEach((button) => {
    button.disabled = true;
    button.setAttribute('aria-pressed', String(button.dataset.feedback === feedback));
  });
  try {
    const response = await apiFetch(apiContract.endpoints.feedback, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ job_id: article.dataset.jobId, feedback }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Could not save feedback.');
    status.textContent = 'Thanks for your feedback.';
  } catch {
    buttons.forEach((button) => {
      button.disabled = false;
      button.removeAttribute('aria-pressed');
    });
    status.textContent = 'Feedback could not be saved.';
  }
}

function parseSseBlock(block) {
  let event = 'message';
  const data = [];
  for (const line of block.split(/\r?\n/)) {
    if (line.startsWith('event:')) event = line.slice(6).trim();
    if (line.startsWith('data:')) data.push(line.slice(5).trimStart());
  }
  if (!data.length) return null;
  try {
    return { event, data: JSON.parse(data.join('\n')) };
  } catch {
    return { event, data: { message: data.join('\n') } };
  }
}

async function readStream(response, onEvent) {
  if (!response.body) throw new Error('The server did not start a chat stream.');
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  while (true) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
    const blocks = buffer.split(/\r?\n\r?\n/);
    buffer = blocks.pop() || '';
    for (const block of blocks) {
      const event = parseSseBlock(block);
      if (event) onEvent(event);
    }
    if (done) break;
  }
  if (buffer.trim()) {
    const event = parseSseBlock(buffer);
    if (event) onEvent(event);
  }
}

function setBusy(isBusy) {
  submitting = isBusy;
  elements.send.hidden = isBusy;
  elements.stop.hidden = !isBusy;
}

function renderAttachments() {
  elements.attachmentList.replaceChildren();
  for (const attachment of uploadedAttachments) {
    const chip = document.createElement('div');
    chip.className = 'attachment-chip';
    const name = document.createElement('span');
    name.textContent = attachment.name;
    const size = document.createElement('small');
    size.textContent = attachment.status === 'uploading'
      ? 'Uploading…'
      : attachment.status === 'error'
        ? 'Failed'
        : attachment.sizeLabel;
    const remove = document.createElement('button');
    remove.type = 'button';
    remove.textContent = '×';
    remove.setAttribute('aria-label', `Remove ${attachment.name}`);
    remove.disabled = attachment.status === 'uploading';
    remove.addEventListener('click', () => {
      uploadedAttachments = uploadedAttachments.filter((item) => item.id !== attachment.id);
      persistAttachments();
      renderAttachments();
    });
    chip.append(name, size, remove);
    elements.attachmentList.appendChild(chip);
  }
}

function formatBytes(bytes) {
  return bytes < 1024 * 1024 ? `${Math.max(1, Math.round(bytes / 1024))} KB` : `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

async function uploadFiles(files) {
  const supported = /\.(txt|md|csv|json|log|py|html|xml|yaml|yml|toml)$/i;
  for (const file of files) {
    const attachment = {
      id: makeSessionId(),
      name: file.name,
      sizeLabel: formatBytes(file.size),
      status: 'uploading',
      path: '',
    };
    if (!supported.test(file.name)) {
      elements.composerStatus.textContent = `${file.name}: use a text, Markdown, CSV, JSON, or code file.`;
      continue;
    }
    if (file.size > 1024 * 1024) {
      elements.composerStatus.textContent = `${file.name}: files must be 1 MB or smaller.`;
      continue;
    }
    uploadedAttachments.push(attachment);
    renderAttachments();
    try {
      const response = await apiFetch('/api/upload', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ filename: file.name, content: await file.text() }),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || 'Upload failed.');
      attachment.name = result.name || file.name;
      attachment.path = result.path || result.name || file.name;
      attachment.status = 'ready';
      elements.composerStatus.textContent = `${attachment.name} is ready to attach.`;
    } catch (error) {
      attachment.status = 'error';
      elements.composerStatus.textContent = error.message || 'The file could not be uploaded.';
    }
    persistAttachments();
    renderAttachments();
  }
}

async function answerPendingQuestion(message) {
  const jobId = pendingQuestion.id;
  const approvalPending = pendingQuestion.state === 'waiting_for_approval';
  if (approvalPending && message.trim().toLowerCase() === 'details') {
    const details = elements.stream.querySelector(`[data-approval-job="${CSS.escape(jobId)}"] details`);
    if (details) details.open = true;
    return;
  }
  const endpoint = apiContract.endpoints.ask_answer.replace('{job_id}', encodeURIComponent(jobId));
  const payload = { answer: message };
  if (approvalPending) payload.approval_id = pendingQuestion.approval_id;
  const response = await apiFetch(endpoint, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || 'Could not submit the answer.');
  if (data.job && ['waiting_for_approval', 'waiting_for_user'].includes(data.job.state)) {
    pendingQuestion = data.job;
    if (data.job.error) appendMessage('assistant', data.job.error, data.job.id);
    return;
  }
  if (approvalPending) {
    elements.stream.querySelector(`[data-approval-job="${CSS.escape(jobId)}"]`)?.remove();
  }
  pendingQuestion = null;
  const activity = appendActivity('Continuing the task…');
  await pollQuestionJob(jobId, activity);
}

function appendApprovalCard(job) {
  const approval = job.approval || {};
  const card = document.createElement('section');
  card.className = 'approval-card';
  card.dataset.approvalJob = job.id;
  card.setAttribute('aria-label', 'Hsx action approval');
  const title = document.createElement('h3');
  title.textContent = 'Hsx wants to:';
  const summary = document.createElement('p');
  summary.textContent = approval.summary || 'Run a tool';
  const why = document.createElement('p');
  why.textContent = `Why: ${approval.why || 'This action needs your approval.'}`;
  const risk = document.createElement('p');
  risk.textContent = `Risk: ${approval.risk || 'UNKNOWN'}${approval.risk === 'DANGEROUS' ? ' (cannot be undone)' : ''}`;
  const details = document.createElement('details');
  const detailsSummary = document.createElement('summary');
  detailsSummary.textContent = 'Details';
  const detailsText = document.createElement('pre');
  detailsText.textContent = approval.details || 'No additional details.';
  details.append(detailsSummary, detailsText);
  const actions = document.createElement('div');
  actions.className = 'approval-actions';
  for (const [label, answer] of [['Yes', 'yes'], ['No', 'no']]) {
    const button = document.createElement('button');
    button.type = 'button';
    button.textContent = label;
    button.addEventListener('click', async () => {
      if (submitting) return;
      setBusy(true);
      try {
        await answerPendingQuestion(answer);
      } catch {
        appendMessage('assistant', 'Could not continue the task. Please try again.');
      } finally {
        setBusy(false);
      }
    });
    actions.appendChild(button);
  }
  card.append(title, summary, why, risk, details, actions);
  elements.welcome.hidden = true;
  elements.stream.appendChild(card);
  scrollToLatest();
}

async function pollQuestionJob(jobId, activity) {
  const endpoint = apiContract.endpoints.ask_job.replace('{job_id}', encodeURIComponent(jobId));
  while (true) {
    await new Promise((resolve) => setTimeout(resolve, 1200));
    const response = await apiFetch(endpoint);
    const data = await response.json();
    if (!response.ok || !data.job) throw new Error(data.error || 'Could not refresh the task.');
    const job = data.job;
    if (job.state === 'waiting_for_user') {
      activity.remove();
      pendingQuestion = job;
      appendMessage('assistant', job.question || 'I need a little more information.', job.id);
      return;
    }
    if (job.state === 'waiting_for_approval') {
      activity.remove();
      pendingQuestion = job;
      appendApprovalCard(job);
      return;
    }
    if (['done', 'failed', 'cancelled'].includes(job.state)) {
      activity.remove();
      pendingQuestion = null;
      appendMessage('assistant', job.result || job.error || 'The task was cancelled.', job.id);
      return;
    }
  }
}

async function submitPrompt() {
  const typedMessage = elements.prompt.value.trim();
  const readyAttachments = uploadedAttachments.filter((item) => item.status === 'ready');
  if ((!typedMessage && !readyAttachments.length) || submitting) return;
  const userMessage = typedMessage || 'Please review the attached file(s).';
  const attachmentContext = readyAttachments.length
    ? `\n\nUploaded workspace files:\n${readyAttachments.map((item) => `- ${item.path}`).join('\n')}`
    : '';
  const requestMessage = `${userMessage}${attachmentContext}`;
  const displayMessage = readyAttachments.length
    ? `${userMessage}\n\nAttached: ${readyAttachments.map((item) => item.name).join(', ')}`
    : userMessage;
  appendMessage('user', displayMessage);
  setBusy(true);
  safeStorageSet('sessionStorage', `hsx-draft-${sessionId}`, typedMessage);
  elements.send.disabled = true;

  if (pendingQuestion) {
    try {
      await answerPendingQuestion(requestMessage);
      elements.prompt.value = '';
      uploadedAttachments = [];
      persistAttachments();
      renderAttachments();
      safeStorageSet('sessionStorage', `hsx-draft-${sessionId}`, '');
    } catch {
      appendMessage('assistant', 'Could not submit your answer. Your text is still available in this tab.');
      elements.prompt.value = typedMessage;
    } finally {
      setBusy(false);
      elements.send.disabled = false;
    }
    return;
  }

  let assistantCard = null;
  let assistantContent = '';
  let currentJob = null;
  let activity;
  try {
    const response = await apiFetch(apiContract.endpoints.chat_stream, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        message: requestMessage,
        session_id: sessionId,
        target_device: 'client_browser',
      }),
    });
    if (!response.ok) {
      const error = await response.json();
      throw new Error(error.error || (response.status === 401 ? 'Set the remote access token to continue.' : 'The request could not be completed.'));
    }
    elements.prompt.value = '';
    activity = appendActivity('Thinking…');
    await readStream(response, ({ event, data }) => {
      if (event === 'text_delta') {
        assistantContent += data.text || '';
        if (!assistantCard) {
          assistantCard = appendMessage('assistant', assistantContent, '', { noFeedback: true });
          activity.remove();
        } else {
          assistantCard.dataset.response = assistantContent;
          renderMarkdown(assistantContent, assistantCard.querySelector('.message-content'));
          scrollToLatest();
        }
      } else if (event === 'job' && data.id) {
        currentJob = data;
        if (data.state === 'waiting_for_user') {
          pendingQuestion = data;
          activity?.remove();
          appendMessage('assistant', data.question || 'I need a little more information.', data.id);
        }
        if (data.state === 'waiting_for_approval') {
          pendingQuestion = data;
          activity?.remove();
          appendApprovalCard(data);
        }
        if (['done', 'failed', 'cancelled'].includes(data.state)) {
          activity?.remove();
          const text = assistantContent || data.result || data.error || 'The task was cancelled.';
          assistantCard?.remove();
          appendMessage('assistant', text, data.id);
        }
      } else if (event === 'error') {
        throw new Error(data.message || 'The task failed.');
      }
    });
    if (!assistantCard && currentJob && currentJob.state === 'running') {
      activity?.remove();
      appendMessage('assistant', 'Task started.', currentJob.id);
    } else if (!assistantCard && currentJob?.state === 'waiting_for_user') {
      pendingQuestion = currentJob;
    } else if (!assistantCard && currentJob?.state === 'waiting_for_approval') {
      pendingQuestion = currentJob;
    }
    uploadedAttachments = [];
    persistAttachments();
    renderAttachments();
    safeStorageSet('sessionStorage', `hsx-draft-${sessionId}`, '');
  } catch (error) {
    activity?.remove();
    appendMessage('assistant', error.message || 'The request could not be completed.', currentJob?.id || '');
    if (!elements.prompt.value) elements.prompt.value = typedMessage;
    persistDraft();
    elements.composerStatus.textContent = 'The request did not complete. Your draft was kept.';
  } finally {
    setBusy(false);
    elements.send.disabled = false;
    elements.prompt.focus();
    refreshArtifacts();
    refreshConnections();
  }
}

function setCameraStatus(connected, message) {
  elements.cameraStatus.textContent = message;
  elements.cameraBadge.classList.toggle('camera-badge-online', connected);
  elements.cameraBadge.classList.toggle('camera-badge-offline', !connected);
  elements.cameraBadge.classList.remove('camera-badge-reconnecting');
  elements.cameraBadge.replaceChildren();
  const dot = document.createElement('span');
  dot.className = 'connection-dot';
  const label = document.createElement('span');
  label.textContent = connected ? (cameraHasFrame ? 'Live' : 'Connecting') : 'Offline';
  elements.cameraBadge.append(dot, label);
  setConnection('camera-dot', 'camera-state', connected ? 'Live' : 'Offline', connected ? 'connected' : 'disconnected');
  elements.camera.hidden = !connected;
  elements.cameraPlaceholder.hidden = connected;
  document.getElementById('camera-clip-time').hidden = !connected || !cameraHasFrame;
}

async function refreshCamera() {
  try {
    const response = await apiFetch(apiContract.endpoints.camera_status);
    const data = await response.json();
    if (response.status === 401) {
      cameraAvailable = false;
      stopCameraStream();
      cameraHasFrame = false;
      if (cameraObjectUrl) {
        URL.revokeObjectURL(cameraObjectUrl);
        cameraObjectUrl = '';
      }
      setCameraStatus(false, 'Set the remote access token to view the host camera.');
      return;
    }
    const connected = response.ok && data.running === true;
    if (connected) {
      cameraAvailable = true;
      setCameraStatus(true, cameraHasFrame ? 'Live from the Hsx host' : 'Connecting to the Hsx host…');
      startCameraStream();
    } else {
      cameraAvailable = false;
      stopCameraStream();
      cameraHasFrame = false;
      elements.camera.removeAttribute('src');
      if (cameraObjectUrl) {
        URL.revokeObjectURL(cameraObjectUrl);
        cameraObjectUrl = '';
      }
      document.getElementById('camera-clip-time').textContent = 'Waiting for video';
      setCameraLatency(null);
      setCameraStatus(false, data.device_available === false
        ? 'No host camera device is available.'
        : 'Host camera capture is not running.');
    }
  } catch {
    if (cameraHasFrame) setCameraReconnecting('Camera status unavailable; reconnecting…');
    else setCameraStatus(false, bearerToken ? 'Camera status is unavailable.' : 'Enter a remote token if the host requires one.');
  }
}

function findBytes(haystack, needle) {
  for (let index = 0; index <= haystack.length - needle.length; index += 1) {
    if (needle.every((byte, offset) => haystack[index + offset] === byte)) return index;
  }
  return -1;
}

function concatenateBytes(first, second) {
  const result = new Uint8Array(first.length + second.length);
  result.set(first);
  result.set(second, first.length);
  return result;
}

function takeCameraFrame(buffer) {
  const headerEnd = findBytes(buffer, [13, 10, 13, 10]);
  if (headerEnd < 0) {
    if (buffer.length > 16_384) throw new Error('Invalid camera stream header.');
    return null;
  }
  const headers = new TextDecoder().decode(buffer.slice(0, headerEnd));
  const length = Number(headers.match(/(?:^|\r\n)Content-Length:\s*(\d+)/i)?.[1]);
  const capturedAt = Number(headers.match(/(?:^|\r\n)X-Hsx-Captured-At:\s*([\d.]+)/i)?.[1]);
  if (!Number.isInteger(length) || length < 4 || length > 2 * 1024 * 1024) {
    throw new Error('Invalid camera frame size.');
  }
  const payloadStart = headerEnd + 4;
  if (buffer.length < payloadStart + length) return null;
  return {
    data: buffer.slice(payloadStart, payloadStart + length),
    capturedAt: Number.isFinite(capturedAt) && capturedAt > 0 ? capturedAt : Date.now() / 1000,
    remaining: buffer.slice(payloadStart + length),
  };
}

function setCameraLatency(capturedAt) {
  const output = document.getElementById('camera-latency');
  if (!Number.isFinite(capturedAt)) {
    output.textContent = cameraHasFrame ? 'Waiting for frames' : 'Waiting';
    output.dataset.state = 'warning';
    return;
  }
  const delayMs = Date.now() + cameraClockOffsetMs - capturedAt * 1000;
  if (delayMs < -1000) {
    output.textContent = 'Clock is ahead';
    output.dataset.state = 'warning';
    return;
  }
  const delaySeconds = Math.max(0, delayMs) / 1000;
  output.textContent = `${delaySeconds.toFixed(1)} s behind`;
  output.dataset.state = delaySeconds > 2 ? 'warning' : 'connected';
}

function showCameraFrame(frame) {
  const nextUrl = URL.createObjectURL(new Blob([frame.data], { type: 'image/jpeg' }));
  const previousUrl = cameraObjectUrl;
  cameraObjectUrl = nextUrl;
  elements.camera.onerror = () => {
    if (cameraObjectUrl === nextUrl) {
      URL.revokeObjectURL(nextUrl);
      cameraObjectUrl = '';
    }
    setCameraReconnecting('Camera frame decode failed; resyncing…');
  };
  elements.camera.src = nextUrl;
  if (previousUrl) URL.revokeObjectURL(previousUrl);
  cameraHasFrame = true;
  cameraRetryDelay = 500;
  const captured = new Date(frame.capturedAt * 1000);
  document.getElementById('camera-clip-time').textContent =
    `Clip ${captured.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}`;
  document.getElementById('camera-clip-time').dataset.capturedAt = String(frame.capturedAt);
  document.getElementById('camera-clip-time').hidden = false;
  setCameraLatency(frame.capturedAt);
  setCameraStatus(true, 'Live from the Hsx host');
}

function setCameraReconnecting(message) {
  elements.cameraStatus.textContent = message;
  elements.cameraBadge.classList.remove('camera-badge-online', 'camera-badge-offline');
  elements.cameraBadge.classList.add('camera-badge-reconnecting');
  elements.cameraBadge.replaceChildren();
  const dot = document.createElement('span');
  dot.className = 'connection-dot';
  const label = document.createElement('span');
  label.textContent = 'Reconnecting';
  elements.cameraBadge.append(dot, label);
  setConnection('camera-dot', 'camera-state', 'Reconnecting', 'warning');
  elements.camera.hidden = !cameraHasFrame;
  elements.cameraPlaceholder.hidden = cameraHasFrame;
  if (!cameraHasFrame) setCameraLatency(null);
}

function stopCameraStream() {
  if (cameraReconnectTimer) {
    window.clearTimeout(cameraReconnectTimer);
    cameraReconnectTimer = null;
  }
  if (cameraStreamController) {
    cameraStreamController.abort();
    cameraStreamController = null;
  }
}

function scheduleCameraReconnect() {
  if (!cameraAvailable || document.hidden || !navigator.onLine || cameraReconnectTimer) return;
  setCameraReconnecting('Video paused; reconnecting to the latest frame…');
  cameraReconnectTimer = window.setTimeout(() => {
    cameraReconnectTimer = null;
    startCameraStream();
  }, cameraRetryDelay);
  cameraRetryDelay = Math.min(cameraRetryDelay * 2, 8000);
}

async function startCameraStream() {
  if (!cameraAvailable || document.hidden || !navigator.onLine || cameraStreamController || cameraReconnectTimer) return;
  const controller = new AbortController();
  cameraStreamController = controller;
  cameraStreamStartedAt = Date.now();
  try {
    const requestStartedAt = Date.now();
    const response = await apiFetch(apiContract.endpoints.camera_stream, { signal: controller.signal });
    if (!response.ok || !response.body) throw new Error('Camera stream unavailable.');
    const serverTime = Number(response.headers.get('X-Hsx-Server-Time'));
    if (Number.isFinite(serverTime) && serverTime > 0) {
      cameraClockOffsetMs = serverTime * 1000 - ((requestStartedAt + Date.now()) / 2);
    }
    const reader = response.body.getReader();
    let buffer = new Uint8Array();
    while (!controller.signal.aborted) {
      const { value, done } = await reader.read();
      if (done) throw new Error('Camera stream ended.');
      buffer = concatenateBytes(buffer, value);
      while (true) {
        const frame = takeCameraFrame(buffer);
        if (!frame) break;
        buffer = frame.remaining;
        showCameraFrame(frame);
      }
      if (buffer.length > 2 * 1024 * 1024 + 16_384) {
        throw new Error('Camera stream is out of sync.');
      }
    }
  } catch {
    if (!controller.signal.aborted) scheduleCameraReconnect();
  } finally {
    if (cameraStreamController === controller) cameraStreamController = null;
    if (!controller.signal.aborted && cameraAvailable) scheduleCameraReconnect();
  }
}

function updateCameraClock() {
  document.getElementById('camera-clock').textContent = new Date().toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
  if (cameraHasFrame) {
    const clip = Number(document.getElementById('camera-clip-time').dataset.capturedAt);
    if (Number.isFinite(clip)) setCameraLatency(clip);
  }
  const currentDelayMs = cameraHasFrame
    ? Date.now() + cameraClockOffsetMs - Number(document.getElementById('camera-clip-time').dataset.capturedAt) * 1000
    : Date.now() - cameraStreamStartedAt;
  if (
    cameraAvailable
    && cameraStreamController
    && !document.hidden
    && currentDelayMs > 5000
  ) {
    cameraStreamController.abort();
    cameraStreamController = null;
    scheduleCameraReconnect();
  }
}

function tailnetStatus() {
  const hostname = window.location.hostname.toLowerCase();
  if (hostname.endsWith('.ts.net')) return { label: 'Active', state: 'connected' };
  if (hostname === 'localhost' || hostname === '127.0.0.1' || hostname === '::1') {
    return { label: 'Local access', state: 'warning' };
  }
  return { label: 'Not in use', state: 'warning' };
}

async function refreshConnections() {
  const tailnet = tailnetStatus();
  setConnection('tailnet-dot', 'tailnet-state', tailnet.label, tailnet.state);
  const online = navigator.onLine;
  setConnection('internet-dot', 'internet-state', online ? 'Online' : 'Offline', online ? 'connected' : 'disconnected');
  try {
    const [healthResponse, telemetryResponse] = await Promise.all([
      apiFetch(apiContract.endpoints.health),
      apiFetch(apiContract.endpoints.telemetry),
    ]);
    if (healthResponse.status === 401) {
      setConnection('api-dot', 'api-state', 'Token needed', 'warning');
      elements.connection.textContent = 'Token needed';
      setConnection('llm-dot', 'llm-state', 'Locked', 'warning');
      document.getElementById('connection-summary').textContent = 'Enter token to connect';
      await refreshCamera();
      return;
    }
    setConnection('api-dot', 'api-state', healthResponse.ok ? 'Online' : 'Offline', healthResponse.ok ? 'connected' : 'disconnected');
    elements.connection.replaceChildren();
    const connectionDot = document.createElement('span');
    connectionDot.className = 'connection-dot';
    connectionDot.dataset.state = healthResponse.ok ? 'connected' : 'disconnected';
    elements.connection.append(connectionDot, document.createTextNode(healthResponse.ok ? ' Connected' : ' Offline'));
    if (telemetryResponse.ok) {
      const telemetry = await telemetryResponse.json();
      const provider = String(telemetry.provider || 'not connected');
      const available = !['not connected', ''].includes(provider.toLowerCase());
      setConnection('llm-dot', 'llm-state', available ? 'Used' : 'Standby', available ? 'connected' : 'warning');
    } else {
      setConnection('llm-dot', 'llm-state', 'Unavailable', 'disconnected');
    }
    const healthy = healthResponse.ok;
    document.getElementById('connection-summary').textContent = healthy ? 'Host reachable' : 'Offline';
  } catch {
    setConnection('api-dot', 'api-state', 'Offline', 'disconnected');
    setConnection('llm-dot', 'llm-state', 'Unavailable', 'disconnected');
    elements.connection.replaceChildren();
    const connectionDot = document.createElement('span');
    connectionDot.className = 'connection-dot';
    connectionDot.dataset.state = 'disconnected';
    elements.connection.append(connectionDot, document.createTextNode(' Offline'));
    document.getElementById('connection-summary').textContent = 'Offline';
  }
  await refreshCamera();
}

function closeMobilePanels() {
  elements.sidebar.classList.remove('is-open');
  elements.cameraSidebar.classList.remove('is-open');
  elements.panelBackdrop.hidden = true;
  document.getElementById('sidebar-toggle').setAttribute('aria-expanded', 'false');
  document.getElementById('camera-toggle').setAttribute('aria-expanded', 'false');
}

function openMobilePanel(panel) {
  closeMobilePanels();
  panel.classList.add('is-open');
  elements.panelBackdrop.hidden = false;
  const isSidebar = panel === elements.sidebar;
  document.getElementById(isSidebar ? 'sidebar-toggle' : 'camera-toggle')
    .setAttribute('aria-expanded', 'true');
}

async function refreshArtifacts() {
  const empty = document.createElement('p');
  empty.className = 'artifacts-empty';
  empty.textContent = 'Loading generated files…';
  elements.artifactsList.replaceChildren(empty);
  try {
    const response = await apiFetch(apiContract.endpoints.outputs);
    if (!response.ok) throw new Error('Could not load generated files.');
    const data = await response.json();
    const files = Array.isArray(data.files) ? data.files : [];
    if (!files.length) {
      empty.textContent = 'No generated files yet. Files created by Hsx will appear here.';
      return;
    }
    const fragment = document.createDocumentFragment();
    for (const file of files) {
      if (typeof file.path !== 'string' || typeof file.name !== 'string') continue;
      const item = document.createElement('div');
      item.className = 'artifact-item';
      const links = document.createElement('a');
      const query = new URLSearchParams({ path: file.path });
      links.href = `/api/files/download?${query}`;
      links.setAttribute('aria-label', `Download ${file.name}`);
      const name = document.createElement('span');
      name.className = 'artifact-name';
      name.textContent = file.name;
      const meta = document.createElement('span');
      meta.className = 'artifact-meta';
      meta.textContent = file.path;
      links.append(name, meta);
      const type = document.createElement('span');
      type.className = 'artifact-type';
      type.textContent = String(file.type || 'file');
      item.append(links, type);
      fragment.append(item);
    }
    if (!fragment.childNodes.length) {
      empty.textContent = 'No generated files yet. Files created by Hsx will appear here.';
      return;
    }
    elements.artifactsList.replaceChildren(fragment);
  } catch {
    empty.textContent = 'Generated files are unavailable right now. Tap refresh to try again.';
  }
}

async function refreshAgentActivity() {
  const empty = document.createElement('p');
  empty.className = 'artifacts-empty';
  empty.textContent = 'Checking for agent tasks…';
  try {
    const query = new URLSearchParams({ session_id: sessionId });
    const response = await apiFetch(`${apiContract.endpoints.activity}?${query}`);
    if (!response.ok) throw new Error('Could not load agent activity.');
    const data = await response.json();
    const tasks = Array.isArray(data.tasks) ? data.tasks : [];
    document.getElementById('activity-count').textContent =
      `${tasks.filter((task) => ['queued', 'running', 'waiting_for_user', 'waiting_for_approval'].includes(task.state)).length} active`;
    if (!tasks.length) {
      empty.textContent = 'No agent tasks in this session yet.';
      elements.activityList.replaceChildren(empty);
      return;
    }
    const fragment = document.createDocumentFragment();
    for (const task of tasks) {
      const item = document.createElement('div');
      item.className = 'activity-item';
      const summary = document.createElement('span');
      summary.className = 'activity-summary';
      summary.textContent = String(task.summary || 'Agent task');
      const meta = document.createElement('span');
      meta.className = 'activity-meta';
      const state = document.createElement('span');
      state.className = 'activity-state';
      state.dataset.state = String(task.state || 'unknown');
      state.textContent = String(task.state || 'unknown').replaceAll('_', ' ');
      meta.append(state);
      item.append(summary, meta);
      fragment.append(item);
    }
    elements.activityList.replaceChildren(fragment);
  } catch {
    document.getElementById('activity-count').textContent = 'Unavailable';
    empty.textContent = 'Agent activity is unavailable right now.';
    elements.activityList.replaceChildren(empty);
  }
}

async function retryCamera() {
  const button = document.getElementById('camera-retry');
  button.disabled = true;
  stopCameraStream();
  cameraHasFrame = false;
  cameraRetryDelay = 500;
  elements.camera.removeAttribute('src');
  if (cameraObjectUrl) {
    URL.revokeObjectURL(cameraObjectUrl);
    cameraObjectUrl = '';
  }
  document.getElementById('camera-clip-time').textContent = 'Waiting for video';
  document.getElementById('camera-clip-time').hidden = true;
  setCameraLatency(null);
  setCameraStatus(false, 'Refreshing host camera…');
  try {
    await refreshCamera();
  } finally {
    window.setTimeout(() => {
      button.disabled = false;
    }, 750);
  }
}

async function stopTask() {
  try {
    await apiFetch(apiContract.endpoints.stop, { method: 'POST' });
  } catch {
    elements.composerStatus.textContent = 'The stop request could not reach Hsx.';
  }
}

function resetConversation() {
  sessionId = makeSessionId();
  persistSessionId();
  pendingQuestion = null;
  uploadedAttachments = [];
  elements.stream.querySelectorAll('.message-card, .approval-card, .activity-card').forEach((item) => item.remove());
  elements.welcome.hidden = false;
  elements.prompt.value = '';
  renderAttachments();
  persistDraft();
  elements.prompt.focus();
}

function setupTokenControls() {
  const tokenButton = document.getElementById('access-token-button');
  tokenButton.addEventListener('click', () => {
    elements.tokenForm.hidden = !elements.tokenForm.hidden;
    if (!elements.tokenForm.hidden) elements.tokenInput.focus();
  });
  elements.tokenForm.addEventListener('submit', (event) => {
    event.preventDefault();
    bearerToken = elements.tokenInput.value.trim();
    if (bearerToken && !safeStorageSet('sessionStorage', 'hsx-bearer-token', bearerToken)) {
      elements.composerStatus.textContent = 'Token is active for this page only; session storage is unavailable.';
    } else if (!bearerToken) {
      try { window.sessionStorage.removeItem('hsx-bearer-token'); } catch { /* Storage is optional. */ }
    }
    elements.tokenInput.value = '';
    elements.tokenForm.hidden = true;
    refreshConnections();
  });
  document.getElementById('clear-token-button').addEventListener('click', () => {
    bearerToken = '';
    try { window.sessionStorage.removeItem('hsx-bearer-token'); } catch { /* Storage is optional. */ }
    elements.tokenInput.value = '';
    elements.tokenForm.hidden = true;
    refreshConnections();
  });
}

async function initialize() {
  const [tokensResponse, contractResponse] = await Promise.all([
    fetch('/web/design-tokens.json'),
    fetch('/web/api-contract.json'),
  ]);
  if (!tokensResponse.ok || !contractResponse.ok) throw new Error('Could not load the Hsx interface contract.');
  apiContract = await contractResponse.json();
  document.getElementById('sidebar-toggle').addEventListener('click', () => {
    if (elements.sidebar.classList.contains('is-open')) closeMobilePanels();
    else openMobilePanel(elements.sidebar);
  });
  document.getElementById('camera-toggle').addEventListener('click', () => {
    if (elements.cameraSidebar.classList.contains('is-open')) {
      closeMobilePanels();
    } else {
      openMobilePanel(elements.cameraSidebar);
      refreshArtifacts();
      refreshCamera();
    }
  });
  document.getElementById('camera-close').addEventListener('click', closeMobilePanels);
  elements.panelBackdrop.addEventListener('click', closeMobilePanels);
  document.getElementById('refresh-artifacts').addEventListener('click', refreshArtifacts);
  document.getElementById('camera-retry').addEventListener('click', retryCamera);
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') closeMobilePanels();
  });
  persistSessionId();
  setupTokenControls();
  const draft = safeStorageGet('sessionStorage', `hsx-draft-${sessionId}`);
  const storedAttachments = safeStorageGet('sessionStorage', `hsx-attachments-${sessionId}`);
  if (storedAttachments) {
    try {
      uploadedAttachments = JSON.parse(storedAttachments).filter(
        (item) => item && item.status === 'ready' && typeof item.path === 'string',
      );
      renderAttachments();
    } catch {
      uploadedAttachments = [];
    }
  }
  if (draft) elements.prompt.value = draft;

  const pendingResponse = await apiFetch(
    `${apiContract.endpoints.ask_pending}?session_id=${encodeURIComponent(sessionId)}`,
  );
  if (pendingResponse.ok) {
    const pendingData = await pendingResponse.json();
    if (pendingData.job?.state === 'waiting_for_approval') {
      pendingQuestion = pendingData.job;
      appendApprovalCard(pendingQuestion);
    } else if (pendingData.job?.state === 'waiting_for_user') {
      pendingQuestion = pendingData.job;
      appendMessage('assistant', pendingQuestion.question || 'I need a little more information.', pendingQuestion.id);
    }
  }
  elements.composer.addEventListener('submit', (event) => {
    event.preventDefault();
    submitPrompt();
  });
  elements.prompt.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      elements.composer.requestSubmit();
    }
  });
  elements.prompt.addEventListener('input', () => {
    elements.prompt.style.height = 'auto';
    elements.prompt.style.height = `${Math.min(elements.prompt.scrollHeight, 180)}px`;
    persistDraft();
  });
  elements.stop.addEventListener('click', stopTask);
  document.getElementById('attach-button').addEventListener('click', () => elements.fileInput.click());
  document.getElementById('browse-files-button').addEventListener('click', () => elements.fileInput.click());
  elements.fileInput.addEventListener('change', async () => {
    await uploadFiles([...elements.fileInput.files]);
    elements.fileInput.value = '';
  });
  document.querySelectorAll('[data-prompt]').forEach((button) => {
    button.addEventListener('click', () => {
      elements.prompt.value = button.dataset.prompt;
      elements.prompt.focus();
      persistDraft();
    });
  });
  document.getElementById('new-chat-button').addEventListener('click', resetConversation);

  for (const eventName of ['dragenter', 'dragover']) {
    elements.composer.addEventListener(eventName, (event) => {
      event.preventDefault();
      elements.dropHint.hidden = false;
    });
  }
  for (const eventName of ['dragleave', 'drop']) {
    elements.composer.addEventListener(eventName, (event) => {
      event.preventDefault();
      elements.dropHint.hidden = true;
      if (eventName === 'drop' && event.dataTransfer?.files.length) uploadFiles([...event.dataTransfer.files]);
    });
  }
  window.addEventListener('online', refreshConnections);
  window.addEventListener('offline', () => {
    stopCameraStream();
    if (cameraAvailable) setCameraReconnecting('Network offline; waiting to reconnect…');
    refreshConnections();
  });
  window.addEventListener('pagehide', stopCameraStream);
  window.addEventListener('pageshow', () => {
    if (!document.hidden) refreshConnections();
  });
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) {
      stopCameraStream();
    } else {
      cameraRetryDelay = 500;
      refreshConnections();
    }
  });
  await refreshConnections();
  refreshArtifacts();
  refreshAgentActivity();
  window.setInterval(refreshConnections, 15000);
  window.setInterval(refreshAgentActivity, 5000);
  updateCameraClock();
  window.setInterval(updateCameraClock, 1000);
}

initialize().catch(() => {
  elements.connection.replaceChildren();
  const connectionDot = document.createElement('span');
  connectionDot.className = 'connection-dot';
  connectionDot.dataset.state = 'disconnected';
  elements.connection.append(connectionDot, document.createTextNode(' Offline'));
  document.getElementById('connection-summary').textContent = 'Offline';
});
