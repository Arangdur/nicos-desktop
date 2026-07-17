document.getElementById('btn-director').addEventListener('click', async () => {
  await window.nicos.setRole('director');
});
document.getElementById('btn-operativa').addEventListener('click', async () => {
  await window.nicos.setRole('operativa');
});
