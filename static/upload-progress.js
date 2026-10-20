(function () {
  const form = document.getElementById("upload-form");
  if (!form || !window.XMLHttpRequest) return;

  const wrapper = document.getElementById("upload-progress-wrap");
  const progress = document.getElementById("upload-progress");
  const label = document.getElementById("upload-progress-text");
  const submit = document.getElementById("upload-submit");
  if (!wrapper || !progress || !label || !submit) return;

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    wrapper.style.display = "grid";
    progress.value = 0;
    label.textContent = "正在上传视频…";
    submit.disabled = true;

    const request = new XMLHttpRequest();
    request.open("POST", form.action, true);
    request.setRequestHeader("X-Requested-With", "XMLHttpRequest");
    request.upload.addEventListener("progress", function (event) {
      if (!event.lengthComputable) {
        label.textContent = "正在上传视频…";
        return;
      }
      const percent = Math.min(100, Math.round(event.loaded / event.total * 100));
      progress.value = percent;
      label.textContent = percent < 100
        ? "正在上传视频… " + percent + "%"
        : "上传已接收，服务器正在校验视频…";
    });
    request.addEventListener("load", function () {
      if (request.status >= 200 && request.status < 400 && request.responseURL) {
        progress.value = 100;
        label.textContent = "视频已接收，正在打开比赛工作台…";
        window.location.assign(request.responseURL);
        return;
      }
      label.textContent = "上传失败（HTTP " + request.status + "）。请检查文件或服务器日志后重试。";
      submit.disabled = false;
    });
    request.addEventListener("error", function () {
      label.textContent = "网络中断，无法确认上传结果。请先检查比赛列表，避免重复上传。";
      submit.disabled = false;
    });
    request.addEventListener("abort", function () {
      label.textContent = "上传已取消。";
      submit.disabled = false;
    });
    request.send(new FormData(form));
  });
})();
