/* CMaster 章节导学播放器
 *
 * 播放器只保存“观看到哪里”，不会把知识点写入服务端已读记录。
 * 正文、代码与精读链接仍来自原有课程路由；这里负责时间轴、字幕和无障碍操作。
 */
(function () {
  "use strict";

  var root = document.getElementById("lecture-player");
  var bootEl = document.getElementById("lecture-boot");
  if (!root || !bootEl) return;

  var boot;
  try {
    boot = JSON.parse(bootEl.textContent || "{}");
  } catch (e) {
    return;
  }

  var sceneEls = Array.prototype.slice.call(root.querySelectorAll(".lecture-scene"));
  var outlineBtns = Array.prototype.slice.call(root.querySelectorAll(".lecture-outline-item"));
  if (!sceneEls.length || !outlineBtns.length) return;

  var playBtn = document.getElementById("lecture-play");
  var prevBtn = document.getElementById("lecture-prev");
  var nextBtn = document.getElementById("lecture-next");
  var timeline = document.getElementById("lecture-timeline");
  var speedSelect = document.getElementById("lecture-speed");
  var counterEl = document.getElementById("lecture-scene-counter");
  var captionEl = document.getElementById("lecture-caption-text");
  var stateEl = document.getElementById("lecture-playing-state");
  var currentTimeEl = document.getElementById("lecture-current-time");
  var durationEl = document.getElementById("lecture-duration");
  if (!playBtn || !prevBtn || !nextBtn || !timeline || !speedSelect) return;

  var durations = (boot.durations || []).map(function (value) {
    var n = Number(value);
    return isFinite(n) && n > 0 ? n : 28;
  });
  while (durations.length < sceneEls.length) durations.push(28);
  durations = durations.slice(0, sceneEls.length);

  var starts = [];
  var totalDuration = 0;
  durations.forEach(function (duration, index) {
    starts[index] = totalDuration;
    totalDuration += duration;
  });

  var storageId = document.body.dataset.storageId || "guest";
  var storageKey = "cmaster:lecture:v1:" + storageId + ":" + boot.cid;
  var reduceMotion = window.matchMedia &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  var state = {
    time: 0,
    speed: 1,
    current: -1,
    playing: false,
    completed: {},
    watched: durations.map(function () { return 0; }),
    lastFrame: 0,
    lastSave: 0,
    raf: 0
  };

  function clamp(value, min, max) {
    return Math.max(min, Math.min(max, value));
  }

  function formatTime(seconds) {
    var sec = Math.max(0, Math.floor(seconds));
    var minutes = Math.floor(sec / 60);
    var rest = sec % 60;
    return (minutes < 10 ? "0" : "") + minutes + ":" +
      (rest < 10 ? "0" : "") + rest;
  }

  function sceneAt(time) {
    for (var i = durations.length - 1; i >= 0; i--) {
      if (time >= starts[i] || i === 0) return i;
    }
    return 0;
  }

  function readSavedState() {
    try {
      var saved = JSON.parse(localStorage.getItem(storageKey) || "null");
      if (!saved || typeof saved !== "object") return;

      if (isFinite(Number(saved.time))) {
        state.time = clamp(Number(saved.time), 0, totalDuration);
      }
      if ([0.75, 1, 1.25, 1.5].indexOf(Number(saved.speed)) >= 0) {
        state.speed = Number(saved.speed);
      }
      if (Array.isArray(saved.completed)) {
        saved.completed.forEach(function (index) {
          if (Number.isInteger(index) && index >= 0 && index < sceneEls.length) {
            state.completed[index] = true;
          }
        });
      }
      if (Array.isArray(saved.watched)) {
        saved.watched.forEach(function (seconds, index) {
          if (index < state.watched.length && isFinite(Number(seconds))) {
            state.watched[index] = clamp(Number(seconds), 0, durations[index]);
          }
        });
      }
    } catch (e) {
      /* 隐私模式或旧数据损坏时从零开始，不阻断播放。 */
    }
  }

  function completedIndexes() {
    return Object.keys(state.completed).filter(function (index) {
      return state.completed[index];
    }).map(Number);
  }

  function saveState(force) {
    var now = Date.now();
    if (!force && now - state.lastSave < 900) return;
    state.lastSave = now;
    try {
      localStorage.setItem(storageKey, JSON.stringify({
        time: Number(state.time.toFixed(2)),
        speed: state.speed,
        completed: completedIndexes(),
        watched: state.watched.map(function (seconds) {
          return Number(seconds.toFixed(2));
        })
      }));
    } catch (e) {
      /* localStorage 不可用时，本次播放仍然可用。 */
    }
  }

  function sceneProgress(index) {
    if (state.time < starts[index]) return 0;
    if (index === sceneEls.length - 1 && state.time >= totalDuration) return 1;
    return clamp((state.time - starts[index]) / durations[index], 0, 1);
  }

  function markCompleted(index) {
    if (index < 0 || index >= sceneEls.length || state.completed[index]) return;
    state.completed[index] = true;
    outlineBtns[index].classList.add("is-complete");
  }

  function updateWatched(index, delta) {
    if (index < 0) return;
    state.watched[index] = clamp(state.watched[index] + delta, 0, durations[index]);
    if (state.watched[index] >= Math.min(durations[index] * .72, durations[index] - 2)) {
      markCompleted(index);
    }
  }

  function setPlaying(playing) {
    if (state.playing === playing) return;
    state.playing = playing;
    root.dataset.playing = playing ? "true" : "false";
    playBtn.setAttribute("aria-pressed", playing ? "true" : "false");
    playBtn.setAttribute("aria-label", playing ? "暂停导览" : "播放导览");

    if (playing) {
      if (state.time >= totalDuration - .05) {
        state.time = 0;
        state.watched = durations.map(function () { return 0; });
        state.completed = {};
        outlineBtns.forEach(function (button) {
          button.classList.remove("is-complete");
        });
      }
      state.lastFrame = 0;
      render();
      state.raf = requestAnimationFrame(tick);
    } else {
      cancelAnimationFrame(state.raf);
      state.raf = 0;
      render();
      saveState(true);
    }
  }

  function tick(timestamp) {
    if (!state.playing) return;
    if (!state.lastFrame) state.lastFrame = timestamp;
    var delta = Math.min(.25, Math.max(0, (timestamp - state.lastFrame) / 1000));
    state.lastFrame = timestamp;

    var active = sceneAt(state.time);
    var scaledDelta = delta * state.speed;
    state.time = Math.min(totalDuration, state.time + scaledDelta);
    updateWatched(active, scaledDelta);
    render();
    saveState(false);

    if (state.time >= totalDuration) {
      markCompleted(sceneEls.length - 1);
      setPlaying(false);
      stateEl.textContent = "本章导览完成";
      return;
    }
    state.raf = requestAnimationFrame(tick);
  }

  function renderScene(index) {
    if (state.current === index) return;
    state.current = index;

    sceneEls.forEach(function (scene, sceneIndex) {
      var active = sceneIndex === index;
      scene.hidden = !active;
      scene.classList.toggle("is-active", active);
      if (active) {
        var read = scene.querySelector(".lecture-read");
        if (read) read.scrollTop = 0;
      }
    });

    outlineBtns.forEach(function (button, buttonIndex) {
      var active = buttonIndex === index;
      button.classList.toggle("on", active);
      if (active) {
        button.setAttribute("aria-current", "step");
      } else {
        button.removeAttribute("aria-current");
      }
    });

    var scene = sceneEls[index];
    var title = "";
    var sceneTitle = scene.querySelector(".lecture-scene-head h3");
    if (sceneTitle) title = sceneTitle.textContent.trim();
    counterEl.textContent = "场景 " + String(index + 1).padStart(2, "0") +
      " / " + String(sceneEls.length).padStart(2, "0");
    if (captionEl) {
      var caption = (scene.getAttribute("data-narration") || "").trim();
      if (caption) captionEl.textContent = caption;
    }
    captionEl.setAttribute("aria-label", "场景 " + (index + 1) + "：" + title);
    prevBtn.disabled = index === 0;
    nextBtn.disabled = index === sceneEls.length - 1;
  }

  function render() {
    var index = sceneAt(state.time);
    renderScene(index);

    var seconds = Math.round(state.time * 10) / 10;
    timeline.value = totalDuration > 0 ? String(Math.round(state.time / totalDuration * 1000)) : "0";
    timeline.setAttribute("aria-valuetext", formatTime(state.time) + " / " + formatTime(totalDuration));
    currentTimeEl.textContent = formatTime(state.time);
    durationEl.textContent = formatTime(totalDuration);

    outlineBtns.forEach(function (button, buttonIndex) {
      var bar = button.querySelector(".lecture-mini-bar i");
      if (bar) bar.style.width = Math.round(sceneProgress(buttonIndex) * 100) + "%";
      button.classList.toggle("is-complete", !!state.completed[buttonIndex]);
    });

    if (!state.playing) {
      if (state.time >= totalDuration) {
        stateEl.textContent = "本章导览完成";
      } else {
        stateEl.textContent = "已暂停 · " + seconds.toFixed(1) + " 秒";
      }
    } else {
      stateEl.textContent = "正在播放 · " + state.speed + "×";
    }
  }

  function seek(time) {
    state.time = clamp(time, 0, totalDuration);
    render();
    saveState(false);
  }

  function goScene(index, shouldPause) {
    index = clamp(index, 0, sceneEls.length - 1);
    if (shouldPause) setPlaying(false);
    seek(starts[index]);
  }

  function isInteractiveTarget(target) {
    if (!target) return false;
    var tag = (target.tagName || "").toLowerCase();
    return tag === "input" || tag === "textarea" || tag === "select" ||
      tag === "button" || tag === "a" || target.isContentEditable;
  }

  readSavedState();
  speedSelect.value = String(state.speed);
  render();

  playBtn.addEventListener("click", function () {
    setPlaying(!state.playing);
  });

  prevBtn.addEventListener("click", function () {
    goScene(state.current - 1, true);
  });

  nextBtn.addEventListener("click", function () {
    goScene(state.current + 1, true);
  });

  outlineBtns.forEach(function (button, index) {
    button.classList.toggle("is-read", (boot.readKids || []).indexOf(index + 1) >= 0);
    button.classList.toggle("is-complete", !!state.completed[index]);
    button.addEventListener("click", function () {
      goScene(index, true);
      if (!reduceMotion) {
        root.scrollIntoView({ behavior: "smooth", block: "start" });
      }
    });
  });

  timeline.addEventListener("input", function () {
    seek(Number(timeline.value) / 1000 * totalDuration);
  });

  speedSelect.addEventListener("change", function () {
    var speed = Number(speedSelect.value);
    if ([0.75, 1, 1.25, 1.5].indexOf(speed) >= 0) {
      state.speed = speed;
      render();
      saveState(true);
    }
  });

  document.addEventListener("keydown", function (event) {
    if (event.defaultPrevented || event.metaKey || event.ctrlKey || event.altKey) return;
    if (isInteractiveTarget(event.target)) return;

    if (event.code === "Space" || event.key === "p" || event.key === "P") {
      event.preventDefault();
      setPlaying(!state.playing);
    } else if (event.key === "ArrowRight") {
      event.preventDefault();
      goScene(state.current + 1, true);
    } else if (event.key === "ArrowLeft") {
      event.preventDefault();
      goScene(state.current - 1, true);
    } else if (event.key === "Home") {
      event.preventDefault();
      goScene(0, true);
    } else if (event.key === "End") {
      event.preventDefault();
      goScene(sceneEls.length - 1, true);
    }
  });

  window.addEventListener("beforeunload", function () {
    saveState(true);
  });

  document.addEventListener("visibilitychange", function () {
    if (document.hidden) {
      if (state.playing) setPlaying(false);
      saveState(true);
    }
  });

  /* 进入“导学模式”本身就是一个播放意图。没有语音自动播放限制，稍作停顿
     让用户看清第一屏，然后开始推进；按空格或播放键可以立即退回暂停。 */
  window.setTimeout(function () {
    if (!document.hidden && state.time < totalDuration) setPlaying(true);
  }, reduceMotion ? 900 : 550);
})();
