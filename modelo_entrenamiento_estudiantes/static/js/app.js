// app.js - Common JavaScript utilities for VidVig

// Show a Bootstrap toast message
function showToast(message, title = 'Notificación', type = 'info') {
    // Ensure toast container exists (already in base.html)
    const container = document.getElementById('toast-container');
    if (!container) return;

    const toastId = 'toast-' + Date.now();
    const toastHTML = `
    <div id="${toastId}" class="toast" role="alert" aria-live="assertive" aria-atomic="true" data-bs-delay="5000">
      <div class="toast-header">
        <strong class="me-auto">${title}</strong>
        <small class="text-muted">just now</small>
        <button type="button" class="btn-close" data-bs-dismiss="toast" aria-label="Close"></button>
      </div>
      <div class="toast-body text-${type}">
        ${message}
      </div>
    </div>`;
    container.insertAdjacentHTML('beforeend', toastHTML);
    const toastEl = document.getElementById(toastId);
    const toast = new bootstrap.Toast(toastEl);
    toast.show();
}

// Example: expose globally for other scripts
window.showToast = showToast;
