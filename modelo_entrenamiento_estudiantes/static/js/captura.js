document.addEventListener('DOMContentLoaded', function () {
    const app = document.getElementById('capture-app');
    if (!app) return;

    const video = document.getElementById('camera-video');
    const canvas = document.getElementById('capture-canvas');
    const cameraToggle = document.getElementById('camera-toggle');
    const captureButton = document.getElementById('capture-button');
    const cameraState = document.getElementById('camera-state');
    const cameraMessage = document.getElementById('camera-message');
    const photoGrid = document.getElementById('photo-grid');
    const photoEmpty = document.getElementById('photo-empty');
    const photoCount = document.getElementById('photo-count');
    const photoProgress = document.getElementById('photo-progress');
    const minimumRule = document.getElementById('minimum-rule');
    const qualityRule = document.getElementById('quality-rule');
    const trainButton = document.getElementById('train-button');
    const trainingStatus = document.getElementById('training-status');
    const trainingProgress = document.getElementById('training-progress');
    const photosUrl = app.dataset.photosUrl;
    const trainUrl = app.dataset.trainUrl;
    const trainStatusUrl = app.dataset.trainStatusUrl;
    let cameraStream = null;
    let photos = [];
    let capturing = false;
    let training = false;

    function showCameraMessage(message, isError) {
        cameraMessage.textContent = message;
        cameraMessage.classList.toggle('text-alert', Boolean(isError));
    }

    function updateSummary() {
        const validPhotos = photos.filter(function (photo) { return photo.valida; }).length;
        const invalidPhotos = photos.length - validPhotos;
        const displayedCount = Math.min(photos.length, 20);
        photoCount.textContent = photos.length + ' de 20 recomendadas · ' + validPhotos + ' válidas';
        photoProgress.setAttribute('aria-valuenow', String(displayedCount));
        photoProgress.querySelector('span').style.width = (displayedCount / 20 * 100) + '%';
        minimumRule.classList.toggle('is-valid', validPhotos >= 10);
        qualityRule.classList.toggle('is-warning', invalidPhotos > 0);
        trainButton.disabled = validPhotos < 10 || training;
        photoEmpty.hidden = photos.length > 0;
        return validPhotos;
    }

    function renderPhotos() {
        photoGrid.querySelectorAll('.photo-tile').forEach(function (tile) { tile.remove(); });

        photos.forEach(function (photo) {
            const tile = document.createElement('article');
            tile.className = 'photo-tile' + (photo.valida ? '' : ' is-invalid');

            const image = document.createElement('img');
            image.src = photo.preview || photo.url;
            image.alt = photo.valida ? 'Foto válida capturada' : 'Foto que requiere reemplazo';
            tile.appendChild(image);

            const validity = document.createElement('span');
            validity.className = 'photo-validity';
            validity.setAttribute('aria-label', photo.valida ? 'Foto válida' : 'Foto por reemplazar');
            tile.appendChild(validity);

            const removeButton = document.createElement('button');
            removeButton.className = 'photo-delete';
            removeButton.type = 'button';
            removeButton.setAttribute('aria-label', 'Eliminar foto capturada');
            removeButton.textContent = '×';
            removeButton.addEventListener('click', function () {
                removePhoto(photo);
            });
            tile.appendChild(removeButton);
            photoGrid.appendChild(tile);
        });

        updateSummary();
    }

    async function removePhoto(photo) {
        if (photo.persistida) {
            try {
                const response = await fetch(photosUrl + '/' + encodeURIComponent(photo.id), {
                    method: 'DELETE'
                });
                const result = await response.json();
                if (!response.ok) throw new Error(result.error || 'No se pudo eliminar la foto.');
            } catch (error) {
                console.error('Error al eliminar foto:', error);
                showCameraMessage(error.message, true);
                return;
            }
        }

        photos = photos.filter(function (item) { return item !== photo; });
        renderPhotos();
    }

    async function loadPhotos() {
        try {
            const response = await fetch(photosUrl);
            const result = await response.json();
            if (!response.ok) throw new Error(result.error || 'No se pudieron cargar las fotos.');
            photos = result.fotos.map(function (photo) {
                return Object.assign({}, photo, {persistida: true});
            });
            renderPhotos();
        } catch (error) {
            console.error('Error al cargar fotos:', error);
            showCameraMessage(error.message, true);
        }
    }

    function stopCamera() {
        if (cameraStream) {
            cameraStream.getTracks().forEach(function (track) { track.stop(); });
            cameraStream = null;
        }
        video.srcObject = null;
        cameraState.textContent = 'Cámara detenida';
        cameraState.classList.remove('is-live');
        cameraToggle.textContent = 'Iniciar cámara';
        captureButton.disabled = true;
    }

    async function startCamera() {
        if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
            showCameraMessage('Este navegador no permite acceder a la cámara.', true);
            return;
        }

        try {
            cameraStream = await navigator.mediaDevices.getUserMedia({
                video: {facingMode: 'user', width: {ideal: 1280}, height: {ideal: 720}},
                audio: false
            });
            video.srcObject = cameraStream;
            await video.play();
            cameraState.textContent = 'Cámara activa';
            cameraState.classList.add('is-live');
            cameraToggle.textContent = 'Detener cámara';
            captureButton.disabled = false;
            showCameraMessage('Ajusta el rostro dentro del óvalo y captura la foto.', false);
        } catch (error) {
            console.error('No se pudo iniciar la cámara:', error);
            showCameraMessage('No se pudo iniciar la cámara. Revisa los permisos del navegador.', true);
            stopCamera();
        }
    }

    async function capturePhoto() {
        if (!video.videoWidth || capturing) return;

        capturing = true;
        captureButton.disabled = true;
        canvas.width = video.videoWidth;
        canvas.height = video.videoHeight;
        canvas.getContext('2d').drawImage(video, 0, 0, canvas.width, canvas.height);
        const preview = canvas.toDataURL('image/jpeg', 0.9);

        try {
            const response = await fetch(photosUrl, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({imagen: preview})
            });
            const result = await response.json();
            if (!response.ok) throw new Error(result.error || 'No se pudo guardar la foto.');

            if (result.valida) {
                photos.push(Object.assign({}, result.foto, {
                    valida: true,
                    persistida: true
                }));
                showCameraMessage('Foto válida guardada.', false);
            } else {
                photos.push({
                    id: 'local-' + Date.now(),
                    preview: preview,
                    valida: false,
                    persistida: false
                });
                showCameraMessage(result.motivo || 'La foto no es válida; captura otra.', true);
            }
            renderPhotos();
        } catch (error) {
            console.error('Error al capturar foto:', error);
            showCameraMessage(error.message, true);
        } finally {
            capturing = false;
            captureButton.disabled = !cameraStream;
        }
    }

    function setTrainingProgress(progress) {
        const boundedProgress = Math.max(0, Math.min(100, Number(progress) || 0));
        trainingProgress.setAttribute('aria-valuenow', String(boundedProgress));
        trainingProgress.querySelector('span').style.width = boundedProgress + '%';
    }

    async function pollTraining(jobId) {
        const statusUrl = trainStatusUrl.replace('JOB_ID', encodeURIComponent(jobId));
        try {
            const response = await fetch(statusUrl);
            const result = await response.json();
            if (!response.ok) throw new Error(result.error || 'No se pudo consultar el entrenamiento.');
            setTrainingProgress(result.progress);

            if (result.status === 'finished') {
                trainingStatus.textContent = 'Entrenamiento completado. El modelo está listo.';
                training = false;
                updateSummary();
                return;
            }
            if (result.status === 'failed' || result.status === 'unknown') {
                throw new Error(result.error || 'El entrenamiento no pudo completarse.');
            }

            trainingStatus.textContent = 'Entrenando modelo... ' + result.progress + '%';
            window.setTimeout(function () { pollTraining(jobId); }, 1000);
        } catch (error) {
            console.error('Error durante el entrenamiento:', error);
            trainingStatus.textContent = error.message;
            training = false;
            updateSummary();
        }
    }

    async function trainModel() {
        if (updateSummary() < 10 || training) return;

        training = true;
        trainButton.disabled = true;
        trainingStatus.textContent = 'Preparando el entrenamiento...';
        setTrainingProgress(5);

        try {
            const response = await fetch(trainUrl, {method: 'POST'});
            const result = await response.json();
            if (!response.ok) throw new Error(result.error || 'No se pudo iniciar el entrenamiento.');
            pollTraining(result.job_id);
        } catch (error) {
            console.error('No se pudo iniciar el entrenamiento:', error);
            trainingStatus.textContent = error.message;
            training = false;
            updateSummary();
        }
    }

    cameraToggle.addEventListener('click', function () {
        if (cameraStream) stopCamera();
        else startCamera();
    });
    captureButton.addEventListener('click', capturePhoto);
    trainButton.addEventListener('click', trainModel);
    window.addEventListener('pagehide', stopCamera);
    loadPhotos();
});
