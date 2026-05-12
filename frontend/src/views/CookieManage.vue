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
      <el-table-column prop="id" label="Cookie ID" width="130" />
      <el-table-column label="平台" width="100">
        <template #default="{ row }">{{ PLATFORMS[row._platform] || row._platform }}</template>
      </el-table-column>
      <el-table-column label="状态" width="90">
        <template #default="{ row }">
          <el-tag :type="row.valid ? 'success' : 'danger'" size="small">
            {{ row.valid ? '有效' : '失效' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="fatal_count" label="失败次数" width="100" />
      <el-table-column prop="note" label="备注" min-width="150" show-overflow-tooltip />
      <el-table-column label="Cookie 内容" min-width="200" show-overflow-tooltip>
        <template #default="{ row }">
          <span class="cookie-preview">{{ row.cookie?.substring(0, 80) }}...</span>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="100" fixed="right">
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
            <div>有效 <el-tag type="success" size="small">{{ val.valid }}</el-tag></div>
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
import { listCookies, addCookie, removeCookie, reloadCookies, batchRemoveCookies } from '../api'
import { ElMessage, ElMessageBox } from 'element-plus'

const PLATFORMS = { dy: '抖音', ks: '快手', bili: 'B站', toutiao: '今日头条', xhs: '小红书', wb: '微博' }

const loading = ref(false)
const reloading = ref(false)
const cookieList = ref([])
const cookieStats = ref({})
const filterPlatform = ref('')
const selectedRows = ref([])
const cookieTableRef = ref(null)

function onSelectionChange(rows) {
  selectedRows.value = rows
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

onMounted(loadCookies)
</script>

<style scoped>
.cookie-manage { max-width: 1200px; margin: 0 auto; }
.toolbar { display: flex; gap: 12px; margin-bottom: 16px; align-items: center; }
.cookie-table { margin-bottom: 20px; }
.cookie-preview { font-family: 'Consolas', monospace; font-size: 12px; color: #606266; }
.stats-card { margin-top: 16px; }
.section-title { font-weight: 600; }
.stat-block { text-align: center; padding: 8px 0; }
.stat-name { font-weight: 600; margin-bottom: 4px; color: #303133; }
</style>
