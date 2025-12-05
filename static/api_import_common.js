/**
 * Common API Import functionality
 * Configurable for Orders, Stock, Products, Sales, etc.
 *
 * Usage:
 * <script src="api_import_common.js"></script>
 * <script>
 *   window.apiImportCommon.init({
 *     apiEndpoint: '/import/orders/api',
 *     displayFields: ['order_id', 'sku', 'item_name'],
 *     fieldLabels: { 'order_id': 'เลข Order', ... },
 *     wmsFields: [{ key: 'order_id', label: 'เลข Order' }, ...]
 *   });
 * </script>
 */

(function () {
  'use strict';

  // Default configuration
  const DEFAULT_CONFIG = {
    moduleType: 'orders', // 'orders', 'stock', 'products', 'sales'
    apiEndpoint: '/import/orders/api',
    displayFields: ['order_id', 'sku', 'item_name', 'qty'],
    fieldLabels: {
      'order_id': 'เลข Order',
      'sku': 'SKU',
      'item_name': 'ชื่อสินค้า',
      'qty': 'จำนวน',
      'order_time': 'เวลาสั่งซื้อ',
      'shop_name': 'ชื่อร้าน',
      'logistic_type': 'ขนส่ง'
    },
    wmsFields: [
      { key: 'order_id', label: 'เลข Order' },
      { key: 'sku', label: 'SKU' },
      { key: 'item_name', label: 'ชื่อสินค้า' },
      { key: 'qty', label: 'จำนวน' },
      { key: 'order_time', label: 'เวลาสั่งซื้อ' },
      { key: 'shop_name', label: 'ชื่อร้าน' },
      { key: 'logistic_type', label: 'ประเภทขนส่ง' }
    ],
    requirePlatform: true, // Platform required?
    requireShopName: false, // Shop name required?
    redirectUrl: null // null = redirect to /?import_from=date&import_to=date
  };

  // Module state
  let config = { ...DEFAULT_CONFIG };
  let apiData = null;
  let mappingData = null;
  let previewData = null;

  // Elements (will be initialized in init())
  let btnFetchPreview = null;
  let btnPreviewCache = null;
  let btnEditMapping = null;
  let btnConfirmImport = null;
  let btnSaveMapping = null;
  let btnSaveConfig = null;
  let btnManageConfigs = null;
  let btnConfirmDelete = null;
  let savedConfigSelect = null;
  let previewModal = null;
  let editMappingModal = null;
  let manageConfigsModal = null;
  let viewConfigModal = null;
  let deleteConfigModal = null;
  let allConfigs = [];
  let configToDelete = null;

  /**
   * Public API: Initialize with custom configuration
   * @param {Object} customConfig - Configuration object
   */
  function init(customConfig = {}) {
    // Merge custom config with defaults
    config = { ...DEFAULT_CONFIG, ...customConfig };

    console.log('API Import Common: Initializing with config...', config);

    // Get elements
    btnFetchPreview = document.getElementById('btnFetchPreview');
    btnPreviewCache = document.getElementById('btnPreviewCache');
    btnEditMapping = document.getElementById('btnEditMapping');
    btnConfirmImport = document.getElementById('btnConfirmImport');
    btnSaveMapping = document.getElementById('btnSaveMapping');
    btnSaveConfig = document.getElementById('btnSaveConfig');
    btnManageConfigs = document.getElementById('btnManageConfigs');
    btnConfirmDelete = document.getElementById('btnConfirmDelete');
    savedConfigSelect = document.getElementById('savedConfigSelect');

    console.log('Elements found:', {
      btnFetchPreview: !!btnFetchPreview,
      btnPreviewCache: !!btnPreviewCache,
      btnEditMapping: !!btnEditMapping,
      btnConfirmImport: !!btnConfirmImport,
      btnSaveMapping: !!btnSaveMapping,
      btnSaveConfig: !!btnSaveConfig,
      btnManageConfigs: !!btnManageConfigs,
      btnConfirmDelete: !!btnConfirmDelete,
      savedConfigSelect: !!savedConfigSelect
    });

    // Initialize modals
    const previewModalEl = document.getElementById('previewModal');
    const editMappingModalEl = document.getElementById('editMappingModal');
    const manageConfigsModalEl = document.getElementById('manageConfigsModal');
    const viewConfigModalEl = document.getElementById('viewConfigModal');
    const deleteConfigModalEl = document.getElementById('deleteConfigModal');

    if (previewModalEl && typeof bootstrap !== 'undefined') {
      previewModal = new bootstrap.Modal(previewModalEl);
      console.log('✅ Preview modal initialized');
    } else {
      console.error('❌ Cannot initialize preview modal', {
        modalExists: !!previewModalEl,
        bootstrapExists: typeof bootstrap !== 'undefined'
      });
    }

    if (editMappingModalEl && typeof bootstrap !== 'undefined') {
      editMappingModal = new bootstrap.Modal(editMappingModalEl);
      console.log('✅ Edit mapping modal initialized');
    }

    if (manageConfigsModalEl && typeof bootstrap !== 'undefined') {
      manageConfigsModal = new bootstrap.Modal(manageConfigsModalEl);
      console.log('✅ Manage configs modal initialized');
    }

    if (viewConfigModalEl && typeof bootstrap !== 'undefined') {
      viewConfigModal = new bootstrap.Modal(viewConfigModalEl);
      console.log('✅ View config modal initialized');
    }

    if (deleteConfigModalEl && typeof bootstrap !== 'undefined') {
      deleteConfigModal = new bootstrap.Modal(deleteConfigModalEl);
      console.log('✅ Delete config modal initialized');
    }

    // Attach event listeners
    if (btnFetchPreview) {
      btnFetchPreview.addEventListener('click', handleFetchPreview);
      console.log('✅ Fetch preview button listener attached');
    } else {
      console.warn('⚠️ btnFetchPreview not found');
    }

    if (btnPreviewCache) {
      btnPreviewCache.addEventListener('click', handlePreviewCache);
      console.log('✅ Preview cache button listener attached');
    } else {
      console.warn('⚠️ btnPreviewCache not found');
    }

    if (btnEditMapping) {
      btnEditMapping.addEventListener('click', handleEditMapping);
    }

    if (btnConfirmImport) {
      btnConfirmImport.addEventListener('click', handleConfirmImport);
    }

    if (btnSaveMapping) {
      btnSaveMapping.addEventListener('click', handleSaveMapping);
    }

    if (btnSaveConfig) {
      btnSaveConfig.addEventListener('click', handleSaveConfig);
      console.log('✅ Save config button listener attached');
    }

    if (savedConfigSelect) {
      savedConfigSelect.addEventListener('change', handleConfigSelect);
      console.log('✅ Config select listener attached');
    }

    if (btnManageConfigs) {
      btnManageConfigs.addEventListener('click', openManageConfigsModal);
      console.log('✅ Manage configs button listener attached');
    }

    if (btnConfirmDelete) {
      btnConfirmDelete.addEventListener('click', confirmDeleteConfig);
      console.log('✅ Confirm delete button listener attached');
    }

    // Load saved configs
    loadSavedConfigs();

    console.log('API Import Common: Initialization complete');
  }

  // Fetch and preview API data
  async function handleFetchPreview(e) {
    e.preventDefault();

    const form = document.getElementById('apiImportForm');
    if (!form.checkValidity()) {
      form.reportValidity();
      return;
    }

    const formData = {
      api_url: document.getElementById('apiUrl').value,
      data_path: document.getElementById('dataPath').value,
      api_key: document.getElementById('apiKey').value,
      platform: document.getElementById('apiPlatform').value,
      shop_name: document.getElementById('apiShopName').value,
      use_cache: false
    };

    await fetchAndPreview(formData);
  }

  // Preview from cache
  async function handlePreviewCache(e) {
    e.preventDefault();

    const formData = {
      api_url: document.getElementById('apiUrl').value,
      data_path: document.getElementById('dataPath').value,
      api_key: document.getElementById('apiKey').value,
      platform: document.getElementById('apiPlatform').value,
      shop_name: document.getElementById('apiShopName').value,
      use_cache: true
    };

    if (!formData.api_url) {
      alert('กรุณากรอก API URL');
      return;
    }

    await fetchAndPreview(formData);
  }

  // Core fetch and preview function
  async function fetchAndPreview(formData) {
    // Check if modal exists
    if (!previewModal) {
      console.error('❌ previewModal is not initialized');
      alert('Preview Modal ไม่พร้อมใช้งาน กรุณารีเฟรชหน้าเว็บ');
      return;
    }

    showLoading();
    hideError();
    hideContent();
    previewModal.show();

    try {
      const response = await fetch(`${config.apiEndpoint}/preview`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify(formData)
      });

      const result = await response.json();

      if (!response.ok) {
        throw new Error(result.error || 'เกิดข้อผิดพลาดในการดึงข้อมูล');
      }

      // Store data
      apiData = result.data;
      mappingData = result.mapping;
      previewData = result.preview;

      // Display preview
      displayPreview(result);

      hideLoading();
      showContent();

    } catch (error) {
      console.error('Error fetching API:', error);
      showError(error.message);
      hideLoading();
    }
  }

  // Display preview data
  function displayPreview(result) {
    // Display mapping info
    const mappingInfo = document.getElementById('mappingInfo');
    let mappingHtml = '<div class="row g-2">';

    for (const [wmsField, apiField] of Object.entries(result.mapping)) {
      if (apiField) {
        mappingHtml += `
          <div class="col-md-6">
            <small>
              <span class="badge bg-light text-dark">${apiField}</span>
              <i class="bi bi-arrow-right mx-1"></i>
              <span class="badge bg-success">${wmsField}</span>
            </small>
          </div>
        `;
      }
    }
    mappingHtml += '</div>';
    mappingInfo.innerHTML = mappingHtml;

    // Display table header
    const headerRow = document.getElementById('previewTableHeader');
    headerRow.innerHTML = '';
    config.displayFields.forEach(field => {
      const th = document.createElement('th');
      th.textContent = getFieldLabel(field);
      headerRow.appendChild(th);
    });

    // Display table body (first 10 rows)
    const tbody = document.getElementById('previewTableBody');
    tbody.innerHTML = '';
    const previewRows = result.preview.slice(0, 10);

    previewRows.forEach(row => {
      const tr = document.createElement('tr');
      config.displayFields.forEach(field => {
        const td = document.createElement('td');
        td.textContent = row[field] || '-';
        tr.appendChild(td);
      });
      tbody.appendChild(tr);
    });

    // Display summary
    const summary = document.getElementById('previewSummary');
    summary.innerHTML = `
      จำนวนรายการทั้งหมด: <strong>${result.total_rows}</strong> รายการ
      (แสดง 10 รายการแรก) |
      Cache: <strong>${result.from_cache ? 'ใช้จาก Cache' : 'ดึงใหม่จาก API'}</strong>
      ${result.cache_expires ? ` | หมดอายุ: ${new Date(result.cache_expires).toLocaleString('th-TH')}` : ''}
    `;
  }

  // Edit mapping
  function handleEditMapping(e) {
    e.preventDefault();

    if (!mappingData) {
      alert('ไม่มีข้อมูล mapping');
      return;
    }

    // Build mapping form
    const formFields = document.getElementById('mappingFormFields');
    formFields.innerHTML = '';

    // Get available API fields from first data row
    const apiFields = apiData && apiData.length > 0 ? Object.keys(apiData[0]) : [];

    config.wmsFields.forEach(wmsField => {
      const div = document.createElement('div');
      div.className = 'col-md-6';

      let optionsHtml = '<option value="">- ไม่ระบุ -</option>';
      apiFields.forEach(apiField => {
        const selected = mappingData[wmsField.key] === apiField ? 'selected' : '';
        optionsHtml += `<option value="${apiField}" ${selected}>${apiField}</option>`;
      });

      div.innerHTML = `
        <label class="form-label">${wmsField.label}</label>
        <select class="form-select" name="mapping_${wmsField.key}" data-wms-field="${wmsField.key}">
          ${optionsHtml}
        </select>
      `;
      formFields.appendChild(div);
    });

    editMappingModal.show();
  }

  // Save mapping
  function handleSaveMapping(e) {
    e.preventDefault();

    const form = document.getElementById('mappingForm');
    const formData = new FormData(form);

    // Update mapping data
    const newMapping = {};
    for (const [key, value] of formData.entries()) {
      const wmsField = key.replace('mapping_', '');
      newMapping[wmsField] = value || null;
    }

    mappingData = newMapping;

    // Re-process preview data with new mapping
    reprocessPreviewWithMapping();

    editMappingModal.hide();

    // Update preview display
    displayPreview({
      mapping: mappingData,
      preview: previewData,
      total_rows: previewData.length,
      from_cache: true
    });
  }

  // Reprocess preview data with new mapping
  function reprocessPreviewWithMapping() {
    if (!apiData || !mappingData) return;

    previewData = apiData.map(row => {
      const mapped = {};
      for (const [wmsField, apiField] of Object.entries(mappingData)) {
        if (apiField && row.hasOwnProperty(apiField)) {
          mapped[wmsField] = row[apiField];
        }
      }
      return mapped;
    });
  }

  // Confirm and import
  async function handleConfirmImport(e) {
    e.preventDefault();

    // Check if we have the full API data
    if (!apiData || !mappingData) {
      alert('ไม่มีข้อมูลให้นำเข้า');
      return;
    }

    const platformEl = document.getElementById('apiPlatform');
    const shopNameEl = document.getElementById('apiShopName');

    const platform = platformEl ? platformEl.value : '';
    const shopName = shopNameEl ? shopNameEl.value : '';

    // Validate based on config
    if (config.requirePlatform && !platform) {
      alert('กรุณาเลือกแพลตฟอร์ม');
      return;
    }

    if (config.requireShopName && !shopName) {
      alert('กรุณากรอกชื่อร้าน');
      return;
    }

    // Map ALL API data (not just preview)
    const fullMappedData = apiData.map(row => {
      const mapped = {};
      for (const [wmsField, apiField] of Object.entries(mappingData)) {
        if (apiField && row.hasOwnProperty(apiField)) {
          mapped[wmsField] = row[apiField];
        }
      }
      return mapped;
    });

    const totalRecords = fullMappedData.length;
    const confirmMsg = `ยืนยันการนำเข้าข้อมูล ${totalRecords} รายการ?\n\n(แสดง preview 10 รายการ แต่จะนำเข้าทั้งหมด ${totalRecords} รายการ)`;
    if (!confirm(confirmMsg)) {
      return;
    }

    // Disable button
    btnConfirmImport.disabled = true;
    btnConfirmImport.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span>กำลังนำเข้า...';

    try {
      const response = await fetch(`${config.apiEndpoint}/import`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({
          platform: platform,
          shop_name: shopName,
          data: fullMappedData,  // Send ALL data, not just preview
          mapping: mappingData
        })
      });

      const result = await response.json();

      if (!response.ok) {
        throw new Error(result.error || 'เกิดข้อผิดพลาดในการนำเข้า');
      }

      // Success
      alert(`นำเข้าสำเร็จ: ${result.imported} รายการ`);
      previewModal.hide();

      // Redirect
      if (config.redirectUrl) {
        window.location.href = config.redirectUrl;
      } else if (result.import_date) {
        const date = result.import_date;
        window.location.href = `/?import_from=${date}&import_to=${date}`;
      } else {
        window.location.href = '/';
      }

    } catch (error) {
      console.error('Error importing:', error);
      alert('เกิดข้อผิดพลาด: ' + error.message);
    } finally {
      // Re-enable button
      btnConfirmImport.disabled = false;
      btnConfirmImport.innerHTML = '<i class="bi bi-check-circle"></i> Confirm และนำเข้าข้อมูล';
    }
  }

  // Helper functions
  function showLoading() {
    document.getElementById('previewLoading').style.display = 'block';
  }

  function hideLoading() {
    document.getElementById('previewLoading').style.display = 'none';
  }

  function showContent() {
    document.getElementById('previewContent').style.display = 'block';
  }

  function hideContent() {
    document.getElementById('previewContent').style.display = 'none';
  }

  function showError(message) {
    const errorDiv = document.getElementById('previewError');
    errorDiv.innerHTML = `<i class="bi bi-exclamation-triangle"></i> ${message}`;
    errorDiv.style.display = 'block';
  }

  function hideError() {
    document.getElementById('previewError').style.display = 'none';
  }

  function getFieldLabel(field) {
    return config.fieldLabels[field] || field;
  }

  // ===== Saved Config Management =====

  // Load saved configs from server
  async function loadSavedConfigs() {
    try {
      const response = await fetch('/api/configs');
      const result = await response.json();

      if (result.success && result.configs) {
        // Filter configs by moduleType
        allConfigs = result.configs.filter(cfg => {
          // If config has module_type, filter by it
          // Otherwise, show all configs (backward compatibility)
          return !cfg.module_type || cfg.module_type === config.moduleType;
        });
        populateConfigSelect();
        console.log('✅ Loaded', allConfigs.length, `saved configs for ${config.moduleType}`);
      }
    } catch (error) {
      console.error('Error loading saved configs:', error);
    }
  }

  // Populate dropdown with configs
  function populateConfigSelect() {
    if (!savedConfigSelect) return;

    // Clear existing options except first
    savedConfigSelect.innerHTML = '<option value="">-- เลือก Config ที่บันทึกไว้ --</option>';

    allConfigs.forEach(config => {
      const option = document.createElement('option');
      option.value = config.id;
      option.textContent = `${config.config_name} (${config.platform || 'N/A'})`;
      savedConfigSelect.appendChild(option);
    });
  }

  // Handle config selection
  async function handleConfigSelect(e) {
    const configId = e.target.value;

    if (!configId) {
      return;
    }

    try {
      const response = await fetch(`/api/configs/${configId}`);
      const result = await response.json();

      if (result.success && result.config) {
        const config = result.config;

        // Fill form
        document.getElementById('apiPlatform').value = config.platform || '';
        document.getElementById('apiUrl').value = config.api_url || '';
        document.getElementById('dataPath').value = config.data_path || '';
        document.getElementById('apiKey').value = config.api_key || '';

        console.log('✅ Loaded config:', config.config_name);

        // Optional: Show success message
        alert(`โหลด Config "${config.config_name}" สำเร็จ!`);
      }
    } catch (error) {
      console.error('Error loading config:', error);
      alert('เกิดข้อผิดพลาดในการโหลด Config');
    }
  }

  // Handle save config
  async function handleSaveConfig(e) {
    e.preventDefault();

    const shopNameEl = document.getElementById('apiShopName');
    const platformEl = document.getElementById('apiPlatform');
    const apiUrlEl = document.getElementById('apiUrl');
    const dataPathEl = document.getElementById('dataPath');
    const apiKeyEl = document.getElementById('apiKey');

    const shopName = shopNameEl ? shopNameEl.value : '';
    const platform = platformEl ? platformEl.value : '';
    const apiUrl = apiUrlEl ? apiUrlEl.value : '';
    const dataPath = dataPathEl ? dataPathEl.value : '';
    const apiKey = apiKeyEl ? apiKeyEl.value : '';

    // Validation based on config
    if (config.requireShopName && !shopName) {
      alert('กรุณากรอกชื่อร้านก่อนบันทึก');
      return;
    }

    if (config.requirePlatform && !platform) {
      alert('กรุณาเลือก Platform ก่อนบันทึก');
      return;
    }

    if (!apiUrl) {
      alert('กรุณากรอก API URL ก่อนบันทึก');
      return;
    }

    // Auto-save without prompt (config_name = shop_name_platform or api_url)
    const configName = shopName && platform
      ? `${shopName}_${platform}`
      : `${config.moduleType}_${new Date().getTime()}`;

    try {
      const response = await fetch('/api/configs', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({
          module_type: config.moduleType, // แยก config ตาม module
          config_name: configName,
          shop_name: shopName || null,
          platform: platform || null,
          api_url: apiUrl,
          data_path: dataPath,
          api_key: apiKey
        })
      });

      const result = await response.json();

      if (result.success) {
        // Show message from backend (บันทึกสำเร็จ or อัปเดตสำเร็จ)
        alert(result.message);

        // Reload configs
        await loadSavedConfigs();
      } else {
        throw new Error(result.error || 'บันทึกไม่สำเร็จ');
      }
    } catch (error) {
      console.error('Error saving config:', error);
      alert('เกิดข้อผิดพลาด: ' + error.message);
    }
  }

  // ===== Config Management =====

  // Open manage configs modal
  async function openManageConfigsModal(e) {
    e.preventDefault();

    console.log('openManageConfigsModal called');

    // Check if modal exists
    if (!manageConfigsModal) {
      console.error('❌ manageConfigsModal is not initialized');
      alert('Modal ไม่พร้อมใช้งาน กรุณารีเฟรชหน้าเว็บ');
      return;
    }

    // Show modal
    console.log('Showing modal...');
    manageConfigsModal.show();

    // Load configs table
    await loadConfigsTable();
  }

  // Load configs table
  async function loadConfigsTable() {
    // Show loading
    document.getElementById('configsLoading').style.display = 'block';
    document.getElementById('configsTableContainer').style.display = 'none';
    document.getElementById('configsEmpty').style.display = 'none';
    document.getElementById('configsError').style.display = 'none';

    try {
      const response = await fetch('/api/configs');
      const result = await response.json();

      if (result.success && result.configs) {
        // Filter by moduleType
        const configs = result.configs.filter(cfg => {
          return !cfg.module_type || cfg.module_type === config.moduleType;
        });

        // Hide loading
        document.getElementById('configsLoading').style.display = 'none';

        if (configs.length === 0) {
          // Show empty state
          document.getElementById('configsEmpty').style.display = 'block';
        } else {
          // Show table
          document.getElementById('configsTableContainer').style.display = 'block';

          // Populate table
          const tbody = document.getElementById('configsTableBody');
          tbody.innerHTML = '';

          configs.forEach(config => {
            const tr = document.createElement('tr');

            // Config Name
            const tdName = document.createElement('td');
            tdName.innerHTML = `<strong>${config.config_name}</strong>`;
            tr.appendChild(tdName);

            // Platform
            const tdPlatform = document.createElement('td');
            tdPlatform.innerHTML = `<span class="badge bg-primary">${config.platform || 'N/A'}</span>`;
            tr.appendChild(tdPlatform);

            // API URL
            const tdUrl = document.createElement('td');
            tdUrl.className = 'text-truncate';
            tdUrl.style.maxWidth = '300px';
            tdUrl.textContent = config.api_url;
            tdUrl.title = config.api_url;
            tr.appendChild(tdUrl);

            // Data Path
            const tdPath = document.createElement('td');
            tdPath.textContent = config.data_path || '-';
            tr.appendChild(tdPath);

            // Actions
            const tdActions = document.createElement('td');
            tdActions.className = 'text-center';
            tdActions.innerHTML = `
              <button class="btn btn-sm btn-info me-1" onclick="window.apiImportCommon.handleViewConfig(${config.id})">
                <i class="bi bi-eye"></i> View
              </button>
              <button class="btn btn-sm btn-danger" onclick="window.apiImportCommon.handleDeleteConfig(${config.id})">
                <i class="bi bi-trash"></i> Delete
              </button>
            `;
            tr.appendChild(tdActions);

            tbody.appendChild(tr);
          });

          // Update count
          document.getElementById('configsCount').textContent = configs.length;
        }
      } else {
        throw new Error('Failed to load configs');
      }
    } catch (error) {
      console.error('Error loading configs table:', error);
      document.getElementById('configsLoading').style.display = 'none';
      document.getElementById('configsError').style.display = 'block';
      document.getElementById('configsErrorMessage').textContent = error.message;
    }
  }

  // Handle view config
  async function handleViewConfig(configId) {
    try {
      const response = await fetch(`/api/configs/${configId}`);
      const result = await response.json();

      if (result.success && result.config) {
        const config = result.config;

        // Populate view modal
        const content = document.getElementById('viewConfigContent');
        content.innerHTML = `
          <div class="col-12">
            <h6 class="text-muted mb-3">ข้อมูลพื้นฐาน</h6>
          </div>
          <div class="col-md-6">
            <label class="form-label"><strong>ชื่อ Config:</strong></label>
            <p class="text-primary">${config.config_name}</p>
          </div>
          <div class="col-md-6">
            <label class="form-label"><strong>Platform:</strong></label>
            <p><span class="badge bg-primary">${config.platform || 'N/A'}</span></p>
          </div>
          <div class="col-md-6">
            <label class="form-label"><strong>Shop ID:</strong></label>
            <p>${config.shop_id || 'N/A'}</p>
          </div>
          <div class="col-md-6">
            <label class="form-label"><strong>สร้างโดย:</strong></label>
            <p>${config.created_by_user_id || 'N/A'}</p>
          </div>
          <div class="col-12">
            <label class="form-label"><strong>API URL:</strong></label>
            <p class="text-break">${config.api_url}</p>
          </div>
          <div class="col-12">
            <label class="form-label"><strong>Data Path:</strong></label>
            <p>${config.data_path || '-'}</p>
          </div>
          <div class="col-12">
            <label class="form-label"><strong>API Key:</strong></label>
            <p class="text-muted">${config.api_key ? '••••••••••••' : '-'}</p>
          </div>
          <div class="col-12">
            <hr>
            <h6 class="text-muted mb-3">ข้อมูลระบบ</h6>
          </div>
          <div class="col-md-6">
            <label class="form-label"><strong>สร้างเมื่อ:</strong></label>
            <p>${config.created_at || 'N/A'}</p>
          </div>
          <div class="col-md-6">
            <label class="form-label"><strong>แก้ไขล่าสุด:</strong></label>
            <p>${config.updated_at || 'N/A'}</p>
          </div>
        `;

        // Show view modal
        viewConfigModal.show();
      }
    } catch (error) {
      console.error('Error viewing config:', error);
      alert('เกิดข้อผิดพลาดในการดูรายละเอียด Config');
    }
  }

  // Handle delete config
  async function handleDeleteConfig(configId) {
    try {
      const response = await fetch(`/api/configs/${configId}`);
      const result = await response.json();

      if (result.success && result.config) {
        const config = result.config;

        // Store config to delete
        configToDelete = config;

        // Populate delete confirmation modal
        document.getElementById('deleteConfigName').textContent = config.config_name;
        document.getElementById('deleteConfigPlatform').textContent = config.platform || 'N/A';
        document.getElementById('deleteConfigShopId').textContent = config.shop_id || 'N/A';
        document.getElementById('deleteConfigUrl').textContent = config.api_url;

        // Show delete confirmation modal
        deleteConfigModal.show();
      }
    } catch (error) {
      console.error('Error preparing delete:', error);
      alert('เกิดข้อผิดพลาดในการเตรียมลบ Config');
    }
  }

  // Confirm delete config
  async function confirmDeleteConfig() {
    if (!configToDelete) {
      alert('ไม่พบ Config ที่ต้องการลบ');
      return;
    }

    // Disable button
    btnConfirmDelete.disabled = true;
    btnConfirmDelete.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span>กำลังลบ...';

    try {
      const response = await fetch(`/api/configs/${configToDelete.id}`, {
        method: 'DELETE'
      });

      const result = await response.json();

      if (result.success) {
        alert(`ลบ Config "${configToDelete.config_name}" สำเร็จ!`);

        // Close delete modal
        deleteConfigModal.hide();

        // Reload configs table
        await loadConfigsTable();

        // Reload dropdown
        await loadSavedConfigs();

        // Clear config to delete
        configToDelete = null;
      } else {
        throw new Error(result.error || 'ลบไม่สำเร็จ');
      }
    } catch (error) {
      console.error('Error deleting config:', error);
      alert('เกิดข้อผิดพลาด: ' + error.message);
    } finally {
      // Re-enable button
      btnConfirmDelete.disabled = false;
      btnConfirmDelete.innerHTML = '<i class="bi bi-trash"></i> ลบ Config';
    }
  }

  // Expose public API
  window.apiImportCommon = {
    init: init,
    handleViewConfig: handleViewConfig,
    handleDeleteConfig: handleDeleteConfig
  };

  console.log('API Import Common script loaded');

})();
