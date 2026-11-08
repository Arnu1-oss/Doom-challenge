(() => {
  "use strict";
  const API_BASE = String(window.CQA_CONFIG?.apiBase || "/api").replace(/\/$/, "");
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const h = (value) => String(value ?? "").replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
  const fmt = (value) => (value === null || value === undefined ? "—" : Number(value).toLocaleString("ru-RU", {maximumFractionDigits: 2, minimumFractionDigits: Number.isInteger(value) ? 0 : 2}));
  const isScore = value => typeof value === "number" && Number.isFinite(value);
  const niceDate = iso => {try {return iso ? new Date(iso).toLocaleString("ru-RU", {day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit"}) : "—";} catch {return "—";}};
  const names = {overview: "Обзор", conversations: "Диалоги", detail: "Диалог", checklist: "Чек-лист", appeals: "Апелляции", review: "Проверка споров"};
  const state = {reviewMessages: [],view: "overview", conversations: [], checklist: null, draftChecklist: null, appeals: [], detail: null, selectedId: null, expanded: new Set([1]), filters: {q: "", channel: "all", language: "all", status: "all"}, appealFilter: "all", activeAppealId: null, loading: true, inspectorHidden: false};
  let toastTimer;

  async function request(path, options = {}) {
    let response;
    try {
      response = await fetch(`${API_BASE}${path}`, {
        ...options,
        headers: { ...(options.body ? {"Content-Type": "application/json"} : {}), ...(options.headers || {}) },
        credentials: "omit"
      });
    } catch (error) {
      throw Error(`Нет соединения с API (${error.message}). Запустите python api_server.py.`);
    }
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw Error(body.error || `HTTP ${response.status}`);
    return body;
  }
  const jsonRequest = (path, method, obj) => request(path, {method, body: JSON.stringify(obj)});
  const content = () => $("#content");
  const inspector = () => $("#inspector");
  function toast(message, error = false) {
    const el = document.createElement("div"); el.className = "toast" + (error ? " error" : ""); el.textContent = message;
    $("#toast-area").append(el); setTimeout(() => el.remove(), 4600);
  }
  function showNotice(message) {$("#notice-text").textContent = message; $("#notice-bar").hidden = false;}
  function hideNotice() {$("#notice-bar").hidden = true;}
  function setNav(view) {
    $$(".nav-item").forEach(item => item.classList.toggle("active", item.dataset.view === (view === "detail" ? "conversations" : view)));
    $("#topbar-crumb").textContent = names[view] || "CQA";
    $("#nav-conversations-count").textContent = state.conversations.length;
    $("#nav-appeals-count").textContent = state.appeals.filter(a => a.status === "open").length;
    $("#shell").classList.remove("mobile-menu");
  }
  function setView(view) {
    state.view = view; state.inspectorHidden = false; state.detail = null;
    if(view === "review") ensureReviewEvidence();
    if (view === "checklist" && !state.draftChecklist && state.checklist) resetDraft();
    render();
  }
  function resetDraft() {state.draftChecklist = structuredClone(state.checklist);}
  function render() {
    setNav(state.view);
    const hasInspector = state.view === "detail" && !state.inspectorHidden;
    $("#shell").classList.toggle("with-inspector", hasInspector);
    inspector().hidden = !hasInspector;
    if (state.loading) {content().innerHTML = `<div class="panel skeleton">Загрузка данных из API...</div>`;return;}
    switch (state.view) {
      case "overview": content().innerHTML = renderOverview(); break;
      case "conversations": content().innerHTML = renderConversations(); break;
      case "detail": content().innerHTML = renderDetail(); break;
      case "checklist": content().innerHTML = renderChecklist(); break;
      case "appeals": content().innerHTML = renderAppeals(); break;
      case "review": content().innerHTML = renderReview(); break;
    }
    if (hasInspector) inspector().innerHTML = renderInspector();
  }
  function heading(eyebrow, title, subtitle, action = "") {return `<div class="page-heading"><div><div class="eyebrow">${h(eyebrow)}</div><h1 class="page-title">${h(title)}</h1><p class="page-subtitle">${h(subtitle)}</p></div>${action}</div>`;}
  const scoreView = n => isScore(n) ? `<span class="${n < 60 ? "score-negative" : n < 80 ? "score-low" : "score-large"}">${fmt(n)} <small>/ 100</small></span>` : `<span class="score-na">—</span>`;
  const channelBadge = c => `<span class="badge ${c === "voice call" ? "voice" : "chat"}">${c === "voice call" ? "◉ Звонок" : "▤ Чат"}</span>`;
  const langBadge = l => `<span class="badge ${String(l).toLowerCase()}">${h(l || "—")}</span>`;
  const grossBadge = gross => gross ? `<span class="badge error">! Нарушение</span>` : `<span class="badge success">Без нарушений</span>`;
  const statCard = (label, value, foot, icon, kind="") => `<div class="panel stat-card ${kind}"><div class="stat-label">${h(label)}</div><div class="stat-icon">${icon}</div><div class="stat-number">${h(value)}</div><div class="stat-foot">${h(foot)}</div></div>`;

  function renderOverview() {
    const all = state.conversations;
    const scored = all.filter(x => isScore(x.total_score));
    const average = scored.length ? scored.reduce((acc, x) => acc + x.total_score, 0)/scored.length : null;
    const open = state.appeals.filter(x => x.status === "open").length;
    const chats = all.filter(x => x.channel === "agent chat").length;
    const calls = all.filter(x => x.channel === "voice call").length;
    const languages = ["RU", "KZ", "EN"].map(x => ({key:x, value:all.filter(item=>item.language===x).length}));
    const recent = state.appeals.slice(0,4);
    return heading("АНАЛИТИКА / ОБЗОР", "Контроль качества диалогов", "Мониторинг DEV-набора и результатов AI-оценивания.", `<button class="btn primary" data-action="nav" data-view="conversations">Открыть диалоги →</button>`) + `
      <section class="grid-stats" aria-label="Ключевые показатели">
        ${statCard("Всего диалогов", String(all.length), "Из DEV-набора", "▤")}
        ${statCard("Оценено", String(scored.length), `${all.length ? Math.round(scored.length/all.length*100):0}% покрытия`, "◫", "success-stat")}
        ${statCard("Средняя оценка", average === null ? "—" : fmt(Math.round(average*100)/100), "По завершённым оценкам / 100", "◈")}
        ${statCard("Открытых апелляций", String(open), "Ожидают проверки", "◇", "danger-stat")}
      </section>
      <div class="overview-grid"><section class="panel"><div class="panel-head"><div><h2 class="panel-title">Структура обращений</h2><div class="panel-desc">Распределение по каналам и языкам</div></div></div><div class="panel-body h-bars">
        ${[{key:"Agent chat",value:chats,color:""},{key:"Voice call",value:calls,color:"green"},...languages.map((x,i)=>({key:`Язык ${x.key}`,value:x.value,color:["","green","amber"][i]}))].map(x=>`<div><div class="bar-line-top"><span>${h(x.key)}</span><strong>${x.value} / ${all.length}</strong></div><div class="bar-track"><div class="bar-fill ${x.color}" style="width:${all.length ? x.value/all.length*100 : 0}%"></div></div></div>`).join("")}
        <div class="metric-note">Используются реальные данные CSV, без демонстрационных значений.</div>
      </div></section><section class="panel"><div class="panel-head"><div><h2 class="panel-title">Последние апелляции</h2><div class="panel-desc">Обращения операционного контура</div></div><button class="link-button" data-action="nav" data-view="appeals">Все →</button></div><div class="panel-body">
        ${recent.length ? recent.map(a=>`<div class="activity-item"><span class="activity-icon">◈</span><div class="activity-text"><strong>${h(a.conversation_id)}</strong> · критерий ${a.criterion_id}<small>${h(niceDate(a.created_at))} · ${h(statusText(a.status))}</small></div></div>`).join("") : `<div class="empty"><div class="empty-icon">◇</div><strong>Апелляций пока нет</strong>Они появятся после отправки обращения из карточки диалога.</div>`}
      </div></section></div>
      <section class="panel overview-bottom"><div class="panel-head"><div><h2 class="panel-title">Диалоги DEV-набора</h2><div class="panel-desc">Быстрый переход к расшифровке и оценке</div></div><button class="link-button" data-action="nav" data-view="conversations">Полный список →</button></div>${renderTable(all.slice(0,5),true)}</section>`;
  }

  function filteredConversations() {
    const {q, channel, language, status} = state.filters;
    return state.conversations.filter(x => {
      if (q && !`${x.conversation_id} ${x.preview}`.toLowerCase().includes(q.toLowerCase())) return false;
      if (channel !== "all" && x.channel !== channel) return false;
      if (language !== "all" && x.language !== language) return false;
      if (status === "scored" && x.evaluation_status !== "scored") return false;
      if (status === "unscored" && x.evaluation_status === "scored") return false;
      if (status === "gross" && !x.gross_error) return false;
      return true;
    });
  }
  function renderConversations() {
    const all = filteredConversations();
    return heading("КОНТУР 1 / ДИАЛОГИ", "Реестр диалогов", "Все обращения из DEV CSV, включая чаты и голосовые звонки.", `<button class="btn" data-action="refresh">↻ Обновить данные</button>`) + `
      <div class="filter-bar"><label class="search-wrap"><span>⌕</span><input id="filter-q" aria-label="Поиск диалога" placeholder="Поиск по ID или тексту запроса" value="${h(state.filters.q)}" /></label>
        <select id="filter-channel" class="select-compact" aria-label="Канал"><option value="all">Все каналы</option><option value="agent chat" ${state.filters.channel==="agent chat"?"selected":""}>Agent chat</option><option value="voice call" ${state.filters.channel==="voice call"?"selected":""}>Voice call</option></select>
        <select id="filter-language" class="select-compact" aria-label="Язык"><option value="all">Все языки</option>${["RU","KZ","EN"].map(v=>`<option value="${v}" ${state.filters.language===v?"selected":""}>${v}</option>`).join("")}</select>
        <select id="filter-status" class="select-compact" aria-label="Статус"><option value="all">Все оценки</option><option value="scored" ${state.filters.status==="scored"?"selected":""}>Оценено</option><option value="unscored" ${state.filters.status==="unscored"?"selected":""}>Не оценено</option><option value="gross" ${state.filters.status==="gross"?"selected":""}>Нарушения</option></select>
      </div><section class="panel" id="conversations-panel">${renderTable(all)}</section>`;
  }
  function renderTable(rows, compact=false) {
    return `<div class="table-scroll"><table class="data-table"><thead><tr><th>Conversation ID</th><th>Канал</th><th>Язык</th><th>Запрос клиента</th><th>Оценка</th><th></th></tr></thead><tbody id="conversation-tbody">${rows.length ? rows.map(x => `<tr class="selectable" tabindex="0" data-action="open-conversation" data-id="${h(x.conversation_id)}"><td class="mono">${h(x.conversation_id)}</td><td>${channelBadge(x.channel)}</td><td>${langBadge(x.language)}</td><td><div class="table-preview" title="${h(x.preview)}">${h(x.preview || "Нет превью")}</div></td><td>${scoreView(x.total_score)}</td><td style="color:#80a4ce;font-size:16px">↗</td></tr>`).join("") : `<tr><td colspan="6"><div class="empty"><strong>Диалоги не найдены</strong>Попробуйте изменить фильтры.</div></td></tr>`}</tbody></table></div><div class="count-line">${compact ? "Первые 5 записей" : `Показано ${rows.length} из ${state.conversations.length} диалогов`}</div>`;
  }
  function refreshTable() {
    const panel = $("#conversations-panel");
    if (panel) panel.innerHTML = renderTable(filteredConversations());
  }
  function displayTimestamp(timestamp, channel) {
    if (channel !== "voice call") return h(timestamp);
    // Preserve the source ISO 8601 timestamp for voice calls (rubric UI requirement).
    return h(timestamp);
  }
  function audioUrl(value) {
    if (typeof value !== "string" || !value.trim()) return null;
    try {
      const url = new URL(value, window.location.href);
      return ["https:", "http:"].includes(url.protocol) ? url.href : null;
    } catch {return null;}
  }
  function renderDetail() {
    const detail = state.detail;
    if (!detail) return `<div class="panel skeleton">Загрузка диалога...</div>`;
    const {conversation:c, evaluation:e} = detail;
    const src = audioUrl(c.audio_url);
    return `<button class="back-link" data-action="nav" data-view="conversations">← Все диалоги</button>
      <div class="detail-head"><div><div class="eyebrow">КАРТОЧКА ДИАЛОГА</div><h1 class="detail-id">${h(c.conversation_id)}</h1><div class="metadata-row">${channelBadge(c.channel)}${langBadge(c.language)}<span class="badge">${c.messages.length} сообщений</span>${e && e.error ? '<span class="badge error">Ошибка AI</span>' : ''}</div></div>
        <div class="toolbar"><button class="btn" data-action="toggle-inspector">${state.inspectorHidden ? "Показать оценку" : "Скрыть оценку"}</button><button class="btn primary" data-action="evaluate" ${state.evaluating?"disabled":""}>${state.evaluating?"Оценивание...":"▶ Запустить AI-оценку"}</button></div></div>
      ${c.channel === "voice call" ? `<div class="audio-box"><div class="audio-box-title"><span>◉ Аудиозапись звонка</span><span class="badge">Источник: audio_url</span></div>${src ? `<audio id="call-audio" controls preload="metadata" src="${h(src)}"></audio><div class="audio-note">Нажмите на таймкод или доказательство для перехода к соответствующей отметке. <span id="audio-status" aria-live="polite"></span></div>` : `<div class="audio-note">Для этого звонка нет доступного HTTP(S) audio_url. Транскрипт остаётся доступен.</div>`}</div>` : ""}
      <section class="panel transcript-panel"><div class="transcript-head"><h2 class="panel-title">Транскрипт диалога</h2><span class="transcript-count">${c.messages.length} сообщений · ${c.channel === "voice call" ? "ISO 8601" : "MM:SS"}</span></div>
        <div class="transcript-list ${c.channel === "voice call" ? "voice-transcript" : ""}" id="transcript-list">${c.messages.length ? c.messages.map(m => `<article class="message ${h(m.speaker)}" id="msg-${h(m.id)}"><button class="message-time" data-action="jump" data-msg="${h(m.id)}" title="Перейти к таймкоду">${displayTimestamp(m.timestamp, c.channel)}</button><div class="message-body"><div class="role-label ${h(m.speaker)}"><span class="role-pip"></span>${h(m.speaker.toUpperCase())}<span class="criterion-micro">${h(m.id)}</span></div><div class="message-text">${h(m.text)}</div></div></article>`).join("") : `<div class="empty">Сообщений нет</div>`}</div></section>`;
  }
  function resultLabel(result) {
    if (result === null || result === undefined) return "—";
    if (result === "YES") return "ДА";
    if (result === "NO") return "НЕТ";
    return result;
  }
  function renderInspector() {
    if (!state.detail) return "";
    const e = state.detail.evaluation;
    const cfg = state.checklist;
    const gross = e?.gross_errors;
    const score = isScore(e?.total_score) ? e.total_score : null;
    return `<div class="inspector-header"><div><div class="inspector-heading">Результат оценки</div><div class="inspector-subtitle">Критерии и доказательства</div></div><button class="icon-button" aria-label="Скрыть панель" data-action="toggle-inspector">×</button></div>
      <div class="inspector-scroll"><div class="score-hero"><div class="score-hero-top"><span class="score-hero-label">ИТОГОВЫЙ БАЛЛ</span><span class="badge ${score === null ? "warning" : "success"}">${score===null?"Нет полной оценки":"Проверено"}</span></div><div class="score-hero-number">${score===null?"—":fmt(score)} <small>/ 100</small></div><div class="score-progress"><div class="score-progress-fill" style="width:${score===null?0:score}%"></div></div><div class="score-hero-foot"><span>N/A веса перераспределены</span><span>${cfg?.criteria.length||7} критериев</span></div></div>
      ${e?.pending_criteria?.length ? `<div class="callout warn">Не хватает оценки для критериев: ${e.pending_criteria.join(", ")}. Общий балл пока не рассчитывается.</div>` : ""}
      ${e?.error ? `<div class="callout error">${h(e.error)}</div>` : ""}
      <div class="section-label"><span>Детализация критериев</span><span>${e?.criteria?.length || 0} / ${cfg?.criteria.length || 7}</span></div>
      ${e?.criteria?.length ? e.criteria.map(item => {
        const open = state.expanded.has(item.id); const result = resultLabel(item.result);
        const cn = item.result === "NO" || item.result === 0 ? "no" : item.result === "N/A" || item.pending ? "na" : "";
        const awarded = item.pending ? "Ожидает оценки" : item.result === "N/A" ? "N/A — исключён из расчёта" : `${fmt(item.effective_points)} / ${fmt(item.effective_weight)} баллов после перераспределения · исходный вес ${item.weight}%`;
        return `<section class="criterion-card"><button class="criterion-top" data-action="expand-criterion" data-id="${item.id}" aria-expanded="${open}"><span class="criterion-index">${item.id.toString().padStart(2,"0")}</span><span class="criterion-title">${h(item.name)}</span><span class="criterion-value ${cn}">${h(result)}</span><span class="criterion-chevron">${open?"⌃":"⌄"}</span></button>${open ? `<div class="criterion-detail">${item.evidence?.length ? `<button class="explanation-link" data-action="jump" data-msg="${h(item.evidence[0].message_id)}" title="Перейти к доказательству">${h(item.explanation || "Обоснование пока отсутствует")} ↗</button>` : `<p>${h(item.explanation || "Обоснование пока отсутствует")}</p>`}<div class="criterion-micro">${h(awarded)}</div><div style="margin-top:8px">${(item.evidence || []).map(ev=>`<button class="evidence-link" data-action="jump" data-msg="${h(ev.message_id)}" title="${h(ev.text)}">↗ ${h(ev.message_id)} · ${h(ev.timestamp)}</button>`).join("") || '<span class="criterion-micro">Нет ссылки на реплику</span>'}${item.pending ? `<button class="btn small section-spaced" data-action="manual-score" data-id="${item.id}">Назначить оценку вручную</button>` : ""}</div></div>` : ""}</section>`;
      }).join("") : `<div class="empty"><strong>Оценка отсутствует</strong>Запустите оценивание диалога или загрузите готовый evaluations.jsonl.</div>`}
      ${gross ? `<div class="gross-alert ${gross.detected ? "" : "gross-clear"}"><strong>${gross.detected?"⚠ Обнаружена грубая ошибка":"✓ Грубых ошибок не выявлено"}</strong>${gross.explanation ? `<div>${h(gross.explanation)}</div>` : ""}${(gross.evidence || []).map(ev=>`<button class="evidence-link" data-action="jump" data-msg="${h(ev.message_id)}">↗ ${h(ev.message_id)}</button>`).join("")}</div>` : ""}
      <div class="inspector-actions"><button class="btn primary" data-action="open-appeal" ${!e?.criteria?.some(c=>!c.pending)?"disabled":""}>◈ Подать апелляцию</button></div></div>`;
  }

  function scoreTotal(draft) {return draft.criteria.reduce((sum, row)=>sum+Number(row.weight||0),0);}
  function renderChecklist() {
    const cfg = state.draftChecklist || state.checklist;
    if (!cfg) return `<div class="empty">Чек-лист не загружен</div>`;
    const total = scoreTotal(cfg);
    return heading("КОНТУР 1 / КОНФИГУРАЦИЯ", "Редактор чек-листа", "Управление критериями, весами, шкалами и применимостью N/A.", `<div class="toolbar"><button class="btn" data-action="reset-checklist">Сбросить</button><button class="btn primary" data-action="save-checklist">Сохранить изменения</button></div>`) + `
    <div class="callout"><strong>Базовая методика Freedom Broker:</strong> 7 оцениваемых критериев + отдельный флаг грубых ошибок. После изменения шкалы или добавления критерия прежнюю AI-оценку может потребоваться проверить вручную. Python AI-подсказка не меняется автоматически.</div>
    <section class="panel"><div class="checklist-top"><div><h2 class="panel-title">Критерии качества</h2><div class="panel-desc">Вес каждого критерия, уровни оценки, N/A</div></div><span class="badge ${total===100?"success":"error"}">Σ ${total}% ${total===100?"":"· требуется 100%"}</span></div>
    ${cfg.criteria.map(c=>`<div class="checklist-row"><span class="checklist-index">${c.id.toString().padStart(2,"0")}</span><div><div class="checklist-name">${h(c.name)}</div><div class="checklist-description">${h(c.description)}</div></div><div class="checklist-meta">Шкала<strong>${h(c.type === "binary" ? "ДА / НЕТ" : c.levels.join(" / "))}</strong></div><div class="checklist-meta">Вес<strong>${c.weight}%</strong></div><div class="checklist-meta">N/A<strong>${c.na_allowed?"Да":"Нет"}</strong></div><div><button class="icon-button" data-action="edit-criterion" data-id="${c.id}" title="Изменить критерий">✎</button>${c.id>7 ? `<button class="icon-button" data-action="delete-criterion" data-id="${c.id}" title="Удалить критерий">×</button>` : ""}</div></div>`).join("")}</section>
    <button class="checklist-add" data-action="add-criterion">+ Добавить критерий</button>
    <section class="panel section-spaced"><div class="panel-head"><div><h2 class="panel-title">08 · Грубые ошибки</h2><div class="panel-desc">Отдельный флаг, без веса в итоговом балле</div></div><span class="badge error">Не входит в 100 баллов</span></div><div class="panel-body layout-note">${h(cfg.gross_errors.description)} <button class="link-button" data-action="edit-gross">Изменить формулировку</button></div></section>`;
  }
  function statusText(status) {return ({open:"Ожидает проверки",confirmed:"Подтверждена",changed:"Оценка изменена"})[status] || status;}
  function statusBadge(status) {return `<span class="badge ${status==="open"?"warning":status==="changed"?"success":""}">${h(statusText(status))}</span>`;}
  function filteredAppeals() {return state.appeals.filter(a=>state.appealFilter==="all" || a.status === state.appealFilter);}
  function renderAppeals() {
    const arr = filteredAppeals();
    return heading("КОНТУР 2 / ОБРАЩЕНИЯ", "Апелляции", "История возражений по результатам оценки и решений проверяющего.", `<button class="btn" data-action="refresh">↻ Обновить</button>`) + `<div class="filter-bar"><select id="appeal-filter" aria-label="Статус апелляции"><option value="all">Все обращения</option><option value="open" ${state.appealFilter==="open"?"selected":""}>Ожидают проверки</option><option value="confirmed" ${state.appealFilter==="confirmed"?"selected":""}>Подтверждены</option><option value="changed" ${state.appealFilter==="changed"?"selected":""}>Оценка изменена</option></select><span class="layout-note">${arr.length} обращений</span></div>
    <section class="panel"><div class="table-scroll"><table class="data-table"><thead><tr><th>Диалог</th><th>Критерий</th><th>Обращение</th><th>Дата</th><th>Статус</th><th></th></tr></thead><tbody>
    ${arr.length ? arr.map(a=>`<tr class="selectable" data-action="go-review" data-id="${h(a.id)}"><td class="mono">${h(a.conversation_id)}</td><td>№ ${a.criterion_id} · ${h(a.criterion_name)}</td><td><div class="table-preview">${h(a.comment)}</div></td><td>${h(niceDate(a.created_at))}</td><td>${statusBadge(a.status)}</td><td>↗</td></tr>`).join("") : `<tr><td colspan="6"><div class="empty"><strong>Нет апелляций</strong>Чтобы подать обращение, откройте оцененный диалог и нажмите «Подать апелляцию».</div></td></tr>`}</tbody></table></div></section>`;
  }
  function getActiveAppeal() {return state.appeals.find(a=>a.id===state.activeAppealId) || state.appeals[0];}
  async function ensureReviewEvidence() {
    const appeal = getActiveAppeal();
    state.reviewMessages = [];
    if (!appeal || appeal.criterion_id !== 8) return;
    try {
      const result = await request(`/conversations/${encodeURIComponent(appeal.conversation_id)}`);
      if(getActiveAppeal()?.id===appeal.id){state.reviewMessages=result.conversation.messages;render();}
    }catch(error){toast(error.message,true);}
  }
  function availableResults(rule, selected) {
    if (!rule) return "";
    const choices = rule.type === "binary" ? ["YES", "NO"] : [...rule.levels];
    if (rule.na_allowed) choices.push("N/A");
    return choices.map(v=>`<option value="${h(v)}" ${String(v)===String(selected)?"selected":""}>${h(resultLabel(v))}</option>`).join("");
  }
  function renderReview() {
    const active = getActiveAppeal();
    const rule = active?.criterion_id === 8 ? {id:8,name:"Грубые ошибки",type:"binary",levels:[0,1],na_allowed:false} : state.checklist?.criteria.find(c=>c.id===active?.criterion_id);
    return heading("КОНТУР 2 / ПРОВЕРЯЮЩИЙ", "Проверка апелляций", "Рассмотрите спор, подтвердите оценку или измените её с обязательным обоснованием.") + `<div class="appeal-grid">
      <section class="panel appeal-list" aria-label="Список апелляций"><div class="panel-head"><h2 class="panel-title">Обращения · ${state.appeals.length}</h2></div>${state.appeals.length ? state.appeals.map(a=>`<button class="appeal-item ${a.id===active?.id?"active":""}" data-action="select-appeal" data-id="${h(a.id)}"><div class="appeal-item-head"><span>${h(a.conversation_id)} · № ${a.criterion_id}</span>${statusBadge(a.status)}</div><div class="checklist-description">${h(a.comment)}</div><div class="appeal-item-foot"><span>${h(niceDate(a.created_at))}</span><span>Открыть →</span></div></button>`).join("") : `<div class="empty"><div class="empty-icon">◇</div><strong>Нет обращений</strong>Апелляции появятся здесь.</div>`}</section>
      <section class="panel">${active ? `<div class="review-content"><div class="eyebrow">АПЕЛЛЯЦИЯ / ${h(active.id)}</div><h2 class="review-heading">${h(active.conversation_id)}</h2><p class="page-subtitle">Критерий ${active.criterion_id}: ${h(active.criterion_name)}</p><div class="review-criterion"><div class="split-metric">Исходный результат<strong>${h(resultLabel(active.original_result))}</strong></div>${statusBadge(active.status)}<button class="btn small" data-action="open-conversation" data-id="${h(active.conversation_id)}">↗ Диалог</button></div><div class="form-label">Комментарий к апелляции</div><div class="review-quote">${h(active.comment)}</div>
          ${active.status === "open" ? `<form id="review-form"><div class="form-label">Решение проверяющего</div><div class="review-radio"><label class="radio-card"><input type="radio" name="review-action" value="confirm" checked /> Подтвердить оценку</label><label class="radio-card"><input type="radio" name="review-action" value="change" /> Изменить оценку</label></div><div class="form-field" id="revised-result-field" hidden><label class="form-label" for="new-result">Новая оценка</label><select id="new-result" name="new-result">${availableResults(rule,active.original_result)}</select></div>${active.criterion_id===8?`<div class="form-field" id="gross-evidence-field" hidden><label class="form-label" for="gross-evidence">Доказательство грубого нарушения</label><select id="gross-evidence" style="width:100%"><option value="">Выберите реплику оператора</option>${state.reviewMessages.filter(m=>m.speaker==="operator").map(m=>`<option value="${h(m.id)}">${h(m.id)} · ${h(m.timestamp)} · ${h(m.text.slice(0,80))}</option>`).join("")}</select><div class="form-help">Если изменяете флаг на ДА, укажите реальную реплику оператора. Для НЕТ доказательство не требуется.</div></div>`:""}<div class="form-field"><label class="form-label" for="review-comment">Обоснование решения</label><textarea class="field-textarea" id="review-comment" placeholder="Укажите, почему оценка сохранена или изменена" maxlength="2000" required minlength="5"></textarea></div><button class="btn primary" type="submit">Сохранить решение</button></form>` : `<div class="review-resolution"><strong>Решение:</strong> ${h(statusText(active.status))}${active.revised_result!==null?` · новая оценка ${h(resultLabel(active.revised_result))}`:""}<p>${h(active.reviewer_comment || "")}</p><small>${h(niceDate(active.resolved_at))}</small></div>`}
        </div>` : `<div class="empty"><strong>Пока нет апелляций</strong>После подачи обращения его можно будет обработать здесь.</div>`}</section></div>`;
  }

  async function loadBootstrap() {
    state.loading = true; render();
    try {
      const data = await request("/bootstrap");
      state.conversations = data.conversations || [];
      state.checklist = data.checklist;
      state.appeals = data.appeals || [];
      state.loading = false; state.draftChecklist = null;
      $("#connection-label").textContent = "API подключён";
      hideNotice(); render();
    } catch (error) {
      state.loading = false;
      state.conversations = []; state.appeals = [];
      $("#connection-label").textContent = "API недоступен";
      showNotice(`Нет данных: ${error.message}`);
      render();
      toast(error.message, true);
    }
  }
  async function refreshKeepView() {
    const current = state.view, detailId = state.selectedId;
    try {
      const data = await request("/bootstrap");
      state.conversations = data.conversations || []; state.appeals = data.appeals || []; state.checklist = data.checklist;
      if (current === "detail" && detailId) {state.detail = await request(`/conversations/${encodeURIComponent(detailId)}`);}
      if (current === "checklist") resetDraft();
      hideNotice(); render();toast("Данные обновлены");
    } catch(error) {toast(error.message,true);}
  }
  async function openConversation(id, jumpId=null) {
    state.selectedId = id; state.view="detail";state.detail=null;state.inspectorHidden=false;render();
    try {
      state.detail = await request(`/conversations/${encodeURIComponent(id)}`);
      render();
      if (jumpId) jumpToMessage(jumpId);
    } catch (error) {toast(error.message, true); setView("conversations");}
  }
  async function evaluateSelected() {
    if (!state.selectedId || state.evaluating) return;
    state.evaluating=true; render();toast("Запрос отправлен в OpenRouter; оценивание может занять некоторое время");
    try {
      state.detail = await jsonRequest(`/conversations/${encodeURIComponent(state.selectedId)}/evaluate`, "POST", {});
      const summary = state.conversations.find(c=>c.conversation_id===state.selectedId);
      if (summary) {summary.total_score = state.detail.evaluation?.total_score;summary.evaluation_status = state.detail.evaluation?.pending_criteria?.length ? "pending" : "scored";summary.gross_error = !!state.detail.evaluation?.gross_errors?.detected;}
      toast("AI-оценка сохранена. Проверьте критерии и доказательства.");
    } catch (error) {toast(error.message, true);}
    finally {state.evaluating=false;render();}
  }
  document.addEventListener("error", event => {
    if (event.target?.id === "call-audio") {
      const label = $("#audio-status");
      if (label) label.textContent = "Не удалось загрузить аудио. Проверьте путь/ссылку, формат файла и доступ браузера.";
    }
  }, true);

  function timestampSeconds(timestamp, initial) {
    if (/^\d{1,2}:\d\d(?::\d\d)?$/.test(timestamp)) {
      const numbers = timestamp.split(":").map(Number);
      return numbers.reduce((sum, n)=>sum*60+n,0);
    }
    const one=Date.parse(timestamp), zero=Date.parse(initial);
    if (Number.isFinite(one) && Number.isFinite(zero)) return Math.max(0,(one-zero)/1000);
    return null;
  }
  function jumpToMessage(id) {
    if (!state.detail) return;
    const el = document.getElementById(`msg-${id}`);
    if (el) {el.scrollIntoView({behavior:"smooth",block:"center"});el.classList.remove("highlight");void el.offsetWidth;el.classList.add("highlight");}
    const audio = $("#call-audio");
    if (audio && state.detail.conversation.channel === "voice call") {
      const messages = state.detail.conversation.messages;
      const target = messages.find(m=>m.id===id);
      const seconds = target && timestampSeconds(target.timestamp, messages[0]?.timestamp);
      if (seconds !== null && Number.isFinite(seconds)) {
        const seek = () => {audio.currentTime = Math.min(seconds, Number.isFinite(audio.duration) ? audio.duration : seconds);};
        if (audio.readyState >= 1) seek(); else audio.addEventListener("loadedmetadata",seek,{once:true});
      }
    }
  }
  function openModal(title, body, buttons) {
    $("#modal-root").innerHTML = `<div class="modal-backdrop" id="modal-backdrop"><div role="dialog" aria-modal="true" aria-labelledby="modal-title" class="modal"><div class="modal-header"><h2 class="modal-title" id="modal-title">${h(title)}</h2><button class="icon-button" data-action="close-modal" aria-label="Закрыть">×</button></div><div class="modal-body">${body}</div><div class="modal-footer">${buttons}</div></div></div>`;
    $(".modal button, .modal input, .modal select, .modal textarea")?.focus();
  }
  const closeModal = () => $("#modal-root").replaceChildren();
  function showAppealModal() {
    const evaluation = state.detail?.evaluation;
    const items = (evaluation?.criteria || []).filter(x=>!x.pending);
    if (evaluation?.gross_errors) items.push({id:8,name:"Грубые ошибки",result:evaluation.gross_errors.detected?"YES":"NO"});
    if (!items.length) return toast("Для апелляции нужна готовая оценка",true);
    openModal("Подать апелляцию", `<div class="callout">Диалог <strong>${h(state.selectedId)}</strong>. Укажите критерий и конкретную причину несогласия с оценкой.</div><form id="appeal-form"><div class="form-field"><label class="form-label" for="appeal-criterion">Критерий</label><select id="appeal-criterion" required style="width:100%">${items.map(x=>`<option value="${x.id}">${x.id}. ${h(x.name)} (${h(resultLabel(x.result))})</option>`).join("")}</select></div><div class="form-field"><label class="form-label" for="appeal-comment">Комментарий</label><textarea class="field-textarea" id="appeal-comment" required minlength="5" maxlength="2000" placeholder="Почему вы считаете оценку неверной?"></textarea></div></form>`, `<button class="btn" data-action="close-modal">Отмена</button><button class="btn primary" data-action="submit-appeal">Отправить апелляцию</button>`);
  }
  async function submitAppeal() {
    const select = $("#appeal-criterion"), comment = $("#appeal-comment");
    if (!$("#appeal-form").reportValidity()) return;
    try {
      const value = await jsonRequest("/appeals","POST", {conversation_id:state.selectedId,criterion_id:Number(select.value),comment:comment.value});
      state.appeals.unshift(value);closeModal();render();toast("Апелляция сохранена и передана на проверку");
    } catch(error) {toast(error.message,true);}
  }
  function editCriterion(id, isNew = false) {
    const item = state.draftChecklist?.criteria.find(x=>x.id===id);
    if (!item) return;
    openModal(isNew?"Новый критерий":"Редактирование критерия", `<form id="criterion-form"><div class="form-field"><label class="form-label" for="criterion-name">Название</label><input class="field-input" id="criterion-name" required maxlength="120" value="${h(item.name)}" /></div><div class="form-field"><label class="form-label" for="criterion-description">Описание</label><textarea class="field-textarea" id="criterion-description" maxlength="1000">${h(item.description)}</textarea></div><div class="form-grid"><div class="form-field"><label class="form-label" for="criterion-weight">Вес, %</label><input class="field-input" id="criterion-weight" type="number" min="0" max="100" step="1" value="${item.weight}" required /></div><div class="form-field"><label class="form-label" for="criterion-type">Тип шкалы</label><select id="criterion-type"><option value="binary" ${item.type==="binary"?"selected":""}>ДА / НЕТ</option><option value="scale" ${item.type==="scale"?"selected":""}>Балльная</option></select></div></div><div class="form-field"><label class="form-label" for="criterion-levels">Шкала в баллах (через запятую)</label><input class="field-input" id="criterion-levels" value="${h(item.levels.join(", "))}" /><div class="form-help">Например: 0, 5, 10, 15, 20. Для ДА/НЕТ: 0, 10.</div></div><label class="toggle-row"><input type="checkbox" id="criterion-na" ${item.na_allowed?"checked":""} /> Разрешить статус N/A</label></form>`, `<button class="btn" data-action="close-modal">Отмена</button><button class="btn primary" data-action="save-criterion" data-id="${id}">Применить</button>`);
  }
  function editGross() {
    openModal("Грубые ошибки", `<div class="form-field"><label class="form-label" for="gross-description">Описание флага нарушений</label><textarea class="field-textarea" id="gross-description" minlength="10" maxlength="1000">${h(state.draftChecklist.gross_errors.description)}</textarea></div><div class="callout warn">Флаг остаётся отдельным и не входит в итоговый балл.</div>`, `<button class="btn" data-action="close-modal">Отмена</button><button class="btn primary" data-action="save-gross">Применить</button>`);
  }
  function saveGross() {
    const value = $("#gross-description").value.trim();
    if (value.length < 10) return toast("Описание слишком короткое",true);
    state.draftChecklist.gross_errors.description = value;
    closeModal();render();toast("Формулировка добавлена в черновик");
  }
  function showManualScore(id) {
    const rule = state.checklist?.criteria.find(c=>c.id===id);
    const messages = state.detail?.conversation.messages || [];
    if (!rule) return;
    openModal(`Ручная оценка · ${rule.name}`, `<form id="manual-form"><div class="callout">Оценка будет сохранена как решение проверяющего. Требуется основание из транскрипта.</div><div class="form-field"><label class="form-label" for="manual-result">Результат</label><select id="manual-result" style="width:100%">${availableResults(rule, rule.type === "binary" ? "YES" : rule.levels.at(-1))}</select></div><div class="form-field"><label class="form-label" for="manual-evidence">Доказательство</label><select id="manual-evidence" style="width:100%"><option value="">— Нет доказательства —</option>${messages.map(m=>`<option value="${h(m.id)}">${h(m.id)} · ${h(m.speaker)} · ${h(m.text.slice(0,80))}</option>`).join("")}</select></div><div class="form-field"><label class="form-label" for="manual-explanation">Обоснование</label><textarea id="manual-explanation" class="field-textarea" minlength="5" maxlength="2000" required placeholder="Почему выставлена именно такая оценка?"></textarea></div></form>`, `<button class="btn" data-action="close-modal">Отмена</button><button class="btn primary" data-action="submit-manual" data-id="${id}">Сохранить оценку</button>`);
  }
  async function submitManual(id) {
    if (!$("#manual-form").reportValidity()) return;
    const rule=state.checklist.criteria.find(c=>c.id===id);
    const raw=$("#manual-result").value;
    const result = raw === "N/A" ? "N/A" : rule.type === "binary" ? raw : Number(raw);
    const ev=$("#manual-evidence").value;
    try {
      const data=await jsonRequest(`/conversations/${encodeURIComponent(state.selectedId)}/criteria/${id}`,"PATCH",{result,explanation:$("#manual-explanation").value,evidence_message_ids:ev?[ev]:[]});
      state.detail=data;
      const summary=state.conversations.find(c=>c.conversation_id===state.selectedId);
      if(summary) {summary.total_score=data.evaluation.total_score;summary.evaluation_status=data.evaluation.pending_criteria.length?"pending":"scored";}
      closeModal();render();toast("Оценка вручную сохранена и пересчитана");
    }catch(error){toast(error.message,true);}
  }
  function saveCriterion(id) {
    const item = state.draftChecklist?.criteria.find(x=>x.id===id);
    if (!item || !$("#criterion-form").reportValidity()) return;
    const type = $("#criterion-type").value;
    const levelsText = $("#criterion-levels").value;
    const levels = levelsText.split(",").map(x=>Number(x.trim()));
    if (levels.some(x=>!Number.isInteger(x)) || levels.length<2 || levels[0]!==0 || [...new Set(levels)].length!==levels.length || !levels.every((v,i)=>i===0 || v>levels[i-1]) || levels.at(-1)<=0 || (type==="binary" && levels.length !==2)) {
      toast("Шкала должна быть возрастающей, начинаться с 0; для ДА/НЕТ укажите 2 значения",true);return;
    }
    Object.assign(item,{name:$("#criterion-name").value.trim(), description:$("#criterion-description").value.trim(), weight:Number($("#criterion-weight").value), type,levels,na_allowed:$("#criterion-na").checked});
    closeModal(); render();toast("Изменение внесено в черновик. Нажмите «Сохранить изменения».");
  }
  async function saveChecklist() {
    const total = scoreTotal(state.draftChecklist);
    if (total !== 100) return toast(`Сумма весов = ${total}%. Должно быть 100%.`, true);
    try {
      const saved = await jsonRequest("/checklist", "PUT", state.draftChecklist);
      state.checklist = saved;resetDraft();render();toast("Чек-лист сохранён. Результаты пересчитываются по новым весам.");
    } catch(error) {toast(error.message,true);}
  }
  async function submitReview(form) {
    const appeal = getActiveAppeal(); if (!appeal) return;
    const action = form.querySelector('[name="review-action"]:checked')?.value;
    const raw = form.querySelector("#new-result")?.value;
    const rule = state.checklist.criteria.find(x=>x.id===appeal.criterion_id);
    const value = raw === "N/A" ? "N/A" : rule.type === "binary" ? raw : Number(raw);
    const comment = form.querySelector("#review-comment").value;
    const evidence_message_id = form.querySelector("#gross-evidence")?.value || "";
    try {
      const updated = await jsonRequest(`/appeals/${encodeURIComponent(appeal.id)}/review`,"PATCH",{action,result:value,comment,evidence_message_id});
      state.appeals = state.appeals.map(x=>x.id===updated.id?updated:x);
      if (state.detail?.conversation.conversation_id === appeal.conversation_id) state.detail = await request(`/conversations/${encodeURIComponent(appeal.conversation_id)}`);
      const list = await request("/conversations");state.conversations=list;
      render();toast(action==="change"?"Оценка изменена и пересчитана":"Исходная оценка подтверждена");
    } catch(error) {toast(error.message,true);}
  }

  document.addEventListener("click", event=>{
    const target = event.target.closest("[data-action]");
    if (!target) return;
    const {action,id,view,msg} = target.dataset;
    switch(action){
      case "nav": setView(view); break;
      case "open-conversation": openConversation(id); break;
      case "refresh": refreshKeepView(); break;
      case "toggle-inspector": state.inspectorHidden=!state.inspectorHidden;render();break;
      case "expand-criterion": {const n=Number(id);state.expanded.has(n)?state.expanded.delete(n):state.expanded.add(n);render();break;}
      case "jump": jumpToMessage(msg);break;
      case "evaluate":evaluateSelected();break;
      case "open-appeal":showAppealModal();break;
      case "close-modal":closeModal();break;
      case "submit-appeal":submitAppeal();break;
      case "edit-criterion":editCriterion(Number(id));break;
      case "edit-gross":editGross();break;
      case "save-gross":saveGross();break;
      case "manual-score":showManualScore(Number(id));break;
      case "submit-manual":submitManual(Number(id));break;
      case "save-criterion":saveCriterion(Number(id));break;
      case "reset-checklist":resetDraft();render();toast("Черновик сброшен");break;
      case "save-checklist":saveChecklist();break;
      case "add-criterion":{
        const next=Math.max(8,...state.draftChecklist.criteria.map(c=>c.id))+1;
        state.draftChecklist.criteria.push({id:next,name:"Новый критерий",description:"",weight:0,type:"binary",levels:[0,10],na_allowed:false});
        render();editCriterion(next,true);break;
      }
      case "delete-criterion":state.draftChecklist.criteria=state.draftChecklist.criteria.filter(c=>c.id!==Number(id));render();break;
      case "go-review":state.activeAppealId=id;setView("review");break;
      case "select-appeal":state.activeAppealId=id;render();ensureReviewEvidence();break;
    }
  });
  document.addEventListener("input", event=>{
    if(event.target.id==="filter-q") {state.filters.q=event.target.value;refreshTable();}
  });
  document.addEventListener("change",event=>{
    const el=event.target;
    if(el.id==="filter-channel"||el.id==="filter-language"||el.id==="filter-status"){
      state.filters[{"filter-channel":"channel","filter-language":"language","filter-status":"status"}[el.id]]=el.value;refreshTable();
    }
    if(el.id==="appeal-filter") {state.appealFilter=el.value;render();}
    if(el.matches('[name="review-action"]')) {const field=$("#revised-result-field");if(field)field.hidden=el.value!=="change";}
    if(el.matches('[name="review-action"], #new-result')) {
      const result=$("#new-result"), action=$("[name='review-action']:checked");
      const field=$("#gross-evidence-field");
      if(field) field.hidden=!(action?.value==="change" && result?.value==="YES");
    }
  });
  document.addEventListener("submit",event=>{
    if (["review-form", "criterion-form", "appeal-form", "manual-form"].includes(event.target.id)) event.preventDefault();
    if(event.target.id==="review-form") submitReview(event.target);
  });
  document.addEventListener("keydown",event=>{
    if(event.key==="Escape") {closeModal();$("#shell").classList.remove("mobile-menu");}
    if((event.key==="Enter"||event.key===" ") && event.target.matches('tr[data-action="open-conversation"]')){event.preventDefault();openConversation(event.target.dataset.id);}
  });
  $("#mobile-nav").addEventListener("click",()=>$("#shell").classList.toggle("mobile-menu"));
  $("#notice-close").addEventListener("click",hideNotice);
  $("#modal-root").addEventListener("click", event=>{if(event.target.id==="modal-backdrop")closeModal();});
  $("#current-date").textContent = new Date().toLocaleDateString("ru-RU", {day:"numeric",month:"long",year:"numeric"});
  loadBootstrap();
})();
