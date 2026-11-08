# Vision and camera design audit

## Scope and evidence

This is the V0 read-only audit for the HP all-in-one target (AMD E1-1200,
2 cores, 1.4 GHz, 2 GB RAM, Debian 13, USB camera at `/dev/video0`). The audit
followed `agent/hardware/camera.py`, `camera_stream.py` and
`motion_monitor.py`, `agent/tools/vision_capture.py`, the camera routes and
notification wiring in `server.py`, the camera panel and worker in `web/`,
and the modules under `agent/ml/`. Existing coverage is in
`tests/test_hardware_services.py`, `tests/test_ml_modules.py`, and related
notification tests.

The development environment is Windows and has no access to the target camera
or HP hardware. Consequently, no HP CPU, RAM, frame-rate, or latency figure is
claimed here. The benchmark in V0 is intended to collect those measurements on
the target.

## What exists today

### Capture and live view

- `CameraService` owns one FFmpeg process and publishes the newest validated
  JPEG frame in memory. It does not keep a frame queue. Frame payloads are
  capped at 2 MiB and considered stale after 3 seconds.
- Capture is configured for 640x480 at 15 fps. FFmpeg reads V4L2, applies a
  scale filter, and encodes MJPEG at quality 8; this is not yet a native-MJPEG
  passthrough path. Capture restarts with exponential backoff if FFmpeg exits.
- Capture starts when a browser session or another explicit owner acquires
  the camera, and normally stops after its owners release it. Browser camera
  sessions have a five-minute lease. Camera status inspection alone does not
  start the camera.
- `/api/camera/stream` is an authenticated-session multipart MJPEG response
  that sends the latest frames and their capture timestamps. The existing page
  has a live `<img>`, status/latency indicators, and a canvas reserved for
  overlays. There is no configured stream-FPS setting, adaptive quality
  ladder, or measured slow-link policy yet.
- After successful explicit camera start, the page opens the MJPEG stream
  directly rather than making an extra status request. The browser keeps only
  the newest frame received in each network read, drops frames older than one
  second, and reconnects to request the latest frame when that delay is
  exceeded. This is a stale-frame recovery policy, not a measured latency
  guarantee; target hardware and tailnet behavior still need measurement.
- The legacy `CameraStream` is only a wrapper around `CameraService`; it no
  longer opens a second camera reader or publishes RTSP.

### Motion, detection, events, and storage

- The server-side motion monitor reads the shared camera frame. Its current
  default poll interval is 3 seconds. It decodes to grayscale, scales to
  80x60, and compares consecutive frames using mean absolute pixel difference
  with a threshold of 18. It does not currently run MOG2, detect at 5 fps,
  filter lighting changes, use zones, enforce a minimum duration, or maintain
  the planned idle/motion/active/cooldown event state machine.
- A changed frame is saved as a full JPEG under `data/snapshots/motion/`.
  Motion files are pruned by count (up to 500), not age or total bytes.
  `data/motion_events.json` is bounded to 500 events. There is no vision
  SQLite index, 7-14 day age retention, configurable size cap, or separate
  training dataset store today.
- The existing `camera_motion` notification setting starts/stops the monitor;
  notifications include a thumbnail reference and have a 120-second cooldown.
  There is no current per-event user feedback row or class metadata.
- `camera_snapshot`/`vision_capture` is disabled by default unless explicitly
  enabled, and writes the requested frame to `data/snapshots/latest.jpg` (or
  an explicitly supplied output path). The vision request path can attach the
  frame to a user-requested image-description request. Camera tool references
  are collected as metadata; the feedback manifest does not retain camera
  footage.
- There is also a browser worker, `web/camera-vision-worker.js`. When the
  camera view is active it fetches MediaPipe WASM and face/object detector
  models from public CDNs, then samples browser frames at up to two per second.
  It runs face detection and person-only object detection and the page draws
  the results. This is a privacy and policy conflict with the stated
  face-detection non-goal, has an implicit external model-download dependency,
  and is not the selected offline HP detector backend. Later implementation
  must remove face detection and ensure no frame leaves the host except after
  the explicit describe action.
- Audio is disabled in the camera FFmpeg command (`-an`). Camera start/stop
  requests and enabling/disabling motion monitoring currently create policy
  audit events. The planned operating modes, armed indicator, auto-off policy,
  and delete-all action do not exist yet.

### Existing learning code and data boundaries

- `agent/ml/intent_learning.py` keeps redacted uncertain-intent examples;
  `dataset_exporter.py` records redacted successful text/tool trajectories;
  `dpo_collector.py` derives conservative execution preferences.
- `memory.py` provides feedback-driven local TF-IDF retrieval; `cache.py`
  implements a bounded semantic response cache; `forecaster.py` is telemetry
  forecasting; `anomaly.py` monitors host metrics and fits an Isolation Forest;
  `planner.py` executes bounded LLM plans; `prefetch.py` records intent
  transitions; `log_recorder.py` writes bounded/redacted telemetry; and
  `storage.py` supplies atomic JSON persistence.
- These are not a vision model registry or vision trainer. There is no current
  vision export/train/evaluate/promotion workflow or common learner
  `status()`/learning dashboard contract.
- Camera frames are not ordinarily stored by live viewing. Exceptions are
  explicit snapshot and motion paths above. Browser-side detection performs
  inference locally in the browser, but its model/runtime assets are obtained
  from external services. Existing camera tool metadata does not imply consent
  to store or export frame content.

## Current CPU and RAM cost: known bounds and missing measurements

No target-machine measurement is available at this stage. Existing code does
not expose per-camera CPU or resident-memory counters, so a precise current
cost cannot be reported responsibly.

The inspectable bounds are:

- The server retains only one published JPEG frame, capped at 2 MiB, plus
  FFmpeg pipe/process buffers. The MJPEG parser's incomplete-frame buffer is
  also bounded around the 2 MiB frame limit. Actual process and decoder memory
  are not measured here.
- Motion analysis uses one previous 80x60 grayscale image (4,800 pixels) and
  transiently decodes/resizes a 640x480 frame. Its polling interval limits its
  current analysis rate, but actual CPU cost depends on the HP's FFmpeg,
  camera driver, and image decoder.
- Browser detection has separate browser CPU/RAM cost; it loads two model
  assets and WASM and evaluates browser frames at a maximum interval of 500 ms.
  The model payload and runtime memory are not measured in this audit.
- Snapshot disk use can grow until the 500-file count cap is reached; file
  sizes are not currently governed by an aggregate byte cap or age limit.

The V0 benchmark must record per-test elapsed time/FPS and process peak-RSS
where the OS exposes it, and must label unavailable measurements instead of
substituting estimates. It must not download models: absent model files or
optional runtimes are reported as skipped, not as detector performance.

## Decisions for implementation

These are the target design constraints for subsequent versions, not claims
that the behavior is already implemented.

### V0: benchmark and backend choice

- Run each hardware-sensitive test in a child process so an illegal-instruction
  failure is contained and recorded as a crash. Cover 160x120 grayscale
  OpenCV motion, available tiny detector candidates (OpenCV DNN
  MobileNet-SSD, ncnn YOLO-nano, ONNX Runtime with a locally supplied tiny
  model), and `/dev/video0` capture at 640x480 MJPEG.
- Report FPS, peak RAM when available, and each candidate's run/crash/skip
  state; save machine-readable results under `benchmarks/hp-DATE.json`.
- Select a configured backend only from measured, installed candidates. If no
  detector reaches at least 1 FPS, choose motion-only. Do not fetch models or
  require a network connection during CI.

### V1: live stream

- Use one capture thread and a replace-only latest-frame slot: drop frames,
  never queue them. Analysis cannot block capture or the live path.
- Prefer native camera MJPEG passthrough when supported. Default to 640x480
  and `HSX_STREAM_FPS=10`, capped at 15 on this target.
- Step down 15 -> 10 -> 5 fps -> one snapshot every 2 seconds when CPU exceeds
  80% or the connection is slow, and expose the current quality state.
- Run capture only while a viewer is present or the camera is armed; watchdog
  a stuck capture process.
- Acceptance tests use a 30-fps fake camera and slow analyzer to prove latest
  frame delivery, bounded memory, and that no sent frame is older than one
  second.

### V2: motion events and notifications

- Analyze grayscale 160x120 at 5 fps with frame difference plus MOG2,
  lighting/noise filtering, optional zones, and a configurable minimum motion
  duration.
- Apply cooldown, deduplication, and an explicit idle/motion/active/cooldown
  state machine. An event contains its timestamp, duration, motion score, at
  most three snapshots, and detected classes when available.
- Reuse existing SSE notifications and the notification bell, adding an event
  card with thumbnail, time, classes, and feedback controls. Add quiet hours;
  web push is deferred.

### V3: operating modes, privacy, and retention

- Modes are Off (default), Live view, Armed (motion and objects), and Armed
  plus snapshots. Show an armed indicator, audit every mode change, and
  auto-return to Off after a configurable duration.
- Store images under `data/vision/`, indexed by SQLite; cap JPEGs at 640x480.
  Keep event snapshots and, while armed, periodic samples (10-minute default).
  Save clips only on request.
- Default retention is 14 days (minimum 7), with a 3 GB default size cap and
  oldest-first deletion. User-kept training frames use a separately capped
  store with a visible count. Provide a confirmed delete-all camera-data
  action.
- Capture no audio. Do not detect faces, identify people, or recognize named
  individuals; `person` is only an object class. Face blur may be optional for
  stored frames. No frame leaves the machine unless the user invokes
  "Describe this frame"; log that action with provider and payload size.

### V4: object detection

- Use only the backend selected from V0 results. Run on motion-gated frames or
  at a configurable interval, never on every stream frame. Restrict classes by
  whitelist and confidence threshold; track detections to suppress repeat
  alerts.
- Send timestamped detection JSON over SSE and draw boxes on a browser canvas,
  never into the encoded video.
- Keep all vision under a 300 MB RAM budget. If no local model meets the
  measured target speed, offer remote detection only as an explicit opt-in
  mode using a laptop on the tailnet and the same frame privacy rules.

### V5: labels and review

- Each alert supports thumbs up/down, correcting the class, and "not
  important". Persist label provenance (automatic or user), model version,
  and confidence.
- Prioritize uncertain or disagreeing samples in a Review frames panel.
  Export only user-confirmed labels and configurable high-confidence automatic
  labels. Never export private or deleted frames.

### V6: off-device training

- Provide `tools/vision_export.py` for YOLO or COCO data, with train/validation/
  test splits by day so nearby frames cannot leak between splits, and a count
  manifest.
- Provide `tools/vision_train.py` and a documented Colab notebook. Prefer
  fine-tuning a small pretrained model or a small classifier. With one to two
  weeks of frames, fine-tuning may be reasonable; training from scratch is not.
- Provide `tools/vision_eval.py` for precision, recall, mAP on held-out data,
  and false alerts/day on replay samples.
- Keep models out of git under `models/vision/NAME/VERSION/`, with data range,
  counts, metrics, and source commit metadata. Promote only when the model
  beats the current held-out result and meets the HP speed budget; provide a
  one-command rollback.

### V7: learning registry and dashboard

- Define `status()` for each learner (intent router, provider selector,
  experience memory, anomaly/forecast, and vision), reporting samples, last
  training, metrics, and next retraining. Light router/selector retraining may
  run nightly on the HP; heavy model training remains off-device.
- Record camera tool calls (`camera_snapshot`, `camera_record`, and describe)
  and outcomes as trajectory metadata (tool, summarized arguments, result,
  frame IDs). Include frame content only under a separate explicit consent
  switch.
- Add `/learning` in web and CLI, per-source consent switches and retention/
  delete controls (chats, uploaded-file metadata, tool traces, camera frames),
  metric trends, a Sunday weekly report, and monthly comparison to the first
  week's baseline. Clearly state that the external LLM is not fine-tuned.
- Month-one goals are measurements, not promises: fewer false alerts per day
  than week one, improved router accuracy on a fixed 50-prompt test set, and
  one vision model promoted through the gate.

## Validation status

- On the Windows development host, `ruff check .` passed before changes.
- The full `pytest -q --timeout=60` baseline had one failure in the existing
  SSE heartbeat test (`test_notification_sse_resumes_from_last_event_id_and_sends_heartbeat`);
  the same test passed when rerun alone. No camera or HP hardware was tested.
- The click-to-stream path no longer waits for a second camera-status request.
  Camera UI tests verify immediate stream startup and one-second stale-frame
  recovery. The HP camera and Tailscale link still require the commands in the
  benchmark instructions before latency or smoothness can be confirmed.
- Run the V0 benchmark on the HP before choosing a detector. A benchmark run
  must report exactly what ran, crashed, or was skipped and include measured
  values only.
