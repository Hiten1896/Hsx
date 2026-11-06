let sessionId = loadSessionId();
let apiContract;
let pendingQuestion = null;
let submitting = false;
let activeChatController = null;
let activeRunId = '';
let queuedMessage = '';
let chatWatchdog = null;
let notificationController = null;
let notificationLastId = 0;
let notificationInitialized = false;
let notificationReconnectTimer = null;
let notificationRetryDelay = 500;
let notificationToastTimer = null;
let pendingFeedback = null;
const chatSessions = new Map();
let artifactsRequestId = 0;
let voiceRecognition = null;
let voiceIsListening = false;
let voiceDraft = '';
let voiceStatusMessage = '';
let uploadedAttachments = [];
let cameraObjectUrl = '';
let cameraStreamController = null;
let cameraReconnectTimer = null;
let cameraRetryDelay = 500;
let cameraAvailable = false;
let cameraHasFrame = false;
let cameraEnabled = false;
let cameraClockOffsetMs = 0;
let cameraStreamStartedAt = 0;
let cameraAutoOffTimer = null;
let cameraVisionWorker = null;
let cameraVisionObserver = null;
let cameraVisionFrameId = 0;
let cameraVisionLastFrameAt = 0;
let cameraVisionLastDrawAt = 0;
let cameraVisionLastTimestamp = 0;
let cameraVisionInFlight = false;
let cameraVisionReady = false;
let cameraVisionFailed = false;
let cameraVisionGeneration = 0;
let cameraVisionDetections = [];
let cameraVisionSourceSize = { width: 0, height: 0 };
let conversationSaveTimer = null;
let restoringConversation = false;

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
  cameraView: document.getElementById('camera-view'),
  cameraAiCanvas: document.getElementById('camera_ai_canvas'),
  cameraStatus: document.getElementById('camera-status'),
  cameraPlaceholder: document.getElementById('camera-placeholder'),
  cameraBadge: document.getElementById('camera-badge'),
  fileInput: document.getElementById('file-input'),
  attachmentList: document.getElementById('attachment-list'),
  composerStatus: document.getElementById('composer-status'),
  dropHint: document.getElementById('drop-hint'),
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
  const signal = options.signal || (globalThis.AbortSignal?.timeout
    ? AbortSignal.timeout(8000)
    : undefined);
  return fetch(url, { ...options, headers, signal });
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

function refreshGreeting() {
  const hour = new Date().getHours();
  const timeOfDay = hour < 12 ? 'morning' : hour < 18 ? 'afternoon' : 'evening';
  elements.greeting.textContent = `Good ${timeOfDay}, Sir`;
  elements.greeting.setAttribute('aria-busy', 'false');
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

function renderChatHistory() {
  const list = document.getElementById('chat-history-list');
  const sessions = [...chatSessions.entries()]
    .sort((first, second) => (second[1].updatedAt || 0) - (first[1].updatedAt || 0))
    .filter(([, session]) => session.saved === true || session.hasMessages === true)
    .filter(([, session]) => (session.title || 'New chat').toLowerCase().includes(
      (document.getElementById('chat-history-search')?.value || '').trim().toLowerCase(),
    ));
  if (!sessions.length) {
    const empty = document.createElement('p');
    empty.className = 'artifacts-empty';
    empty.textContent = document.getElementById('chat-history-search')?.value.trim()
      ? 'No matching chats.' : 'No saved chats yet.';
    list.replaceChildren(empty);
    return;
  }
  const fragment = document.createDocumentFragment();
  for (const [id, session] of sessions) {
    const row = document.createElement('div');
    row.className = 'chat-history-row';
    row.dataset.active = String(id === sessionId);
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'chat-history-item';
    button.title = session.title || 'New chat';
    button.setAttribute('aria-current', String(id === sessionId));
    button.setAttribute('aria-label', `Open chat: ${session.title || 'New chat'}`);
    button.addEventListener('click', () => switchChatSession(id));
    const icon = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    icon.setAttribute('viewBox', '0 0 24 24');
    icon.setAttribute('aria-hidden', 'true');
    const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    path.setAttribute('d', 'M4 5h16v12H8l-4 3V5Z');
    icon.append(path);
    const title = document.createElement('span');
    title.textContent = session.title || 'New chat';
    button.append(icon, title);
    const rename = document.createElement('button');
    rename.type = 'button';
    rename.className = 'chat-history-action';
    rename.textContent = '✎';
    rename.title = 'Rename chat';
    rename.setAttribute('aria-label', `Rename chat: ${session.title || 'New chat'}`);
    rename.addEventListener('click', () => {
      const updated = window.prompt('Rename this chat', session.title || 'New chat');
      if (updated?.trim()) {
        session.title = updated.trim().slice(0, 100);
        void apiFetch(`${apiContract.endpoints.conversations}/${encodeURIComponent(id)}`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ title: session.title }),
        });
        renderChatHistory();
      }
    });
    const remove = document.createElement('button');
    remove.type = 'button';
    remove.className = 'chat-history-action';
    remove.textContent = '×';
    remove.title = 'Delete chat';
    remove.setAttribute('aria-label', `Delete chat: ${session.title || 'New chat'}`);
    remove.addEventListener('click', () => {
      if (!window.confirm(`Delete “${session.title || 'New chat'}”? This cannot be undone.`)) return;
      chatSessions.delete(id);
      void apiFetch(`${apiContract.endpoints.conversations}/${encodeURIComponent(id)}`, { method: 'DELETE' });
      if (id === sessionId) {
        resetConversation(false);
        chatSessions.delete(id);
        renderChatHistory();
      } else renderChatHistory();
    });
    row.append(button, rename, remove);
    fragment.append(row);
  }
  list.replaceChildren(fragment);
}

function saveCurrentChatSession() {
  let session = chatSessions.get(sessionId);
  if (!session) {
    session = { title: 'New chat', fragment: document.createDocumentFragment() };
    chatSessions.set(sessionId, session);
  }
  session.fragment = document.createDocumentFragment();
  for (const child of [...elements.stream.children]) {
    if (child !== elements.welcome) session.fragment.append(child);
  }
  session.isEmpty = elements.conversationLayout.classList.contains('is-empty');
  session.draft = elements.prompt.value;
  session.attachments = uploadedAttachments.map((item) => ({ ...item }));
  session.pendingQuestion = pendingQuestion;
  session.hasMessages = session.fragment.childElementCount > 0;
  if (session.hasMessages && apiContract) void saveConversation(sessionId);
  renderChatHistory();
}

function conversationSnapshot(id) {
  const session = chatSessions.get(id);
  const messages = id === sessionId
    ? [...elements.stream.querySelectorAll('.message-card')]
    : [...(session?.fragment?.querySelectorAll?.('.message-card') || [])];
  const artifacts = id === sessionId
    ? [...document.querySelectorAll('#artifacts-list .artifact-item')].map((item) => ({
      name: item.dataset.name || item.textContent.trim(), path: item.dataset.path || '',
    }))
    : [];
  return {
    session_id: id,
    title: session?.title || 'New chat',
    thread: {
      messages: messages.map((article) => ({
        role: article.dataset.role || 'assistant',
        text: article.dataset.response || article.querySelector('.message-content')?.textContent || '',
        job_id: article.dataset.jobId || '',
        prompt: article.dataset.prompt || '',
        edit_text: article.dataset.editText || '',
        feedback: article.querySelector('[data-feedback][aria-pressed="true"]')?.dataset.feedback || '',
      })),
      artifacts,
    },
  };
}

async function saveConversation(id) {
  const snapshot = conversationSnapshot(id);
  if (!snapshot.thread.messages.length) return;
  try {
    const response = await apiFetch(apiContract.endpoints.conversations, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(snapshot),
    });
    if (response.ok) {
      const session = chatSessions.get(id);
      if (session) {
        session.saved = true;
        session.updatedAt = Date.now();
      }
      renderChatHistory();
    }
  } catch { /* Server reconnect restores persistence on the next save. */ }
}

function queueConversationSave() {
  if (restoringConversation || !apiContract) return;
  window.clearTimeout(conversationSaveTimer);
  conversationSaveTimer = window.setTimeout(() => { void saveConversation(sessionId); }, 350);
}

async function restoreConversation(id) {
  const response = await apiFetch(`${apiContract.endpoints.conversations}/${encodeURIComponent(id)}`);
  if (!response.ok) throw new Error('Conversation could not be opened.');
  const saved = await response.json();
  const session = chatSessions.get(id) || { title: saved.title, fragment: document.createDocumentFragment() };
  session.title = saved.title;
  session.saved = true;
  session.fragment = document.createDocumentFragment();
  chatSessions.set(id, session);
  restoringConversation = true;
  try {
    for (const message of saved.thread?.messages || []) {
      const article = appendMessage(message.role, message.text, message.job_id, {
        prompt: message.prompt, editText: message.edit_text, noFeedback: !message.job_id,
      });
      if (message.feedback) article.querySelector(`[data-feedback="${message.feedback}"]`)?.setAttribute('aria-pressed', 'true');
      session.fragment.append(article);
    }
  } finally { restoringConversation = false; }
  session.hasMessages = session.fragment.childElementCount > 0;
  return session;
}

async function switchChatSession(nextSessionId) {
  if (nextSessionId === sessionId) return;
  saveCurrentChatSession();
  const previousSessionId = sessionId;
  sessionId = nextSessionId;
  persistSessionId();
  let next = chatSessions.get(nextSessionId);
  if (!next) return;
  if (next.saved && !next.hasMessages) {
    try {
      next = await restoreConversation(nextSessionId);
    } catch {
      sessionId = previousSessionId;
      persistSessionId();
      return;
    }
  }
  elements.stream.replaceChildren(elements.welcome);
  elements.stream.append(next.fragment);
  next.fragment = document.createDocumentFragment();
  elements.welcome.hidden = !next.isEmpty;
  elements.prompt.value = next.draft || '';
  uploadedAttachments = next.attachments || [];
  pendingQuestion = next.pendingQuestion || null;
  renderAttachments();
  setConversationEmpty(Boolean(next.isEmpty));
  updateSendButton();
  persistDraft();
  renderChatHistory();
  refreshArtifacts();
  refreshAgentActivity();
}

function hasComposerContent() {
  return Boolean(elements.prompt.value.trim() || uploadedAttachments.some((item) => item.status === 'ready'));
}

function updateSendButton() {
  elements.send.hidden = false;
  elements.send.disabled = !hasComposerContent();
}

function setConnection(id, labelId, value, state) {
  const dot = document.getElementById(id);
  const label = document.getElementById(labelId);
  if (dot) dot.dataset.state = state;
  if (label) label.textContent = value;
}

function showEmergencyStop(stopped) {
  document.getElementById('emergency-banner').hidden = !stopped;
  document.getElementById('resume-button').hidden = !stopped;
  document.getElementById('emergency-stop-button').hidden = stopped;
}

let settingsReturnFocus = null;

function selectSettingsTab(name, focus = false) {
  const tabs = [...document.querySelectorAll('[data-settings-tab]')];
  for (const tab of tabs) {
    const selected = tab.dataset.settingsTab === name;
    tab.setAttribute('aria-selected', String(selected));
    tab.tabIndex = selected ? 0 : -1;
    document.querySelector(`[data-settings-panel="${tab.dataset.settingsTab}"]`).hidden = !selected;
  }
  if (focus) document.querySelector(`[data-settings-tab="${name}"]`).focus();
  if (name === 'advanced') {
    document.getElementById('debug-info').textContent = JSON.stringify({
      app: 'Hsx',
      online: navigator.onLine,
      provider: document.getElementById('llm-state').textContent,
      camera: document.getElementById('camera-state').textContent,
      viewport: `${window.innerWidth} × ${window.innerHeight}`,
    }, null, 2);
  }
}

async function testProviderHealth() {
  const status = document.getElementById('provider-health-status');
  const list = document.getElementById('provider-health-list');
  const button = document.getElementById('test-provider-health');
  button.disabled = true;
  status.textContent = 'Checking configured providers…';
  list.replaceChildren();
  try {
    const response = await apiFetch(apiContract.endpoints.provider_health);
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Provider health check failed.');
    if (!Array.isArray(data.providers) || !data.providers.length) {
      const empty = document.createElement('p');
      empty.textContent = 'No providers are configured.';
      list.append(empty);
    }
    for (const provider of data.providers || []) {
      const item = document.createElement('div');
      item.className = 'provider-health-item';
      const name = document.createElement('strong');
      name.textContent = `${provider.provider} · ${provider.model}`;
      const state = document.createElement('span');
      state.dataset.state = provider.status;
      state.textContent = provider.status === 'ok' ? 'Connected' : `Failed: ${provider.reason || 'unavailable'}`;
      item.append(name, state);
      list.append(item);
    }
    status.textContent = 'Provider health check complete.';
  } catch (error) {
    status.textContent = error.message || 'Provider health check failed.';
  } finally {
    button.disabled = false;
  }
}

async function refreshEmergencyStop() {
  try {
    const response = await apiFetch(apiContract.endpoints.policy_status);
    if (response.ok) showEmergencyStop(Boolean((await response.json()).stopped));
  } catch {
    // Leave the last reported policy state visible.
  }
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
  if (role === 'user') {
    const session = chatSessions.get(sessionId);
    if (session && (!session.title || session.title === 'New chat')) {
      session.title = String(options.editText ?? text ?? '').trim().slice(0, 40) || 'New chat';
      renderChatHistory();
    }
  }
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
        button.addEventListener('click', () => {
          pendingFeedback = { article, rating: button.dataset.feedback };
          document.getElementById('feedback-reason').value = '';
          document.getElementById('feedback-note').value = '';
          document.getElementById('feedback-dialog').showModal();
        });
      } else {
        button.title = 'Feedback is unavailable for this message';
      }
    });
  } else {
    feedback?.remove();
  }
  elements.stream.appendChild(fragment);
  const session = chatSessions.get(sessionId);
  if (session) session.hasMessages = true;
  if (session) session.updatedAt = Date.now();
  queueConversationSave();
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
      body: JSON.stringify({
        job_id: article.dataset.jobId,
        conversation_id: sessionId,
        message_id: article.dataset.jobId,
        feedback: feedback === 'good' ? 'up' : 'down',
        reason: document.getElementById('feedback-reason').value,
        note: document.getElementById('feedback-note').value,
        prompt_messages: [{ role: 'user', content: article.dataset.prompt || '' }],
        response: article.dataset.response || '',
        client: 'web',
      }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Could not save feedback.');
    status.textContent = result.saved
      ? 'Thanks for your feedback.'
      : 'Feedback saving is off; nothing was stored.';
    buttons.forEach((button) => {
      button.disabled = false;
    });
    queueConversationSave();
    document.getElementById('feedback-dialog').close();
    refreshFeedbackSettings();
  } catch {
    buttons.forEach((button) => {
      button.disabled = false;
      button.removeAttribute('aria-pressed');
    });
    status.textContent = 'Feedback could not be saved.';
  }
}

async function refreshFeedbackSettings() {
  try {
    const response = await apiFetch(apiContract.endpoints.feedback_settings);
    if (!response.ok) throw new Error('Feedback settings are unavailable.');
    const settings = await response.json();
    document.getElementById('save-feedback-setting').checked = Boolean(settings.save_feedback);
    document.getElementById('full-conversation-setting').checked = Boolean(settings.include_full_conversation);
    document.getElementById('file-contents-setting').checked = Boolean(settings.include_file_contents);
    document.getElementById('feedback-count').textContent =
      `${Number(settings.count) || 0} feedback entr${Number(settings.count) === 1 ? 'y' : 'ies'}`;
    document.getElementById('feedback-storage-note').textContent = settings.storage;
  } catch {
    document.getElementById('feedback-count').textContent = 'Feedback settings unavailable';
  }
}

async function updateFeedbackSettings() {
  const response = await apiFetch(apiContract.endpoints.feedback_settings, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      save_feedback: document.getElementById('save-feedback-setting').checked,
      include_full_conversation: document.getElementById('full-conversation-setting').checked,
      include_file_contents: document.getElementById('file-contents-setting').checked,
    }),
  });
  if (!response.ok) throw new Error('Feedback settings could not be saved.');
  await refreshFeedbackSettings();
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
  if (!isBusy) queueConversationSave();
  if (chatWatchdog) window.clearTimeout(chatWatchdog);
  chatWatchdog = null;
  if (isBusy) {
    chatWatchdog = window.setTimeout(async () => {
      if (!activeRunId) {
        setBusy(false);
        return;
      }
      try {
        const status = await apiFetch(`/api/runs/status?run_id=${encodeURIComponent(activeRunId)}`);
        const result = await status.json();
        if (!status.ok || ['done', 'failed', 'cancelled', 'unknown'].includes(result.state)) {
          activeChatController?.abort();
          setBusy(false);
        }
      } catch {
        setBusy(false);
      }
    }, 45_000);
  }
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
    recognition.lang = document.getElementById('voice-language-setting').value || navigator.language || 'en-US';
    voiceDraft = elements.prompt.value;
    voiceFinalText = '';
    recognition.onstart = () => {
      voiceIsListening = true;
      voiceStatusMessage = '';
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

async function answerPendingQuestion(message, signal) {
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
    signal,
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
  await pollQuestionJob(jobId, activity, signal);
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
      const controller = new AbortController();
      activeChatController = controller;
      setBusy(true);
      try {
        await answerPendingQuestion(answer, controller.signal);
      } catch (error) {
        if (error.name !== 'AbortError') {
          appendMessage('assistant', 'Could not continue the task. Please try again.');
        }
      } finally {
        if (activeChatController === controller) {
          activeChatController = null;
          setBusy(false);
        }
      }
    });
    actions.appendChild(button);
  }
  card.append(title, summary, why, risk, details, actions);
  elements.welcome.hidden = true;
  elements.stream.appendChild(card);
  scrollToLatest();
}

async function pollQuestionJob(jobId, activity, signal) {
  const endpoint = apiContract.endpoints.ask_job.replace('{job_id}', encodeURIComponent(jobId));
  while (true) {
    await new Promise((resolve) => setTimeout(resolve, 1200));
    if (signal?.aborted) throw new DOMException('Task stopped.', 'AbortError');
    const response = await apiFetch(endpoint, { signal });
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
  if ((!typedMessage && !readyAttachments.length)) return;
  if (submitting) {
    queuedMessage = typedMessage;
    elements.prompt.value = '';
    persistDraft();
    elements.composerStatus.textContent = 'Next message queued.';
    return;
  }
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

  if (pendingQuestion) {
    const controller = new AbortController();
    activeChatController = controller;
    try {
      await answerPendingQuestion(requestMessage, controller.signal);
      elements.prompt.value = '';
      uploadedAttachments = [];
      persistAttachments();
      renderAttachments();
      safeStorageSet('sessionStorage', `hsx-draft-${sessionId}`, '');
    } catch (error) {
      if (error.name !== 'AbortError') {
        appendMessage('assistant', 'Could not submit your answer. Your text is still available in this tab.');
      }
      elements.prompt.value = typedMessage;
    } finally {
      if (activeChatController === controller) {
        activeChatController = null;
        setBusy(false);
      }
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
      throw new Error(error.error || 'The request could not be completed.');
    }
    uploadedAttachments = [];
    persistAttachments();
    renderAttachments();
    elements.prompt.value = '';
    updateSendButton();
    activity = appendActivity('Thinking…');
    setBusy(true);
    await readStream(response, ({ event, data }) => {
      if (event === 'message_start') {
        activeRunId = data.run_id || '';
        setBusy(true);
      } else if (event === 'abort' || event === 'stream_end') {
        setBusy(false);
      }
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
        activeRunId = data.id;
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
        setBusy(false);
        throw new Error(data.message || 'The task failed.');
      } else if (event === 'done' && activeChatController === controller) {
        setBusy(false);
        elements.prompt.focus();
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
    if (activeChatController === controller) setBusy(false);
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
      activeRunId = '';
      setBusy(false);
      elements.prompt.focus();
    }
    if (queuedMessage) {
      elements.prompt.value = queuedMessage;
      queuedMessage = '';
      window.setTimeout(() => elements.composer.requestSubmit(), 0);
    }
    refreshArtifacts();
    refreshConnections();
  }
}

function setCameraStatus(connected, message) {
  elements.cameraStatus.textContent = message;
  elements.cameraBadge.classList.toggle('camera-badge-online', connected);
  elements.cameraBadge.classList.toggle('camera-badge-offline', !connected);
  elements.cameraBadge.classList.toggle('camera-badge-standby', !connected && !cameraEnabled);
  elements.cameraBadge.classList.remove('camera-badge-reconnecting');
  elements.cameraBadge.replaceChildren();
  const dot = document.createElement('span');
  dot.className = 'connection-dot';
  const label = document.createElement('span');
  label.textContent = connected ? (cameraHasFrame ? 'Live' : 'Connecting') : cameraEnabled ? 'Offline' : 'Standby';
  elements.cameraBadge.append(dot, label);
  const state = connected ? 'connected' : cameraEnabled ? 'disconnected' : 'warning';
  const status = connected ? (cameraHasFrame ? 'Live' : 'Connecting') : cameraEnabled ? 'Offline' : 'Standby';
  setConnection('camera-dot', 'camera-state', status, state);
  elements.camera.hidden = !connected || !cameraHasFrame;
  elements.cameraPlaceholder.hidden = connected && cameraHasFrame;
  elements.cameraView.setAttribute('aria-label', cameraEnabled ? 'Turn off camera' : 'Turn on camera');
  document.getElementById('camera-prompt').textContent = cameraEnabled
    ? connected ? 'Connecting to camera…' : 'Camera unavailable'
    : 'Camera is off';
  document.getElementById('camera-clip-time').hidden = !connected || !cameraHasFrame;
}

function stopCameraAndStandby() {
  cameraEnabled = false;
  stopCameraVision(true);
  window.clearTimeout(cameraAutoOffTimer);
  cameraAutoOffTimer = null;
  if (apiContract) void apiFetch(apiContract.endpoints.camera_stop, { method: 'POST' });
  cameraAvailable = false;
  cameraHasFrame = false;
  stopCameraStream();
  elements.camera.removeAttribute('src');
  if (cameraObjectUrl) {
    URL.revokeObjectURL(cameraObjectUrl);
    cameraObjectUrl = '';
  }
  document.getElementById('camera-clip-time').textContent = 'Waiting for video';
  document.getElementById('camera-clip-time').hidden = true;
  setCameraLatency(null);
  setCameraStatus(false, 'Click to turn on camera');
}

async function refreshCamera() {
  if (!cameraEnabled) return;
  try {
    const startResponse = await apiFetch(apiContract.endpoints.camera_start, { method: 'POST' });
    if (!startResponse.ok) throw new Error('Camera could not be started.');
    const response = await apiFetch(apiContract.endpoints.camera_status);
    const data = await response.json();
    const host = data.host || {};
    const hostLabel = [host.hostname, host.hardware].filter(Boolean).join(' · ');
    document.getElementById('camera-host-label').textContent =
      hostLabel || 'Host hardware unavailable';
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
      setCameraStatus(false, 'Camera feed offline');
      scheduleCameraReconnect();
    }
  } catch {
    document.getElementById('camera-host-label').textContent = 'Host hardware unavailable';
    if (cameraHasFrame) setCameraReconnecting('Camera feed offline; retrying…');
    else setCameraStatus(false, 'Camera feed offline');
    scheduleCameraReconnect();
  }
}

function toggleCamera() {
  if (cameraEnabled) {
    stopCameraAndStandby();
    return;
  }
  cameraEnabled = true;
  startCameraVision();
  window.clearTimeout(cameraAutoOffTimer);
  cameraAutoOffTimer = window.setTimeout(stopCameraAndStandby, 5 * 60 * 1000);
  setCameraStatus(false, 'Connecting to the host camera…');
  refreshCamera();
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
    output.textContent = cameraEnabled ? 'Waiting for frames' : 'No live feed';
    output.dataset.state = 'warning';
    return;
  }
  const captureBrowserTime = Math.min(
    capturedAt * 1000 - cameraClockOffsetMs,
    Date.now(),
  );
  const delaySeconds = Math.max(0, Date.now() - captureBrowserTime) / 1000;
  output.textContent = `${delaySeconds.toFixed(1)} s behind`;
  output.dataset.state = delaySeconds > 2 ? 'warning' : 'connected';
}

function showCameraFrame(frame) {
  if (!cameraEnabled) return;
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
  const captureBrowserTime = Math.min(
    frame.capturedAt * 1000 - cameraClockOffsetMs,
    Date.now(),
  );
  const captured = new Date(captureBrowserTime);
  document.getElementById('camera-clip-time').textContent =
    `Clip ${captured.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}`;
  document.getElementById('camera-clip-time').dataset.capturedAt = String(captureBrowserTime);
  document.getElementById('camera-clip-time').hidden = false;
  setCameraLatency(frame.capturedAt);
  setCameraStatus(true, 'Live from the Hsx host');
  startCameraVision();
}

function isCameraVisionVisible() {
  if (!cameraEnabled || document.hidden || !cameraHasFrame || elements.camera.hidden) return false;
  const rect = elements.cameraView.getBoundingClientRect();
  return rect.width > 0 && rect.height > 0
    && rect.bottom > 0 && rect.right > 0
    && rect.top < window.innerHeight && rect.left < window.innerWidth;
}

function clearCameraVisionCanvas() {
  const canvas = elements.cameraAiCanvas;
  const context = canvas.getContext('2d');
  if (context) context.clearRect(0, 0, canvas.width, canvas.height);
  canvas.hidden = true;
  cameraVisionDetections = [];
}

function stopCameraVision(shutdown = false) {
  if (cameraVisionFrameId) cancelAnimationFrame(cameraVisionFrameId);
  cameraVisionFrameId = 0;
  clearCameraVisionCanvas();
  if (shutdown) {
    cameraVisionGeneration += 1;
    cameraVisionInFlight = false;
    cameraVisionReady = false;
    cameraVisionFailed = false;
    if (cameraVisionWorker) {
      cameraVisionWorker.postMessage({ type: 'stop' });
      cameraVisionWorker.terminate();
      cameraVisionWorker = null;
    }
  }
}

function ensureCameraVisionWorker() {
  if (cameraVisionWorker || cameraVisionFailed || !('Worker' in window) || !('createImageBitmap' in window)) return;
  const generation = cameraVisionGeneration;
  try {
    cameraVisionWorker = new Worker('/web/camera-vision-worker.js', { type: 'module', name: 'hsx-camera-vision' });
    cameraVisionWorker.onmessage = (event) => {
      if (!cameraEnabled || generation !== cameraVisionGeneration) return;
      const data = event.data || {};
      if (data.type === 'ready') {
        cameraVisionReady = true;
        if (!cameraVisionFrameId) cameraVisionFrameId = requestAnimationFrame(drawCameraVision);
      } else if (data.type === 'detections') {
        cameraVisionSourceSize = { width: data.width, height: data.height };
        const previous = cameraVisionDetections;
        cameraVisionDetections = Array.isArray(data.detections) ? data.detections.map((detection, index) => {
          const prior = previous.find((item, priorIndex) => item.label === detection.label && priorIndex === index);
          if (!prior) return detection;
          const blend = (oldValue, newValue) => oldValue * 0.35 + newValue * 0.65;
          return {
            ...detection,
            x: blend(prior.x, detection.x),
            y: blend(prior.y, detection.y),
            width: blend(prior.width, detection.width),
            height: blend(prior.height, detection.height),
          };
        }) : [];
        cameraVisionInFlight = false;
      } else if (data.type === 'error') {
        cameraVisionInFlight = false;
        cameraVisionReady = false;
        cameraVisionFailed = true;
        clearCameraVisionCanvas();
        console.warn(data.message || 'Camera detection is unavailable.');
      }
    };
    cameraVisionWorker.onerror = () => {
      cameraVisionReady = false;
      cameraVisionInFlight = false;
      cameraVisionFailed = true;
      clearCameraVisionCanvas();
      cameraVisionWorker?.terminate();
      cameraVisionWorker = null;
    };
    cameraVisionWorker.postMessage({ type: 'init' });
  } catch (error) {
    console.warn('Camera detection worker could not start.', error);
  }
}

async function sendCameraVisionFrame(timestamp) {
  if (!cameraVisionWorker || !cameraVisionReady || cameraVisionInFlight || !isCameraVisionVisible()) return;
  if (timestamp - cameraVisionLastFrameAt < 500 || !elements.camera.complete || !elements.camera.naturalWidth) return;
  cameraVisionLastFrameAt = timestamp;
  cameraVisionInFlight = true;
  const generation = cameraVisionGeneration;
  try {
    const scale = Math.min(1, 640 / elements.camera.naturalWidth, 360 / elements.camera.naturalHeight);
    const bitmap = await createImageBitmap(elements.camera, {
      resizeWidth: Math.max(1, Math.round(elements.camera.naturalWidth * scale)),
      resizeHeight: Math.max(1, Math.round(elements.camera.naturalHeight * scale)),
      resizeQuality: 'low',
    });
    if (generation !== cameraVisionGeneration || !isCameraVisionVisible() || !cameraVisionWorker) {
      bitmap.close();
      cameraVisionInFlight = false;
      return;
    }
    const nextTimestamp = Math.max(Math.round(performance.now()), cameraVisionLastTimestamp + 1);
    cameraVisionLastTimestamp = nextTimestamp;
    cameraVisionWorker.postMessage({ type: 'frame', bitmap, timestamp: nextTimestamp }, [bitmap]);
  } catch {
    cameraVisionInFlight = false;
  }
}

function drawCameraVision() {
  cameraVisionFrameId = 0;
  if (cameraVisionFailed) {
    clearCameraVisionCanvas();
    return;
  }
  if (!isCameraVisionVisible()) {
    stopCameraVision(false);
    return;
  }
  const now = performance.now();
  if (now - cameraVisionLastDrawAt < 33) {
    cameraVisionFrameId = requestAnimationFrame(drawCameraVision);
    return;
  }
  cameraVisionLastDrawAt = now;
  if (!cameraVisionWorker) ensureCameraVisionWorker();
  if (cameraVisionReady) {
    const canvas = elements.cameraAiCanvas;
    const rect = elements.cameraView.getBoundingClientRect();
    const ratio = Math.min(window.devicePixelRatio || 1, 1.5);
    const width = Math.max(1, Math.round(rect.width * ratio));
    const height = Math.max(1, Math.round(rect.height * ratio));
    if (canvas.width !== width || canvas.height !== height) {
      canvas.width = width;
      canvas.height = height;
    }
    canvas.hidden = false;
    const context = canvas.getContext('2d');
    context.setTransform(ratio, 0, 0, ratio, 0, 0);
    context.clearRect(0, 0, rect.width, rect.height);
    const source = cameraVisionSourceSize;
    if (source.width && source.height) {
      const scale = Math.max(rect.width / source.width, rect.height / source.height);
      const offsetX = (rect.width - source.width * scale) / 2;
      const offsetY = (rect.height - source.height * scale) / 2;
      context.lineWidth = 2;
      context.font = '600 11px system-ui, sans-serif';
      for (const detection of cameraVisionDetections) {
        const x = detection.x * scale + offsetX;
        const y = detection.y * scale + offsetY;
        const boxWidth = detection.width * scale;
        const boxHeight = detection.height * scale;
        const color = detection.label === 'face' ? '#a9dcff' : '#a8e6ad';
        context.strokeStyle = color;
        context.fillStyle = color;
        context.strokeRect(x, y, boxWidth, boxHeight);
        const label = `${detection.label} ${Math.round(detection.score * 100)}%`;
        const labelWidth = context.measureText(label).width + 12;
        const labelY = Math.max(0, y - 21);
        context.fillRect(x, labelY, labelWidth, 19);
        context.fillStyle = '#101713';
        context.fillText(label, x + 6, labelY + 13);
      }
    }
  }
  void sendCameraVisionFrame(now);
  cameraVisionFrameId = requestAnimationFrame(drawCameraVision);
}

function startCameraVision() {
  if (!cameraEnabled || cameraVisionFailed || !isCameraVisionVisible()) return;
  ensureCameraVisionWorker();
  if (!cameraVisionFrameId) cameraVisionFrameId = requestAnimationFrame(drawCameraVision);
}

function pauseCameraVision() {
  stopCameraVision(false);
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
  if (!cameraEnabled || document.hidden || !navigator.onLine || cameraReconnectTimer) return;
  if (cameraHasFrame) setCameraReconnecting('Video paused; reconnecting to the latest frame…');
  else setCameraStatus(false, 'Camera feed offline');
  cameraReconnectTimer = window.setTimeout(() => {
    cameraReconnectTimer = null;
    refreshCamera();
  }, cameraRetryDelay);
  cameraRetryDelay = Math.min(cameraRetryDelay * 2, 8000);
}

async function startCameraStream() {
  if (!cameraEnabled || !cameraAvailable || document.hidden || !navigator.onLine || cameraStreamController || cameraReconnectTimer) return;
  const controller = new AbortController();
  cameraStreamController = controller;
  cameraStreamStartedAt = Date.now();
  try {
    const requestStartedAt = Date.now();
    const response = await apiFetch(apiContract.endpoints.camera_stream, {
      signal: controller.signal,
    });
    if (!response.ok || !response.body) {
      cameraAvailable = false;
      setCameraStatus(false, 'Camera feed offline');
      scheduleCameraReconnect();
      return;
    }
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
    const clipBrowserTime = Number(document.getElementById('camera-clip-time').dataset.capturedAt);
    if (Number.isFinite(clipBrowserTime)) {
      setCameraLatency((clipBrowserTime + cameraClockOffsetMs) / 1000);
    }
  }
  const currentDelayMs = cameraHasFrame
    ? Date.now() - Number(document.getElementById('camera-clip-time').dataset.capturedAt)
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
      const providerStatus = available ? String(telemetry.provider_model || telemetry.provider) : 'Standby';
      const providerCall = String(telemetry.provider_status || '');
      const providerState = providerCall.startsWith('Failed')
        ? 'disconnected'
        : providerStatus === 'Standby' ? 'warning' : 'connected';
      setConnection('llm-dot', 'llm-state', providerStatus, providerState);
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
  const indicator = document.getElementById('connection-indicator');
  indicator.dataset.state = document.getElementById('api-dot').dataset.state || 'warning';
  if (cameraEnabled) await refreshCamera();
  else setCameraStatus(false, 'Click to turn on camera');
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
  const requestedSessionId = sessionId;
  const requestId = ++artifactsRequestId;
  const empty = document.createElement('p');
  empty.className = 'artifacts-empty';
  empty.textContent = 'Loading generated files…';
  elements.artifactsList.replaceChildren(empty);
  try {
    const query = new URLSearchParams({ session_id: requestedSessionId });
    const response = await apiFetch(`${apiContract.endpoints.outputs}?${query}`);
    if (!response.ok) throw new Error('Could not load generated files.');
    const data = await response.json();
    if (requestId !== artifactsRequestId || requestedSessionId !== sessionId) return;
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
      item.dataset.name = file.name;
      item.dataset.path = file.path;
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
    if (requestId === artifactsRequestId && requestedSessionId === sessionId) {
      empty.textContent = 'Generated files are unavailable right now. Tap refresh to try again.';
    }
  }
}

function formatElapsed(seconds) {
  if (!Number.isFinite(seconds)) return '—';
  const total = Math.max(0, Math.floor(seconds));
  if (total < 60) return `${total}s`;
  const minutes = Math.floor(total / 60);
  const remaining = total % 60;
  return `${minutes}m ${remaining}s`;
}

function closeHeaderPopovers() {
  const panels = [
    [elements.notificationPanel, document.getElementById('notifications-toggle')],
    [document.getElementById('connections-panel'), document.getElementById('connection-toggle')],
  ];
  for (const [panel, button] of panels) {
    panel.hidden = true;
    button.setAttribute('aria-expanded', 'false');
  }
}

function toggleHeaderPopover(panel, button) {
  const opening = panel.hidden;
  closeHeaderPopovers();
  panel.hidden = !opening;
  button.setAttribute('aria-expanded', String(opening));
  return opening;
}

function updateActivityElapsed() {
  const now = Date.now();
  elements.activityList.querySelectorAll('[data-elapsed-start]').forEach((node) => {
    const started = Number(node.dataset.elapsedStart);
    if (Number.isFinite(started)) node.textContent = formatElapsed((now - started) / 1000);
  });
}

async function refreshAgentActivity() {
  const previousScrollTop = elements.activityList.scrollTop;
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
      item.dataset.state = String(task.state || 'unknown');
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
      const addDetail = (label, value) => {
        const detail = document.createElement('div');
        detail.className = 'activity-detail';
        const key = document.createElement('span');
        key.textContent = label;
        const text = document.createElement('strong');
        text.textContent = value;
        detail.append(key, text);
        item.append(detail);
        return text;
      };
      addDetail('Agent', `${task.agent_role || 'Assistant'} · ${task.agent_name || 'Hsx'}`);
      addDetail('Sub-task', task.subtask || task.task_description || task.summary || 'Task details unavailable');
      const targetPaths = Array.isArray(task.file_paths) ? task.file_paths.filter(Boolean).join(', ')
        : (task.file_paths || task.task_path || 'No target path reported');
      addDetail('Target', String(targetPaths));
      if (['running', 'queued'].includes(task.state)) {
        const elapsed = addDetail('Elapsed', formatElapsed(Number(task.elapsed_seconds)));
        elapsed.dataset.elapsedStart = String(Date.now() - Math.max(0, Number(task.elapsed_seconds) || 0) * 1000);
        addDetail(
          'ETA',
          Number.isFinite(Number(task.eta_seconds)) && task.eta_seconds !== null
            ? formatElapsed(Number(task.eta_seconds))
            : 'Estimating',
        );
      }
      fragment.append(item);
    }
    elements.activityList.replaceChildren(fragment);
    elements.activityList.scrollTop = previousScrollTop;
    updateActivityElapsed();
  } catch {
    document.getElementById('activity-count').textContent = 'Unavailable';
    empty.textContent = 'Agent activity is unavailable right now.';
    elements.activityList.replaceChildren(empty);
  }
}

async function retryCamera() {
  const button = document.getElementById('camera-retry');
  button.disabled = true;
  cameraEnabled = true;
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
  const runId = activeRunId;
  if (controller) {
    try {
      await apiFetch(apiContract.endpoints.stop, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ run_id: runId }),
      });
    } catch {
      elements.composerStatus.textContent = 'The stop request could not reach Hsx.';
    }
    controller.abort();
    if (!elements.prompt.value) {
      elements.prompt.value = safeStorageGet('sessionStorage', `hsx-draft-${sessionId}`);
      persistDraft();
    }
  }
  setBusy(false);
}

function resetConversation(saveCurrent = true) {
  if (saveCurrent) saveCurrentChatSession();
  sessionId = makeSessionId();
  chatSessions.set(sessionId, {
    title: 'New chat',
    fragment: document.createDocumentFragment(),
    isEmpty: true,
    draft: '',
    attachments: [],
    pendingQuestion: null,
  });
  persistSessionId();
  pendingQuestion = null;
  uploadedAttachments = [];
  elements.stream.replaceChildren(elements.welcome);
  elements.welcome.hidden = false;
  refreshGreeting();
  elements.prompt.value = '';
  elements.prompt.style.height = 'auto';
  renderAttachments();
  setConversationEmpty(true);
  updateSendButton();
  persistDraft();
  renderChatHistory();
  elements.artifactsList.replaceChildren();
  const empty = document.createElement('p');
  empty.className = 'artifacts-empty';
  empty.textContent = 'Generated files for this chat will appear here.';
  elements.artifactsList.append(empty);
  refreshArtifacts();
  refreshAgentActivity();
  elements.prompt.focus();
}

async function initialize() {
  const [tokensResponse, contractResponse] = await Promise.all([
    fetch('/web/design-tokens.json'),
    fetch('/web/api-contract.json'),
  ]);
  if (!tokensResponse.ok || !contractResponse.ok) throw new Error('Could not load the Hsx interface contract.');
  apiContract = await contractResponse.json();
  try {
    const response = await apiFetch(apiContract.endpoints.conversations);
    if (response.ok) {
      const data = await response.json();
      for (const saved of data.conversations || []) {
        chatSessions.set(saved.session_id, {
          title: saved.title, fragment: document.createDocumentFragment(), isEmpty: false,
          hasMessages: false, saved: true, updatedAt: Date.parse(saved.updated) || 0,
          draft: '', attachments: [], pendingQuestion: null,
        });
      }
    }
  } catch { /* The sidebar will show local sessions until the server reconnects. */ }
  if (!chatSessions.has(sessionId)) {
    chatSessions.set(sessionId, {
      title: 'New chat', fragment: document.createDocumentFragment(), isEmpty: true,
      draft: safeStorageGet('sessionStorage', `hsx-draft-${sessionId}`), attachments: [], pendingQuestion: null,
    });
  }
  if (chatSessions.get(sessionId)?.saved) {
    await restoreConversation(sessionId);
    elements.stream.replaceChildren(elements.welcome, chatSessions.get(sessionId).fragment);
    chatSessions.get(sessionId).fragment = document.createDocumentFragment();
    elements.welcome.hidden = true;
    setConversationEmpty(false);
  }
  renderChatHistory();
  const savedTheme = safeStorageGet('localStorage', 'hsx-theme') || 'dark';
  document.documentElement.dataset.theme = savedTheme === 'system'
    ? (window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark')
    : savedTheme;
  document.getElementById('theme-setting').value = savedTheme;
  document.getElementById('language-setting').value = safeStorageGet('localStorage', 'hsx-language') || 'en';
  document.getElementById('voice-language-setting').value = safeStorageGet('localStorage', 'hsx-voice-language') || navigator.language || 'en-US';
  refreshGreeting();
  await refreshEmergencyStop();
  setupVoiceInput();
  setupComposerOptions();
  document.getElementById('sidebar-toggle').addEventListener('click', () => {
    if (elements.sidebar.classList.contains('is-open')) closeMobilePanels();
    else openMobilePanel(elements.sidebar);
  });
  document.getElementById('camera-toggle').addEventListener('click', () => {
    if (elements.cameraSidebar.classList.contains('is-open')) {
      stopCameraAndStandby();
      closeMobilePanels();
    } else {
      openMobilePanel(elements.cameraSidebar);
      refreshArtifacts();
    }
  });
  document.getElementById('camera-close').addEventListener('click', () => {
    stopCameraAndStandby();
    closeMobilePanels();
  });
  elements.cameraView.addEventListener('click', toggleCamera);
  elements.cameraView.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      toggleCamera();
    }
  });
  elements.panelBackdrop.addEventListener('click', () => {
    if (elements.cameraSidebar.classList.contains('is-open')) stopCameraAndStandby();
    closeMobilePanels();
  });
  document.getElementById('refresh-artifacts').addEventListener('click', refreshArtifacts);
  document.getElementById('camera-retry').addEventListener('click', retryCamera);
  document.getElementById('activity-toggle').addEventListener('click', (event) => {
    const button = event.currentTarget;
    const expanded = button.getAttribute('aria-expanded') === 'true';
    button.setAttribute('aria-expanded', String(!expanded));
    button.setAttribute('aria-label', expanded ? 'Expand agent activity' : 'Collapse agent activity');
    button.title = expanded ? 'Expand agent activity' : 'Collapse agent activity';
    elements.activityList.hidden = expanded;
    event.currentTarget.closest('.activity-panel').classList.toggle('is-expanded', !expanded);
  });
  document.getElementById('history-toggle').addEventListener('click', (event) => {
    const button = event.currentTarget;
    const expanded = button.getAttribute('aria-expanded') === 'true';
    button.setAttribute('aria-expanded', String(!expanded));
    button.setAttribute('aria-label', expanded ? 'Expand chat history' : 'Collapse chat history');
    button.title = expanded ? 'Expand chat history' : 'Collapse chat history';
    document.getElementById('chat-history-list').hidden = expanded;
    event.currentTarget.closest('.history-panel').classList.toggle('is-expanded', !expanded);
  });
  document.getElementById('chat-history-search').addEventListener('input', renderChatHistory);
  document.getElementById('connection-toggle').addEventListener('click', () => {
    const panel = document.getElementById('connections-panel');
    const button = document.getElementById('connection-toggle');
    toggleHeaderPopover(panel, button);
  });
  document.getElementById('settings-toggle').addEventListener('click', () => {
    closeHeaderPopovers();
    const dialog = document.getElementById('settings-dialog');
    settingsReturnFocus = document.activeElement;
    selectSettingsTab('general');
    dialog.showModal();
    dialog.querySelector('[aria-selected="true"]').focus();
  });
  document.getElementById('settings-close').addEventListener('click', () => {
    document.getElementById('settings-dialog').close();
  });
  document.getElementById('settings-dialog').addEventListener('click', (event) => {
    if (event.target === event.currentTarget) event.currentTarget.close();
  });
  document.getElementById('settings-dialog').addEventListener('close', () => {
    if (settingsReturnFocus?.isConnected) settingsReturnFocus.focus();
    settingsReturnFocus = null;
  });
  document.getElementById('settings-dialog').addEventListener('keydown', (event) => {
    if (event.key !== 'Tab') return;
    const focusable = [...event.currentTarget.querySelectorAll(
      'button:not([disabled]):not([hidden]), input:not([disabled]):not([hidden]), select:not([disabled]):not([hidden]), [tabindex="0"]',
    )].filter((item) => item.tabIndex >= 0 && item.getClientRects().length);
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  });
  document.querySelectorAll('[data-settings-tab]').forEach((tab) => {
    tab.addEventListener('click', () => selectSettingsTab(tab.dataset.settingsTab));
    tab.addEventListener('keydown', (event) => {
      const tabs = [...document.querySelectorAll('[data-settings-tab]')];
      const index = tabs.indexOf(tab);
      let next = index;
      if (['ArrowDown', 'ArrowRight'].includes(event.key)) next = (index + 1) % tabs.length;
      else if (['ArrowUp', 'ArrowLeft'].includes(event.key)) next = (index + tabs.length - 1) % tabs.length;
      else if (event.key === 'Home') next = 0;
      else if (event.key === 'End') next = tabs.length - 1;
      else return;
      event.preventDefault();
      selectSettingsTab(tabs[next].dataset.settingsTab, true);
    });
  });
  document.getElementById('theme-setting').addEventListener('change', (event) => {
    safeStorageSet('localStorage', 'hsx-theme', event.target.value);
    document.documentElement.dataset.theme = event.target.value === 'system'
      ? (window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark')
      : event.target.value;
  });
  document.getElementById('language-setting').addEventListener('change', (event) => {
    safeStorageSet('localStorage', 'hsx-language', event.target.value);
  });
  document.getElementById('voice-language-setting').addEventListener('change', (event) => {
    safeStorageSet('localStorage', 'hsx-voice-language', event.target.value);
  });
  document.getElementById('test-provider-health').addEventListener('click', testProviderHealth);
  document.getElementById('settings-open-camera').addEventListener('click', () => {
    document.getElementById('settings-dialog').close();
    openMobilePanel(elements.cameraSidebar);
    if (!cameraEnabled) toggleCamera();
  });
  document.getElementById('feedback-form').addEventListener('submit', (event) => {
    event.preventDefault();
    if (!pendingFeedback) return;
    submitFeedback(pendingFeedback.article, pendingFeedback.rating);
    pendingFeedback = null;
  });
  document.getElementById('feedback-cancel').addEventListener('click', () => {
    document.getElementById('feedback-dialog').close();
    pendingFeedback = null;
  });
  for (const id of ['save-feedback-setting', 'full-conversation-setting', 'file-contents-setting']) {
    document.getElementById(id).addEventListener('change', () => {
      updateFeedbackSettings().catch(() => {
        document.getElementById('feedback-count').textContent = 'Feedback settings could not be saved.';
      });
    });
  }
  document.getElementById('delete-feedback-button').addEventListener('click', async () => {
    if (!window.confirm('Delete all saved feedback from this computer? This cannot be undone.')) return;
    try {
      const response = await apiFetch(apiContract.endpoints.feedback_delete, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ confirm: true }),
      });
      if (!response.ok) throw new Error('Feedback could not be deleted.');
      await refreshFeedbackSettings();
    } catch {
      document.getElementById('feedback-count').textContent = 'Feedback could not be deleted.';
    }
  });
  document.getElementById('emergency-stop-button').addEventListener('click', async () => {
    if (!window.confirm('Emergency stop pauses all runs until you resume. Continue?')) return;
    await apiFetch(apiContract.endpoints.emergency_stop, { method: 'POST' });
    showEmergencyStop(true);
  });
  const resumeEmergencyStop = async () => {
    await apiFetch(apiContract.endpoints.resume, { method: 'POST' });
    showEmergencyStop(false);
  };
  document.getElementById('resume-button').addEventListener('click', resumeEmergencyStop);
  document.getElementById('banner-resume-button').addEventListener('click', resumeEmergencyStop);
  document.getElementById('notifications-toggle').addEventListener('click', async () => {
    const opening = toggleHeaderPopover(elements.notificationPanel, document.getElementById('notifications-toggle'));
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
  document.getElementById('clear-notifications').addEventListener('click', async () => {
    const response = await apiFetch(apiContract.endpoints.notifications, { method: 'DELETE' });
    if (response.ok) await refreshNotifications();
    else elements.notificationsList.textContent = 'Notifications could not be cleared.';
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') {
      closeMobilePanels();
      closeHeaderPopovers();
    }
  });
  persistSessionId();
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
    if (cameraEnabled) stopCameraAndStandby();
    else stopCameraStream();
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
      if (cameraEnabled) stopCameraAndStandby();
      else stopCameraStream();
      pauseCameraVision();
      abortNotificationStream();
    } else {
      cameraRetryDelay = 500;
      refreshConnections();
      startNotificationStream();
    }
  });
  if ('IntersectionObserver' in window) {
    cameraVisionObserver = new IntersectionObserver((entries) => {
      const entry = entries[entries.length - 1];
      if (entry?.isIntersecting) startCameraVision();
      else pauseCameraVision();
    });
    cameraVisionObserver.observe(elements.cameraView);
  }
  await refreshConnections();
  refreshFeedbackSettings();
  refreshArtifacts();
  refreshAgentActivity();
  startNotificationStream();
  window.setInterval(refreshConnections, 15000);
  window.setInterval(refreshAgentActivity, 5000);
  window.setInterval(updateActivityElapsed, 1000);
  window.setInterval(updateMessageTimes, 60000);
  updateCameraClock();
  window.setInterval(updateCameraClock, 1000);
  document.body.dataset.ready = 'true';
}

initialize().catch(() => {
  document.body.dataset.ready = 'error';
  elements.connection.replaceChildren();
  const connectionDot = document.createElement('span');
  connectionDot.className = 'connection-dot';
  connectionDot.dataset.state = 'disconnected';
  elements.connection.append(connectionDot, document.createTextNode(' Offline'));
  document.getElementById('connection-summary').textContent = 'Offline';
});
