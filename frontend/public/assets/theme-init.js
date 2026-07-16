// 同步设置主题，避免首次渲染先亮后暗。
(function () {
  const theme = localStorage.getItem('admin-theme') || 'light';
  if (theme === 'dark') document.documentElement.classList.add('dark');
})();
