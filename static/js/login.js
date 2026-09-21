/* Login page script — external file for CSP script-src 'self' compliance
 *
 * 企业版 - 仅支持登录，注册功能已禁用
 */

/* ---- login ---- */

async function doLogin() {
  var btn = document.getElementById('login-btn');
  var errEl = document.getElementById('login-error');
  var email = document.getElementById('login-email').value.trim();
  var password = document.getElementById('login-password').value;
  errEl.textContent = '';
  if (!email || !password) { errEl.textContent = '请输入账号和密码'; return; }
  btn.disabled = true; btn.innerHTML = '<span class="spinner"></span>';
  try {
    var r = await fetch('/api/auth/login', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({email: email, password: password})
    });
    var d = await r.json();
    if (d.error) { errEl.textContent = d.error; btn.disabled = false; btn.textContent = '登录'; return; }
    window.location.href = '/';
  } catch(e) {
    errEl.textContent = '网络错误'; btn.disabled = false; btn.textContent = '登录';
  }
}

/* ---- event listeners ---- */

document.getElementById('login-btn').addEventListener('click', doLogin);
document.getElementById('login-password').addEventListener('keydown', function(e) { if (e.key === 'Enter') doLogin(); });
document.getElementById('login-email').addEventListener('keydown', function(e) { if (e.key === 'Enter') doLogin(); });

