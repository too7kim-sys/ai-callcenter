// PWA 부팅 — 서비스 워커 등록 + manifest 링크 자동 삽입 + 설치 프롬프트
//          + CSRF Double-Submit Cookie 자동 첨부 (fetch 가로채기).
// 모든 주요 페이지에서 <script src="/assets/pwa.js" defer></script> 한 줄로 활성화.

(function () {
  // ---------- CSRF — 상태 변경 요청에 X-CSRF-Token 헤더 자동 첨부 ----------
  // 서버 미들웨어가 _csrf_token 쿠키와 X-CSRF-Token 헤더 일치 검증.
  // 쿠키는 서버가 발급(HttpOnly X). 여기선 쿠키 값을 읽어 헤더로 첨부.
  function _readCookie(name) {
    const m = document.cookie.match(new RegExp("(?:^|; )" + name + "=([^;]*)"));
    return m ? decodeURIComponent(m[1]) : "";
  }
  const _origFetch = window.fetch ? window.fetch.bind(window) : null;
  if (_origFetch) {
    window.fetch = function (input, init) {
      try {
        const req = (typeof input === "string" || input instanceof URL)
          ? new Request(input, init) : input;
        const method = (init && init.method || req.method || "GET").toUpperCase();
        if (!["GET","HEAD","OPTIONS"].includes(method)) {
          const tok = _readCookie("_csrf_token");
          if (tok) {
            init = init || {};
            const headers = new Headers(init.headers || req.headers || {});
            if (!headers.has("X-CSRF-Token")) headers.set("X-CSRF-Token", tok);
            init.headers = headers;
            // 원본이 Request 객체라면 method/body 도 같이 전달
            if (!(typeof input === "string" || input instanceof URL)) {
              return _origFetch(new Request(req, init));
            }
          }
        }
      } catch (e) { /* fallback to original fetch */ }
      return _origFetch(input, init);
    };
  }

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
