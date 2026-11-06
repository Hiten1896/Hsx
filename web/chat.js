let sessionId = loadSessionId();
let apiContract;
let pendingQuestion = null;
let submitting = false;
let activeChatController = null;
let notificationController = null;
let notificationLastId = 0;
let notificationInitialized = false;
let notificationReconnectTimer = null;
let notificationRetryDelay = 500;
let notificationToastTimer = null;
let voiceRecognition = null;
let voiceIsListening = false;
let voiceDraft = '';
let voiceStatusMessage = 'Files up to 1 MB each';
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
  greeting: document.getElementById('greeting-title'),
  conversationLayout: document.getElementById('conversation-layout'),
  composerDock: document.querySelector('.composer-dock'),
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
  notificationPanel: document.getElementById('notifications-panel'),
  notificationsList: document.getElementById('notifications-list'),
  voiceButton: document.getElementById('voice-button'),
  voiceIndicator: document.getElementById('voice-indicator'),
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

function updateGreeting() {
  const hour = new Date().getHours();
  const greeting = hour < 12 ? 'Good morning' : hour < 18 ? 'Good afternoon' : 'Good evening';
  elements.greeting.textContent = `${greeting}, Sir`;
}

async function refreshGreeting() {
  updateGreeting();
  const hour = new Date().getHours();
  const timeOfDay = hour < 12 ? 'morning' : hour < 18 ? 'afternoon' : 'evening';
  try {
    const response = await apiFetch(apiContract.endpoints.greeting, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ time_of_day: timeOfDay }),
    });
    if (!response.ok) return;
    const result = await response.json();
    if (typeof result.greeting === 'string' && result.greeting.trim()) {
      elements.greeting.textContent = result.greeting.trim();
    }
  } catch {
    // Keep the local-time greeting when the cloud service is unavailable.
  }
}

function setConversationEmpty(isEmpty) {
  const wasEmpty = elements.conversationLayout.classList.contains('is-empty');
  if (wasEmpty === isEmpty) return;
  const before = elements.composerDock.getBoundingClientRect();
  elements.conversationLayout.classList.toggle('is-empty', isEmpty);
  elements.conversationLayout.classList.toggle('is-active', !isEmpty);
  const after = elements.composerDock.getBoundingClientRect();
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches || !elements.composerDock.animate) return;
  const offsetX = before.left - after.left;
  const offsetY = before.top - after.top;
  elements.composerDock.animate(
    [{ transform: `translate(${offsetX}px, ${offsetY}px)` }, { transform: 'translate(0, 0)' }],
    { duration: 360, easing: 'cubic-bezier(.2,.8,.2,1)' },
  );
}

function hasComposerContent() {
  return Boolean(elements.prompt.value.trim() || uploadedAttachments.some((item) => item.status === 'ready'));
}

function updateSendButton() {
  elements.send.hidden = submitting || !hasComposerContent();
}

function setConnection(id, labelId, value, state) {
  const dot = document.getElementById(id);
  const label = document.getElementById(labelId);
  if (dot) dot.dataset.state = state;
  if (label) label.textContent = value;
}

function relativeMessageTime(timestamp) {
  const elapsedSeconds = Math.round((timestamp - Date.now()) / 1000);
  if (Math.abs(elapsedSeconds) < 60) return 'now';
  const elapsedMinutes = Math.round(elapsedSeconds / 60);
  if (Math.abs(elapsedMinutes) < 60) {
    return new Intl.RelativeTimeFormat(undefined, { numeric: 'auto' }).format(elapsedMinutes, 'minute');
  }
  const elapsedHours = Math.round(elapsedMinutes / 60);
  if (Math.abs(elapsedHours) < 24) {
    return new Intl.RelativeTimeFormat(undefined, { numeric: 'auto' }).format(elapsedHours, 'hour');
  }
  return new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric' }).format(timestamp);
}

function updateMessageTimes() {
  document.querySelectorAll('.message-time[data-timestamp]').forEach((time) => {
    const timestamp = new Date(time.dataset.timestamp);
    if (!Number.isNaN(timestamp.getTime())) time.textContent = relativeMessageTime(timestamp);
  });
}

function appendMessage(role, text, jobId = '', options = {}) {
  elements.welcome.hidden = true;
  setConversationEmpty(false);
  const fragment = elements.template.content.cloneNode(true);
  const article = fragment.querySelector('.message-card');
  const content = fragment.querySelector('.message-content');
  article.dataset.role = role;
  article.dataset.response = String(text || '');
  article.dataset.editText = String(options.editText ?? text ?? '');
  article.dataset.prompt = String(options.prompt || '');
  const createdAt = new Date();
  article.dataset.createdAt = createdAt.toISOString();
  if (jobId) article.dataset.jobId = jobId;
  renderMarkdown(text, content);

  const copyButton = fragment.querySelector('.copy-button');
  if (role === 'assistant') {
    fragment.querySelector('.edit-button').remove();
    const timestamp = fragment.querySelector('.message-time');
    timestamp.dateTime = createdAt.toISOString();
    timestamp.dataset.timestamp = createdAt.toISOString();
    timestamp.textContent = relativeMessageTime(createdAt);
    timestamp.title = createdAt.toLocaleString();
    fragment.querySelector('.message-feedback').hidden = Boolean(options.noFeedback);
    fragment.querySelector('.retry-button').disabled = !article.dataset.prompt;
  } else {
    fragment.querySelector('.speak-button').remove();
    fragment.querySelector('.message-feedback').remove();
    fragment.querySelector('.retry-button').remove();
    fragment.querySelector('.message-time').remove();
  }
  copyButton.addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText(article.dataset.response || '');
      copyButton.title = 'Copied';
      window.setTimeout(() => { copyButton.title = role === 'assistant' ? 'Copy response' : 'Copy message'; }, 1400);
    } catch {
      copyButton.title = 'Copy unavailable';
    }
  });

  const editButton = fragment.querySelector('.edit-button');
  editButton?.addEventListener('click', () => {
    elements.prompt.value = article.dataset.editText || '';
    elements.prompt.dispatchEvent(new Event('input', { bubbles: true }));
    elements.prompt.focus();
  });
  const speakButton = fragment.querySelector('.speak-button');
  speakButton?.addEventListener('click', () => {
    if (!('speechSynthesis' in window) || !('SpeechSynthesisUtterance' in window)) {
      speakButton.title = 'Read-aloud is not available in this browser';
      return;
    }
    window.speechSynthesis.cancel();
    const utterance = new SpeechSynthesisUtterance(article.dataset.response || '');
    utterance.onend = () => speakButton.removeAttribute('aria-pressed');
    utterance.onerror = () => speakButton.removeAttribute('aria-pressed');
    speakButton.setAttribute('aria-pressed', 'true');
    window.speechSynthesis.speak(utterance);
  });
  const retryButton = fragment.querySelector('.retry-button');
  retryButton?.addEventListener('click', () => {
    if (!article.dataset.prompt || submitting) return;
    elements.prompt.value = article.dataset.prompt;
    elements.prompt.dispatchEvent(new Event('input', { bubbles: true }));
    elements.composer.requestSubmit();
  });

  const feedback = fragment.querySelector('.message-feedback');
  if (role === 'assistant' && !options.noFeedback) {
    feedback.hidden = false;
    feedback.querySelectorAll('[data-feedback]').forEach((button) => {
      button.disabled = !jobId;
      if (jobId) {
        button.addEventListener('click', () => submitFeedback(article, button.dataset.feedback));
      } else {
        button.title = 'Feedback is unavailable for this message';
      }
    });
  } else {
    feedback?.remove();
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
  let receivedDone = false;
  while (true) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
    const blocks = buffer.split(/\r?\n\r?\n/);
    buffer = blocks.pop() || '';
    for (const block of blocks) {
      const event = parseSseBlock(block);
      if (event) {
        if (event.event === 'done') receivedDone = true;
        onEvent(event);
      }
    }
    if (done) break;
  }
  if (buffer.trim()) {
    const event = parseSseBlock(buffer);
    if (event) {
      if (event.event === 'done') receivedDone = true;
      onEvent(event);
    }
  }
  if (!receivedDone) throw new Error('The connection ended before Hsx finished. Your draft was kept.');
}

function setBusy(isBusy) {
  submitting = isBusy;
  updateSendButton();
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
  updateSendButton();
}

function formatBytes(bytes) {
  return bytes < 1024 * 1024 ? `${Math.max(1, Math.round(bytes / 1024))} KB` : `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

async function uploadFiles(files) {
  for (const file of files) {
    const attachment = {
      id: makeSessionId(),
      name: file.name,
      sizeLabel: formatBytes(file.size),
      status: 'uploading',
      path: '',
    };
    if (file.size > 1024 * 1024) {
      elements.composerStatus.textContent = `${file.name}: files must be 1 MB or smaller.`;
      continue;
    }
    uploadedAttachments.push(attachment);
    renderAttachments();
    try {
      const bytes = new Uint8Array(await file.arrayBuffer());
      let binary = '';
      for (let offset = 0; offset < bytes.length; offset += 0x8000) {
        binary += String.fromCharCode(...bytes.subarray(offset, offset + 0x8000));
      }
      const response = await apiFetch('/api/upload', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ filename: file.name, content_base64: btoa(binary) }),
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

function stopVoiceInput() {
  if (voiceRecognition && voiceIsListening) voiceRecognition.stop();
}

function setupVoiceInput() {
  const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!Recognition || !window.isSecureContext) {
    elements.voiceButton.hidden = true;
    return;
  }
  elements.voiceButton.addEventListener('click', () => {
    if (voiceIsListening) {
      stopVoiceInput();
      return;
    }
    const recognition = new Recognition();
    recognition.continuous = true;
    recognition.interimResults = true;
    recognition.lang = navigator.language || 'en-US';
    voiceDraft = elements.prompt.value;
    voiceFinalText = '';
    recognition.onstart = () => {
      voiceIsListening = true;
      voiceStatusMessage = 'Files up to 1 MB each';
      elements.voiceButton.setAttribute('aria-label', 'Stop voice input');
      elements.voiceButton.title = 'Stop voice input';
      elements.voiceIndicator.hidden = false;
      elements.composerStatus.textContent = 'Listening…';
    };
    recognition.onresult = (event) => {
      const finalParts = [];
      const interimParts = [];
      for (let index = 0; index < event.results.length; index += 1) {
        const result = event.results[index];
        (result.isFinal ? finalParts : interimParts).push(result[0].transcript);
      }
      const finalText = finalParts.join(' ').trim();
      const separator = voiceDraft && finalText && !/\s$/.test(voiceDraft) ? ' ' : '';
      elements.prompt.value = `${voiceDraft}${separator}${finalText}`;
      elements.prompt.style.height = 'auto';
      elements.prompt.style.height = `${Math.min(elements.prompt.scrollHeight, 180)}px`;
      voiceStatusMessage = 'Voice text added. Review it before sending.';
      elements.composerStatus.textContent = interimParts.length
        ? `Listening… ${interimParts.join(' ')}`
        : voiceStatusMessage;
      persistDraft();
      updateSendButton();
    };
    recognition.onerror = (event) => {
      const messages = {
        'not-allowed': 'Microphone permission was denied.',
        'service-not-allowed': 'Voice input is blocked by the browser.',
        'no-speech': 'No speech was detected.',
        'audio-capture': 'No microphone is available.',
        network: 'Voice input is unavailable because of a network error.',
      };
      voiceStatusMessage = messages[event.error] || 'Voice input could not start.';
      elements.composerStatus.textContent = voiceStatusMessage;
    };
    recognition.onend = () => {
      voiceIsListening = false;
      elements.voiceButton.setAttribute('aria-label', 'Start voice input');
      elements.voiceButton.title = 'Dictate a message; Chrome and Edge send audio to their speech service.';
      elements.voiceIndicator.hidden = true;
      elements.composerStatus.textContent = voiceStatusMessage;
      persistDraft();
      updateSendButton();
      voiceRecognition = null;
    };
    voiceRecognition = recognition;
    voiceIsListening = true;
    try {
      recognition.start();
    } catch (error) {
      voiceRecognition = null;
      voiceIsListening = false;
      voiceStatusMessage = error.message || 'Voice input could not start.';
      elements.composerStatus.textContent = voiceStatusMessage;
    }
  });
}

function setupComposerOptions() {
  const button = document.getElementById('composer-options-button');
  const menu = document.getElementById('composer-options-menu');
  const closeMenu = () => {
    menu.hidden = true;
    button.setAttribute('aria-expanded', 'false');
  };
  button.addEventListener('click', () => {
    menu.hidden = !menu.hidden;
    button.setAttribute('aria-expanded', String(!menu.hidden));
  });
  menu.addEventListener('click', (event) => {
    const action = event.target.closest('[data-composer-action]')?.dataset.composerAction;
    if (action === 'attach') elements.fileInput.click();
    if (action === 'camera') document.getElementById('camera-toggle').click();
    closeMenu();
  });
  document.addEventListener('click', (event) => {
    if (!event.target.closest('.composer-options')) closeMenu();
  });
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
  elements.welcome.hidden = true;
  setConversationEmpty(false);
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
  appendMessage('user', displayMessage, '', { editText: userMessage });
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

  const controller = new AbortController();
  activeChatController = controller;
  let assistantCard = null;
  let assistantContent = '';
  let currentJob = null;
  let activity;
  try {
    const response = await apiFetch(apiContract.endpoints.chat_stream, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      signal: controller.signal,
      body: JSON.stringify({
        message: requestMessage,
        session_id: sessionId,
        target_device: 'local_host',
      }),
    });
    if (!response.ok) {
      const error = await response.json();
      throw new Error(error.error || (response.status === 401 ? 'Set the remote access token to continue.' : 'The request could not be completed.'));
    }
    uploadedAttachments = [];
    persistAttachments();
    renderAttachments();
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
          appendMessage('assistant', text, data.id, { prompt: requestMessage });
        }
      } else if (event === 'error') {
        throw new Error(data.message || 'The task failed.');
      }
    });
    if (!assistantCard && currentJob && currentJob.state === 'running') {
      activity?.remove();
      appendMessage('assistant', 'Task started.', currentJob.id, { prompt: requestMessage });
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
    if (controller.signal.aborted) {
      appendMessage('assistant', 'Task stopped. Your draft was kept.');
      elements.composerStatus.textContent = 'Task stopped. Your draft was kept.';
    } else {
      appendMessage('assistant', error.message || 'The request could not be completed.', currentJob?.id || '');
      elements.composerStatus.textContent = 'The request did not complete. Your draft was kept.';
    }
    if (!elements.prompt.value) elements.prompt.value = typedMessage;
    persistDraft();
  } finally {
    if (activeChatController === controller) {
      activeChatController = null;
      setBusy(false);
      elements.send.disabled = false;
      elements.prompt.focus();
    }
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

async function refreshNotifications() {
  const badge = document.getElementById('notification-count');
  try {
    const response = await apiFetch(apiContract.endpoints.notifications);
    if (!response.ok) throw new Error('Notifications are unavailable.');
    const data = await response.json();
    const notifications = Array.isArray(data.notifications) ? data.notifications : [];
    const unread = Number(data.unread) || 0;
    badge.textContent = unread > 99 ? '99+' : String(unread);
    badge.hidden = unread === 0;
    if (!notifications.length) {
      const empty = document.createElement('p');
      empty.className = 'artifacts-empty';
      empty.textContent = 'No notifications yet.';
      elements.notificationsList.replaceChildren(empty);
      return 0;
    }
    const fragment = document.createDocumentFragment();
    for (const notification of notifications) {
      const item = document.createElement('button');
      item.type = 'button';
      item.className = 'notification-item';
      item.dataset.unread = String(!notification.read);
      item.dataset.notificationId = String(notification.id);
      item.dataset.jobId = String(notification.job_id || '');
      const title = document.createElement('span');
      title.className = 'notification-title';
      title.textContent = String(notification.title || 'Hsx update');
      const meta = document.createElement('span');
      meta.className = 'notification-meta';
      const created = new Date(notification.created);
      meta.textContent = `${String(notification.event || 'update').replaceAll('_', ' ')} · ${
        Number.isNaN(created.getTime()) ? '' : created.toLocaleString()
      }`;
      item.append(title, meta);
      item.addEventListener('click', () => openNotification(notification));
      fragment.append(item);
    }
    elements.notificationsList.replaceChildren(fragment);
    return notifications.reduce((highest, item) => Math.max(highest, Number(item.id) || 0), 0);
  } catch {
    const error = document.createElement('p');
    error.className = 'artifacts-empty';
    error.textContent = 'Notifications are unavailable right now.';
    elements.notificationsList.replaceChildren(error);
    return 0;
  }
}

function showNotificationToast(title) {
  const toast = document.getElementById('notification-toast');
  toast.textContent = title;
  toast.hidden = false;
  window.clearTimeout(notificationToastTimer);
  notificationToastTimer = window.setTimeout(() => { toast.hidden = true; }, 5000);
}

function handleLiveNotification(notification) {
  const supportedEvents = new Set([
    'job_done', 'job_failed', 'needs_input', 'needs_approval', 'health_alert', 'camera_motion',
  ]);
  if (!supportedEvents.has(notification.event)) return;
  showNotificationToast(String(notification.title || 'Hsx update'));
  refreshNotifications();
  refreshAgentActivity();
}

function abortNotificationStream() {
  if (notificationController) notificationController.abort();
  notificationController = null;
  window.clearTimeout(notificationReconnectTimer);
  notificationReconnectTimer = null;
}

async function consumeNotificationStream(controller) {
  const headers = new Headers();
  if (bearerToken) headers.set('Authorization', `Bearer ${bearerToken}`);
  headers.set('Last-Event-ID', String(notificationLastId));
  const response = await fetch(apiContract.endpoints.notifications_stream, {
    headers,
    signal: controller.signal,
    cache: 'no-store',
  });
  if (!response.ok) throw new Error('Notification stream is unavailable.');
  if (!response.body) throw new Error('Notification stream returned no body.');
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  while (!controller.signal.aborted) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
    const blocks = buffer.split(/\r?\n\r?\n/);
    buffer = blocks.pop() || '';
    for (const block of blocks) {
      if (block.startsWith(':')) continue;
      const id = Number(block.match(/^id:\s*(\d+)/m)?.[1]);
      const event = parseSseBlock(block);
      if (!event || event.event !== 'notification' || !Number.isInteger(id) || id <= notificationLastId) continue;
      notificationLastId = id;
      handleLiveNotification(event.data);
    }
    if (done) return;
  }
}

async function startNotificationStream() {
  abortNotificationStream();
  if (!notificationInitialized) {
    notificationLastId = Math.max(notificationLastId, await refreshNotifications());
    notificationInitialized = true;
  } else {
    await refreshNotifications();
  }
  const connect = async () => {
    if (document.hidden || notificationController) return;
    const controller = new AbortController();
    notificationController = controller;
    try {
      await consumeNotificationStream(controller);
      notificationRetryDelay = 500;
    } catch {
      if (controller.signal.aborted) return;
    } finally {
      if (notificationController === controller) notificationController = null;
    }
    if (!document.hidden) {
      notificationReconnectTimer = window.setTimeout(() => {
        notificationRetryDelay = Math.min(notificationRetryDelay * 2, 15000);
        connect();
      }, notificationRetryDelay);
    }
  };
  connect();
}

async function openNotification(notification) {
  const notificationId = Number(notification.id);
  if (Number.isInteger(notificationId)) {
    try {
      await apiFetch(apiContract.endpoints.notifications_read, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id: notificationId }),
      });
    } catch {
      // The notification remains visible and can be opened again if marking it read fails.
    }
  }
  elements.notificationPanel.hidden = true;
  document.getElementById('notifications-toggle').setAttribute('aria-expanded', 'false');
  await refreshNotifications();
  if (!notification.job_id) return;
  const endpoint = apiContract.endpoints.ask_job.replace(
    '{job_id}',
    encodeURIComponent(notification.job_id),
  );
  try {
    const response = await apiFetch(endpoint);
    const data = await response.json();
    const job = data.job;
    if (!response.ok || !job) return;
    if (job.state === 'waiting_for_approval') {
      pendingQuestion = job;
      if (!elements.stream.querySelector(`[data-approval-job="${CSS.escape(job.id)}"]`)) {
        appendApprovalCard(job);
      }
    } else if (job.state === 'waiting_for_user') {
      pendingQuestion = job;
      appendMessage('assistant', job.question || 'I need a little more information.', job.id);
    } else if (job.state === 'done' || job.state === 'failed') {
      appendMessage('assistant', job.result || job.error || notification.title, job.id);
    }
  } catch {
    appendMessage('assistant', 'That notification could not be opened. Check the task status and try again.');
  }
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
  const controller = activeChatController;
  if (controller) {
    activeChatController = null;
    controller.abort();
    if (!elements.prompt.value) {
      elements.prompt.value = safeStorageGet('sessionStorage', `hsx-draft-${sessionId}`);
      persistDraft();
    }
    setBusy(false);
    elements.send.disabled = false;
  }
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
  refreshGreeting();
  elements.prompt.value = '';
  elements.prompt.style.height = 'auto';
  renderAttachments();
  setConversationEmpty(true);
  updateSendButton();
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
    startNotificationStream();
  });
  document.getElementById('clear-token-button').addEventListener('click', () => {
    bearerToken = '';
    try { window.sessionStorage.removeItem('hsx-bearer-token'); } catch { /* Storage is optional. */ }
    elements.tokenInput.value = '';
    elements.tokenForm.hidden = true;
    refreshConnections();
    startNotificationStream();
  });
}

async function initialize() {
  const [tokensResponse, contractResponse] = await Promise.all([
    fetch('/web/design-tokens.json'),
    fetch('/web/api-contract.json'),
  ]);
  if (!tokensResponse.ok || !contractResponse.ok) throw new Error('Could not load the Hsx interface contract.');
  apiContract = await contractResponse.json();
  updateGreeting();
  setupVoiceInput();
  setupComposerOptions();
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
  document.getElementById('activity-toggle').addEventListener('click', (event) => {
    const button = event.currentTarget;
    const expanded = button.getAttribute('aria-expanded') === 'true';
    button.setAttribute('aria-expanded', String(!expanded));
    button.setAttribute('aria-label', expanded ? 'Expand agent activity' : 'Collapse agent activity');
    button.title = expanded ? 'Expand agent activity' : 'Collapse agent activity';
    elements.activityList.hidden = expanded;
  });
  document.getElementById('notifications-toggle').addEventListener('click', async () => {
    const opening = elements.notificationPanel.hidden;
    elements.notificationPanel.hidden = !opening;
    document.getElementById('notifications-toggle').setAttribute('aria-expanded', String(opening));
    if (opening) await refreshNotifications();
  });
  document.getElementById('mark-notifications-read').addEventListener('click', async () => {
    try {
      await apiFetch(apiContract.endpoints.notifications_read, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
      });
      await refreshNotifications();
    } catch {
      elements.notificationsList.textContent = 'Could not mark notifications as read.';
    }
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') {
      closeMobilePanels();
      elements.notificationPanel.hidden = true;
      document.getElementById('notifications-toggle').setAttribute('aria-expanded', 'false');
    }
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
  updateSendButton();

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
    stopVoiceInput();
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
    updateSendButton();
  });
  elements.stop.addEventListener('click', stopTask);
  document.getElementById('attach-button').addEventListener('click', () => elements.fileInput.click());
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
  window.addEventListener('online', () => {
    refreshConnections();
    startNotificationStream();
  });
  window.addEventListener('offline', () => {
    stopCameraStream();
    if (cameraAvailable) setCameraReconnecting('Network offline; waiting to reconnect…');
    refreshConnections();
  });
  window.addEventListener('pagehide', () => {
    stopCameraStream();
    abortNotificationStream();
  });
  window.addEventListener('pageshow', () => {
    if (!document.hidden) {
      refreshConnections();
      startNotificationStream();
    }
  });
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) {
      stopCameraStream();
      abortNotificationStream();
    } else {
      cameraRetryDelay = 500;
      refreshConnections();
      startNotificationStream();
    }
  });
  await refreshConnections();
  refreshGreeting();
  refreshArtifacts();
  refreshAgentActivity();
  startNotificationStream();
  window.setInterval(refreshConnections, 15000);
  window.setInterval(refreshAgentActivity, 5000);
  window.setInterval(updateMessageTimes, 60000);
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
