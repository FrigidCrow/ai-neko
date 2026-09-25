'use strict';

const accept = document.getElementById('accept');
document.getElementById('decline').addEventListener('click', () => window.aiNekoConsent.decline());
accept.addEventListener('click', async () => {
  accept.disabled = true;
  try { await window.aiNekoConsent.accept(); }
  catch { document.getElementById('error').textContent = '无法保存同意记录，请检查本应用数据目录权限。'; accept.disabled = false; }
});
window.aiNekoConsent.terms().then((text) => {
  document.getElementById('terms').textContent = text;
  accept.disabled = false;
}).catch(() => { document.getElementById('error').textContent = '随包条款读取失败，请完整解压应用后再启动。'; });
