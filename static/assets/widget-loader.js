/*!
 * AI 콜센터 — 임베디드 위젯 로더
 *
 * 사용법 (외부 사이트):
 *   <script src="https://your-callcenter.com/widget/loader.js" defer
 *           data-color="#1d4ed8"
 *           data-title="고객 지원"
 *           data-greeting="무엇을 도와드릴까요?"
 *           data-position="right"></script>
 *
 * 동작:
 *   1) 오른쪽 아래에 챗 버블 버튼 삽입
 *   2) 클릭 시 iframe 으로 /widget 페이지 띄움
 *   3) postMessage 로 위젯과 통신 (close, message, ready)
 */
(function () {
  if (window.__aiCallcenterWidgetLoaded) return;
  window.__aiCallcenterWidgetLoaded = true;

  // 로더 스크립트 자신의 origin 을 추출 — 같은 도메인에서 /widget 을 띄움
  var currentScript = document.currentScript ||
    (function () {
      var scripts = document.getElementsByTagName("script");
      for (var i = scripts.length - 1; i >= 0; i--) {
        if (scripts[i].src && scripts[i].src.indexOf("widget/loader.js") !== -1) {
          return scripts[i];
        }
      }
      return null;
    })();

  if (!currentScript) {
    console.warn("[ai-callcenter] loader.js 의 origin 을 식별할 수 없습니다.");
    return;
  }

  var ORIGIN = new URL(currentScript.src).origin;
  var ds = currentScript.dataset || {};
  var color    = ds.color    || "#1d4ed8";
  var title    = ds.title    || "AI 상담";
  var greeting = ds.greeting || "무엇을 도와드릴까요?";
  var position = (ds.position || "right").toLowerCase() === "left" ? "left" : "right";

  // ===== 스타일 =====
  var css = "" +
    ".ai-cc-launcher{position:fixed;bottom:24px;"+position+":24px;z-index:2147483646;" +
      "width:56px;height:56px;border-radius:50%;background:"+color+";color:#fff;border:none;" +
      "cursor:pointer;box-shadow:0 6px 24px rgba(15,23,42,.25);display:flex;align-items:center;" +
      "justify-content:center;transition:transform .15s;}" +
    ".ai-cc-launcher:hover{transform:scale(1.06);}" +
    ".ai-cc-launcher svg{width:26px;height:26px;}" +
    ".ai-cc-launcher .dot{position:absolute;top:8px;right:8px;width:10px;height:10px;" +
      "background:#ef4444;border-radius:50%;border:2px solid "+color+";display:none;}" +
    ".ai-cc-frame{position:fixed;bottom:96px;"+position+":24px;z-index:2147483647;" +
      "width:380px;height:560px;max-width:calc(100vw - 48px);max-height:calc(100vh - 120px);" +
      "border-radius:14px;overflow:hidden;background:#fff;border:none;display:none;" +
      "box-shadow:0 24px 60px rgba(15,23,42,.35);}" +
    "@media(max-width:480px){" +
      ".ai-cc-frame{bottom:0;"+position+":0;width:100%;height:100%;max-width:100%;max-height:100%;border-radius:0;}" +
    "}";
  var style = document.createElement("style");
  style.setAttribute("data-ai-cc", "");
  style.appendChild(document.createTextNode(css));
  document.head.appendChild(style);

  // ===== 런처 버튼 =====
  var btn = document.createElement("button");
  btn.className = "ai-cc-launcher";
  btn.setAttribute("aria-label", title + " 열기");
  btn.innerHTML =
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" ' +
    'stroke-linecap="round" stroke-linejoin="round">' +
      '<path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"/>' +
    '</svg><span class="dot" id="ai-cc-dot"></span>';

  // ===== iframe =====
  var qs = new URLSearchParams({ color: color, title: title, greeting: greeting }).toString();
  var iframe = document.createElement("iframe");
  iframe.className = "ai-cc-frame";
  iframe.title = title;
  iframe.allow = "microphone";
  iframe.src = ORIGIN + "/widget?" + qs;

  var open = false;
  function setOpen(o) {
    open = o;
    iframe.style.display = o ? "block" : "none";
    document.getElementById("ai-cc-dot").style.display = "none";
  }
  btn.addEventListener("click", function () { setOpen(!open); });

  document.body.appendChild(btn);
  document.body.appendChild(iframe);

  // ===== 위젯 → 부모 메시지 =====
  window.addEventListener("message", function (e) {
    var data = e.data || {};
    if (data.source !== "ai-callcenter-widget") return;
    if (data.type === "close") setOpen(false);
    if (data.type === "agent_reply" && !open) {
      document.getElementById("ai-cc-dot").style.display = "block";
    }
    // 외부 사이트가 듣고 싶을 때 — window.aiCallcenter.onMessage 콜백 호출
    try {
      var cb = window.aiCallcenter && window.aiCallcenter.onMessage;
      if (typeof cb === "function") cb(data);
    } catch (_) {}
  });

  // 외부 사이트에서 제어할 수 있는 작은 API
  window.aiCallcenter = window.aiCallcenter || {};
  window.aiCallcenter.open  = function () { setOpen(true);  };
  window.aiCallcenter.close = function () { setOpen(false); };
  window.aiCallcenter.toggle = function () { setOpen(!open); };
})();
