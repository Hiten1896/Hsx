const state = {
  files: [],
  jobs: [],
  facts: [],
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

  jobList.innerHTML = state.jobs.map((job) => `
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
      ${job.result && job.result.output_path ? `<a href="/api/outputs/${encodeURIComponent(job.result.output_path.split('/').pop())}" class="text-secondary hover:underline font-mono text-[10px]">Download output</a>` : ''}
    </div>
  `).join('');

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
    const [filesRes, memoryRes, jobsRes] = await Promise.all([
      fetch('/api/outputs'),
      fetch('/api/memory'),
      fetch('/api/video/jobs'),
    ]);

    const filesData = await filesRes.json();
    const memoryData = await memoryRes.json();
    const jobsData = await jobsRes.json();

    state.files = Array.isArray(filesData.files) ? filesData.files : [];
    state.facts = Array.isArray(memoryData.facts) ? memoryData.facts : [];
    state.jobs = Array.isArray(jobsData.jobs) ? jobsData.jobs : [];

    renderAssets();
    renderMemory();
    renderJobs();
    renderTranscript();
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

async function submitPrompt() {
  if (!aiPrompt || !aiPrompt.value.trim()) return;

  const payload = {
    project_id: 'project_video',
    prompt: aiPrompt.value.trim(),
    source_files: state.files.map((file) => file.name),
    output_filename: 'edited_video.mp4',
  };

  try {
    const response = await fetch('/api/video/jobs', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });

    const data = await response.json();
    if (data && data.job) {
      aiPrompt.value = '';
      await refreshState();
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
        const text = event.results[0][0].transcript;
        if (aiPrompt) aiPrompt.value = text;
        submitPrompt();
      };
      document.getElementById('voice-talk').addEventListener('click', () => recognition.start());
      document.getElementById('voice-mute').addEventListener('click', (event) => {
        speechMuted = !speechMuted;
        event.currentTarget.textContent = speechMuted ? 'Unmute' : 'Mute';
      });
    }
  } catch (error) {
    console.error('Could not create a video job', error);
  }
}

if (runPromptBtn) {
  runPromptBtn.addEventListener('click', submitPrompt);
}

if (renderBtn) {
  renderBtn.addEventListener('click', submitPrompt);
}

if (ingestBtn && mediaInput) {
  ingestBtn.addEventListener('click', () => mediaInput.click());
}

if (mediaInput) {
  mediaInput.addEventListener('change', uploadMedia);
}

refreshState();
addVoiceControls();