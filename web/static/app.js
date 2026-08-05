// Ни одной формулы модели здесь нет и быть не должно: возрастные ограничения и индекс
// уверенности приходят из движка по HTTP. Это тот же инвариант, что между calc.py и Excel —
// третья копия правил в JavaScript разошлась бы с ними при первой же правке.

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));

// ------------------------------------------------------------------ сегменты
const segform = $('#segform');
if (segform) {
  const refresh = async () => {
    $$('.seg').forEach(el => {
      const cb = $('input[name=segments]', el);
      const on = cb && cb.checked && !cb.disabled;
      el.classList.toggle('on', on);
      // Поля невыбранного сегмента остаются в DOM, только скрыты. Отключаем их, чтобы
      // они физически не уходили на сервер: иначе автозаполнение браузера способно
      // подсунуть в расчёт значения, которых человек не вводил.
      $$('.seg-body input', el).forEach(i => i.disabled = !on);
    });
    // Размеры не шлём: сервер берёт их из реестра сам. Отправляем только переопределения,
    // если человек их ввёл — иначе превью показывало бы не то, что уйдёт в расчёт.
    const segments = $$('input[name=segments]:checked').map(cb => {
      const el = cb.closest('.seg');
      const ov = $(`[name="seg.${cb.value}.target_size"]`, el);
      const out = { id: cb.value };
      if (ov && ov.value) out.target_size = ov.value;
      return out;
    });
    const notes = $('#segnotes');
    if (!segments.length) { notes.innerHTML = ''; return; }

    const r = await fetch('/api/segments/preview', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ product: segform.dataset.product, segments }),
    });
    const data = await r.json();
    notes.innerHTML = data.notes.map(n => `<div class="callout">${escapeHtml(n)}</div>`).join('');
  };
  segform.addEventListener('change', refresh);
  segform.addEventListener('input', debounce(refresh, 400));
  refresh();
}

// ------------------------------------------------------------------ индекс уверенности
const confIns = $$('.conf-in');
if (confIns.length) {
  const out = $('#confout');
  const refresh = debounce(async () => {
    const body = {};
    confIns.forEach(i => body[i.dataset.grade] = i.value || 0);
    const r = await fetch('/api/confidence', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const d = await r.json();
    if (d.index === null || d.index === undefined) {
      out.textContent = d.note || 'Проставьте грейды допущений.';
      out.classList.remove('low');
      return;
    }
    out.innerHTML = `<b>Индекс ${d.index}</b> — ${escapeHtml(d.tier)}. ${escapeHtml(d.note)}`;
    out.classList.toggle('low', d.index < 0.6);
  }, 300);
  confIns.forEach(i => i.addEventListener('input', refresh));
}

// ------------------------------------------------------------------ каналы
const addch = $('#addch');
if (addch) {
  addch.addEventListener('click', () => {
    const body = $('#chbody');
    const idx = body.rows.length;
    const last = body.rows[body.rows.length - 1];
    const row = last.cloneNode(true);
    $$('input, select', row).forEach(el => {
      el.name = el.name.replace(/^ch\.\d+\./, `ch.${idx}.`);
      if (el.tagName === 'INPUT') el.value = el.name.endsWith('.frk_tier') ? '4' : '';
    });
    body.appendChild(row);
  });
}

// ------------------------------------------------------------------ бриф панели
const copybrief = $('#copybrief');
if (copybrief) {
  copybrief.addEventListener('click', async () => {
    const ta = $('#brief');
    try {
      await navigator.clipboard.writeText(ta.value);
    } catch (e) {
      ta.select();                    // буфер недоступен без https — выделяем, копирует человек
      document.execCommand?.('copy');
    }
    copybrief.textContent = 'Скопировано';
    setTimeout(() => copybrief.textContent = 'Скопировать бриф', 1600);
  });
}

function debounce(fn, ms) {
  let t;
  return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
}
function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
