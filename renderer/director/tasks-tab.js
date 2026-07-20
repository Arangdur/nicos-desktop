// Bandeja de tareas + Aprobaciones — el corazón de NicOS 2.0 que faltaba en v0.1.
// `API` (la URL del sidecar local) se pasa desde app.js, que ya la resuelve al boot.

const ESTADO_LABEL = {
  received: 'Recibida', parsing: 'Procesando', classified: 'Clasificada',
  needs_information: 'Falta información', pending_approval: 'Esperando tu aprobación',
  ready: 'Lista', executing: 'Ejecutando', completed: 'Completada',
  failed: 'Falló', needs_review: 'Necesita revisión manual', cancelled: 'Cancelada',
};

const ESTADO_TAG_CLASS = {
  pending_approval: 'proceso', needs_information: 'proceso', needs_review: 'proceso',
  completed: 'resuelto', ready: 'resuelto', executing: 'resuelto',
  failed: 'nuevo', cancelled: 'nuevo',
};

// Traduce las claves del JSON extraído a etiquetas legibles -- lo que antes
// se mostraba como {"domain":"cfo","concept":"...","amount":45000} crudo.
const FIELD_LABEL = {
  domain: 'Dominio', intent: 'Tipo de acción', concept: 'Concepto',
  amount: 'Monto', date: 'Fecha', evidence: 'Evidencia / detalle',
  proveedor: 'Proveedor', nota: 'Nota',
};

const DOMAIN_LABEL = { cfo: 'CFO (personal de Nicolás)', abate: 'Abate (institucional)' };
const INTENT_LABEL = { register_expense: 'Registrar gasto', register_income: 'Registrar ingreso' };

let tasksApiBase = null;
let currentTasksFilter = 'todas';

function escHtmlTasks(s) {
  return String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function fmtFieldValue(key, value) {
  if (value === null || value === undefined || value === '') return '—';
  if (key === 'amount') return `$${Number(value).toLocaleString('es-AR')}`;
  if (key === 'domain') return DOMAIN_LABEL[value] || escHtmlTasks(value);
  if (key === 'intent') return INTENT_LABEL[value] || escHtmlTasks(value);
  return escHtmlTasks(value);
}

// Convierte el extracted_json/result_json (antes un <pre> con JSON crudo) en
// una lista de definición legible -- mismo dato, sin llaves ni comillas.
function renderKvGrid(obj) {
  const keys = Object.keys(obj || {}).filter((k) => obj[k] !== null && obj[k] !== undefined && obj[k] !== '');
  if (keys.length === 0) return '';
  return `
    <dl class="kv-grid">
      ${keys.map((k) => `<dt>${FIELD_LABEL[k] || escHtmlTasks(k)}</dt><dd>${fmtFieldValue(k, obj[k])}</dd>`).join('')}
    </dl>
  `;
}

// Convierte el historial de eventos (antes "from → to (actor, hora)" en texto
// plano) en un timeline visual, con las etiquetas de estado ya traducidas.
function renderTimeline(events) {
  if (!events || events.length === 0) return '';
  return `
    <ul class="timeline">
      ${events.map((e, i) => {
        const isLast = i === events.length - 1;
        const toLabel = ESTADO_LABEL[e.to_state] || e.to_state;
        const actorLabel = e.actor === 'system' ? 'automático' : e.actor === 'ai' ? 'IA' : escHtmlTasks(e.actor);
        return `
          <li class="${isLast ? 'is-terminal' : ''}">
            <div class="timeline-state">${toLabel}</div>
            <div class="timeline-meta">${actorLabel} · ${new Date(e.created_at).toLocaleString('es-AR', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })}</div>
          </li>
        `;
      }).join('')}
    </ul>
  `;
}

function initTasksTab(apiBase) {
  tasksApiBase = apiBase;
}

async function loadTasksTab() {
  const el = document.getElementById('tab-tareas');
  if (!el.querySelector('#tasks-filters')) {
    el.innerHTML = `
      <div class="card" id="tasks-filters">
        <div class="row-wrap">
          <button class="tab active" data-tfilter="todas">Todas</button>
          <button class="tab" data-tfilter="pending_approval">Esperando aprobación</button>
          <button class="tab" data-tfilter="needs_information">Falta información</button>
          <button class="tab" data-tfilter="needs_review">Necesitan revisión</button>
          <button class="tab" data-tfilter="completed">Completadas</button>
        </div>
      </div>
      <div id="tasks-list"></div>
    `;
    el.querySelectorAll('[data-tfilter]').forEach((btn) => {
      btn.addEventListener('click', () => {
        currentTasksFilter = btn.dataset.tfilter;
        el.querySelectorAll('[data-tfilter]').forEach((b) => b.classList.toggle('active', b === btn));
        renderTasksList();
      });
    });
  }
  await renderTasksList();
}

async function renderTasksList() {
  const listEl = document.getElementById('tasks-list');
  listEl.innerHTML = '<div class="empty">Cargando...</div>';

  const query = currentTasksFilter === 'todas' ? '' : `?state=${currentTasksFilter}`;
  const res = await fetch(`${tasksApiBase}/api/v1/tasks${query}`);
  const data = await res.json();
  if (!data.ok) {
    listEl.innerHTML = `<div class="error-box">${escHtmlTasks(data.error)}</div>`;
    return;
  }

  const pendingCount = (await (await fetch(`${tasksApiBase}/api/v1/tasks?state=pending_approval`)).json()).tasks?.length || 0;
  const badge = document.getElementById('badge-pending-count');
  if (badge) badge.textContent = pendingCount > 0 ? `(${pendingCount})` : '';

  if (data.tasks.length === 0) {
    listEl.innerHTML = '<div class="empty">No hay tareas con este filtro.</div>';
    return;
  }

  listEl.innerHTML = data.tasks.map((t) => `
    <div class="card" data-task-id="${t.task_id}">
      <div class="row-between">
        <div class="row">
          <span class="tag ${ESTADO_TAG_CLASS[t.state] || 'proceso'}">${ESTADO_LABEL[t.state] || t.state}</span>
          <span style="font-size:var(--text-sm); color:var(--muted);">
            ${escHtmlTasks(t.submitted_by)} · ${new Date(t.created_at).toLocaleString('es-AR')}
          </span>
        </div>
        <button class="secondary btn-toggle-detail" data-id="${t.task_id}">Ver detalle</button>
      </div>
      <div style="margin-top:var(--space-2); font-size:var(--text-md);">${escHtmlTasks(t.raw_text)}</div>
      <div class="task-detail" id="detail-${t.task_id}" style="display:none; margin-top:var(--space-4); border-top:1px solid var(--border-soft); padding-top:var(--space-4);"></div>
    </div>
  `).join('');

  listEl.querySelectorAll('.btn-toggle-detail').forEach((btn) => {
    btn.addEventListener('click', () => toggleTaskDetail(btn.dataset.id));
  });
}

async function toggleTaskDetail(taskId) {
  const detailEl = document.getElementById(`detail-${taskId}`);
  if (detailEl.style.display === 'block') {
    detailEl.style.display = 'none';
    return;
  }
  detailEl.style.display = 'block';
  detailEl.innerHTML = '<div class="empty">Cargando...</div>';

  const res = await fetch(`${tasksApiBase}/api/v1/tasks/${taskId}`);
  const data = await res.json();
  if (!data.ok) {
    detailEl.innerHTML = `<div class="error-box">${escHtmlTasks(data.error)}</div>`;
    return;
  }
  const task = data.task;

  // Si needs_review vino de una interrupción a mitad de ejecución (worker.
  // recover_orphaned_tasks dejó "reconciliacion" en el detail_json del último
  // evento), se muestran las 4 opciones de resolución en vez del botón
  // genérico de cancelar -- ver tasks.resolve_execution.
  let reconciliacionInfo = null;
  if (task.state === 'needs_review') {
    for (let i = data.events.length - 1; i >= 0; i--) {
      let detail = data.events[i].detail_json;
      if (typeof detail === 'string') {
        try { detail = JSON.parse(detail); } catch (e) { detail = null; }
      }
      if (detail && detail.reconciliacion) {
        reconciliacionInfo = detail;
        break;
      }
    }
  }

  let actionButtons = '';
  if (task.state === 'pending_approval') {
    actionButtons = `
      <div class="row-wrap" style="margin-top:var(--space-4);">
        <button class="primary btn-approve" data-id="${taskId}" data-hash="${task.action_version_hash}" data-revision="${task.task_revision}">Aprobar</button>
        <button class="danger btn-reject" data-id="${taskId}">Rechazar</button>
      </div>
    `;
  } else if (task.state === 'needs_review' && reconciliacionInfo) {
    actionButtons = `
      <div class="error-box" style="margin-top:var(--space-4);">${escHtmlTasks(reconciliacionInfo.aviso || '')}</div>
      <div class="row-wrap">
        <button class="primary btn-resolve" data-id="${taskId}" data-decision="confirm_executed">Confirmar ejecutada</button>
        <button class="secondary btn-resolve" data-id="${taskId}" data-decision="confirm_not_executed_retry">Confirmar NO ejecutada y reintentar</button>
        <button class="danger btn-resolve" data-id="${taskId}" data-decision="cancel">Cancelar</button>
        <button class="secondary btn-resolve" data-id="${taskId}" data-decision="keep_in_review">Mantener en revisión</button>
      </div>
    `;
  } else if (task.state === 'needs_information') {
    // v0.2.1-rc6 -- completar a mano lo que falta (típicamente el dominio,
    // cuando la IA lo dejó en "unknown") sin ningún llamado nuevo a IA, ver
    // worker.provide_missing_info.
    actionButtons = `
      <div style="margin-top:var(--space-4);">
        <label>Dominio</label>
        <select class="info-domain-select" data-id="${taskId}">
          <option value="">-- elegir --</option>
          <option value="cfo">CFO (personal de Nicolás)</option>
          <option value="abate">Abate (institucional / consultorio)</option>
        </select>
        <div class="row-wrap" style="margin-top:var(--space-3);">
          <button class="primary btn-provide-info" data-id="${taskId}">Completar y reclasificar</button>
          <button class="danger btn-reject" data-id="${taskId}">Cancelar tarea</button>
        </div>
      </div>
    `;
  } else if (task.state === 'needs_review') {
    actionButtons = `
      <div style="margin-top:var(--space-4);">
        <button class="danger btn-reject" data-id="${taskId}">Cancelar tarea</button>
      </div>
    `;
  }

  const summaryLine = [
    task.domain ? `<b>Dominio:</b> ${DOMAIN_LABEL[task.domain] || escHtmlTasks(task.domain)}` : null,
    task.intent ? `<b>Tipo:</b> ${INTENT_LABEL[task.intent] || escHtmlTasks(task.intent)}` : null,
    task.risk_level ? `<b>Riesgo:</b> ${task.risk_level === 'simple' ? 'simple (automático)' : 'requiere tu OK'}` : null,
  ].filter(Boolean).join(' &nbsp;·&nbsp; ');

  detailEl.innerHTML = `
    ${summaryLine ? `<div style="font-size:var(--text-sm); color:var(--muted); margin-bottom:var(--space-3);">${summaryLine}</div>` : ''}
    ${task.extracted_json ? renderKvGrid(task.extracted_json) : ''}
    ${task.error_message ? `<div class="error-box" style="margin-top:var(--space-3);">${escHtmlTasks(task.error_message)}</div>` : ''}
    ${task.result_json ? `<div class="success-box" style="margin-top:var(--space-3);">${escHtmlTasks(task.result_json.stdout || 'Ejecutado correctamente.')}</div>` : ''}
    ${actionButtons}
    <div style="margin-top:var(--space-5);">
      <h3 style="margin-bottom:var(--space-3);">Historial</h3>
      ${renderTimeline(data.events)}
    </div>
  `;

  detailEl.querySelectorAll('.btn-approve').forEach((btn) => {
    btn.addEventListener('click', async () => {
      const ok = await showConfirm('Aprobar esta tarea', 'Se va a ejecutar tal como está mostrado arriba.', { confirmLabel: 'Aprobar' });
      if (!ok) return;
      const res = await fetch(`${tasksApiBase}/api/v1/tasks/${btn.dataset.id}/approve`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          approved_action_hash: btn.dataset.hash,
          approved_task_revision: parseInt(btn.dataset.revision, 10),
        }),
      });
      const result = await res.json();
      if (!result.ok) {
        showToast(result.error, 'error');
      } else {
        showToast('Tarea aprobada.', 'success');
      }
      renderTasksList();
    });
  });
  detailEl.querySelectorAll('.btn-reject').forEach((btn) => {
    btn.addEventListener('click', async () => {
      const ok = await showConfirm('Rechazar esta tarea', 'Queda cancelada, sin ejecutar nada.', { confirmLabel: 'Rechazar', danger: true });
      if (!ok) return;
      await fetch(`${tasksApiBase}/api/v1/tasks/${btn.dataset.id}/reject`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ reason: 'Rechazada desde la Bandeja de tareas' }),
      });
      showToast('Tarea rechazada.');
      renderTasksList();
    });
  });
  detailEl.querySelectorAll('.btn-provide-info').forEach((btn) => {
    btn.addEventListener('click', async () => {
      const select = detailEl.querySelector(`.info-domain-select[data-id="${btn.dataset.id}"]`);
      const domain = select ? select.value : '';
      if (!domain) {
        showToast('Elegí un dominio antes de completar.', 'error');
        return;
      }
      const res = await fetch(`${tasksApiBase}/api/v1/tasks/${btn.dataset.id}/provide-info`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ updates: { domain } }),
      });
      const result = await res.json();
      if (!result.ok) {
        showToast(result.error, 'error');
      } else {
        showToast('Tarea reclasificada.', 'success');
      }
      renderTasksList();
    });
  });
  detailEl.querySelectorAll('.btn-resolve').forEach((btn) => {
    btn.addEventListener('click', async () => {
      const decision = btn.dataset.decision;
      let reason = null;
      if (decision === 'keep_in_review') {
        reason = await showPrompt('Nota (opcional)', 'Por qué queda en revisión.', { placeholder: 'Ej: esperando confirmación del banco...' });
        if (reason === null) return;
      }
      const res = await fetch(`${tasksApiBase}/api/v1/tasks/${btn.dataset.id}/resolve-execution`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ decision, reason }),
      });
      const result = await res.json();
      if (!result.ok) {
        showToast(result.error, 'error');
      } else {
        showToast('Resolución registrada.', 'success');
      }
      renderTasksList();
    });
  });
}
