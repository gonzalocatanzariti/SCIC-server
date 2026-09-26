(function () {
// Prevenir carga duplicada
if (window.__screenshotExtensionLoaded__) {
  console.log('⚠️ [Content] Ya está cargado, limpiando y reiniciando...');
  // Limpiar cualquier overlay anterior
  const oldOverlay = document.getElementById('screenshot-extension-overlay');
  const oldBox = document.getElementById('screenshot-extension-box');
  if (oldOverlay) oldOverlay.remove();
  if (oldBox) oldBox.remove();
} else {
  console.log('🚀 [Content] Inicializando content script...');
  window.__screenshotExtensionLoaded__ = true;
}

let overlay = null;
let box = null;
let isSelecting = false;
let startX, startY;
let mouseDownHandler = null;
let mouseMoveHandler = null;
let mouseUpHandler = null;
let keyDownHandler = null;

window.addEventListener('message', (event) => {
  if (event.source !== window || !event.data || event.data.source !== 'scic-dashboard') {
    return;
  }
  if (event.data.action === 'saveOtsToCaptureFolder') {
    chrome.runtime.sendMessage(
      {
        action: 'saveOtsToCaptureFolder',
        folder: event.data.folder,
        evidenceId: event.data.evidenceId,
        files: event.data.files || []
      },
      (response) => {
          const error = chrome.runtime.lastError && chrome.runtime.lastError.message;
          window.postMessage(
            {
              source: 'scic-extension',
              requestId: event.data.requestId,
              result: error ? { ok: false, error: error } : (response || { ok: false, error: 'sin respuesta' })
            },
            '*'
          );
        }
    );
  }
});

// LISTENER DE MENSAJES
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  console.log('📨 [Content] Mensaje recibido:', msg);
  
  if (msg.action === 'startSelection') {
    console.log('📸 [Content] Iniciando selección de área...');
    
    // Limpiar cualquier selección anterior primero
    cleanup();
    
    // Pequeño delay para asegurar limpieza
    setTimeout(() => {
      startAreaSelection();
      sendResponse({ status: 'started' });
    }, 100);
    
    return true;
  }
});

function startAreaSelection() {
  console.log('🎨 [Content] Creando overlay...');
  
  // Asegurar limpieza completa
  cleanup();
  
  // Crear overlay oscuro
  overlay = document.createElement('div');
  overlay.id = 'screenshot-extension-overlay';
  overlay.style.cssText = `
    position: fixed !important;
    top: 0 !important;
    left: 0 !important;
    width: 100% !important;
    height: 100% !important;
    background: rgba(0, 0, 0, 0.3) !important;
    cursor: crosshair !important;
    z-index: 2147483647 !important;
    margin: 0 !important;
    padding: 0 !important;
  `;
  
  // Crear caja de selección
  box = document.createElement('div');
  box.id = 'screenshot-extension-box';
  box.style.cssText = `
    position: fixed !important;
    border: 2px dashed #667eea !important;
    background: rgba(102, 126, 234, 0.1) !important;
    display: none !important;
    z-index: 2147483648 !important;
    pointer-events: none !important;
    margin: 0 !important;
    padding: 0 !important;
  `;
  
  // Crear texto de instrucciones
  const instructions = document.createElement('div');
  instructions.id = 'screenshot-extension-instructions';
  instructions.style.cssText = `
    position: fixed !important;
    top: 50% !important;
    left: 50% !important;
    transform: translate(-50%, -50%) !important;
    background: rgba(0, 0, 0, 0.9) !important;
    color: white !important;
    padding: 20px 30px !important;
    border-radius: 10px !important;
    font-family: Arial, sans-serif !important;
    font-size: 16px !important;
    text-align: center !important;
    z-index: 2147483649 !important;
    pointer-events: none !important;
    margin: 0 !important;
  `;
  instructions.innerHTML = `
    <div><b>Arrastra</b> para seleccionar el area</div>
    <div style="font-size: 12px; margin-top: 10px; opacity: 0.7;">ESC para cancelar</div>
  `;
  
  document.body.appendChild(overlay);
  document.body.appendChild(box);
  document.body.appendChild(instructions);
  
  console.log('✅ [Content] Overlay creado');
  
  // Ocultar instrucciones después de 2s
  setTimeout(() => {
    if (instructions && instructions.parentNode) {
      instructions.remove();
    }
  }, 2000);
  
  // Crear handlers NUEVOS cada vez
  mouseDownHandler = (e) => onMouseDown(e);
  mouseMoveHandler = (e) => onMouseMove(e);
  mouseUpHandler = (e) => onMouseUp(e);
  keyDownHandler = (e) => onKeyDown(e);
  
  // Agregar eventos
  overlay.addEventListener('mousedown', mouseDownHandler, { once: false });
  overlay.addEventListener('mousemove', mouseMoveHandler, { passive: true });
  overlay.addEventListener('mouseup', mouseUpHandler, { once: false });
  document.addEventListener('keydown', keyDownHandler, { once: false });
  
  console.log('✅ [Content] Event listeners agregados');
}

function onMouseDown(e) {
  console.log('🖱️ [Content] Mouse down');
  e.preventDefault();
  e.stopPropagation();
  
  isSelecting = true;
  startX = e.clientX;
  startY = e.clientY;
  
  if (box) {
    box.style.display = 'block';
    updateBox(e.clientX, e.clientY);
  }
}

function onMouseMove(e) {
  if (!isSelecting || !box) return;
  updateBox(e.clientX, e.clientY);
}

function onMouseUp(e) {
  if (!isSelecting) return;
  
  console.log('🖱️ [Content] Mouse up');
  e.preventDefault();
  e.stopPropagation();
  
  isSelecting = false;
  
  const endX = e.clientX;
  const endY = e.clientY;
  const width = Math.abs(endX - startX);
  const height = Math.abs(endY - startY);
  
  console.log('📐 [Content] Área seleccionada:', { width, height });
  
  // Verificar tamaño mínimo
  if (width < 10 || height < 10) {
    cleanup();
    showMsg('⚠️ Área muy pequeña', 'error');
    return;
  }
  
  // Guardar selección
  const selection = {
    x: Math.min(startX, endX),
    y: Math.min(startY, endY),
    width: width,
    height: height,
    devicePixelRatio: window.devicePixelRatio || 1
  };
  
  console.log('💾 [Content] Guardando selección:', selection);
  
  try {
    sessionStorage.setItem('screenshotSelection', JSON.stringify(selection));
    console.log('✅ [Content] Selección guardada en sessionStorage');
  } catch (err) {
    console.error('❌ [Content] Error guardando:', err);
    showMsg('❌ Error guardando selección', 'error');
    cleanup();
    return;
  }
  
  // Limpiar ANTES de enviar mensaje
  cleanup();
  
  // Mostrar mensaje de procesamiento
  const msgEl = showMsg('⏳ Procesando captura...', 'info');
  
  // ENVIAR MENSAJE AL BACKGROUND
  console.log('📤 [Content] Enviando mensaje de captura al background...');
  
  chrome.runtime.sendMessage(
    { action: 'capture', mode: 'area' },
    (response) => {
      console.log('📬 [Content] Respuesta recibida:', response);
      
      // Verificar error de runtime
      if (chrome.runtime.lastError) {
        console.error('❌ [Content] Runtime error:', chrome.runtime.lastError);
        updateMsg(msgEl, '❌ Error: Recarga la extensión', 'error');
        return;
      }
      
      // Verificar respuesta
      if (response && response.status === 'success') {
        console.log('✅ [Content] ¡Captura exitosa!');
        updateMsg(msgEl, '✅ Captura completada', 'success');
      } else {
        console.error('❌ [Content] Error en captura:', response);
        const errorMsg = response?.message || 'Error desconocido';
        updateMsg(msgEl, '❌ ' + errorMsg, 'error');
      }
    }
  );
}

function onKeyDown(e) {
  if (e.key === 'Escape') {
    console.log('⌨️ [Content] ESC presionado - cancelando');
    cleanup();
    showMsg('❌ Cancelado', 'error');
  }
}

function updateBox(x, y) {
  if (!box) return;
  
  const left = Math.min(startX, x);
  const top = Math.min(startY, y);
  const width = Math.abs(x - startX);
  const height = Math.abs(y - startY);
  
  box.style.left = left + 'px';
  box.style.top = top + 'px';
  box.style.width = width + 'px';
  box.style.height = height + 'px';
}

function cleanup() {
  console.log('🧹 [Content] Limpiando...');
  
  // Remover event listeners primero
  if (overlay && mouseDownHandler) {
    overlay.removeEventListener('mousedown', mouseDownHandler);
    overlay.removeEventListener('mousemove', mouseMoveHandler);
    overlay.removeEventListener('mouseup', mouseUpHandler);
  }
  
  if (keyDownHandler) {
    document.removeEventListener('keydown', keyDownHandler);
  }
  
  // Limpiar referencias
  mouseDownHandler = null;
  mouseMoveHandler = null;
  mouseUpHandler = null;
  keyDownHandler = null;
  
  // Remover elementos del DOM
  if (overlay && overlay.parentNode) {
    overlay.remove();
  }
  
  if (box && box.parentNode) {
    box.remove();
  }
  
  // Limpiar por ID también (por si acaso)
  const oldOverlay = document.getElementById('screenshot-extension-overlay');
  const oldBox = document.getElementById('screenshot-extension-box');
  const oldInstructions = document.getElementById('screenshot-extension-instructions');
  
  if (oldOverlay) oldOverlay.remove();
  if (oldBox) oldBox.remove();
  if (oldInstructions) oldInstructions.remove();
  
  overlay = null;
  box = null;
  isSelecting = false;
  
  console.log('✅ [Content] Limpieza completada');
}

function showMsg(text, type) {
  const colors = {
    info: 'rgba(102, 126, 234, 0.95)',
    success: 'rgba(72, 187, 120, 0.95)',
    error: 'rgba(220, 53, 69, 0.95)'
  };
  
  const msg = document.createElement('div');
  msg.className = 'screenshot-extension-message';
  msg.style.cssText = `
    position: fixed !important;
    top: 20px !important;
    left: 50% !important;
    transform: translateX(-50%) !important;
    background: ${colors[type] || colors.info} !important;
    color: white !important;
    padding: 15px 30px !important;
    border-radius: 8px !important;
    font-family: Arial, sans-serif !important;
    font-size: 14px !important;
    font-weight: bold !important;
    z-index: 2147483647 !important;
    box-shadow: 0 4px 12px rgba(0,0,0,0.3) !important;
    margin: 0 !important;
  `;
  msg.textContent = text;
  document.body.appendChild(msg);
  
  // Auto-remover después de 3s
  setTimeout(() => {
    if (msg && msg.parentNode) {
      msg.style.opacity = '0';
      msg.style.transition = 'opacity 0.3s';
      setTimeout(() => {
        if (msg.parentNode) msg.remove();
      }, 300);
    }
  }, 3000);
  
  return msg;
}

function updateMsg(msgEl, text, type) {
  if (!msgEl || !msgEl.parentNode) return;
  
  const colors = {
    info: 'rgba(102, 126, 234, 0.95)',
    success: 'rgba(72, 187, 120, 0.95)',
    error: 'rgba(220, 53, 69, 0.95)'
  };
  
  msgEl.textContent = text;
  msgEl.style.background = colors[type] || colors.info;
}

console.log('✅ [Content] Content script cargado correctamente');
})();