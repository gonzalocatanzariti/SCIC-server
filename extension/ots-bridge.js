(function () {
  try {
    document.documentElement.setAttribute('data-scic-ext', chrome.runtime.id);
  } catch (e) {
    // sin DOM todavia
  }

  function reply(requestId, result) {
    window.postMessage(
      {
        source: 'scic-extension',
        requestId: requestId,
        result: result || { ok: false, error: 'sin resultado' }
      },
      '*'
    );
  }

  window.addEventListener('message', (event) => {
    if (event.source !== window || !event.data || event.data.source !== 'scic-dashboard') {
      return;
    }
    if (event.data.action !== 'saveOtsToCaptureFolder') {
      return;
    }
    const requestId = event.data.requestId;
    try {
      chrome.runtime.sendMessage(
        {
          action: 'saveOtsToCaptureFolder',
          folder: event.data.folder,
          evidenceId: event.data.evidenceId,
          files: event.data.files || []
        },
        (response) => {
          const error = chrome.runtime.lastError && chrome.runtime.lastError.message;
          reply(requestId, error ? { ok: false, error: error } : response);
        }
      );
    } catch (err) {
      reply(requestId, { ok: false, error: err.message || String(err) });
    }
  });
})();
