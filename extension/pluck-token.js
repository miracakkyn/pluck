"use strict";
/* Pluck — paylaşımlı yerel API jetonu (eklenti tarafı).

   Bu SABİT, motorun config.api_token() varsayılanıyla (_DEFAULT_API_TOKEN)
   ve web/app.js'teki PLUCK_TOKEN ile BİREBİR eşleşmelidir. Amaç: Pluck'ın kendi
   istemcilerini rastgele diğer yerel eklenti/yazılımdan ayırmak — onlar bu
   başlığı göndermediği için motor 403 döner. Gömülü bir paylaşımlı sırdır;
   hedefli saldırıya karşı değildir. Motorda PLUCK_TOKEN ortam değişkeni
   ayarlanırsa buradaki değeri de eşleştirin.

   background.js (service worker) importScripts ile, popup.html ise <script>
   etiketiyle bu dosyayı yükler; ikisi de PLUCK_TOKEN/pluckHeaders'a erişir. */
const PLUCK_TOKEN = "pluck-local-v1-a3f19c7e";

function pluckHeaders() {
  return { "X-Pluck-Token": PLUCK_TOKEN };
}
