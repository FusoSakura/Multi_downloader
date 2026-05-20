/* =========================================================
   FETCH · Frontend Logic
   ========================================================= */

const $ = (id) => document.getElementById(id);
const show = (el) => el && el.classList.remove('hidden');
const hide = (el) => el && el.classList.add('hidden');

const state = {
  url: '',
  info: null,
  mode: 'video',   // 'video' | 'audio'
  quality: null,   // height (number) or kbps (number)
  fps: null,       // video일 때 선택된 fps (없으면 null)
  taskId: null,
  polling: false,
};

const AUDIO_BITRATES = [320, 256, 192, 128, 96];

// =========================================================
// 유틸
// =========================================================
function setStatus(kind, text) {
  $('statusLight').className = 'status-light ' + kind;
  $('statusText').textContent = text;
}

function showError(msg) {
  const box = $('errorBox');
  box.textContent = '! ' + msg;
  show(box);
}
function clearError() { hide($('errorBox')); }

function formatSize(bytes) {
  if (bytes === null || bytes === undefined || isNaN(bytes)) return '-';
  const units = ['B', 'KB', 'MB', 'GB'];
  let i = 0, n = Number(bytes);
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
  return n.toFixed(1) + units[i];
}
function formatDuration(s) {
  if (!s) return '-';
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = Math.floor(s % 60);
  const pad = (x) => String(x).padStart(2, '0');
  return h ? `${h}:${pad(m)}:${pad(sec)}` : `${m}:${pad(sec)}`;
}

function escapeHtml(s) {
  const div = document.createElement('div');
  div.textContent = s ?? '';
  return div.innerHTML;
}

function resetSubsequentBlocks(from) {
  // from 이후 블록 숨김 (재선택 시 깔끔하게)
  const order = ['infoBlock', 'modeBlock', 'qualityBlock', 'executeBlock',
                 'progressBlock', 'resultBlock'];
  const idx = order.indexOf(from);
  for (let i = idx; i < order.length; i++) hide($(order[i]));
}

// =========================================================
// [01] FETCH INFO
// =========================================================
$('fetchBtn').addEventListener('click', fetchInfo);
$('urlInput').addEventListener('keydown', (e) => {
  if (e.key === 'Enter') fetchInfo();
});

async function fetchInfo() {
  const url = $('urlInput').value.trim();
  if (!url) { showError('URL을 입력하세요.'); return; }

  clearError();
  resetSubsequentBlocks('infoBlock');
  setStatus('busy', 'FETCHING');
  $('fetchBtn').disabled = true;
  $('fetchBtn').textContent = '...';

  try {
    const res = await fetch('/api/info', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || '정보 조회 실패');

    state.url = url;
    state.info = data;
    state.quality = null;
    renderInfo(data);
    setStatus('idle', 'READY');
  } catch (e) {
    showError(e.message || String(e));
    setStatus('err', 'ERROR');
  } finally {
    $('fetchBtn').disabled = false;
    $('fetchBtn').textContent = 'FETCH  ▸';
  }
}

function renderInfo(info) {
  // 썸네일
  const thumb = $('thumbnail');
  if (info.thumbnail) {
    thumb.src = info.thumbnail;
    thumb.style.visibility = 'visible';
  } else {
    thumb.removeAttribute('src');
    thumb.style.visibility = 'hidden';
  }

  $('videoTitle').textContent = info.title || '(no title)';

  const metaParts = [];
  if (info.is_playlist) {
    metaParts.push(`PLAYLIST`);
    metaParts.push(`${info.count} ITEMS`);
  } else {
    if (info.uploader) metaParts.push(info.uploader);
    if (info.duration) metaParts.push(formatDuration(info.duration));
  }
  $('videoMeta').innerHTML = metaParts
    .map(p => `<span>${escapeHtml(p)}</span>`).join('');

  show($('infoBlock'));
  show($('modeBlock'));
  show($('qualityBlock'));
  show($('executeBlock'));

  renderQualities();
}

// =========================================================
// [03] MODE
// =========================================================
document.querySelectorAll('.seg-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.seg-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    state.mode = btn.dataset.mode;
    state.quality = null;
    renderQualities();
  });
});

// =========================================================
// [04] QUALITY
// =========================================================
function renderQualities() {
  const grid = $('qualityGrid');
  const label = $('qualityLabel');
  grid.innerHTML = '';

  if (state.mode === 'video') {
    label.textContent = '[04] RESOLUTION';
    const qs = (state.info && state.info.qualities) || [];
    if (qs.length === 0) {
      grid.innerHTML = `<div style="color:var(--error);font-size:12px;
                        padding:14px;border:1px solid var(--error);
                        background:rgba(208,72,72,0.08);">
                        사용 가능한 영상 화질이 없습니다.</div>`;
      return;
    }
    qs.forEach(q => {
      const btn = document.createElement('button');
      btn.className = 'q-btn';
      btn.type = 'button';
      btn.dataset.quality = q.height;
      const codec = q.codec || '?';
      const isH264 = codec === 'H.264';
      const isAV1 = codec === 'AV1';
      // H.264는 호박색 강조(권장), AV1은 빨강(호환성 경고), 그 외 일반
      let codecHtml;
      if (isH264) {
        codecHtml = `<span style="color:var(--accent);font-weight:700;">${escapeHtml(codec)}</span>`;
      } else if (isAV1) {
        codecHtml = `<span style="color:var(--error);font-weight:700;" title="구형 기기/플레이어에서 재생되지 않을 수 있습니다">${escapeHtml(codec)} ⚠</span>`;
      } else {
        codecHtml = escapeHtml(codec);
      }
      // fps 표시: 60fps 등 고프레임은 해상도 옆에 붙임 (예: 1080p60)
      // 30fps 이하는 일반적이므로 생략하여 라벨을 간결하게 유지
      const fpsTag = (q.fps && q.fps >= 50) ? `<span style="color:var(--accent);">${q.fps}</span>` : '';
      btn.innerHTML = `
        <div class="q-main">${q.height}p${fpsTag}</div>
        <div class="q-sub">${codecHtml} · ${escapeHtml(q.ext || '?')} · ${q.filesize ? formatSize(q.filesize) : '~'}</div>
      `;
      btn.addEventListener('click', () => selectQuality(btn, q.height, q.fps));
      grid.appendChild(btn);
    });
  } else {
    label.textContent = '[04] BITRATE';
    AUDIO_BITRATES.forEach(k => {
      const btn = document.createElement('button');
      btn.className = 'q-btn';
      btn.type = 'button';
      btn.dataset.quality = k;
      btn.innerHTML = `
        <div class="q-main">${k}k</div>
        <div class="q-sub">MP3 · CBR</div>
      `;
      btn.addEventListener('click', () => selectQuality(btn, k, null));
      grid.appendChild(btn);
    });
  }
}

function selectQuality(btn, value, fps) {
  document.querySelectorAll('.q-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  state.quality = value;
  state.fps = (fps === undefined) ? null : fps;
}

// =========================================================
// [05] EXECUTE
// =========================================================
$('executeBtn').addEventListener('click', execute);

async function execute() {
  if (state.quality === null) {
    showError('화질/음질을 먼저 선택하세요.');
    return;
  }
  clearError();

  $('executeBtn').disabled = true;
  $('executeBtn').textContent = '◌  STARTING...';
  hide($('resultBlock'));
  show($('progressBlock'));
  resetProgress();
  setStatus('busy', 'TRANSFERRING');

  try {
    const res = await fetch('/api/download', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        url: state.url,
        mode: state.mode,
        quality: state.quality,
        fps: state.mode === 'video' ? state.fps : null,
      })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || '다운로드 시작 실패');

    state.taskId = data.task_id;
    state.polling = true;
    pollProgress();
  } catch (e) {
    showError(e.message || String(e));
    setStatus('err', 'ERROR');
    $('executeBtn').disabled = false;
    $('executeBtn').textContent = '▶  EXECUTE DOWNLOAD';
  }
}

function resetProgress() {
  $('progressFill').style.width = '0%';
  $('statProgress').textContent = '0.0%';
  $('statSpeed').textContent = '-';
  $('statEta').textContent = '-';
  $('statSize').textContent = '-';
}

async function pollProgress() {
  if (!state.polling || !state.taskId) return;

  try {
    const res = await fetch(`/api/progress/${state.taskId}`);
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || '진행 상태 조회 실패');

    const s = data.status;
    if (s === 'queued') {
      $('statSpeed').textContent = 'queued';
      setTimeout(pollProgress, 500);
    } else if (s === 'downloading') {
      const pct = data.progress || 0;
      $('progressFill').style.width = pct.toFixed(1) + '%';
      $('statProgress').textContent = pct.toFixed(1) + '%';
      $('statSpeed').textContent = data.speed || '-';
      $('statEta').textContent = data.eta || '-';
      $('statSize').textContent = data.total ? formatSize(data.total) : '-';
      // 재생목록이면 progress 라벨에 진행 표시
      if (data.playlist_total) {
        $('statProgress').textContent =
          `${pct.toFixed(1)}% · ${data.playlist_index||1}/${data.playlist_total}`;
      }
      setTimeout(pollProgress, 500);
    } else if (s === 'processing') {
      $('progressFill').style.width = '100%';
      $('statProgress').textContent = '100.0%';
      $('statSpeed').textContent = 'merging';
      $('statEta').textContent = 'finalize';
      setTimeout(pollProgress, 500);
    } else if (s === 'done') {
      state.polling = false;
      $('progressFill').style.width = '100%';
      $('statProgress').textContent = '100.0%';
      $('statSpeed').textContent = 'done';
      $('statEta').textContent = '0:00';
      $('statSize').textContent = formatSize(data.filesize);

      $('resultFile').textContent = data.filename;
      $('resultSize').textContent = formatSize(data.filesize).toUpperCase();
      $('downloadLink').href = `/api/file/${state.taskId}`;
      $('downloadLink').setAttribute('download', data.filename);
      show($('resultBlock'));

      setStatus('done', 'COMPLETE');
      $('executeBtn').disabled = false;
      $('executeBtn').textContent = '▶  EXECUTE DOWNLOAD';
    } else if (s === 'error') {
      state.polling = false;
      showError(data.error || '알 수 없는 오류');
      setStatus('err', 'ERROR');
      $('executeBtn').disabled = false;
      $('executeBtn').textContent = '▶  EXECUTE DOWNLOAD';
    } else {
      // 미확정 상태도 한 번 더 폴링
      setTimeout(pollProgress, 500);
    }
  } catch (e) {
    state.polling = false;
    showError(e.message || String(e));
    setStatus('err', 'ERROR');
    $('executeBtn').disabled = false;
    $('executeBtn').textContent = '▶  EXECUTE DOWNLOAD';
  }
}

// =========================================================
// FOOTER
// =========================================================
$('footerHost').textContent = location.host.toUpperCase();
