importScripts('crypto-js.min.js', 'pdf-lib.min.js');

const MAX_FULL_CANVAS_PX = 16000;
const FULL_CAPTURE_SLICE_LIMIT = 40;
const MIN_CAPTURE_GAP_MS = 550;
let lastCaptureVisibleAt = 0;

async function getAppSettings() {
  const result = await chrome.storage.local.get(['saveMode', 'apiKey', 'serverUrl', 'timezoneOffset']);
  const tz = Number(result.timezoneOffset);
  return {
    saveMode: result.saveMode === 'server' ? 'server' : 'local',
    apiKey: result.apiKey || '',
    serverUrl: (result.serverUrl || '').replace(/\/$/, ''),
    timezoneOffset: Number.isFinite(tz) ? tz : -3
  };
}

chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  if (request.action === 'capture') {
    handleCapture(request.mode, sendResponse);
    return true;
  }

  if (request.action === 'ping') {
    sendResponse({ status: 'alive', time: Date.now() });
    return true;
  }

  if (request.action === 'saveOtsToCaptureFolder') {
    saveOtsToCaptureFolder(request)
      .then((result) => sendResponse(result))
      .catch((error) => sendResponse({ ok: false, error: error.message }));
    return true;
  }

  return false;
});

chrome.runtime.onMessageExternal.addListener((request, sender, sendResponse) => {
  if (request && request.action === 'saveOtsToCaptureFolder') {
    saveOtsToCaptureFolder(request)
      .then((result) => sendResponse(result))
      .catch((error) => sendResponse({ ok: false, error: error.message }));
    return true;
  }
  sendResponse({ ok: false, error: 'accion no soportada' });
  return false;
});

async function handleCapture(mode, sendResponse) {
  try {
    let jpegUri;
    if (mode === 'area') {
      jpegUri = await captureArea();
    } else if (mode === 'full') {
      jpegUri = await captureFull();
    } else {
      jpegUri = await captureVisible();
    }

    if (!jpegUri) {
      throw new Error('No se pudo capturar la imagen');
    }

    const hashes = await calculateHashes(jpegUri);
    const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
    const url = tabs[0]?.url || 'N/A';
    const [userIp, serverIp, trustedTime] = await Promise.all([
      getUserIp(),
      getServerIp(url),
      getTrustedUtcTime()
    ]);
    const appSettingsEarly = await getAppSettings();

    const metadata = {
      jpegHashes: hashes,
      utc3Date: trustedTime.iso,
      timeSource: trustedTime.source,
      timezoneOffset: appSettingsEarly.timezoneOffset,
      dualTime: formatDualTime(trustedTime.iso, appSettingsEarly.timezoneOffset),
      url: url,
      userIp: userIp,
      urlIp: serverIp,
      captureMode: mode
    };

    const stamp = trustedTime.iso.replace(/[:.]/g, '-').replace('T', '_').substring(0, 19);
    const urlDomain = (() => {
      try {
        return new URL(url).hostname.replace(/^www\./, '').replace(/[^a-zA-Z0-9._-]/g, '_').slice(0, 40);
      } catch (e) {
        return 'local';
      }
    })();
    let folder = `SCIC_${urlDomain}_${mode}_${stamp}`;

    const pdfBlob = await createPDF(jpegUri, metadata);
    const pdfUrl = await blobToDataURL(pdfBlob);
    const txtUrl = textToDataURL(buildSidecarText('captura.jpeg', metadata));
    const shaUrl = textToDataURL(`SHA256 (captura.jpeg)\r\n${hashes.sha256 || ''}\r\n`);

    const firstDownload = await downloadFile(jpegUri, `${folder}/captura.jpeg`);
    const actualFolder = await folderFromDownload(firstDownload.id);
    if (actualFolder) {
      folder = actualFolder;
    }

    await downloadFile(txtUrl, `${folder}/metadatos.txt`);
    await downloadFile(pdfUrl, `${folder}/captura.pdf`);
    await downloadFile(shaUrl, `${folder}/SHA256.txt`);

    const appSettings = appSettingsEarly;
    let uploaded = false;
    let uploadError = '';
    if (appSettings.saveMode === 'server') {
      const uploadResult = await uploadToFlask(jpegUri, metadata, appSettings, folder);
      uploaded = Boolean(uploadResult.ok);
      uploadError = uploadResult.error || '';
      if (uploaded && uploadResult.evidenceId) {
        const stored = await chrome.storage.local.get(['otsFolders']);
        const otsFolders = stored.otsFolders || {};
        otsFolders[String(uploadResult.evidenceId)] = folder;
        await chrome.storage.local.set({ otsFolders });
      }
    }

    try {
      chrome.notifications.create({
        type: 'basic',
        iconUrl: 'icon48.png',
        title: uploaded ? 'Captura reportada' : 'Captura lista',
        message: uploaded
          ? 'Guardada en local y subida al caso de esa API Key'
          : (appSettings.saveMode === 'server'
            ? ('Local ok. No subio al servidor: ' + (uploadError || 'revisa modo, URL y API Key'))
            : 'Carpeta con JPEG, PDF y metadatos en Descargas'),
        priority: 2
      });
    } catch (e) {
      // Las notificaciones son opcionales
    }

    sendResponse({
      status: 'success',
      filename: `${folder}/captura.pdf`,
      jpegFilename: `${folder}/captura.jpeg`,
      uploadedToServer: uploaded,
      uploadError: uploadError
    });
  } catch (error) {
    sendResponse({ status: 'error', message: error.message });
  }
}

function downloadFile(url, filename, conflictAction) {
  return new Promise((resolve) => {
    chrome.downloads.download(
      {
        url,
        filename,
        conflictAction: conflictAction || 'uniquify',
        saveAs: false
      },
      (id) => {
        if (chrome.runtime.lastError) {
          resolve({ ok: false, id: null, error: chrome.runtime.lastError.message });
          return;
        }
        resolve({ ok: true, id });
      }
    );
  });
}

function folderFromDownload(downloadId) {
  if (!downloadId) {
    return Promise.resolve(null);
  }
  return new Promise((resolve) => {
    const finish = (fullPath) => {
      if (!fullPath) {
        resolve(null);
        return;
      }
      const parts = String(fullPath).replace(/\\/g, '/').split('/').filter(Boolean);
      resolve(parts.length >= 2 ? parts[parts.length - 2] : null);
    };

    const timer = setTimeout(() => {
      chrome.downloads.onChanged.removeListener(onChanged);
      chrome.downloads.search({ id: downloadId }, (items) => {
        finish(items && items[0] ? items[0].filename : '');
      });
    }, 2500);

    function onChanged(delta) {
      if (delta.id !== downloadId || !delta.filename || !delta.filename.current) {
        return;
      }
      clearTimeout(timer);
      chrome.downloads.onChanged.removeListener(onChanged);
      finish(delta.filename.current);
    }

    chrome.downloads.onChanged.addListener(onChanged);
    chrome.downloads.search({ id: downloadId }, (items) => {
      if (items && items[0] && items[0].filename) {
        clearTimeout(timer);
        chrome.downloads.onChanged.removeListener(onChanged);
        finish(items[0].filename);
      }
    });
  });
}

function safeCaptureFolder(name) {
  const cleaned = String(name || '').replace(/\\/g, '/').split('/').pop();
  if (!cleaned || cleaned === '.' || cleaned === '..') {
    return null;
  }
  return cleaned.slice(0, 180);
}

async function findExistingCaptureFolder(hint, evidenceId) {
  let folder = safeCaptureFolder(hint);
  if (!folder && evidenceId) {
    const stored = await chrome.storage.local.get(['otsFolders']);
    folder = safeCaptureFolder((stored.otsFolders || {})[String(evidenceId)]);
  }

  const pickFromItems = (items) => {
    const hit = (items || []).find((item) => /captura\.jpe?g$/i.test(String(item.filename || '').replace(/\\/g, '/')));
    if (!hit || !hit.filename) {
      return null;
    }
    const parts = String(hit.filename).replace(/\\/g, '/').split('/').filter(Boolean);
    return parts.length >= 2 ? parts[parts.length - 2] : null;
  };

  try {
    const searchWithTimeout = (query) => Promise.race([
      chrome.downloads.search(query),
      new Promise((resolve) => setTimeout(() => resolve([]), 2500))
    ]);
    if (folder) {
      const matches = await searchWithTimeout({
        query: [folder, 'captura.jpeg'],
        limit: 50,
        orderBy: ['-startTime']
      });
      const found = pickFromItems(matches);
      if (found) {
        return found;
      }
    }
    const recent = await searchWithTimeout({
      query: ['SCIC_', 'captura.jpeg'],
      limit: 30,
      orderBy: ['-startTime']
    });
    const foundRecent = pickFromItems(recent);
    if (folder) {
      return folder;
    }
    if (foundRecent) {
      return foundRecent;
    }
  } catch (e) {
    // usamos el nombre recordado
  }
  return folder;
}

async function saveOtsToCaptureFolder(request) {
  const folder = await findExistingCaptureFolder(request.folder, request.evidenceId);
  if (!folder) {
    throw new Error('No hay carpeta de captura asociada. Recaptura con la extension actualizada.');
  }
  const files = Array.isArray(request.files) ? request.files : [];
  if (!files.length) {
    throw new Error('No hay archivos OTS para guardar');
  }
  for (const file of files) {
    const name = String(file.name || 'captura.ots').replace(/[\\/]/g, '');
    if (!file.dataUrl || !name) {
      continue;
    }
    const saved = await downloadFile(file.dataUrl, `${folder}/${name}`, 'overwrite');
    if (!saved.ok) {
      throw new Error(saved.error || ('No se pudo guardar ' + name));
    }
  }
  return { ok: true, folder };
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function captureVisibleOnce() {
  return new Promise((resolve, reject) => {
    chrome.tabs.captureVisibleTab(null, { format: 'jpeg', quality: 90 }, (dataUrl) => {
      if (chrome.runtime.lastError) {
        reject(new Error(chrome.runtime.lastError.message));
      } else {
        resolve(dataUrl);
      }
    });
  });
}

async function captureVisible() {
  const elapsed = Date.now() - lastCaptureVisibleAt;
  if (elapsed < MIN_CAPTURE_GAP_MS) {
    await sleep(MIN_CAPTURE_GAP_MS - elapsed);
  }

  let lastError = null;
  for (let attempt = 0; attempt < 5; attempt++) {
    try {
      const dataUrl = await captureVisibleOnce();
      lastCaptureVisibleAt = Date.now();
      return dataUrl;
    } catch (error) {
      lastError = error;
      const quotaHit = String(error.message || '').includes('MAX_CAPTURE_VISIBLE_TAB');
      if (!quotaHit) {
        throw error;
      }
      await sleep(MIN_CAPTURE_GAP_MS * (attempt + 1));
    }
  }

  throw lastError || new Error('No se pudo capturar el area visible');
}

async function captureArea() {
  const visibleCapture = await captureVisible();
  const tabs = await chrome.tabs.query({ active: true, currentWindow: true });

  const [result] = await chrome.scripting.executeScript({
    target: { tabId: tabs[0].id },
    func: (dataUrl) => {
      return new Promise((resolve) => {
        const selection = sessionStorage.getItem('screenshotSelection');
        if (!selection) {
          resolve(dataUrl);
          return;
        }

        const area = JSON.parse(selection);
        const ratio = area.devicePixelRatio || 1;
        const img = new Image();

        img.onload = () => {
          const canvas = document.createElement('canvas');
          canvas.width = area.width * ratio;
          canvas.height = area.height * ratio;
          const ctx = canvas.getContext('2d');
          ctx.drawImage(
            img,
            area.x * ratio,
            area.y * ratio,
            area.width * ratio,
            area.height * ratio,
            0,
            0,
            area.width * ratio,
            area.height * ratio
          );
          resolve(canvas.toDataURL('image/jpeg', 0.92));
        };

        img.onerror = () => resolve(dataUrl);
        img.src = dataUrl;
      });
    },
    args: [visibleCapture]
  });

  return result.result;
}

async function captureFull() {
  const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
  const tabId = tabs[0].id;

  const [{ result: dims }] = await chrome.scripting.executeScript({
    target: { tabId },
    func: () => {
      const root = document.scrollingElement || document.documentElement;
      const body = document.body;
      return {
        scrollX: window.scrollX,
        scrollY: window.scrollY,
        viewportW: window.innerWidth,
        viewportH: window.innerHeight,
        pageW: Math.max(
          root.scrollWidth,
          body ? body.scrollWidth : 0,
          root.clientWidth
        ),
        pageH: Math.max(
          root.scrollHeight,
          body ? body.scrollHeight : 0,
          root.clientHeight
        ),
        dpr: window.devicePixelRatio || 1
      };
    }
  });

  if (!dims) {
    return captureVisible();
  }

  let pageH = dims.pageH;
  if (pageH * dims.dpr > MAX_FULL_CANVAS_PX) {
    pageH = Math.floor(MAX_FULL_CANVAS_PX / dims.dpr);
  }

  const canvasW = Math.round(dims.viewportW * dims.dpr);
  const canvasH = Math.round(pageH * dims.dpr);

  await chrome.scripting.executeScript({
    target: { tabId },
    func: (width, height) => {
      const canvas = document.createElement('canvas');
      canvas.width = width;
      canvas.height = height;
      window.__scicFullCanvas = canvas;
      window.__scicFullCtx = canvas.getContext('2d');
    },
    args: [canvasW, canvasH]
  });

  const maxY = Math.max(0, pageH - dims.viewportH);
  const positions = [];
  for (let y = 0; y < maxY; y += dims.viewportH) {
    positions.push(y);
  }
  positions.push(maxY);

  const uniquePositions = [...new Set(positions)].slice(0, FULL_CAPTURE_SLICE_LIMIT);

  await chrome.scripting.executeScript({
    target: { tabId },
    func: () => window.scrollTo(0, 0)
  });
  await sleep(250);

  for (let i = 0; i < uniquePositions.length; i++) {
    const y = uniquePositions[i];
    await chrome.scripting.executeScript({
      target: { tabId },
      func: (top) => window.scrollTo(0, top),
      args: [y]
    });
    await sleep(200);

    if (i > 0) {
      await setFixedElementsHidden(tabId, true);
    }

    const slice = await captureVisible();

    if (i > 0) {
      await setFixedElementsHidden(tabId, false);
    }

    await chrome.scripting.executeScript({
      target: { tabId },
      func: (dataUrl, destYCss, dpr) => {
        return new Promise((resolve) => {
          const img = new Image();
          img.onload = () => {
            const ctx = window.__scicFullCtx;
            if (!ctx) {
              resolve(false);
              return;
            }
            const destY = Math.round(destYCss * dpr);
            const remaining = ctx.canvas.height - destY;
            const drawH = Math.min(img.height, remaining);
            ctx.drawImage(img, 0, 0, img.width, drawH, 0, destY, img.width, drawH);
            resolve(true);
          };
          img.onerror = () => resolve(false);
          img.src = dataUrl;
        });
      },
      args: [slice, y, dims.dpr]
    });
  }

  await chrome.scripting.executeScript({
    target: { tabId },
    func: (x, y) => window.scrollTo(x, y),
    args: [dims.scrollX, dims.scrollY]
  });
  await setFixedElementsHidden(tabId, false);

  const [exported] = await chrome.scripting.executeScript({
    target: { tabId },
    func: () => {
      const canvas = window.__scicFullCanvas;
      const dataUrl = canvas ? canvas.toDataURL('image/jpeg', 0.88) : null;
      window.__scicFullCanvas = null;
      window.__scicFullCtx = null;
      return dataUrl;
    }
  });

  return exported.result || captureVisible();
}

async function setFixedElementsHidden(tabId, hide) {
  await chrome.scripting.executeScript({
    target: { tabId },
    func: (shouldHide) => {
      if (shouldHide) {
        if (!window.__scicFixedEls) {
          window.__scicFixedEls = [];
          document.querySelectorAll('body *').forEach((el) => {
            const style = getComputedStyle(el);
            if (style.position === 'fixed' || style.position === 'sticky') {
              window.__scicFixedEls.push([el, el.style.visibility]);
              el.style.visibility = 'hidden';
            }
          });
        }
      } else if (window.__scicFixedEls) {
        window.__scicFixedEls.forEach(([el, vis]) => {
          el.style.visibility = vis;
        });
        window.__scicFixedEls = null;
      }
    },
    args: [hide]
  });
}

async function calculateHashes(uri) {
  const response = await fetch(uri);
  const buffer = await response.arrayBuffer();
  const wordArray = CryptoJS.lib.WordArray.create(buffer);

  return {
    md5: CryptoJS.MD5(wordArray).toString(),
    sha1: CryptoJS.SHA1(wordArray).toString(),
    sha256: CryptoJS.SHA256(wordArray).toString()
  };
}

async function getTrustedUtcTime() {
  const withTimeout = (ms) => AbortSignal.timeout(ms);

  try {
    const response = await fetch('https://www.cloudflare.com/cdn-cgi/trace', {
      signal: withTimeout(5000)
    });
    const text = await response.text();
    const line = text.split('\n').find((row) => row.startsWith('ts='));
    if (line) {
      const unix = parseFloat(line.slice(3));
      if (!Number.isNaN(unix)) {
        return {
          iso: new Date(unix * 1000).toISOString(),
          source: 'cloudflare.com/cdn-cgi/trace'
        };
      }
    }
  } catch (e) {
    // siguiente fuente
  }

  try {
    const response = await fetch('https://worldtimeapi.org/api/timezone/Etc/UTC', {
      signal: withTimeout(5000)
    });
    const data = await response.json();
    const iso = data.utc_datetime || data.datetime;
    if (iso) {
      return { iso: new Date(iso).toISOString(), source: 'worldtimeapi.org' };
    }
  } catch (e) {
    // siguiente fuente
  }

  try {
    const response = await fetch('https://timeapi.io/api/Time/current/zone?timeZone=UTC', {
      signal: withTimeout(5000)
    });
    const data = await response.json();
    const iso = data.dateTime || data.utcDateTime;
    if (iso) {
      return { iso: new Date(iso).toISOString(), source: 'timeapi.io' };
    }
  } catch (e) {
    // reloj local como ultimo recurso
  }

  return { iso: new Date().toISOString(), source: 'reloj local (sin red)' };
}

async function getUserIp() {
  const readers = [
    async () => {
      const response = await fetch('https://api.ipify.org?format=json', { signal: AbortSignal.timeout(4000) });
      const data = await response.json();
      return data.ip;
    },
    async () => {
      const response = await fetch('https://api64.ipify.org?format=json', { signal: AbortSignal.timeout(4000) });
      const data = await response.json();
      return data.ip;
    },
    async () => {
      const response = await fetch('https://icanhazip.com', { signal: AbortSignal.timeout(4000) });
      return (await response.text()).trim();
    },
    async () => {
      const response = await fetch('https://www.cloudflare.com/cdn-cgi/trace', { signal: AbortSignal.timeout(4000) });
      const text = await response.text();
      const line = text.split('\n').find((row) => row.startsWith('ip='));
      return line ? line.slice(3).trim() : '';
    }
  ];
  for (const read of readers) {
    try {
      const ip = (await read() || '').trim();
      if (ip && ip !== 'N/A' && /[0-9a-f:]/i.test(ip)) {
        return ip;
      }
    } catch (e) {
      // siguiente fuente
    }
  }
  return 'N/A';
}

async function getServerIp(url) {
  try {
    const hostname = new URL(url).hostname;
    if (/^(\d{1,3}\.){3}\d{1,3}$/.test(hostname) || hostname.includes(':')) {
      return hostname.replace(/^\[|\]$/g, '');
    }
    const endpoints = [
      `https://cloudflare-dns.com/dns-query?name=${encodeURIComponent(hostname)}&type=A`,
      `https://dns.google/resolve?name=${encodeURIComponent(hostname)}&type=A`
    ];
    for (const endpoint of endpoints) {
      try {
        const response = await fetch(endpoint, {
          headers: { Accept: 'application/dns-json' },
          signal: AbortSignal.timeout(4000)
        });
        const data = await response.json();
        const answer = (data.Answer || []).find((row) => row.type === 1 && row.data);
        if (answer && answer.data) {
          return answer.data;
        }
      } catch (e) {
        // siguiente resolver
      }
    }
  } catch (error) {
    // sin hostname valido
  }
  return 'N/A';
}

async function uploadToFlask(jpegUri, metadata, settings, downloadFolder) {
  try {
    const apiKey = settings?.apiKey;
    const flaskServer = (settings?.serverUrl || '').replace(/\/$/, '');

    if (!apiKey) {
      throw new Error('Falta API Key en la extension');
    }
    if (!flaskServer) {
      throw new Error('Falta la URL del servidor en la extension');
    }

    const uploadData = {
      image: jpegUri,
      timestamp: metadata.utc3Date,
      time_source: metadata.timeSource,
      url: metadata.url,
      user_ip: metadata.userIp,
      server_ip: metadata.urlIp,
      md5: metadata.jpegHashes.md5,
      sha1: metadata.jpegHashes.sha1,
      sha256: metadata.jpegHashes.sha256,
      capture_mode: metadata.captureMode,
      api_key: apiKey,
      download_folder: downloadFolder || ''
    };

    const response = await fetch(`${flaskServer}/api/upload`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Accept: 'application/json',
        'X-API-Key': apiKey
      },
      body: JSON.stringify(uploadData)
    });

    let data = {};
    try {
      data = await response.json();
    } catch (e) {
      data = {};
    }

    if (!response.ok || !data.success) {
      throw new Error(data.error || ('HTTP ' + response.status));
    }

    return { ok: true, sessionId: data.session_id, evidenceId: data.id, sha256: data.sha256 };
  } catch (error) {
    console.error('Upload al servidor:', error);
    return { ok: false, error: error.message };
  }
}

function buildSidecarText(jpegFilename, metadata) {
  const modeLabel =
    metadata.captureMode === 'area'
      ? 'Area seleccionada'
      : metadata.captureMode === 'full'
        ? 'Pagina completa'
        : 'Area visible';

  return [
    'INFORME DE CAPTURA - SCIC v2.5',
    '================================',
    '',
    '[ IMAGEN ]',
    `Archivo     : ${jpegFilename}`,
    `Modo        : ${modeLabel}`,
    '',
    '[ TIEMPO ]',
    `Fecha/Hora  : ${metadata.dualTime || metadata.utc3Date}`,
    `Zona        : UTC y ${tzOffsetLabel(metadata.timezoneOffset)}`,
    `Fuente      : ${metadata.timeSource || 'N/A'}`,
    '',
    '[ RED ]',
    `URL         : ${metadata.url}`,
    `IP usuario  : ${metadata.userIp}`,
    `IP del sitio: ${metadata.urlIp}`,
    '',
    '[ HASHES DE INTEGRIDAD ]',
    `MD5         : ${metadata.jpegHashes.md5}`,
    `SHA1        : ${metadata.jpegHashes.sha1}`,
    `SHA256      : ${metadata.jpegHashes.sha256 || 'N/A'}`,
    '',
    '================================',
    'Documento generado automaticamente por la extension SCIC.',
    'Los hashes corresponden al archivo captura.jpeg de esta carpeta.',
    ''
  ].join('\r\n');
}

function tzOffsetLabel(hours) {
  const n = Number(hours);
  const value = Number.isFinite(n) ? n : -3;
  const sign = value >= 0 ? '+' : '-';
  return 'UTC' + sign + Math.abs(value);
}

function formatDualTime(iso, offsetHours) {
  const utcDate = new Date(iso);
  if (Number.isNaN(utcDate.getTime())) {
    return String(iso || 'N/A');
  }
  const offset = Number.isFinite(Number(offsetHours)) ? Number(offsetHours) : -3;
  const pad = (n) => String(n).padStart(2, '0');
  const utcStr =
    utcDate.getUTCFullYear() +
    '-' + pad(utcDate.getUTCMonth() + 1) +
    '-' + pad(utcDate.getUTCDate()) +
    ' ' + pad(utcDate.getUTCHours()) +
    ':' + pad(utcDate.getUTCMinutes()) +
    ':' + pad(utcDate.getUTCSeconds()) +
    ' UTC';
  const local = new Date(utcDate.getTime() + offset * 3600000);
  const localStr =
    local.getUTCFullYear() +
    '-' + pad(local.getUTCMonth() + 1) +
    '-' + pad(local.getUTCDate()) +
    ' ' + pad(local.getUTCHours()) +
    ':' + pad(local.getUTCMinutes()) +
    ':' + pad(local.getUTCSeconds()) +
    ' ' + tzOffsetLabel(offset);
  return utcStr + '  |  ' + localStr;
}

function textToDataURL(text) {
  return 'data:text/plain;charset=utf-8,' + encodeURIComponent(text);
}

function sanitizePdfText(value) {
  // Permite Latin-1 supplement (0xA0-0xFF) para caracteres españoles (á é í ó ú ñ ü etc.)
  // Solo strip controles y caracteres fuera de Latin-1
  return String(value || 'N/A')
    .replace(/[\x00-\x1F\x7F-\x9F]/g, '')
    .replace(/[^\x20-\xFF]/g, '?');
}

function clipPage(page, x, y, width, height) {
  const { pushGraphicsState, popGraphicsState, rectangle, clip, endPath } = PDFLib;
  page.pushOperators(
    pushGraphicsState(),
    rectangle(x, y, width, height),
    clip(),
    endPath()
  );
}

function unclipPage(page) {
  const { popGraphicsState } = PDFLib;
  page.pushOperators(popGraphicsState());
}

async function createPDF(jpegUri, metadata) {
  const { PDFDocument, rgb, StandardFonts } = PDFLib;
  const pdfDoc = await PDFDocument.create();
  const font = await pdfDoc.embedFont(StandardFonts.Helvetica);
  const boldFont = await pdfDoc.embedFont(StandardFonts.HelveticaBold);
  const jpegBytes = await fetch(jpegUri).then((res) => res.arrayBuffer());
  const image = await pdfDoc.embedJpg(jpegBytes);

  const modeLabel =
    metadata.captureMode === 'area'
      ? 'Area seleccionada'
      : metadata.captureMode === 'full'
        ? 'Pagina completa'
        : 'Area visible';

  const fields = [
    ['Modo', modeLabel],
    ['Fecha/Hora', metadata.dualTime || metadata.utc3Date],
    ['Zona horaria', 'UTC y ' + tzOffsetLabel(metadata.timezoneOffset)],
    ['Fuente de hora', metadata.timeSource || 'N/A'],
    ['URL', metadata.url],
    ['IP usuario', metadata.userIp],
    ['IP del sitio', metadata.urlIp],
    ['MD5', metadata.jpegHashes.md5],
    ['SHA1', metadata.jpegHashes.sha1],
    ['SHA256', metadata.jpegHashes.sha256 || 'N/A']
  ];

  const pageWidth = 595.28;
  const pageHeight = 841.89;
  const marginTop = 48;
  const marginBottom = 48;
  const marginX = 48;
  const contentWidth = pageWidth - marginX * 2;
  const firstPage = pdfDoc.addPage([pageWidth, pageHeight]);

  firstPage.drawText('INFORME DE CAPTURA', {
    x: marginX,
    y: pageHeight - marginTop,
    size: 16,
    font: boldFont,
    color: rgb(0.15, 0.15, 0.15)
  });

  let y = pageHeight - marginTop - 28;
  fields.forEach(([label, value]) => {
    firstPage.drawText(sanitizePdfText(label) + ':', {
      x: marginX,
      y,
      size: 9,
      font: boldFont
    });
    const text = sanitizePdfText(value);
    const maxChars = 68;
    const lines = [];
    for (let i = 0; i < text.length; i += maxChars) {
      lines.push(text.substring(i, i + maxChars));
    }
    lines.forEach((line, idx) => {
      firstPage.drawText(line, {
        x: marginX + 118,
        y: y - idx * 11,
        size: 8,
        font
      });
    });
    y -= 16 + Math.max(0, lines.length - 1) * 11;
  });

  const imageGap = 18;
  const clipBottom = marginBottom;
  const clipTop = y - imageGap;
  const firstClipHeight = clipTop - clipBottom;
  const scale = contentWidth / image.width;
  const scaledW = contentWidth;
  const scaledH = image.height * scale;

  if (firstClipHeight > 20) {
    clipPage(firstPage, marginX, clipBottom, contentWidth, firstClipHeight);
    firstPage.drawImage(image, {
      x: marginX,
      y: clipTop - scaledH,
      width: scaledW,
      height: scaledH
    });
    unclipPage(firstPage);
  }

  let shown = Math.max(0, firstClipHeight);
  while (shown < scaledH - 1) {
    const page = pdfDoc.addPage([pageWidth, pageHeight]);
    const usableH = pageHeight - marginTop - marginBottom;
    clipPage(page, marginX, marginBottom, contentWidth, usableH);
    page.drawImage(image, {
      x: marginX,
      y: pageHeight - marginTop - scaledH + shown,
      width: scaledW,
      height: scaledH
    });
    unclipPage(page);
    shown += usableH;
  }

  const pdfBytes = await pdfDoc.save();
  return new Blob([pdfBytes], { type: 'application/pdf' });
}

function blobToDataURL(blob) {
  return new Promise((resolve) => {
    const reader = new FileReader();
    reader.onloadend = () => resolve(reader.result);
    reader.readAsDataURL(blob);
  });
}
