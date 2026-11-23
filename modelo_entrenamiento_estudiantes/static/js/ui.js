// UI helpers for the modern interface
// Requires fetch API (available in modern browsers)

function startTraining(nbr) {
  const btn = document.getElementById(`train-${nbr}`);
  const spinner = document.getElementById(`spinner-${nbr}`);
  // Disable button and show spinner
  btn.disabled = true;
  spinner.classList.remove('d-none');

  // Start training via POST (no body needed)
  fetch(`/train_classifier/${nbr}`, {method: 'POST'})
    .then(r => r.json())
    .then(data => {
      const jobId = data.job_id;
      // Poll status every 1s
      const poll = setInterval(() => {
        fetch(`/train_status/${jobId}`)
          .then(r => r.json())
          .then(status => {
            if (status.status === 'finished') {
              clearInterval(poll);
              btn.disabled = false;
              spinner.classList.add('d-none');
              // Optional: show a toast/alert
              alert('Entrenamiento completado para ID ' + nbr);
            }
          })
          .catch(err => {
            console.error('Error checking status', err);
          });
      }, 1000);
    })
    .catch(err => {
      console.error('Error starting training', err);
      btn.disabled = false;
      spinner.classList.add('d-none');
      alert('No se pudo iniciar el entrenamiento.');
    });
}
