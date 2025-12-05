
// static/main.js
(function ($) {
  "use strict";

  // ====== Thai locale sorting for DataTables ======
  if ($.fn.dataTable) {
    $.fn.dataTable.ext.type.order['thai-pre'] = function (d) {
      if (!d || d === '-') return '';
      // Remove HTML tags
      var text = d.replace(/<.*?>/g, '');
      return text.toLowerCase();
    };
    
    // Register Thai collation
    $.fn.dataTable.ext.order['thai-asc'] = function (a, b) {
      return a.localeCompare(b, 'th');
    };
    
    $.fn.dataTable.ext.order['thai-desc'] = function (a, b) {
      return b.localeCompare(a, 'th');
    };
  }

  // ====== ตำแหน่งคอลัมน์ ======
  const CHECKBOX_COL = 0;  // ช่องติ๊ก
  const ORDER_COL    = 3;  // เลข Order
  const TIME_COL     = 10; // เวลาที่ลูกค้าสั่ง

  // ====== เก็บสถานะการเลือก ======
  const selectedSet = new Set(); // string of order_line_id

  // ====== Utilities ======
  function getDataTable() {
    if (!$.fn.dataTable) return null;
    if (!$('#ordersTable').length) return null;
    return $.fn.dataTable.isDataTable('#ordersTable') ? $('#ordersTable').DataTable() : null;
  }

  function $pageRowChecks(includeUnchecked) {
    const table = getDataTable();
    if (!table) return $();
    const sel = 'input.js-row-check' + (includeUnchecked ? '' : ':checked');
    return $(table.rows({ page: 'current' }).nodes()).find(sel);
  }

  function updateBulkBar() {
    $('#selected-count').text(selectedSet.size);
    $('#bulk-bar').toggleClass('d-none', selectedSet.size === 0);
  }

  function getIdFromCheck($chk) {
    const v = $chk.data('id') ?? $chk.val();
    return (v === undefined || v === null) ? '' : String(v).trim();
  }

  function syncChecksOnPage() {
    $pageRowChecks(true).each(function () {
      const $c = $(this);
      const id = getIdFromCheck($c);
      $c.prop('checked', id && selectedSet.has(id));
    });
    const $allOnPage = $pageRowChecks(true);
    const $checkedOnPage = $pageRowChecks(false);
    $('#check-all').prop('checked', $allOnPage.length > 0 && $checkedOnPage.length === $allOnPage.length);
  }

  function currentFiltersQS() {
    const $f = $('#filterForm');
    const params = new URLSearchParams();
    function keep(name) {
      const $el = $f.find('[name="' + name + '"]');
      if ($el.length) {
        const v = ($el.val() || '').toString().trim();
        if (v !== '') params.set(name, v);
      }
    }
    if ($f.length) ['platform','shop_id','import_date','date_from','date_to','status'].forEach(keep);
    return params.toString();
  }

  function submitBulk(actionUrl) {
    if (selectedSet.size === 0) {
      alert('กรุณาเลือกรายการที่ต้องการกดรับ');
      return;
    }
    const qs = currentFiltersQS();
    const $form = $('<form/>', { method: 'POST', action: actionUrl + (qs ? ('?' + qs) : '') });
    Array.from(selectedSet).forEach(id => {
      $form.append($('<input/>', { type: 'hidden', name: 'order_line_ids[]', value: id }));
    });
    $('body').append($form);
    $form.trigger('submit');
  }

  // ====== Events ======
  $(document).on('change', '.js-row-check', function () {
    const id = getIdFromCheck($(this));
    if (!id) return;
    if (this.checked) selectedSet.add(id); else selectedSet.delete(id);
    syncChecksOnPage();
    updateBulkBar();
  });

  $(document).on('change', '#check-all', function (e) {
    e.stopPropagation();
    const checked = this.checked;
    $pageRowChecks(true).each(function () {
      const id = getIdFromCheck($(this));
      if (!id) return;
      if (checked) selectedSet.add(id); else selectedSet.delete(id);
      this.checked = checked;
    });
    updateBulkBar();
  });

  $(document).on('click', '#btn-bulk-accept', function (e) { e.preventDefault(); submitBulk('/bulk_accept'); });
  $(document).on('click', '#btn-bulk-cancel', function (e) { e.preventDefault(); submitBulk('/bulk_cancel'); });
  $(document).on('click', '#btn-bulk-clear',  function (e) {
    e.preventDefault();
    selectedSet.clear();
    $pageRowChecks(true).prop('checked', false);
    $('#check-all').prop('checked', false);
    updateBulkBar();
  });

  // ====== DataTables init ======
  $(document).ready(function () {
    if ($.fn.dataTable.isDataTable('#ordersTable')) {
      // มีคน init ไว้แล้ว → ใช้อันเดิม
      const tbl = $('#ordersTable').DataTable();
      tbl.on('draw', function(){ syncChecksOnPage(); updateBulkBar(); });
      return;
    }

    const table = $('#ordersTable').DataTable({
      pageLength: 50,
      orderFixed: { pre: [[ORDER_COL, 'asc']] },     // บังคับให้เลข Order เดียวกันติดกัน
      order: [[ORDER_COL, 'asc'], [TIME_COL, 'asc']],// รองลงมาเรียงตามเวลา
      stateSave: true,
      orderCellsTop: true,
      language: {
        emptyTable: "ไม่มีข้อมูล",
        info: "แสดง _START_ ถึง _END_ จากทั้งหมด _TOTAL_ รายการ",
        infoEmpty: "แสดง 0 ถึง 0 จากทั้งหมด 0 รายการ",
        infoFiltered: "(กรองจากทั้งหมด _MAX_ รายการ)",
        lengthMenu: "แสดง _MENU_ รายการ",
        loadingRecords: "กำลังโหลด...",
        processing: "กำลังประมวลผล...",
        search: "ค้นหา:",
        zeroRecords: "ไม่พบข้อมูลที่ตรงกัน",
        paginate: {
          first: "หน้าแรก",
          last: "หน้าสุดท้าย",
          next: "ถัดไป",
          previous: "ก่อนหน้า"
        },
        aria: {
          sortAscending: ": เรียงจากน้อยไปมาก",
          sortDescending: ": เรียงจากมากไปน้อย"
        }
      },
      columnDefs: [
        { targets: CHECKBOX_COL, orderable: false, searchable: false }, // ปิด sort/search ช่องติ๊ก
        { type: 'thai-pre', targets: '_all' } // Apply Thai sorting to all columns
      ],
      drawCallback: function () {
        const api = this.api();

        // ====== ตีกรอบหนากลุ่มเลข Order เดียวกัน ======
        const nodes = api.column(ORDER_COL, { page: 'current' }).nodes().toArray();
        $(nodes).removeClass('order-group-cell order-group-start order-group-end');
        if (nodes.length > 0) {
          let groups = [], start = 0, lastTxt = $.trim($(nodes[0]).text());
          for (let i = 1; i < nodes.length; i++) {
            const txt = $.trim($(nodes[i]).text());
            if (txt !== lastTxt) { groups.push([start, i - 1]); start = i; lastTxt = txt; }
          }
          groups.push([start, nodes.length - 1]);
          groups.forEach(([s, e]) => {
            for (let r = s; r <= e; r++) $(nodes[r]).addClass('order-group-cell');
            $(nodes[s]).addClass('order-group-start');
            $(nodes[e]).addClass('order-group-end');
          });
        }

        // ซิงก์ checkbox + bulk bar
        syncChecksOnPage();
        updateBulkBar();
      }
    });

    // ฟิลเตอร์รายคอลัมน์ (ถ้าใช้แถบ filters)
    var $filters = $('#ordersTable thead tr.filters').first();
    $('#ordersTable thead tr.filters').not(':first').remove();
    $filters.find('th').each(function (i) {
      $('input', this).off().on('keyup change', function () {
        if (table.column(i).search() !== this.value) table.column(i).search(this.value).draw();
      });
    });

    // เติมค่า input จาก stateSave
    var state = table.state.loaded();
    if (state && state.columns) {
      $filters.find('th').each(function (i) {
        var colState = state.columns[i];
        if (colState && colState.search && colState.search.search) $('input', this).val(colState.search.search);
      });
    }

    // ปุ่มล้างฟิลเตอร์
    $('#clearTableFilters').on('click', function () {
      table.state.clear(); table.search(''); table.columns().search(''); table.draw();
      $filters.find('input').val('');
    });

    // KPI ปุ่ม
    $('.kpi-filter').on('click', function () {
      const s = $(this).data('status');
      const url = new URL(window.location.href);
      if (s) url.searchParams.set('status', s); else url.searchParams.delete('status');
      window.location.href = url.toString();
    });

    // sync ครั้งแรก
    syncChecksOnPage();
    updateBulkBar();
  });

})(jQuery);


// ===== Clock (Realtime BE) =====
function updateThaiClock(){
  const el = document.getElementById('clockTH');
  if(!el) return;
  const now = new Date();
  const y = now.getFullYear() + 543;
  const pad = n => String(n).padStart(2,'0');
  el.textContent = `${pad(now.getDate())}/${pad(now.getMonth()+1)}/${y} ${pad(now.getHours())}:${pad(now.getMinutes())}`;
}
setInterval(updateThaiClock, 1000);
updateThaiClock();

// ===== Confirm on LOW_STOCK =====
function confirmAccept(status){
  if(status === "LOW_STOCK"){
    return confirm("สถานะ: สินค้าน้อย (1–3 ชิ้น)\nยืนยันว่าตรวจคลังแล้ว และต้องการกดรับใช่หรือไม่?");
  }
  return true;
}

// ===== วันทำการ (ให้สอดคล้องกับฝั่งเซิร์ฟเวอร์) =====
function isWeekend(d){ const w=d.getDay(); return w===0 || w===6; }
function isHoliday(d){ return false; }
function isBiz(d){ return !isWeekend(d) && !isHoliday(d); }
function addBizDays(d, n){
  const dt = new Date(d.getFullYear(), d.getMonth(), d.getDate());
  const step = n>=0 ? 1 : -1;
  let cnt = 0;
  while (cnt !== n){
    dt.setDate(dt.getDate()+step);
    if (isBiz(dt)) cnt += step;
  }
  return dt;
}
function diffBizDays(d1, d2){
  const a = new Date(d1.getFullYear(), d1.getMonth(), d1.getDate());
  const b = new Date(d2.getFullYear(), d2.getMonth(), d2.getDate());
  if (a.getTime()===b.getTime()) return 0;
  const step = (b>=a)?1:-1;
  let cur = new Date(a), cnt = 0;
  while (cur.getTime()!==b.getTime()){
    cur.setDate(cur.getDate()+step);
    if (isBiz(cur)) cnt += step;
  }
  return cnt;
}
function computeSLA(platform, orderISO){
  if(!orderISO) return '';
  const t = new Date(orderISO);
  const now = new Date();
  let cutoffHour = (platform==='Lazada') ? 11 : 12;
  const cutoff = new Date(t.getFullYear(), t.getMonth(), t.getDate(), cutoffHour, 0, 0);
  let due = (t <= cutoff)
    ? new Date(t.getFullYear(), t.getMonth(), t.getDate())
    : addBizDays(new Date(t.getFullYear(), t.getMonth(), t.getDate()), 1);
  while(!isBiz(due)) due = addBizDays(due, 1);
  const today0 = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const diff = diffBizDays(due, today0);
  if (diff > 0)  return `เลยกำหนด (${diff} วัน)`;
  if (diff === 0) return 'วันนี้';
  if (diff === -1) return 'พรุ่งนี้';
  return `อีก ${-diff} วัน`;
}