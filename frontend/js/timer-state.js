// Shared mutable state between the persistence layer (components/workspace.js)
// and the session timer feature (components/timer.js). Only taskSessionSeconds
// is needed on this side of the boundary; the rest of the timer's state
// (sessionSeconds, isTimerRunning, etc.) stays local to timer.js's own
// timerLocalState wrapper.
export const timerState = {
  taskSessionSeconds: 0
};
