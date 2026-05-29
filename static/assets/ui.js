// AI 콜센터 공통 UI 헬퍼.
//   UI.alert(msg, options)         — 확인 다이얼로그 (브라우저 alert 대체)
//   UI.confirm(msg, options)       — 예/아니오 다이얼로그 (브라우저 confirm 대체)
//   UI.prompt(msg, default, opts)  — 단일 입력 (브라우저 prompt 대체)
//   UI.form({ title, fields, ... })— 다중 입력 폼 (사용자 정보 수정 등)
//   UI.toast(msg, options)         — 토스트 알림
// 각 함수는 결과(true/false/string/object/null)를 Promise 로 반환한다.

(function () {
  const _ = (el) => (typeof el === "string"
    ? Object.assign(document.createElement(el.split(".")[0] || "div"), {
        className: el.includes(".") ? el.split(".").slice(1).join(" ") : "",
      })
    : el);

  function escapeHtml(text) {
    const d = document.createElement("div");
    d.textContent = text == null ? "" : String(text);
    return d.innerHTML;
  }

  // -----------------------------------------------------------------
  // 모달 코어
  // -----------------------------------------------------------------
  let _activeModal = null;

  function openModal({ title, description, body, footer, wide, onClose }) {
    closeModal(); // 중첩 방지
    const backdrop = document.createElement("div");
    backdrop.className = "modal-backdrop";
    backdrop.innerHTML =
      `<div class="modal${wide ? " modal-wide" : ""}" role="dialog" aria-modal="true">` +
      (title || description
        ? `<div class="modal-head">` +
            (title ? `<div class="title">${escapeHtml(title)}</div>` : "") +
            (description ? `<div class="desc">${escapeHtml(description)}</div>` : "") +
          `</div>`
        : "") +
      `<div class="modal-body"></div>` +
      `<div class="modal-foot"></div>` +
      `</div>`;
    document.body.appendChild(backdrop);
    const modalBody = backdrop.querySelector(".modal-body");
    const modalFoot = backdrop.querySelector(".modal-foot");
    if (body) {
      if (typeof body === "string") modalBody.innerHTML = body;
      else modalBody.appendChild(body);
    }
    if (footer) {
      if (typeof footer === "string") modalFoot.innerHTML = footer;
      else modalFoot.appendChild(footer);
    } else {
      modalFoot.remove();
    }

    requestAnimationFrame(() => backdrop.classList.add("open"));

    const escHandler = (e) => { if (e.key === "Escape") closeModal(); };
    document.addEventListener("keydown", escHandler);

    backdrop.addEventListener("click", (e) => {
      if (e.target === backdrop) closeModal();
    });

    _activeModal = { backdrop, escHandler, onClose };
    return { backdrop, body: modalBody, foot: modalFoot };
  }

  function closeModal() {
    if (!_activeModal) return;
    const { backdrop, escHandler, onClose } = _activeModal;
    backdrop.classList.remove("open");
    document.removeEventListener("keydown", escHandler);
    _activeModal = null;
    setTimeout(() => backdrop.remove(), 200);
    if (onClose) onClose();
  }

  // -----------------------------------------------------------------
  // alert
  // -----------------------------------------------------------------
  function alertDialog(message, { title = "안내", okText = "확인", type } = {}) {
    return new Promise((resolve) => {
      const okBtn = document.createElement("button");
      okBtn.className = "btn btn-primary";
      okBtn.textContent = okText;
      const { backdrop } = openModal({
        title,
        description: message,
        footer: okBtn,
        onClose: () => resolve(true),
      });
      okBtn.addEventListener("click", () => closeModal());
      if (type === "danger") okBtn.className = "btn btn-danger";
      setTimeout(() => okBtn.focus(), 50);
    });
  }

  // -----------------------------------------------------------------
  // confirm
  // -----------------------------------------------------------------
  function confirmDialog(message, {
    title = "확인",
    okText = "확인",
    cancelText = "취소",
    danger = false,
  } = {}) {
    return new Promise((resolve) => {
      const cancel = document.createElement("button");
      cancel.className = "btn btn-secondary";
      cancel.textContent = cancelText;
      const ok = document.createElement("button");
      ok.className = danger ? "btn btn-danger" : "btn btn-primary";
      if (danger) {
        ok.style.background = "var(--c-danger)";
        ok.style.color = "#fff";
        ok.style.borderColor = "var(--c-danger)";
      }
      ok.textContent = okText;
      const foot = document.createDocumentFragment();
      foot.appendChild(cancel);
      foot.appendChild(ok);
      let resolved = false;
      openModal({
        title,
        description: message,
        footer: foot,
        onClose: () => { if (!resolved) { resolved = true; resolve(false); } },
      });
      cancel.addEventListener("click", () => { resolved = true; resolve(false); closeModal(); });
      ok.addEventListener("click", () => { resolved = true; resolve(true); closeModal(); });
      setTimeout(() => ok.focus(), 50);
    });
  }

  // -----------------------------------------------------------------
  // form (멀티 입력 다이얼로그)
  //   fields: [{ name, label, type?, value?, placeholder?, required?, minLength?,
  //              rows? (textarea), options?[] (select) }]
  // -----------------------------------------------------------------
  function formDialog({
    title,
    description,
    fields,
    okText = "저장",
    cancelText = "취소",
    wide = false,
  }) {
    return new Promise((resolve) => {
      const container = document.createElement("div");
      const inputs = {};
      fields.forEach((f) => {
        const wrap = document.createElement("div");
        wrap.className = "field";
        const label = document.createElement("label");
        label.className = "label";
        label.textContent = f.label + (f.required ? " *" : "");
        let input;
        if (f.type === "textarea") {
          input = document.createElement("textarea");
          input.className = "textarea";
          input.rows = f.rows || 3;
        } else if (f.type === "select") {
          input = document.createElement("select");
          input.className = "select";
          (f.options || []).forEach((opt) => {
            const o = document.createElement("option");
            o.value = opt.value;
            o.textContent = opt.label;
            if (opt.value === f.value) o.selected = true;
            input.appendChild(o);
          });
        } else {
          input = document.createElement("input");
          input.className = "input";
          input.type = f.type || "text";
        }
        input.name = f.name;
        if (f.placeholder) input.placeholder = f.placeholder;
        if (f.value != null && f.type !== "select") input.value = f.value;
        if (f.minLength) input.minLength = f.minLength;
        if (f.maxLength) input.maxLength = f.maxLength;
        if (f.autocomplete) input.autocomplete = f.autocomplete;
        const errEl = document.createElement("div");
        errEl.className = "field-error";
        wrap.append(label, input, errEl);
        container.appendChild(wrap);
        inputs[f.name] = { input, errEl, def: f };
      });

      const cancel = document.createElement("button");
      cancel.className = "btn btn-secondary";
      cancel.textContent = cancelText;
      const ok = document.createElement("button");
      ok.className = "btn btn-primary";
      ok.textContent = okText;
      const foot = document.createDocumentFragment();
      foot.appendChild(cancel);
      foot.appendChild(ok);

      let resolved = false;
      openModal({
        title,
        description,
        body: container,
        footer: foot,
        wide,
        onClose: () => { if (!resolved) { resolved = true; resolve(null); } },
      });

      const collect = () => {
        let valid = true;
        const result = {};
        Object.entries(inputs).forEach(([name, { input, errEl, def }]) => {
          const val = input.value;
          errEl.textContent = "";
          if (def.required && !val.trim()) {
            errEl.textContent = "필수 항목입니다.";
            valid = false;
          } else if (def.minLength && val.length && val.length < def.minLength) {
            errEl.textContent = `${def.minLength}자 이상이어야 합니다.`;
            valid = false;
          }
          result[name] = val;
        });
        return valid ? result : null;
      };

      cancel.addEventListener("click", () => { resolved = true; resolve(null); closeModal(); });
      ok.addEventListener("click", () => {
        const data = collect();
        if (!data) return;
        resolved = true;
        resolve(data);
        closeModal();
      });
      container.addEventListener("keydown", (e) => {
        if (e.key === "Enter" && e.target.tagName !== "TEXTAREA") {
          e.preventDefault();
          ok.click();
        }
      });
      setTimeout(() => {
        const first = Object.values(inputs)[0];
        if (first) first.input.focus();
      }, 50);
    });
  }

  // -----------------------------------------------------------------
  // prompt (단일 입력 — form 의 단축)
  // -----------------------------------------------------------------
  function promptDialog(message, defaultValue = "", {
    title = "입력",
    okText = "확인",
    cancelText = "취소",
    placeholder,
    type = "text",
    minLength,
  } = {}) {
    return formDialog({
      title,
      description: message,
      fields: [{ name: "value", label: "", type, value: defaultValue,
                  placeholder, minLength, required: true }],
      okText,
      cancelText,
    }).then((r) => (r ? r.value : null));
  }

  // -----------------------------------------------------------------
  // 토스트
  // -----------------------------------------------------------------
  function ensureToastStack() {
    let stack = document.querySelector(".toast-stack");
    if (!stack) {
      stack = document.createElement("div");
      stack.className = "toast-stack";
      document.body.appendChild(stack);
    }
    return stack;
  }

  function toast(message, { type = "default", duration = 2400 } = {}) {
    const stack = ensureToastStack();
    const el = document.createElement("div");
    el.className = "toast" + (type !== "default" ? " " + type : "");
    el.textContent = message;
    stack.appendChild(el);
    requestAnimationFrame(() => el.classList.add("show"));
    setTimeout(() => {
      el.classList.remove("show");
      setTimeout(() => el.remove(), 250);
    }, duration);
  }

  window.UI = {
    alert: alertDialog,
    confirm: confirmDialog,
    prompt: promptDialog,
    form: formDialog,
    toast,
    closeModal,
    escapeHtml,
  };
})();
