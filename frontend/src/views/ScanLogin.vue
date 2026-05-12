<template>
  <div class="scan-login">
    <!-- 步骤一：选择平台 -->
    <el-card shadow="never" class="section-card" v-if="!sessionId">
      <template #header><span class="section-title">选择扫码平台</span></template>
      <el-form label-width="100px">
        <el-form-item label="扫码平台">
          <el-radio-group v-model="selectedPlatform">
            <el-radio value="all">全部平台（串行扫码）</el-radio>
            <el-radio v-for="(name, key) in PLATFORMS" :key="key" :value="key">{{ name }}</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="登录模式">
          <el-radio-group v-model="scanMode">
            <el-radio-button value="force_new">强制扫码</el-radio-button>
            <el-radio-button value="refresh">刷新登录态</el-radio-button>
            <el-radio-button value="virtual">虚拟Cookie</el-radio-button>
          </el-radio-group>
          <div class="mode-tip">
            <span v-if="scanMode === 'force_new'">清除浏览器缓存，强制显示二维码，用于添加新账号Cookie</span>
            <span v-else-if="scanMode === 'refresh'">复用浏览器中已有的登录态，快速刷新当前账号Cookie</span>
            <span v-else>为不需要登录的平台（如头条）生成虚拟Cookie</span>
          </div>
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="note" placeholder="可选，如：主号、账号A..." style="width:300px" />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :loading="starting" @click="doStartScan">
            <el-icon><Iphone /></el-icon> 开始扫码
          </el-button>
        </el-form-item>
      </el-form>
    </el-card>

    <!-- 步骤二：扫码进行中 -->
    <template v-if="sessionId">
      <el-card shadow="never" class="section-card">
        <template #header>
          <div class="scan-header">
            <span class="section-title">扫码登录 — {{ statusMsg }}</span>
            <div>
              <el-button
                v-if="!isFinished"
                type="danger"
                size="small"
                :loading="cancelling"
                @click="doCancelScan"
              >
                终止扫码
              </el-button>
              <el-button text type="info" @click="resetSession">关闭面板</el-button>
            </div>
          </div>
        </template>

        <el-row :gutter="24">
          <!-- 左侧：二维码 -->
          <el-col :span="12">
            <div class="qr-area">
              <div v-if="scanStatus === 'starting'" class="qr-placeholder">
                <el-icon :size="48" class="loading-icon"><Loading /></el-icon>
                <p>浏览器启动中，请稍候...</p>
              </div>
              <div v-else-if="scanStatus === 'clicking_login'" class="qr-placeholder">
                <el-icon :size="48" class="loading-icon"><Loading /></el-icon>
                <p>正在打开登录页面，等待二维码加载...</p>
                <p class="qr-sub-tip">部分平台需要几秒钟弹出二维码</p>
              </div>
              <div v-else-if="qrBase64" class="qr-wrapper">
                <img :src="'data:image/png;base64,' + qrBase64" class="qr-img" />
                <p class="qr-tip">请使用 <strong>{{ currentPlatformName }}</strong> App 扫描上方二维码</p>
              </div>
              <div v-else-if="isFinished" class="qr-placeholder">
                <el-icon :size="48" :color="scanStatus === 'cancelled' ? '#e6a23c' : '#67c23a'">
                  <component :is="scanStatus === 'cancelled' ? 'WarningFilled' : 'CircleCheckFilled'" />
                </el-icon>
                <p>{{ statusMsg }}</p>
              </div>
              <div v-else class="qr-placeholder">
                <el-icon :size="48"><Picture /></el-icon>
                <p>等待二维码加载...</p>
              </div>
            </div>
          </el-col>

          <!-- 右侧：状态信息 -->
          <el-col :span="12">
            <h4>平台队列</h4>
            <el-timeline>
              <el-timeline-item
                v-for="plat in platformsQueue"
                :key="plat"
                :type="timelineType(plat)"
                :hollow="plat !== currentPlatform"
              >
                <span :class="{ 'active-plat': plat === currentPlatform }">
                  {{ PLATFORMS[plat] || plat }}
                </span>
                <el-tag v-if="completedMap[plat]" type="success" size="small" class="plat-tag">已完成</el-tag>
                <el-tag v-else-if="skippedList.includes(plat)" type="info" size="small" class="plat-tag">已跳过</el-tag>
                <el-tag v-else-if="plat === currentPlatform && !isFinished" type="" size="small" class="plat-tag">进行中</el-tag>
              </el-timeline-item>
            </el-timeline>

            <el-divider />

            <el-descriptions :column="1" border size="small">
              <el-descriptions-item label="Session ID">{{ sessionId }}</el-descriptions-item>
              <el-descriptions-item label="当前状态">
                <el-tag :type="scanStatusType">{{ statusMsg }}</el-tag>
              </el-descriptions-item>
              <el-descriptions-item label="已完成">{{ Object.keys(completedMap).length }} 个平台</el-descriptions-item>
              <el-descriptions-item label="已跳过">{{ skippedList.length }} 个平台</el-descriptions-item>
            </el-descriptions>
          </el-col>
        </el-row>
      </el-card>
    </template>
  </div>
</template>

<script setup>
import { ref, computed, onUnmounted } from 'vue'
import { startScan, getScanQrcode, getScanStatus, cancelScan } from '../api'
import { ElMessage } from 'element-plus'

const PLATFORMS = { dy: '抖音', ks: '快手', bili: 'B站', wb: '微博', toutiao: '今日头条' }

const selectedPlatform = ref('all')
const note = ref('')
const scanMode = ref('force_new')
const starting = ref(false)
const cancelling = ref(false)

const sessionId = ref('')
const scanStatus = ref('')
const statusMsg = ref('')
const qrBase64 = ref('')
const currentPlatform = ref('')
const platformsQueue = ref([])
const completedMap = ref({})
const skippedList = ref([])

let pollTimer = null

const currentPlatformName = computed(() => PLATFORMS[currentPlatform.value] || currentPlatform.value)
const isFinished = computed(() => ['all_done', 'failed', 'cancelled'].includes(scanStatus.value))

const scanStatusType = computed(() => {
  const map = {
    starting: 'info', clicking_login: 'info', waiting: '',
    success: 'success', timeout: 'warning',
    all_done: 'success', failed: 'danger', cancelled: 'warning',
  }
  return map[scanStatus.value] || 'info'
})

function timelineType(plat) {
  if (completedMap.value[plat]) return 'success'
  if (skippedList.value.includes(plat)) return 'info'
  if (plat === currentPlatform.value) return 'primary'
  return 'info'
}

async function doStartScan() {
  starting.value = true
  try {
    const res = await startScan(selectedPlatform.value, note.value, scanMode.value)
    sessionId.value = res.cookie_id
    platformsQueue.value = res.platforms || [selectedPlatform.value]
    scanStatus.value = 'starting'
    statusMsg.value = '启动中...'
    completedMap.value = {}
    skippedList.value = []
    qrBase64.value = ''
    startPolling()
    ElMessage.success('扫码会话已启动')
  } catch (e) {
    ElMessage.error('启动失败: ' + e.message)
  } finally {
    starting.value = false
  }
}

async function doCancelScan() {
  if (!sessionId.value) return
  cancelling.value = true
  try {
    const res = await cancelScan(sessionId.value)
    if (res.success) {
      ElMessage.warning('扫码已终止')
      stopPolling()
      scanStatus.value = 'cancelled'
      statusMsg.value = res.message
    } else {
      ElMessage.info(res.message)
    }
  } catch (e) {
    ElMessage.error('终止失败: ' + e.message)
  } finally {
    cancelling.value = false
  }
}

function startPolling() {
  pollTimer = setInterval(async () => {
    await pollStatus()
    await pollQrcode()
  }, 2000)
}

async function pollStatus() {
  if (!sessionId.value) return
  try {
    const res = await getScanStatus(sessionId.value)
    scanStatus.value = res.status
    statusMsg.value = res.message
    currentPlatform.value = res.current_platform || ''
    completedMap.value = res.completed || {}
    skippedList.value = res.skipped || []
    if (res.platforms_queue) platformsQueue.value = res.platforms_queue

    if (['all_done', 'failed', 'cancelled'].includes(res.status)) {
      stopPolling()
      if (res.status === 'all_done') {
        const count = Object.keys(res.completed || {}).length
        ElMessage.success(`扫码完成! 成功 ${count} 个平台`)
      }
    }
  } catch { /* 轮询错误静默处理 */ }
}

async function pollQrcode() {
  if (!sessionId.value || isFinished.value) return
  try {
    const res = await getScanQrcode(sessionId.value)
    if (res.qrcode_base64) qrBase64.value = res.qrcode_base64
  } catch { /* 截图未就绪 */ }
}

function stopPolling() {
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null }
}

function resetSession() {
  stopPolling()
  sessionId.value = ''
  qrBase64.value = ''
  scanStatus.value = ''
}

onUnmounted(stopPolling)
</script>

<style scoped>
.scan-login { max-width: 1000px; margin: 0 auto; }
.section-card { margin-bottom: 20px; }
.section-title { font-weight: 600; font-size: 15px; }
.scan-header { display: flex; justify-content: space-between; align-items: center; }

.qr-area { text-align: center; padding: 20px; }
.qr-placeholder {
  display: flex; flex-direction: column; align-items: center;
  justify-content: center; height: 300px; color: #909399;
}
.qr-img { max-width: 100%; border-radius: 8px; box-shadow: 0 2px 12px rgba(0,0,0,.12); }
.qr-tip { margin-top: 12px; color: #606266; font-size: 14px; }
.qr-sub-tip { margin-top: 4px; font-size: 12px; color: #c0c4cc; }
.loading-icon { animation: spin 1.2s linear infinite; }
@keyframes spin { from { transform: rotate(0) } to { transform: rotate(360deg) } }

.active-plat { font-weight: 700; color: #409eff; }
.plat-tag { margin-left: 8px; }
.mode-tip { margin-top: 6px; font-size: 12px; color: #909399; }
</style>
