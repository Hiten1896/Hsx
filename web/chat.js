let sessionId = makeSessionId();
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
let artifactViewerFile = null;
let artifactViewerTab = 'preview';
let artifactViewerText = '';
let artifactViewerObjectUrl = '';
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
const browserCameraFeeds = [];
const MAX_CAMERA_LIVE_DELAY_MS = 1000;
// Remote HP PC camera. The server decides whether one is configured (data-remote-camera) and
// relays the stream through /api/camera/remote-stream, so the browser never sees the URL or token.
const REMOTE_CAMERA_ENABLED = Boolean((document.querySelector('.app-shell')?.dataset.remoteCamera || '').trim());
const MAX_REMOTE_CAMERA_DELAY_MS = 5000;
let remoteCameraMode = false;
let remoteResumeOnVisible = false;

function liveDelayLimitMs() {
  return remoteCameraMode ? MAX_REMOTE_CAMERA_DELAY_MS : MAX_CAMERA_LIVE_DELAY_MS;
}

function formatFeedTime(milliseconds) {
  return new Date(milliseconds).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

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
  providerNotice: document.getElementById('provider-notice'),
  dropHint: document.getElementById('drop-hint'),
  sidebar: document.getElementById('app-sidebar'),
  cameraSidebar: document.getElementById('camera-sidebar'),
  cameraFeedsPanel: document.getElementById('camera-feeds-panel'),
  panelBackdrop: document.getElementById('panel-backdrop'),
  artifactsList: document.getElementById('artifacts-list'),
  activityList: document.getElementById('agent-activity-strip'),
  notificationPanel: document.getElementById('notifications-panel'),
  notificationsList: document.getElementById('notifications-list'),
  permissionDialog: document.getElementById('permission-dialog'),
  permissionDialogContent: document.getElementById('permission-dialog-content'),
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

function applyTheme(preference) {
  const resolved = preference === 'system'
    ? (window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark')
    : preference;
  document.documentElement.dataset.theme = resolved;
  document.querySelector('meta[name="theme-color"]').content = resolved === 'light' ? '#ffffff' : '#1e1e1e';
}

function makeSessionId() {
  return `web-${globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(16).slice(2)}`}`;
}

function makeClientMessageId() {
  return globalThis.crypto?.randomUUID?.()
    || `client-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function escapeHtml(value) {
  return String(value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
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
    decorateMarkdownCodeBlocks(container);
    container.querySelectorAll('a[href]').forEach((link) => {
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
    });
  } else {
    renderMarkdownFallback(source, container);
  }
}

function renderMarkdownFallback(source, container) {
  const fencedBlocks = /```([^\r\n`]*)\r?\n([\s\S]*?)```/g;
  let cursor = 0;
  let match;
  while ((match = fencedBlocks.exec(source))) {
    if (match.index > cursor) {
      container.append(document.createTextNode(source.slice(cursor, match.index)));
    }
    const pre = document.createElement('pre');
    const code = document.createElement('code');
    const language = match[1].trim().split(/\s+/, 1)[0].replace(/[^a-zA-Z0-9_+-]/g, '');
    if (language) code.classList.add(`language-${language}`);
    code.textContent = match[2].replace(/\n$/, '');
    pre.append(code);
    container.append(pre);
    cursor = fencedBlocks.lastIndex;
  }
  if (cursor < source.length) container.append(document.createTextNode(source.slice(cursor)));
  if (cursor === 0) container.textContent = source;
  else decorateMarkdownCodeBlocks(container);
}

function decorateMarkdownCodeBlocks(container) {
  container.querySelectorAll('pre').forEach((pre) => {
    if (pre.parentElement?.classList.contains('markdown-code-block')) return;
    const code = pre.querySelector('code');
    if (!code) return;
    const languageClass = [...code.classList].find((name) => name.startsWith('language-'));
    const language = languageClass ? languageClass.slice('language-'.length) : 'code';
    const wrapper = document.createElement('section');
    wrapper.className = 'markdown-code-block';
    const header = document.createElement('header');
    header.className = 'markdown-code-header';
    const label = document.createElement('span');
    label.textContent = language;
    const copy = document.createElement('button');
    copy.type = 'button';
    copy.className = 'markdown-code-copy';
    copy.setAttribute('aria-label', 'Copy code');
    copy.title = 'Copy code';
    copy.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="8" y="8" width="12" height="13" rx="2"></rect><path d="M16 8V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h3"></path></svg>';
    copy.addEventListener('click', async () => {
      try {
        await navigator.clipboard.writeText(code.textContent || '');
        copy.title = 'Copied';
        copy.setAttribute('aria-label', 'Code copied');
      } catch {
        copy.title = 'Copy unavailable';
      }
      window.setTimeout(() => {
        copy.title = 'Copy code';
        copy.setAttribute('aria-label', 'Copy code');
      }, 1400);
    });
    header.append(label, copy);
    pre.replaceWith(wrapper);
    wrapper.append(header, pre);
  });
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
      ? 'No matching chats.' : 'No chats yet. Start with New chat.';
    list.replaceChildren(empty);
    return;
  }
  const fragment = document.createDocumentFragment();
  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  let currentGroup = '';
  for (const [id, session] of sessions) {
    const updated = new Date(session.updatedAt || now);
    const age = Math.floor((today - new Date(updated.getFullYear(), updated.getMonth(), updated.getDate())) / 86400000);
    const group = age <= 0 ? 'Today' : age === 1 ? 'Yesterday' : age <= 7 ? 'Previous 7 days' : 'Older';
    if (group !== currentGroup) {
      const heading = document.createElement('h2');
      heading.className = 'chat-history-group';
      heading.textContent = group;
      fragment.append(heading);
      currentGroup = group;
    }
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
    rename.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m14 5 5 5M4 20l4-.8L20 7a2.1 2.1 0 0 0-3-3L5 16l-1 4Z"></path></svg>';
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
    remove.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h16M9 7V4h6v3m3 0-1 13H7L6 7m4 3v6m4-6v6"></path></svg>';
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
        created_at: article.dataset.createdAt || '',
        attachments: JSON.parse(article.dataset.attachments || '[]'),
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
        attachments: message.attachments,
      });
      article.dataset.createdAt = message.created_at || saved.created || article.dataset.createdAt;
      if (message.feedback) article.querySelector(`[data-feedback="${message.feedback}"]`)?.setAttribute('aria-pressed', 'true');
      session.fragment.append(article);
    }
  } finally { restoringConversation = false; }
  session.hasMessages = session.fragment.childElementCount > 0;
  return session;
}

async function switchChatSession(nextSessionId) {
  if (nextSessionId === sessionId) return;
  if (cameraEnabled) stopCameraAndStandby();
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
  elements.send.hidden = submitting;
  elements.send.disabled = submitting || !hasComposerContent();
  elements.stop.hidden = !submitting;
}

function setConnection(id, labelId, value, state) {
  const dot = document.getElementById(id);
  const label = document.getElementById(labelId);
  if (dot) dot.dataset.state = state;
  if (label) { label.textContent = value; label.title = value; }
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

function renderMessageAttachments(article, attachments) {
  const container = article.querySelector('.message-attachments');
  if (!container || !attachments.length) return;
  container.hidden = false;
  for (const attachment of attachments) {
    const path = String(attachment.path || '');
    const name = String(attachment.name || path.split(/[\\/]/).pop() || 'Attached file');
    const imageFile = /\.(?:png|jpe?g|gif|webp|bmp)$/i.test(name);
    const button = document.createElement('button');
    button.type = 'button';
    button.className = imageFile ? 'message-attachment message-attachment-image' : 'message-attachment';
    button.title = `Open ${name}`;
    button.setAttribute('aria-label', `Open attached file ${name}`);
    button.addEventListener('click', () => openActivityFileViewer(path));
    if (imageFile) {
      const image = document.createElement('img');
      image.alt = name;
      image.loading = 'lazy';
      const query = new URLSearchParams({ path });
      void apiFetch(`/api/files/content?${query}`)
        .then(async (response) => {
          if (!response.ok) throw new Error('Attachment preview is unavailable.');
          image.src = URL.createObjectURL(await response.blob());
          activityImageObjectUrls.add(image.src);
        })
        .catch(() => { image.alt = `${name} (preview unavailable)`; });
      button.append(image);
    } else {
      const type = document.createElement('span');
      type.className = 'message-attachment-type';
      type.textContent = name.includes('.') ? name.split('.').pop().slice(0, 5).toUpperCase() : 'FILE';
      button.append(type);
    }
    const caption = document.createElement('span');
    caption.className = 'message-attachment-name';
    caption.textContent = name;
    const size = document.createElement('small');
    size.textContent = String(attachment.sizeLabel || 'Attachment');
    const label = document.createElement('span');
    label.className = 'message-attachment-label';
    label.append(caption, size);
    button.append(label);
    container.append(button);
  }
}

function bindMessageFeedback(article, jobId) {
  const feedback = article.querySelector('.message-feedback');
  if (!feedback) return;
  feedback.hidden = false;
  feedback.dataset.jobId = jobId;
  feedback.querySelectorAll('[data-feedback]').forEach((button) => {
    button.disabled = false;
    button.removeAttribute('title');
    button.addEventListener('click', () => {
      void submitFeedback(article, button.dataset.feedback);
    });
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
  const attachments = Array.isArray(options.attachments)
    ? options.attachments
      .filter((item) => item && typeof item.name === 'string' && typeof item.path === 'string')
      .map((item) => ({ name: item.name, path: item.path, sizeLabel: String(item.sizeLabel || 'Attachment') }))
    : [];
  article.dataset.attachments = JSON.stringify(attachments);
  renderMessageAttachments(article, attachments);
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
  if (role === 'assistant') {
    if (options.noFeedback) {
      feedback.hidden = true;
    } else if (jobId) {
      bindMessageFeedback(article, jobId);
    } else {
      feedback.hidden = false;
      feedback.querySelectorAll('[data-feedback]').forEach((button) => {
        button.disabled = true;
        button.title = 'Feedback is unavailable for this message';
      });
    }
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
  const activity = fragment.querySelector('.activity-trace');
  activity.querySelector('.activity-thinking span').textContent = label.replace(/…$/, '');
  const summary = activity.querySelector('.activity-group-summary');
  summary.addEventListener('click', () => {
    activity.dataset.groupsExpanded = String(summary.getAttribute('aria-expanded') !== 'true');
    updateActivityGrouping(activity);
  });
  const showSteps = activity.querySelector('.activity-show-steps');
  showSteps.addEventListener('click', () => {
    activity.dataset.groupsExpanded = String(showSteps.getAttribute('aria-expanded') !== 'true');
    updateActivityGrouping(activity);
  });
  elements.stream.appendChild(fragment);
  const thinkingStarted = Date.now();
  const thinkingBase = label.replace(/…$/, '');
  const thinkingTimer = setInterval(() => {
    const span = activity.querySelector('.activity-thinking span');
    if (!span || !activity.isConnected) { clearInterval(thinkingTimer); return; }
    const seconds = Math.round((Date.now() - thinkingStarted) / 1000);
    span.textContent = seconds >= 3 ? `${thinkingBase} · ${seconds}s` : thinkingBase;
  }, 1000);
  scrollToLatest();
  return activity;
}

const activityImageObjectUrls = new Set();
const artifactViewerObjectUrls = new Set();

async function appendInlineCameraSnapshot(article) {
  const content = article?.querySelector('.message-content');
  if (!content || article.dataset.cameraSnapshotRendered === 'true') return;
  article.dataset.cameraSnapshotRendered = 'true';
  try {
    const response = await apiFetch(apiContract.endpoints.camera_snapshot);
    if (!response.ok) throw new Error(`The snapshot endpoint returned HTTP ${response.status}.`);
    const blob = await response.blob();
    if (!blob.type.startsWith('image/')) throw new Error('The snapshot endpoint did not return an image.');
    const image = document.createElement('img');
    image.className = 'message-inline-camera-snapshot';
    image.alt = 'Camera snapshot';
    image.src = URL.createObjectURL(blob);
    activityImageObjectUrls.add(image.src);
    content.append(image);
    scrollToLatest();
  } catch (error) {
    const notice = document.createElement('p');
    notice.className = 'camera-snapshot-error';
    notice.textContent = `Camera snapshot unavailable: ${error.message}`;
    content.append(notice);
  }
}

function activityIcon(name, extraClass = '') {
  const icon = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  icon.classList.add('activity-icon');
  if (extraClass) icon.classList.add(extraClass);
  icon.setAttribute('aria-hidden', 'true');
  const use = document.createElementNS('http://www.w3.org/2000/svg', 'use');
  use.setAttribute('href', `#activity-icon-${name || 'gear'}`);
  icon.append(use);
  return icon;
}

function appendActionButton(parent, label, action) {
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'activity-detail-action';
  button.textContent = label;
  button.addEventListener('click', action);
  parent.append(button);
  return button;
}

function activityDuration(value) {
  const seconds = Math.max(0, Math.round(Number(value) / 1000));
  return seconds < 60 ? `${seconds}s` : `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
}

function toggleActivityDetails(step, toggle, details, expanded = !details.hidden) {
  details.hidden = !expanded;
  toggle.setAttribute('aria-expanded', String(expanded));
  step.dataset.expanded = String(expanded);
  step.classList.toggle('collapsed', !expanded);
}

function getArtifactIcon(type) {
  const t = (type || '').toLowerCase();
  if (['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg', 'bmp'].includes(t)) {
    return '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"></rect><circle cx="8.5" cy="8.5" r="1.5"></circle><polyline points="21 15 16 10 5 21"></polyline></svg>';
  }
  if (['xlsx', 'csv', 'tsv'].includes(t)) {
    return '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"></rect><line x1="3" y1="9" x2="21" y2="9"></line><line x1="3" y1="15" x2="21" y2="15"></line><line x1="9" y1="3" x2="9" y2="21"></line><line x1="15" y1="3" x2="15" y2="21"></line></svg>';
  }
  if (['py', 'js', 'ts', 'jsx', 'tsx', 'html', 'htm', 'css', 'json', 'yaml', 'yml', 'toml', 'rs', 'go', 'java', 'c', 'cpp', 'cs', 'sh', 'sql'].includes(t)) {
    return '<svg viewBox="0 0 24 24" aria-hidden="true"><polyline points="16 18 22 12 16 6"></polyline><polyline points="8 6 2 12 8 18"></polyline></svg>';
  }
  if (['zip', 'tar', 'gz', '7z'].includes(t)) {
    return '<svg viewBox="0 0 24 24" aria-hidden="true"><polyline points="21 8 21 21 3 21 3 8"></polyline><rect x="1" y="3" width="22" height="5"></rect><line x1="10" y1="12" x2="14" y2="12"></line></svg>';
  }
  return '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline><line x1="16" y1="13" x2="8" y2="13"></line><line x1="16" y1="17" x2="8" y2="17"></line></svg>';
}

function formatArtifactTime(isoString) {
  if (!isoString) return '';
  try {
    const date = new Date(isoString);
    if (Number.isNaN(date.getTime())) return '';
    return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  } catch {
    return '';
  }
}

function highlightCode(rawCode, ext = '') {
  const escaped = String(rawCode || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
  const re = /((\/\/[^\n]*|\/\*[\s\S]*?\*\/|#[^\n]*)|("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|`(?:\\.|[^`\\])*`)|\b(def|class|return|import|from|as|if|elif|else|for|while|try|except|finally|with|async|await|function|const|let|var|yield|export|default|new|this|typeof|instanceof|switch|case|break|continue|None|True|False|true|false|null|undefined)\b|\b(\d+(?:\.\d+)?)\b)/g;
  const highlighted = escaped.replace(re, (match, p1, comment, string, keyword, number) => {
    if (comment) return `<span class="tok-comment">${comment}</span>`;
    if (string) return `<span class="tok-string">${string}</span>`;
    if (keyword) return `<span class="tok-keyword">${keyword}</span>`;
    if (number) return `<span class="tok-number">${number}</span>`;
    return match;
  });
  const lines = highlighted.split('\n');
  return lines.map((line, idx) => `<div class="code-line"><span class="line-num">${idx + 1}</span><span class="line-text">${line || ' '}</span></div>`).join('');
}

async function openArtifactViewer(file, initialTab = 'preview') {
  if (window.matchMedia('(max-width: 700px)').matches) openMobilePanel(elements.cameraSidebar);
  artifactViewerFile = file;
  artifactViewerTab = initialTab;
  const dialog = document.getElementById('activity-file-viewer');
  const title = document.getElementById('activity-viewer-title');
  const versionSelect = document.getElementById('artifact-version-select');
  const tabsNav = document.getElementById('artifact-viewer-tabs');

  title.textContent = file.name || 'Artifact';

  const versions = Array.isArray(file.versions) && file.versions.length > 1 ? file.versions : [file];
  if (versions.length > 1) {
    versionSelect.hidden = false;
    versionSelect.innerHTML = versions.map((v) => `<option value="${v.path}">v${v.version || 1}</option>`).join('');
    versionSelect.value = file.path;
    versionSelect.onchange = () => {
      const selected = versions.find((v) => v.path === versionSelect.value) || file;
      artifactViewerFile = { ...file, ...selected };
      title.textContent = artifactViewerFile.name || file.name;
      void renderArtifactViewerTab();
    };
  } else {
    versionSelect.hidden = true;
  }

  tabsNav.querySelectorAll('button').forEach((btn) => {
    btn.setAttribute('aria-selected', String(btn.dataset.artifactTab === artifactViewerTab));
  });

  if (!dialog.open) dialog.showModal();
  await renderArtifactViewerTab();
}

async function renderArtifactViewerTab() {
  const file = artifactViewerFile;
  if (!file) return;
  const content = document.getElementById('activity-viewer-content');
  artifactViewerObjectUrls.forEach((url) => URL.revokeObjectURL(url));
  artifactViewerObjectUrls.clear();
  content.replaceChildren();

  if (artifactViewerTab === 'details') {
    const dl = document.createElement('dl');
    dl.className = 'artifact-details-grid';
    const fields = [
      ['Name', file.name],
      ['Path', file.path],
      ['Type', (file.type || '').toUpperCase()],
      ['Version', `v${file.version || 1}`],
      ['Created', file.created ? new Date(file.created).toLocaleString() : '—'],
      ['Chat', chatSessions.get(file.session_id)?.title || file.session_id || 'Current session'],
    ];
    for (const [key, val] of fields) {
      const dt = document.createElement('dt');
      dt.textContent = key;
      const dd = document.createElement('dd');
      dd.textContent = String(val || '—');
      dl.append(dt, dd);
    }
    content.append(dl);
    return;
  }

  const isImage = /\.(?:png|jpe?g|gif|webp|bmp|svg)$/i.test(file.path || file.name);
  if (file.imageId) {
    const response = await apiFetch(`/api/activity/image?id=${encodeURIComponent(file.imageId)}&thumbnail=0`);
    if (response.ok) {
      const img = document.createElement('img');
      img.src = URL.createObjectURL(await response.blob());
      artifactViewerObjectUrls.add(img.src);
      content.append(img);
      return;
    }
  }

  if (artifactViewerTab === 'preview' && isImage) {
    const query = new URLSearchParams({ path: file.path });
    const response = await apiFetch(`/api/files/content?${query}`);
    if (response.ok) {
      const img = document.createElement('img');
      img.src = URL.createObjectURL(await response.blob());
      artifactViewerObjectUrls.add(img.src);
      content.append(img);
      return;
    }
  }

  if (artifactViewerTab === 'preview' && /\.(?:html|htm)$/i.test(file.path || file.name)) {
    const query = new URLSearchParams({ path: file.path });
    const iframe = document.createElement('iframe');
    iframe.className = 'artifact-preview-frame';
    iframe.src = `/api/files/content?${query}`;
    iframe.sandbox = 'allow-scripts allow-same-origin';
    content.append(iframe);
    return;
  }

  if (artifactViewerTab === 'preview' && /\.pdf$/i.test(file.path || file.name)) {
    const query = new URLSearchParams({ path: file.path });
    const response = await apiFetch(`/api/files/content?${query}`);
    if (!response.ok) {
      const error = document.createElement('p');
      error.className = 'artifacts-empty';
      error.textContent = 'PDF preview is unavailable.';
      content.append(error);
      return;
    }
    const obj = document.createElement('object');
    obj.data = URL.createObjectURL(await response.blob());
    artifactViewerObjectUrls.add(obj.data);
    obj.type = 'application/pdf';
    obj.className = 'artifact-preview-object';
    const fallback = document.createElement('p');
    fallback.append('PDF preview unavailable. ');
    const link = document.createElement('a');
    link.href = obj.data;
    link.download = file.name;
    link.textContent = `Download ${file.name}`;
    fallback.append(link);
    obj.append(fallback);
    content.append(obj);
    return;
  }

  try {
    const query = new URLSearchParams({ path: file.path });
    const response = await apiFetch(`/api/files/preview?${query}`);
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'File preview is unavailable.');
    artifactViewerText = result.content || '';
    if (artifactViewerTab === 'code') {
      const codeWrap = document.createElement('div');
      codeWrap.className = 'code-viewer';
      codeWrap.innerHTML = highlightCode(artifactViewerText, file.type);
      content.append(codeWrap);
    } else {
      const pre = document.createElement('pre');
      pre.className = 'artifact-preview-text';
      pre.textContent = artifactViewerText;
      content.append(pre);
    }
  } catch (error) {
    const message = document.createElement('p');
    message.className = 'artifacts-empty';
    message.textContent = error.message || 'File preview is unavailable.';
    content.append(message);
  }
}

async function openActivityFileViewer(path, imageId = '') {
  const name = path ? path.replace(/\\/g, '/').split('/').pop() : 'Preview';
  const type = path ? (path.split('.').pop() || 'file') : 'file';
  const file = { name, path: path || '', type, created: new Date().toISOString(), versions: [] };
  if (imageId) file.imageId = imageId;
  await openArtifactViewer(file, 'preview');
}

function appendDiffCard(details, data) {
  const diff = data.diff;
  const card = document.createElement('section');
  card.className = 'activity-diff-card';
  const header = document.createElement('header');
  const file = document.createElement('button');
  file.type = 'button';
  file.className = 'activity-file-link';
  file.textContent = diff.path || data.file_path || data.target;
  file.title = file.textContent;
  file.addEventListener('click', () => openActivityFileViewer(diff.path || data.file_path));
  const counts = document.createElement('span');
  counts.className = 'activity-diff-counts';
  counts.append(
    Object.assign(document.createElement('span'), { className: 'activity-added-count', textContent: `+${diff.added || 0}` }),
    document.createTextNode(' '),
    Object.assign(document.createElement('span'), { className: 'activity-removed-count', textContent: `-${diff.removed || 0}` }),
  );
  header.append(file, counts);
  appendActionButton(header, 'Copy', async () => {
    const text = (diff.hunks || []).flatMap((hunk) => [
      hunk.header,
      ...(hunk.lines || []).map((line) => `${line.kind === 'added' ? '+' : line.kind === 'removed' ? '-' : ' '}${line.text}`),
    ]).join('\n');
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      details.querySelector('.activity-copy-status').textContent = 'Could not copy the diff.';
      return;
    }
    details.querySelector('.activity-copy-status').textContent = 'Diff copied.';
  });
  card.append(header);
  if (diff.binary) {
    const binary = document.createElement('p');
    binary.textContent = `Binary file, ${Math.max(1, Math.round((diff.size_bytes || 0) / 1024))} KB`;
    card.append(binary);
    appendActionButton(card, 'Open', () => openActivityFileViewer(diff.path || data.file_path));
  } else {
    const list = document.createElement('div');
    list.className = 'activity-diff-lines';
    const rows = (diff.hunks || []).flatMap((hunk) => [
      { kind: 'header', text: hunk.header },
      ...(hunk.lines || []).map((line) => ({ ...line, kind: line.kind })),
    ]);
    rows.forEach((line, index) => {
      const row = document.createElement('div');
      row.className = `activity-diff-line ${line.kind}`;
      if (line.kind === 'header') {
        row.textContent = line.text;
      } else {
        const number = document.createElement('span');
        number.className = 'activity-diff-line-number';
        number.textContent = line.kind === 'added'
          ? String(line.new_line || '')
          : line.kind === 'removed'
            ? String(line.old_line || '')
            : `${line.old_line || ''}:${line.new_line || ''}`;
        const value = document.createElement('code');
        const prefix = line.kind === 'added' ? '+' : line.kind === 'removed' ? '-' : ' ';
        value.textContent = `${prefix}${line.text || ''}`;
        row.append(number, value);
      }
      if (index >= 200) row.hidden = true;
      list.append(row);
    });
    card.append(list);
    if (rows.length > 200) {
      appendActionButton(card, 'Show more', (event) => {
        const hiddenRows = [...list.children].filter((row) => row.hidden);
        const showMore = hiddenRows.length > 0;
        hiddenRows.forEach((row) => { row.hidden = !showMore; });
        event.currentTarget.textContent = showMore ? 'Show less' : 'Show more';
      });
    }
    if (diff.truncated) {
      const notice = document.createElement('p');
      notice.textContent = 'The diff reached its storage limit; additional lines are not available.';
      card.append(notice);
    }
  }
  const copyStatus = document.createElement('span');
  copyStatus.className = 'activity-copy-status';
  copyStatus.setAttribute('role', 'status');
  card.append(copyStatus);
  details.append(card);
}

function appendActivityImages(details, images) {
  if (!Array.isArray(images) || !images.length) return;
  const gallery = document.createElement('div');
  gallery.className = 'activity-image-gallery';
  images.slice(0, 5).forEach((item) => {
    if (!item || typeof item.id !== 'string') return;
    const figure = document.createElement('figure');
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'activity-image-open';
    button.setAttribute('aria-label', `View image ${item.path || ''}`);
    const image = document.createElement('img');
    image.alt = item.path || 'Viewed image';
    button.append(image);
    button.addEventListener('click', () => openActivityFileViewer(item.path, item.id));
    const caption = document.createElement('figcaption');
    caption.textContent = item.path || 'Image';
    figure.append(button, caption);
    gallery.append(figure);
    apiFetch(`/api/activity/image?id=${encodeURIComponent(item.id)}&thumbnail=1`)
      .then((response) => {
        if (!response.ok) throw new Error('Thumbnail unavailable.');
        return response.blob();
      })
      .then((blob) => {
        image.src = URL.createObjectURL(blob);
        activityImageObjectUrls.add(image.src);
      })
      .catch(() => { image.alt = `Preview unavailable: ${item.path || 'image'}`; });
  });
  if (images.length > 5) {
    const more = document.createElement('span');
    more.className = 'activity-image-more';
    more.textContent = `+${images.length - 5} more`;
    gallery.append(more);
  }
  details.append(gallery);
}

function appendActivityArtifact(trace, data) {
  if (data.artifact !== true || !data.file_path || !trace) return;
  const steps = trace.querySelector('.activity-steps');
  if (steps.querySelector(`[data-tool-call-id="${CSS.escape(String(data.tool_call_id || ''))}"].activity-artifact-card`)) return;
  const path = String(data.file_path);
  const name = path.replace(/\\/g, '/').split('/').pop() || 'Generated file';
  const type = name.includes('.') ? name.split('.').pop().toUpperCase() : 'FILE';
  const card = document.createElement('button');
  card.type = 'button';
  card.className = 'activity-artifact-card';
  card.dataset.toolCallId = String(data.tool_call_id || '');
  card.setAttribute('aria-label', `Open generated ${type} file ${name}`);
  const icon = document.createElement('span');
  icon.className = 'activity-artifact-icon';
  icon.append(activityIcon('file-plus'));
  const label = document.createElement('span');
  label.className = 'activity-artifact-label';
  const title = document.createElement('strong');
  title.textContent = name;
  const subtitle = document.createElement('small');
  subtitle.textContent = 'Generated file';
  label.append(title, subtitle);
  const typeChip = document.createElement('span');
  typeChip.className = 'activity-artifact-type';
  typeChip.textContent = type;
  const time = document.createElement('time');
  time.dateTime = new Date().toISOString();
  time.textContent = 'Just now';
  card.append(icon, label, typeChip, time);
  card.addEventListener('click', () => openActivityFileViewer(path));
  steps.append(card);
}

function humanizeTool(name) {
  return String(name || 'tool').replace(/[_-]+/g, ' ').trim();
}

function appendIOBlock(parent, label, text, { prompt = '', badge = '', clampLines = 12 } = {}) {
  const wrap = document.createElement('section');
  wrap.className = 'activity-io';
  const head = document.createElement('header');
  const title = document.createElement('span');
  title.className = 'activity-io-label';
  title.textContent = label;
  head.append(title);
  if (badge) {
    const pill = document.createElement('span');
    pill.className = 'activity-exit';
    pill.dataset.ok = String(/^(exit )?0$/.test(String(badge).replace(/^exit\s*/i, '')) || badge === 'ok');
    pill.textContent = badge;
    head.append(pill);
  }
  const copy = document.createElement('button');
  copy.type = 'button';
  copy.className = 'activity-io-copy';
  copy.textContent = 'Copy';
  copy.addEventListener('click', async (event) => {
    event.stopPropagation();
    try { await navigator.clipboard.writeText(text); copy.textContent = 'Copied'; }
    catch { copy.textContent = 'Could not copy'; }
    setTimeout(() => { copy.textContent = 'Copy'; }, 1500);
  });
  head.append(copy);
  const pre = document.createElement('pre');
  const lines = String(text).split('\n');
  const clamped = lines.length > clampLines;
  const shown = clamped ? lines.slice(0, clampLines).join('\n') : String(text);
  pre.textContent = prompt + shown;
  wrap.append(head, pre);
  if (clamped) {
    const more = document.createElement('button');
    more.type = 'button';
    more.className = 'activity-io-more';
    more.textContent = `+${lines.length - clampLines} more lines`;
    let open = false;
    more.addEventListener('click', (event) => {
      event.stopPropagation();
      open = !open;
      pre.textContent = prompt + (open ? String(text) : shown);
      more.textContent = open ? 'Show less' : `+${lines.length - clampLines} more lines`;
    });
    wrap.append(more);
  }
  parent.append(wrap);
  return wrap;
}

function appendCapabilityCards(details, groups) {
  if (!Array.isArray(groups)) return;
  const valid = groups.filter((group) => group && typeof group.name === 'string' && Array.isArray(group.items));
  const all = valid.flatMap((group) => group.items.filter((item) => item && typeof item.name === 'string'));
  const ready = all.filter((item) => /works now|ready|ok/i.test(String(item.status || ''))).length;
  const root = document.createElement('div');
  root.className = 'activity-capability-groups';
  const bar = document.createElement('div');
  bar.className = 'activity-capability-bar';
  const summary = document.createElement('span');
  summary.className = 'activity-capability-summary';
  summary.textContent = `${all.length} tools · ${ready} ready · ${all.length - ready} need setup`;
  const search = document.createElement('input');
  search.type = 'search';
  search.placeholder = 'Filter tools';
  search.setAttribute('aria-label', 'Filter tools');
  search.className = 'activity-capability-search';
  bar.append(summary, search);
  root.append(bar);
  const rows = [];
  valid.forEach((group) => {
    const section = document.createElement('section');
    section.className = 'activity-capability-group';
    const heading = document.createElement('h4');
    heading.textContent = group.name;
    section.append(heading);
    group.items.forEach((item) => {
      if (!item || typeof item.name !== 'string') return;
      const row = document.createElement('article');
      row.className = 'activity-capability-card';
      const dot = document.createElement('i');
      dot.className = 'activity-capability-dot';
      dot.dataset.status = item.status || 'needs setup';
      const title = document.createElement('strong');
      title.textContent = item.name;
      const status = document.createElement('span');
      status.className = 'activity-capability-status';
      status.dataset.status = item.status || 'needs setup';
      status.textContent = item.status || 'needs setup';
      const detail = document.createElement('p');
      detail.textContent = item.detail || 'No setup details are available.';
      row.append(dot, title, status, detail);
      row.dataset.search = `${item.name} ${item.detail || ''} ${group.name}`.toLowerCase();
      rows.push({ row, section });
      section.append(row);
    });
    root.append(section);
  });
  search.addEventListener('input', () => {
    const query = search.value.trim().toLowerCase();
    rows.forEach(({ row }) => { row.hidden = Boolean(query) && !row.dataset.search.includes(query); });
    root.querySelectorAll('.activity-capability-group').forEach((section) => {
      section.hidden = ![...section.querySelectorAll('.activity-capability-card')].some((row) => !row.hidden);
    });
  });
  search.addEventListener('click', (event) => event.stopPropagation());
  details.append(root);
}

function updateActivityGrouping(trace) {
  const completed = [...trace.querySelectorAll('.activity-step[data-status="completed"]')];
  const runningOrFailed = [...trace.querySelectorAll('.activity-step')]
    .filter((step) => ['running', 'failed', 'waiting'].includes(step.dataset.status));
  const summary = trace.querySelector('.activity-group-summary');
  const toggle = trace.querySelector('.activity-show-steps');
  if (completed.length >= 3) {
    summary.hidden = false;
    summary.textContent = `Used ${completed.length} tools`;
    const expanded = trace.dataset.groupsExpanded === 'true';
    summary.setAttribute('aria-expanded', String(expanded));
    completed.forEach((step) => { step.hidden = !expanded; });
    runningOrFailed.forEach((step) => { step.hidden = false; });
    toggle.hidden = false;
    toggle.textContent = expanded ? 'Show fewer steps' : 'Show steps';
    toggle.setAttribute('aria-expanded', String(expanded));
  } else {
    summary.hidden = true;
    completed.forEach((step) => { step.hidden = false; });
    runningOrFailed.forEach((step) => { step.hidden = false; });
    toggle.hidden = true;
  }
}

function renderActivityEvent(trace, event, data) {
  if (!trace || !data) return;
  if (event === 'activity_narration') {
    const narration = document.createElement('p');
    narration.className = 'activity-narration';
    narration.textContent = data.text || '';
    trace.querySelector('.activity-steps').append(narration);
    return;
  }
  if (!['tool_start', 'tool_end'].includes(event)) return;
  trace.querySelector('.activity-thinking')?.remove();
  const steps = trace.querySelector('.activity-steps');
  let step = [...steps.querySelectorAll('.activity-step')]
    .find((item) => item.dataset.toolCallId === data.tool_call_id);
  if (!step) {
    step = document.createElement('article');
    step.className = 'activity-step';
    step.dataset.toolCallId = data.tool_call_id || '';
    const toggle = document.createElement('button');
    toggle.type = 'button';
    toggle.className = 'activity-row-toggle';
    toggle.setAttribute('aria-expanded', 'false');
    const icon = activityIcon(data.icon);
    icon.classList.add('activity-step-icon');
    const toolLabel = document.createElement('span');
    toolLabel.className = 'activity-verb';
    const target = document.createElement('span');
    target.className = 'activity-target-chip';
    target.addEventListener('click', (event) => {
      if (!target.dataset.filePath) return;
      event.preventDefault();
      event.stopPropagation();
      openActivityFileViewer(target.dataset.filePath);
    });
    target.addEventListener('keydown', (event) => {
      if (event.key !== 'Enter' || !target.dataset.filePath) return;
      event.preventDefault();
      event.stopPropagation();
      openActivityFileViewer(target.dataset.filePath);
    });
    const statusIcon = activityIcon('spinner', 'activity-status-icon');
    const metadata = document.createElement('span');
    metadata.className = 'activity-metadata';
    const changes = document.createElement('span');
    changes.className = 'activity-change-count';
    const chevron = activityIcon('chevron', 'activity-chevron');
    toggle.append(icon, toolLabel, target, statusIcon, metadata, changes, chevron);
    const details = document.createElement('div');
    details.className = 'activity-details';
    details.hidden = true;
    step.classList.add('collapsed');
    step.append(toggle, details);
    toggle.addEventListener('click', (event) => {
      if (event.target.closest('.activity-file-target')) return;
      toggleActivityDetails(step, toggle, details, details.hidden);
    });
    steps.append(step);
  }
  const toggle = step.querySelector('.activity-row-toggle');
  const details = step.querySelector('.activity-details');
  const currentExpanded = toggle.getAttribute('aria-expanded') === 'true';
  const requestedStatus = String(data.status || (event === 'tool_start' ? 'running' : 'completed')).toLowerCase();
  const status = event === 'tool_end' && requestedStatus === 'running' ? 'completed' : requestedStatus;
  const isRunning = event === 'tool_start' && status === 'running';
  step.dataset.status = status;
  step.dataset.past = data.past || '';
  const stateLabel = isRunning
    ? data.present || 'Running'
    : status === 'failed'
      ? 'Failed'
      : status === 'cancelled' || status === 'canceled'
        ? 'Cancelled'
        : status === 'waiting' || status === 'waiting_approval'
          ? 'Waiting for approval'
          : data.past || 'Completed';
  toggle.setAttribute('aria-label', `${stateLabel} ${data.target || data.tool || ''}`);
  step.querySelector('.activity-step-icon use').setAttribute('href', `#activity-icon-${data.icon || 'gear'}`);
  step.querySelector('.activity-verb').textContent = (isRunning || status !== 'completed')
    ? stateLabel
    : (data.past || `Used ${humanizeTool(data.tool)}`);
  const target = step.querySelector('.activity-target-chip');
  target.textContent = (data.past || isRunning || status !== 'completed') ? (data.target || humanizeTool(data.tool)) : '';
  if (target.textContent.trim().toLowerCase() === step.querySelector('.activity-verb').textContent.trim().toLowerCase()) {
    target.textContent = '';  // e.g. "Checked [checked]" would just repeat itself
  }
  target.dataset.filePath = data.file_path || '';
  target.title = data.file_path || data.target || data.tool || '';
  target.classList.toggle('activity-file-target', Boolean(data.file_path));
  if (data.file_path) {
    target.setAttribute('role', 'link');
    target.setAttribute('tabindex', '0');
    target.setAttribute('aria-label', `Open ${data.file_path}`);
  } else if (data.tool === 'capabilities' && data.capabilities) {
    appendCapabilityCards(details, data.capabilities.groups);
  } else {
    target.removeAttribute('role');
    target.removeAttribute('tabindex');
    target.removeAttribute('aria-label');
  }
  const statusIcon = step.querySelector('.activity-status-icon');
  const statusSymbol = isRunning
    ? 'spinner'
    : status === 'waiting' || status === 'waiting_approval'
      ? 'shield'
      : status === 'failed' || status === 'cancelled' || status === 'canceled'
        ? 'x'
        : 'check';
  statusIcon.querySelector('use').setAttribute('href', `#activity-icon-${statusSymbol}`);
  statusIcon.dataset.state = isRunning ? 'running' : status;
  statusIcon.classList.toggle('is-running', isRunning);
  const diff = data.diff;
  const metadataEl = step.querySelector('.activity-metadata');
  clearInterval(step._activityTimer);
  if (isRunning) {
    const startedAt = step._activityStartedAt || (step._activityStartedAt = Date.now());
    metadataEl.textContent = '';
    step._activityTimer = setInterval(() => {
      if (!step.isConnected || step.dataset.status !== 'running') { clearInterval(step._activityTimer); return; }
      metadataEl.textContent = activityDuration(Date.now() - startedAt);
    }, 500);
  } else {
    metadataEl.textContent = event === 'tool_start' ? '' : activityDuration(data.duration_ms || 0);
  }
  const changeCount = step.querySelector('.activity-change-count');
  changeCount.replaceChildren();
  if (diff && !diff.binary) {
    const added = document.createElement('span');
    added.className = 'activity-added-count';
    added.textContent = `+${diff.added || 0}`;
    const removed = document.createElement('span');
    removed.className = 'activity-removed-count';
    removed.textContent = `-${diff.removed || 0}`;
    changeCount.append(added, document.createTextNode(' '), removed);
  }
  step.classList.toggle('is-failed', status === 'failed');
  if (event === 'tool_end') {
    details.replaceChildren();
    if (diff) {
      appendDiffCard(details, data);
    } else if (data.icon === 'terminal') {
      const lines = data.details?.output_lines || [];
      const exitStatus = data.details?.exit_status ?? (data.status === 'failed' ? 'failed' : 'unknown');
      appendIOBlock(details, 'Command', data.details?.command || data.target || '', { prompt: '$ ', badge: `exit ${exitStatus}` });
      if (lines.length) appendIOBlock(details, 'Output', lines.slice(0, 500).join('\n'));
    } else if (data.music_player) {
      const playerCard = document.createElement('div');
      playerCard.className = 'activity-music-player-host';
      playerCard.dataset.toolCallId = String(data.tool_call_id || '');
      appendMusicPlayerCard(playerCard, data.music_player);
      steps.querySelector(
        `.activity-music-player-host[data-tool-call-id="${CSS.escape(String(data.tool_call_id || ''))}"]`,
      )?.remove();
      steps.append(playerCard);
    } else if (data.tool === 'capabilities' && data.capabilities) {
      appendCapabilityCards(details, data.capabilities.groups);
    } else {
      const args = data.details?.arguments || {};
      if (args && Object.keys(args).length) appendIOBlock(details, 'Input', JSON.stringify(args, null, 2));
      appendIOBlock(details, status === 'failed' ? 'Error' : 'Output', String(data.output || 'No additional output.'));
    }
    appendActivityImages(details, data.images);
    if (data.file_path && !diff) {
      const link = document.createElement('button');
      link.type = 'button';
      link.className = 'activity-file-link';
      link.textContent = data.file_path;
      link.addEventListener('click', () => openActivityFileViewer(data.file_path));
      details.append(link);
    }
    if (status === 'failed') {
      appendActionButton(details, 'Retry step', () => retryActivityStep(data, trace));
    }
    toggleActivityDetails(step, toggle, details, currentExpanded);
    appendActivityArtifact(trace, data);
  }
  updateActivityGrouping(trace);
}

function appendMusicPlayerCard(container, player) {
  const tracks = Array.isArray(player.queue) && player.queue.length
    ? player.queue.slice(0, 10)
    : [player];
  const card = document.createElement('section');
  card.className = 'activity-music-player';
  card.setAttribute('aria-label', 'Audio player');
  const title = document.createElement('strong');
  title.className = 'activity-music-title';
  const attribution = document.createElement('p');
  attribution.className = 'activity-music-attribution';
  const status = document.createElement('p');
  status.className = 'activity-music-status';
  status.setAttribute('aria-live', 'polite');
  const audio = document.createElement('audio');
  audio.controls = true;
  audio.preload = 'none';
  audio.setAttribute('aria-label', 'Music playback controls');
  const queue = document.createElement('ol');
  queue.className = 'activity-music-queue';
  const queueButtons = [];
  let activeIndex = 0;
  let settingTrack = false;

  const selectTrack = async (index) => {
    if (settingTrack || !tracks[index]?.stream_id) return;
    settingTrack = true;
    queueButtons.forEach((button, buttonIndex) => {
      button.setAttribute('aria-current', String(buttonIndex === index));
    });
    activeIndex = index;
    const track = tracks[index];
    title.textContent = track.title || 'Wikimedia Commons audio';
    const creator = track.artist || track.attribution || 'Creator not listed';
    attribution.textContent = `${creator} · ${track.license || 'Free license'}${track.attribution_required ? ' · attribution required' : ''}`;
    status.textContent = 'Preparing secure playback…';
    try {
      const response = await apiFetch('/api/music/stream-ticket', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ stream_id: track.stream_id }),
      });
      if (!response.ok) {
        const error = await response.json();
        throw new Error(error.error || 'This audio stream is unavailable.');
      }
      audio.src = `/api/music/stream/${encodeURIComponent(track.stream_id)}`;
      audio.load();
      status.textContent = 'Ready. Press Play to start audio in this browser.';
    } catch (error) {
      status.textContent = error.message || 'Playback could not be prepared.';
    } finally {
      settingTrack = false;
    }
  };

  tracks.forEach((track, index) => {
    const item = document.createElement('li');
    const button = document.createElement('button');
    button.type = 'button';
    button.textContent = track.title || `Track ${index + 1}`;
    button.addEventListener('click', () => void selectTrack(index));
    queueButtons.push(button);
    item.appendChild(button);
    queue.appendChild(item);
  });
  audio.addEventListener('ended', () => {
    if (activeIndex + 1 < tracks.length) {
      void selectTrack(activeIndex + 1);
    } else {
      status.textContent = 'Queue finished.';
    }
  });
  audio.addEventListener('play', () => {
    status.textContent = 'Playing in this browser.';
  });
  audio.addEventListener('pause', () => {
    if (!audio.ended) status.textContent = 'Paused.';
  });
  audio.addEventListener('waiting', () => {
    status.textContent = 'Buffering audio…';
  });
  audio.addEventListener('error', () => {
    status.textContent = 'The selected audio could not be played in this browser.';
  });
  card.append(title, attribution, status, audio);
  if (tracks.length > 1) card.append(queue);
  container.appendChild(card);
  void selectTrack(0);
}

async function retryActivityStep(data, trace) {
  if (!trace?.dataset.jobId || !data.tool_call_id) {
    elements.composerStatus.textContent = 'This failed step is no longer available to retry.';
    return;
  }
  const button = [...trace.querySelectorAll('.activity-detail-action')]
    .find((item) => item.textContent === 'Retry step');
  if (button) button.disabled = true;
  try {
    const response = await apiFetch(apiContract.endpoints.activity_retry, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ job_id: trace.dataset.jobId, tool_call_id: data.tool_call_id }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'The failed step could not be retried.');
    renderActivityEvent(trace, 'tool_end', result.activity);
  } catch (error) {
    elements.composerStatus.textContent = error.message || 'The failed step could not be retried.';
    if (button) button.disabled = false;
  }
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
        reason: '',
        note: '',
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
  let id = null;
  const data = [];
  for (const line of block.split(/\r?\n/)) {
    if (line.startsWith('event:')) event = line.slice(6).trim();
    if (line.startsWith('id:')) id = Number(line.slice(3).trim());
    if (line.startsWith('data:')) data.push(line.slice(5).trimStart());
  }
  if (!data.length) return null;
  try {
    return { event, data: JSON.parse(data.join('\n')), id };
  } catch {
    return { event, data: { message: data.join('\n') }, id };
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
    const finalResults = new Map();
    recognition.onstart = () => {
      voiceIsListening = true;
      voiceStatusMessage = '';
      elements.voiceButton.setAttribute('aria-label', 'Stop voice input');
      elements.voiceButton.title = 'Stop voice input';
      elements.voiceIndicator.hidden = false;
      elements.composerStatus.textContent = 'Listening…';
    };
    recognition.onresult = (event) => {
      const interimParts = [];
      const startIndex = Number.isInteger(event.resultIndex) ? event.resultIndex : 0;
      for (let index = startIndex; index < event.results.length; index += 1) {
        const result = event.results[index];
        const transcript = String(result[0]?.transcript || '').trim();
        if (result.isFinal) {
          if (transcript) finalResults.set(index, transcript);
          else finalResults.delete(index);
        } else if (transcript) {
          interimParts.push(transcript);
        }
      }
      const finalText = [...finalResults.entries()]
        .sort(([left], [right]) => left - right)
        .map(([, transcript]) => transcript)
        .join(' ')
        .trim();
      const separator = voiceDraft && finalText && !/\s$/.test(voiceDraft) ? ' ' : '';
      elements.prompt.value = `${voiceDraft}${separator}${finalText}`;
      elements.prompt.style.height = 'auto';
      elements.prompt.style.height = `${Math.min(elements.prompt.scrollHeight, 240)}px`;
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

async function answerPendingQuestion(message, signal) {
  const jobId = pendingQuestion.id;
  const approvalPending = ['waiting_for_approval', 'permission_request'].includes(pendingQuestion.state);
  if (approvalPending && message.trim().toLowerCase() === 'details') {
    const details = [...elements.stream.querySelectorAll('.approval-card')]
      .find((card) => card.dataset.approvalJob === jobId)?.querySelector('details');
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
  if (data.job && ['waiting_for_approval', 'permission_request', 'waiting_for_user'].includes(data.job.state)) {
    pendingQuestion = data.job;
    if (['waiting_for_approval', 'permission_request'].includes(data.job.state)) {
      appendApprovalCard(data.job);
    } else {
      appendInteractiveQuestion(data.job);
    }
    return;
  }
  const pendingCard = [...elements.stream.querySelectorAll('.message-card')]
    .find((article) => article.dataset.jobId === jobId);
  const pendingUi = pendingCard?.querySelector(approvalPending ? '.approval-card' : '.interactive-question');
  if (pendingUi) {
    pendingUi.querySelectorAll('button').forEach((button) => {
      button.disabled = true;
      if (button.textContent.trim() === 'Sending…') button.textContent = 'Sent';
    });
    const status = document.createElement('p');
    status.className = 'pending-answer-status';
    status.textContent = approvalPending
      ? (message.trim().toLowerCase() === 'reject' ? 'Not approved.' : 'Approved once.')
      : `Answer sent: ${message}`;
    pendingUi.append(status);
  }
  if (elements.permissionDialog.open) elements.permissionDialog.close();
  pendingQuestion = null;
  const activity = appendActivity('Continuing the task…');
  await pollQuestionJob(jobId, activity, signal);
}

function submitQuestionChoice(answer) {
  if (!pendingQuestion || !answer.trim() || submitting) return;
  const jobId = pendingQuestion.id;
  const article = [...elements.stream.querySelectorAll('.message-card')]
    .find((item) => item.dataset.jobId === jobId);
  const card = article?.querySelector('.interactive-question');
  if (card) {
    card.dataset.submitting = 'true';
    card.querySelectorAll('button').forEach((button) => {
      button.disabled = true;
      if (button.textContent.trim() === 'Send') button.textContent = 'Sending…';
    });
    card.querySelectorAll('input').forEach((input) => { input.disabled = true; });
  }
  elements.prompt.value = answer;
  elements.prompt.dispatchEvent(new Event('input', { bubbles: true }));
  elements.composer.requestSubmit();
}

function appendInteractiveQuestion(job) {
  const jobId = String(job.id || '');
  if (!jobId) return;
  let article = [...elements.stream.querySelectorAll('.message-card')]
    .find((item) => item.dataset.jobId === jobId);
  if (!article) {
    article = appendMessage(
      'assistant',
      job.question || 'I need a little more information.',
      jobId,
      { noFeedback: true },
    );
  }
  const questionCount = Number(job.question_count) || 1;
  if (
    article.dataset.questionCount === String(questionCount)
    && article.querySelector('.interactive-question')
  ) return;
  article.dataset.questionCount = String(questionCount);
  const content = article.querySelector('.message-content');
  content.replaceChildren();
  const feedback = article.querySelector('.message-feedback');
  if (feedback) feedback.hidden = true;

  const card = document.createElement('section');
  card.className = 'interactive-question';
  card.setAttribute('aria-label', 'Question for you');
  const progress = document.createElement('p');
  progress.className = 'interactive-question-progress';
  progress.textContent = `Question ${questionCount} of 3`;
  const question = document.createElement('p');
  question.className = 'interactive-question-prompt';
  question.textContent = job.question || 'I need a little more information.';
  card.append(progress, question);

  const options = Array.isArray(job.options) ? job.options : [];
  if (options.length) {
    const optionList = document.createElement('div');
    optionList.className = 'interactive-question-options';
    options.forEach((value, index) => {
      const label = typeof value === 'string' ? value : String(value?.label || '');
      if (!label) return;
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'interactive-question-option';
      button.textContent = label;
      button.setAttribute('aria-label', `Choose option ${index + 1}: ${label}`);
      button.addEventListener('click', () => submitQuestionChoice(label));
      optionList.append(button);
    });
    card.append(optionList);
  }

  if (job.allow_free_text !== false) {
    const otherRow = document.createElement('div');
    otherRow.className = 'interactive-question-other';
    const otherButton = document.createElement('button');
    otherButton.type = 'button';
    otherButton.className = 'interactive-question-other-toggle';
    otherButton.textContent = 'Other';
    const answerBox = document.createElement('div');
    answerBox.className = 'interactive-question-answer';
    answerBox.hidden = true;
    const answerInput = document.createElement('input');
    answerInput.type = 'text';
    answerInput.maxLength = 4000;
    answerInput.placeholder = 'Type your own answer';
    answerInput.setAttribute('aria-label', 'Type your own answer');
    const send = document.createElement('button');
    send.type = 'button';
    send.textContent = 'Send';
    send.addEventListener('click', () => submitQuestionChoice(answerInput.value.trim()));
    answerInput.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' && !event.isComposing) {
        event.preventDefault();
        submitQuestionChoice(answerInput.value.trim());
      }
    });
    otherButton.addEventListener('click', () => {
      answerBox.hidden = false;
      answerInput.focus();
    });
    answerBox.append(answerInput, send);
    otherRow.append(otherButton, answerBox);
    card.append(otherRow);
  }
  content.append(card);
  scrollToLatest();
}

function appendApprovalCard(job) {
  elements.welcome.hidden = true;
  setConversationEmpty(false);
  pendingQuestion = job;
  const approval = job.approval || {};
  const isPermissionRequest = job.state === 'permission_request' || approval.request_type === 'permission_request';
  let article = [...elements.stream.querySelectorAll('.message-card')]
    .find((item) => item.dataset.jobId === job.id);
  if (!article) {
    article = appendMessage('assistant', approval.summary || 'Hsx needs your approval.', job.id, { noFeedback: true });
  }
  const card = document.createElement('section');
  card.className = 'approval-card';
  card.dataset.approvalJob = job.id;
  card.setAttribute('aria-label', 'Hsx action approval');
  const title = document.createElement('h3');
  title.textContent = isPermissionRequest
    ? 'Permission needed'
    : approval.request_type === 'package_install'
      ? 'Hsx can add this ability'
      : approval.request_type === 'tool_consent'
        ? 'Review before Hsx acts'
        : 'Hsx wants to:';
  const summary = document.createElement('p');
  summary.className = 'approval-summary';
  summary.textContent = approval.summary || 'Run a tool';
  const why = document.createElement('p');
  why.className = 'approval-consequence';
  why.textContent = approval.why || 'This action needs your approval.';
  const policyNote = document.createElement('p');
  policyNote.className = 'approval-policy-note';
  policyNote.textContent = isPermissionRequest
    ? 'Elevated execution is limited to a non-interactive sudo attempt. Your OS password is never requested or stored.'
    : '';
  const impact = document.createElement('p');
  impact.className = 'approval-impact';
  impact.textContent = approval.host_impact ? `Effect on this HP: ${approval.host_impact}` : '';
  const risk = document.createElement('p');
  risk.className = 'approval-risk';
  risk.textContent = approval.security_risk ? `Security considerations: ${approval.security_risk}` : '';
  const details = document.createElement('details');
  const detailsSummary = document.createElement('summary');
  detailsSummary.textContent = 'Command and action details';
  details.append(detailsSummary);
  if (approval.command) {
    const commandLabel = document.createElement('strong');
    commandLabel.textContent = 'Command Hsx will run';
    const commandText = document.createElement('pre');
    commandText.className = 'approval-command';
    commandText.textContent = approval.command;
    details.append(commandLabel, commandText);
  }
  if (approval.details && approval.details !== approval.command) {
    const detailsText = document.createElement('pre');
    detailsText.textContent = approval.details;
    details.append(detailsText);
  }
  const actions = document.createElement('div');
  actions.className = 'approval-actions';
  const choices = [
    [approval.request_type === 'package_install' ? 'Allow' : 'Allow once', 'accept_once'],
    [approval.request_type === 'package_install' ? 'Not now' : 'Deny', 'reject'],
  ];
  for (const [label, answer] of choices) {
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
  card.append(title, summary, why);
  if (impact.textContent) card.append(impact);
  if (risk.textContent) card.append(risk);
  if (policyNote.textContent) card.append(policyNote);
  card.append(details);
  if (job.error) {
    const error = document.createElement('p');
    error.className = 'approval-error';
    error.textContent = job.error;
    card.appendChild(error);
  }
  card.appendChild(actions);
  article.querySelector('.message-content').replaceChildren(card);
  if (elements.permissionDialog.open) elements.permissionDialog.close();
  elements.permissionDialogContent.replaceChildren();
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
      appendInteractiveQuestion(job);
      return;
    }
    if (['waiting_for_approval', 'permission_request'].includes(job.state)) {
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
  appendMessage('user', userMessage, '', { editText: userMessage, attachments: readyAttachments });
  setBusy(true);
  safeStorageSet('sessionStorage', `hsx-draft-${sessionId}`, typedMessage);

  if (pendingQuestion) {
    const controller = new AbortController();
    activeChatController = controller;
    try {
      await answerPendingQuestion(requestMessage, controller.signal);
      elements.prompt.value = '';
      elements.prompt.dispatchEvent(new Event('input', { bubbles: true }));
      uploadedAttachments = [];
      persistAttachments();
      renderAttachments();
      safeStorageSet('sessionStorage', `hsx-draft-${sessionId}`, '');
    } catch (error) {
      if (error.name !== 'AbortError') {
        appendMessage('assistant', 'Could not submit your answer. Your text is still available in this tab.');
      }
      const questionCard = [...elements.stream.querySelectorAll('.message-card')]
        .find((article) => article.dataset.jobId === pendingQuestion?.id)
        ?.querySelector('.interactive-question');
      if (questionCard) {
        questionCard.dataset.submitting = 'false';
        questionCard.querySelectorAll('button').forEach((button) => {
          button.disabled = false;
          if (button.textContent.trim() === 'Sending…') button.textContent = 'Send';
        });
        questionCard.querySelectorAll('input').forEach((input) => { input.disabled = false; });
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
  let visibleAssistantContent = '';
  let assistantStreamEnded = false;
  let assistantRenderTimer = null;
  let currentJob = null;
  let cameraSnapshotPending = false;
  let activity;
  const clientMessageId = makeClientMessageId();
  let lastEventId = 0;
  let retries = 0;
  const scheduleAssistantReveal = () => {
    if (assistantRenderTimer !== null || !assistantCard) return;
    const revealNext = () => {
      assistantRenderTimer = null;
      const remaining = assistantContent.slice(visibleAssistantContent.length);
      if (!remaining) return;
      let nextLength = 0;
      const boundary = remaining.search(/\s/);
      if (boundary === 0) {
        nextLength = remaining.match(/^\s+/)?.[0].length || 1;
      } else if (boundary > 0) {
        nextLength = boundary + 1;
      } else if (assistantStreamEnded) {
        nextLength = remaining.length;
      }
      if (nextLength) {
        visibleAssistantContent += remaining.slice(0, nextLength);
        const content = assistantCard.querySelector('.message-content');
        content.textContent = visibleAssistantContent;
        assistantCard.dataset.response = assistantContent;
        scrollToLatest();
      }
      if (visibleAssistantContent.length < assistantContent.length) {
        assistantRenderTimer = window.setTimeout(revealNext, nextLength ? 34 : 50);
      } else if (assistantStreamEnded) {
        const content = assistantCard.querySelector('.message-content');
        renderMarkdown(assistantContent, content);
        assistantCard.dataset.response = assistantContent;
        queueConversationSave();
      }
    };
    assistantRenderTimer = window.setTimeout(revealNext, 0);
  };
  const finishAssistantStream = () => {
    assistantStreamEnded = true;
    scheduleAssistantReveal();
  };
  try {
    let streamInitialized = false;
    const handleStreamEvent = ({ event, data, id }) => {
      if (Number.isInteger(id)) {
        if (id <= lastEventId) return;
        lastEventId = id;
      }
      if (event === 'message_start') {
        activeRunId = data.run_id || '';
        activity.dataset.jobId = activeRunId;
        setBusy(true);
      } else if (event === 'provider_notice') {
        elements.providerNotice.textContent = data.message || '';
        elements.providerNotice.hidden = !data.message;
      } else if (['tool_start', 'tool_end', 'activity_narration'].includes(event)) {
        renderActivityEvent(activity, event, data);
      } else if (event === 'abort' || event === 'stream_end') {
        setBusy(false);
      }
      if (event === 'text_delta') {
        assistantContent += data.text || '';
        if (!assistantCard) {
          assistantCard = appendMessage('assistant', '', '', { noFeedback: true });
          if (!activity?.querySelector('.activity-step')) activity?.remove();
        }
        assistantCard.dataset.response = assistantContent;
        scheduleAssistantReveal();
      } else if (event === 'job' && data.id) {
        currentJob = data;
        activeRunId = data.id;
        activity.dataset.jobId = data.id;
        if (data.state === 'waiting_for_user') {
          finishAssistantStream();
          pendingQuestion = data;
          if (!activity?.querySelector('.activity-step')) activity?.remove();
          appendInteractiveQuestion(data);
        }
        if (['waiting_for_approval', 'permission_request'].includes(data.state)) {
          finishAssistantStream();
          pendingQuestion = data;
          if (!activity?.querySelector('.activity-step')) activity?.remove();
          appendApprovalCard(data);
        }
        if (['done', 'failed', 'cancelled'].includes(data.state)) {
          finishAssistantStream();
          if (!activity?.querySelector('.activity-step')) activity?.remove();
          const text = assistantContent || data.result || data.error || 'The task was cancelled.';
          if (assistantCard) {
            assistantCard.dataset.jobId = data.id;
            assistantCard.dataset.prompt = requestMessage;
            assistantCard.dataset.response = text;
            bindMessageFeedback(assistantCard, data.id);
          } else {
            assistantCard = appendMessage('assistant', text, data.id, { prompt: requestMessage });
          }
          if (cameraSnapshotPending) void appendInlineCameraSnapshot(assistantCard);
        }
      } else if (event === 'error') {
        finishAssistantStream();
        elements.providerNotice.hidden = true;
        setBusy(false);
        const error = new Error(data.message || 'The task failed.');
        error.noReconnect = true;
        throw error;
      } else if (event === 'camera_state') {
        handleCameraStateEvent(data);
      } else if (event === 'camera_snapshot') {
        cameraSnapshotPending = data.source === apiContract.endpoints.camera_snapshot;
      } else if (event === 'done' && activeChatController === controller) {
        finishAssistantStream();
        elements.providerNotice.hidden = true;
        setBusy(false);
        elements.prompt.focus();
        checkAndShowBrowserNotification('Your Hsx task is complete.');
      }
    };
    while (true) {
      const headers = new Headers({ 'Content-Type': 'application/json' });
      if (lastEventId > 0) headers.set('Last-Event-ID', String(lastEventId));
      const response = await apiFetch(apiContract.endpoints.chat_stream, {
        method: 'POST',
        headers,
        signal: controller.signal,
        body: JSON.stringify({
          message: requestMessage,
          session_id: sessionId,
          target_device: 'local_host',
          client_message_id: clientMessageId,
        }),
      });
      if (!response.ok) {
        const errorPayload = await response.json().catch(() => ({}));
        if (response.status >= 500 && retries < 3 && !controller.signal.aborted) {
          retries += 1;
          elements.composerStatus.textContent = `Connection interrupted. Resuming (${retries}/3)…`;
          await new Promise((resolve) => window.setTimeout(resolve, 250 * (2 ** (retries - 1))));
          continue;
        }
        throw new Error(errorPayload.error || 'The request could not be completed.');
      }
      if (!streamInitialized) {
        uploadedAttachments = [];
        persistAttachments();
        renderAttachments();
        elements.prompt.value = '';
        elements.prompt.dispatchEvent(new Event('input', { bubbles: true }));
        updateSendButton();
        activity = appendActivity('Thinking…');
        activity.dataset.retryPrompt = requestMessage;
        setBusy(true);
        streamInitialized = true;
      }
      try {
        await readStream(response, handleStreamEvent);
        break;
      } catch (error) {
        if (controller.signal.aborted || error.noReconnect || retries >= 3) throw error;
        retries += 1;
        elements.composerStatus.textContent = `Connection interrupted. Resuming (${retries}/3)…`;
        await new Promise((resolve) => window.setTimeout(resolve, 250 * (2 ** (retries - 1))));
      }
    }
    if (!assistantCard && currentJob && currentJob.state === 'running') {
      activity?.remove();
      appendMessage('assistant', 'Task started.', currentJob.id, { prompt: requestMessage });
    } else if (!assistantCard && currentJob?.state === 'waiting_for_user') {
      pendingQuestion = currentJob;
    } else if (!assistantCard && ['waiting_for_approval', 'permission_request'].includes(currentJob?.state)) {
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
  label.textContent = connected ? (cameraHasFrame ? 'Live' : 'Connecting') : 'Offline';
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

async function stopCameraAndStandby({ notifyServer = true } = {}) {
  if (remoteCameraMode) {
    const wasOn = cameraEnabled;
    disconnectRemoteCamera();
    remoteResumeOnVisible = wasOn && document.hidden;
    return true;
  }
  let hostStillActive = false;
  if (notifyServer && apiContract) {
    try {
      const response = await apiFetch(apiContract.endpoints.camera_stop, {
        method: 'POST',
        keepalive: true,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: sessionId }),
      });
      const data = await response.json();
      if (!response.ok || data.stopped !== true || data.session_active === true) {
        throw new Error(data.error || `Camera stop failed (HTTP ${response.status}).`);
      }
      hostStillActive = data.active === true;
    } catch (error) {
      const message = error.message || 'The host did not confirm that the camera stopped.';
      setCameraStatus(cameraEnabled, `Could not stop camera: ${message}`);
      return false;
    }
  }
  cameraEnabled = false;
  stopCameraVision(true);
  window.clearTimeout(cameraAutoOffTimer);
  cameraAutoOffTimer = null;
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
  setCameraStatus(
    false,
    hostStillActive
      ? 'This session is off; the host camera is active for another session'
      : 'Click to turn on camera',
  );
  return true;
}

let hostCameraNames = [];
const FEED_REFRESH_ICON = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20 7v5h-5M4 17v-5h5"></path><path d="M5.6 9a7 7 0 0 1 11.6-2L20 12M4 12l2.8 5a7 7 0 0 0 11.6-2"></path></svg>';

function normalizeCameraName(name) {
  return String(name || '').toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
}

// Same physical camera even if one list adds a suffix such as "(Windows Virtual Camera)".
function sameCameraName(a, b) {
  const x = normalizeCameraName(a);
  const y = normalizeCameraName(b);
  if (!x || !y) return false;
  if (x === y || x.includes(y) || y.includes(x)) return true;
  const base = (name) => normalizeCameraName(String(name).split('(')[0]);
  return base(a) !== '' && base(a) === base(b);
}

async function loadRemoteCameraName() {
  const heading = document.getElementById('camera-device-name');
  try {
    const base = new URL(apiContract.endpoints.camera_devices, window.location.href);
    const response = await apiFetch(new URL('remote-info', base).toString());
    const data = response.ok ? await response.json() : {};
    heading.textContent = data.label || data.name || data.host || 'Camera';
    heading.parentElement.title = [
      data.host && `Machine: ${data.host}`,
      data.name && `Camera: ${data.name}`,
      data.address && `Address: ${data.address}`,
      data.source && `Detected via ${data.source}`,
    ].filter(Boolean).join('\n') || 'Camera';
  } catch {
    if (heading.textContent === 'Detecting camera…') heading.textContent = 'Camera';
  }
}

// Middle slots: cameras the Hsx host detects that are not already shown as a browser-device feed.
function renderExtraCameraFeeds() {
  const list = document.getElementById('camera-feeds-list');
  list.querySelectorAll('[data-feed-host="extra"]').forEach((node) => node.remove());
  hostCameraNames
    .filter((name) => !browserCameraFeeds.some((feed) => sameCameraName(feed.label, name)))
    .slice(0, 2)
    .forEach((name) => {
      const panel = document.createElement('div');
      panel.className = 'camera-panel';
      panel.dataset.feedHost = 'extra';
      panel.innerHTML = `
        <div class="camera-heading" title="${escapeHtml(name)}">
          <h2>${escapeHtml(name)}</h2>
          <div class="camera-heading-actions">
            <span class="camera-delay" data-state="warning">No live feed</span>
            <span class="camera-badge camera-badge-offline"><span class="connection-dot"></span> Offline</span>
            <button class="panel-refresh camera-feed-refresh" type="button" aria-label="Refresh ${escapeHtml(name)}" title="Refresh camera">${FEED_REFRESH_ICON}</button>
          </div>
        </div>
        <div class="camera-view" role="button" tabindex="0" aria-label="${escapeHtml(name)}">
          <div class="camera-placeholder">
            <svg class="camera-placeholder-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7.5h11.5a2 2 0 0 1 2 2v1l3-2v7l-3-2v1a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2Z"></path></svg>
            <strong>Camera is off</strong>
            <span class="camera-feed-status">Detected on the Hsx host — standby</span>
          </div>
        </div>
        `;
      const status = panel.querySelector('.camera-feed-status');
      const note = () => { status.textContent = 'Only one host camera streams at a time'; };
      panel.querySelector('.camera-feed-refresh').addEventListener('click', () => {
        status.textContent = 'Refreshing…';
        void refreshCameraDevices();
      });
      const view = panel.querySelector('.camera-view');
      view.addEventListener('click', note);
      view.addEventListener('keydown', (event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          note();
        }
      });
      list.append(panel);
    });
  // Browser-device feed(s) always stay in the last slot(s).
  for (const feed of browserCameraFeeds) list.append(feed.panel);
}

async function refreshCameraDevices() {
  void loadRemoteCameraName();
  if (!apiContract?.endpoints?.camera_devices) return;
  try {
    const response = await apiFetch(apiContract.endpoints.camera_devices);
    if (!response.ok) return;
    const data = await response.json();
    hostCameraNames = Array.isArray(data.devices) ? data.devices : [];
    renderExtraCameraFeeds();
  } catch {
    // Keep the current slots until the host is reachable.
  }
}

async function refreshCamera() {
  if (document.hidden) return;
  if (remoteCameraMode) return; // The first feed is the remote HP PC stream; local logic stays out of the way.
  try {
    const statusUrl = new URL(apiContract.endpoints.camera_status, window.location.href);
    statusUrl.searchParams.set('session_id', sessionId);
    const response = await apiFetch(statusUrl.toString());
    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.error || `Camera status failed (HTTP ${response.status}).`);
    }
    const host = data.host || {};
    const hostLabel = [host.hostname, host.hardware].filter(Boolean).join(' · ');
    document.getElementById('camera-heading').title =
      hostLabel || 'Host hardware information unavailable';
    const connected = data.session_active === true;
    if (connected) {
      if (!cameraEnabled) {
        cameraEnabled = true;
      }
      cameraAvailable = true;
      setCameraStatus(true, cameraHasFrame ? 'Live from the Hsx host' : 'Connecting to the Hsx host…');
      startCameraStream();
    } else {
      if (!cameraEnabled) {
        cameraAvailable = false;
        setCameraStatus(
          false,
          data.running
            ? 'The host camera is active for another session; this feed stays hidden'
            : 'Click to turn on camera',
        );
        return;
      }
      await stopCameraAndStandby({ notifyServer: false });
      cameraAvailable = false;
      const reason = data.running
        ? 'The host camera is active for another session.'
        : data.last_error || data.snapshot_reason || 'The host camera is not running.';
      setCameraStatus(false, reason);
    }
  } catch (error) {
    document.getElementById('camera-heading').title = 'Host hardware information unavailable';
    const message = error.message || 'The camera status could not be read.';
    if (!cameraEnabled) {
      setCameraStatus(false, 'Click to turn on camera');
      return;
    }
    if (cameraHasFrame) setCameraReconnecting(`Camera feed interrupted: ${message}`);
    else setCameraStatus(false, `Camera feed unavailable: ${message}`);
    if (!/\(HTTP 4\d\d\)/.test(message)) scheduleCameraReconnect(`Camera feed unavailable: ${message}`);
  }
}

function handleCameraStateEvent(data) {
  if (remoteCameraMode) return; // Host-camera events do not control the remote feed.
  if (data && data.session_id && data.session_id !== sessionId) return;
  const active = Boolean(data && (data.session_id ? data.session_active : data.active));
  if (active) {
    if (!cameraEnabled) {
      cameraEnabled = true;
      openCameraFeedsPanel();
      setCameraStatus(false, 'Live feed active…');
      refreshCamera();
    }
    if (data.session_id === sessionId) {
      window.clearTimeout(cameraAutoOffTimer);
      cameraAutoOffTimer = window.setTimeout(stopCameraAndStandby, 5 * 60 * 1000);
    }
  } else {
    if (cameraEnabled) {
      stopCameraAndStandby({ notifyServer: false });
    }
  }
}

async function toggleCamera() {
  if (remoteCameraMode) {
    // Click = switch. Dropping the connection makes the proxy close the HP stream, which stops its camera.
    if (cameraEnabled) disconnectRemoteCamera();
    else connectRemoteCamera();
    return;
  }
  if (cameraEnabled) {
    await stopCameraAndStandby();
    return;
  }
  setCameraStatus(false, 'Requesting the host camera…');
  try {
    const response = await apiFetch(apiContract.endpoints.camera_start, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: sessionId }),
    });
    const data = await response.json();
    if (!response.ok || data.started !== true) {
      throw new Error(data.error || `Camera start failed (HTTP ${response.status}).`);
    }
    cameraEnabled = true;
    cameraAvailable = true;
    startCameraVision();
    window.clearTimeout(cameraAutoOffTimer);
    cameraAutoOffTimer = window.setTimeout(stopCameraAndStandby, 5 * 60 * 1000);
    setCameraStatus(true, 'Live feed active…');
    startCameraStream();
  } catch (error) {
    setCameraStatus(false, `Camera did not start: ${error.message || 'Unknown camera error.'}`);
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
  const sourceTime = Number(headers.match(/(?:^|\r\n)X-Hsx-Source-Time:\s*([\d.]+)/i)?.[1]);
  if (!Number.isInteger(length) || length < 4 || length > 2 * 1024 * 1024) {
    throw new Error('Invalid camera frame size.');
  }
  const payloadStart = headerEnd + 4;
  if (buffer.length < payloadStart + length) return null;
  return {
    data: buffer.slice(payloadStart, payloadStart + length),
    capturedAt: Number.isFinite(capturedAt) && capturedAt > 0 ? capturedAt : Date.now() / 1000,
    sourceTime: Number.isFinite(sourceTime) && sourceTime > 0 ? sourceTime : null,
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
  output.textContent = `${delaySeconds.toFixed(1)} s behind live`;
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
  // Clock of the system being filmed: the HP PC's clock for the remote feed, this host's otherwise.
  document.getElementById('camera-clip-time').textContent =
    formatFeedTime((frame.sourceTime ?? frame.capturedAt) * 1000);
  document.getElementById('camera-clip-time').dataset.capturedAt = String(captureBrowserTime);
  document.getElementById('camera-clip-time').hidden = false;
  setCameraLatency(frame.capturedAt);
  setCameraStatus(true, remoteCameraMode ? 'Live' : 'Live from the Hsx host');
  if (!remoteCameraMode) startCameraVision();
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

function scheduleCameraReconnect(message = '') {
  if (!cameraEnabled || document.hidden || !navigator.onLine || cameraReconnectTimer) return;
  if (message) setCameraStatus(false, message);
  else if (cameraHasFrame) setCameraReconnecting('Video paused; reconnecting to the latest frame…');
  else setCameraStatus(false, 'Camera feed offline');
  cameraReconnectTimer = window.setTimeout(() => {
    cameraReconnectTimer = null;
    if (remoteCameraMode) void startCameraStream();
    else refreshCamera();
  }, cameraRetryDelay);
  cameraRetryDelay = Math.min(cameraRetryDelay * 2, remoteCameraMode ? 15000 : 8000);
}

async function startCameraStream() {
  if (!cameraEnabled || !cameraAvailable || document.hidden || !navigator.onLine || cameraStreamController || cameraReconnectTimer) return;
  const controller = new AbortController();
  cameraStreamController = controller;
  cameraStreamStartedAt = Date.now();
  try {
    const requestStartedAt = Date.now();
    const streamUrl = remoteCameraMode
      ? new URL('remote-stream', new URL(apiContract.endpoints.camera_devices, window.location.href))
      : new URL(apiContract.endpoints.camera_stream, window.location.href);
    if (!remoteCameraMode) streamUrl.searchParams.set('session_id', sessionId);
    const response = await apiFetch(streamUrl.toString(), {
      signal: controller.signal,
    });
    if (!response.ok || !response.body) {
      if (!remoteCameraMode) cameraAvailable = false; // The remote feed keeps retrying until switched off.
      const error = await response.json().catch(() => ({}));
      const message = error.error || `Camera stream failed (HTTP ${response.status}).`;
      setCameraStatus(false, `Camera feed unavailable: ${message}`);
      if (response.status >= 500 || response.status === 0) {
        scheduleCameraReconnect(`Camera feed unavailable: ${message}`);
      }
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
      let newestFrame = null;
      while (true) {
        const frame = takeCameraFrame(buffer);
        if (!frame) break;
        buffer = frame.remaining;
        newestFrame = frame;
      }
      if (newestFrame) {
        const frameAgeMs = Date.now() - (newestFrame.capturedAt * 1000 - cameraClockOffsetMs);
        if (frameAgeMs > liveDelayLimitMs()) {
          controller.abort();
          if (cameraStreamController === controller) cameraStreamController = null;
          cameraRetryDelay = 500;
          scheduleCameraReconnect();
          break;
        }
        showCameraFrame(newestFrame);
      }
      if (buffer.length > 2 * 1024 * 1024 + 16_384) {
        throw new Error('Camera stream is out of sync.');
      }
    }
  } catch (error) {
    if (!controller.signal.aborted) {
      const message = `Camera feed interrupted: ${error.message || 'stream ended unexpectedly'}`;
      setCameraStatus(false, message);
      scheduleCameraReconnect(message);
    }
  } finally {
    if (cameraStreamController === controller) cameraStreamController = null;
    if (!controller.signal.aborted && cameraAvailable) scheduleCameraReconnect();
  }
}

function updateCameraClock() {
  const deviceClock = formatFeedTime(Date.now());
  // Browser-device feeds are filmed by this very device, so its own clock is the footage clock.
  document.querySelectorAll('.camera-local-clock').forEach((node) => {
    if (!node.hidden) node.textContent = deviceClock;
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
    && currentDelayMs > liveDelayLimitMs()
  ) {
    cameraStreamController.abort();
    cameraStreamController = null;
    cameraRetryDelay = 500;
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
}

function createBrowserCameraFeed(label, deviceId = '', facingMode = '') {
  const panel = document.createElement('div');
  panel.className = 'camera-panel';
  panel.dataset.feedType = 'browser';
  panel.innerHTML = `
    <div class="camera-heading" title="${escapeHtml(label)}">
      <h2>${escapeHtml(label)}</h2>
      <div class="camera-heading-actions">
        <span class="camera-delay" data-state="connected">Local device</span>
        <span class="camera-badge camera-badge-offline"><span class="connection-dot"></span> Offline</span>
        <button class="panel-refresh camera-feed-refresh" type="button" aria-label="Refresh ${escapeHtml(label)}" title="Refresh camera"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20 7v5h-5M4 17v-5h5"></path><path d="M5.6 9a7 7 0 0 1 11.6-2L20 12M4 12l2.8 5a7 7 0 0 0 11.6-2"></path></svg></button>
      </div>
    </div>
    <div class="camera-view" role="button" tabindex="0" aria-label="Turn on ${escapeHtml(label)}">
      <video class="camera-frame" autoplay playsinline muted hidden></video>
      <div class="camera-clip-time camera-local-clock" title="Time on this device" hidden>--:--:--</div>
      <div class="camera-placeholder">
        <svg class="camera-placeholder-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7.5h11.5a2 2 0 0 1 2 2v1l3-2v7l-3-2v1a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2Z"></path></svg>
        <strong>Camera is off</strong>
        <span class="camera-feed-status">Click to turn on this camera</span>
      </div>
    </div>
    `;
  const state = {
    panel,
    deviceId,
    label,
    stream: null,
    video: panel.querySelector('video'),
    placeholder: panel.querySelector('.camera-placeholder'),
    clock: panel.querySelector('.camera-local-clock'),
    badge: panel.querySelector('.camera-badge'),
    status: panel.querySelector('.camera-feed-status'),
  };
  const setFeedState = (active, text, connecting = false) => {
    state.badge.classList.toggle('camera-badge-online', active);
    state.badge.classList.toggle('camera-badge-offline', !active && !connecting);
    state.badge.classList.toggle('camera-badge-reconnecting', connecting);
    state.badge.classList.remove('camera-badge-standby');
    state.badge.replaceChildren();
    const dot = document.createElement('span');
    dot.className = 'connection-dot';
    state.badge.append(dot, document.createTextNode(active ? ' Live' : connecting ? ' Connecting' : ' Offline'));
    state.status.textContent = text;
    state.video.hidden = !active;
    state.placeholder.hidden = active;
    state.clock.hidden = !active;
    if (active) state.clock.textContent = formatFeedTime(Date.now());
  };
  state.stop = () => {
    if (state.stream) {
      state.stream.getTracks().forEach((track) => track.stop());
      state.stream = null;
    }
    state.video.srcObject = null;
    setFeedState(false, 'Click to turn on this camera');
  };
  state.start = async () => {
    if (state.stream || !navigator.mediaDevices?.getUserMedia) return;
    setFeedState(false, 'Requesting camera…', true);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        video: state.deviceId
          ? { deviceId: { exact: state.deviceId } }
          : facingMode
            ? { facingMode: { ideal: facingMode } }
            : true,
        audio: false,
      });
      state.stream = stream;
      state.video.srcObject = stream;
      await state.video.play().catch(() => {});
      setFeedState(true, 'Live');
    } catch (error) {
      setFeedState(false, `Camera did not start: ${error.message || 'permission denied'}`);
    }
  };
  const toggle = () => {
    if (state.stream) state.stop();
    else void state.start();
  };
  panel.querySelector('.camera-feed-refresh').addEventListener('click', async () => {
    state.stop();
    await state.start();
  });
  const view = panel.querySelector('.camera-view');
  view.addEventListener('click', toggle);
  view.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      toggle();
    }
  });
  setFeedState(false, 'Click to turn on this camera');
  return state;
}

async function refreshBrowserCameraFeeds() {
  if (!navigator.mediaDevices?.enumerateDevices) return;
  // Ask for camera permission once so labels are visible and we can tell front from back.
  try {
    const probe = await navigator.mediaDevices.getUserMedia({ video: true });
    probe.getTracks().forEach((track) => track.stop());
  } catch { /* Fall back to a single unlabeled feed. */ }
  let inputs = [];
  try {
    const devices = await navigator.mediaDevices.enumerateDevices();
    const seen = new Set();
    inputs = devices
      .filter((device) => device.kind === 'videoinput' && device.deviceId)
      .filter((device) => (seen.has(device.deviceId) ? false : (seen.add(device.deviceId), true)));
  } catch { /* Keep the fallback feed below. */ }
  const list = document.getElementById('camera-feeds-list');
  for (const feed of browserCameraFeeds) feed.panel.remove();
  browserCameraFeeds.length = 0;
  const configs = [];
  inputs.slice(0, 3).forEach((device, index) => {
    let label = device.label || `Camera ${index + 1}`;
    const isBack = /back|rear|world|environment|wide/i.test(label);
    const isFront = /front|user|face|selfie/i.test(label);
    const facingMode = isBack ? 'environment' : (inputs.length > 1 && !isFront && index === inputs.length - 1 ? 'environment' : 'user');
    configs.push({ label, deviceId: device.deviceId, facingMode });
  });
  if (!configs.length) configs.push({ label: 'Camera', deviceId: '', facingMode: '' });
  configs.slice(0, 3).forEach((config) => {
    const feed = createBrowserCameraFeed(config.label, config.deviceId, config.facingMode);
    list.append(feed.panel);
    browserCameraFeeds.push(feed);
  });
  renderExtraCameraFeeds();
}

function applyCameraFeedsWidth(width) {
  const shell = document.querySelector('.app-shell');
  let value = Number(width);
  if (!Number.isFinite(value) || value <= 0) {
    value = Number(safeStorageGet('localStorage', 'hsx-camera-feeds-width')) || 0;
  }
  if (!Number.isFinite(value) || value <= 0) {
    // Default: match the width of the artifact panel the camera panel replaces.
    value = Math.round(elements.cameraSidebar.getBoundingClientRect().width) || 340;
  }
  value = Math.round(Math.max(280, Math.min(value, window.innerWidth * 0.75, window.innerWidth - CHAT_MIN_WIDTH)));
  shell.style.setProperty('--camera-feeds-width', `${value}px`);
  safeStorageSet('localStorage', 'hsx-camera-feeds-width', String(value));
  applySidebarLayout();
}

function setupCameraFeedsResize() {
  const handle = document.getElementById('camera-feeds-resize-handle');
  if (!handle) return;
  handle.addEventListener('pointerdown', (event) => {
    if (event.button !== 0) return;
    event.preventDefault();
    handle.setPointerCapture(event.pointerId);
    document.body.classList.add('camera-feeds-resizing');
    const onMove = (moveEvent) => {
      applyCameraFeedsWidth(window.innerWidth - moveEvent.clientX + 6);
    };
    const onUp = () => {
      document.body.classList.remove('camera-feeds-resizing');
      handle.removeEventListener('pointermove', onMove);
      handle.removeEventListener('pointerup', onUp);
      handle.removeEventListener('pointercancel', onUp);
    };
    handle.addEventListener('pointermove', onMove);
    handle.addEventListener('pointerup', onUp);
    handle.addEventListener('pointercancel', onUp);
  });
}

const desktopLayout = window.matchMedia('(min-width: 701px)');
const wideLayout = window.matchMedia('(min-width: 1101px)');
const CHAT_MIN_WIDTH = 520;
// What the person wants (persisted). The sidebar may still be closed automatically while the camera panel needs the room.
let sidebarWanted = safeStorageGet('localStorage', 'hsx-sidebar-collapsed') !== '1';

function sidebarColumnWidth() {
  const width = window.innerWidth;
  return width >= 1500 ? 276 : width >= 1101 ? 258 : 230;
}

function cameraPanelWidth() {
  const shell = document.querySelector('.app-shell');
  return parseFloat(shell.style.getPropertyValue('--camera-feeds-width')) || 340;
}

function sidebarHasRoom() {
  if (!document.querySelector('.app-shell').classList.contains('camera-feeds-open')) return true;
  return window.innerWidth - sidebarColumnWidth() - cameraPanelWidth() >= CHAT_MIN_WIDTH;
}

// Single place that decides whether the chat history sidebar is shown.
function applySidebarLayout() {
  const shell = document.querySelector('.app-shell');
  const toggle = document.getElementById('sidebar-toggle');
  if (!desktopLayout.matches) {
    shell.classList.remove('sidebar-collapsed');
    toggle.removeAttribute('aria-disabled');
    toggle.setAttribute('aria-expanded', String(elements.sidebar.classList.contains('is-open')));
    return;
  }
  const room = sidebarHasRoom();
  const collapsed = !sidebarWanted || !room;
  shell.classList.toggle('sidebar-collapsed', collapsed);
  toggle.setAttribute('aria-expanded', String(!collapsed));
  // No room beside the camera panel: the sidebar can't open (it would cover the chat).
  if (!room) {
    toggle.setAttribute('aria-disabled', 'true');
    toggle.title = 'No room for chat history — narrow or close the camera panel';
  } else {
    toggle.removeAttribute('aria-disabled');
    toggle.title = 'Chat history';
  }
}

function toggleSidebar() {
  if (desktopLayout.matches) {
    if (!sidebarHasRoom()) return;
    sidebarWanted = !sidebarWanted;
    safeStorageSet('localStorage', 'hsx-sidebar-collapsed', sidebarWanted ? '0' : '1');
    applySidebarLayout();
    return;
  }
  if (elements.sidebar.classList.contains('is-open')) closeMobilePanels();
  else openMobilePanel(elements.sidebar);
}

function setArtifactsHidden(hidden, { persist = true } = {}) {
  document.querySelector('.app-shell').classList.toggle('artifacts-hidden', hidden);
  document.getElementById('artifacts-toggle').setAttribute('aria-expanded', String(!hidden));
  if (persist) safeStorageSet('localStorage', 'hsx-artifacts-hidden', hidden ? '1' : '0');
}

// Remote (HP PC) feed. The fetch connection to /api/camera/remote-stream is the switch:
// opening it starts the HP camera, aborting it (off button, closing the panel, hiding the tab)
// makes the server drop the HP connection immediately so the camera stops.
function connectRemoteCamera() {
  if (!REMOTE_CAMERA_ENABLED) return;
  remoteCameraMode = true;
  remoteResumeOnVisible = false;
  stopCameraStream();
  cameraEnabled = true;
  cameraAvailable = true;
  cameraHasFrame = false;
  cameraRetryDelay = 500;
  document.getElementById('camera-clip-time').textContent = 'Waiting for video';
  setCameraLatency(null);
  setCameraStatus(true, 'Connecting to the camera…');
  void startCameraStream();
}

function disconnectRemoteCamera(message = 'Click to turn on camera') {
  cameraEnabled = false;
  cameraAvailable = false;
  cameraHasFrame = false;
  stopCameraStream();
  elements.camera.removeAttribute('src');
  if (cameraObjectUrl) {
    URL.revokeObjectURL(cameraObjectUrl);
    cameraObjectUrl = '';
  }
  document.getElementById('camera-clip-time').textContent = '--:--:--';
  document.getElementById('camera-clip-time').hidden = true;
  setCameraLatency(null);
  setCameraStatus(false, message);
}

function openCameraFeedsPanel() {
  closeMobilePanels();
  elements.cameraFeedsPanel.hidden = false;
  applyCameraFeedsWidth();
  document.querySelector('.app-shell').classList.add('camera-feeds-open');
  applySidebarLayout();
  elements.cameraFeedsPanel.classList.add('is-open');
  document.getElementById('camera-toggle').setAttribute('aria-expanded', 'true');
  void refreshCameraDevices();
  if (REMOTE_CAMERA_ENABLED) {
    // Slot 1 = the HP PC camera: it starts with the panel and is switched by clicking the feed.
    connectRemoteCamera();
  }
  void refreshBrowserCameraFeeds().then(() => {
    // Browser camera feeds open by default; each feed can be switched off individually.
    for (const feed of browserCameraFeeds) void feed.start();
  });
  if (!REMOTE_CAMERA_ENABLED) {
    startCameraVision();
    if (!cameraEnabled) void toggleCamera();
  }
}

function closeCameraFeedsPanel() {
  if (elements.cameraFeedsPanel.hidden) return;
  elements.cameraFeedsPanel.classList.remove('is-open');
  elements.cameraFeedsPanel.hidden = true;
  document.querySelector('.app-shell').classList.remove('camera-feeds-open');
  document.getElementById('camera-toggle').setAttribute('aria-expanded', 'false');
  applySidebarLayout();
  pauseCameraVision();
  if (remoteCameraMode) {
    disconnectRemoteCamera();
    remoteCameraMode = false;
    remoteResumeOnVisible = false;
    setCameraStatus(false, 'Click to turn on camera');
  }
  // Closing the panel switches off every feed.
  if (cameraEnabled) void stopCameraAndStandby();
  for (const feed of browserCameraFeeds) feed.stop();
}

function closeMobilePanels() {
  elements.sidebar.classList.remove('is-open');
  elements.cameraSidebar.classList.remove('is-open');
  elements.panelBackdrop.hidden = true;
  applySidebarLayout();
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
      item.dataset.severity = String(notification.severity || 'info');
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
      if (notification.thumbnail) {
        const thumb = document.createElement('img');
        thumb.className = 'notification-thumbnail';
        thumb.src = notification.thumbnail;
        thumb.alt = 'Motion snapshot';
        item.append(thumb);
      }
      if (notification.link) {
        const link = document.createElement('a');
        link.className = 'notification-link';
        link.href = notification.link;
        link.target = '_blank';
        link.rel = 'noopener';
        link.textContent = 'View details';
        link.addEventListener('click', (e) => e.stopPropagation());
        item.append(link);
      }
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
    'system_temp', 'disk_full', 'low_memory', 'network', 'download', 'backup', 'service_restart',
    'camera_motion', 'needs_input', 'needs_approval', 'health_alert',
  ]);
  if (!supportedEvents.has(notification.event)) return;
  showNotificationToast(String(notification.title || 'Hsx update'));
  refreshNotifications();
  refreshAgentActivity();
}

async function loadNotificationSettings() {
  try {
    const response = await apiFetch(apiContract.endpoints.notifications_settings);
    if (!response.ok) return;
    const data = await response.json();
    const settings = data.settings || {};
    for (const [key, val] of Object.entries(settings)) {
      const input = document.getElementById(`notif-toggle-${key}`);
      if (input) input.checked = Boolean(val);
    }
    const tempEl = document.getElementById('settings-cpu-temp');
    if (tempEl) {
      if (data.temperature !== null && data.temperature !== undefined) {
        tempEl.textContent = `${data.temperature}°C`;
      } else {
        tempEl.textContent = 'Unavailable on this host';
      }
    }
    const motionBadge = document.getElementById('motion-badge');
    if (motionBadge) {
      motionBadge.hidden = !data.motion_active && !settings.camera_motion;
    }
  } catch {
    // Non-blocking
  }
}

async function saveNotificationSetting(key, enabled) {
  try {
    const response = await apiFetch(apiContract.endpoints.notifications_settings, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ [key]: enabled }),
    });
    if (response.ok) {
      const data = await response.json();
      const motionBadge = document.getElementById('motion-badge');
      if (motionBadge) {
        motionBadge.hidden = !data.motion_active && !(data.settings && data.settings.camera_motion);
      }
    }
  } catch {
    // Non-blocking
  }
}

function checkAndShowBrowserNotification(message) {
  if (!document.hidden) return;
  if (!('Notification' in window) || Notification.permission !== 'granted') return;
  const toggle = document.getElementById('notif-toggle-task_browser');
  if (toggle && !toggle.checked) return;
  try {
    new Notification('Hsx', {
      body: message,
      icon: '/favicon.svg',
    });
  } catch {
    // Ignore notification error
  }
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
  const response = await apiFetch(apiContract.endpoints.notifications_stream, {
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
      if (event && event.event === 'camera_state') {
        handleCameraStateEvent(event.data);
        continue;
      }
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
    if (['waiting_for_approval', 'permission_request'].includes(job.state)) {
      pendingQuestion = job;
      if (!elements.permissionDialogContent.querySelector(`[data-approval-job="${CSS.escape(job.id)}"]`)) {
        appendApprovalCard(job);
      }
    } else if (job.state === 'waiting_for_user') {
      pendingQuestion = job;
      appendInteractiveQuestion(job);
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
    const response = await apiFetch(apiContract.endpoints.outputs);
    if (!response.ok) throw new Error('Could not load generated files.');
    const data = await response.json();
    if (requestId !== artifactsRequestId || requestedSessionId !== sessionId) return;
    const location = document.getElementById('artifacts-location');
    const storagePath = typeof data.storage_path === 'string' ? data.storage_path : 'Hsx Work';
    location.textContent = data.storage_available === false
      ? 'Hsx Work folder unavailable'
      : `Shared Hsx Work · ${storagePath}`;
    location.title = storagePath;
    const files = Array.isArray(data.files) ? data.files : [];
    if (!files.length) {
      empty.textContent = 'Files Hsx creates for you will appear here.';
      return;
    }
    const fragment = document.createDocumentFragment();
    const grouped = new Map();
    for (const file of files) {
      if (typeof file.path !== 'string' || typeof file.name !== 'string') continue;
      const sId = file.session_id || requestedSessionId;
      if (!grouped.has(sId)) grouped.set(sId, []);
      grouped.get(sId).push(file);
    }

    for (const [sId, sessionFiles] of grouped.entries()) {
      const groupEl = document.createElement('div');
      groupEl.className = 'artifact-group';
      if (grouped.size > 1) {
        const title = document.createElement('h3');
        title.className = 'artifact-group-title';
        title.textContent = chatSessions.get(sId)?.title || (sId === requestedSessionId ? 'Current chat' : 'Saved chat');
        groupEl.append(title);
      }
      for (const file of sessionFiles) {
        const card = document.createElement('div');
        card.className = 'artifact-card artifact-item';
        card.tabIndex = 0;
        card.role = 'button';
        card.dataset.name = file.name;
        card.dataset.path = file.path;
        card.setAttribute('aria-label', `Open artifact ${file.name}`);

        const iconEl = document.createElement('span');
        iconEl.className = 'artifact-icon';
        iconEl.innerHTML = getArtifactIcon(file.type || file.name.split('.').pop());

        const bodyEl = document.createElement('div');
        bodyEl.className = 'artifact-card-body';

        const nameEl = document.createElement('span');
        nameEl.className = 'artifact-name';
        nameEl.textContent = file.name;

        const metaEl = document.createElement('div');
        metaEl.className = 'artifact-card-meta';

        const chip = document.createElement('span');
        chip.className = 'artifact-chip';
        chip.textContent = (file.type || 'file').toUpperCase();

        const timeEl = document.createElement('span');
        timeEl.className = 'artifact-time';
        timeEl.textContent = formatArtifactTime(file.created);

        metaEl.append(chip, timeEl);
        bodyEl.append(nameEl, metaEl);
        card.append(iconEl, bodyEl);

        const openViewer = () => {
          const isCode = ['py', 'js', 'ts', 'jsx', 'tsx', 'html', 'css', 'json', 'yaml', 'yml', 'toml', 'sh', 'sql', 'c', 'cpp', 'rs', 'go', 'java'].includes((file.type || '').toLowerCase());
          void openArtifactViewer(file, isCode ? 'code' : 'preview');
        };
        card.addEventListener('click', openViewer);
        card.addEventListener('keydown', (e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            openViewer();
          }
        });
        groupEl.append(card);
      }
      fragment.append(groupEl);
    }

    if (!fragment.childNodes.length) {
      empty.textContent = 'Files Hsx creates for you will appear here.';
      return;
    }
    elements.artifactsList.replaceChildren(fragment);
  } catch {
    if (requestId === artifactsRequestId && requestedSessionId === sessionId) {
      empty.textContent = 'Files Hsx creates for you will appear here.';
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
  if (opening && panel.id === 'connections-panel') positionConnectionsPopover();
  return opening;
}

function positionConnectionsPopover() {
  const panel = document.getElementById('connections-panel');
  const dot = document.getElementById('connection-toggle');
  if (window.innerWidth < 480) return;
  const rect = dot.getBoundingClientRect();
  panel.style.left = 'auto';
  panel.style.right = `${Math.max(12, window.innerWidth - rect.right)}px`;
  panel.style.top = `${rect.bottom + 8}px`;
}

function updateActivityElapsed() {
  const now = Date.now();
  elements.activityList.querySelectorAll('[data-elapsed-start]').forEach((node) => {
    const started = Number(node.dataset.elapsedStart);
    if (Number.isFinite(started)) node.textContent = formatElapsed((now - started) / 1000);
  });
}

async function refreshAgentActivity() {
  try {
    const query = new URLSearchParams({ session_id: sessionId });
    const response = await apiFetch(`${apiContract.endpoints.activity}?${query}`);
    if (!response.ok) throw new Error('Could not load agent activity.');
    const data = await response.json();
    const tasks = Array.isArray(data.tasks) ? data.tasks : [];
    const activeTask = tasks.find((task) => task.state === 'running');
    if (!activeTask) {
      elements.activityList.hidden = true;
      return;
    }
    document.getElementById('activity-running-title').textContent = String(activeTask.summary || 'Working on your request');
    elements.activityList.dataset.runId = String(activeTask.id || '');
    elements.activityList.hidden = false;
  } catch {
    elements.activityList.hidden = true;
  }
}

async function retryCamera() {
  if (remoteCameraMode) {
    connectRemoteCamera();
    return;
  }
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
  if (cameraEnabled) stopCameraAndStandby();
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
  document.body.append(document.getElementById('connections-panel'));
  const viewer = document.getElementById('activity-file-viewer');
  document.getElementById('activity-viewer-close').addEventListener('click', () => viewer.close());
  viewer.addEventListener('close', () => {
    artifactViewerObjectUrls.forEach((url) => URL.revokeObjectURL(url));
    artifactViewerObjectUrls.clear();
    artifactViewerFile = null;
  });
  viewer.addEventListener('click', (event) => {
    if (event.target === viewer) viewer.close();
  });
  const tabsNav = document.getElementById('artifact-viewer-tabs');
  if (tabsNav) {
    tabsNav.addEventListener('click', (event) => {
      const button = event.target.closest('button[data-artifact-tab]');
      if (!button) return;
      artifactViewerTab = button.dataset.artifactTab;
      tabsNav.querySelectorAll('button').forEach((btn) => {
        btn.setAttribute('aria-selected', String(btn === button));
      });
      void renderArtifactViewerTab();
    });
  }
  const copyBtn = document.getElementById('artifact-copy');
  if (copyBtn) {
    copyBtn.addEventListener('click', async () => {
      try {
        if (!artifactViewerText && artifactViewerFile) {
          const query = new URLSearchParams({ path: artifactViewerFile.path });
          const response = await apiFetch(`/api/files/preview?${query}`);
          if (response.ok) {
            const data = await response.json();
            artifactViewerText = data.content || '';
          }
        }
        await navigator.clipboard.writeText(artifactViewerText);
        const original = copyBtn.textContent;
        copyBtn.textContent = 'Copied!';
        window.setTimeout(() => { copyBtn.textContent = original; }, 1500);
      } catch {
        copyBtn.textContent = 'Failed';
        window.setTimeout(() => { copyBtn.textContent = 'Copy'; }, 1500);
      }
    });
  }
  const downloadBtn = document.getElementById('artifact-download');
  if (downloadBtn) {
    downloadBtn.addEventListener('click', async () => {
      if (!artifactViewerFile) return;
      try {
        const query = new URLSearchParams({ path: artifactViewerFile.path });
        const response = await apiFetch(`/api/files/download?${query}`);
        if (!response.ok) throw new Error('Download failed');
        const blob = await response.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = artifactViewerFile.name || 'file';
        a.click();
        window.setTimeout(() => URL.revokeObjectURL(url), 1000);
      } catch {
        elements.composerStatus.textContent = `Could not download ${artifactViewerFile.name}.`;
      }
    });
  }
  window.addEventListener('pagehide', () => {
    activityImageObjectUrls.forEach((url) => URL.revokeObjectURL(url));
    activityImageObjectUrls.clear();
  });
  elements.permissionDialog.addEventListener('cancel', (event) => {
    event.preventDefault();
  });
  const [tokensResponse, contractResponse] = await Promise.all([
    fetch('/web/design-tokens.json'),
    fetch('/web/api-contract.json'),
  ]);
  if (!tokensResponse.ok || !contractResponse.ok) throw new Error('Could not load the Hsx interface contract.');
  apiContract = await contractResponse.json();
  persistSessionId();
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
  renderChatHistory();
  const savedTheme = safeStorageGet('localStorage', 'hsx-theme') || 'dark';
  applyTheme(savedTheme);
  window.matchMedia('(prefers-color-scheme: light)').addEventListener('change', () => {
    if (safeStorageGet('localStorage', 'hsx-theme') === 'system') applyTheme('system');
  });
  document.getElementById('theme-setting').value = savedTheme;
  document.getElementById('language-setting').value = safeStorageGet('localStorage', 'hsx-language') || 'en';
  document.getElementById('voice-language-setting').value = safeStorageGet('localStorage', 'hsx-voice-language') || navigator.language || 'en-US';
  refreshGreeting();
  await refreshEmergencyStop();
  setupVoiceInput();
  desktopLayout.addEventListener('change', applySidebarLayout);
  window.addEventListener('resize', applySidebarLayout);
  applySidebarLayout();
  document.getElementById('sidebar-toggle').addEventListener('click', toggleSidebar);
  if (safeStorageGet('localStorage', 'hsx-artifacts-hidden') === '1') setArtifactsHidden(true, { persist: false });
  document.getElementById('artifacts-toggle').addEventListener('click', () => {
    setArtifactsHidden(!document.querySelector('.app-shell').classList.contains('artifacts-hidden'));
  });
  document.getElementById('camera-toggle').addEventListener('click', () => {
    if (elements.cameraFeedsPanel.classList.contains('is-open')) closeCameraFeedsPanel();
    else openCameraFeedsPanel();
  });
  document.getElementById('camera-feeds-close').addEventListener('click', closeCameraFeedsPanel);
  document.getElementById('camera-close').addEventListener('click', () => {
    if (wideLayout.matches) {
      setArtifactsHidden(true);
      return;
    }
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
  setupCameraFeedsResize();
  elements.panelBackdrop.addEventListener('click', () => {
    if (elements.cameraSidebar.classList.contains('is-open')) stopCameraAndStandby();
    closeMobilePanels();
  });
  document.getElementById('refresh-artifacts').addEventListener('click', refreshArtifacts);
  document.getElementById('camera-retry').addEventListener('click', retryCamera);
  document.getElementById('activity-stop').addEventListener('click', async () => {
    const runId = elements.activityList.dataset.runId;
    if (!runId) return;
    try {
      const response = await apiFetch(apiContract.endpoints.stop, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ run_id: runId }),
      });
      if (!response.ok) throw new Error('The active task could not be stopped.');
      await refreshAgentActivity();
    } catch (error) {
      document.getElementById('activity-stop').title = error.message || 'The active task could not be stopped.';
    }
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
    tab.addEventListener('click', () => {
      selectSettingsTab(tab.dataset.settingsTab);
    });
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
    applyTheme(event.target.value);
  });
  document.getElementById('language-setting').addEventListener('change', (event) => {
    safeStorageSet('localStorage', 'hsx-language', event.target.value);
  });
  document.getElementById('voice-language-setting').addEventListener('change', (event) => {
    safeStorageSet('localStorage', 'hsx-voice-language', event.target.value);
  });
  document.querySelectorAll('.settings-notification-toggles input[type="checkbox"]').forEach((checkbox) => {
    checkbox.addEventListener('change', () => {
      const eventKey = checkbox.dataset.event;
      if (eventKey) {
        if (eventKey === 'task_browser' && checkbox.checked && 'Notification' in window && Notification.permission !== 'granted') {
          Notification.requestPermission();
        }
        saveNotificationSetting(eventKey, checkbox.checked);
      }
    });
  });
  document.getElementById('settings-clear-notifications')?.addEventListener('click', async () => {
    await apiFetch(apiContract.endpoints.notifications_clear, { method: 'POST' });
    refreshNotifications();
  });
  loadNotificationSettings();
  document.getElementById('test-provider-health').addEventListener('click', testProviderHealth);
  document.getElementById('update-music-engine').addEventListener('click', () => {
    document.getElementById('settings-dialog').close();
    elements.prompt.value = 'Update the yt-dlp music engine using the pinned package resolver. Ask me before installing; install only into the active virtual environment, self-test, and continue.';
    updateSendButton();
    submitPrompt();
  });
  document.getElementById('settings-open-camera').addEventListener('click', () => {
    document.getElementById('settings-dialog').close();
    openCameraFeedsPanel();
    if (!cameraEnabled) toggleCamera();
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
      const connectionPanel = document.getElementById('connections-panel');
      const connectionWasOpen = !connectionPanel.hidden;
      closeMobilePanels();
      closeCameraFeedsPanel();
      closeHeaderPopovers();
      if (connectionWasOpen) document.getElementById('connection-toggle').focus();
    }
  });
  document.addEventListener('click', (event) => {
    if (!(event.target instanceof Element)) return;
    const panels = [
      [elements.notificationPanel, document.getElementById('notifications-toggle')],
      [document.getElementById('connections-panel'), document.getElementById('connection-toggle')],
    ];
    if (panels.some(([panel]) => !panel.hidden && panel.contains(event.target))) return;
    if (panels.some(([panel, button]) => !panel.hidden && !button.contains(event.target))) closeHeaderPopovers();
  });
  window.addEventListener('resize', () => {
    const panel = document.getElementById('connections-panel');
    if (!panel.hidden) positionConnectionsPopover();
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
    if (['waiting_for_approval', 'permission_request'].includes(pendingData.job?.state)) {
      pendingQuestion = pendingData.job;
      appendApprovalCard(pendingQuestion);
    } else if (pendingData.job?.state === 'waiting_for_user') {
      pendingQuestion = pendingData.job;
      appendInteractiveQuestion(pendingQuestion);
    }
  }
  elements.composer.addEventListener('submit', (event) => {
    event.preventDefault();
    stopVoiceInput();
    submitPrompt();
  });
  elements.prompt.addEventListener('keydown', (event) => {
    if (submitting && event.key === 'Enter') { event.preventDefault(); return; }
    if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      elements.composer.requestSubmit();
    }
  });
  elements.prompt.addEventListener('input', () => {
    elements.prompt.style.height = 'auto';
    elements.prompt.style.height = `${Math.min(elements.prompt.scrollHeight, 240)}px`;
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
      elements.prompt.dispatchEvent(new Event('input', { bubbles: true }));
      elements.prompt.focus();
      elements.prompt.setSelectionRange(elements.prompt.value.length, elements.prompt.value.length);
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
    for (const feed of browserCameraFeeds) feed.stop();
    abortNotificationStream();
  });
  window.addEventListener('pageshow', () => {
    if (!document.hidden) {
      refreshConnections();
      refreshCamera();
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
      if (remoteCameraMode && remoteResumeOnVisible) connectRemoteCamera();
      refreshConnections();
      refreshCamera();
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
  await refreshCamera();
  refreshCameraDevices();
  refreshFeedbackSettings();
  refreshArtifacts();
  refreshAgentActivity();
  startNotificationStream();
  window.setInterval(refreshConnections, 15000);
  window.setInterval(refreshCamera, 5000);
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