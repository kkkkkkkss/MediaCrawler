<template>
  <div class="cookie-manage">
    <!-- 操作栏 -->
    <div class="toolbar">
      <el-button type="primary" @click="showAddDialog = true">
        <el-icon><Plus /></el-icon> 添加 Cookie
      </el-button>
      <el-button @click="doReload" :loading="reloading">
        <el-icon><Refresh /></el-icon> 重新加载
      </el-button>
      <el-button @click="doValidate" :loading="validating">
        <el-icon><CircleCheck /></el-icon> 验证有效性
      </el-button>
      <el-button @click="doRefreshCookies" :loading="refreshing">
        <el-icon><RefreshRight /></el-icon> 刷新Cookie
      </el-button>
      <el-button
        type="danger"
        :disabled="!selectedRows.length"
        @click="doBatchRemove"
      >
        <el-icon><Delete /></el-icon> 批量删除 ({{ selectedRows.length }})
      </el-button>
      <el-select v-model="filterPlatform" placeholder="按平台筛选" clearable style="width:160px" @change="loadCookies">
        <el-option label="全部平台" value="" />
        <el-option v-for="(name, key) in PLATFORMS" :key="key" :label="name" :value="key" />
      </el-select>
    </div>

    <!-- Cookie 表格 -->
    <el-table
      :data="cookieList"
      stripe
      v-loading="loading"
      class="cookie-table"
      @selection-change="onSelectionChange"
      ref="cookieTableRef"
    >
      <el-table-column type="selection" width="45" />
      <el-table-column prop="id" label="Cookie ID" width="112" />
      <el-table-column label="平台" width="82">
        <template #default="{ row }">{{ PLATFORMS[row._platform] || row._platform }}</template>
      </el-table-column>
      <el-table-column label="类型" width="98">
        <template #default="{ row }">
          <el-tag :type="cookieTypeTag(row.cookie_type)" size="small">
            {{ cookieTypeName(row.cookie_type) }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="能力状态" width="158">
        <template #default="{ row }">
          <div class="ability-tags">
            <el-tag :type="isAccountValid(row) ? 'success' : 'danger'" size="small">
              账号{{ isAccountValid(row) ? '可用' : '失效' }}
            </el-tag>
            <el-tag :type="row.public_detail_valid ? 'success' : 'info'" size="small">
              详情{{ row.public_detail_valid ? '可用' : '不可用' }}
            </el-tag>
          </div>
        </template>
      </el-table-column>
      <el-table-column label="使用统计" width="92">
        <template #default="{ row }">
          <div class="metric-stack">
            <span>失败 {{ row.fatal_count ?? 0 }}</span>
            <span>使用 {{ row.use_count ?? 0 }}</span>
          </div>
        </template>
      </el-table-column>
      <el-table-column label="最近时间" width="220" show-overflow-tooltip>
        <template #default="{ row }">
          <div class="time-stack">
            <span>使用：{{ formatTime(row.last_used_at) }}</span>
            <span>验证：{{ formatTime(row.last_validated_at) }}</span>
            <span>刷新：{{ formatTime(row.last_refreshed_at) }}</span>
          </div>
        </template>
      </el-table-column>
      <el-table-column prop="note" label="备注" min-width="260" show-overflow-tooltip />
      <el-table-column label="操作" width="78" fixed="right">
        <template #default="{ row }">
          <el-popconfirm title="确认删除该 Cookie?" @confirm="doRemove(row._platform, row.id)">
            <template #reference>
              <el-button type="danger" size="small" text><el-icon><Delete /></el-icon></el-button>
            </template>
          </el-popconfirm>
        </template>
      </el-table-column>
    </el-table>

    <!-- 统计信息 -->
    <el-card shadow="never" class="stats-card" v-if="Object.keys(cookieStats).length">
      <template #header><span class="section-title">Cookie 池统计</span></template>
      <el-row :gutter="16">
        <el-col :span="4" v-for="(val, key) in cookieStats" :key="key">
          <div class="stat-block">
            <div class="stat-name">{{ PLATFORMS[key] || key }}</div>
            <div>账号 <el-tag type="success" size="small">{{ val.account_valid ?? val.valid }}</el-tag></div>
            <div>详情 <el-tag type="primary" size="small">{{ val.public_detail_valid ?? 0 }}</el-tag></div>
            <div>失效 <el-tag type="danger" size="small">{{ val.invalid }}</el-tag></div>
          </div>
        </el-col>
      </el-row>
    </el-card>

    <!-- 添加 Cookie 弹窗 -->
    <el-dialog v-model="showAddDialog" title="添加 Cookie" width="560px">
      <el-form :model="addForm" label-width="80px">
        <el-form-item label="平台">
          <el-select v-model="addForm.platform" placeholder="选择平台" style="width:100%">
            <el-option v-for="(name, key) in PLATFORMS" :key="key" :label="name" :value="key" />
          </el-select>
        </el-form-item>
        <el-form-item label="Cookie">
          <el-input
            v-model="addForm.cookie"
            type="textarea"
            :rows="4"
            placeholder="从浏览器 F12 开发者工具中复制 Cookie 字符串"
          />
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="addForm.note" placeholder="如：主号、测试号..." />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showAddDialog = false">取消</el-button>
        <el-button type="primary" :loading="addLoading" @click="doAdd">添加</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import {
  listCookies,
  addCookie,
  removeCookie,
  reloadCookies,
  validateCookies,
  refreshCookies,
  batchRemoveCookies,
} from '../api'
import { ElMessage, ElMessageBox } from 'element-plus'

const PLATFORMS = { dy: '抖音', ks: '快手', bili: 'B站', toutiao: '今日头条', xhs: '小红书', wb: '微博' }
const COOKIE_TYPES = { account: '账号', public_session: '公开会话', virtual: '虚拟' }
const COOKIE_TYPE_TAGS = { account: 'primary', public_session: 'warning', virtual: 'info' }

const loading = ref(false)
const reloading = ref(false)
const validating = ref(false)
const refreshing = ref(false)
const cookieList = ref([])
const cookieStats = ref({})
const filterPlatform = ref('')
const selectedRows = ref([])
const cookieTableRef = ref(null)

function onSelectionChange(rows) {
  selectedRows.value = rows
}

function isAccountValid(row) {
  return row.account_valid ?? row.valid
}

function cookieTypeName(type) {
  return COOKIE_TYPES[type] || type || '账号'
}

function cookieTypeTag(type) {
  return COOKIE_TYPE_TAGS[type] || 'info'
}

function formatTime(value) {
  return value || '-'
}

async function loadCookies() {
  loading.value = true
  try {
    const res = await listCookies(filterPlatform.value)
    cookieStats.value = res.stats || {}
    const list = []
    for (const [platform, cookies] of Object.entries(res.pool || {})) {
      for (const c of cookies) {
        list.push({ ...c, _platform: platform })
      }
    }
    cookieList.value = list
  } catch (e) {
    ElMessage.error('加载失败: ' + e.message)
  } finally {
    loading.value = false
  }
}

/* ── 添加 Cookie ── */
const showAddDialog = ref(false)
const addForm = reactive({ platform: 'dy', cookie: '', note: '' })
const addLoading = ref(false)

async function doAdd() {
  if (!addForm.cookie.trim()) return ElMessage.warning('请输入 Cookie')
  addLoading.value = true
  try {
    const res = await addCookie(addForm)
    ElMessage.success(res.message || '添加成功')
    showAddDialog.value = false
    addForm.cookie = ''
    addForm.note = ''
    await loadCookies()
  } catch (e) {
    ElMessage.error('添加失败: ' + e.message)
  } finally {
    addLoading.value = false
  }
}

/* ── 删除 Cookie ── */
async function doRemove(platform, cookieId) {
  try {
    await removeCookie({ platform, cookie_id: cookieId })
    ElMessage.success('删除成功')
    await loadCookies()
  } catch (e) {
    ElMessage.error('删除失败: ' + e.message)
  }
}

/* ── 批量删除 ── */
async function doBatchRemove() {
  if (!selectedRows.value.length) return
  try {
    await ElMessageBox.confirm(
      `确认删除选中的 ${selectedRows.value.length} 条 Cookie？`,
      '批量删除',
      { type: 'warning' }
    )
    const items = selectedRows.value.map(r => ({ platform: r._platform, cookie_id: r.id }))
    const res = await batchRemoveCookies(items)
    ElMessage.success(res.message || '批量删除成功')
    selectedRows.value = []
    await loadCookies()
  } catch { /* 用户取消 */ }
}

/* ── 重新加载 ── */
async function doReload() {
  reloading.value = true
  try {
    const res = await reloadCookies()
    ElMessage.success(res.message || '重新加载成功')
    await loadCookies()
  } catch (e) {
    ElMessage.error('重新加载失败: ' + e.message)
  } finally {
    reloading.value = false
  }
}

/* ── 验证/刷新 ── */
async function doValidate() {
  validating.value = true
  try {
    const res = await validateCookies(filterPlatform.value)
    ElMessage.success(res.message || '验证任务完成')
    await loadCookies()
  } catch (e) {
    ElMessage.error('验证失败: ' + e.message)
  } finally {
    validating.value = false
  }
}

async function doRefreshCookies() {
  refreshing.value = true
  try {
    const res = await refreshCookies(filterPlatform.value)
    ElMessage.success(res.message || '刷新任务已启动')
    await loadCookies()
  } catch (e) {
    ElMessage.error('刷新失败: ' + e.message)
  } finally {
    refreshing.value = false
  }
}

onMounted(loadCookies)
</script>

<style scoped>
.cookie-manage { max-width: 1600px; margin: 0 auto; }
.toolbar { display: flex; gap: 8px; margin-bottom: 14px; align-items: center; flex-wrap: wrap; }
.cookie-table { margin-bottom: 20px; }
.ability-tags { display: flex; gap: 4px; flex-wrap: wrap; align-items: center; }
.metric-stack,
.time-stack { display: flex; flex-direction: column; gap: 1px; line-height: 1.3; }
.metric-stack { color: #606266; }
.time-stack { color: #606266; font-size: 12px; }
.cookie-table :deep(.el-table__cell) { padding: 7px 0; }
.cookie-table :deep(.cell) { padding: 0 7px; }
.stats-card { margin-top: 16px; }
.section-title { font-weight: 600; }
.stat-block { text-align: center; padding: 8px 0; }
.stat-name { font-weight: 600; margin-bottom: 4px; color: #303133; }
</style>
