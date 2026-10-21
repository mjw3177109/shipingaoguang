/* BasketCut V1.7 - resume bytes from confirmed offsets; no full video buffer. */
(() => {
  const form = document.getElementById("upload-form");
  if (!form) return;
  const wrapper = document.getElementById("upload-progress-wrap");
  const progress = document.getElementById("upload-progress");
  const label = document.getElementById("upload-progress-text");
  const submit = document.getElementById("upload-submit");
  if (!wrapper || !progress || !label || !submit) return;
  const csrf = form.querySelector('input[name="csrfmiddlewaretoken"]')?.value;

  function update(message, percent) {
    wrapper.style.display = "grid";
    label.textContent = message;
    if (percent !== undefined) progress.value = Math.min(100, percent);
  }
  function saved(key) {
    try { return localStorage.getItem(key); } catch (_) { return null; }
  }
  function remember(key, id) {
    try {
      if (id) localStorage.setItem(key, id);
      else localStorage.removeItem(key);
    } catch (_) { /* browser storage disabled */ }
  }
  async function api(path, method, body, headers = {}) {
    const options = {
      method, credentials: "same-origin",
      headers: { "X-CSRFToken": csrf, ...headers }
    };
    if (body !== undefined) options.body = body;
    const response = await fetch(path, options);
    let data = {};
    try { data = await response.json(); } catch (_) {}
    return { response, data };
  }
  async function sha256(buffer) {
    const digest = await crypto.subtle.digest("SHA-256", buffer);
    return Array.from(new Uint8Array(digest),
      byte => byte.toString(16).padStart(2, "0")).join("");
  }
  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const file = form.querySelector('input[name="video"]')?.files?.[0];
    const venue = form.querySelector('select[name="venue_id"]')?.value;
    const title = form.querySelector('input[name="title"]')?.value?.trim();
    if (!file || !venue || !title) return;
    if (!window.crypto?.subtle || !window.fetch || !csrf) {
      update("该浏览器不支持安全分片上传，请使用较新的 Chrome 或 Edge。", 0);
      return;
    }
    const key = "basketcut:v17:" +
      JSON.stringify([file.name, file.size, file.lastModified, venue, title]);
    submit.disabled = true;
    update("正在查询断点续传状态…", 0);
    let id = saved(key);
    const address = id => "/games/resumable/" + id + "/";
    try {
      let offset = 0;
      if (id) {
        const status = await api(address(id), "GET");
        if (status.response.ok && status.data.state === "receiving" &&
            !status.data.expired && status.data.byte_size === file.size) {
          offset = status.data.offset;
        } else if (status.response.ok && status.data.state === "complete" &&
                   status.data.game_id) {
          remember(key, null);
          window.location.assign("/games/" + status.data.game_id + "/");
          return;
        } else {
          id = null;
          remember(key, null);
        }
      }
      if (!id) {
        const body = JSON.stringify({
          venue_id: venue, title, filename: file.name,
          byte_size: file.size, sha256: ""
        });
        const start = await api("/games/resumable/", "POST", body,
          { "Content-Type": "application/json" });
        if (!start.response.ok) {
          throw new Error("创建上传会话失败 " + (start.data.error || start.response.status));
        }
        id = start.data.id;
        remember(key, id);
      }
      const chunkSize = 8 * 1024 * 1024;
      let failures = 0;
      while (offset < file.size) {
        const end = Math.min(offset + chunkSize, file.size);
        update("上传 " + (offset / file.size * 100).toFixed(1) + "% · 校验当前分片",
          offset / file.size * 100);
        const buffer = await file.slice(offset, end).arrayBuffer();
        const digest = await sha256(buffer);
        try {
          const result = await api(address(id) + "chunk/", "POST", buffer, {
            "Content-Type": "application/octet-stream",
            "X-Upload-Offset": String(offset),
            "X-Chunk-SHA256": digest
          });
          if (result.response.status === 409 &&
              Number.isFinite(result.data.offset)) {
            offset = result.data.offset;
            failures = 0;
            continue;
          }
          if (!result.response.ok) {
            throw new Error("上传失败 " + (result.data.error || result.response.status));
          }
          offset = result.data.offset;
          failures = 0;
        } catch (error) {
          if (++failures > 3) throw error;
          update("连接暂时中断，正在重新确认上传位置…", offset / file.size * 100);
          await sleep(500 * failures);
          const status = await api(address(id), "GET");
          if (!status.response.ok || status.data.state !== "receiving") {
            throw new Error("上传会话已失效，请重新提交");
          }
          offset = status.data.offset;
        }
      }
      update("上传完成，正在校验完整视频…", 100);
      const result = await api(address(id) + "finish/", "POST", "");
      if (!result.response.ok || !result.data.game_id) {
        throw new Error("视频验收失败 " + (result.data.error || result.response.status));
      }
      remember(key, null);
      window.location.assign("/games/" + result.data.game_id + "/");
    } catch (error) {
      update((error.message || String(error)) + "；再次点击可从已确认位置续传。");
      submit.disabled = false;
    }
  });
})();
