<template>
  <div class="report-complaint">
    <el-tabs v-model="activeTab" type="border-card">
      <!-- Tab 1: 单条举报 -->
      <el-tab-pane label="单条举报" name="single">
        <el-form :model="singleForm" label-width="100px" class="report-form">
          <el-form-item label="链接/文本">
            <el-input
              v-model="singleForm.url"
              type="textarea"
              :rows="3"
              placeholder="粘贴作品链接或分享文本（自动提取URL）&#10;支持：抖音、微博、快手、今日头条"
              clearable
            />
          </el-form-item>
          <el-form-item label="举报理由">
            <el-select v-model="singleForm.reason" placeholder="选择举报理由" style="width:300px">
              <el-option v-for="r in currentReasons" :key="r" :label="r" :value="r" />
            </el-select>
          </el-form-item>
          <el-form-item label="补充说明">
            <el-input v-model="singleForm.description" placeholder="可选，填写补充说明" clearable style="width:400px" />
          </el-form-item>
          <el-form-item>
            <el-button type="danger" :loading="submitting" @click="doSingleReport">
              <el-icon><Warning /></el-icon> 开始举报
            </el-button>
          </el-form-item>
        </el-form>
      </el-tab-pane>

      <!-- Tab 2: 批量举报 -->
      <el-tab-pane label="批量举报" name="batch">
        <el-form :model="batchForm" label-width="100px" class="report-form">
          <el-form-item label="链接列表">
            <el-input
              v-model="batchForm.urls"
              type="textarea"
              :rows="8"
              placeholder="每行一个链接或分享文本（自动提取URL）&#10;支持多平台混合"
            />
          </el-form-item>
          <el-form-item label="举报理由">
            <el-select v-model="batchForm.reason" placeholder="统一举报理由" style="width:300px">
              <el-option v-for="r in allReasonsList" :key="r" :label="r" :value="r" />
            </el-select>
          </el-form-item>
          <el-form-item label="补充说明">
            <el-input v-model="batchForm.description" placeholder="可选" clearable style="width:400px" />
          </el-form-item>
          <el-form-item>
            <el-button type="danger" :loading="submitting" @click="doBatchReport">
              <el-icon><Warning /></el-icon> 批量举报
            </el-button>
          </el-form-item>
        </el-form>
      </el-tab-pane>

      <!-- Tab 3: 文件举报 -->
      <el-tab-pane label="文件举报" name="file">
        <el-form label-width="100px" class="report-form">
          <el-form-item label="上传文件">
            <el-upload
              ref="uploadRef"
              :auto-upload="false"
              :limit="1"
              accept=".xlsx,.csv,.txt"
              :on-change="onFileChange"
            >
              <el-button type="primary">选择文件</el-button>
              <template #tip>
                <div class="el-upload__tip">支持 .xlsx / .csv / .txt 格式</div>
              </template>
            </el-upload>
          </el-form-item>
          <el-form-item label="URL列名">
            <el-input v-model="fileForm.url_column" placeholder="url" style="width:200px" />
          </el-form-item>
          <el-form-item label="举报理由">
            <el-select v-model="fileForm.reason" placeholder="举报理由" style="width:300px">
              <el-option v-for="r in allReasonsList" :key="r" :label="r" :value="r" />
            </el-select>
          </el-form-item>
          <el-form-item label="补充说明">
            <el-input v-model="fileForm.description" placeholder="可选" clearable style="width:400px" />
          </el-form-item>
          <el-form-item>
            <el-button type="danger" :loading="submitting" @click="doFileReport">
              <el-icon><Warning /></el-icon> 文件举报
            </el-button>
          </el-form-item>
        </el-form>
      </el-tab-pane>

      <!-- Tab 4: 数据库举报 -->
      <el-tab-pane label="数据库举报" name="mysql">
        <el-form :model="mysqlForm" label-width="100px" class="report-form">
          <el-row :gutter="16">
            <el-col :span="8">
              <el-form-item label="主机">
                <el-input v-model="mysqlForm.host" />
              </el-form-item>
            </el-col>
            <el-col :span="4">
              <el-form-item label="端口">
                <el-input-number v-model="mysqlForm.port" :min="1" :max="65535" />
              </el-form-item>
            </el-col>
            <el-col :span="6">
              <el-form-item label="用户名">
                <el-input v-model="mysqlForm.user" />
              </el-form-item>
            </el-col>
            <el-col :span="6">
              <el-form-item label="密码">
                <el-input v-model="mysqlForm.password" type="password" show-password />
              </el-form-item>
            </el-col>
          </el-row>
          <el-row :gutter="16">
            <el-col :span="8">
              <el-form-item label="数据库">
                <el-input v-model="mysqlForm.database" />
              </el-form-item>
            </el-col>
            <el-col :span="8">
              <el-form-item label="表名">
                <el-input v-model="mysqlForm.table" />
              </el-form-item>
            </el-col>
            <el-col :span="4">
              <el-form-item label="URL列">
                <el-input v-model="mysqlForm.url_column" />
              </el-form-item>
            </el-col>
            <el-col :span="4">
              <el-form-item label="数量上限">
                <el-input-number v-model="mysqlForm.limit" :min="1" :max="10000" />
              </el-form-item>
            </el-col>
          </el-row>
          <el-form-item label="WHERE条件">
            <el-input v-model="mysqlForm.where" placeholder="可选，如 status=0" style="width:400px" />
          </el-form-item>
          <el-form-item label="举报理由">
            <el-select v-model="mysqlForm.reason" placeholder="举报理由" style="width:300px">
              <el-option v-for="r in allReasonsList" :key="r" :label="r" :value="r" />
            </el-select>
          </el-form-item>
          <el-form-item>
            <el-button type="danger" :loading="submitting" @click="doMysqlReport">
              <el-icon><Warning /></el-icon> 数据库举报
            </el-button>
          </el-form-item>
        </el-form>
      </el-tab-pane>
    </el-tabs>

    <!-- 任务进度区域 -->
    <template v-if="taskId">
      <el-card shadow="never" class="progress-card">
        <template #header>
          <div class="progress-header">
            <span class="section-title">
              举报任务 <el-tag size="small">{{ taskId }}</el-tag>
              <el-tag :type="statusType" size="small" style="margin-left:8px">{{ taskStatus }}</el-tag>
            </span>
            <div>
              <el-button v-if="isRunning" type="danger" size="small" @click="doCancel">取消任务</el-button>
              <el-button v-if="isCompleted" type="success" size="small" @click="doDownloadScreenshots">
                下载截图ZIP
              </el-button>
              <el-button v-if="isCompleted" type="primary" size="small" @click="doDownloadExcel">
                导出Excel报告
              </el-button>
              <el-button text type="info" size="small" @click="resetTask">关闭面板</el-button>
            </div>
          </div>
        </template>

        <!-- 进度条 -->
        <el-progress
          :percentage="progress"
          :status="isCompleted ? 'success' : isFailed ? 'exception' : ''"
          :stroke-width="16"
          style="margin-bottom:16px"
        />
        <el-descriptions :column="4" border size="small" style="margin-bottom:16px">
          <el-descriptions-item label="总次数">{{ total }}</el-descriptions-item>
          <el-descriptions-item label="已完成">{{ processed }}</el-descriptions-item>
          <el-descriptions-item label="进度">{{ progress }}%</el-descriptions-item>
          <el-descriptions-item label="状态">{{ message }}</el-descriptions-item>
        </el-descriptions>

        <!-- 截图预览 + 日志 并排 -->
        <el-row :gutter="16">
          <el-col :span="10">
            <h4 style="margin-bottom:8px">实时截图预览</h4>
            <div class="screenshot-preview">
              <img v-if="latestScreenshot" :src="'data:image/png;base64,' + latestScreenshot" class="screenshot-img" />
              <div v-else class="screenshot-placeholder">
                <el-icon :size="36"><Picture /></el-icon>
                <p>{{ isRunning ? '等待截图...' : '暂无截图' }}</p>
              </div>
            </div>
          </el-col>
          <el-col :span="14">
            <h4 style="margin-bottom:8px">
              实时日志
              <el-tag v-if="isRunning" size="small" type="warning" style="margin-left:8px">更新中...</el-tag>
            </h4>
            <div class="log-panel" ref="logPanelRef">
              <div v-for="(log, i) in logs" :key="i" class="log-line">{{ log }}</div>
              <div v-if="isRunning && !logs.length" class="log-line" style="color:#909399">等待任务启动...</div>
            </div>
          </el-col>
        </el-row>

        <!-- 结果表格（任务完成后展示） -->
        <template v-if="isCompleted && resultData.length">
          <h4 style="margin:20px 0 12px">举报结果明细 (点击行展开查看截图)</h4>
          <el-table
            :data="resultData"
            border
            stripe
            size="small"
            row-key="rowKey"
            :expand-row-keys="expandedRows"
            @expand-change="onExpandChange"
          >
            <el-table-column type="expand">
              <template #default="{ row }">
                <div class="expand-screenshots">
                  <div v-if="row._preImg || row._postImg" class="screenshot-pair">
                    <div class="screenshot-item" v-if="row._preImg">
                      <p class="screenshot-label">提交前</p>
                      <img :src="'data:image/png;base64,' + row._preImg" class="expand-img" />
                    </div>
                    <div class="screenshot-item" v-if="row._postImg">
                      <p class="screenshot-label">提交后</p>
                      <img :src="'data:image/png;base64,' + row._postImg" class="expand-img" />
                    </div>
                  </div>
                  <div v-else-if="row._loadingImg" style="padding:20px;color:#909399">加载截图中...</div>
                  <div v-else style="padding:20px;color:#909399">无截图</div>
                </div>
              </template>
            </el-table-column>
            <el-table-column label="链接" prop="url" min-width="200" show-overflow-tooltip />
            <el-table-column label="平台" prop="platform" width="80" align="center">
              <template #default="{ row }">{{ platformNames[row.platform] || row.platform }}</template>
            </el-table-column>
            <el-table-column label="账号" prop="cookie_id" width="100" align="center" />
            <el-table-column label="理由" prop="reason" width="100" align="center" />
            <el-table-column label="结果" width="80" align="center">
              <template #default="{ row }">
                <el-tag :type="row.success ? 'success' : 'danger'" size="small">
                  {{ row.success ? '成功' : '失败' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="失败原因" prop="error_msg" min-width="150" show-overflow-tooltip />
            <el-table-column label="耗时" width="80" align="center">
              <template #default="{ row }">{{ row.elapsed_sec }}s</template>
            </el-table-column>
          </el-table>
        </template>
      </el-card>
    </template>
  </div>
</template>

<script setup>
import { ref, computed, watch, onUnmounted, nextTick, reactive } from 'vue'
import {
  getAllReportReasons, reportSingle, reportBatch, reportUpload, reportMysql,
  getReportProgress, cancelReportTask, downloadReportScreenshots,
  getReportResult, getReportScreenshot, downloadReportExcel,
} from '../api'
import { ElMessage } from 'element-plus'

const platformNames = { dy: '抖音', wb: '微博', ks: '快手', toutiao: '今日头条' }

/* ---- 举报理由 ---- */
const allReasons = ref({})
const allReasonsList = computed(() => {
  const set = new Set()
  for (const plat of Object.values(allReasons.value)) {
    for (const r of (plat.reasons || [])) set.add(r)
  }
  return [...set]
})
const currentReasons = computed(() => {
  if (allReasonsList.value.length) return allReasonsList.value
  return ['不实信息', '虚假信息', '违法违规', '低俗色情', '其他']
})

async function loadReasons() {
  try {
    const res = await getAllReportReasons()
    allReasons.value = res
  } catch { /* 静默 */ }
}
loadReasons()

/* ---- 表单数据 ---- */
const activeTab = ref('single')
const submitting = ref(false)

const singleForm = ref({ url: '', reason: '不实信息', description: '' })
const batchForm = ref({ urls: '', reason: '不实信息', description: '' })
const fileForm = ref({ url_column: 'url', reason: '不实信息', description: '' })
const selectedFile = ref(null)
const uploadRef = ref(null)

const mysqlForm = ref({
  host: '', port: 3306,
  user: '', password: '',
  database: '', table: '',
  url_column: 'url', limit: 100, where: '',
  reason: '不实信息', description: '',
})

function onFileChange(file) { selectedFile.value = file.raw }

/* ---- 任务状态 ---- */
const taskId = ref('')
const taskStatus = ref('')
const progress = ref(0)
const total = ref(0)
const processed = ref(0)
const message = ref('')
const logs = ref([])
const latestScreenshot = ref(null)
const resultData = ref([])
const expandedRows = ref([])
let pollTimeout = null
let logOffset = 0
const logPanelRef = ref(null)

const isRunning = computed(() => ['pending', 'running'].includes(taskStatus.value))
const isCompleted = computed(() => taskStatus.value === 'completed')
const isFailed = computed(() => ['failed', 'cancelled'].includes(taskStatus.value))
const statusType = computed(() => {
  const m = { pending: 'info', running: '', completed: 'success', failed: 'danger', cancelled: 'warning' }
  return m[taskStatus.value] || 'info'
})

/* ---- 提交举报 ---- */
async function doSingleReport() {
  if (!singleForm.value.url.trim()) return ElMessage.warning('请输入链接')
  submitting.value = true
  try {
    const res = await reportSingle(singleForm.value)
    startPolling(res.task_id)
    ElMessage.success(`举报任务已提交: ${res.url_count} 条链接`)
  } catch (e) { ElMessage.error('提交失败: ' + e.message) }
  finally { submitting.value = false }
}

async function doBatchReport() {
  const lines = batchForm.value.urls.split('\n').filter(l => l.trim())
  if (!lines.length) return ElMessage.warning('请输入链接')
  submitting.value = true
  try {
    const res = await reportBatch({ urls: lines, reason: batchForm.value.reason, description: batchForm.value.description })
    startPolling(res.task_id)
    ElMessage.success(`批量举报任务已提交: ${res.url_count} 条链接`)
  } catch (e) { ElMessage.error('提交失败: ' + e.message) }
  finally { submitting.value = false }
}

async function doFileReport() {
  if (!selectedFile.value) return ElMessage.warning('请选择文件')
  submitting.value = true
  try {
    const fd = new FormData()
    fd.append('file', selectedFile.value)
    fd.append('url_column', fileForm.value.url_column)
    fd.append('reason', fileForm.value.reason)
    fd.append('description', fileForm.value.description)
    const res = await reportUpload(fd)
    startPolling(res.task_id)
    ElMessage.success(`文件举报任务已提交: ${res.url_count} 条链接`)
  } catch (e) { ElMessage.error('提交失败: ' + e.message) }
  finally { submitting.value = false }
}

async function doMysqlReport() {
  submitting.value = true
  try {
    const res = await reportMysql(mysqlForm.value)
    startPolling(res.task_id)
    ElMessage.success('数据库举报任务已提交')
  } catch (e) { ElMessage.error('提交失败: ' + e.message) }
  finally { submitting.value = false }
}

/* ---- 将举报任务保存到 sessionStorage，使任务管理页可见 ---- */
function saveTaskToSession(tid) {
  try {
    const saved = sessionStorage.getItem('mc_tasks')
    const arr = saved ? JSON.parse(saved) : []
    if (!arr.find(t => t.task_id === tid)) {
      arr.unshift({ task_id: tid, status: 'pending', progress: 0, processed: 0, total: 0, message: '举报任务已提交', type: 'report' })
      sessionStorage.setItem('mc_tasks', JSON.stringify(arr))
    }
  } catch { /* 静默 */ }
}

/* ---- 自适应轮询：运行中有新日志=5s，无新日志=10s，结束=停止 ---- */
function startPolling(tid) {
  resetTask()
  taskId.value = tid
  taskStatus.value = 'pending'
  logOffset = 0
  saveTaskToSession(tid)
  schedulePoll(2000)
}

function schedulePoll(delay) {
  stopPolling()
  pollTimeout = setTimeout(pollProgress, delay)
}

async function pollProgress() {
  if (!taskId.value) return
  try {
    const res = await getReportProgress(taskId.value, logOffset)
    taskStatus.value = res.status
    progress.value = res.progress || 0
    total.value = res.total || 0
    processed.value = res.processed || 0
    message.value = res.message || ''

    const hasNewLogs = res.logs && res.logs.length > 0
    if (hasNewLogs) {
      logs.value.push(...res.logs)
      logOffset += res.logs.length
      nextTick(() => {
        if (logPanelRef.value) logPanelRef.value.scrollTop = logPanelRef.value.scrollHeight
      })
    }

    if (res.latest_screenshot) latestScreenshot.value = res.latest_screenshot

    if (['completed', 'failed', 'cancelled'].includes(res.status)) {
      stopPolling()
      if (res.status === 'completed') {
        ElMessage.success('举报任务完成')
        loadResultData()
      }
    } else {
      // 自适应间隔：有新日志 5s，无新日志 10s
      const nextDelay = hasNewLogs ? 5000 : 10000
      schedulePoll(nextDelay)
    }
  } catch {
    schedulePoll(10000)
  }
}

function stopPolling() { if (pollTimeout) { clearTimeout(pollTimeout); pollTimeout = null } }

function resetTask() {
  stopPolling()
  taskId.value = ''
  taskStatus.value = ''
  progress.value = 0
  total.value = 0
  processed.value = 0
  message.value = ''
  logs.value = []
  latestScreenshot.value = null
  logOffset = 0
  resultData.value = []
  expandedRows.value = []
}

/* ---- 加载结果数据 ---- */
async function loadResultData() {
  if (!taskId.value) return
  try {
    const res = await getReportResult(taskId.value)
    resultData.value = (res.results || []).map((r, i) => ({
      ...r,
      rowKey: `${r.url}_${r.cookie_id}_${i}`,
      _preImg: null,
      _postImg: null,
      _loadingImg: false,
    }))
  } catch { /* 静默 */ }
}

/* ---- 展开行时加载截图 ---- */
async function onExpandChange(row, expandedList) {
  expandedRows.value = expandedList.map(r => r.rowKey)
  if (!expandedList.includes(row)) return
  if (row._preImg || row._postImg || row._loadingImg) return

  row._loadingImg = true
  try {
    const preFn = extractFilename(row.screenshot_pre_path)
    const postFn = extractFilename(row.screenshot_post_path)
    const [preRes, postRes] = await Promise.all([
      preFn ? getReportScreenshot(taskId.value, preFn).catch(() => null) : null,
      postFn ? getReportScreenshot(taskId.value, postFn).catch(() => null) : null,
    ])
    if (preRes && preRes.screenshot) row._preImg = preRes.screenshot
    if (postRes && postRes.screenshot) row._postImg = postRes.screenshot
  } catch { /* 静默 */ }
  row._loadingImg = false
}

function extractFilename(path) {
  if (!path) return ''
  return path.replace(/\\/g, '/').split('/').pop()
}

/* ---- 操作按钮 ---- */
async function doCancel() {
  try {
    await cancelReportTask(taskId.value)
    ElMessage.warning('任务已取消')
  } catch (e) { ElMessage.error('取消失败: ' + e.message) }
}

async function doDownloadScreenshots() {
  try {
    const res = await downloadReportScreenshots(taskId.value)
    const url = URL.createObjectURL(new Blob([res.data]))
    const a = document.createElement('a')
    a.href = url
    a.download = `report_screenshots_${taskId.value}.zip`
    a.click()
    URL.revokeObjectURL(url)
  } catch (e) { ElMessage.error('下载失败: ' + e.message) }
}

async function doDownloadExcel() {
  try {
    const res = await downloadReportExcel(taskId.value)
    const url = URL.createObjectURL(new Blob([res.data]))
    const a = document.createElement('a')
    a.href = url
    a.download = `举报结果_${taskId.value}.xlsx`
    a.click()
    URL.revokeObjectURL(url)
  } catch (e) { ElMessage.error('下载失败: ' + e.message) }
}

onUnmounted(stopPolling)
</script>

<style scoped>
.report-complaint { max-width: 1200px; margin: 0 auto; }
.report-form { padding: 12px 0; }
.progress-card { margin-top: 20px; }
.progress-header { display: flex; justify-content: space-between; align-items: center; }
.section-title { font-weight: 600; font-size: 15px; }

.screenshot-preview {
  border: 1px solid #e4e7ed;
  border-radius: 8px;
  min-height: 260px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #fafafa;
  overflow: hidden;
}
.screenshot-img { max-width: 100%; max-height: 400px; border-radius: 4px; }
.screenshot-placeholder {
  display: flex; flex-direction: column; align-items: center;
  color: #909399; gap: 8px;
}

.log-panel {
  background: #1e1e1e; color: #d4d4d4;
  border-radius: 6px; padding: 12px;
  font-family: 'Consolas', 'Monaco', monospace;
  font-size: 12px; line-height: 1.6;
  max-height: 350px; overflow-y: auto;
}
.log-line { white-space: pre-wrap; word-break: break-all; }

/* 结果表格截图展开区域 */
.expand-screenshots { padding: 12px 16px; }
.screenshot-pair {
  display: flex; gap: 24px; flex-wrap: wrap;
}
.screenshot-item {
  display: flex; flex-direction: column; align-items: center; gap: 6px;
}
.screenshot-label {
  font-size: 12px; color: #606266; font-weight: 600; margin: 0;
}
.expand-img {
  max-width: 480px; max-height: 320px; border: 1px solid #dcdfe6;
  border-radius: 6px; cursor: pointer;
}
</style>
