(function () {
  const root = document.getElementById("task-live-status");
  if (!root) return;

  const statusUrl = root.dataset.statusUrl;
  const interval = Math.max(2000, Number(root.dataset.pollMs) || 5000);
  const message = document.getElementById("task-live-message");
  const refreshButton = document.getElementById("refresh-task-status");
  let timer = null;
  let busy = false;
  let stopped = false;

  function schedule() {
    if (stopped) return;
    if (timer) window.clearTimeout(timer);
    timer = window.setTimeout(() => poll(false), interval);
  }

  function setError(element, text, prefix) {
    if (!element) return;
    if (text) {
      element.textContent = prefix + text;
      element.style.display = "";
    } else {
      element.textContent = "";
      element.style.display = "none";
    }
  }

  function update(data) {
    const state = document.getElementById("game-task-state");
    if (state) {
      const started = data.game_state === "analyzing" && data.game_worker_started_at
        ? " · 开始于 " + new Date(data.game_worker_started_at).toLocaleTimeString("zh-CN", {hour12:false})
        : "";
      const recoveries = data.game_worker_recovery_count
        ? " · Worker 自动恢复 " + data.game_worker_recovery_count + " 次" : "";
      state.textContent = "状态 " + data.game_state_label + started + recoveries;
    }
    setError(document.getElementById("game-task-error"), data.game_error, "后台任务错误：");
    const tableBody = document.getElementById("export-table-body");
    if (tableBody && typeof data.exports_html === "string") {
      tableBody.innerHTML = data.exports_html;
    }
    const sampling = document.getElementById("sampling-status");
    if (sampling) {
      const recoveries = data.ball_sampling_recovery_count
        ? " · 自动恢复 " + data.ball_sampling_recovery_count + " 次" : "";
      sampling.textContent = "状态：" + data.ball_sampling_state_label
        + " · 已抽 " + data.ball_sampling_round + " 轮 · 累积待审 "
        + data.ball_suggestion_count + " 条（最多保存 300 条）" + recoveries;
    }
    setError(document.getElementById("sampling-error"),
      data.ball_sampling_error, "采样失败：");
    if (message) {
      const stamp = new Date(data.checked_at).toLocaleTimeString("zh-CN", {hour12:false});
      message.textContent = data.active
        ? "状态已更新 " + stamp + " · 运行中任务每 " + Math.round(interval / 1000)
          + " 秒自动刷新；不会重置视频播放位置。"
        : "状态已更新 " + stamp + " · 当前没有运行中的后台任务。";
    }
  }

  async function poll(manual) {
    if (busy || stopped) return;
    if (manual && timer) {
      window.clearTimeout(timer);
      timer = null;
    }
    busy = true;
    try {
      const response = await fetch(statusUrl, {
        method: "GET",
        credentials: "same-origin",
        cache: "no-store",
        headers: {"X-Requested-With": "XMLHttpRequest"}
      });
      if ([401, 403, 404].includes(response.status)) {
        stopped = true;
        return;
      }
      if (!response.ok) throw new Error("HTTP " + response.status);
      const data = await response.json();
      update(data);
      if (data.active) schedule();
      else {
        stopped = true;
        if (refreshButton) refreshButton.disabled = false;
      }
    } catch (error) {
      if (message) message.textContent = "暂时无法读取任务状态；稍后自动重试，不会影响视频播放。";
      schedule();
    } finally {
      busy = false;
    }
  }

  if (refreshButton) refreshButton.addEventListener("click", () => {
    stopped = false;
    poll(true);
  });
  poll(false);
})();
