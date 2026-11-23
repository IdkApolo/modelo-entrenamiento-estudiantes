document.addEventListener('DOMContentLoaded', function () {
    const app = document.getElementById('monitor-app');
    if (!app) return;

    const toggleButton = document.getElementById('camera-toggle');
    const cameraStatus = document.getElementById('camera-status');
    const videoFeed = document.getElementById('video-feed');
    const liveBadge = document.getElementById('live-badge');
    const cameraTime = document.getElementById('camera-time');
    const recentList = document.getElementById('recent-list');
    const threshold = document.getElementById('confidence-threshold');
    const thresholdValue = document.getElementById('threshold-value');
    const waitSelect = document.getElementById('detection-wait');
    let pollTimer = null;
    let isStarting = false;

    function setCameraState(active) {
        cameraStatus.textContent = active ? 'Cámara activa' : 'Cámara inactiva';
        cameraStatus.classList.toggle('status-ok', active);
        cameraStatus.classList.toggle('status-neutral', !active);
        liveBadge.textContent = active ? 'EN VIVO' : 'FUERA DE VIVO';
        liveBadge.classList.toggle('is-live', active);
        toggleButton.textContent = active ? 'Detener cámara' : 'Iniciar cámara';
        toggleButton.classList.toggle('button-outline', active);
        toggleButton.classList.toggle('button-primary', !active);
    }

    function updateClock() {
        const now = new Date();
        cameraTime.textContent = now.toLocaleString('es-PE', {
            day: '2-digit', month: '2-digit', year: 'numeric',
            hour: '2-digit', minute: '2-digit', second: '2-digit'
        });
    }

    async function saveSettings() {
        try {
            await fetch('/api/configuracion', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    umbral: Number(threshold.value),
                    espera_minutos: Number(waitSelect.value)
                })
            });
        } catch (error) {
            console.error('No se pudieron guardar los ajustes:', error);
        }
    }

    async function loadModelState() {
        try {
            const response = await fetch('/api/estado_modelo');
            const model = await response.json();
            document.getElementById('model-state').textContent =
                model.entrenado ? 'Entrenado' : 'Pendiente';
            document.getElementById('model-photos').textContent = model.fotos;
            document.getElementById('model-persons').textContent = model.personas;
        } catch (error) {
            document.getElementById('model-state').textContent = 'No disponible';
            console.error('No se pudo consultar el estado del modelo:', error);
        }
    }

    function initials(name) {
        return String(name || 'DESCONOCIDO')
            .split(/\s+/)
            .filter(Boolean)
            .slice(0, 2)
            .map(function (word) { return word.charAt(0).toUpperCase(); })
            .join('');
    }

    function renderDetections(detections) {
        if (!detections.length) {
            recentList.innerHTML = '<p class="photo-empty">Esperando detecciones.</p>';
            return;
        }

        recentList.innerHTML = '';
        detections.forEach(function (item) {
            const row = document.createElement('article');
            row.className = 'recent-row';
            const isPresent = item.estado === 'presente';

            const avatar = document.createElement('span');
            avatar.className = 'recent-avatar';
            avatar.textContent = isPresent ? initials(item.nombre) : '!';
            row.appendChild(avatar);

            const identity = document.createElement('span');
            identity.className = 'recent-identity';
            const name = document.createElement('strong');
            name.textContent = item.nombre || 'DESCONOCIDO';
            const code = document.createElement('small');
            code.textContent = item.prs_nbr ? 'Código ' + item.prs_nbr : item.hora;
            identity.appendChild(name);
            identity.appendChild(code);
            row.appendChild(identity);

            const confidence = document.createElement('span');
            confidence.className = 'recent-confidence';
            confidence.textContent = item.confianza + '%';
            row.appendChild(confidence);

            const state = document.createElement('span');
            state.className = 'recent-state ' + (isPresent ? 'text-ok' : 'text-alert');
            state.textContent = isPresent ? 'Presente' : 'Desconocido';
            row.appendChild(state);
            recentList.appendChild(row);
        });
    }

    async function pollDetections() {
        try {
            const response = await fetch('/api/detecciones?limite=8');
            const data = await response.json();
            if (!response.ok) throw new Error(data.error || 'No se pudieron consultar las detecciones.');

            document.getElementById('faces-now').textContent = data.rostros_ahora;
            document.getElementById('present-today').textContent = data.presentes_hoy;
            document.getElementById('unknown-count').textContent = data.desconocidos;
            document.getElementById('fps-count').textContent = data.fps;
            renderDetections(data.detecciones);
            setCameraState(data.camara_activa);
        } catch (error) {
            console.error('Error al actualizar detecciones:', error);
        }
    }

    async function toggleCamera() {
        if (isStarting) return;
        isStarting = true;

        const active = liveBadge.classList.contains('is-live');
        toggleButton.disabled = true;
        try {
            const response = await fetch(active ? '/api/camara/detener' : '/api/camara/iniciar', {
                method: 'POST'
            });
            const data = await response.json();
            if (!response.ok) throw new Error(data.error || 'No se pudo cambiar la cámara.');

            setCameraState(data.activa);
            if (data.activa) {
                videoFeed.src = '/video_feed?' + Date.now();
            } else {
                videoFeed.removeAttribute('src');
            }
        } catch (error) {
            console.error(error);
            setCameraState(false);
        } finally {
            toggleButton.disabled = false;
            isStarting = false;
            pollDetections();
        }
    }

    threshold.addEventListener('input', function () {
        thresholdValue.textContent = threshold.value + '%';
    });
    threshold.addEventListener('change', saveSettings);
    waitSelect.addEventListener('change', saveSettings);
    toggleButton.addEventListener('click', toggleCamera);
    window.addEventListener('beforeunload', function () {
        if (liveBadge.classList.contains('is-live')) {
            navigator.sendBeacon('/api/camara/detener');
        }
    });

    updateClock();
    window.setInterval(updateClock, 1000);
    pollDetections();
    loadModelState();
    pollTimer = window.setInterval(pollDetections, 2000);
    window.addEventListener('pagehide', function () {
        window.clearInterval(pollTimer);
    });
});
