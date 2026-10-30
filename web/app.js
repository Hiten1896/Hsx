const nativeFetch = window.fetch.bind(window);
let authToken = sessionStorage.getItem('hsx-auth-token') || '';

function showLogin() {
  if (document.getElementById('hsx-login')) return;
  const panel = document.createElement('form');
  panel.id = 'hsx-login';
  panel.setAttribute('aria-label', 'Hsx login');
  panel.style.cssText = 'position:fixed;inset:0;z-index:1000;display:grid;place-items:center;background:#090b12;color:#f8fafc;font:14px monospace';
  panel.innerHTML = '<div style="width:min(420px,90vw);padding:24px;border:1px solid #38bdf8;background:#111827"><h1>Hsx access</h1><p>Enter the bearer token printed by the Hsx server.</p><input id="hsx-token" type="password" autocomplete="off" required style="width:100%;padding:8px;background:#020617;color:#f8fafc;border:1px solid #64748b"><button style="margin-top:12px;padding:8px 14px" type="submit">Connect</button><p id="hsx-login-error" role="alert"></p></div>';
  panel.addEventListener('submit', async (event) => {
    event.preventDefault();
    const input = panel.querySelector('#hsx-token');
    const candidate = input.value.trim();
    const response = await nativeFetch('/health', { headers: { Authorization: `Bearer ${candidate}` } });
    if (!response.ok) {
      panel.querySelector('#hsx-login-error').textContent = 'Invalid token.';
      return;
    }
    authToken = candidate;
    sessionStorage.setItem('hsx-auth-token', candidate);
    panel.remove();
    refreshState();
    openJobFromUrl();
    startNotificationStream();
    touchPushActivity();
  });
  document.body.appendChild(panel);
  panel.querySelector('#hsx-token').focus();
}

window.fetch = async (input, init = {}) => {
  if (!authToken && String(input).includes('/api/')) {
    showLogin();
    throw new Error('Hsx authentication required');
  }
  const headers = new Headers(init.headers || {});
  if (authToken) headers.set('Authorization', `Bearer ${authToken}`);
  const response = await nativeFetch(input, { ...init, headers });
  if (response.status === 401) {
    authToken = '';
    sessionStorage.removeItem('hsx-auth-token');
    showLogin();
  }
  return response;
};

if (!authToken) showLogin();

const state = {
  files: [],
  workspaceFiles: [],
  jobs: [],
  facts: [],
  notifications: [],
  unread: 0,
  activeQuestion: null,
  handledDeviceActions: new Set(),
};

const assetList = document.getElementById('asset-list');
const jobList = document.getElementById('job-list');
const jobCount = document.getElementById('job-count');
const transcriptPanel = document.getElementById('transcript-panel');
const memoryList = document.getElementById('memory-list');
const mediaInput = document.getElementById('media-input');
const aiPrompt = document.getElementById('ai-prompt');
const runPromptBtn = document.getElementById('run-prompt');
const renderBtn = document.getElementById('render-video-btn');
const ingestBtn = document.getElementById('ingest-media-btn');
const chatHistory = document.getElementById('chat-history');
const questionCard = document.getElementById('question-card');
const filesList = document.getElementById('files-list');
const filesPreview = document.getElementById('files-preview');
const notificationList = document.getElementById('notification-list');
let speechMuted = false;

function escapeHtml(value) {
  return String(value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

function setJobStatusText() {
  const active = state.jobs.filter((job) => job.status && ['queued', 'running'].includes(job.status)).length;
  if (jobCount) {
    jobCount.textContent = `${active} ACTIVE`;
  }
}

function renderAssets() {
  if (!assetList) return;

  if (!state.files.length) {
    assetList.innerHTML = `
      <div class="text-label-caps font-label-caps text-outline uppercase px-1 pt-1">Footage Staging</div>
      <div class="mt-3 p-3 border border-dashed border-outline-variant rounded text-center bg-surface-container-lowest/40">
        <span class="material-symbols-outlined text-[20px] text-outline" data-icon="cloud_upload">cloud_upload</span>
        <div class="text-body-sm font-body-sm text-on-surface-variant mt-1">No media yet</div>
      </div>
    `;
    return;
  }

  assetList.innerHTML = `
    <div class="text-label-caps font-label-caps text-outline uppercase px-1 pt-1">Footage Staging</div>
    ${state.files.map((file, index) => `
      <div class="group flex items-center gap-2 p-1.5 rounded ${index === 0 ? 'bg-surface-container border-l-2 border-primary border-t border-r border-b border-outline-variant' : 'bg-surface-container-low hover:bg-surface-container border border-transparent hover:border-outline-variant'} cursor-pointer transition-colors">
        <span class="material-symbols-outlined text-[14px] text-outline group-hover:text-on-surface cursor-grab" data-icon="drag_indicator">drag_indicator</span>
        <div class="w-10 h-7 rounded bg-surface-container-highest overflow-hidden relative shrink-0">
          <span class="material-symbols-outlined text-[16px] text-secondary flex items-center justify-center w-full h-full" data-icon="video_file">video_file</span>
        </div>
        <div class="flex-1 min-w-0">
          <div class="text-body-sm font-body-sm ${index === 0 ? 'text-on-surface' : 'text-on-surface-variant'} truncate">${escapeHtml(file.name)}</div>
          <div class="text-telemetry-xs font-telemetry-xs text-outline flex items-center gap-2">
            <span>${escapeHtml(file.type || 'media')}</span>
          </div>
        </div>
      </div>
    `).join('')}
  `;
}

function renderJobs() {
  if (!jobList) return;

  if (!state.jobs.length) {
    jobList.innerHTML = `
      <div class="p-2 rounded bg-surface-container-lowest border border-outline-variant flex items-center justify-between text-telemetry-xs font-telemetry-xs">
        <div class="flex items-center gap-1.5 text-on-surface-variant">
          <span class="material-symbols-outlined text-[15px] text-outline" data-icon="task_alt">task_alt</span>
          <span>No render jobs yet</span>
        </div>
      </div>
    `;
    setJobStatusText();
    return;
  }

  jobList.innerHTML = state.jobs.map((job) => {
    const outputName = job.result && job.result.output_path
      ? String(job.result.output_path).split(/[\\/]/).pop()
      : '';
    const outputFile = state.workspaceFiles.find((file) => file.name === outputName);
    const outputLink = outputFile
      ? `<button type="button" data-download-path="${escapeHtml(outputFile.path)}" class="text-secondary hover:underline font-mono text-[10px]">Download output</button>`
      : '';
    return `
    <div class="bg-surface-container-low border border-outline-variant rounded p-2.5 space-y-2">
      <div class="flex items-center justify-between">
        <div class="text-code-sm font-code-sm font-semibold text-on-surface truncate">${escapeHtml(job.project_id || 'video-job')}</div>
        <span class="text-[10px] font-mono ${job.status === 'completed' ? 'text-tertiary bg-tertiary/10 border border-tertiary/30' : job.status === 'failed' ? 'text-error bg-error/10 border border-error/30' : 'text-secondary bg-secondary/10 border border-secondary/30'} px-1.5 py-0.5 rounded">
          ${escapeHtml((job.status || 'queued').toUpperCase())}
        </span>
      </div>
      <div class="flex justify-between text-telemetry-xs font-telemetry-xs text-outline font-mono">
        <span>${escapeHtml(job.job_id ? `Job ${job.job_id.slice(0, 6)}` : 'Job')}</span>
        <span>${escapeHtml(job.updated_at ? new Date(job.updated_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : 'just now')}</span>
      </div>
      ${outputLink}
    </div>
  `;
  }).join('');
  jobList.querySelectorAll('[data-download-path]').forEach((button) => {
    button.addEventListener('click', () => {
      const path = button.getAttribute('data-download-path');
      const file = state.workspaceFiles.find((candidate) => candidate.path === path);
      if (file) downloadFile(file);
    });
  });

  setJobStatusText();
}

function renderMemory() {
  if (!memoryList) return;

  if (!state.facts.length) {
    memoryList.innerHTML = '<div class="p-2 rounded bg-surface-container-low border border-outline-variant text-on-surface-variant text-telemetry-xs">No saved facts yet.</div>';
    return;
  }

  memoryList.innerHTML = state.facts.slice(-3).reverse().map((fact) => `
    <div class="p-2 rounded bg-surface-container-low border border-outline-variant text-on-surface-variant text-telemetry-xs">${escapeHtml(typeof fact === 'string' ? fact : (fact.summary || JSON.stringify(fact)))}</div>
  `).join('');
}

function renderTranscript() {
  if (!transcriptPanel) return;

  const transcriptJob = state.jobs.find((job) => job.result && job.result.transcript);
  if (!transcriptJob) {
    transcriptPanel.innerHTML = `
      <div class="flex items-start gap-1.5 text-tertiary">
        <span class="material-symbols-outlined text-[13px] shrink-0 mt-0.5" data-icon="check_circle">check_circle</span>
        <div><span class="font-semibold">[Local AI]</span> Transcript will appear here once a session is processed.</div>
      </div>
    `;
    return;
  }

  transcriptPanel.innerHTML = `
    <div class="flex items-start gap-1.5 text-tertiary">
      <span class="material-symbols-outlined text-[13px] shrink-0 mt-0.5" data-icon="check_circle">check_circle</span>
      <div><span class="font-semibold">[Whisper]</span> ${escapeHtml(transcriptJob.result.transcript.slice(0, 180) || 'Transcript generated')}</div>
    </div>
  `;
}

async function refreshState() {
  try {
    const [filesRes, workspaceRes, memoryRes, jobsRes, notificationsRes, askJobsRes] = await Promise.all([
      fetch('/api/outputs'),
      fetch('/api/files?limit=100'),
      fetch('/api/memory'),
      fetch('/api/video/jobs'),
      fetch('/api/notifications'),
      fetch('/api/ask-jobs'),
    ]);

    const filesData = await filesRes.json();
    const workspaceData = await workspaceRes.json();
    const memoryData = await memoryRes.json();
    const jobsData = await jobsRes.json();
    const notificationsData = await notificationsRes.json();
    const askJobsData = await askJobsRes.json();

    state.files = Array.isArray(filesData.files) ? filesData.files : [];
    state.workspaceFiles = Array.isArray(workspaceData.files) ? workspaceData.files : [];
    state.facts = Array.isArray(memoryData.facts) ? memoryData.facts : [];
    state.jobs = Array.isArray(jobsData.jobs) ? jobsData.jobs : [];
    state.notifications = Array.isArray(notificationsData.notifications) ? notificationsData.notifications : [];
    state.unread = Number(notificationsData.unread || 0);

    renderAssets();
    renderMemory();
    renderJobs();
    renderTranscript();
    renderWorkspaceFiles();
    renderNotifications();
    if (!state.activeQuestion) {
      const active = (Array.isArray(askJobsData.jobs) ? askJobsData.jobs : [])
        .find((job) => ['running', 'waiting_for_user'].includes(job.state));
      if (active) {
        state.activeQuestion = active;
        if (active.state === 'waiting_for_user') showQuestion(active);
        pollAskJob(active.id);
      }
    }
  } catch (error) {
    console.error('Failed to refresh dashboard state', error);
  }
}

async function uploadMedia(event) {
  const selected = [...(event.target.files || [])];
  if (!selected.length) return;

  for (const file of selected) {
    try {
      const content = await file.text();
      await fetch('/api/upload', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ filename: file.name, content }),
      });
    } catch (err) {
      console.error('Upload failed', err);
    }
  }

  if (mediaInput) {
    mediaInput.value = '';
  }
  await refreshState();
}

function appendChatMessage(role, text) {
  if (!chatHistory) return;
  const entry = document.createElement('div');
  entry.className = 'rounded border border-outline-variant bg-surface-container-low p-2 text-body-sm';
  const label = document.createElement('strong');
  label.className = 'mr-2 text-secondary';
  label.textContent = role === 'user' ? 'You' : 'Hsx';
  const message = document.createElement('span');
  message.textContent = text;
  entry.append(label, message);
  chatHistory.appendChild(entry);
  chatHistory.scrollTop = chatHistory.scrollHeight;
}

async function submitPrompt() {
  if (!aiPrompt || !aiPrompt.value.trim()) return;
  const message = aiPrompt.value.trim();
  aiPrompt.value = '';
  appendChatMessage('user', message);
  const approvals = {};
  if (/\b(camera|webcam|snapshot|photo|record|play|audio|music)\b/i.test(message)) {
    const pin = window.prompt('Owner PIN for requested local media actions (leave blank to skip):');
    if (pin) approvals.audio_play = approvals.camera_record = pin;
  }
  try {
    const response = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message, session_id: 'web', target_device: 'client_browser', approvals }),
    });
    const data = await response.json();
    if (!response.ok && response.status !== 202) throw new Error(data.error || 'The request could not be completed.');
    if (data.job) {
      state.activeQuestion = data.job;
      if (data.job.state === 'waiting_for_user') showQuestion(data.job);
      pollAskJob(data.job.id);
    } else if (data.response) {
      appendChatMessage('assistant', data.response);
    }
    await refreshState();
  } catch (error) {
    appendChatMessage('assistant', `I could not start that request: ${error.message}`);
  }
}

async function submitVideoPrompt() {
  if (!aiPrompt || !aiPrompt.value.trim()) return;
  const payload = {
    project_id: 'project_video',
    prompt: aiPrompt.value.trim(),
    source_files: state.files.map((file) => file.path),
    output_filename: 'edited_video.mp4',
  };
  try {
    const response = await fetch('/api/video/jobs', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Could not create the video job.');
    if (data.job) {
      aiPrompt.value = '';
      await refreshState();
    }
  } catch (error) {
    appendChatMessage('assistant', `I could not create the video job: ${error.message}`);
  }
}

function showQuestion(job) {
  if (!questionCard || !job) return;
  state.activeQuestion = job;
  questionCard.classList.remove('hidden');
  document.getElementById('question-text').textContent = job.question || 'Hsx needs more information.';
  document.getElementById('question-status').textContent = `Question ${job.question_count || 1} of 3`;
  const options = document.getElementById('question-options');
  options.replaceChildren();
  (job.options || []).forEach((choice) => {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'rounded border border-outline-variant px-2 py-1 text-body-sm hover:border-primary';
    button.textContent = choice;
    button.addEventListener('click', () => {
      const input = document.getElementById('question-answer');
      input.value = choice;
      if (!job.allow_free_text) document.getElementById('question-form').requestSubmit();
    });
    options.appendChild(button);
  });
  const answer = document.getElementById('question-answer');
  answer.hidden = !job.allow_free_text && !(job.options || []).length;
}

async function pollAskJob(jobId) {
  while (state.activeQuestion && state.activeQuestion.id === jobId) {
    await new Promise((resolve) => setTimeout(resolve, 2000));
    try {
      const response = await fetch(`/api/ask-jobs/${encodeURIComponent(jobId)}`);
      const data = await response.json();
      const job = data.job;
      if (!job) return;
      if (job.state === 'waiting_for_user') {
        showQuestion(job);
      } else if (['done', 'failed', 'cancelled'].includes(job.state)) {
        await handleBrowserActions(job);
        state.activeQuestion = null;
        questionCard.classList.add('hidden');
        appendChatMessage('assistant', job.result || job.error || 'The task was cancelled.');
        await refreshState();
        return;
      }
    } catch (error) {
      console.error('Could not refresh the pending question', error);
    }
  }
}

async function handleBrowserActions(job) {
  for (const [index, action] of (job.device_actions || []).entries()) {
    const key = `${job.id}:${index}`;
    if (state.handledDeviceActions.has(key)) continue;
    state.handledDeviceActions.add(key);
    const card = document.createElement('div');
    card.className = 'rounded border border-outline-variant bg-surface-container-low p-2 text-body-sm';
    const label = document.createElement('span');
    label.textContent = action.type === 'camera_record'
      ? `HSX requests ${action.mode} capture from this browser's camera (${action.duration_seconds || 1}s). `
      : 'HSX requests audio playback in this browser. ';
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'ml-2 rounded border border-secondary px-2 py-1 text-secondary';
    button.textContent = action.type === 'camera_record' ? 'Allow camera capture' : 'Play audio';
    card.append(label, button);
    chatHistory?.appendChild(card);
    button.addEventListener('click', async () => {
      button.disabled = true;
      try {
        if (action.type === 'audio_play') {
          let source = action.source_url;
          if (action.source_path) {
            const response = await fetch(`/api/outputs/${encodeURIComponent(action.source_path)}`);
            if (!response.ok) throw new Error('Could not load the requested audio file.');
            source = URL.createObjectURL(await response.blob());
          }
          if (!source) throw new Error('No browser-playable audio source was returned.');
          const player = new Audio(source);
          await player.play();
          card.append(document.createTextNode('Playback started.'));
          player.addEventListener('ended', () => {
            if (source.startsWith('blob:')) URL.revokeObjectURL(source);
          }, { once: true });
        } else if (action.type === 'camera_record') {
          await captureBrowserMedia(action, card);
        } else {
          throw new Error(`Unsupported browser media action: ${action.type}`);
        }
      } catch (error) {
        card.append(document.createTextNode(` ${error.message}`));
        button.disabled = false;
      }
    });
  }
}

async function captureBrowserMedia(action, card) {
  if (!navigator.mediaDevices?.getUserMedia) {
    throw new Error('This browser does not support camera capture.');
  }
  const stream = await navigator.mediaDevices.getUserMedia({ video: true, audio: false });
  try {
    let blob;
    let mimeType;
    if (action.mode === 'snapshot') {
      const video = document.createElement('video');
      video.muted = true;
      video.srcObject = stream;
      await video.play();
      await new Promise((resolve) => setTimeout(resolve, 300));
      const canvas = document.createElement('canvas');
      canvas.width = video.videoWidth;
      canvas.height = video.videoHeight;
      canvas.getContext('2d').drawImage(video, 0, 0);
      blob = await new Promise((resolve) => canvas.toBlob(resolve, 'image/jpeg', 0.9));
      mimeType = 'image/jpeg';
    } else {
      const recorder = new MediaRecorder(stream);
      const parts = [];
      recorder.addEventListener('dataavailable', (event) => {
        if (event.data.size) parts.push(event.data);
      });
      const stopped = new Promise((resolve, reject) => {
        recorder.addEventListener('stop', resolve, { once: true });
        recorder.addEventListener('error', () => reject(new Error('Browser recording failed.')), { once: true });
      });
      recorder.start();
      await new Promise((resolve) => setTimeout(resolve, Math.max(1, Math.min(action.duration_seconds || 10, 60)) * 1000));
      recorder.stop();
      await stopped;
      mimeType = recorder.mimeType || 'video/webm';
      blob = new Blob(parts, { type: mimeType });
    }
    if (!blob) throw new Error('The browser did not produce a media file.');
    const extension = action.mode === 'snapshot' ? '.jpg' : (mimeType.includes('mp4') ? '.mp4' : '.webm');
    let filename = action.filename || `browser_capture${extension}`;
    if (action.mode === 'video' && !filename.toLowerCase().endsWith(extension)) {
      filename = `${filename.replace(/\.[^.]+$/, '')}${extension}`;
    }
    const response = await fetch('/api/media/upload', {
      method: 'POST',
      headers: { 'Content-Type': mimeType, 'X-Filename': filename },
      body: blob,
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Could not save the browser capture.');
    card.append(document.createTextNode(` Saved ${result.path} (${result.bytes} bytes).`));
    await refreshState();
  } finally {
    stream.getTracks().forEach((track) => track.stop());
  }
}

function renderWorkspaceFiles() {
  if (!filesList) return;
  filesList.replaceChildren();
  for (const file of state.workspaceFiles) {
    const row = document.createElement('div');
    row.className = 'flex items-center justify-between gap-2 rounded bg-surface-container-low px-2 py-1 text-body-sm';
    const label = document.createElement('span');
    label.className = 'min-w-0 flex-1 truncate';
    label.textContent = `${file.path} · ${file.type || 'file'} · ${formatBytes(file.size)}`;
    const preview = document.createElement('button');
    preview.type = 'button';
    preview.className = 'text-secondary hover:underline';
    preview.textContent = 'Preview';
    preview.addEventListener('click', () => previewFile(file));
    const download = document.createElement('button');
    download.type = 'button';
    download.className = 'text-secondary hover:underline';
    download.textContent = 'Download';
    download.addEventListener('click', () => downloadFile(file));
    row.append(label, preview, download);
    filesList.appendChild(row);
  }
}

function formatBytes(size) {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

async function previewFile(file) {
  if (!filesPreview) return;
  filesPreview.replaceChildren();
  filesPreview.classList.remove('hidden');
  const query = new URLSearchParams({ path: file.path });
  const extension = (file.type || '').toLowerCase();
  if (['pdf', 'png', 'jpg', 'jpeg', 'gif', 'webp'].includes(extension)) {
    const response = await fetch(`/api/files/content?${query}`);
    if (!response.ok) throw new Error('Could not preview this file.');
    const objectUrl = URL.createObjectURL(await response.blob());
    const element = extension === 'pdf' ? document.createElement('iframe') : document.createElement('img');
    element.src = objectUrl;
    element.className = 'w-full h-64 rounded';
    if (extension !== 'pdf') element.alt = file.name;
    filesPreview.appendChild(element);
    return;
  }
  const response = await fetch(`/api/files/preview?${query}`);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || 'Could not preview this file.');
  const text = document.createElement('pre');
  text.className = 'whitespace-pre-wrap break-words text-body-sm';
  text.textContent = data.content;
  filesPreview.appendChild(text);
}

async function downloadFile(file) {
  const query = new URLSearchParams({ path: file.path });
  const response = await fetch(`/api/files/download?${query}`);
  if (!response.ok) throw new Error('Could not download this file.');
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement('a');
  link.href = url;
  link.download = file.name;
  link.click();
  URL.revokeObjectURL(url);
}

function renderNotifications() {
  const unread = document.getElementById('notification-unread');
  if (unread) unread.textContent = String(state.unread);
  if (!notificationList) return;
  notificationList.replaceChildren();
  for (const item of state.notifications) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = `block w-full rounded px-2 py-1 text-left text-body-sm ${item.read ? 'bg-surface-container-low text-outline' : 'bg-surface-container text-on-surface'}`;
    button.textContent = item.title;
    button.addEventListener('click', () => openNotification(item));
    notificationList.appendChild(button);
  }
}

async function openNotification(item) {
  await fetch('/api/notifications/read', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ id: item.id }),
  });
  if (item.result_path) {
    const file = state.workspaceFiles.find((candidate) => candidate.path === item.result_path);
    if (file) await previewFile(file);
  } else if (item.job_id) {
    const response = await fetch(`/api/ask-jobs/${encodeURIComponent(item.job_id)}`);
    const data = await response.json();
    if (data.job && data.job.state === 'waiting_for_user') showQuestion(data.job);
    else if (data.job && data.job.result) appendChatMessage('assistant', data.job.result);
  }
  await refreshState();
}

async function searchWorkspace() {
  const query = new URLSearchParams();
  const text = document.getElementById('files-search').value.trim();
  const fileType = document.getElementById('files-type').value;
  if (text) query.set('q', text);
  if (fileType) query.set('type', fileType);
  const response = await fetch(`/api/files?${query}`);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || 'Workspace search failed.');
  state.workspaceFiles = data.files || [];
  renderWorkspaceFiles();
}

async function openJobFromUrl() {
  const params = new URLSearchParams(window.location.search);
  const jobId = params.get('job');
  const filePath = params.get('file');
  if (filePath) {
    const file = state.workspaceFiles.find((candidate) => candidate.path === filePath);
    if (file) await previewFile(file);
  }
  if (jobId) {
    const response = await fetch(`/api/ask-jobs/${encodeURIComponent(jobId)}`);
    const data = await response.json();
    if (data.job && data.job.state === 'waiting_for_user') {
      showQuestion(data.job);
      pollAskJob(jobId);
    } else if (data.job && data.job.result) {
      appendChatMessage('assistant', data.job.result);
    }
  }
}

async function markAllNotificationsRead() {
  await fetch('/api/notifications/read', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({}),
  });
  await refreshState();
}

let eventStreamRetry = null;
async function startNotificationStream() {
  if (eventStreamRetry) clearTimeout(eventStreamRetry);
  try {
    const lastId = Number(localStorage.getItem('hsx-last-notification-id') || '0');
    const response = await fetch(`/api/notifications/stream?after=${lastId}`);
    if (!response.ok || !response.body) throw new Error('Notification stream unavailable.');
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const events = buffer.split('\n\n');
      buffer = events.pop() || '';
      for (const eventText of events) {
        const idLine = eventText.split('\n').find((line) => line.startsWith('id: '));
        if (idLine) localStorage.setItem('hsx-last-notification-id', idLine.slice(4));
        const dataLine = eventText.split('\n').find((line) => line.startsWith('data: '));
        if (dataLine) refreshState();
      }
    }
  } catch (error) {
    console.error('Notification stream disconnected', error);
  }
  eventStreamRetry = setTimeout(startNotificationStream, 3000);
}

function decodeApplicationKey(encoded) {
  const padded = encoded.replace(/-/g, '+').replace(/_/g, '/').padEnd(Math.ceil(encoded.length / 4) * 4, '=');
  return Uint8Array.from(atob(padded), (character) => character.charCodeAt(0));
}

async function enablePush() {
  const status = document.getElementById('push-status');
  try {
    if (!('serviceWorker' in navigator) || !('PushManager' in window)) {
      throw new Error('Push notifications are not supported by this browser.');
    }
    const configResponse = await fetch('/api/notifications/webpush/config');
    const config = await configResponse.json();
    if (!config.enabled) throw new Error('Web Push has not been configured on this server.');
    const permission = await Notification.requestPermission();
    if (permission !== 'granted') throw new Error('Notification permission was not granted.');
    const registration = await navigator.serviceWorker.ready;
    let pushSubscription = await registration.pushManager.getSubscription();
    if (!pushSubscription) {
      pushSubscription = await registration.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: decodeApplicationKey(config.public_key),
      });
    }
    const response = await fetch('/api/notifications/webpush/subscribe', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        subscription: pushSubscription.toJSON(),
        device_name: `${navigator.platform || 'Device'} browser`,
      }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Could not register this device.');
    localStorage.setItem('hsx-push-subscription-id', data.subscription.id);
    status.textContent = 'Push notifications enabled on this device.';
  } catch (error) {
    status.textContent = error.message;
  }
}

async function disablePush() {
  const status = document.getElementById('push-status');
  const subscriptionId = localStorage.getItem('hsx-push-subscription-id');
  if (!('serviceWorker' in navigator)) {
    status.textContent = 'Service workers are not supported by this browser.';
    return;
  }
  const registration = await navigator.serviceWorker.ready;
  const subscription = await registration.pushManager.getSubscription();
  if (subscription) await subscription.unsubscribe();
  if (subscriptionId) {
    await fetch(`/api/notifications/webpush/subscriptions/${encodeURIComponent(subscriptionId)}`, { method: 'DELETE' });
    localStorage.removeItem('hsx-push-subscription-id');
  }
  status.textContent = 'Push notifications disabled on this device.';
}

async function testPush() {
  const response = await fetch('/api/notifications/webpush/test', { method: 'POST' });
  const data = await response.json();
  document.getElementById('push-status').textContent = response.ok ? 'Test notification sent.' : (data.error || 'Push test failed.');
}

async function touchPushActivity() {
  const activePushId = localStorage.getItem('hsx-push-subscription-id');
  if (!authToken || !activePushId) return;
  await fetch('/api/notifications/webpush/activity', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ id: activePushId }),
  });
}

function addVoiceControls() {
  if (!('webkitSpeechRecognition' in window || 'SpeechRecognition' in window)) return;
  const controls = document.createElement('div');
  controls.className = 'fixed bottom-3 right-3 z-40 flex gap-2';
  controls.innerHTML = '<button id="voice-talk" class="px-3 py-2 rounded bg-primary text-on-primary text-xs">Push to talk</button><button id="voice-mute" class="px-3 py-2 rounded bg-surface-container text-xs">Mute</button>';
  document.body.appendChild(controls);
  const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  const recognition = new Recognition();
  recognition.lang = 'en-US';
  recognition.onresult = (event) => {
    if (aiPrompt) aiPrompt.value = event.results[0][0].transcript;
    submitPrompt();
  };
  document.getElementById('voice-talk').addEventListener('click', () => recognition.start());
  document.getElementById('voice-mute').addEventListener('click', (event) => {
    speechMuted = !speechMuted;
    event.currentTarget.textContent = speechMuted ? 'Unmute' : 'Mute';
  });
}

if (runPromptBtn) {
  runPromptBtn.addEventListener('click', submitPrompt);
}

if (renderBtn) {
  renderBtn.addEventListener('click', submitVideoPrompt);
}

if (ingestBtn && mediaInput) {
  ingestBtn.addEventListener('click', () => mediaInput.click());
}

if (mediaInput) {
  mediaInput.addEventListener('change', uploadMedia);
}

document.getElementById('question-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const job = state.activeQuestion;
  const input = document.getElementById('question-answer');
  if (!job || !input.value.trim()) return;
  document.getElementById('question-status').textContent = 'Resuming the task…';
  const response = await fetch(`/api/ask-jobs/${encodeURIComponent(job.id)}/answer`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ answer: input.value.trim() }),
  });
  const data = await response.json();
  if (!response.ok) {
    document.getElementById('question-status').textContent = data.error || 'Could not submit that answer.';
    return;
  }
  input.value = '';
  questionCard.classList.add('hidden');
  await refreshState();
});

document.getElementById('files-refresh').addEventListener('click', searchWorkspace);
document.getElementById('files-search').addEventListener('input', () => {
  clearTimeout(window.hsxFileSearchTimer);
  window.hsxFileSearchTimer = setTimeout(searchWorkspace, 250);
});
document.getElementById('files-type').addEventListener('change', searchWorkspace);
document.getElementById('notifications-read-all').addEventListener('click', markAllNotificationsRead);
document.getElementById('push-enable').addEventListener('click', enablePush);
document.getElementById('push-disable').addEventListener('click', disablePush);
document.getElementById('push-test').addEventListener('click', testPush);

if (authToken) {
  refreshState();
  openJobFromUrl();
  startNotificationStream();
}
touchPushActivity().catch((error) => console.error('Could not update push-device activity', error));
if ('serviceWorker' in navigator) {
  navigator.serviceWorker.register('/web/service-worker.js').catch((error) => console.error('PWA setup failed', error));
}
setInterval(() => {
  if (authToken) refreshState();
}, 15000);
addVoiceControls();
