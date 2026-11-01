// "Enable notifications on this device" for any [data-push] block.
// iPhone: only works in the home-screen app (iOS 16.4+), and permission must be
// requested from a tap. All text goes in via textContent.
(function () {
  const box = document.querySelector("[data-push]");
  if (!box) return;
  const $ = (sel) => box.querySelector(sel);
  const status = $("[data-push-status]");
  const btn = { enable: $("[data-push-enable]"), test: $("[data-push-test]"), disable: $("[data-push-disable]") };

  function show(text, on) {
    status.textContent = text;
    btn.enable.hidden = on !== false;
    btn.test.hidden = btn.disable.hidden = on !== true;
  }
  const post = (url, body) => fetch(url, { method: "POST", headers: { "Content-Type": "application/json" },
                                         body: JSON.stringify(body || {}) });
  function keyBytes(b64) {
    const pad = "=".repeat((4 - (b64.length % 4)) % 4);
    const raw = atob((b64 + pad).replace(/-/g, "+").replace(/_/g, "/"));
    return Uint8Array.from(raw, (c) => c.charCodeAt(0));
  }

  const ios = /iPhone|iPad|iPod/.test(navigator.userAgent);
  const standalone = window.matchMedia("(display-mode: standalone)").matches || navigator.standalone;
  if (!("serviceWorker" in navigator) || !("PushManager" in window) || !("Notification" in window)) {
    show(ios && !standalone
      ? "To get reminders on iPhone, open this app from its Home Screen icon (Safari → Share → Add to Home Screen), then come back here."
      : "This browser can't show notifications.");
    btn.enable.hidden = btn.test.hidden = btn.disable.hidden = true;
    return;
  }

  async function refresh() {
    if (Notification.permission === "denied") {
      show(ios ? "Notifications are blocked for this app. Allow them in iPhone Settings → Notifications → My Apps."
               : "Notifications are blocked for this site. Allow them in your browser's site settings.");
      btn.enable.hidden = true;
      return;
    }
    const reg = await navigator.serviceWorker.ready;
    const sub = await reg.pushManager.getSubscription();
    if (sub) {
      // re-send in case the server lost it (e.g. different account on this device)
      post("/api/push/subscribe", sub.toJSON());
      show("Notifications are on for this device.", true);
    } else {
      show("Notifications are off on this device. Reminders won't reach it until you enable them.", false);
    }
  }

  btn.enable.addEventListener("click", async () => {
    btn.enable.disabled = true;
    try {
      if ((await Notification.requestPermission()) !== "granted") return refresh();
      const { key } = await (await fetch("/api/push/key")).json();
      const reg = await navigator.serviceWorker.ready;
      const sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: keyBytes(key) });
      const res = await post("/api/push/subscribe", sub.toJSON());
      if (!res.ok) throw new Error("save failed");
      await refresh();
    } catch (e) {
      show("Couldn't enable notifications. Please try again.", false);
    } finally {
      btn.enable.disabled = false;
    }
  });

  btn.test.addEventListener("click", async () => {
    btn.test.disabled = true;
    try {
      const { sent } = await (await post("/api/push/test")).json();
      status.textContent = sent ? "Test sent — it should appear in a few seconds."
                                : "No device accepted the test. Try turning notifications off and on.";
    } finally {
      btn.test.disabled = false;
    }
  });

  btn.disable.addEventListener("click", async () => {
    const reg = await navigator.serviceWorker.ready;
    const sub = await reg.pushManager.getSubscription();
    if (sub) {
      await post("/api/push/unsubscribe", { endpoint: sub.endpoint });
      await sub.unsubscribe();
    }
    refresh();
  });

  refresh();
})();
