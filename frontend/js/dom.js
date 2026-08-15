// Stable DOM element/context lookups shared across modules. Only elements
// that are never reassigned belong here — app.js's top-level consts are
// module-scoped (not global) once it became an ES module, so anything a
// separate module needs to see must be exported from somewhere explicit.
export const canvas = document.querySelector("#annotationCanvas");
export const ctx = canvas.getContext("2d");
export const imageCanvas = document.querySelector("#imageCanvas");
export const imageCtx = imageCanvas.getContext("2d");
export const staticCanvas = document.querySelector("#staticCanvas");
export const staticCtx = staticCanvas.getContext("2d");
export const stageWrap = document.querySelector(".stage-wrap");
export const saveStatus = document.querySelector("#saveStatus");
export const emptyState = document.querySelector("#emptyState");
export const classesList = document.querySelector("#classesList");
export const annotationList = document.querySelector("#annotationList");
export const annotationCount = document.querySelector("#annotationCount");
// Objects pane header: toggle that filters the list to hidden objects, and the
// live count of hidden rows shown inside it.
export const hiddenFilterButton = document.querySelector("#hiddenFilterButton");
export const hiddenCount = document.querySelector("#hiddenCount");
export const selectedInfo = document.querySelector("#selectedInfo");
export const drawMode = document.querySelector("#drawMode");
export const selectMode = document.querySelector("#selectMode");
export const boxMode = document.querySelector("#boxMode");
export const polygonMode = document.querySelector("#polygonMode");
export const commentMode = document.querySelector("#commentMode");
export const magicWandMode = document.querySelector("#magicWandMode");
export const autoDetectButton = document.querySelector("#autoDetectButton");
// AI section — also gated by toolAvailability.ai in renderControls()
export const aiSettingsMenuButton = document.querySelector("#aiSettingsMenuButton");
export const aiSettingsDropdownContainer = document.querySelector("#aiSettingsDropdownContainer");
export const autoTagButton = document.querySelector("#autoTagButton");
// Smooth section — also gated by toolAvailability.smooth in renderControls()
export const fftToolGroup = document.querySelector(".fft-tool-group");
export const undoButton = document.querySelector("#undoButton");
export const redoButton = document.querySelector("#redoButton");
export const deleteButton = document.querySelector("#deleteButton");
export const unhideAllButton = document.querySelector("#unhideAllButton");
export const clearButton = document.querySelector("#clearButton");
// Assign the open task to a team / person. Replaced the old Export link, which
// was only a link to the project's Exports tab (still reachable there).
// Hidden until a `manager` role is confirmed — see canvas-assign.js.
export const assignTaskButton = document.querySelector("#assignTaskButton");
export const shapeHint = document.querySelector("#shapeHint");
export const saveButton = document.querySelector("#saveButton");
