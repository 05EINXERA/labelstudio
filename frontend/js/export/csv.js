import { state, labelById } from "../state.js?v=1";
import { view } from "../canvas/view.js?v=1";
import { annotationPoints } from "../canvas/geometry.js?v=1";
import { exportLabelName } from "./coco.js?v=1";

export function exportCsvData() {
  try {
    if (state.galleryIndex >= 0 && state.gallery[state.galleryIndex]) {
      state.gallery[state.galleryIndex].annotations = [...state.annotations];
    }
    const items = state.gallery.length > 0 ? state.gallery : [{
      name: state.image?.name || "image",
      width: state.image?.width || view.imageElement?.naturalWidth || 0,
      height: state.image?.height || view.imageElement?.naturalHeight || 0,
      annotations: state.annotations
    }];

    const header = ["image", "label", "type", "x", "y", "width", "height", "imgWidth", "imgHeight", "points"];
    const allRows = [];

    items.forEach(item => {
      const rows = item.annotations.filter(a => a.type !== "comment").map(annotation => {
        const label = labelById(annotation.labelId);
        const labelName = exportLabelName(annotation, label);
        const pts = annotationPoints(annotation);
        const isPolygon = annotation.points && annotation.points.length !== 4;
        const type = isPolygon ? "polygon" : "box";
        const x = annotation.x;
        const y = annotation.y;
        const w = annotation.width;
        const h = annotation.height;
        const pointsStr = JSON.stringify(pts).replace(/"/g, '""');
        return [item.name, labelName, type, x, y, w, h, item.width || 0, item.height || 0, `"${pointsStr}"`].join(",");
      });
      allRows.push(...rows);
    });

    const csvContent = [header.join(","), ...allRows].join("\n");
    const blob = new Blob([csvContent], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `dataset_annotations.csv`;
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
