// v4.79: PWA 설치(홈 화면 추가) 요건 충족용 서비스워커.
// 스캐너는 항상 최신 데이터가 필요해서 오프라인 캐싱은 하지 않는다 —
// 그냥 네트워크로 그대로 통과시킨다.
// v4.92: /api/ 요청은 브라우저 HTTP 캐시까지 확실히 건너뛰도록 cache:'no-store'
// 명시. 서버가 Cache-Control 헤더를 안 보내던 시절 이 서비스워커가 등록된
// 이후로 API 응답이 캐시돼 서버를 고쳐도 화면이 안 바뀌는 것처럼 보이던
// 문제의 방어선(서버 쪽 헤더 수정과 별개로 이중 안전장치).
//
// v5.13 [버그수정] 메인 HTML(코드 자체)이 "강제 새로고침(Cmd+Shift+R)"으로도
// 안 바뀌는 문제. [원인] /api/가 아닌 요청(메인 문서 자체 포함)은 그냥
// fetch(e.request)만 했는데, 서비스워커의 fetch 핸들러를 거치는 요청은
// 브라우저의 '강제 새로고침으로 캐시 우회' 지시가 서비스워커 내부의 별도
// fetch 호출에는 안 이어질 수 있음 — 사용자가 하드 리프레시를 해도 이
// 서비스워커가 오래된 HTML/JS를 계속 서빙하는 것처럼 보이는 PWA의 잘 알려진
// 함정. [해결] 메인 문서(HTML)도 명시적으로 cache:'no-store'로 강제.
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (e) => e.waitUntil(self.clients.claim()));
self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url);
  if (e.request.mode === 'navigate' || url.pathname.startsWith('/api/') || url.pathname === '/') {
    e.respondWith(fetch(e.request, { cache: 'no-store' }));
  } else {
    e.respondWith(fetch(e.request));
  }
});
