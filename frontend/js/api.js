export async function apiFetch(url, options = {}) {
  const logged_in = localStorage.getItem('logged_in');
  if (!logged_in) {
    window.location.href = '/';
    return;
  }
  options.headers = { ...options.headers };
  const res = await fetch(url, options);
  if (res.status === 401) {
    localStorage.removeItem('logged_in');
    localStorage.removeItem('dataset_username');
    window.location.href = '/';
  }
  return res;
}

export async function pollJob(jobId, controller) {
  while (true) {
    if (controller && controller.signal.aborted) throw new Error("Aborted");
    const res = await apiFetch(`${window.location.origin}/api/detect/status/${jobId}`);
    if (res.status === 404) throw new Error("Job not found or expired");
    if (!res.ok) throw new Error(`Polling failed (${res.status})`);

    const data = await res.json();
    if (data.status === "completed") return data.result;
    if (data.status === "failed") throw new Error(data.error);

    await new Promise(r => setTimeout(r, 1000));
  }
}
