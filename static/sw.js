// v4.79: PWA 설치(홈 화면 추가) 요건 충족용 서비스워커.
// 스캐너는 항상 최신 데이터가 필요해서 오프라인 캐싱은 하지 않는다 —
// 그냥 네트워크로 그대로 통과시킨다.
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (e) => e.waitUntil(self.clients.claim()));
self.addEventListener('fetch', (e) => {
  e.respondWith(fetch(e.request));
});
