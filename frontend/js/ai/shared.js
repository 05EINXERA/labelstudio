import { state } from "../state.js?v=1";
import { view } from "../canvas/view.js?v=1";

/**
 * Returns the image source suitable for the AI API.
 * If the current image src is a blob: URL (local file), converts it to a
 * base64 data URL via a canvas, since blob URLs are browser-only and
 * cannot be fetched by the backend server.
 */
export async function getImageSrcForAPI() {
  const src = state.image?.src || view.imageElement?.src;
  if (!src) return null;
  // If it's already a normal URL or base64 data URL, send as-is
  if (!src.startsWith("blob:")) return src;
  // Convert blob URL -> base64 via canvas
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.crossOrigin = "anonymous";
    img.onload = () => {
      const cvs = document.createElement("canvas");
      cvs.width = img.naturalWidth;
      cvs.height = img.naturalHeight;
      cvs.getContext("2d").drawImage(img, 0, 0);
      resolve(cvs.toDataURL("image/jpeg", 0.92));
    };
    img.onerror = reject;
    img.src = src;
  });
}
