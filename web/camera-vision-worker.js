let faceDetector;
let personDetector;
let lastFaces = [];
let lastPeople = [];
let initialized = false;
let busy = false;
let frameCount = 0;

const WASM_ROOT = 'https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.22/wasm';
const FACE_MODEL = 'https://storage.googleapis.com/mediapipe-models/face_detector/blaze_face_short_range/float16/1/blaze_face_short_range.tflite';
const PERSON_MODEL = 'https://storage.googleapis.com/mediapipe-models/object_detector/efficientdet_lite0/float16/1/efficientdet_lite0.tflite';

function packDetections(items, label) {
  return (items || []).flatMap((item) => {
    const box = item.boundingBox;
    const category = label || item.categories?.[0]?.categoryName || item.categories?.[0]?.displayName || 'person';
    const score = label === 'face' ? (item.categories?.[0]?.score ?? item.score ?? 0) : (item.categories?.[0]?.score ?? 0);
    if (!box || score < 0.5) return [];
    return [{ label: category, score, x: box.originX, y: box.originY, width: box.width, height: box.height }];
  });
}

async function initialize() {
  try {
    const { FilesetResolver, FaceDetector, ObjectDetector } = await import(
      'https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.22/vision_bundle.mjs'
    );
    const vision = await FilesetResolver.forVisionTasks(WASM_ROOT);
    faceDetector = await FaceDetector.createFromOptions(vision, {
      baseOptions: { modelAssetPath: FACE_MODEL, delegate: 'GPU' },
      runningMode: 'VIDEO',
      minDetectionConfidence: 0.5,
      minSuppressionThreshold: 0.3,
    });
    personDetector = await ObjectDetector.createFromOptions(vision, {
      baseOptions: { modelAssetPath: PERSON_MODEL, delegate: 'GPU' },
      runningMode: 'VIDEO',
      scoreThreshold: 0.5,
      categoryAllowlist: ['person'],
      maxResults: 4,
    });
    initialized = true;
    self.postMessage({ type: 'ready' });
  } catch (error) {
    self.postMessage({ type: 'error', message: `Camera detection could not start: ${String(error?.message || error).slice(0, 180)}` });
    shutdown();
  }
}

function shutdown() {
  initialized = false;
  busy = false;
  try { faceDetector?.close(); } catch { /* The task may not have finished initializing. */ }
  try { personDetector?.close(); } catch { /* The task may not have finished initializing. */ }
  faceDetector = null;
  personDetector = null;
}

self.onmessage = async (event) => {
  const data = event.data || {};
  if (data.type === 'init') {
    if (!initialized && !faceDetector) await initialize();
    return;
  }
  if (data.type === 'stop') {
    shutdown();
    return;
  }
  if (data.type !== 'frame' || !initialized || busy) {
    data.bitmap?.close?.();
    return;
  }
  busy = true;
  const { bitmap, timestamp } = data;
  try {
    frameCount += 1;
    if (frameCount % 2) {
      const result = faceDetector.detectForVideo(bitmap, timestamp);
      lastFaces = packDetections(result.detections, 'face');
    } else {
      const result = personDetector.detectForVideo(bitmap, timestamp);
      lastPeople = packDetections(result.detections);
    }
    self.postMessage({ type: 'detections', width: bitmap.width, height: bitmap.height, detections: [...lastPeople, ...lastFaces] });
  } catch (error) {
    self.postMessage({ type: 'error', message: `Camera detection paused: ${String(error?.message || error).slice(0, 180)}` });
    shutdown();
  } finally {
    bitmap.close();
    busy = false;
  }
};
