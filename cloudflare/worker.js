// 812拉基醫院 Telegram 掛號櫃台（Cloudflare Workers 免費方案）
//
// Telegram 一收到訊息就直接送到這裡，不用等 GitHub 排程，幾秒內回覆。
// 診斷文字由 GitHub 每次自動更新時預先算好（docs/bot.json），這裡只負責查表回覆。
// 主治病人清單存在 Cloudflare KV（變數名稱 WATCH），每次自動更新會同步回網站。
//
// 需要的設定（Worker → Settings → Variables and Secrets）：
//   TELEGRAM_BOT_TOKEN  機器人的 token（Secret）
//   TELEGRAM_CHAT_ID    你的聊天室 ID（只回覆這個聊天室）
//   SETUP_KEY           自己取的密碼，只能用英文字母、數字、底線或減號
// 以及 KV 綁定：變數名稱 WATCH

const SITE = "https://n0930121.github.io/LajiHospital/";

const HELP = `🏥 812拉基醫院 掛號櫃台
傳股票代號或名稱（例如 2330 或 台積電），我會回你綜合診斷。

/watch 2330　加入我的主治病人
/unwatch 2330　移除主治病人
/list　查看我的主治病人
/report　今天的早晨交班報告

診斷只是觀察重點，不構成投資建議。`;

export default {
  async fetch(req, env, ctx) {
    const url = new URL(req.url);

    // 一次性設定：告訴 Telegram 之後把訊息送到這個網址
    if (url.pathname === "/setup") {
      if (url.searchParams.get("key") !== env.SETUP_KEY) return text("密碼不對，請確認網址最後的 key 和 SETUP_KEY 一樣。", 403);
      const r = await tg(env, "setWebhook", { url: `${url.origin}/hook`, secret_token: env.SETUP_KEY, allowed_updates: ["message"] });
      const j = await r.json();
      return text(j.ok ? "✅ 設定完成！現在傳訊息給機器人，幾秒內就會回覆。" : `❌ 設定失敗：${j.description || JSON.stringify(j)}`);
    }

    // GitHub 每次更新時來拿主治病人清單
    if (url.pathname === "/watch") {
      if (url.searchParams.get("key") !== env.SETUP_KEY) return text("forbidden", 403);
      return Response.json({ codes: await getWatch(env) });
    }

    // Telegram 送來的訊息
    if (url.pathname === "/hook" && req.method === "POST") {
      if (req.headers.get("X-Telegram-Bot-Api-Secret-Token") !== env.SETUP_KEY) return text("forbidden", 403);
      const update = await req.json();
      ctx.waitUntil(handle(update, env).catch(e => send(env, `處理時發生錯誤：${e.message}`)));
      return text("ok");
    }

    return text("812拉基醫院 掛號櫃台運作中");
  },
};

function text(s, status = 200) {
  return new Response(s, { status, headers: { "content-type": "text/plain; charset=utf-8" } });
}

function tg(env, method, body) {
  return fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/${method}`, {
    method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body),
  });
}

function send(env, s) {
  return tg(env, "sendMessage", { chat_id: env.TELEGRAM_CHAT_ID, text: s.slice(0, 4000), disable_web_page_preview: true });
}

// 預先算好的診斷，快取 5 分鐘
async function loadBot() {
  const r = await fetch(SITE + "bot.json", { cf: { cacheTtl: 300, cacheEverything: true } });
  if (!r.ok) throw new Error("讀不到診斷資料（bot.json），請等下一次自動更新後再試");
  return r.json();
}

async function getWatch(env) {
  const v = await env.WATCH.get("codes");
  if (v) return JSON.parse(v);
  // 第一次使用：從網站上原本的清單接手
  try {
    const r = await fetch(SITE + "watch.json");
    const codes = r.ok ? (await r.json()).codes || [] : [];
    await env.WATCH.put("codes", JSON.stringify(codes));
    return codes;
  } catch (e) {
    return [];
  }
}

function findCode(q, S) {
  q = q.trim();
  if (S[q]) return q;
  const all = Object.entries(S);
  const exact = all.find(([, v]) => v[0] === q);
  if (exact) return exact[0];
  const hits = all.filter(([, v]) => v[0].includes(q));
  return hits.length ? hits[0][0] : null;
}

async function handle(update, env) {
  const m = update.message || {};
  if (String(m.chat?.id) !== String(env.TELEGRAM_CHAT_ID)) return;   // 只回覆你自己的聊天室
  const msg = (m.text || "").trim();
  if (!msg) return;
  const sp = msg.indexOf(" ");
  const cmd = (sp < 0 ? msg : msg.slice(0, sp)).split("@")[0].toLowerCase();
  const arg = sp < 0 ? "" : msg.slice(sp + 1).trim();

  if (cmd === "/start" || cmd === "/help") return send(env, HELP);

  const B = await loadBot();
  const S = B.s || {};

  if (cmd === "/report") return send(env, B.report || "今天的交班報告還沒產生。");

  if (cmd === "/list") {
    const codes = await getWatch(env);
    if (!codes.length) return send(env, "目前還沒有主治病人。傳 /watch 2330 試試看。");
    return send(env, "【我的主治病人】\n" + codes.map(c => S[c]?.[2] || `⚪ ${c}`).join("\n") + `\n\n資料日 ${B.asof || ""}`);
  }

  if (cmd === "/watch" || cmd === "/unwatch") {
    const c = arg ? findCode(arg, S) : null;
    if (!c) return send(env, `找不到「${arg}」，請輸入股票代號或名稱，例如 ${cmd} 2330`);
    let codes = await getWatch(env);
    if (cmd === "/watch") {
      if (!codes.includes(c)) codes.push(c);
      await env.WATCH.put("codes", JSON.stringify(codes));
      return send(env, `✅ 已把 ${S[c][0]}（${c}）列為你的主治病人。每天收盤推播會附上它的診斷，出狀況時會即時通報。`);
    }
    codes = codes.filter(x => x !== c);
    await env.WATCH.put("codes", JSON.stringify(codes));
    return send(env, `已移除 ${S[c][0]}（${c}）。`);
  }

  const c = findCode(msg, S);
  return send(env, c ? S[c][1] : `掛號櫃台找不到「${msg}」。請傳股票代號（例如 2330）或名稱，或傳 /help 看說明。`);
}
