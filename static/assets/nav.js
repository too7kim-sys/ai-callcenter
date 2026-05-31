// 공통 상단 메뉴 + 인증 가드. 모든 인증 화면에 포함되어:
//  1) /api/auth/me 로 로그인 확인 → 미인증 시 /login 으로 리다이렉트
//  2) 사용자 권한에 따라 메뉴 노출 (admin 전용 항목)
//  3) window.appUser 에 사용자 정보 저장 (페이지 코드에서 활용 가능)

(function () {
  const NAV_ITEMS = [
    { key: "dashboard",   href: "/dashboard",   label: "대시보드",    perm: "conversation.view" },
    { key: "agent",       href: "/agent",       label: "상담 콘솔",  perm: "conversation.view" },
    { key: "faq",         href: "/faq",         label: "FAQ",         perm: "faq.view" },
    { key: "knowledge",   href: "/knowledge",   label: "학습 데이터", perm: "knowledge.view" },
    { key: "templates",   href: "/templates",   label: "답변 템플릿", perm: "conversation.reply" },
    { key: "users",       href: "/users",       label: "사용자 관리", perm: "user.manage" },
    { key: "permissions", href: "/permissions", label: "권한 관리",   perm: "permission.manage" },
    { key: "audit",       href: "/audit",       label: "변경 이력",   perm: "audit.view" },
  ];

  function escapeHtml(text) {
    const d = document.createElement("div");
    d.textContent = text == null ? "" : String(text);
    return d.innerHTML;
  }

  function activeKey() {
    const path = location.pathname;
    return NAV_ITEMS.find((i) => path === i.href || path.startsWith(i.href + "/"))?.key;
  }

  async function init() {
    const slot = document.getElementById("app-nav");
    if (!slot) return;

    // 사용자 정보 + 권한 조회 (/api/auth/me 가 permissions 배열을 반환)
    let user = null;
    let perms = new Set();
    try {
      const meRes = await fetch("/api/auth/me");
      if (!meRes.ok) throw new Error("unauth");
      user = await meRes.json();
      (user.permissions || []).forEach((p) => perms.add(p));
    } catch (e) {
      const next = encodeURIComponent(location.pathname + location.search);
      location.href = "/login?next=" + next;
      return;
    }

    window.appUser = user;
    window.appPermissions = perms;

    const cur = activeKey();
    const items = NAV_ITEMS
      .filter((i) => perms.has(i.perm))
      .map((i) => {
        const active = i.key === cur ? " active" : "";
        return `<a class="item${active}" href="${i.href}">${escapeHtml(i.label)}</a>`;
      })
      .join("");

    const roleLabel = user.role === "admin" ? "관리자" : escapeHtml(user.role);
    slot.className = "app-nav";
    slot.innerHTML =
      `<a class="brand" href="/agent">AI 콜센터</a>` +
      `<div class="items">${items}</div>` +
      `<div class="user">` +
      `<span class="me">${escapeHtml(user.name || user.username)}` +
      `<span class="role">${roleLabel}</span></span>` +
      `<a class="logout" href="#" id="app-logout">로그아웃</a>` +
      `</div>`;

    document.getElementById("app-logout").addEventListener("click", async (e) => {
      e.preventDefault();
      try { await fetch("/api/auth/logout", { method: "POST" }); } catch (err) {}
      location.href = "/login";
    });

    document.dispatchEvent(new CustomEvent("auth-ready", { detail: user }));
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
