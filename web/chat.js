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
    rename.setAttribute('aria-hidden', 'true');
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
    remove.setAttribute('aria-hidden', 'true');
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
    button.addEventListener('click', () => openActivityFileViewer(path, '', imageFile));
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
  scrollToLatest();
  return activity;
}

const activityImageObjectUrls = new Set();

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
      activityImageObjectUrls.add(img.src);
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
      activityImageObjectUrls.add(img.src);
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
    const obj = document.createElement('object');
    obj.data = `/api/files/content?${query}`;
    obj.type = 'application/pdf';
    obj.className = 'artifact-preview-object';
    const fallback = document.createElement('p');
    fallback.innerHTML = `PDF preview unavailable. <a href="/api/files/download?${query}">Download ${file.name}</a>`;
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

async function openActivityFileViewer(path, imageId = '', binary = false) {
  const name = path ? path.replace(/\\/g, '/').split('/').pop() : 'Preview';
  const type = path ? (path.split('.').pop() || 'file') : 'file';
  const file = { name, path: path || '', type, created: new Date().toISOString(), versions: [] };
  if (imageId) file.imageId = imageId;
  await openArtifactViewer(file, binary ? 'preview' : 'code');
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
    appendActionButton(card, 'Open', () => openActivityFileViewer(diff.path || data.file_path, '', true));
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
  card.addEventListener('click', () => openActivityFileViewer(path, '', /\.(?:png|jpe?g|gif|webp|bmp)$/i.test(name)));
  steps.append(card);
}

function appendCapabilityCards(details, groups) {
  if (!Array.isArray(groups)) return;
  const cards = document.createElement('div');
  cards.className = 'activity-capability-groups';
  groups.forEach((group) => {
    if (!group || typeof group.name !== 'string' || !Array.isArray(group.items)) return;
    const section = document.createElement('section');
    section.className = 'activity-capability-group';
    const heading = document.createElement('h4');
    heading.textContent = group.name;
    section.append(heading);
    group.items.forEach((item) => {
      if (!item || typeof item.name !== 'string') return;
      const card = document.createElement('article');
      card.className = 'activity-capability-card';
      const title = document.createElement('strong');
      title.textContent = item.name;
      const status = document.createElement('span');
      status.className = 'activity-capability-status';
      status.dataset.status = item.status || 'needs setup';
      status.textContent = item.status || 'needs setup';
      const detail = document.createElement('p');
      detail.textContent = item.detail || 'No setup details are available.';
      card.append(title, status, detail);
      section.append(card);
    });
    cards.append(section);
  });
  details.append(cards);
}

function updateActivityGrouping(trace) {
  const completed = [...trace.querySelectorAll('.activity-step[data-status="completed"]')];
  const runningOrFailed = [...trace.querySelectorAll('.activity-step')]
    .filter((step) => ['running', 'failed', 'waiting'].includes(step.dataset.status));
  const summary = trace.querySelector('.activity-group-summary');
  const toggle = trace.querySelector('.activity-show-steps');
  if (completed.length > 6) {
    summary.hidden = false;
    summary.textContent = `${completed.every((step) => step.dataset.past === 'Ran') ? 'Ran' : 'Completed'} ${completed.length} steps`;
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
  step.querySelector('.activity-verb').textContent = `Tool: ${data.tool || 'unknown'}`;
  const target = step.querySelector('.activity-target-chip');
  target.textContent = data.target || data.tool || 'request';
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
  step.querySelector('.activity-metadata').textContent = event === 'tool_start'
    ? ''
    : activityDuration(data.duration_ms || 0);
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
      const command = document.createElement('pre');
      command.className = 'activity-command';
      command.textContent = data.details?.command || data.target || '';
      details.append(command);
      const exit = document.createElement('p');
      exit.textContent = `Exit status: ${data.details?.exit_status ?? (data.status === 'failed' ? 'failed' : 'unknown')}`;
      details.append(exit);
      const output = document.createElement('pre');
      output.className = 'activity-command-output';
      const lines = data.details?.output_lines || [];
      output.textContent = lines.slice(0, 200).join('\n');
      details.append(output);
      if (lines.length > 200) {
        appendActionButton(details, 'Show more', (button) => {
          output.textContent = button.currentTarget.textContent === 'Show more'
            ? lines.join('\n')
            : lines.slice(0, 200).join('\n');
          button.currentTarget.textContent = button.currentTarget.textContent === 'Show more' ? 'Show less' : 'Show more';
        });
      }
    } else if (data.music_player) {
      appendMusicPlayerCard(details, data.music_player);
    } else if (data.tool === 'capabilities' && data.capabilities) {
      appendCapabilityCards(details, data.capabilities.groups);
    } else {
      const tool = document.createElement('p');
      tool.textContent = `Tool: ${data.tool || 'unknown'}`;
      const argumentsBlock = document.createElement('pre');
      argumentsBlock.textContent = JSON.stringify(data.details?.arguments || {}, null, 2);
      const output = document.createElement('p');
      output.textContent = data.output || 'No additional output.';
      details.append(tool, argumentsBlock, output);
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

  const selectTrack = async (index, shouldPlay = false) => {
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
      status.textContent = 'Tap Play to start. Playback never starts automatically.';
      if (shouldPlay) await audio.play();
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
    button.addEventListener('click', () => void selectTrack(index, true));
    queueButtons.push(button);
    item.appendChild(button);
    queue.appendChild(item);
  });
  audio.addEventListener('ended', () => {
    if (activeIndex + 1 < tracks.length) {
      void selectTrack(activeIndex + 1, true);
    } else {
      status.textContent = 'Queue finished.';
    }
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

async function answerPendingQuestion(message, signal) {
  const jobId = pendingQuestion.id;
  const approvalPending = ['waiting_for_approval', 'permission_request'].includes(pendingQuestion.state);
  if (approvalPending && message.trim().toLowerCase() === 'details') {
    const details = elements.permissionDialogContent.querySelector(`[data-approval-job="${CSS.escape(jobId)}"] details`);
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
    if (approvalPending) appendApprovalCard(data.job);
    else if (data.job.error) appendMessage('assistant', data.job.error, data.job.id);
    return;
  }
  if (approvalPending) {
    elements.permissionDialogContent.querySelector(`[data-approval-job="${CSS.escape(jobId)}"]`)?.remove();
    if (elements.permissionDialog.open) elements.permissionDialog.close();
  }
  pendingQuestion = null;
  const activity = appendActivity('Continuing the task…');
  await pollQuestionJob(jobId, activity, signal);
}

function appendApprovalCard(job) {
  elements.welcome.hidden = true;
  setConversationEmpty(false);
  const approval = job.approval || {};
  const isPermissionRequest = job.state === 'permission_request' || approval.request_type === 'permission_request';
  const card = document.createElement('section');
  card.className = 'approval-card';
  card.dataset.approvalJob = job.id;
  card.setAttribute('aria-label', 'Hsx action approval');
  const title = document.createElement('h3');
  title.id = 'permission-dialog-title';
  title.textContent = isPermissionRequest
    ? 'Permission needed'
    : approval.request_type === 'package_install'
      ? 'Hsx can add this ability'
      : approval.request_type === 'tool_consent'
        ? 'Review before Hsx acts'
        : 'Hsx wants to:';
  const summary = document.createElement('p');
  summary.textContent = approval.summary || 'Run a tool';
  const why = document.createElement('p');
  why.textContent = `${approval.request_type === 'tool_consent' ? 'Possible consequence' : 'Why'}: ${approval.why || 'This action needs your approval.'}`;
  const policyNote = document.createElement('p');
  policyNote.textContent = isPermissionRequest
    ? 'Elevated execution is limited to a non-interactive sudo attempt. Your OS password is never requested or stored.'
    : '';
  const details = document.createElement('details');
  const detailsSummary = document.createElement('summary');
  detailsSummary.textContent = 'Details';
  const detailsText = document.createElement('pre');
  detailsText.textContent = approval.details || 'No additional details.';
  details.append(detailsSummary, detailsText);
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
  if (policyNote.textContent) card.append(title, summary, why, policyNote, details);
  else card.append(title, summary, why, details);
  if (job.error) {
    const error = document.createElement('p');
    error.className = 'approval-error';
    error.textContent = job.error;
    card.appendChild(error);
  }
  card.appendChild(actions);
  elements.permissionDialogContent.replaceChildren(card);
  if (!elements.permissionDialog.open) elements.permissionDialog.showModal();
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
  let cameraSnapshotPending = false;
  let activity;
  const clientMessageId = makeClientMessageId();
  let lastEventId = 0;
  let retries = 0;
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
          assistantCard = appendMessage('assistant', assistantContent, '', { noFeedback: true });
          if (!activity?.querySelector('.activity-step')) activity?.remove();
        } else {
          assistantCard.dataset.response = assistantContent;
          renderMarkdown(assistantContent, assistantCard.querySelector('.message-content'));
          scrollToLatest();
        }
      } else if (event === 'job' && data.id) {
        currentJob = data;
        activeRunId = data.id;
        activity.dataset.jobId = data.id;
        if (data.state === 'waiting_for_user') {
          pendingQuestion = data;
          if (!activity?.querySelector('.activity-step')) activity?.remove();
          appendMessage('assistant', data.question || 'I need a little more information.', data.id);
        }
        if (['waiting_for_approval', 'permission_request'].includes(data.state)) {
          pendingQuestion = data;
          if (!activity?.querySelector('.activity-step')) activity?.remove();
          appendApprovalCard(data);
        }
        if (['done', 'failed', 'cancelled'].includes(data.state)) {
          if (!activity?.querySelector('.activity-step')) activity?.remove();
          const text = assistantContent || data.result || data.error || 'The task was cancelled.';
          assistantCard?.remove();
          assistantCard = appendMessage('assistant', text, data.id, { prompt: requestMessage });
          if (cameraSnapshotPending) void appendInlineCameraSnapshot(assistantCard);
        }
      } else if (event === 'error') {
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
  if (apiContract) {
    void apiFetch(apiContract.endpoints.camera_stop, {
      method: 'POST',
      keepalive: true,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: sessionId }),
    });
  }
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
    const startResponse = await apiFetch(apiContract.endpoints.camera_start, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: sessionId }),
    });
    if (!startResponse.ok) throw new Error('Camera could not be started.');
    const response = await apiFetch(apiContract.endpoints.camera_status);
    const data = await response.json();
    const host = data.host || {};
    const hostLabel = [host.hostname, host.hardware].filter(Boolean).join(' · ');
    document.getElementById('camera-heading').title =
      hostLabel || 'Host hardware information unavailable';
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
    document.getElementById('camera-heading').title = 'Host hardware information unavailable';
    if (cameraHasFrame) setCameraReconnecting('Camera feed offline; retrying…');
    else setCameraStatus(false, 'Camera feed offline');
    scheduleCameraReconnect();
  }
}

function handleCameraStateEvent(data) {
  const active = Boolean(data && data.active);
  if (active) {
    if (!cameraEnabled) {
      cameraEnabled = true;
      openMobilePanel(elements.cameraSidebar);
      setCameraStatus(false, 'Live feed active…');
      refreshCamera();
      window.clearTimeout(cameraAutoOffTimer);
      cameraAutoOffTimer = window.setTimeout(stopCameraAndStandby, 5 * 60 * 1000);
    }
  } else {
    if (cameraEnabled) {
      stopCameraAndStandby();
    }
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
    const streamUrl = new URL(apiContract.endpoints.camera_stream, window.location.href);
    streamUrl.searchParams.set('session_id', sessionId);
    const response = await apiFetch(streamUrl.toString(), {
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
      const connectionPanel = document.getElementById('connections-panel');
      const connectionWasOpen = !connectionPanel.hidden;
      closeMobilePanels();
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
      appendMessage('assistant', pendingQuestion.question || 'I need a little more information.', pendingQuestion.id);
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
