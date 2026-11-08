// 睿深行 AI 工作台 · Service Worker（运行时缓存，无构建依赖）
// 策略：
//  - 导航请求：network-first，失败回退到缓存的 app shell（离线可用）
//  - 同源静态资源（JS/CSS/字体/图片）：cache-first + 后台刷新（stale-while-revalidate）
//  - API（/api、/healthz）与跨域请求：不缓存、直接透传（避免缓存对话/业务数据）
const CACHE = "rsh-pwa-v1";
const OFFLINE_URL = "/index.html";

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE).then((c) => c.add(OFFLINE_URL).catch(() => {})),
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))),
    ),
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;

  const url = new URL(req.url);
  // 跨域（如后端部署在别的域）不处理
  if (url.origin !== self.location.origin) return;
  // API 与探针不缓存
  if (url.pathname.startsWith("/api/") || url.pathname.startsWith("/healthz")) return;

  // SPA 导航：先网络，失败给离线壳
  if (req.mode === "navigate") {
    event.respondWith(
      fetch(req).catch(() => caches.match(OFFLINE_URL).then((r) => r || Response.error())),
    );
    return;
  }

  // 静态资源：缓存优先，命中则后台刷新
  event.respondWith(
    caches.match(req).then((hit) => {
      const network = fetch(req)
        .then((res) => {
          if (res && res.status === 200 && res.type === "basic") {
            const copy = res.clone();
            caches.open(CACHE).then((c) => c.put(req, copy));
          }
          return res;
        })
        .catch(() => hit);
      return hit || network;
    }),
  );
});
