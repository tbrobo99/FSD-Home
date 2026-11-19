"use strict";

/* FSD-Home front end — v3 "soft modern" (Figma: ★ v3 · Screens).

   Four tabs, each one phone screen: Home (how the house is, in one sentence),
   Devices, Activity (the append-only audit trail, FR-6) and Settings. Talking
   is the button in the middle of the tab bar. A Level 2 question opens a
   bottom sheet by itself: the reason once, then one card per action with its
   own Yes / No (FR-3/FR-4). Silence changes nothing. */

const $ = (sel) => document.querySelector(sel);

const ICON = {
  air_conditioner: "i-ac",
  air_purifier: "i-purifier",
  washer: "i-washer",
  dryer: "i-dryer",
  dish_washer: "i-washer",
  refrigerator: "i-fridge",
  water_heater: "i-fridge",
  dehumidifier: "i-dehumid",
  range_hood: "i-hood",
  oven: "i-oven",
  cooktop: "i-cooktop",
};

const BACKEND_LABEL = { mock: "Simulator", thinq: "LG ThinQ" };
const MODE_LABEL = { cool: "Cool", heat: "Heat", dry: "Dry", fan: "Fan" };
const MODES = ["cool", "heat", "dry", "fan"];
const ROOM_ORDER = ["Living Room", "Kitchen", "Bedroom", "Balcony"];
const ROOM_SHORT = { "Living Room": "Living", Kitchen: "Kitchen", Bedroom: "Bedroom", Balcony: "Balcony" };

let snapshot = null;   // /api/snapshot: devices, jobs, mode
let pro = null;        // /api/proactive: live risks, approvals, sensor state
let auditRows = null;  // /api/audit: the FR-6 trail
let activeRoom = "All";
let actFilter = "all";
let currentTab = "home";
let sheetId = null;
let chipsSig = "";
let askSig = "";            // ids of the pending questions shown in the sheet
let askDismissed = "";      // the user closed the sheet for this set of questions
let askBusy = false;
let spokenAsks = "";

/* ---------- helpers ---------- */

function esc(value) {
  return String(value == null ? "" : value).replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[ch]));
}

async function api(path, opts) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...(opts || {}),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

function findDevice(id) {
  return snapshot ? (snapshot.devices || []).find((d) => d.id === id) : null;
}

function fmtWhen(iso) {
  if (!iso) return "—";
  const when = new Date(iso);
  if (isNaN(when)) return iso;
  const now = new Date();
  const time = when.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  if (when.toDateString() === now.toDateString()) return `today ${time}`;
  const tomorrow = new Date(now);
  tomorrow.setDate(now.getDate() + 1);
  if (when.toDateString() === tomorrow.toDateString()) return `tomorrow ${time}`;
  return `${when.toLocaleDateString([], { month: "short", day: "numeric" })} ${time}`;
}

function cap(text) {
  const s = String(text == null ? "" : text);
  return s ? s.charAt(0).toUpperCase() + s.slice(1) : s;
}

function repeatShort(label) {
  const s = String(label || "").trim();
  if (!/every/i.test(s)) return "once";
  return s.replace(/\s+at\s+\d{1,2}:\d{2}\s*$/, "").trim();
}

function icon(id, cls = "ic") {
  return `<svg class="${cls}" aria-hidden="true"><use href="#${id}"/></svg>`;
}

function clock(sec) {
  const s = Math.max(0, Math.floor(sec));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

function hhmm(ts) {
  return String(ts || "").slice(11, 16);
}

/* Homie, the house mascot, drawn at status-icon size. Same shapes as the
   Figma "Homie (mascot)" component; one face per mood. */
const MASCOT = {
  calm: { body: "#7ccba8", face: '<circle cx="38" cy="56" r="4.2" fill="#16161c"/><circle cx="58" cy="56" r="4.2" fill="#16161c"/><path d="M41 65 Q48 72 55 65" stroke="#16161c" stroke-width="3.2" stroke-linecap="round" fill="none"/>' },
  busy: { body: "#efa27f", face: '<path d="M33 58 q5 -6 10 0 M53 58 q5 -6 10 0" stroke="#16161c" stroke-width="3.2" stroke-linecap="round" fill="none"/><path d="M42 66 Q48 71 54 66" stroke="#16161c" stroke-width="3.2" stroke-linecap="round" fill="none"/>' },
  ask: { body: "#e9c35b", face: '<circle cx="38" cy="56" r="4.2" fill="#16161c"/><circle cx="58" cy="56" r="4.2" fill="#16161c"/><circle cx="48" cy="68" r="3.6" stroke="#16161c" stroke-width="3" fill="none"/>' },
  watch: { body: "#ea94ad", face: '<circle cx="38" cy="56" r="5.6" fill="#fff" stroke="#16161c" stroke-width="2.4"/><circle cx="58" cy="56" r="5.6" fill="#fff" stroke="#16161c" stroke-width="2.4"/><circle cx="40.5" cy="56" r="2.6" fill="#16161c"/><circle cx="60.5" cy="56" r="2.6" fill="#16161c"/><path d="M43 68 h10" stroke="#16161c" stroke-width="3.2" stroke-linecap="round"/>' },
};

function mascot(mood) {
  const m = MASCOT[mood] || MASCOT.calm;
  return `<svg viewBox="0 0 96 96" aria-hidden="true">
    <use href="#m-house" fill="${m.body}" stroke="#16161c" stroke-width="3.4" stroke-linejoin="round"/>
    ${m.face}</svg>`;
}

/* ---------- icons for questions and risks (words live in I18N) ---------- */

const QUESTION = {
  pause_washer: { icon: "i-washer", tone: "" },
  stop_dryer: { icon: "i-dryer", tone: "" },
  preheat_cooktop: { icon: "i-cooktop", tone: "pink" },
};

const RISK = {
  mold: { key: "risk.washer", icon: "i-washer" },
  thermal: { key: "risk.kitchen", icon: "i-flame" },
  heat_health: { key: "risk.heat", icon: "i-temp" },
  energy: { key: "risk.power", icon: "i-bolt" },
};
const RISK_ORDER = ["mold", "thermal", "heat_health", "energy"];

/* ---------- language (KO / EN) ----------
   The 한국어 | EN toggle switches the interface text, speech recognition and
   read-aloud together. The house's own replies (chat answers, engine
   messages) come from the server in English. Copy matches the Figma
   ★ v3 · Screens rows "EN" and "KO". */

const I18N = {
  en: {
    "home.title": "My Home", "home.check": "Home check", "home.acts": "acts at 50", "home.actsTitle": "The house steps in when a score reaches 50",
    "tab.home": "Home", "tab.devices": "Devices", "tab.activity": "Activity", "tab.settings": "Settings", "tab.talk": "Talk",
    "talk.aria": "Talk to the house", "talk.title": "Talk to the house", "talk.tap": "Tap to talk", "talk.listening": "Listening — pause when you're done",
    "talk.placeholder": "Or type here…", "talk.listeningPh": "Listening…", "talk.send": "Send", "close": "Close",
    "dev.title": "Devices", "dev.count": "{n} devices · {on} on", "dev.all": "All", "dev.empty": "Nothing in this room.",
    "dev.temp": "Temperature", "dev.hum": "Humidity", "dev.on": "On", "dev.devicesOn": "Devices on", "dev.heat": "Heat load", "dev.rooms": "Rooms",
    "dev.details": "{name} details", "dev.cooler": "Cooler", "dev.warmer": "Warmer", "dev.turnOn": "Turn on", "dev.turnOff": "Turn off",
    "act.eyebrow": "What the house did, and why", "act.title": "Activity", "act.locked": "Can't be edited", "act.all": "All", "act.asked": "Asked you", "act.done": "Done",
    "act.today": "Today", "act.empty": "Nothing recorded yet.",
    "set.eyebrow": "Voice, schedules and demo", "set.title": "Settings", "set.tts": "Read replies aloud", "set.ttsSub": "Questions are always read",
    "set.mode": "How it decides", "set.modeSub": "Rules first, or the agent", "set.cascade": "Cascade", "set.agent": "Agent",
    "set.schedule": "Schedule", "set.none": "Nothing scheduled", "set.noneSub": "Ask the house to set something", "set.demo": "Demo", "set.demoTag": "for presentations",
    "set.calm": "Calm house", "set.wet": "Wet washer", "set.dinner": "Dryer + dinner soon", "set.oven": "Dryer + oven on", "set.del": "Delete task",
    "hero.calm": "All cozy at home", "hero.calmSub": "Nothing needs you right now.",
    "hero.ask1": "Can I ask you something?", "hero.askN": "Can I ask you {n} things?", "hero.askSub": "It only takes a tap.", "hero.answer": "Answer",
    "hero.watch": "Watching the cooktop", "hero.cooking": "Cooking in progress.", "hero.watchMeal": "Turns off by itself if no cooking starts. Dinner in {m} min.",
    "hero.watchIdle": "Turns off in {m} min if no cooking starts.",
    "hero.mold": "Airing the washer for you", "hero.moldSub": "Drum vent, dehumidifier and purifier are on.",
    "hero.heat": "Cooling the kitchen down", "hero.heatSub": "Range hood and kitchen AC are on.",
    "risk.washer": "Washer", "risk.kitchen": "Kitchen", "risk.heat": "Room heat", "risk.power": "Power",
    "risk.feels": "{room} feels {t}°", "risk.away": "Nobody home", "risk.cool": "{room} {t}° · cool", "risk.rate.peak": "peak rate", "risk.rate.offpeak": "normal rate",
    "hero.hot": "Cooling the {room} for you", "hero.hotSub": "It feels like {t}° — AC set gently to 26°.",
    "hero.power": "Saving power at the peak rate", "hero.powerSub": "{kw} kW now — an AC was raised 2°.",
    "ask.energy": "Power is at the peak rate", "fact.peak": "Peak rate", "fact.load": "{kw} kW now", "fact.acs": "{n} ACs cooling",
    "q.pause_washer": "Pause the wash until the peak ends?", "c.pause_washer": "You restart it yourself later.",
    "act.roomHot": "Room getting hot", "act.powerHigh": "Power use high",
    "set.hot": "Hot night", "set.peak": "Peak rate", "risk.high": "On it", "risk.watch": "Watching", "risk.low": "All good",
    "risk.open": "Washer is open or empty", "risk.humid": "{h}% humid", "risk.shut": "shut {n} h", "risk.sealed": "sealed", "risk.nothingHot": "Nothing hot",
    "risk.quiet": "Quiet for {m} min", "risk.aria": "{name} risk",
    "fact.drum": "Drum {h}% humid", "fact.shut": "Shut {t}", "fact.kw": "{kw} kW running", "fact.dinner": "Dinner in {m} min",
    "heat.title": "Heat load", "heat.aria": "Heat-producing load", "heat.running": "Running: {list}",
    "app.oven": "oven", "app.cooktop": "cooktop", "app.dryer": "dryer", "app.dishwasher": "dishwasher",
    "quick.off": "All off", "quick.cool": "Cool 24°", "quick.fresh": "Fresh air",
    "recent.title": "Just now", "recent.cooled": "Kitchen cooled down — you can restart the dryer",
    "ask.mold": "The washer is getting damp", "ask.heat": "The kitchen is getting hot", "ask.one": "1 quick question", "ask.n": "{n} quick questions",
    "ask.left": "{t} left", "ask.up": "Time is up", "ask.rule": "No answer? Nothing changes.", "ask.yes": "Yes", "ask.no": "No",
    "ask.speakOne": "One question.", "ask.speakN": "{n} questions.",
    "q.stop_dryer": "Stop the dryer now?", "c.stop_dryer": "You restart it yourself later.",
    "q.preheat_cooktop": "Preheat the cooktop?", "c.preheat_cooktop": "Turns itself off if no cooking starts.",
    "toast.late": "Too late — nothing was changed.", "toast.closed": "That question had already closed.", "toast.done": "Done.", "toast.no": "Okay — nothing changed.",
    "toast.unreach": "Could not reach the house: {e}", "toast.working": "Working on it…", "toast.scenario": "Scenario loaded.", "toast.scenarioFail": "Could not load the scenario: {e}",
    "toast.ttsOn": "Reading replies aloud", "toast.ttsOff": "Reading aloud off", "toast.lang": "English", "toast.noResponse": "The device did not respond.", "toast.ctrl": "Control failed: {e}",
    "state.off": "Off", "state.on": "On", "state.washing": "Washing · {m} min", "state.finished": "Cycle finished", "state.standby": "Standby", "state.fan": "Fan {f}", "state.heating": "Heating · {t}",
    "mode.cool": "Cool", "mode.heat": "Heat", "mode.dry": "Dry", "mode.fan": "Fan",
    "act.washerDamp": "Washer getting damp", "act.kitchenHot": "Kitchen getting hot", "act.score": "Score {s} · acts at 50",
    "act.askedYou": "Asked you: {a}", "act.yes": "You said yes: {a}", "act.no": "You said no: {a}", "act.noAnswer": "No answer: {a}", "act.nothingChanged": "Nothing changed",
    "act.saidYes": "You said yes", "act.safety": "Safety rule", "act.own": "Did it on its own", "act.fromApp": "From the app", "act.doneX": "Done: {a}",
    "act.noRespond": "{a} didn't respond", "act.blocked": "Didn't go ahead: {a}", "act.cooled": "Kitchen cooled down", "act.restart": "You can restart the dryer",
    "act.demo": "Demo scenario loaded", "act.request": "request {id}",
    "job.next": "next {t}", "job.disabled": "disabled", "job.once": "once",
  },
  ko: {
    "home.title": "우리 집", "home.check": "집 상태", "home.acts": "50점부터 작동", "home.actsTitle": "점수가 50이 되면 집이 직접 움직여요",
    "tab.home": "홈", "tab.devices": "기기", "tab.activity": "활동", "tab.settings": "설정", "tab.talk": "말하기",
    "talk.aria": "집에게 말하기", "talk.title": "집에게 말하기", "talk.tap": "눌러서 말하기", "talk.listening": "듣고 있어요 — 다 말하면 잠시 멈추세요",
    "talk.placeholder": "또는 여기에 입력…", "talk.listeningPh": "듣는 중…", "talk.send": "보내기", "close": "닫기",
    "dev.title": "기기", "dev.count": "기기 {n}개 · {on}개 켜짐", "dev.all": "전체", "dev.empty": "이 방에는 기기가 없어요.",
    "dev.temp": "온도", "dev.hum": "습도", "dev.on": "켜짐", "dev.devicesOn": "켜진 기기", "dev.heat": "열 부하", "dev.rooms": "방",
    "dev.details": "{name} 자세히", "dev.cooler": "온도 내리기", "dev.warmer": "온도 올리기", "dev.turnOn": "켜기", "dev.turnOff": "끄기",
    "act.eyebrow": "집이 한 일과 그 이유", "act.title": "활동", "act.locked": "수정 불가", "act.all": "전체", "act.asked": "물어본 것", "act.done": "한 일",
    "act.today": "오늘", "act.empty": "아직 기록이 없어요.",
    "set.eyebrow": "음성, 예약, 데모", "set.title": "설정", "set.tts": "답변 소리 내어 읽기", "set.ttsSub": "질문은 항상 읽어 드려요",
    "set.mode": "판단 방식", "set.modeSub": "규칙 또는 에이전트", "set.cascade": "규칙 우선", "set.agent": "에이전트",
    "set.schedule": "예약", "set.none": "예약 없음", "set.noneSub": "집에게 예약을 부탁해 보세요", "set.demo": "데모", "set.demoTag": "발표용",
    "set.calm": "평온한 집", "set.wet": "젖은 세탁기", "set.dinner": "건조기 + 곧 저녁", "set.oven": "건조기 + 오븐", "set.del": "예약 삭제",
    "hero.calm": "집이 편안해요", "hero.calmSub": "지금은 신경 쓸 일이 없어요.",
    "hero.ask1": "하나 여쭤봐도 될까요?", "hero.askN": "{n}가지 여쭤봐도 될까요?", "hero.askSub": "한 번만 눌러 주시면 돼요.", "hero.answer": "답하기",
    "hero.watch": "쿡탑을 지켜보는 중", "hero.cooking": "요리하는 중이에요.", "hero.watchMeal": "요리를 시작하지 않으면 저절로 꺼져요. {m}분 후 저녁.",
    "hero.watchIdle": "요리를 시작하지 않으면 {m}분 뒤 꺼져요.",
    "hero.mold": "세탁기를 말리는 중이에요", "hero.moldSub": "통 환기, 제습기, 공기청정기를 켰어요.",
    "hero.heat": "주방을 식히는 중이에요", "hero.heatSub": "레인지 후드와 주방 에어컨을 켰어요.",
    "risk.washer": "세탁기", "risk.kitchen": "주방", "risk.heat": "실내 더위", "risk.power": "전기",
    "risk.feels": "{room} 체감 {t}°", "risk.away": "집에 아무도 없음", "risk.cool": "{room} {t}° · 쾌적", "risk.rate.peak": "피크 요금", "risk.rate.offpeak": "일반 요금",
    "hero.hot": "{room}을 시원하게 하는 중이에요", "hero.hotSub": "체감 {t}°예요 — 에어컨을 26°로 약하게 켰어요.",
    "hero.power": "피크 요금이라 전기를 아끼는 중", "hero.powerSub": "지금 {kw} kW — 에어컨 온도를 2° 올렸어요.",
    "ask.energy": "지금은 피크 요금 시간이에요", "fact.peak": "피크 요금", "fact.load": "지금 {kw} kW", "fact.acs": "에어컨 {n}대 냉방 중",
    "q.pause_washer": "피크가 끝날 때까지 세탁을 멈출까요?", "c.pause_washer": "나중에 직접 다시 켜 주세요.",
    "act.roomHot": "실내가 더워짐", "act.powerHigh": "전력 사용이 높음",
    "set.hot": "더운 밤", "set.peak": "피크 요금", "risk.high": "처리 중", "risk.watch": "지켜보는 중", "risk.low": "좋아요",
    "risk.open": "문 열림", "risk.humid": "습도 {h}%", "risk.shut": "{n}시간 닫힘", "risk.sealed": "문 닫힘", "risk.nothingHot": "이상 없음",
    "risk.quiet": "{m}분간 조용히", "risk.aria": "{name} 위험도",
    "fact.drum": "통 습도 {h}%", "fact.shut": "{t} 닫힘", "fact.kw": "{kw} kW 사용 중", "fact.dinner": "{m}분 후 저녁",
    "heat.title": "열 부하", "heat.aria": "열을 내는 기기 전력", "heat.running": "작동 중: {list}",
    "app.oven": "오븐", "app.cooktop": "쿡탑", "app.dryer": "건조기", "app.dishwasher": "식기세척기",
    "quick.off": "모두 끄기", "quick.cool": "24° 냉방", "quick.fresh": "환기",
    "recent.title": "방금", "recent.cooled": "주방이 식었어요 — 건조기를 다시 켜도 돼요",
    "ask.mold": "세탁기가 눅눅해지고 있어요", "ask.heat": "주방이 더워지고 있어요", "ask.one": "질문 1개", "ask.n": "질문 {n}개",
    "ask.left": "{t} 남음", "ask.up": "시간이 지났어요", "ask.rule": "답하지 않으면 아무것도 바뀌지 않아요.", "ask.yes": "네", "ask.no": "아니요",
    "ask.speakOne": "질문이 하나 있어요.", "ask.speakN": "질문이 {n}개 있어요.",
    "q.stop_dryer": "지금 건조기를 끌까요?", "c.stop_dryer": "나중에 직접 다시 켜 주세요.",
    "q.preheat_cooktop": "쿡탑을 미리 데울까요?", "c.preheat_cooktop": "요리를 시작하지 않으면 저절로 꺼져요.",
    "toast.late": "시간이 지나서 아무것도 바꾸지 않았어요.", "toast.closed": "이미 끝난 질문이에요.", "toast.done": "완료했어요.", "toast.no": "알겠어요 — 그대로 둘게요.",
    "toast.unreach": "집에 연결할 수 없어요: {e}", "toast.working": "처리 중이에요…", "toast.scenario": "시나리오를 불러왔어요.", "toast.scenarioFail": "시나리오를 불러올 수 없어요: {e}",
    "toast.ttsOn": "답변을 소리 내어 읽어요", "toast.ttsOff": "소리 내어 읽기 끔", "toast.lang": "한국어", "toast.noResponse": "기기가 응답하지 않아요.", "toast.ctrl": "제어 실패: {e}",
    "state.off": "꺼짐", "state.on": "켜짐", "state.washing": "세탁 중 · {m}분", "state.finished": "세탁 끝남", "state.standby": "대기", "state.fan": "바람 {f}", "state.heating": "가열 중 · {t}",
    "mode.cool": "냉방", "mode.heat": "난방", "mode.dry": "제습", "mode.fan": "송풍",
    "act.washerDamp": "세탁기가 눅눅해짐", "act.kitchenHot": "주방이 더워짐", "act.score": "점수 {s} · 50점부터 작동",
    "act.askedYou": "여쭤봤어요: {a}", "act.yes": "'네' 답변: {a}", "act.no": "'아니요' 답변: {a}", "act.noAnswer": "답 없음: {a}", "act.nothingChanged": "아무것도 바꾸지 않았어요",
    "act.saidYes": "'네'라고 답하셨어요", "act.safety": "안전 규칙", "act.own": "자동으로 처리", "act.fromApp": "앱에서 조작", "act.doneX": "완료: {a}",
    "act.noRespond": "{a} 응답 없음", "act.blocked": "실행하지 않음: {a}", "act.cooled": "주방이 식었어요", "act.restart": "건조기를 다시 켜도 돼요",
    "act.demo": "데모 시나리오 불러옴", "act.request": "요청 {id}",
    "job.next": "다음 {t}", "job.disabled": "꺼짐", "job.once": "한 번",
  },
};

/* Action names, done-lines and device words per language. */
const ACTIONS = {
  en: {
    ventilate_washer: ["Air the washer drum", "Washer drum airing"], dehumidifier_on: ["Dehumidifier on", "Dehumidifier on"],
    purifier_on: ["Air purifier on", "Air purifier on"], ventilate_kitchen: ["Range hood on", "Range hood on"],
    precool_kitchen: ["Pre-cool the kitchen", "Kitchen pre-cooling to 24°"], stop_dryer: ["Stop the dryer", "Dryer stopped — restart it whenever you like"],
    preheat_cooktop: ["Preheat the cooktop", "Cooktop preheating for dinner"], cooktop_auto_off: ["Cooktop safety shut-off", "Cooktop switched off for safety"],
    cool_room: ["Cool the room gently", "Room cooling gently to 26°"], eco_ac: ["Raise the AC 2°", "AC raised 2° to save power"],
    pause_washer: ["Pause the wash", "Wash paused — restart it after the peak"],
  },
  ko: {
    ventilate_washer: ["세탁기 통 환기", "세탁기 통을 말리는 중"], dehumidifier_on: ["제습기 켜기", "제습기를 켰어요"],
    purifier_on: ["공기청정기 켜기", "공기청정기를 켰어요"], ventilate_kitchen: ["레인지 후드 켜기", "레인지 후드를 켰어요"],
    precool_kitchen: ["주방 미리 냉방", "주방을 24°로 미리 냉방 중"], stop_dryer: ["건조기 끄기", "건조기를 껐어요 — 원할 때 다시 켜세요"],
    preheat_cooktop: ["쿡탑 예열", "저녁을 위해 쿡탑 예열 중"], cooktop_auto_off: ["쿡탑 안전 끄기", "안전을 위해 쿡탑을 껐어요"],
    cool_room: ["방을 약하게 냉방", "방을 26°로 약하게 냉방 중"], eco_ac: ["에어컨 2° 올리기", "전기를 아끼려고 에어컨을 2° 올렸어요"],
    pause_washer: ["세탁 멈추기", "세탁을 멈췄어요 — 피크가 끝나면 다시 켜세요"],
  },
};
const ROOM_KO = { "Living Room": "거실", Kitchen: "주방", Bedroom: "침실", Balcony: "발코니" };
const ROOM_EN_SHORT = { "Living Room": "Living", Kitchen: "Kitchen", Bedroom: "Bedroom", Balcony: "Balcony" };
const TYPE_KO = { air_conditioner: "에어컨", air_purifier: "공기청정기", washer: "세탁기", dryer: "건조기", dish_washer: "식기세척기",
  refrigerator: "냉장고", water_heater: "온수기", dehumidifier: "제습기", range_hood: "레인지 후드", oven: "오븐", cooktop: "쿡탑" };

// Korean first: the household is Korean. The choice is remembered per browser.
let lang = "ko";
try { const saved = localStorage.getItem("fsd-lang"); if (saved === "en" || saved === "ko") lang = saved; } catch (_) { /* private mode */ }
// A link can choose the language: /?lang=en or /?lang=ko (demos, screenshots).
try { const q = new URLSearchParams(location.search).get("lang"); if (q === "en" || q === "ko") lang = q; } catch (_) { /* ignore */ }

function t(key, vars) {
  let s = (I18N[lang] && I18N[lang][key]) || I18N.en[key] || key;
  if (vars) for (const [k, v] of Object.entries(vars)) s = s.split(`{${k}}`).join(String(v));
  return s;
}
const actionName = (k) => (ACTIONS[lang][k] || ACTIONS.en[k] || [k || ""])[0];
const actionDone = (k) => (ACTIONS[lang][k] || ACTIONS.en[k] || [])[1];
const roomName = (r) => lang === "ko" ? (ROOM_KO[r] || r) : (ROOM_EN_SHORT[r] || r);

function deviceName(d, short) {
  if (lang === "ko") return short ? (TYPE_KO[d.type] || d.name) : `${ROOM_KO[d.room] || ""} ${TYPE_KO[d.type] || d.name}`.trim();
  const n = String(d.name || "");
  return short && n.startsWith((d.room || "") + " ") ? n.slice(d.room.length + 1) : n;
}

/* Static copy in index.html: data-i18n (text), data-i18n-aria, data-i18n-ph, data-i18n-title. */
function applyStatic() {
  document.documentElement.lang = lang;
  document.querySelectorAll("[data-i18n]").forEach((el) => { el.textContent = t(el.dataset.i18n); });
  document.querySelectorAll("[data-i18n-aria]").forEach((el) => el.setAttribute("aria-label", t(el.dataset.i18nAria)));
  document.querySelectorAll("[data-i18n-ph]").forEach((el) => { el.placeholder = t(el.dataset.i18nPh); });
  document.querySelectorAll("[data-i18n-title]").forEach((el) => { el.title = t(el.dataset.i18nTitle); });
}

function setLang(next) {
  lang = next === "en" ? "en" : "ko";
  try { localStorage.setItem("fsd-lang", lang); } catch (_) { /* ignore */ }
  voiceLang = lang === "ko" ? "ko-KR" : "en-US";
  if (recognition) recognition.lang = voiceLang;
  // Re-render the open question in the new language without reading it again.
  const shown = askSig; askSig = ""; spokenAsks = shown; chipsSig = "";
  applyStatic();
  renderLangButton();
  $("#quick").innerHTML = "";
  renderAll();
}

/* ---------- voice ---------- */


/* The browser listens (Web Speech API), the text box stays permanent: a
   misheard word is fixed by typing, never by repeating yourself at a machine.
   English first because the course, the demo and the examiner are English;
   Korean is one tap away because the household is not. */
const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
const VOICE_LANGS = ["en-US", "ko-KR"];
const VOICE_PAUSE_MS = 2000;   // a pause this long means "I have finished"
const VOICE_TAIL_QUIET_MS = 900;   // a "final" this long after any audio is a guess
const VOICE_MAX_RESTARTS = 8;
let voiceLang = VOICE_LANGS[0];
let recognition = null;
let listening = false;
let ttsOn = false;
let voiceTimer = null;         // the pause that ends the turn
let voiceRestarts = 0;
let voiceBase = "";            // words finalised before the current session
let voiceSaid = "";            // finalised words so far in this turn
let voiceWarned = false;       // the "I heard nothing" hint, once per turn
let voiceDone = false;         // this turn has already been handed to send()
let voiceFinals = 0;           // finalised phrases the engine has handed back
let voiceInterim = "";         // the last thing the engine was still guessing at
let voiceLastHeard = 0;        // when any text last arrived from the engine

function speak(text, force) {
  if (!(ttsOn || force) || !("speechSynthesis" in window)) return;
  const clean = String(text || "").replace(/\s+/g, " ").trim();
  if (!clean) return;
  window.speechSynthesis.cancel();
  const utter = new SpeechSynthesisUtterance(clean);
  utter.lang = voiceLang;
  utter.rate = 0.95;  // the audience is not in a hurry
  window.speechSynthesis.speak(utter);
}

function setListening(on) {
  listening = on;
  for (const id of ["#mic", "#talkFab"]) {
    const btn = $(id);
    if (!btn) continue;
    btn.classList.toggle("listening", on);
    btn.setAttribute("aria-pressed", String(on));
  }
  const label = $("#micLabel");
  if (label) label.textContent = on ? t("talk.listening") : t("talk.tap");
  const input = $("#input");
  if (input) input.placeholder = on ? t("talk.listeningPh") : t("talk.placeholder");
}

/* The engine hands back each phrase without the space that belonged between
   them: "turn on the living" and "room AC" are two phrases, and gluing them
   with + makes one word out of two. */
function glue(a, b) {
  if (!a) return b || "";
  if (!b) return a;
  if (/[\s-]$/.test(a) || /^[\s,.;:!?]/.test(b)) return a + b;
  return `${a} ${b}`;
}

/* End the turn: take whatever is in the box and send it. Guarded by voiceDone so
   the three ways a turn can end — the pause timer, a second tap, the engine
   giving up — cannot send the same sentence twice. */
function voiceFinish() {
  if (voiceDone) return;
  voiceDone = true;
  clearTimeout(voiceTimer);
  voiceTimer = null;
  const text = ($("#input").value || voiceSaid).replace(/\s+/g, " ").trim();
  setListening(false);
  try { if (recognition) recognition.stop(); } catch (_) { /* already stopped */ }
  if (text) send(text);
}

/* The pause, not the engine, decides when the sentence is over. Every result
   pushes it back, so a breath in the middle of a sentence costs nothing. */
function voiceArmPause() {
  clearTimeout(voiceTimer);
  voiceTimer = setTimeout(voiceFinish, VOICE_PAUSE_MS);
}

function startVoice() {
  if (!SpeechRecognition) {
    toast("Voice needs Chrome or Safari — please type.");
    return;
  }
  if (listening) { voiceFinish(); return; }   // a second tap means "send now"
  if (!recognition) {
    recognition = new SpeechRecognition();
    recognition.interimResults = true;
    // continuous, because the default ends the session at the first short
    // pause: "turn on the living room AC … and set it to 24" arrived as two
    // half sentences, and the first half was already on its way to the model
    // before the second half was spoken.
    recognition.continuous = true;
    recognition.onresult = (event) => {
      // Recompute from index 0 rather than from resultIndex: with continuous
      // on, the results list is the whole turn, and rebuilding it is idempotent
      // where appending to it is not.
      const now = Date.now();
      let finalText = "";
      let interim = "";
      let finals = 0;
      for (let i = 0; i < event.results.length; i++) {
        const result = event.results[i];
        if (result.isFinal) {
          finals++;
          finalText = glue(finalText, result[0].transcript);
        } else if (String(result[0].transcript || "").trim()) {
          interim = glue(interim, result[0].transcript);
        }
      }
      if (interim) voiceInterim = interim;

      if (finals > voiceFinals) {
        // Speech is announced before it is confirmed: while a person is
        // talking, the engine keeps handing back interim text, and the final
        // phrase lands as the confirmation of something it was already
        // guessing at. A phrase that arrives with nothing interim behind it,
        // more than a second after the last audio, was not spoken — the engine
        // filled a silence. "turn on living room" came back as "turn on living
        // room they say": the tail was invented, and because every result
        // pushed the pause timer back, a slow speaker's turn never ended on
        // their own schedule.
        const invented = voiceSaid && !voiceInterim &&
                         now - voiceLastHeard > VOICE_TAIL_QUIET_MS;
        if (invented) return;        // drop it; the pause timer keeps running
        voiceFinals = finals;
        voiceInterim = "";
      } else if (!interim) {
        return;                      // nothing new: do not extend the turn
      }

      voiceLastHeard = now;
      voiceSaid = glue(voiceBase, finalText).trim();
      // Live in the box: the user sees what was heard before it is sent, which
      // is the difference between trusting a microphone and guessing at one.
      $("#input").value = glue(voiceSaid, interim).trim();
      voiceArmPause();
    };
    recognition.onerror = (event) => {
      if (voiceDone) return;
      if (event.error === "not-allowed") {
        toast("Microphone permission denied — please type.");
        voiceDone = true;      // stop onend from restarting a denied mic
      } else if (event.error === "no-speech") {
        // Silence, not a pause: nothing at all was captured this turn. On a
        // laptop this is almost always the wrong input device — Windows keeps
        // the headset-jack microphone selected, which is a microphone that
        // exists and hears nothing unless something is plugged into it. Saying
        // so is the difference between "the app is broken" and "pick the other
        // microphone".
        if (!voiceWarned && !voiceSaid && !$("#input").value.trim()) {
          voiceWarned = true;
          toast("I heard nothing — check which microphone Windows is using.");
        }
        return;                // onend decides; nothing was said yet
      } else if (event.error !== "aborted") {
        toast("Voice stopped — please type.");
      }
    };
    recognition.onend = () => {
      if (voiceDone) { setListening(false); return; }
      // Safari ends the session at every pause even with continuous = true.
      // Start it again and keep what has been heard, so the pause timer — not
      // the engine — is what ends the turn. Delayed slightly to avoid spinning.
      if (listening && voiceRestarts < VOICE_MAX_RESTARTS) {
        voiceRestarts++;
        setTimeout(() => {
          if (voiceDone || !listening) return;
          // A new session starts with an empty results list, so bank what the
          // last one heard before asking for more.
          voiceBase = voiceSaid;
          voiceFinals = 0;
          voiceInterim = "";
          try { recognition.start(); } catch (_) { voiceFinish(); }
        }, 250);
        return;
      }
      voiceFinish();
    };
  }
  recognition.lang = voiceLang;
  voiceBase = "";
  voiceSaid = "";
  voiceWarned = false;
  voiceDone = false;
  voiceRestarts = 0;
  voiceFinals = 0;
  voiceInterim = "";
  voiceLastHeard = Date.now();
  clearTimeout(voiceTimer);
  $("#input").value = "";
  try {
    recognition.start();
    setListening(true);
  } catch (err) {
    setListening(false);  // already running; the tap is a stop
  }
}

function renderLangButton() {
  document.querySelectorAll("#lang [data-lang]").forEach((b) => {
    const on = b.dataset.lang === lang;
    b.classList.toggle("on", on);
    b.setAttribute("aria-pressed", String(on));
  });
}

/* ---------- home: hero ---------- */

function riskFacts(r) {
  const d = (r && r.drivers) || {};
  if (!r) return [];
  if (r.kind === "mold") {
    const out = [];
    if (d.drum_humidity != null) out.push(["sky", t("fact.drum", { h: Math.round(d.drum_humidity) })]);
    if (d.sealed_hours) out.push(["butter", t("fact.shut", { t: d.sealed_hours < 1 ? `${Math.round(d.sealed_hours * 60)}${lang === "ko" ? "분" : " min"}` : `${d.sealed_hours.toFixed(1)}${lang === "ko" ? "시간" : " h"}` })]);
    return out;
  }
  if (r.kind === "energy") {
    const out = [["sky", t("fact.load", { kw: ((d.total_load_w || 0) / 1000).toFixed(1) })]];
    if (d.tariff === "peak") out.push(["pink", t("fact.peak")]);
    if (d.cooling_acs) out.push(["butter", t("fact.acs", { n: d.cooling_acs })]);
    return out;
  }
  if (r.kind === "heat_health") return [["peach", `${Math.round(d.apparent_temp || 0)}°`]];
  const out = [];
  if (d.kitchen_temp != null) out.push(["peach", `${Math.round(d.kitchen_temp)}°C`]);
  if (d.heat_load_w) out.push(["sky", t("fact.kw", { kw: (d.heat_load_w / 1000).toFixed(1) })]);
  if (d.cooking_scheduled_in_min != null) out.push(["butter", t("fact.dinner", { m: d.cooking_scheduled_in_min })]);
  return out;
}

function heroState() {
  const pending = (pro && pro.pending_approvals) || [];
  const risks = (pro && pro.risks) || {};
  if (pending.length) {
    const facts = riskFacts(risks[pending[0].risk_kind]).map((f) => f[1]).slice(0, 2).join(" · ");
    return { mood: "ask", title: pending.length === 1 ? t("hero.ask1") : t("hero.askN", { n: pending.length }),
             sub: facts || t("hero.askSub"), cta: true };
  }
  const g = pro && pro.cooktop_guard;
  if (g) {
    let sub;
    if (g.cooking) sub = t("hero.cooking");
    else if (g.meal_in_min != null) sub = t("hero.watchMeal", { m: Math.round(g.meal_in_min) });
    else sub = t("hero.watchIdle", { m: Math.ceil(g.idle_off_in / 60) });
    return { mood: "watch", title: t("hero.watch"), sub };
  }
  if (risks.mold && risks.mold.firing) return { mood: "busy", title: t("hero.mold"), sub: t("hero.moldSub") };
  if (risks.thermal && risks.thermal.firing) return { mood: "busy", title: t("hero.heat"), sub: t("hero.heatSub") };
  if (risks.heat_health && risks.heat_health.firing) {
    const d = risks.heat_health.drivers || {};
    const rn = roomName(d.room || "Bedroom");
    return { mood: "busy", title: t("hero.hot", { room: lang === "en" ? rn.toLowerCase() : rn }), sub: t("hero.hotSub", { t: Math.round(d.apparent_temp || 0) }) };
  }
  if (risks.energy && risks.energy.firing) {
    return { mood: "busy", title: t("hero.power"), sub: t("hero.powerSub", { kw: (((risks.energy.drivers || {}).total_load_w || 0) / 1000).toFixed(1) }) };
  }
  return { mood: "calm", title: t("hero.calm"), sub: t("hero.calmSub") };
}

function renderHero() {
  const s = heroState();
  const box = $("#hero");
  box.dataset.mood = s.mood;
  box.innerHTML = `<div class="status-ic">${mascot(s.mood)}</div>
    <div class="hero-text"><strong>${esc(s.title)}</strong><span>${esc(s.sub)}</span></div>
    ${s.cta && $("#askSheet").hidden ? `<button type="button" class="hero-cta" data-open-ask>${esc(t("hero.answer"))}</button>` : ""}`;
}

/* ---------- home: risk rings (FR-2) ---------- */

const RING_C = 2 * Math.PI * 38;

function riskDetail(r) {
  const d = (r && r.drivers) || {};
  if (r && r.kind === "mold") {
    if (d.drum_humidity == null) return t("risk.open");
    return `${t("risk.humid", { h: Math.round(d.drum_humidity) })} · ${d.sealed_hours >= 1 ? t("risk.shut", { n: d.sealed_hours.toFixed(0) }) : t("risk.sealed")}`;
  }
  if (r && r.kind === "heat_health" && !(r.drivers || {}).room) return t("risk.away");   // no reading: nobody home
  if (r && r.kind === "heat_health") {
    const room = roomName(d.room || "Living Room");
    return r.score >= 30 ? t("risk.feels", { room, t: Math.round(d.apparent_temp || 0) }) : t("risk.cool", { room, t: Math.round(d.room_temp || 0) });
  }
  if (r && r.kind === "energy") return `${((d.total_load_w || 0) / 1000).toFixed(1)} kW · ${t(`risk.rate.${d.tariff || "offpeak"}`)}`;
  if (!r || d.kitchen_temp == null) return t("risk.nothingHot");
  return `${d.kitchen_temp.toFixed(1)}°C · ${((d.heat_load_w || 0) / 1000).toFixed(1)} kW`;
}

function renderRisks() {
  const risks = (pro && pro.risks) || {};
  const quiet = (pro && pro.quiet) || {};
  const asking = new Set(((pro && pro.pending_approvals) || []).map((a) => a.risk_kind));
  $("#risks").innerHTML = RISK_ORDER.map((kind) => {
    const r = risks[kind] || { kind, score: 0, threshold: 50, drivers: {} };
    const score = Math.round(r.score || 0);
    const level = r.firing ? "high" : score >= r.threshold * 0.6 ? "watch" : "low";
    const tag = { high: ["pink", t("risk.high")], watch: ["butter", t("risk.watch")], low: ["mint", t("risk.low")] }[level];
    const name = t(RISK[kind].key);
    // Only risks that can ask a question have a quiet period worth showing.
    const q = quiet[kind] > 60 && !asking.has(kind) && r.firing && (kind === "thermal" || kind === "energy")
      ? `<p class="quiet">${esc(t("risk.quiet", { m: Math.ceil(quiet[kind] / 60) }))}</p>` : "";
    return `<article class="tile" data-kind="${kind}" data-level="${level}">
      <div class="tile-top">${icon(RISK[kind].icon)}<span class="tag tag-${tag[0]}">${esc(tag[1])}</span></div>
      <div class="ring" role="meter" aria-label="${esc(t("risk.aria", { name }))}" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${score}">
        <svg viewBox="0 0 84 84"><circle class="track" cx="42" cy="42" r="38"/>
          <circle class="prog" cx="42" cy="42" r="38" stroke-dasharray="${RING_C.toFixed(1)}"
            stroke-dashoffset="${(RING_C * (1 - Math.min(100, Math.max(3, score)) / 100)).toFixed(1)}"/></svg>
        <b>${score}</b>
      </div>
      <h3>${esc(name)}</h3>
      <p>${esc(riskDetail(r))}</p>
      ${q}
    </article>`;
  }).join("");
}

/* ---------- home: heat load ---------- */

const FULL_KITCHEN_W = 2500;   // risk.py: 2500 W counts as a full kitchen

function renderHeat() {
  if (!$("#heat")) return;   // folded into the Power tile on Home
  const w = (pro && pro.heat_load_w) || 0;
  const pct = Math.min(100, Math.round((w / FULL_KITCHEN_W) * 100));
  const level = w >= FULL_KITCHEN_W ? "high" : w >= FULL_KITCHEN_W * 0.6 ? "watch" : "low";
  const app = (pro && pro.appliances) || {};
  const on = [["oven", "app.oven"], ["cooktop", "app.cooktop"], ["dryer", "app.dryer"], ["dish_washer", "app.dishwasher"]]
    .filter(([k]) => app[k] && app[k].power).map(([, key]) => t(key));
  const box = $("#heat");
  box.dataset.level = level;
  box.innerHTML = `<div class="heat-head">${icon("i-flame")}<span>${esc(t("heat.title"))}</span><strong>${(w / 1000).toFixed(1)} kW</strong></div>
    <div class="bar" role="meter" aria-label="${esc(t("heat.aria"))}" aria-valuemin="0" aria-valuemax="${FULL_KITCHEN_W}" aria-valuenow="${w}"><span style="width:${pct}%"></span></div>
    ${on.length ? `<small>${esc(t("heat.running", { list: on.join(", ") }))}</small>` : ""}`;
}

/* ---------- home: quick chips ---------- */

// The phrases sent to the house stay English: they go through the same rule
// parser the tests cover. Only the button labels change with the language.
const SCENES = [
  { key: "off", icon: "i-power", label: "quick.off", say: () => "turn off everything in the living room" },
  { key: "cool", icon: "i-temp", label: "quick.cool", say: () => "turn on the living room AC and set it to 24 degrees" },
  { key: "fresh", icon: "i-wind", label: "quick.fresh", say: () => "it's stuffy in the living room" },
];

function renderQuick() {
  $("#quick").innerHTML = SCENES.map((s) =>
    `<button type="button" data-scene="${s.key}">${icon(s.icon)}<span>${esc(t(s.label))}</span></button>`).join("");
}

/* ---------- home: "just now" ---------- */

function renderRecent() {
  const rows = (auditRows || []).filter((r) => r.event_type === "notification.dryer_restart"
    || (r.event_type === "action.executed" && actionDone(r.action_kind)));
  const fresh = rows.filter((r) => Date.now() - new Date(r.ts).getTime() < 15 * 60 * 1000).slice(0, 2);
  const box = $("#recent");
  if (!fresh.length || (pro && (pro.pending_approvals || []).length)) { box.hidden = true; return; }
  box.hidden = false;
  box.innerHTML = `<p>${esc(t("recent.title"))}</p><ul>${fresh.map((r) => `<li>${icon("i-check")}<span>${esc(
    r.event_type === "notification.dryer_restart" ? t("recent.cooled") : actionDone(r.action_kind))}</span></li>`).join("")}</ul>`;
}

/* ---------- the ask sheet (FR-3 / FR-4) ---------- */

function askLeft(ap) {
  return (new Date(ap.expires_at).getTime() - Date.now()) / 1000;
}

function questionText(ap) {
  const k = ap.action_kind || (ap.actions && ap.actions[0] && ap.actions[0].kind);
  const known = I18N.en[`q.${k}`];
  return { k, q: known ? t(`q.${k}`) : (ap.question || ap.summary), c: known ? t(`c.${k}`) : "",
           icon: (QUESTION[k] || {}).icon || "i-sparkle", tone: (QUESTION[k] || {}).tone || "" };
}

function renderAsk() {
  const list = (pro && pro.pending_approvals) || [];
  const sheet = $("#askSheet");
  const sig = list.map((a) => a.id).join("|");
  if (!list.length) {
    if (!sheet.hidden) closeSheets();
    askSig = ""; askDismissed = "";
    return;
  }
  if (sig !== askSig) {
    askSig = sig;
    const r = ((pro && pro.risks) || {})[list[0].risk_kind];
    const title = { mold: t("ask.mold"), energy: t("ask.energy") }[list[0].risk_kind] || t("ask.heat");
    $("#askBody").innerHTML = `
      <div class="why">
        <div class="status-ic">${mascot("ask")}</div>
        <div><h2 id="askTitle">${esc(title)}</h2>
          <div class="facts">${riskFacts(r).map(([tone, l]) => `<span class="tag tag-${tone}">${esc(l)}</span>`).join("")}</div></div>
      </div>
      <div class="count"><span>${esc(list.length === 1 ? t("ask.one") : t("ask.n", { n: list.length }))}</span><span id="askClock"></span></div>
      ${list.map((ap) => {
        const q = questionText(ap);
        return `<div class="card q" role="group" aria-label="${esc(q.q)}" data-ask="${esc(ap.id)}">
          <div class="q-top"><div class="q-ic ${q.tone}">${icon(q.icon)}</div>
            <div><strong>${esc(q.q)}</strong>${q.c ? `<span>${esc(q.c)}</span>` : ""}
              <span class="q-own" data-clock="${esc(ap.id)}"></span></div></div>
          <div class="answers">
            <button type="button" class="answer yes" data-answer="yes" data-id="${esc(ap.id)}">${icon("i-check")}${esc(t("ask.yes"))}${lang === "en" ? "<small>네</small>" : ""}</button>
            <button type="button" class="answer no" data-answer="no" data-id="${esc(ap.id)}">${icon("i-x")}${esc(t("ask.no"))}${lang === "en" ? "<small>아니요</small>" : ""}</button>
          </div></div>`;
      }).join("")}
      <p class="rule">${esc(t("ask.rule"))}</p>`;
    if (askDismissed !== sig) openSheet("askSheet");
    // One spoken prompt for the whole set, so two questions never cut each
    // other off. Read aloud regardless of the read-aloud setting.
    if (spokenAsks !== sig) {
      spokenAsks = sig;
      const qs = list.map((ap) => questionText(ap).q).join(" ");
      speak(`${title}. ${list.length === 1 ? t("ask.speakOne") : t("ask.speakN", { n: list.length })} ${qs}`, true);
    }
  }
  const lefts = list.map(askLeft);
  const min = Math.min(...lefts);
  const spread = Math.max(...lefts) - min > 5;
  const c = $("#askClock");
  if (c) c.textContent = min > 0 ? t("ask.left", { t: clock(min) }) : t("ask.up");
  for (const ap of list) {
    const el = document.querySelector(`[data-clock="${ap.id}"]`);
    if (el) el.textContent = spread ? t("ask.left", { t: clock(askLeft(ap)) }) : "";
  }
}

async function answer(yes, id) {
  if (askBusy || !id) return;
  askBusy = true;
  document.querySelectorAll(`[data-id="${CSS.escape(id)}"]`).forEach((b) => { b.disabled = true; });
  try {
    const res = await api(`/api/approvals/${encodeURIComponent(id)}/${yes ? "confirm" : "decline"}`,
      { method: "POST", body: JSON.stringify({ channel: "ui" }) });
    // `expired` and `blocked` are answers too, and the toast must say what the
    // devices actually did (FR-4), not "Done". The reason text comes from the
    // server in English.
    if (res && res.expired) toast(t("toast.late"));
    else if (res && res.ok === false) toast(cap(res.reason || res.error || t("toast.closed")));
    else toast(yes ? t("toast.done") : t("toast.no"));
    await refresh();
  } catch (err) {
    toast(t("toast.unreach", { e: err.message }));
  } finally {
    askBusy = false;
  }
}

/* ---------- devices ---------- */

function devState(d) {
  const st = d.state || {};
  const on = !!st.power;
  switch (d.type) {
    case "air_conditioner":
      return on ? [t(`mode.${st.mode}`) !== `mode.${st.mode}` ? t(`mode.${st.mode}`) : t("state.on"), st.target_temp != null ? `${st.target_temp}°` : ""].filter(Boolean).join(" ") : t("state.off");
    case "air_purifier": return on ? `PM2.5 ${st.pm25 == null ? "—" : st.pm25}` : t("state.off");
    case "washer": return on ? t("state.washing", { m: st.remaining_min == null ? "?" : st.remaining_min }) : (st.state === "done" ? t("state.finished") : t("state.standby"));
    case "refrigerator": return `${st.fridge_temp == null ? "—" : st.fridge_temp}°C · ${st.duty_cycle == null ? "—" : Math.round(st.duty_cycle * 100) + "%"}`;
    case "range_hood": return on ? t("state.fan", { f: lang === "ko" ? ({ high: "강", mid: "중", medium: "중", low: "약", auto: "자동" }[st.fan || "auto"] || st.fan) : (st.fan || "auto") }) : t("state.off");
    case "oven": return on ? t("state.heating", { t: st.target_temp == null ? "—" : st.target_temp + "°C" }) : t("state.off");
    default: return on ? t("state.on") : t("state.off");
  }
}

function renderDevices() {
  const all = (snapshot && snapshot.devices) || [];
  const rooms = ROOM_ORDER.filter((r) => all.some((d) => d.room === r));
  const onCount = all.filter((d) => (d.state || {}).power).length;
  $("#devCount").textContent = t("dev.count", { n: all.length, on: onCount });
  $("#roomFilter").innerHTML = ["All", ...rooms].map((r) =>
    `<button type="button" role="tab" aria-selected="${r === activeRoom}" class="${r === activeRoom ? "on" : ""}" data-room="${esc(r)}">${esc(r === "All" ? t("dev.all") : roomName(r))}</button>`).join("");
  const list = activeRoom === "All" ? all : all.filter((d) => d.room === activeRoom);
  const sensor = ((pro && pro.rooms) || {})[activeRoom];
  const roomOn = list.filter((d) => (d.state || {}).power).length;
  const kw = (((pro && pro.heat_load_w) || 0) / 1000).toFixed(1);
  const stats = activeRoom === "All"
    ? [[`${onCount}/${all.length}`, t("dev.devicesOn")], [`${kw} kW`, t("dev.heat")], [String(rooms.length), t("dev.rooms")]]
    : [[sensor ? `${sensor.temp.toFixed(1)}°` : "—", t("dev.temp")], [sensor ? `${Math.round(sensor.humidity)}%` : "—", t("dev.hum")], [`${roomOn}/${list.length}`, t("dev.on")]];
  $("#roomStats").innerHTML = stats.map(([v, l]) => `<div><strong>${esc(v)}</strong><span>${esc(l)}</span></div>`).join("");
  $("#devices").innerHTML = list.length ? list.map((d) => {
    const on = !!(d.state || {}).power;
    const name = deviceName(d, activeRoom !== "All");
    return `<article class="dev${on ? " on" : ""}${d.online === false ? " offline" : ""}">
      <button type="button" class="dev-open" data-open="${esc(d.id)}" aria-label="${esc(t("dev.details", { name: deviceName(d) }))}">
        <span class="dev-ic">${icon(ICON[d.type] || "i-devices")}</span>
        <span class="dev-name">${esc(name)}</span>
        <span class="dev-state">${esc(devState(d))}</span>
      </button>
      <button type="button" class="switch" role="switch" aria-checked="${on}" aria-label="${esc(deviceName(d))}"
              data-power="${esc(d.id)}" data-on="${on ? 1 : 0}"></button>
    </article>`;
  }).join("") : `<p class="empty">${esc(t("dev.empty"))}</p>`;
}

/* ---------- activity: the audit trail in words (FR-6) ---------- */

function auditItem(r) {
  const act = r.action_kind ? actionName(r.action_kind) : (r.device_id || "");
  const req = r.approval_id ? t("act.request", { id: String(r.approval_id).slice(0, 6) }) : "";
  switch (r.event_type) {
    case "risk.triggered":
      return { tone: "warn", icon: "i-flame", title: { mold: t("act.washerDamp"), heat_health: t("act.roomHot"), energy: t("act.powerHigh") }[r.risk_kind] || t("act.kitchenHot"),
               detail: t("act.score", { s: Math.round(r.risk_score || 0) }) };
    case "approval.proposed": return { tone: "ask", icon: "i-sparkle", title: t("act.askedYou", { a: act }), detail: req };
    case "approval.confirmed": return { tone: "ok", icon: "i-check", title: t("act.yes", { a: act }), detail: req };
    case "approval.declined": return { tone: "plain", icon: "i-x", title: t("act.no", { a: act }), detail: req };
    case "approval.expired": return { tone: "plain", icon: "i-clock", title: t("act.noAnswer", { a: act }), detail: t("act.nothingChanged") };
    case "action.executed": {
      const who = r.approval_id ? t("act.saidYes") : r.channel === "safety-interlock" ? t("act.safety")
        : r.channel === "engine" ? t("act.own") : t("act.fromApp");
      return { tone: "ok", icon: "i-check", title: actionDone(r.action_kind) || t("act.doneX", { a: act }), detail: [who, req].filter(Boolean).join(" · ") };
    }
    case "action.failed": return { tone: "bad", icon: "i-x", title: t("act.noRespond", { a: act || "—" }), detail: r.detail || "" };
    case "action.blocked": return { tone: "plain", icon: "i-x", title: t("act.blocked", { a: act }), detail: cap(r.detail || "") };
    case "notification.dryer_restart": return { tone: "plain", icon: "i-check", title: t("act.cooled"), detail: t("act.restart") };
    case "scenario.reset": return { tone: "plain", icon: "i-sparkle", title: t("act.demo"), detail: r.detail || "" };
    default: return { tone: "plain", icon: "i-clock", title: r.event_type, detail: r.detail || "" };
  }
}

function renderActivity() {
  document.querySelectorAll("#actFilter [data-act]").forEach((b) => b.classList.toggle("on", b.dataset.act === actFilter));
  const rows = (auditRows || []).filter((r) => r.event_type !== "action.intent").filter((r) =>
    actFilter === "all" ? true
      : actFilter === "asked" ? r.event_type.startsWith("approval.")
      : r.event_type === "action.executed");
  $("#auditLog").innerHTML = rows.length ? rows.slice(0, 30).map((r) => {
    const it = auditItem(r);
    return `<li data-tone="${it.tone}"><span class="t-ic">${icon(it.icon)}</span>
      <div class="t-text"><strong>${esc(it.title)}</strong>${it.detail ? `<span>${esc(it.detail)}</span>` : ""}<code>${esc(r.event_type)}</code></div>
      <span class="t-time">${esc(hhmm(r.ts))}</span></li>`;
  }).join("") : `<li class="empty">${esc(t("act.empty"))}</li>`;
}

/* ---------- settings ---------- */

function renderJobs() {
  const jobs = (snapshot && snapshot.jobs) || [];
  $("#jobs").innerHTML = jobs.length ? jobs.map((j) => `
    <div class="li">${icon("i-clock")}
      <div class="li-text"><strong>${esc(cap(j.summary || j.device_name || "Scheduled task"))}</strong>
        <span>${esc(repeatShort(j.repeat_label) === "once" ? t("job.once") : repeatShort(j.repeat_label))} · ${j.enabled ? esc(t("job.next", { t: fmtWhen(j.next_run_at) })) : esc(t("job.disabled"))}</span></div>
      <button type="button" class="del" data-del="${esc(j.id)}" aria-label="${esc(t("set.del"))}">×</button>
    </div>`).join("")
    : `<div class="li">${icon("i-clock")}<div class="li-text"><strong>${esc(t("set.none"))}</strong><span>${esc(t("set.noneSub"))}</span></div></div>`;
}

function renderMode() {
  const mode = (snapshot && snapshot.mode) || "cascade";
  document.querySelectorAll("[data-agent-mode]").forEach((b) => {
    const on = b.dataset.agentMode === mode;
    b.classList.toggle("on", on);
    b.setAttribute("aria-pressed", String(on));
  });
}

/* ---------- chat ---------- */

function liveBuild() {
  return (snapshot && snapshot.build) || "";
}

/* ---------- chat scrolling ----------
   The transcript is its own scroll container now (see .chat in style.css).
   Nothing here touches the document: on a phone the browser scrolls the
   nearest scrollable ancestor to reveal a focused field, and the field lives
   in the talk bar, so scrolling the page was what made the transcript jump. */
function chatBox() { return $("#chat"); }

function nearBottom(el, slack = 60) {
  return el.scrollHeight - el.scrollTop - el.clientHeight <= slack;
}

function scrollChatToEnd(force = false) {
  const el = chatBox();
  if (!el) return;
  // Never yank someone who scrolled up to re-read a message.
  if (!force && !nearBottom(el)) return;
  el.scrollTop = el.scrollHeight;
}

function addMsg(role, text, message, opts = {}) {
  const el = document.createElement("div");
  el.className = `msg ${role}`;
  el.textContent = text;
  const tag = tagFor(message);
  if (tag) {
    const stamp = document.createElement("span");
    stamp.className = "stamp";
    stamp.textContent = tag;
    el.appendChild(stamp);
  }
  const trace = (message && message.payload && message.payload.trace) || [];
  if (trace.length) {
    const det = document.createElement("details");
    det.className = "trace";
    const sum = document.createElement("summary");
    const calls = trace.filter((s) => s.tool !== "offer").length;
    sum.textContent = (message.payload && message.payload.adopted)
      ? `took the rules' reading · ${calls} ${calls === 1 ? "step" : "steps"}`
      : `declined the reading · ${calls} ${calls === 1 ? "step" : "steps"}`;
    det.appendChild(sum);
    const ol = document.createElement("ol");
    trace.forEach((s) => {
      const li = document.createElement("li");
      li.textContent = s.result || s.tool;
      if (s.tool === "offer") li.className = "offer";
      ol.appendChild(li);
    });
    det.appendChild(ol);
    el.appendChild(det);
  }
  chatBox().appendChild(el);
  scrollChatToEnd(opts.force);

  // Read replies aloud when asked to — and always read a Tier-2 question,
  // because that is the one message that must not be missed. A transcript
  // rebuild is not a new message and must stay silent.
  const approval = message && message.payload && message.payload.approval;
  if (!opts.silent && (ttsOn || approval)) speak(text);
  return el;
}

function tagFor(msg) {
  if (!msg) return null;
  const rows = [];
  const when = msg.ts || msg.created_at;
  if (when) {
    const t = new Date(when);
    rows.push(isNaN(t) ? String(when).slice(11, 16) : t.toTimeString().slice(0, 5));
  }
  // A reply written by a build that no longer runs is a historical answer;
  // marking it is the difference between reading a transcript and trusting it.
  // Only a message that carries a build can be called stale: an optimistic user
  // bubble has no payload, and stamping it "older build" a second after it was
  // typed is a false alarm — the one thing a diagnostic must never do.
  const b = (msg.payload || {}).build;
  if (b && liveBuild() && b !== liveBuild()) rows.push(`old build ${b}`);
  return rows.length ? rows.join(" · ") : null;
}

function renderChips() {
  const devices = (snapshot && snapshot.devices) || [];
  const primary = devices.find((d) => d.type === "air_conditioner") || devices[0];
  if (!primary) return;
  const name = primary.name;
  const room = primary.room || "";
  const on = !!(primary.state || {}).power;

  const chips = [
    [`Turn ${on ? "off" : "on"} the ${name}`, on ? "Turn off" : "Turn on"],
    [`Set the ${name} to 24 degrees`, "24°C"],
    ["It's stuffy in here", "It's stuffy"],
    ["What scheduled tasks do I have?", "Schedules"],
    ["거실 에어컨 꺼줘", "한국어 예시"],
  ];
  if (room) chips.push([`Turn off everything in the ${room}`, "Whole room"]);

  const sig = chips.map((c) => c[0]).join("|");
  if (sig === chipsSig) return;
  chipsSig = sig;
  $("#chips").innerHTML = chips
    .map(([say, label]) => `<button data-say="${esc(say)}">${esc(label)}</button>`)
    .join("");
}

async function send(text) {
  const trimmed = (text || "").trim();
  if (!trimmed) return;
  await openTalk(false);
  addMsg("user", trimmed, { ts: new Date().toISOString() }, { force: true });
  $("#input").value = "";
  // The agent tier takes 4–8 s on a real model. Silence for that long reads as
  // a dead app, so say something immediately and replace it when the answer
  // lands. Built by hand rather than through addMsg: a placeholder should not
  // be read aloud.
  const pending = document.createElement("div");
  pending.className = "msg assistant pending";
  pending.textContent = "Working on it…";
  chatBox().appendChild(pending);
  scrollChatToEnd(true);
  try {
    const data = await api("/api/chat", {
      method: "POST",
      body: JSON.stringify({ text: trimmed }),
    });
    pending.remove();
    addMsg("assistant", data.reply || "…", { ts: new Date().toISOString(), payload: data }, { force: true });
    showReply(data.reply || "");
    await refresh();
    return data.reply || "";
  } catch (err) {
    pending.remove();
    addMsg("system", `Request failed: ${err.message}`);
    return "";
  }
}

let chatSig = "";

async function loadMessages(force = false) {
  const data = await api("/api/messages");
  const list = data.messages || [];
  // Rebuilding the transcript for an unchanged list is not free: it resets the
  // scroll position, and with read-aloud on it re-reads every message. Compare
  // a signature first and leave the DOM alone when nothing has arrived.
  const sig = list
    .map((m, i) => `${i}:${m.role}:${(m.content || "").length}:${(m.content || "").slice(0, 24)}`)
    .join("|");
  if (!force && sig === chatSig && chatBox().children.length) return;
  chatSig = sig;
  const box = chatBox();
  box.innerHTML = "";
  for (const m of list) addMsg(m.role, m.content, m, { silent: true });
  if (!list.length) {
    addMsg("system", "Say something, or tap a suggestion below.", null, { silent: true });
  }
  requestAnimationFrame(() => scrollChatToEnd(true));
}

/* ---------- decision mode ---------- */

async function setMode(mode) {
  try {
    const data = await api("/api/mode", { method: "POST", body: JSON.stringify({ mode }) });
    if (data.snapshot) {
      snapshot = data.snapshot;
      renderAll();
    }
  } catch (err) {
    toast(`Failed: ${err.message}`);
  }
}

/* ---------- toast and replies ---------- */

let toastTimer = null;

function toast(text, ms = 2600) {
  const el = $("#toast");
  el.textContent = text;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, ms);
}

/* The house's last answer, kept in the talk sheet until the next one. */
function showReply(text) {
  const clean = String(text || "").trim();
  if (!clean) return;
  $("#replyText").textContent = clean;
  $("#reply").hidden = false;
  $("#reply").classList.remove("pending");
  speak(clean);
}

function showPending(label) {
  $("#replyText").textContent = label || t("toast.working");
  $("#reply").hidden = false;
  $("#reply").classList.add("pending");
}

/* Quick chips run without leaving Home: the answer comes back as a toast long
   enough to read, and is spoken when read-aloud is on. */
async function runScene(scene) {
  toast(t("toast.working"), 8000);
  try {
    const data = await api("/api/chat", { method: "POST", body: JSON.stringify({ text: scene.say() }) });
    if (data.snapshot) { snapshot = data.snapshot; renderAll(); }
    toast(data.reply || t("toast.done"), 5000);
    speak(data.reply || "");
  } catch (err) {
    toast(t("toast.unreach", { e: err.message }));
  }
}

/* ---------- sheets (ask, talk, device) ---------- */

const SHEETS = ["askSheet", "talkSheet", "sheet"];

function openSheet(id) {
  for (const s of SHEETS) $(`#${s}`).hidden = s !== id;
  $("#scrim").hidden = false;
  renderHero();
}

function closeSheets() {
  if (!$("#askSheet").hidden && askSig) askDismissed = askSig;   // reopen from the hero
  for (const s of SHEETS) $(`#${s}`).hidden = true;
  $("#scrim").hidden = true;
  sheetId = null;
  if (listening) voiceFinish();
  renderHero();
}

async function openTalk(listen) {
  if ($("#talkSheet").hidden) {
    openSheet("talkSheet");
    await loadMessages().catch(() => {});
  }
  if (listen && !listening) startVoice();
}

function renderSheet() {
  const device = findDevice(sheetId);
  if (!device) return closeSheets();
  const st = device.state || {};
  const on = !!st.power;
  const caps = device.capabilities || [];
  const sub = [roomName(device.room) || device.type_label, devState(device)].filter(Boolean).join(" · ");
  let body = `<div><h2>${esc(deviceName(device))}</h2><p class="sub">${esc(sub)}</p></div>`;
  if (caps.includes("temperature")) {
    const target = st.target_temp == null ? 26 : st.target_temp;
    body += `<div class="stepper">
      <button type="button" data-step="-1" ${on ? "" : "disabled"} aria-label="${esc(t("dev.cooler"))}">−</button>
      <span class="target">${target}°C</span>
      <button type="button" data-step="1" ${on ? "" : "disabled"} aria-label="${esc(t("dev.warmer"))}">+</button></div>`;
  }
  if (caps.includes("mode")) {
    body += `<div class="pills">${MODES.map((mode) =>
      `<button type="button" data-mode="${mode}" class="${st.mode === mode ? "active" : ""}">${esc(t(`mode.${mode}`))}</button>`).join("")}</div>`;
  }
  body += `<button type="button" class="sheet-power${on ? " on" : ""}" data-sheet-power="${on ? 0 : 1}">${esc(on ? t("dev.turnOff") : t("dev.turnOn"))}</button>`;
  $("#sheetBody").innerHTML = body;
}

function openDevice(id) {
  if (!findDevice(id)) return;
  sheetId = id;
  renderSheet();
  openSheet("sheet");
}

async function control(deviceId, actions) {
  try {
    const data = await api("/api/control", { method: "POST", body: JSON.stringify({ device_id: deviceId, actions }) });
    if (data.snapshot) { snapshot = data.snapshot; renderAll(); }
    if (data.ok === false) toast(data.error || t("toast.noResponse"));
  } catch (err) {
    toast(t("toast.ctrl", { e: err.message }));
  }
}

async function loadScenario(name) {
  try {
    await api("/api/scenario", { method: "POST", body: JSON.stringify({ name }) });
    toast(t("toast.scenario"));
    askSig = ""; askDismissed = ""; spokenAsks = "";
    await refresh();
    switchTab("home");
  } catch (err) {
    toast(t("toast.scenarioFail", { e: err.message }));
  }
}

/* ---------- render ---------- */

function renderAll() {
  if (!snapshot) return;
  $("#stamp").textContent = `${(lang === "ko" ? { mock: "시뮬레이터", thinq: "LG ThinQ" } : BACKEND_LABEL)[snapshot.backend] || snapshot.backend || ""} · ${snapshot.build || ""}`;
  $("#today").textContent = new Date().toLocaleDateString(lang === "ko" ? "ko-KR" : "en-US", { weekday: "short", month: "short", day: "numeric" });
  renderAsk();
  renderHero();
  renderRisks();
  renderHeat();
  if (!$("#quick").children.length) renderQuick();
  renderRecent();
  renderDevices();
  renderActivity();
  renderJobs();
  renderChips();
  renderMode();
  if (sheetId) renderSheet();
}

async function refresh() {
  try {
    const [snap, state, trail] = await Promise.all([
      api("/api/snapshot"),
      api("/api/proactive").catch(() => null),
      api("/api/audit?limit=60").catch(() => null),
    ]);
    snapshot = snap;
    pro = state;
    auditRows = trail && trail.audit;
    renderAll();
  } catch (err) {
    /* keep the last good snapshot rather than blanking the screen */
  }
}

/* ---------- navigation ---------- */

const TABS = ["home", "devices", "activity", "settings"];

function switchTab(name) {
  if (!TABS.includes(name)) name = "home";
  currentTab = name;
  document.querySelectorAll(".tab").forEach((btn) => {
    const on = btn.dataset.tab === name;
    btn.classList.toggle("active", on);
    if (on) btn.setAttribute("aria-current", "page"); else btn.removeAttribute("aria-current");
  });
  for (const key of TABS) $(`#view-${key}`).hidden = key !== name;
  if (history.replaceState && location.hash !== `#${name}`) history.replaceState(null, "", `#${name}`);
}

/* ---------- events ---------- */

document.addEventListener("click", (event) => {
  const t = event.target;
  const hit = (sel) => t.closest(sel);
  let el;

  if ((el = hit("[data-scenario]"))) return loadScenario(el.dataset.scenario);
  if ((el = hit("[data-answer]"))) return answer(el.dataset.answer === "yes", el.dataset.id);
  if (hit("[data-open-ask]")) { askDismissed = ""; return openSheet("askSheet"); }
  if ((el = hit("[data-room]"))) { activeRoom = el.dataset.room; return renderDevices(); }
  if ((el = hit("[data-act]"))) { actFilter = el.dataset.act; return renderActivity(); }
  if ((el = hit("[data-power]"))) return control(el.dataset.power, [{ action: "power", value: el.dataset.on !== "1" }]);
  if ((el = hit("[data-step]")) && sheetId) {
    const current = (findDevice(sheetId).state || {}).target_temp;
    const next = (current == null ? 26 : current) + Number(el.dataset.step);
    return control(sheetId, [{ action: "temperature", value: Math.max(16, Math.min(30, next)) }]);
  }
  if ((el = hit("[data-mode]")) && sheetId) return control(sheetId, [{ action: "mode", value: el.dataset.mode }]);
  if ((el = hit("[data-sheet-power]")) && sheetId) return control(sheetId, [{ action: "power", value: el.dataset.sheetPower === "1" }]);
  if ((el = hit("[data-agent-mode]"))) return setMode(el.dataset.agentMode);
  if ((el = hit("[data-scene]"))) { const s = SCENES.find((x) => x.key === el.dataset.scene); return s && runScene(s); }
  if ((el = hit("[data-open]"))) return openDevice(el.dataset.open);
  if ((el = hit("[data-tab]"))) return switchTab(el.dataset.tab);
  if ((el = hit("[data-lang]"))) {
    setLang(el.dataset.lang);
    return toast(t("toast.lang"));
  }
  if ((el = hit("[data-del]"))) {
    return api(`/api/jobs/${el.dataset.del}`, { method: "DELETE" })
      .then((data) => { if (data.snapshot) { snapshot = data.snapshot; renderAll(); } })
      .catch(() => {});
  }
  if ((el = hit("[data-say]"))) return send(el.dataset.say);
  if (hit("[data-close]") || t.id === "scrim") return closeSheets();
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !$("#scrim").hidden) closeSheets();
});

$("#composer").addEventListener("submit", (event) => {
  event.preventDefault();
  send($("#input").value);
});

$("#mic").addEventListener("click", startVoice);
$("#talkFab").addEventListener("click", () => openTalk(true));

$("#tts").addEventListener("click", () => {
  ttsOn = !ttsOn;
  $("#tts").setAttribute("aria-checked", String(ttsOn));
  if (!ttsOn && "speechSynthesis" in window) window.speechSynthesis.cancel();
  toast(ttsOn ? t("toast.ttsOn") : t("toast.ttsOff"));
});

/* ---------- boot ---------- */

voiceLang = lang === "ko" ? "ko-KR" : "en-US";
applyStatic();
renderLangButton();
refresh();
setInterval(refresh, 4000);
setInterval(renderAsk, 1000);   // the clock is the point: it has to move

const startTab = (location.hash || "").replace("#", "");
switchTab(TABS.includes(startTab) ? startTab : "home");

if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js").catch(() => {});
  });
}
