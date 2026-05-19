<template>
  <div class="task-list">
    <div class="toolbar">
      <el-button @click="refreshAll" :loading="refreshing">
        <el-icon><Refresh /></el-icon> 刷新全部
      </el-button>
      <el-button
        type="danger"
        :disabled="!selectedTasks.length"
        @click="doBatchDelete"
      >
        <el-icon><Delete /></el-icon> 批量删除 ({{ selectedTasks.length }})
      </el-button>
      <el-input
        v-model="filterTaskId"
        placeholder="输入 Task ID 查询"
        clearable
        style="width:260px"
        @keyup.enter="queryById"
      >
        <template #append>
          <el-button @click="queryById"><el-icon><Search /></el-icon></el-button>
        </template>
      </el-input>
    </div>

    <el-table
      :data="tasks"
      stripe
      class="task-table"
      v-loading="refreshing"
      @selection-change="onSelectionChange"
    >
      <el-table-column type="selection" width="45" :selectable="canSelect" />
      <el-table-column prop="task_id" label="任务 ID" width="180" />
      <el-table-column label="状态" width="110">
        <template #default="{ row }">
          <el-tag :type="statusType(row.status)">{{ statusText(row.status) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="进度" min-width="200">
        <template #default="{ row }">
          <el-progress
            :percentage="Math.round(row.progress || 0)"
            :status="row.status === 'completed' ? 'success' : row.status === 'failed' ? 'exception' : ''"
          />
          <span class="progress-detail">{{ row.processed || 0 }} / {{ row.total || '?' }}</span>
        </template>
      </el-table-column>
      <el-table-column prop="message" label="消息" min-width="180" show-overflow-tooltip />
      <el-table-column label="操作" width="280" fixed="right">
        <template #default="{ row }">
          <el-dropdown
            v-if="row.status === 'completed' && row.result_file"
            split-button
            type="primary"
            size="small"
            @click="downloadResult(row.task_id, 'excel')"
            @command="(cmd) => handleDownload(row.task_id, cmd)"
          >
            <el-icon><Download /></el-icon> 下载
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="excel">Excel 文件</el-dropdown-item>
                <el-dropdown-item command="json_file">JSON 文件</el-dropdown-item>
                <el-dropdown-item command="json_view" divided>查看 JSON 结果</el-dropdown-item>
                <el-dropdown-item command="comments_json">评论 JSON</el-dropdown-item>
                <el-dropdown-item command="comments_excel">评论 Excel</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
          <el-button
            v-if="row.status === 'running' || row.status === 'pending'"
            type="danger"
            size="small"
            plain
            @click="doCancelTask(row.task_id)"
          >
            终止
          </el-button>
          <el-button
            v-if="canSelect(row)"
            type="danger"
            size="small"
            text
            @click="doDeleteTask(row.task_id)"
          >
            <el-icon><Delete /></el-icon>
          </el-button>
          <el-button
            size="small"
            @click="toggleLogs(row.task_id)"
          >
            {{ activeLogTask === row.task_id ? '关闭日志' : '查看日志' }}
          </el-button>
        </template>
      </el-table-column>
    </el-table>

    <!-- JSON 结果查看弹窗 -->
    <el-dialog v-model="jsonDialogVisible" title="JSON 结果预览" width="80%" top="5vh">
      <pre class="json-preview">{{ jsonDialogContent }}</pre>
      <template #footer>
        <el-button @click="copyJson">复制 JSON</el-button>
        <el-button type="primary" @click="jsonDialogVisible = false">关闭</el-button>
      </template>
    </el-dialog>

    <!-- 普通任务日志面板 -->
    <el-card v-if="activeLogTask && !isReportTask(activeLogTask)" shadow="never" class="log-card">
      <template #header>
        <div class="log-header">
          <span class="section-title">任务日志 - {{ activeLogTask }}</span>
          <div>
            <el-button text size="small" @click="clearLogs">清空</el-button>
            <el-button text size="small" type="danger" @click="closeLogs">关闭</el-button>
          </div>
        </div>
      </template>
      <div class="log-container" ref="logContainerRef">
        <div v-for="(msg, idx) in logMessages" :key="idx" class="log-line">
          {{ msg }}
        </div>
        <div v-if="!logMessages.length" class="log-empty">暂无日志...</div>
      </div>
    </el-card>

    <!-- 举报任务详情面板（report- 开头的任务） -->
    <el-card v-if="activeLogTask && isReportTask(activeLogTask)" shadow="never" class="log-card">
      <template #header>
        <div class="log-header">
          <span class="section-title">
            举报任务详情 - {{ activeLogTask }}
            <el-tag :type="rpStatusType" size="small" style="margin-left:8px">{{ rpStatusText }}</el-tag>
          </span>
          <div>
            <el-button v-if="rpCompleted" type="success" size="small" @click="rpDownloadZip">下载截图ZIP</el-button>
            <el-button v-if="rpCompleted" type="primary" size="small" @click="rpDownloadExcel">导出Excel</el-button>
            <el-button text size="small" type="danger" @click="closeLogs">关闭</el-button>
          </div>
        </div>
      </template>

      <!-- 进度+指标 -->
      <el-progress
        :percentage="rpProgress"
        :status="rpCompleted ? 'success' : rpFailed ? 'exception' : ''"
        :stroke-width="14"
        style="margin-bottom:12px"
      />
      <el-descriptions :column="4" border size="small" style="margin-bottom:12px">
        <el-descriptions-item label="总次数">{{ rpTotal }}</el-descriptions-item>
        <el-descriptions-item label="已完成">{{ rpProcessed }}</el-descriptions-item>
        <el-descriptions-item label="进度">{{ rpProgress }}%</el-descriptions-item>
        <el-descriptions-item label="状态">{{ rpMessage }}</el-descriptions-item>
      </el-descriptions>

      <!-- 截图+日志 -->
      <el-row :gutter="16">
        <el-col :span="10">
          <h4 style="margin-bottom:8px">实时截图预览</h4>
          <div class="rp-screenshot-preview">
            <img v-if="rpScreenshot" :src="'data:image/png;base64,' + rpScreenshot" style="max-width:100%;max-height:360px;border-radius:4px" />
            <div v-else style="color:#909399;text-align:center;padding:40px 0">
              {{ rpRunning ? '等待截图...' : '暂无截图' }}
            </div>
          </div>
        </el-col>
        <el-col :span="14">
          <h4 style="margin-bottom:8px">
            实时日志
            <el-tag v-if="rpRunning" size="small" type="warning" style="margin-left:8px">更新中...</el-tag>
          </h4>
          <div class="log-container" ref="logContainerRef">
            <div v-for="(msg, idx) in logMessages" :key="idx" class="log-line">{{ msg }}</div>
            <div v-if="rpRunning && !logMessages.length" class="log-line" style="color:#909399">等待任务启动...</div>
          </div>
        </el-col>
      </el-row>

      <!-- 结果明细表格 -->
      <template v-if="rpCompleted && rpResultData.length">
        <h4 style="margin:16px 0 10px">举报结果明细 (点击行展开查看截图)</h4>
        <el-table :data="rpResultData" border stripe size="small" row-key="rowKey"
          :expand-row-keys="rpExpandedRows" @expand-change="onRpExpandChange">
          <el-table-column type="expand">
            <template #default="{ row }">
              <div style="padding:12px 16px">
                <div v-if="row._preImg || row._postImg" style="display:flex;gap:24px;flex-wrap:wrap">
                  <div v-if="row._preImg" style="text-align:center">
                    <p style="font-size:12px;color:#606266;font-weight:600;margin:0 0 4px">提交前</p>
                    <img :src="'data:image/png;base64,' + row._preImg" style="max-width:480px;max-height:320px;border:1px solid #dcdfe6;border-radius:6px" />
                  </div>
                  <div v-if="row._postImg" style="text-align:center">
                    <p style="font-size:12px;color:#606266;font-weight:600;margin:0 0 4px">提交后</p>
                    <img :src="'data:image/png;base64,' + row._postImg" style="max-width:480px;max-height:320px;border:1px solid #dcdfe6;border-radius:6px" />
                  </div>
                </div>
                <div v-else-if="row._loadingImg" style="color:#909399">加载截图中...</div>
                <div v-else style="color:#909399">无截图</div>
              </div>
            </template>
          </el-table-column>
          <el-table-column label="链接" prop="url" min-width="200" show-overflow-tooltip />
          <el-table-column label="平台" width="80" align="center">
            <template #default="{ row }">{{ rpPlatNames[row.platform] || row.platform }}</template>
          </el-table-column>
          <el-table-column label="账号" prop="cookie_id" width="100" align="center" />
          <el-table-column label="理由" prop="reason" width="100" align="center" />
          <el-table-column label="结果" width="80" align="center">
            <template #default="{ row }">
              <el-tag :type="row.success ? 'success' : 'danger'" size="small">{{ row.success ? '成功' : '失败' }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="失败原因" prop="error_msg" min-width="150" show-overflow-tooltip />
          <el-table-column label="耗时" width="80" align="center">
            <template #default="{ row }">{{ row.elapsed_sec }}s</template>
          </el-table-column>
        </el-table>
      </template>
    </el-card>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, watch, nextTick } from 'vue'
import { useRoute } from 'vue-router'
import {
  getTaskProgress, downloadTaskResult, downloadTaskComments, getTaskJsonResult,
  cancelTask, deleteTask, batchDeleteTasks,
  getReportProgress, getReportResult, getReportScreenshot,
  downloadReportScreenshots, downloadReportExcel,
} from '../api'
import { ElMessage, ElMessageBox } from 'element-plus'

const route = useRoute()
const tasks = ref([])
const refreshing = ref(false)
const filterTaskId = ref('')
const logMessages = ref([])
const activeLogTask = ref(null)
const logContainerRef = ref(null)
const pollingTimers = ref({})
const logOffsets = ref({})
const selectedTasks = ref([])

const STATUS_MAP = {
  pending: { text: '排队中', type: 'info' },
  running: { text: '执行中', type: '' },
  completed: { text: '已完成', type: 'success' },
  failed: { text: '失败', type: 'danger' },
  cancelled: { text: '已取消', type: 'warning' },
}
const statusText = (s) => STATUS_MAP[s]?.text || s
const statusType = (s) => STATUS_MAP[s]?.type || 'info'

/* 只有终态任务可选中删除 */
function canSelect(row) {
  return ['completed', 'failed', 'cancelled'].includes(row.status)
}

function onSelectionChange(rows) {
  selectedTasks.value = rows
}

async function pollTask(taskId) {
  try {
    const offset = logOffsets.value[taskId] || 0
    // 举报任务和普通任务调用不同的 API
    const res = isReportTask(taskId)
      ? await getReportProgress(taskId, offset)
      : await getTaskProgress(taskId, offset)
    const idx = tasks.value.findIndex(t => t.task_id === taskId)
    if (idx >= 0) {
      tasks.value[idx] = { ...tasks.value[idx], ...res, type: isReportTask(taskId) ? 'report' : tasks.value[idx].type }
    } else {
      tasks.value.unshift({ ...res, type: isReportTask(taskId) ? 'report' : undefined })
    }
    saveTasks()

    if (activeLogTask.value === taskId && res.logs && res.logs.length) {
      logMessages.value.push(...res.logs)
      logOffsets.value[taskId] = res.log_total
      nextTick(() => scrollLogToBottom())
    } else if (res.log_total) {
      logOffsets.value[taskId] = res.log_total
    }

    if (res.status === 'running' || res.status === 'pending') {
      if (!pollingTimers.value[taskId]) {
        pollingTimers.value[taskId] = setInterval(() => pollTask(taskId), 3000)
      }
    } else {
      clearInterval(pollingTimers.value[taskId])
      delete pollingTimers.value[taskId]
    }
  } catch {
    // 静默处理（任务可能已被删除）
  }
}

function toggleLogs(taskId) {
  if (activeLogTask.value === taskId) {
    closeLogs()
  } else {
    activeLogTask.value = taskId
    logMessages.value = []
    logOffsets.value[taskId] = 0
    rpProgress.value = 0; rpTotal.value = 0; rpProcessed.value = 0
    rpMessage.value = ''; rpScreenshot.value = null; rpResultData.value = []
    rpExpandedRows.value = []
    if (rpPollTimer) { clearTimeout(rpPollTimer); rpPollTimer = null }

    if (isReportTask(taskId)) {
      rpPollProgress()
    } else {
      fetchFullLogs(taskId)
    }
  }
}

async function fetchFullLogs(taskId) {
  try {
    const res = await getTaskProgress(taskId, 0)
    if (res.logs) {
      logMessages.value = [...res.logs]
      logOffsets.value[taskId] = res.log_total
    }
  } catch { /* ignore */ }
}

function closeLogs() {
  activeLogTask.value = null
  logMessages.value = []
  if (rpPollTimer) { clearTimeout(rpPollTimer); rpPollTimer = null }
  rpResultData.value = []; rpExpandedRows.value = []
  rpScreenshot.value = null
}

function clearLogs() {
  logMessages.value = []
}

function scrollLogToBottom() {
  if (logContainerRef.value) {
    logContainerRef.value.scrollTop = logContainerRef.value.scrollHeight
  }
}

async function doCancelTask(taskId) {
  try {
    await ElMessageBox.confirm('确定要终止此任务？', '确认', { type: 'warning' })
    const res = await cancelTask(taskId)
    if (res.success) {
      ElMessage.success('任务已终止')
      await pollTask(taskId)
    } else {
      ElMessage.warning(res.message)
    }
  } catch { /* 用户取消确认 */ }
}

/* ── 单个删除 ── */
async function doDeleteTask(taskId) {
  try {
    await ElMessageBox.confirm('确认删除该任务记录？', '删除', { type: 'warning' })
    await deleteTask(taskId)
    tasks.value = tasks.value.filter(t => t.task_id !== taskId)
    saveTasks()
    ElMessage.success('任务已删除')
  } catch { /* 用户取消 */ }
}

/* ── 批量删除 ── */
async function doBatchDelete() {
  if (!selectedTasks.value.length) return
  try {
    await ElMessageBox.confirm(
      `确认删除选中的 ${selectedTasks.value.length} 个任务？`,
      '批量删除',
      { type: 'warning' }
    )
    const ids = selectedTasks.value.map(t => t.task_id)
    const res = await batchDeleteTasks(ids)
    ElMessage.success(res.message || '批量删除成功')
    // 从前端列表中移除
    const idSet = new Set(ids)
    tasks.value = tasks.value.filter(t => !idSet.has(t.task_id))
    selectedTasks.value = []
    saveTasks()
  } catch { /* 用户取消 */ }
}

async function queryById() {
  const id = filterTaskId.value.trim()
  if (!id) return
  await pollTask(id)
}

async function refreshAll() {
  if (!tasks.value.length) {
    ElMessage.info('暂无任务，请先在「链接检测」页提交任务')
    return
  }
  refreshing.value = true
  for (const t of tasks.value) {
    await pollTask(t.task_id)
  }
  refreshing.value = false
}

async function downloadResult(taskId, format = 'excel') {
  try {
    const res = await downloadTaskResult(taskId, format)
    const url = window.URL.createObjectURL(new Blob([res.data]))
    const a = document.createElement('a')
    a.href = url
    const disposition = res.headers['content-disposition'] || ''
    const ext = format === 'json' ? '.json' : '.xlsx'
    a.download = getDownloadFilename(disposition, `${taskId}${ext}`)
    a.click()
    window.URL.revokeObjectURL(url)
    ElMessage.success('下载成功')
  } catch (e) {
    ElMessage.error('下载失败: ' + e.message)
  }
}

/* JSON 结果查看弹窗 */
const jsonDialogVisible = ref(false)
const jsonDialogContent = ref('')

async function viewJsonResult(taskId) {
  try {
    const data = await getTaskJsonResult(taskId)
    jsonDialogContent.value = JSON.stringify(data, null, 2)
    jsonDialogVisible.value = true
  } catch (e) {
    ElMessage.error('获取 JSON 失败: ' + e.message)
  }
}

async function downloadComments(taskId, format) {
  try {
    const res = await downloadTaskComments(taskId, format)
    const url = window.URL.createObjectURL(new Blob([res.data]))
    const a = document.createElement('a')
    a.href = url
    const disposition = res.headers['content-disposition'] || ''
    const ext = format === 'excel' ? '.xlsx' : '.json'
    a.download = getDownloadFilename(disposition, `${taskId}_comments${ext}`)
    a.click()
    window.URL.revokeObjectURL(url)
    ElMessage.success('评论下载成功')
  } catch (e) {
    if (e.response && e.response.status === 404) {
      ElMessage.warning('无评论数据（未开启评论抓取或该任务无评论）')
    } else {
      ElMessage.error('下载失败: ' + e.message)
    }
  }
}

function copyJson() {
  navigator.clipboard.writeText(jsonDialogContent.value)
    .then(() => ElMessage.success('已复制到剪贴板'))
    .catch(() => ElMessage.error('复制失败'))
}

function handleDownload(taskId, command) {
  switch (command) {
    case 'excel': downloadResult(taskId, 'excel'); break
    case 'json_file': downloadResult(taskId, 'json'); break
    case 'json_view': viewJsonResult(taskId); break
    case 'comments_json': downloadComments(taskId, 'json'); break
    case 'comments_excel': downloadComments(taskId, 'excel'); break
  }
}

function getDownloadFilename(disposition, fallback) {
  if (!disposition) return fallback

  // FastAPI/浏览器可能返回 filename* 或带引号的 filename；这里统一解析，避免引号被保存成首尾下划线。
  const encodedMatch = disposition.match(/filename\*\s*=\s*(?:UTF-8'')?([^;]+)/i)
  if (encodedMatch) {
    return cleanDownloadFilename(encodedMatch[1], fallback, true)
  }

  const plainMatch = disposition.match(/filename\s*=\s*([^;]+)/i)
  if (plainMatch) {
    return cleanDownloadFilename(plainMatch[1], fallback, false)
  }

  return fallback
}

function cleanDownloadFilename(rawName, fallback, decode) {
  let name = String(rawName || '').trim()
  if (!name) return fallback

  // 去掉 Content-Disposition 标准引号，只保留真正的文件名本体。
  if (
    (name.startsWith('"') && name.endsWith('"')) ||
    (name.startsWith("'") && name.endsWith("'"))
  ) {
    name = name.slice(1, -1)
  }

  if (decode) {
    try {
      name = decodeURIComponent(name)
    } catch {
      // 保留原始文件名，避免因异常编码导致下载失败
    }
  }

  return name.trim() || fallback
}

function saveTasks() {
  sessionStorage.setItem('mc_tasks', JSON.stringify(tasks.value))
}

/* ═══════ 举报任务详情面板 ═══════ */
const rpPlatNames = { dy: '抖音', wb: '微博', ks: '快手', toutiao: '今日头条' }

function isReportTask(taskId) {
  return taskId && taskId.startsWith('report-')
}

const rpProgress = ref(0)
const rpTotal = ref(0)
const rpProcessed = ref(0)
const rpMessage = ref('')
const rpScreenshot = ref(null)
const rpResultData = ref([])
const rpExpandedRows = ref([])
let rpPollTimer = null

const rpRunning = computed(() => {
  const t = tasks.value.find(t => t.task_id === activeLogTask.value)
  return t && ['pending', 'running'].includes(t.status)
})
const rpCompleted = computed(() => {
  const t = tasks.value.find(t => t.task_id === activeLogTask.value)
  return t && t.status === 'completed'
})
const rpFailed = computed(() => {
  const t = tasks.value.find(t => t.task_id === activeLogTask.value)
  return t && ['failed', 'cancelled'].includes(t.status)
})
const rpStatusText = computed(() => {
  const t = tasks.value.find(t => t.task_id === activeLogTask.value)
  return t ? (STATUS_MAP[t.status]?.text || t.status) : ''
})
const rpStatusType = computed(() => {
  const t = tasks.value.find(t => t.task_id === activeLogTask.value)
  return t ? (STATUS_MAP[t.status]?.type || 'info') : 'info'
})

async function rpPollProgress() {
  const tid = activeLogTask.value
  if (!tid || !isReportTask(tid)) return
  try {
    const offset = logOffsets.value[tid] || 0
    const res = await getReportProgress(tid, offset)
    rpProgress.value = res.progress || 0
    rpTotal.value = res.total || 0
    rpProcessed.value = res.processed || 0
    rpMessage.value = res.message || ''

    // 更新任务列表中的状态
    const idx = tasks.value.findIndex(t => t.task_id === tid)
    if (idx >= 0) {
      tasks.value[idx] = { ...tasks.value[idx], status: res.status, progress: res.progress, processed: res.processed, total: res.total, message: res.message }
      saveTasks()
    }

    if (res.logs && res.logs.length) {
      logMessages.value.push(...res.logs)
      logOffsets.value[tid] = (logOffsets.value[tid] || 0) + res.logs.length
      nextTick(() => scrollLogToBottom())
    }
    if (res.latest_screenshot) rpScreenshot.value = res.latest_screenshot

    if (['completed', 'failed', 'cancelled'].includes(res.status)) {
      if (rpPollTimer) { clearTimeout(rpPollTimer); rpPollTimer = null }
      if (res.status === 'completed') rpLoadResult(tid)
    } else {
      rpPollTimer = setTimeout(rpPollProgress, res.logs && res.logs.length ? 5000 : 10000)
    }
  } catch {
    rpPollTimer = setTimeout(rpPollProgress, 10000)
  }
}

async function rpLoadResult(tid) {
  try {
    const res = await getReportResult(tid)
    rpResultData.value = (res.results || []).map((r, i) => ({
      ...r, rowKey: `${r.url}_${r.cookie_id}_${i}`,
      _preImg: null, _postImg: null, _loadingImg: false,
    }))
  } catch { /* 静默 */ }
}

async function onRpExpandChange(row, expandedList) {
  rpExpandedRows.value = expandedList.map(r => r.rowKey)
  if (!expandedList.includes(row)) return
  if (row._preImg || row._postImg || row._loadingImg) return
  row._loadingImg = true
  try {
    const tid = activeLogTask.value
    const preFn = (row.screenshot_pre_path || '').replace(/\\/g, '/').split('/').pop()
    const postFn = (row.screenshot_post_path || '').replace(/\\/g, '/').split('/').pop()
    const [preRes, postRes] = await Promise.all([
      preFn ? getReportScreenshot(tid, preFn).catch(() => null) : null,
      postFn ? getReportScreenshot(tid, postFn).catch(() => null) : null,
    ])
    if (preRes && preRes.screenshot) row._preImg = preRes.screenshot
    if (postRes && postRes.screenshot) row._postImg = postRes.screenshot
  } catch { /* 静默 */ }
  row._loadingImg = false
}

async function rpDownloadZip() {
  try {
    const res = await downloadReportScreenshots(activeLogTask.value)
    const url = URL.createObjectURL(new Blob([res.data]))
    const a = document.createElement('a')
    a.href = url; a.download = `report_screenshots_${activeLogTask.value}.zip`; a.click()
    URL.revokeObjectURL(url)
  } catch (e) { ElMessage.error('下载失败: ' + e.message) }
}

async function rpDownloadExcel() {
  try {
    const res = await downloadReportExcel(activeLogTask.value)
    const url = URL.createObjectURL(new Blob([res.data]))
    const a = document.createElement('a')
    a.href = url; a.download = `举报结果_${activeLogTask.value}.xlsx`; a.click()
    URL.revokeObjectURL(url)
  } catch (e) { ElMessage.error('下载失败: ' + e.message) }
}

onMounted(() => {
  const saved = sessionStorage.getItem('mc_tasks')
  if (saved) {
    try {
      tasks.value = JSON.parse(saved)
      tasks.value.forEach(t => {
        if (t.status === 'running' || t.status === 'pending') pollTask(t.task_id)
      })
    } catch { /* ignore */ }
  }

  const newTaskId = route.query.new
  if (newTaskId) {
    pollTask(newTaskId)
  }
})

watch(() => route.query.new, (val) => {
  if (val) pollTask(val)
})

onUnmounted(() => {
  saveTasks()
  Object.values(pollingTimers.value).forEach(clearInterval)
  if (rpPollTimer) clearTimeout(rpPollTimer)
})
</script>

<style scoped>
.task-list { max-width: 1200px; margin: 0 auto; }
.toolbar { display: flex; gap: 12px; margin-bottom: 16px; align-items: center; }
.task-table { margin-bottom: 20px; }
.progress-detail { font-size: 12px; color: #909399; margin-left: 8px; }
.section-title { font-weight: 600; }
.log-card { margin-top: 16px; }
.log-header { display: flex; justify-content: space-between; align-items: center; }
.log-container {
  max-height: 400px;
  overflow-y: auto;
  font-family: 'Consolas', 'Courier New', monospace;
  font-size: 13px;
  background: #1e1e1e;
  color: #d4d4d4;
  padding: 12px;
  border-radius: 4px;
}
.log-line { padding: 2px 0; white-space: pre-wrap; word-break: break-all; }
.log-empty { color: #666; font-style: italic; }
.rp-screenshot-preview {
  border: 1px solid #e4e7ed; border-radius: 8px;
  min-height: 200px; display: flex; align-items: center; justify-content: center;
  background: #fafafa; overflow: hidden;
}
.json-preview {
  background: #1e1e1e; color: #d4d4d4; padding: 16px;
  border-radius: 6px; font-family: 'Consolas', monospace;
  font-size: 12px; white-space: pre; overflow: auto;
  max-height: 60vh;
}
</style>
