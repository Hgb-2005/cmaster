/* 全局小脚本：编译器状态检测、登录页切换、通用键盘行为 */

(function () {
  "use strict";

  document.addEventListener("DOMContentLoaded", function () {
    if (window.CMaster) CMaster.checkCompiler();
  });
})();
