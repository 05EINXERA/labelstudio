import { state, labelById, labelDisplayName } from "../state.js?v=1";
import { view } from "../canvas/view.js?v=1";
import { annotationPoints } from "../canvas/geometry.js?v=1";
import { round } from "../utils.js?v=1";

export function buildCocoExport() {
  if (state.galleryIndex >= 0 && state.gallery[state.galleryIndex]) {
    state.gallery[state.galleryIndex].annotations = [...state.annotations];
  }

  const categories = state.labels.map((label, index) => ({
    id: index + 1,
    name: label.name,
    supercategory: "none"
  }));

  const labelToCategoryId = {};
  categories.forEach(c => labelToCategoryId[c.name] = c.id);

  const images = [];
  const annotations = [];
  let annId = 1;

  const items = state.gallery.length > 0 ? state.gallery : [{
    name: state.image?.name || "image.jpg",
    width: state.image?.width || view.imageElement?.naturalWidth || 0,
    height: state.image?.height || view.imageElement?.naturalHeight || 0,
    annotations: state.annotations
  }];

  items.forEach((item, imgIndex) => {
    const image_id = imgIndex + 1;
    images.push({
      id: image_id,
      width: item.width || 0,
      height: item.height || 0,
      file_name: item.name,
      name: item.name
    });

    const grouped = {};
    const ungrouped = [];
    item.annotations.forEach(ann => {
      if (ann.type === "comment") return;
      if (ann.groupId) {
        if (!grouped[ann.groupId]) grouped[ann.groupId] = [];
        grouped[ann.groupId].push(ann);
      } else {
        ungrouped.push([ann]);
      }
    });

    const exportGroups = [...Object.values(grouped), ...ungrouped];

    exportGroups.forEach(group => {
      const baseAnn = group[0];
      const label = labelById(baseAnn.labelId);
      const category_id = labelToCategoryId[label.name] || 1;

      const segmentation = [];
      let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;

      group.forEach(ann => {
        const points = annotationPoints(ann);
        segmentation.push(points.flatMap(p => [round(p.x), round(p.y)]));
        points.forEach(p => {
          minX = Math.min(minX, p.x);
          minY = Math.min(minY, p.y);
          maxX = Math.max(maxX, p.x);
          maxY = Math.max(maxY, p.y);
        });
      });

      const bbox = [round(minX), round(minY), round(maxX - minX), round(maxY - minY)];
      const area = bbox[2] * bbox[3];

      annotations.push({
        id: annId++,
        image_id: image_id,
        category_id: category_id,
        segmentation: segmentation,
        area: round(area),
        bbox: bbox,
        iscrowd: 0,
        num_objects: group.length
      });
    });
  });

  return { images, categories, annotations };
}

export function annotationScreenPoints(annotation) {
  return annotationPoints(annotation).map((point) => ({
    x: round(view.imageBox.x + point.x * view.imageBox.scale),
    y: round(view.imageBox.y + point.y * view.imageBox.scale)
  }));
}

export function getImageDimensions() {
  return {
    width: state.image?.width || view.imageElement?.naturalWidth || 0,
    height: state.image?.height || view.imageElement?.naturalHeight || 0
  };
}

export function exportLabelName(annotation, label) {
  return annotation.detectedClass || label?.name || "object";
}

export function toExportValue(labelName) {
  return String(labelName || "object")
    .trim()
    .replace(/\s+/g, "");
}

export function buildExportAnnotation(annotation, index) {
  const label = labelById(annotation.labelId);
  const labelName = exportLabelName(annotation, label);
  const points = annotationPoints(annotation);
  const flatPoints = points.flatMap((point) => [round(point.x), round(point.y)]);

  return {
    type: "polygon",
    title: labelDisplayName(label) || labelName,
    value: toExportValue(labelName),
    color: label.color,
    order: index + 1,
    attributes: [],
    points: flatPoints,
    rotation: 0,
    keypoints: [],
    confidenceScore: -1
  };
}

export function exportJsonData() {
  try {
    const payload = buildCocoExport();

    let baseName = "dataset_annotations";
    if (state.image?.name) {
      const name = state.image.name;
      baseName = name.substring(0, name.lastIndexOf('.')) || name;
    } else if (state.gallery && state.gallery.length > 0) {
      const name = state.gallery[0].name;
      baseName = name.substring(0, name.lastIndexOf('.')) || name;
    }

    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `${baseName}.json`;
    link.style.display = "none";
    document.body.appendChild(link);
    link.click();
    window.setTimeout(() => {
      URL.revokeObjectURL(url);
      link.remove();
    }, 0);
  } catch (error) {
    console.error(error);
    window.alert(error.message || "Export failed.");
  }
}

export function buildExportTasks() {
  if (state.galleryIndex >= 0 && state.gallery[state.galleryIndex]) {
    state.gallery[state.galleryIndex].annotations = [...state.annotations];
  }

  const items = state.gallery.length > 0 ? state.gallery : [{
    name: state.image?.name || "image",
    width: state.image?.width || view.imageElement?.naturalWidth || 0,
    height: state.image?.height || view.imageElement?.naturalHeight || 0,
    annotations: state.annotations
  }];

  const createdAt = new Date().toISOString();

  return items.map(item => ({
    name: item.name,
    status: "completed",
    externalStatus: "registered",
    width: item.width || 0,
    height: item.height || 0,
    secondsToAnnotate: 0,
    annotations: item.annotations.filter(a => a.type !== "comment").map((annotation, index) => buildExportAnnotation(annotation, index)),
    relations: [],
    tags: [],
    metadatas: [],
    assignee: "",
    reviewer: "",
    approver: "",
    externalAssignee: "",
    externalReviewer: "",
    externalApprover: "",
    createdAt,
    updatedAt: createdAt,
    completedAt: createdAt
  }));
}
