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

// ------------------------------------------------------------------ каналы
// Какие поля канал реально читает и без каких он даёт ноль — из реестра, не из головы.
// Это не копия модели, а её описание: сами формулы по-прежнему только в движке.
const CH_USED = {"internal": ["name", "kind", "frk_tier", "budget", "response_rate", "cr_to_app", "reach_abs"], "perform": ["name", "kind", "frk_tier", "budget", "cpm", "ctr", "cr_to_app", "impressions"], "brand": ["name", "kind", "frk_tier", "budget", "cpm", "brand_uplift", "impressions", "effective_freq_target"]};
const CH_REQ = {"internal": ["reach_abs", "response_rate", "cr_to_app"], "perform": ["budget", "cpm", "ctr", "cr_to_app"], "brand": ["budget", "cpm", "brand_uplift"]};

const chbody = document.getElementById('chbody');
if (chbody) {
  const calcBtn = document.querySelector('.actions.sticky .btn');
  const warn = document.getElementById('chwarn');

  const refreshChannels = () => {
    let ready = 0, problems = [];
    Array.from(chbody.rows).forEach(row => {
      const kind = row.querySelector('select')?.value || 'perform';
      const nameEl = row.querySelector('input[name$=".name"]');
      const name = (nameEl?.value || '').trim();
      const used = CH_USED[kind] || [], req = CH_REQ[kind] || [];
      const missing = [];

      Array.from(row.querySelectorAll('input')).forEach(inp => {
        const key = (inp.name.split('.')[2] || '');
        if (key === 'name') return;
        const relevant = used.includes(key);
        // Поле, которого этот тип канала не читает, гасим: заполненный CPM у internal —
        // прямое приглашение думать, что он работает.
        inp.closest('td').classList.toggle('dim', !relevant);
        const need = relevant && req.includes(key) && !inp.value.trim();
        inp.classList.toggle('need-fill', need && !!name);
        if (need && name) missing.push(key);
      });

      if (!name) return;
      if (missing.length) problems.push(`«${name}»: не заполнено ${missing.join(', ')}`);
      else ready++;
    });

    if (calcBtn) {
      calcBtn.disabled = ready === 0;
      calcBtn.title = ready === 0 ? 'Нужен хотя бы один заполненный канал' : '';
    }
    warn.innerHTML = !ready
      ? '<div class="callout bad">Ни одного рабочего канала. Расчёт даст нули, а не оценку '
        + 'инициативы.' + (problems.length ? ' ' + escapeHtml(problems.join('; ')) + '.' : '')
        + '</div>'
      : (problems.length
          ? '<div class="callout">' + escapeHtml(problems.join('; '))
            + ' — эти каналы не дадут заявок.</div>'
          : '');
  };

  chbody.addEventListener('input', refreshChannels);
  chbody.addEventListener('change', refreshChannels);
  refreshChannels();
}

// ------------------------------------------------------------------ бриф: статус обязателен
// Утверждение с текстом, но без статуса движок отвергает — и отвергает конфиг ЦЕЛИКОМ,
// то есть блокирует все последующие шаги чужой ошибкой. На живом тесте человек застрял
// на пилоте, читая сообщение про бриф. Требуем статус здесь, где его и выбирают.
const claimTable = document.getElementById('claims');
if (claimTable) {
  const form = claimTable.closest('form');
  const sync = () => {
    claimTable.querySelectorAll('tr.claim').forEach(row => {
      const text = (row.querySelector('textarea[name$=".claim"]')?.value || '').trim();
      const sel = row.querySelector('select.status');
      if (!sel) return;
      sel.required = !!text;               // пустая строка остаётся необязательной
      sel.classList.toggle('need-fill', !!text && !sel.value);
    });
  };
  claimTable.addEventListener('input', sync);
  claimTable.addEventListener('change', sync);
  if (form) form.addEventListener('submit', sync);
  sync();
}

// ------------------------------------------------------------------ подтверждение удаления
// Текст берётся из data-атрибута, а не собирается в onsubmit: подтверждение с NPV
// и вердиктом внутри требует вложенных кавычек, на которых шаблон уже споткнулся.
document.querySelectorAll('form.js-confirm').forEach(f => {
  f.addEventListener('submit', e => {
    if (!confirm(f.dataset.confirm || 'Удалить?')) e.preventDefault();
  });
});
