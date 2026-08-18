// Service worker do PWA da equipe (MedIA) - só cuida de notificação push
// por enquanto (sem cache offline: a equipe sempre precisa dos dados mais
// recentes de pacientes/perguntas, então não faz sentido servir uma versão
// desatualizada da tela quando não há conexão).

self.addEventListener("install", (event) => {
  // Ativa o novo service worker assim que instalado, sem esperar todas as
  // abas antigas fecharem - importante porque a gente atualiza este
  // arquivo de vez em quando (ver Cache-Control: no-cache na rota /sw.js).
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener("push", (event) => {
  let dados = {};
  try {
    dados = event.data ? event.data.json() : {};
  } catch (erro) {
    dados = {};
  }

  const titulo = dados.title || "MedIA";
  const opcoes = {
    body: dados.body || "",
    icon: "/static/img/pwa/icon-192.png",
    badge: "/static/img/pwa/icon-192.png",
    data: { url: dados.url || "/equipe/perguntas" },
  };

  event.waitUntil(self.registration.showNotification(titulo, opcoes));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = (event.notification.data && event.notification.data.url) || "/equipe/perguntas";

  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((janelas) => {
      for (const janela of janelas) {
        if (janela.url.includes(url) && "focus" in janela) {
          return janela.focus();
        }
      }
      if (self.clients.openWindow) {
        return self.clients.openWindow(url);
      }
    })
  );
});
