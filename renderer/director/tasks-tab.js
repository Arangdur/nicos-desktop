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

let tasksApiBase = null;
let currentTasksFilter = 'todas';

function escHtmlTasks(s) {
  return String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function initTasksTab(apiBase) {
  tasksApiBase = apiBase;
}

async function loadTasksTab() {
  const el = document.getElementById('tab-tareas');
  if (!el.querySelector('#tasks-filters')) {
    el.innerHTML = `
      <div class="card" id="tasks-filters">
        <div style="display:flex; gap:8px; flex-wrap:wrap;">
          <button class="secondary" data-tfilter="todas">Todas</button>
          <button class="secondary" data-tfilter="pending_approval">Esperando aprobación</button>
          <button class="secondary" data-tfilter="needs_information">Falta información</button>
          <button class="secondary" data-tfilter="needs_review">Necesitan revisión</button>
          <button class="secondary" data-tfilter="completed">Completadas</button>
        </div>
      </div>
      <div class="card"><div id="tasks-list"></div></div>
    `;
    el.querySelectorAll('[data-tfilter]').forEach((btn) => {
      btn.addEventListener('click', () => {
        currentTasksFilter = btn.dataset.tfilter;
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
    <div class="card" style="margin-bottom:10px;" data-task-id="${t.task_id}">
      <div style="display:flex; justify-content:space-between; align-items:center;">
        <div>
          <span class="tag ${ESTADO_TAG_CLASS[t.state] || 'proceso'}">${ESTADO_LABEL[t.state] || t.state}</span>
          <span style="margin-left:10px; font-size:13px; color:var(--muted);">
            ${escHtmlTasks(t.submitted_by)} · ${new Date(t.created_at).toLocaleString('es-AR')}
          </span>
        </div>
        <button class="secondary btn-toggle-detail" data-id="${t.task_id}">Ver detalle</button>
      </div>
      <div style="margin-top:8px; font-size:14px;">${escHtmlTasks(t.raw_text)}</div>
      <div class="task-detail" id="detail-${t.task_id}" style="display:none; margin-top:12px; border-top:1px solid var(--border); padding-top:12px;"></div>
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
      <div style="margin-top:12px; display:flex; gap:8px;">
        <button class="primary btn-approve" data-id="${taskId}" data-hash="${task.action_version_hash}" data-revision="${task.task_revision}">Aprobar (rev. ${task.task_revision})</button>
        <button class="secondary btn-reject" data-id="${taskId}">Rechazar</button>
      </div>
    `;
  } else if (task.state === 'needs_review' && reconciliacionInfo) {
    actionButtons = `
      <div class="error-box" style="margin-top:12px;">${escHtmlTasks(reconciliacionInfo.aviso || '')}</div>
      <div style="margin-top:8px; display:flex; gap:8px; flex-wrap:wrap;">
        <button class="primary btn-resolve" data-id="${taskId}" data-decision="confirm_executed">Confirmar ejecutada</button>
        <button class="secondary btn-resolve" data-id="${taskId}" data-decision="confirm_not_executed_retry">Confirmar NO ejecutada y reintentar</button>
        <button class="secondary btn-resolve" data-id="${taskId}" data-decision="cancel">Cancelar</button>
        <button class="secondary btn-resolve" data-id="${taskId}" data-decision="keep_in_review">Mantener en revisión</button>
      </div>
    `;
  } else if (task.state === 'needs_information' || task.state === 'needs_review') {
    actionButtons = `
      <div style="margin-top:12px;">
        <button class="secondary btn-reject" data-id="${taskId}">Cancelar tarea</button>
      </div>
    `;
  }

  detailEl.innerHTML = `
    <div><b>Dominio:</b> ${escHtmlTasks(task.domain) || '—'} &nbsp; <b>Intent:</b> ${escHtmlTasks(task.intent) || '—'} &nbsp; <b>Riesgo:</b> ${escHtmlTasks(task.risk_level) || '—'}</div>
    ${task.extracted_json ? `<pre style="background:#f4f6f9; padding:8px; border-radius:6px; font-size:12px; overflow-x:auto;">${escHtmlTasks(JSON.stringify(task.extracted_json, null, 2))}</pre>` : ''}
    ${task.error_message ? `<div class="error-box">${escHtmlTasks(task.error_message)}</div>` : ''}
    ${task.result_json ? `<pre style="background:#eef9f0; padding:8px; border-radius:6px; font-size:12px; overflow-x:auto;">${escHtmlTasks(JSON.stringify(task.result_json, null, 2))}</pre>` : ''}
    ${actionButtons}
    <div style="margin-top:12px;">
      <b style="font-size:12px; color:var(--muted); text-transform:uppercase;">Historial</b>
      <div style="font-size:12px; color:var(--muted); margin-top:6px;">
        ${data.events.map((e) => `${escHtmlTasks(e.from_state) || 'inicio'} → <b>${escHtmlTasks(e.to_state)}</b> (${escHtmlTasks(e.actor)}, ${new Date(e.created_at).toLocaleTimeString('es-AR')})`).join('<br>')}
      </div>
    </div>
  `;

  detailEl.querySelectorAll('.btn-approve').forEach((btn) => {
    btn.addEventListener('click', async () => {
      const res = await fetch(`${tasksApiBase}/api/v1/tasks/${btn.dataset.id}/approve`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          approved_action_hash: btn.dataset.hash,
          approved_task_revision: parseInt(btn.dataset.revision, 10),
        }),
      });
      const result = await res.json();
      if (!result.ok) {
        alert(result.error); // eslint-disable-line no-alert
      }
      renderTasksList();
    });
  });
  detailEl.querySelectorAll('.btn-reject').forEach((btn) => {
    btn.addEventListener('click', async () => {
      await fetch(`${tasksApiBase}/api/v1/tasks/${btn.dataset.id}/reject`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ reason: 'Rechazada desde la Bandeja de tareas' }),
      });
      renderTasksList();
    });
  });
  detailEl.querySelectorAll('.btn-resolve').forEach((btn) => {
    btn.addEventListener('click', async () => {
      const decision = btn.dataset.decision;
      let reason = null;
      if (decision === 'keep_in_review') {
        reason = prompt('Nota opcional sobre por qué queda en revisión:') || ''; // eslint-disable-line no-alert
      }
      const res = await fetch(`${tasksApiBase}/api/v1/tasks/${btn.dataset.id}/resolve-execution`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ decision, reason }),
      });
      const result = await res.json();
      if (!result.ok) {
        alert(result.error); // eslint-disable-line no-alert
      }
      renderTasksList();
    });
  });
}
