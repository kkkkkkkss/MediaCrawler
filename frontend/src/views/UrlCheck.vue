<template>
  <div class="url-check">
    <el-tabs v-model="activeTab" type="border-card">
      <!-- ════════ Tab 1: 单条检测 ════════ -->
      <el-tab-pane label="单条检测" name="single">
        <el-form :model="singleForm" label-width="100px" class="check-form">
          <el-form-item label="URL">
            <el-input v-model="singleForm.url" placeholder="输入要检测的链接，如 https://www.douyin.com/video/..." clearable />
          </el-form-item>
          <el-form-item label="检测模式">
            <el-radio-group v-model="singleForm.mode">
              <el-radio value="both">完整检测（有效性+指标）</el-radio>
              <el-radio value="validity">仅有效性</el-radio>
              <el-radio value="metrics">仅指标</el-radio>
            </el-radio-group>
          </el-form-item>
          <el-form-item label="抓取评论">
            <el-switch v-model="singleForm.enable_comments" />
          </el-form-item>
          <el-collapse class="callback-collapse">
            <el-collapse-item title="回调配置（可选）" name="cb">
              <el-form-item label="回调地址">
                <el-input v-model="singleForm.callback_url" placeholder="任务完成后 POST 结果到此地址（不填则使用全局配置）" clearable />
              </el-form-item>
            </el-collapse-item>
          </el-collapse>
          <el-form-item>
            <el-button type="primary" :loading="singleLoading" @click="doSingleCheck">开始检测</el-button>
          </el-form-item>
        </el-form>

        <el-card v-if="singleResult" shadow="never" class="result-card">
          <template #header><span class="section-title">检测结果</span></template>
          <el-descriptions :column="2" border>
            <el-descriptions-item label="URL">{{ singleResult.url }}</el-descriptions-item>
            <el-descriptions-item label="平台">{{ platformName(singleResult.platform) }}</el-descriptions-item>
            <el-descriptions-item label="有效性">
              <el-tag :type="singleResult.is_valid === 1 ? 'success' : 'danger'">
                {{ singleResult.is_valid === 1 ? '有效' : '无效' }}
              </el-tag>
            </el-descriptions-item>
            <el-descriptions-item label="作者">{{ singleResult.author || '-' }}</el-descriptions-item>
            <el-descriptions-item label="点赞数">{{ singleResult.praise_count ?? '-' }}</el-descriptions-item>
            <el-descriptions-item label="评论数">{{ singleResult.reply_count ?? '-' }}</el-descriptions-item>
            <el-descriptions-item label="转发数">{{ singleResult.share_count ?? '-' }}</el-descriptions-item>
            <el-descriptions-item label="播放量">{{ singleResult.visit_count ?? '-' }}</el-descriptions-item>
          </el-descriptions>
        </el-card>

        <el-card v-if="singleLogs.length || singleLoading" shadow="never" class="result-card" style="margin-top: 12px">
          <template #header><span class="section-title">处理日志 <el-tag v-if="singleLoading" size="small" type="warning" style="margin-left:8px">实时更新中...</el-tag></span></template>
          <div class="log-panel single-log-panel">
            <div v-for="(log, i) in singleLogs" :key="i" class="log-line">{{ log }}</div>
            <div v-if="singleLoading && !singleLogs.length" class="log-line" style="color:#909399">正在启动检测...</div>
          </div>
        </el-card>
      </el-tab-pane>

      <!-- ════════ Tab 2: 批量检测 ════════ -->
      <el-tab-pane label="批量检测" name="batch">
        <el-form :model="batchForm" label-width="100px" class="check-form">
          <el-form-item label="URL 列表">
            <el-input
              v-model="batchForm.urlText"
              type="textarea"
              :rows="8"
              placeholder="每行一个 URL，支持多平台混合&#10;https://www.douyin.com/video/xxx&#10;https://www.kuaishou.com/short-video/xxx"
            />
          </el-form-item>
          <el-form-item label="检测模式">
            <el-radio-group v-model="batchForm.mode">
              <el-radio value="both">完整检测</el-radio>
              <el-radio value="validity">仅有效性</el-radio>
              <el-radio value="metrics">仅指标</el-radio>
            </el-radio-group>
          </el-form-item>
          <el-form-item label="抓取评论">
            <el-switch v-model="batchForm.enable_comments" />
          </el-form-item>
          <el-collapse class="callback-collapse">
            <el-collapse-item title="回调配置（可选）" name="cb">
              <el-form-item label="回调地址">
                <el-input v-model="batchForm.callback_url" placeholder="不填则使用全局配置" clearable />
              </el-form-item>
            </el-collapse-item>
          </el-collapse>
          <el-form-item>
            <el-button type="primary" :loading="batchLoading" @click="doBatchCheck">提交批量检测</el-button>
            <span class="tip-text">提交后可在「任务管理」页查看进度</span>
          </el-form-item>
        </el-form>
      </el-tab-pane>

      <!-- ════════ Tab 3: 文件上传 ════════ -->
      <el-tab-pane label="文件上传" name="upload">
        <el-form label-width="100px" class="check-form">
          <el-form-item label="选择文件">
            <el-upload
              ref="uploadRef"
              :auto-upload="false"
              :limit="1"
              :on-change="onFileChange"
              accept=".xlsx,.csv,.txt"
              drag
            >
              <el-icon :size="40"><UploadFilled /></el-icon>
              <div>将文件拖到此处，或<em>点击上传</em></div>
              <template #tip><div class="el-upload__tip">支持 .xlsx / .csv / .txt 格式</div></template>
            </el-upload>
          </el-form-item>
          <el-form-item label="URL列名">
            <el-input v-model="uploadForm.url_column" placeholder="Excel/CSV 中存放 URL 的列名" style="width:240px" />
          </el-form-item>
          <el-form-item label="检测模式">
            <el-radio-group v-model="uploadForm.mode">
              <el-radio value="both">完整检测</el-radio>
              <el-radio value="validity">仅有效性</el-radio>
            </el-radio-group>
          </el-form-item>
          <el-form-item label="抓取评论">
            <el-switch v-model="uploadForm.enable_comments" />
          </el-form-item>
          <el-collapse class="callback-collapse">
            <el-collapse-item title="回调配置（可选）" name="cb">
              <el-form-item label="回调地址">
                <el-input v-model="uploadForm.callback_url" placeholder="不填则使用全局配置" clearable />
              </el-form-item>
            </el-collapse-item>
          </el-collapse>
          <el-form-item>
            <el-button type="primary" :loading="uploadLoading" @click="doUploadCheck">上传并检测</el-button>
          </el-form-item>
        </el-form>
      </el-tab-pane>

      <!-- ════════ Tab 4: MySQL 检测 ════════ -->
      <el-tab-pane label="MySQL 数据源" name="mysql">
        <el-form :model="mysqlForm" label-width="120px" class="check-form">
          <el-divider content-position="left">数据库连接</el-divider>
          <el-row :gutter="16">
            <el-col :span="12">
              <el-form-item label="主机地址"><el-input v-model="mysqlForm.host" placeholder="如 123.158.253.65" /></el-form-item>
            </el-col>
            <el-col :span="12">
              <el-form-item label="端口"><el-input-number v-model="mysqlForm.port" :min="1" :max="65535" /></el-form-item>
            </el-col>
          </el-row>
          <el-row :gutter="16">
            <el-col :span="12">
              <el-form-item label="用户名"><el-input v-model="mysqlForm.user" /></el-form-item>
            </el-col>
            <el-col :span="12">
              <el-form-item label="密码"><el-input v-model="mysqlForm.password" type="password" show-password /></el-form-item>
            </el-col>
          </el-row>
          <el-row :gutter="16">
            <el-col :span="12">
              <el-form-item label="数据库名"><el-input v-model="mysqlForm.database" placeholder="如 db_sdga_report" /></el-form-item>
            </el-col>
            <el-col :span="12">
              <el-form-item label="表名"><el-input v-model="mysqlForm.table" placeholder="如 bigscreen_data_test" /></el-form-item>
            </el-col>
          </el-row>
          <el-divider content-position="left">检测参数</el-divider>
          <el-row :gutter="16">
            <el-col :span="8">
              <el-form-item label="URL 列名"><el-input v-model="mysqlForm.url_column" /></el-form-item>
            </el-col>
            <el-col :span="8">
              <el-form-item label="批量大小"><el-input-number v-model="mysqlForm.batch_size" :min="1" :max="500" /></el-form-item>
            </el-col>
            <el-col :span="8">
              <el-form-item label="检测模式">
                <el-select v-model="mysqlForm.mode" style="width:100%">
                  <el-option label="完整检测" value="both" />
                  <el-option label="仅有效性" value="validity" />
                  <el-option label="仅指标" value="metrics" />
                </el-select>
              </el-form-item>
            </el-col>
          </el-row>
          <el-form-item label="抓取评论">
            <el-switch v-model="mysqlForm.enable_comments" />
          </el-form-item>
          <el-collapse class="callback-collapse">
            <el-collapse-item title="回调配置（可选）" name="cb">
              <el-form-item label="回调地址">
                <el-input v-model="mysqlForm.callback_url" placeholder="不填则使用全局配置" clearable />
              </el-form-item>
            </el-collapse-item>
          </el-collapse>
          <el-form-item>
            <el-button type="primary" :loading="mysqlLoading" @click="doMysqlCheck">提交 MySQL 检测</el-button>
            <span class="tip-text">检测结果将回写到同一张数据库表</span>
          </el-form-item>
        </el-form>
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup>
import { ref, reactive, onUnmounted, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import { checkSingleUrl, checkBatchUrls, uploadFileCheck, checkMysqlSource, getTaskProgress, getSingleUrlResult } from '../api'
import { ElMessage } from 'element-plus'

const router = useRouter()
const PLATFORM_MAP = { dy: '抖音', ks: '快手', bili: 'B站', toutiao: '今日头条', xhs: '小红书', wb: '微博' }
const platformName = (k) => PLATFORM_MAP[k] || k || '未知'

const activeTab = ref('single')

/* ── 单条检测（任务模式 + 实时日志轮询） ── */
const singleForm = reactive({ url: '', mode: 'both', enable_comments: false, callback_url: '' })
const singleLoading = ref(false)
const singleResult = ref(null)
const singleLogs = ref([])
const singleLogPanel = ref(null)
let singlePollTimer = null

function stopSinglePoll() {
  if (singlePollTimer) { clearInterval(singlePollTimer); singlePollTimer = null }
}
onUnmounted(stopSinglePoll)

async function doSingleCheck() {
  if (!singleForm.url.trim()) return ElMessage.warning('请输入 URL')
  singleLoading.value = true
  singleResult.value = null
  singleLogs.value = []
  stopSinglePoll()

  try {
    const res = await checkSingleUrl(singleForm)
    const taskId = res.task_id
    if (!taskId) { ElMessage.error('未获取到任务ID'); singleLoading.value = false; return }

    let logOffset = 0
    singlePollTimer = setInterval(async () => {
      try {
        const prog = await getTaskProgress(taskId, logOffset)
        if (prog.logs && prog.logs.length) {
          singleLogs.value.push(...prog.logs)
          logOffset = prog.log_total
          await nextTick()
          const panel = document.querySelector('.single-log-panel')
          if (panel) panel.scrollTop = panel.scrollHeight
        }
        if (['completed', 'failed', 'cancelled'].includes(prog.status)) {
          stopSinglePoll()
          const final = await getSingleUrlResult(taskId)
          singleResult.value = final.result
          if (final.logs && final.logs.length > singleLogs.value.length) {
            singleLogs.value = final.logs
          }
          singleLoading.value = false
        }
      } catch { /* 轮询异常忽略 */ }
    }, 1000)
  } catch (e) {
    ElMessage.error('检测失败: ' + e.message)
    singleLoading.value = false
  }
}

/* ── 批量检测 ── */
const batchForm = reactive({ urlText: '', mode: 'both', enable_comments: false, callback_url: '' })
const batchLoading = ref(false)

async function doBatchCheck() {
  const urls = batchForm.urlText.split('\n').map(s => s.trim()).filter(s => s.startsWith('http'))
  if (!urls.length) return ElMessage.warning('请输入至少一个有效 URL')
  batchLoading.value = true
  try {
    const payload = { urls, mode: batchForm.mode, enable_comments: batchForm.enable_comments }
    if (batchForm.callback_url) payload.callback_url = batchForm.callback_url
    const res = await checkBatchUrls(payload)
    ElMessage.success(`任务已提交! ID: ${res.task_id}`)
    router.push({ path: '/tasks', query: { new: res.task_id } })
  } catch (e) {
    ElMessage.error('提交失败: ' + e.message)
  } finally {
    batchLoading.value = false
  }
}

/* ── 文件上传 ── */
const uploadForm = reactive({ url_column: 'url', mode: 'both', enable_comments: false, callback_url: '' })
const uploadFile = ref(null)
const uploadLoading = ref(false)
const uploadRef = ref(null)

function onFileChange(file) { uploadFile.value = file.raw }

async function doUploadCheck() {
  if (!uploadFile.value) return ElMessage.warning('请选择文件')
  const fd = new FormData()
  fd.append('file', uploadFile.value)
  fd.append('url_column', uploadForm.url_column)
  fd.append('mode', uploadForm.mode)
  fd.append('enable_comments', uploadForm.enable_comments)
  if (uploadForm.callback_url) fd.append('callback_url', uploadForm.callback_url)
  uploadLoading.value = true
  try {
    const res = await uploadFileCheck(fd)
    ElMessage.success(`任务已提交! ID: ${res.task_id}`)
    router.push({ path: '/tasks', query: { new: res.task_id } })
  } catch (e) {
    ElMessage.error('上传失败: ' + e.message)
  } finally {
    uploadLoading.value = false
  }
}

/* ── MySQL 检测 ── */
const mysqlForm = reactive({
  host: '', port: 3306, user: 'root', password: '',
  database: '', table: '', url_column: 'url',
  mode: 'both', batch_size: 50, enable_comments: false, callback_url: '',
})
const mysqlLoading = ref(false)

async function doMysqlCheck() {
  if (!mysqlForm.host || !mysqlForm.database || !mysqlForm.table) {
    return ElMessage.warning('请填写完整的数据库连接信息')
  }
  mysqlLoading.value = true
  try {
    const res = await checkMysqlSource(mysqlForm)
    ElMessage.success(`MySQL 检测任务已提交! ID: ${res.task_id}`)
    router.push({ path: '/tasks', query: { new: res.task_id } })
  } catch (e) {
    ElMessage.error('提交失败: ' + e.message)
  } finally {
    mysqlLoading.value = false
  }
}
</script>

<style scoped>
.url-check { max-width: 1100px; margin: 0 auto; }
.check-form { max-width: 900px; padding: 16px 0; }
.result-card { margin-top: 16px; }
.callback-collapse { margin: 8px 0 16px 100px; }
.callback-collapse .el-collapse-item__header { font-size: 13px; color: #909399; }
.section-title { font-weight: 600; }
.tip-text { margin-left: 12px; color: #909399; font-size: 13px; }
.log-panel { background: #1e1e1e; color: #d4d4d4; padding: 12px 16px; border-radius: 6px; font-family: 'Consolas', monospace; font-size: 13px; max-height: 200px; overflow-y: auto; }
.log-line { line-height: 1.6; white-space: pre-wrap; }
</style>
