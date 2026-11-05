const sessionId = 'web';
let apiContract;
let pendingQuestion = null;
let submitting = false;

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
};

function initializeCameraFeed() {
  if (!elements.camera) return;
  let retryTimer;
  const connect = () => {
    const streamUrl = new URL(apiContract.endpoints.camera_stream, window.location.origin);
    streamUrl.searchParams.set('retry', Date.now());
    elements.camera.src = streamUrl.toString();
  };
  elements.camera.addEventListener('load', () => {
    elements.cameraStatus.textContent = 'Camera connected';
    window.clearTimeout(retryTimer);
  });
  elements.camera.addEventListener('error', () => {
    elements.cameraStatus.textContent = 'Camera unavailable; retrying…';
    window.clearTimeout(retryTimer);
    retryTimer = window.setTimeout(connect, 5000);
  });
  connect();
}

function updateDesignTokens(tokens) {
  const root = document.documentElement;
  for (const [name, value] of Object.entries(tokens.colors || {})) {
    root.style.setProperty(`--${name.replaceAll('_', '-')}`, value);
  }
  for (const [name, value] of Object.entries(tokens.typography || {})) {
    root.style.setProperty(`--font-${name}`, value);
  }
  root.style.setProperty('--content-width', `${tokens.layout?.content_width || 880}px`);
  root.style.setProperty('--header-height', `${tokens.layout?.header_height || 64}px`);
}

async function apiFetch(url, options = {}) {
  return fetch(url, options);
}

function renderMarkdown(text, container) {
  const source = String(text || '');
  if (window.marked && window.DOMPurify) {
    const html = window.marked.parse(source, { breaks: true, gfm: true });
    container.innerHTML = window.DOMPurify.sanitize(html);
    if (window.hljs) {
      container.querySelectorAll('pre code').forEach((block) => window.hljs.highlightElement(block));
    }
  } else {
    container.textContent = source;
  }
}

function scrollToLatest() {
  elements.stream.scrollTo({ top: elements.stream.scrollHeight, behavior: 'smooth' });
}

function appendMessage(role, text, jobId = '', options = {}) {
  elements.welcome.hidden = true;
  const fragment = elements.template.content.cloneNode(true);
  const article = fragment.querySelector('.message-card');
  const avatar = fragment.querySelector('.avatar');
  const content = fragment.querySelector('.message-content');
  article.dataset.role = role;
  if (jobId) article.dataset.jobId = jobId;
  avatar.textContent = role === 'assistant' ? 'H' : 'Y';
  fragment.querySelector('.message-author').textContent = role === 'assistant' ? 'HSX' : 'You';
  fragment.querySelector('.message-time').textContent = new Date().toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit',
  });
  renderMarkdown(text, content);

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
    status.textContent = 'Thanks!';
  } catch (error) {
    buttons.forEach((button) => {
      button.disabled = false;
      button.removeAttribute('aria-pressed');
    });
    status.textContent = error.message;
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

async function answerPendingQuestion(message) {
  const jobId = pendingQuestion.id;
  const endpoint = apiContract.endpoints.ask_answer.replace('{job_id}', encodeURIComponent(jobId));
  const response = await apiFetch(endpoint, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ answer: message }),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || 'Could not submit the answer.');
  pendingQuestion = null;
  const activity = appendActivity('Continuing the task…');
  await pollQuestionJob(jobId, activity);
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
    if (['done', 'failed', 'cancelled'].includes(job.state)) {
      activity.remove();
      appendMessage('assistant', job.result || job.error || 'The task was cancelled.', job.id);
      return;
    }
  }
}

async function submitPrompt() {
  const message = elements.prompt.value.trim();
  if (!message || submitting) return;
  elements.prompt.value = '';
  elements.prompt.style.height = 'auto';
  appendMessage('user', message);
  submitting = true;
  elements.send.disabled = true;
  if (pendingQuestion) {
    try {
      await answerPendingQuestion(message);
    } catch (error) {
      appendMessage('assistant', `Could not continue: ${error.message}`);
    } finally {
      submitting = false;
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
        message,
        session_id: sessionId,
        target_device: 'client_browser',
      }),
    });
    if (!response.ok) {
      const error = await response.json();
      throw new Error(error.error || 'The request could not be completed.');
    }
    activity = appendActivity('Thinking…');
    await readStream(response, ({ event, data }) => {
      if (event === 'text_delta') {
        assistantContent += data.text || '';
        if (!assistantCard) {
          assistantCard = appendMessage('assistant', assistantContent, '', { noFeedback: true });
          activity.remove();
        } else {
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
    }
  } catch (error) {
    activity?.remove();
    appendMessage('assistant', `Something went wrong: ${error.message}`, currentJob?.id || '');
  } finally {
    submitting = false;
    elements.send.disabled = false;
    elements.prompt.focus();
    refreshTelemetry();
  }
}

async function refreshTelemetry() {
  try {
    const response = await apiFetch(apiContract.endpoints.telemetry);
    if (!response.ok) throw new Error('Telemetry unavailable.');
    const data = await response.json();
    document.getElementById('metric-provider').textContent =
      `${data.model || '—'} · ${data.provider || '—'}`;
    document.getElementById('metric-confidence').textContent =
      data.router_confidence == null ? '—' : `${(Number(data.router_confidence) * 100).toFixed(0)}%`;
    document.getElementById('metric-tokens').textContent =
      `${data.token_usage_estimated ? '~' : ''}${data.input_tokens ?? 0} / ${data.output_tokens ?? 0}`;
    document.getElementById('metric-latency').textContent =
      data.latency_ms == null ? '—' : `${Number(data.latency_ms).toFixed(0)} ms`;
    elements.connection.textContent = 'Connected';
  } catch {
    elements.connection.textContent = 'Offline';
  }
}

async function stopTask() {
  try {
    await apiFetch(apiContract.endpoints.stop, { method: 'POST' });
  } catch (error) {
    appendMessage('assistant', `Could not stop the task: ${error.message}`);
  }
}

async function initialize() {
  const [tokensResponse, contractResponse] = await Promise.all([
    fetch('/web/design-tokens.json'),
    fetch('/web/api-contract.json'),
  ]);
  if (!tokensResponse.ok || !contractResponse.ok) throw new Error('Could not load the HSX interface contract.');
  updateDesignTokens(await tokensResponse.json());
  apiContract = await contractResponse.json();
  elements.composer.addEventListener('submit', (event) => {
    event.preventDefault();
    submitPrompt();
  });
  elements.prompt.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      elements.composer.requestSubmit();
    }
  });
  elements.prompt.addEventListener('input', () => {
    elements.prompt.style.height = 'auto';
    elements.prompt.style.height = `${Math.min(elements.prompt.scrollHeight, 180)}px`;
  });
  elements.stop.addEventListener('click', stopTask);
  initializeCameraFeed();
  document.querySelectorAll('[data-prompt]').forEach((button) => {
    button.addEventListener('click', () => {
      elements.prompt.value = button.dataset.prompt;
      elements.prompt.focus();
    });
  });
  await refreshTelemetry();
  window.setInterval(refreshTelemetry, 2500);
}

initialize().catch((error) => {
  elements.connection.textContent = error.message;
});
