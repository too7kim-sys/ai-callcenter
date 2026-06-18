// PWA 부팅 — 서비스 워커 등록 + manifest 링크 자동 삽입 + 설치 프롬프트.
// 모든 주요 페이지에서 <script src="/assets/pwa.js" defer></script> 한 줄로 활성화.

(function () {
  // 매니페스트 / theme-color / apple-touch-icon 자동 삽입 (페이지마다 중복 입력 방지)
  function injectHead() {
    const head = document.head;
    if (!head.querySelector('link[rel="manifest"]')) {
      const link = document.createElement("link");
      link.rel = "manifest"; link.href = "/manifest.webmanifest";
      head.appendChild(link);
    }
    if (!head.querySelector('meta[name="theme-color"]')) {
      const meta = document.createElement("meta");
      meta.name = "theme-color"; meta.content = "#1d4ed8";
      head.appendChild(meta);
    }
    if (!head.querySelector('link[rel="apple-touch-icon"]')) {
      const link = document.createElement("link");
      link.rel = "apple-touch-icon"; link.href = "/assets/icons/apple-touch-icon.png";
      head.appendChild(link);
    }
    if (!head.querySelector('link[rel="icon"]')) {
      const link = document.createElement("link");
      link.rel = "icon"; link.type = "image/png"; link.href = "/assets/icons/favicon-32.png";
      head.appendChild(link);
    }
  }
  injectHead();

  // Service Worker 등록 (HTTPS 또는 localhost 에서만 동작)
  if ("serviceWorker" in navigator) {
    window.addEventListener("load", () => {
      navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(() => {});
    });
  }

  // 설치 프롬프트 — A2HS 가능한 상태에서 우상단에 작은 버튼 표시
  let deferredPrompt = null;
  window.addEventListener("beforeinstallprompt", (e) => {
    e.preventDefault();
    deferredPrompt = e;
    showInstallButton();
  });

  function showInstallButton() {
    if (document.getElementById("pwa-install-btn")) return;
    const btn = document.createElement("button");
    btn.id = "pwa-install-btn";
    btn.textContent = "📲 앱 설치";
    btn.style.cssText =
      "position:fixed;bottom:16px;left:16px;z-index:2147483645;" +
      "background:#1d4ed8;color:#fff;border:none;border-radius:999px;" +
      "padding:8px 14px;font-size:13px;font-weight:600;cursor:pointer;" +
      "box-shadow:0 6px 20px rgba(15,23,42,.2);";
    btn.addEventListener("click", async () => {
      if (!deferredPrompt) return;
      deferredPrompt.prompt();
      const choice = await deferredPrompt.userChoice.catch(() => null);
      if (choice && choice.outcome === "accepted") btn.remove();
      deferredPrompt = null;
    });
    document.body.appendChild(btn);
  }

  window.addEventListener("appinstalled", () => {
    const btn = document.getElementById("pwa-install-btn");
    if (btn) btn.remove();
  });
})();
