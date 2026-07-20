// Shared between ai/detect.js (the writer, via setDetectionBusy) and
// components/workspace.js (a reader, in renderControls). Object-wrapped for
// the same reason as view.js/timer-state.js: ES module imports are read-only
// bindings, so a value that's reassigned needs to live behind a stable object.
export const detectState = {
  detectionBusy: false
};
