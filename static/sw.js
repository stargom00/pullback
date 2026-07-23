// v4.79: PWA 설치(홈 화면 추가) 요건 충족용 서비스워커.
// 스캐너는 항상 최신 데이터가 필요해서 오프라인 캐싱은 하지 않는다 —
// 그냥 네트워크로 그대로 통과시킨다.
// v4.92: /api/ 요청은 브라우저 HTTP 캐시까지 확실히 건너뛰도록 cache:'no-store'
// 명시. 서버가 Cache-Control 헤더를 안 보내던 시절 이 서비스워커가 등록된
// 이후로 API 응답이 캐시돼 서버를 고쳐도 화면이 안 바뀌는 것처럼 보이던
// 문제의 방어선(서버 쪽 헤더 수정과 별개로 이중 안전장치).
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (e) => e.waitUntil(self.clients.claim()));
self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url);
  if (url.pathname.startsWith('/api/')) {
    e.respondWith(fetch(e.request, { cache: 'no-store' }));
  } else {
    e.respondWith(fetch(e.request));
  }
});
