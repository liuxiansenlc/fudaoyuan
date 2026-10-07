/* 奖学金审核工作台 · 前端交互
   尽量用原生 API：没有构建步骤、没有依赖，宝塔上直接能跑。 */
(function () {
  'use strict';

  var CSRF = (document.querySelector('meta[name=csrf-token]') || {}).content || '';

  window.wbCsrf = function () { return CSRF; };

  // ------------------------------------------------------------ 主题
  window.wbToggleTheme = function () {
    var cur = document.documentElement.getAttribute('data-theme');
    if (!cur) {
      cur = window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
    }
    var next = cur === 'dark' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', next);
    localStorage.setItem('wb-theme', next);
    wbSyncIcon();
  };

  function wbSyncIcon() {
    var ic = document.getElementById('themeIc');
    if (!ic) return;
    var cur = document.documentElement.getAttribute('data-theme')
      || (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
    ic.innerHTML = cur === 'dark'
      ? '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>'
      : '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8Z"/></svg>';
  }
  wbSyncIcon();

  // ------------------------------------------------------------ 请求
  window.wbApi = function (url, opt) {
    opt = opt || {};
    opt.headers = Object.assign({
      'X-CSRF-Token': CSRF,
      'Content-Type': 'application/json'
    }, opt.headers || {});
    if (opt.body && typeof opt.body !== 'string' && !(opt.body instanceof FormData)) {
      opt.body = JSON.stringify(opt.body);
    }
    if (opt.body instanceof FormData) {
      delete opt.headers['Content-Type'];
    }
    return fetch(url, opt).then(function (r) {
      return r.json().catch(function () { return { ok: false, error: '返回不是 JSON（HTTP ' + r.status + '）' }; })
        .then(function (j) {
          if (j.need_login) { location.href = '/login'; }
          return j;
        });
    });
  };

  // ------------------------------------------------------------ Toast
  window.wbToast = function (msg, kind, ms) {
    var box = document.getElementById('toasts');
    if (!box) { console.log(msg); return; }
    var el = document.createElement('div');
    el.className = 'toast ' + (kind || '');
    el.setAttribute('role', 'status');
    el.setAttribute('aria-live', 'polite');
    el.textContent = msg;
    box.appendChild(el);
    setTimeout(function () {
      el.style.transition = '.25s'; el.style.opacity = '0';
      el.style.transform = 'translateX(12px)';
      setTimeout(function () { el.remove(); }, 260);
    }, ms || 2800);
  };

  // ------------------------------------------------------------ 灯箱
  window.zoom = function (el) {
    var lb = document.getElementById('lb');
    var img = document.getElementById('lbImg');
    var cap = document.getElementById('lbCap');
    img.src = el.src;
    img.alt = el.getAttribute('alt') || '';
    if (cap) cap.textContent = el.getAttribute('alt') || '';
    lb.classList.add('on');
    // 焦点管理：聚焦关闭按钮，Escape/关闭时回到原图
    lb._returnFocus = el;
    var x = lb.querySelector('.x');
    if (x) x.focus();
  };
  window.closeLb = function () {
    var lb = document.getElementById('lb');
    if (!lb.classList.contains('on')) return;
    lb.classList.remove('on');
    var back = lb._returnFocus;
    if (back && back.focus) back.focus();
  };
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') closeLb();
    // 灯箱打开时，Tab 循环在灯箱内
    if (e.key === 'Tab' && document.getElementById('lb').classList.contains('on')) {
      var focusables = document.getElementById('lb').querySelectorAll('button, [tabindex]:not([tabindex="-1"])');
      if (!focusables.length) return;
      var first = focusables[0], last = focusables[focusables.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    }
  });

  // ------------------------------------------------------------ 键盘激活
  // 对带 role="button" + tabindex 的可聚焦元素（如可点击的缩略图），
  // 让 Enter / Space 触发与点击相同的动作 —— 保证键盘用户能操作灯箱。
  document.addEventListener('keydown', function (e) {
    if (e.key !== 'Enter' && e.key !== ' ') return;
    var t = e.target;
    if (!t || t.tagName === 'BUTTON' || t.tagName === 'A') return;
    if (t.getAttribute('role') === 'button' && t.hasAttribute('tabindex')) {
      e.preventDefault();
      t.click();
    }
  });

  // ------------------------------------------------------------ 上传
  window.wbUpload = function (url, files, onProgress) {
    return new Promise(function (resolve) {
      var fd = new FormData();
      for (var i = 0; i < files.length; i++) fd.append('files', files[i]);
      var xhr = new XMLHttpRequest();
      xhr.open('POST', url);
      xhr.setRequestHeader('X-CSRF-Token', CSRF);
      xhr.upload.onprogress = function (e) {
        if (e.lengthComputable && onProgress) onProgress(e.loaded / e.total);
      };
      xhr.onload = function () {
        var j;
        try { j = JSON.parse(xhr.responseText); } catch (e) { j = { ok: false, error: 'HTTP ' + xhr.status }; }
        resolve(j);
      };
      xhr.onerror = function () { resolve({ ok: false, error: '网络错误' }); };
      xhr.send(fd);
    });
  };

  // ------------------------------------------------------------ 任务轮询
  window.wbPoll = function (taskId, onTick, onDone) {
    var timer = null, fails = 0;
    function tick() {
      wbApi('/api/tasks/' + taskId).then(function (j) {
        if (!j.ok) {
          if (++fails > 5) { clearInterval(timer); onDone && onDone({ status: 'failed', error: j.error }); return; }
          return;
        }
        fails = 0;
        onTick && onTick(j.task);
        if (['done', 'failed', 'canceled'].indexOf(j.task.status) >= 0) {
          clearInterval(timer);
          onDone && onDone(j.task);
        }
      });
    }
    tick();
    timer = setInterval(tick, 900);
    return function () { clearInterval(timer); };
  };

  window.wbFmtBytes = function (n) {
    if (!n) return '0 B';
    var u = ['B', 'KB', 'MB', 'GB'], i = 0;
    while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
    return (i ? n.toFixed(1) : n) + ' ' + u[i];
  };
})();
