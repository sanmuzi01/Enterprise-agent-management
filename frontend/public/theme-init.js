// 首屏前就定好主题，避免深色用户先闪一下白屏（由 index.html 以外部脚本加载，CSP 因此不需要允许内联脚本）
try {
  var t = localStorage.getItem('theme')
  if (t !== 'light' && t !== 'dark') t = matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
  document.documentElement.setAttribute('data-theme', t)
} catch (e) {}
