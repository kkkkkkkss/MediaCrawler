<template>
  <div class="scan-login">
    <!-- 步骤一：选择平台 -->
    <el-card shadow="never" class="section-card" v-if="!sessionId">
      <template #header><span class="section-title">选择扫码平台</span></template>
      <el-form label-width="100px">
        <el-form-item label="扫码平台">
          <el-checkbox-group v-model="selectedPlatforms">
            <el-checkbox v-for="(name, key) in PLATFORMS" :key="key" :value="key">{{ name }}</el-checkbox>
          </el-checkbox-group>
          <div class="platform-actions">
            <el-button text type="primary" size="small" @click="selectAllPlatforms">全选</el-button>
            <el-button text type="info" size="small" @click="selectedPlatforms = []">清空</el-button>
          </div>
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
            <span v-else>为各平台生成虚拟Cookie（不登录，仅用于链接检测等无需登录的功能）</span>
          </div>
        </el-form-item>
        <el-form-item label="执行模式">
          <el-radio-group v-model="execMode">
            <el-radio-button value="serial">串行扫码</el-radio-button>
            <el-radio-button value="parallel">并行扫码</el-radio-button>
          </el-radio-group>
          <div class="mode-tip">
            <span v-if="execMode === 'serial'">逐个平台扫码，一个完成后进入下一个</span>
            <span v-else>同时启动所有平台，展示多个二维码并行扫描</span>
          </div>
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="note" placeholder="可选，如：主号、账号A..." style="width:300px" />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :loading="starting" @click="doStartScan" :disabled="!selectedPlatforms.length">
            <el-icon><Iphone /></el-icon> 开始扫码 ({{ selectedPlatforms.length }} 个平台)
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
                v-if="!isFinished && scanStatus !== 'need_verify'"
                type="warning"
                size="small"
                @click="doSkipPlatform"
                :loading="skipping"
              >
                跳过当前平台
              </el-button>
              <el-button
                v-if="!isFinished"
                size="small"
                @click="doRefreshQr"
                :loading="refreshing"
              >
                刷新二维码
              </el-button>
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
          <!-- 左侧：二维码（占更大空间，确保QR码清晰可扫） -->
          <el-col :span="16">
            <div class="qr-area">
              <!-- 身份验证弹窗处理 -->
              <div v-if="scanStatus === 'need_verify'" class="verify-area">
                <el-alert type="warning" :closable="false" show-icon>
                  <template #title>检测到身份验证弹窗，需要手动验证</template>
                </el-alert>
                <div v-if="qrBase64" class="qr-wrapper" style="margin: 12px 0">
                  <img :src="'data:image/png;base64,' + qrBase64" class="qr-img" style="max-height:300px" />
                </div>
                <div class="verify-form">
                  <el-button
                    type="primary"
                    :loading="sendingCode"
                    :disabled="countdown > 0"
                    @click="doSendVerifyCode"
                  >
                    {{ countdown > 0 ? `${countdown}s 后可重新发送` : (codeSent ? '重新发送验证码' : '发送短信验证码') }}
                  </el-button>
                  <div class="verify-input" v-if="codeSent">
                    <el-input
                      v-model="verifyCode"
                      placeholder="输入收到的验证码"
                      style="width: 200px; margin-right: 12px"
                      @keyup.enter="doSubmitVerifyCode"
                      clearable
                    />
                    <el-button type="success" :loading="submittingCode" @click="doSubmitVerifyCode">
                      提交验证码
                    </el-button>
                  </div>
                  <div v-if="verifyMsg" class="verify-msg" :class="verifyMsgType">
                    {{ verifyMsg }}
                  </div>
                </div>
              </div>
              <div v-else-if="scanStatus === 'starting'" class="qr-placeholder">
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
          <el-col :span="8">
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
import { startScan, getScanQrcode, getScanStatus, cancelScan, skipScanPlatform, refreshScanQr, sendVerifyCode, submitVerifyCode } from '../api'
import { ElMessage } from 'element-plus'

const PLATFORMS = { ks: '快手', wb: '微博', toutiao: '今日头条', bili: 'B站', dy: '抖音' }

const selectedPlatforms = ref(['ks', 'wb', 'toutiao', 'bili', 'dy'])
const note = ref('')
const scanMode = ref('force_new')
const execMode = ref('serial')
const starting = ref(false)
const cancelling = ref(false)
const skipping = ref(false)
const refreshing = ref(false)

const sessionId = ref('')
const scanStatus = ref('')
const statusMsg = ref('')
const qrBase64 = ref('')
const currentPlatform = ref('')
const platformsQueue = ref([])
const completedMap = ref({})
const skippedList = ref([])

// 身份验证相关
const verifyCode = ref('')
const codeSent = ref(false)
const sendingCode = ref(false)
const submittingCode = ref(false)
const verifyMsg = ref('')
const verifyMsgType = ref('')
const countdown = ref(0)
let countdownTimer = null

let pollTimer = null

const currentPlatformName = computed(() => PLATFORMS[currentPlatform.value] || currentPlatform.value)
const isFinished = computed(() => ['all_done', 'failed', 'cancelled'].includes(scanStatus.value))

const scanStatusType = computed(() => {
  const map = {
    starting: 'info', clicking_login: 'info', waiting: '',
    success: 'success', timeout: 'warning', need_verify: 'warning',
    all_done: 'success', failed: 'danger', cancelled: 'warning',
  }
  return map[scanStatus.value] || 'info'
})

function selectAllPlatforms() {
  selectedPlatforms.value = Object.keys(PLATFORMS)
}

function timelineType(plat) {
  if (completedMap.value[plat]) return 'success'
  if (skippedList.value.includes(plat)) return 'info'
  if (plat === currentPlatform.value) return 'primary'
  return 'info'
}

async function doStartScan() {
  if (!selectedPlatforms.value.length) {
    ElMessage.warning('请至少选择一个平台')
    return
  }
  starting.value = true
  try {
    const platformsStr = selectedPlatforms.value.join(',')
    const res = await startScan('all', note.value, scanMode.value, platformsStr, execMode.value)
    sessionId.value = res.cookie_id
    platformsQueue.value = res.platforms || selectedPlatforms.value
    scanStatus.value = 'starting'
    statusMsg.value = '启动中...'
    completedMap.value = {}
    skippedList.value = []
    qrBase64.value = ''
    codeSent.value = false
    verifyCode.value = ''
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

async function doSkipPlatform() {
  if (!sessionId.value) return
  skipping.value = true
  try {
    const res = await skipScanPlatform(sessionId.value)
    if (res.success) {
      ElMessage.info(res.message || '已跳过当前平台')
    }
  } catch (e) {
    ElMessage.error('跳过失败: ' + e.message)
  } finally {
    skipping.value = false
  }
}

async function doRefreshQr() {
  if (!sessionId.value) return
  refreshing.value = true
  try {
    const res = await refreshScanQr(sessionId.value)
    if (res.success) {
      ElMessage.success('二维码已刷新')
    }
  } catch (e) {
    ElMessage.error('刷新失败: ' + e.message)
  } finally {
    refreshing.value = false
  }
}

function startCountdown() {
  stopCountdown()
  countdown.value = 60
  countdownTimer = setInterval(() => {
    countdown.value--
    if (countdown.value <= 0) stopCountdown()
  }, 1000)
}

function stopCountdown() {
  if (countdownTimer) { clearInterval(countdownTimer); countdownTimer = null }
  countdown.value = 0
}

async function doSendVerifyCode() {
  if (!sessionId.value || countdown.value > 0) return
  sendingCode.value = true
  verifyMsg.value = ''
  try {
    const res = await sendVerifyCode(sessionId.value)
    if (res.success) {
      codeSent.value = true
      verifyCode.value = ''
      verifyMsg.value = res.message || '验证码已发送'
      verifyMsgType.value = 'success'
      ElMessage.success(res.message || '验证码已发送')
      startCountdown()
    } else {
      verifyMsg.value = res.message || '发送失败'
      verifyMsgType.value = 'error'
      ElMessage.warning(res.message || '发送失败')
    }
  } catch (e) {
    ElMessage.error('发送验证码失败: ' + e.message)
  } finally {
    sendingCode.value = false
  }
}

async function doSubmitVerifyCode() {
  if (!sessionId.value || !verifyCode.value.trim()) {
    ElMessage.warning('请输入验证码')
    return
  }
  submittingCode.value = true
  verifyMsg.value = ''
  try {
    const res = await submitVerifyCode(sessionId.value, verifyCode.value.trim())
    if (res.success) {
      ElMessage.success('验证码已提交，等待验证结果...')
      // 保持输入框可见，清空值让用户可重新输入（若验证失败）
      // 验证真正通过后 scanStatus 会变化，自动隐藏整个验证区域
      verifyCode.value = ''
      verifyMsg.value = '验证码已提交，等待结果...'
      verifyMsgType.value = 'success'
    } else {
      verifyMsg.value = res.message || '验证失败，请重新输入'
      verifyMsgType.value = 'error'
      ElMessage.error(res.message || '验证失败')
    }
  } catch (e) {
    ElMessage.error('提交验证码失败: ' + e.message)
  } finally {
    submittingCode.value = false
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
  stopCountdown()
  sessionId.value = ''
  qrBase64.value = ''
  scanStatus.value = ''
  codeSent.value = false
  verifyCode.value = ''
  verifyMsg.value = ''
}

onUnmounted(() => {
  stopPolling()
  stopCountdown()
})
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
.qr-img { max-width: 100%; min-width: 320px; border-radius: 8px; box-shadow: 0 2px 12px rgba(0,0,0,.12); }
.qr-tip { margin-top: 12px; color: #606266; font-size: 14px; }
.qr-sub-tip { margin-top: 4px; font-size: 12px; color: #c0c4cc; }
.loading-icon { animation: spin 1.2s linear infinite; }
@keyframes spin { from { transform: rotate(0) } to { transform: rotate(360deg) } }

.active-plat { font-weight: 700; color: #409eff; }
.plat-tag { margin-left: 8px; }
.mode-tip { margin-top: 6px; font-size: 12px; color: #909399; }
.platform-actions { margin-top: 8px; }

.verify-area { padding: 20px; }
.verify-form { margin-top: 16px; display: flex; flex-direction: column; align-items: center; gap: 12px; }
.verify-input { display: flex; align-items: center; margin-top: 12px; }
.verify-msg { margin-top: 8px; font-size: 13px; }
.verify-msg.success { color: #67c23a; }
.verify-msg.error { color: #f56c6c; }
</style>
