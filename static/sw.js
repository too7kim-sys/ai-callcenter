/*!
 * AI 콜센터 — Service Worker
 *
 * 전략:
 *   • 셸 자산(HTML/CSS/JS/아이콘) : cache-first (오프라인에서도 UI 로딩)
 *   • /api/*                      : network-only (실시간 데이터 — 캐시 금지)
 *   • SSE (/api/events/stream)    : network-only, 캐시 우회
 *   • 그 외 navigation 실패         : 오프라인 폴백 페이지
 *
 * 버전 변경 시 CACHE_NAME 의 숫자만 올리면 자동으로 옛 캐시가 정리된다.
 */
const CACHE_NAME = "ai-callcenter-v1";

const SHELL_URLS = [
  "/",
  "/offline.html",
  "/manifest.webmanifest",
  "/assets/ui.css",
  "/assets/ui.js",
  "/assets/nav.css",
  "/assets/nav.js",
  "/assets/icons/icon-192.png",
  "/assets/icons/icon-512.png",
  "/assets/icons/apple-touch-icon.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) =>
      // addAll 은 하나라도 실패하면 전체 실패 — addOptional 패턴으로 변경
      Promise.all(SHELL_URLS.map((u) =>
        cache.add(new Request(u, { cache: "reload" })).catch(() => null)
      ))
    ).then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;

  const url = new URL(req.url);

  // API / SSE / WebSocket / 동적 데이터 — 캐시 우회
  if (url.pathname.startsWith("/api/")) return;       // 기본 동작 (네트워크)
  if (url.pathname.startsWith("/widget/loader.js")) return; // 외부 사이트 캐시 충돌 방지

  // 네비게이션 (HTML 페이지) — 네트워크 우선, 실패 시 오프라인 폴백
  if (req.mode === "navigate") {
    event.respondWith(
      fetch(req).catch(() =>
        caches.match("/offline.html").then((r) => r || new Response(
          "<h1>오프라인 상태입니다.</h1>",
          { status: 503, headers: { "Content-Type": "text/html; charset=utf-8" } }
        ))
      )
    );
    return;
  }

  // 정적 자산 — 캐시 우선, 백그라운드 갱신
  event.respondWith(
    caches.match(req).then((cached) => {
      const networkFetch = fetch(req).then((res) => {
        if (res && res.status === 200 && res.type !== "opaque") {
          const copy = res.clone();
          caches.open(CACHE_NAME).then((c) => c.put(req, copy));
        }
        return res;
      }).catch(() => cached);
      return cached || networkFetch;
    })
  );
});
