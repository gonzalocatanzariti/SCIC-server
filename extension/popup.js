console.log('🚀 [Popup] Popup script loading...');

const captureVisibleBtn = document.getElementById('captureVisibleBtn');
const captureAreaBtn = document.getElementById('captureAreaBtn');
const captureFullBtn = document.getElementById('captureFullBtn');
const settingsBtn = document.getElementById('settingsBtn');
const saveSettingsBtn = document.getElementById('saveSettingsBtn');
const settingsPanel = document.getElementById('settingsPanel');
const serverUrlInput = document.getElementById('serverUrlInput');
const apiKeyInput = document.getElementById('apiKeyInput');
const modeLocalBtn = document.getElementById('modeLocalBtn');
const modeServerBtn = document.getElementById('modeServerBtn');
const modeHint = document.getElementById('modeHint');
const statusDiv = document.getElementById('status');
const spinner = document.getElementById('spinner');

const timezoneSelect = document.getElementById('timezoneSelect');

function tzLabel(hours) {
  const n = Number(hours);
  const sign = n >= 0 ? '+' : '-';
  return 'UTC' + sign + Math.abs(n);
}

async function getSettings() {
  const result = await chrome.storage.local.get(['saveMode', 'apiKey', 'serverUrl', 'timezoneOffset']);
  const tz = Number(result.timezoneOffset);
  return {
    saveMode: result.saveMode === 'server' ? 'server' : 'local',
    apiKey: result.apiKey || '',
    serverUrl: result.serverUrl || '',
    timezoneOffset: Number.isFinite(tz) ? tz : -3
  };
}

function applyModeUi(saveMode) {
  modeLocalBtn.classList.toggle('active', saveMode === 'local');
  modeServerBtn.classList.toggle('active', saveMode === 'server');
  modeHint.textContent = saveMode === 'local'
    ? 'JPEG y PDF en Descargas. No se envia nada al servidor.'
    : 'JPEG y PDF en Descargas, y ademas se reporta al servidor.';
}

window.addEventListener('DOMContentLoaded', async () => {
  const settings = await getSettings();
  applyModeUi(settings.saveMode);
  serverUrlInput.value = settings.serverUrl;
  apiKeyInput.value = settings.apiKey;
  if (timezoneSelect && !timezoneSelect.options.length) {
    for (let h = -12; h <= 14; h++) {
      const opt = document.createElement('option');
      opt.value = String(h);
      opt.textContent = tzLabel(h) + (h === -3 ? ' (Argentina)' : '');
      timezoneSelect.appendChild(opt);
    }
  }
  if (timezoneSelect) timezoneSelect.value = String(settings.timezoneOffset);

  if (settings.saveMode === 'server' && !settings.apiKey) {
    statusDiv.textContent = 'Modo servidor: configura la API Key';
    statusDiv.className = 'error';
  }
});

modeLocalBtn.addEventListener('click', async () => {
  await chrome.storage.local.set({ saveMode: 'local' });
  applyModeUi('local');
  statusDiv.textContent = '✓ Modo: solo local';
  statusDiv.className = 'success';
});

modeServerBtn.addEventListener('click', async () => {
  await chrome.storage.local.set({ saveMode: 'server' });
  applyModeUi('server');
  const settings = await getSettings();
  if (!settings.apiKey) {
    statusDiv.textContent = 'Configura la API Key para reportar';
    statusDiv.className = 'error';
    settingsPanel.style.display = 'block';
  } else {
    statusDiv.textContent = '✓ Modo: reportar al servidor';
    statusDiv.className = 'success';
  }
});

settingsBtn.addEventListener('click', async () => {
  const visible = settingsPanel.style.display === 'block';
  settingsPanel.style.display = visible ? 'none' : 'block';
  if (!visible) {
    const settings = await getSettings();
    serverUrlInput.value = settings.serverUrl;
    apiKeyInput.value = settings.apiKey;
    if (timezoneSelect) timezoneSelect.value = String(settings.timezoneOffset);
  }
});

saveSettingsBtn.addEventListener('click', async () => {
  const serverUrl = (serverUrlInput.value || '').trim().replace(/\/$/, '');
  const apiKey = apiKeyInput.value.trim();
  const timezoneOffset = timezoneSelect ? parseInt(timezoneSelect.value, 10) : -3;
  await chrome.storage.local.set({ serverUrl, apiKey, timezoneOffset });
  statusDiv.textContent = '✓ Configuración guardada';
  statusDiv.className = 'success';
  settingsPanel.style.display = 'none';
});

// Captura Visible
captureVisibleBtn.addEventListener('click', () => {
  console.log('🖱️ [Popup] Click en Captura Visible');
  captureScreen('visible');
});

// Captura de Área
captureAreaBtn.addEventListener('click', async () => {
  console.log('🖱️ [Popup] Click en Captura de Área');
  statusDiv.textContent = 'Preparando selección...';
  statusDiv.className = '';
  
  try {
    const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
    const tab = tabs[0];
    
    console.log('[Popup] Tab ID:', tab.id, 'URL:', tab.url);
    
    // Verificar que no sea una página especial de Chrome
    if (tab.url.startsWith('chrome://') || tab.url.startsWith('chrome-extension://') || tab.url.startsWith('edge://')) {
      statusDiv.textContent = '✗ No se puede capturar páginas internas de Chrome';
      statusDiv.className = 'error';
      return;
    }
    
    // FORZAR inyección del content script
    console.log('[Popup] Inyectando content script...');
    
    await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      files: ['content.js']
    }).catch(err => {
      console.log('[Popup] Nota: script ya inyectado o error menor:', err.message);
    });
    
    console.log('[Popup] ✅ Script inyectado, esperando...');
    
    // Esperar más tiempo para asegurar que se cargó
    await new Promise(resolve => setTimeout(resolve, 500));
    
    // Verificar que el content script está listo
    console.log('[Popup] Verificando content script...');
    
    const [checkResult] = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: () => {
        return window.__screenshotExtensionLoaded__ === true;
      }
    });
    
    console.log('[Popup] Content script loaded?', checkResult.result);
    
    if (!checkResult.result) {
      statusDiv.textContent = '✗ Error: Recarga la página (F5) e intenta de nuevo';
      statusDiv.className = 'error';
      return;
    }
    
    // Enviar mensaje
    console.log('[Popup] Enviando mensaje startSelection...');
    
    chrome.tabs.sendMessage(tab.id, { action: 'startSelection' }, (response) => {
      if (chrome.runtime.lastError) {
        console.error('[Popup] ❌ Error:', chrome.runtime.lastError);
        statusDiv.textContent = '✗ Recarga la página (F5) e intenta de nuevo';
        statusDiv.className = 'error';
      } else {
        console.log('[Popup] ✅ Mensaje enviado, cerrando popup');
        window.close();
      }
    });
    
  } catch (error) {
    console.error('[Popup] ❌ Excepción:', error);
    statusDiv.textContent = '✗ Error: ' + error.message;
    statusDiv.className = 'error';
  }
});

// Captura Completa
captureFullBtn.addEventListener('click', () => {
  console.log('🖱️ [Popup] Click en Captura Completa');
  captureScreen('full');
});

// Función para capturar
function captureScreen(mode) {
  console.log(`📸 [Popup] captureScreen(${mode})`);
  
  const buttons = [captureVisibleBtn, captureAreaBtn, captureFullBtn];
  buttons.forEach(btn => btn.disabled = true);
  
  spinner.style.display = 'block';
  statusDiv.textContent = mode === 'full' ? 'Capturando página completa...' : 'Capturando...';
  statusDiv.className = '';

  // Enviar mensaje al background
  console.log('[Popup] Enviando mensaje al background...');
  chrome.runtime.sendMessage({ action: 'capture', mode: mode }, (response) => {
    console.log('[Popup] Respuesta del background:', response);
    
    spinner.style.display = 'none';
    buttons.forEach(btn => btn.disabled = false);

    if (chrome.runtime.lastError) {
      console.error('[Popup] ❌ Runtime error:', chrome.runtime.lastError);
      statusDiv.textContent = '✗ Error: Recarga la extensión en chrome://extensions';
      statusDiv.className = 'error';
      return;
    }

    if (response && response.status === 'success') {
      console.log('[Popup] ✅ Captura exitosa');
      statusDiv.textContent = '✓ Captura completada';
      statusDiv.className = 'success';
      
      if (response.uploadedToServer) {
        statusDiv.textContent += ' y reportada al servidor';
      }
      
      setTimeout(() => {
        statusDiv.textContent = '';
        statusDiv.className = '';
      }, 3000);
    } else {
      console.error('[Popup] ❌ Error en respuesta:', response);
      statusDiv.textContent = '✗ Error: ' + (response?.message || 'Error desconocido');
      statusDiv.className = 'error';
    }
  });
}

console.log('✅ [Popup] Popup script loaded');