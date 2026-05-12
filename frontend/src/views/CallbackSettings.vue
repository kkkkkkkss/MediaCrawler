<template>
  <div class="callback-settings">
    <el-card shadow="never">
      <template #header><span class="section-title">全局回调配置</span></template>
      <el-form :model="form" label-width="140px" class="config-form" v-loading="loading">
        <el-form-item label="启用全局回调">
          <el-switch v-model="form.enabled" />
          <span class="tip-text">开启后，所有任务完成时会自动 POST 结果到回调地址</span>
        </el-form-item>
        <el-form-item label="全局回调地址">
          <el-input v-model="form.url" placeholder="如 https://your-agent-hub.com/api/callback" clearable />
          <div class="tip-text">各任务提交时可传入 callback_url 覆盖此全局地址</div>
        </el-form-item>
        <el-form-item label="最大重试次数">
          <el-input-number v-model="form.max_retries" :min="0" :max="10" />
        </el-form-item>
        <el-form-item label="重试间隔(秒)">
          <el-input v-model="retryIntervalsStr" placeholder="如 5,15,30" />
          <div class="tip-text">逗号分隔的秒数，依次使用</div>
        </el-form-item>
        <el-form-item>
          <el-button type="primary" @click="saveConfig" :loading="saving">保存配置</el-button>
          <el-button @click="loadConfig">重新加载</el-button>
        </el-form-item>
      </el-form>
    </el-card>

    <el-card shadow="never" style="margin-top:16px">
      <template #header><span class="section-title">回调 Payload 规范</span></template>
      <div class="doc-section">
        <h4>主结果回调 (event: task_completed)</h4>
        <pre class="code-block">{{ mainPayloadExample }}</pre>
        <h4 style="margin-top:16px">评论回调 (event: comments_ready)</h4>
        <pre class="code-block">{{ commentsPayloadExample }}</pre>
        <div class="tip-text" style="margin-top:12px">
          说明：如果任务开启了评论抓取，会分两次 POST：先发主结果，再发评论数据。
          如果提交任务时传了 callback_url，优先用任务级地址；未传则使用上方的全局地址。
        </div>
      </div>
    </el-card>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { getCallbackConfig, updateCallbackConfig } from '../api'
import { ElMessage } from 'element-plus'

const form = ref({ enabled: false, url: '', max_retries: 3, retry_intervals: [5, 15, 30] })
const loading = ref(false)
const saving = ref(false)

const retryIntervalsStr = computed({
  get: () => (form.value.retry_intervals || []).join(','),
  set: (val) => {
    form.value.retry_intervals = val.split(',').map(s => parseInt(s.trim())).filter(n => !isNaN(n))
  }
})

const mainPayloadExample = `{
  "event": "task_completed",
  "task_id": "batch-abc123",
  "status": "completed",
  "timestamp": "2026-05-12T15:30:00+00:00",
  "data": {
    "task_id": "batch-abc123",
    "total": 3,
    "completed_at": "2026-05-12T15:30:00",
    "results": [
      {
        "id": 1,
        "url": "https://www.douyin.com/video/xxx",
        "platform": "dy",
        "platform_name": "抖音",
        "is_valid": true,
        "praise_count": 100,
        "reply_count": 50,
        "visit_count": 1000,
        "share_count": 20,
        "author": "xxx",
        "title": "xxx"
      }
    ]
  }
}`

const commentsPayloadExample = `{
  "event": "comments_ready",
  "task_id": "batch-abc123",
  "status": "completed",
  "timestamp": "2026-05-12T15:30:05+00:00",
  "data": {
    "task_id": "batch-abc123",
    "total_comments": 30,
    "results": [
      {
        "content_url": "https://www.douyin.com/video/xxx",
        "platform": "dy",
        "comments": [
          {
            "comment_id": "xxx",
            "author_name": "用户A",
            "comment_text": "好看!",
            "comment_like_count": 5,
            "comment_time": "2026-05-10 12:00:00"
          }
        ]
      }
    ]
  }
}`

async function loadConfig() {
  loading.value = true
  try {
    const res = await getCallbackConfig()
    form.value = { ...res }
  } catch (e) {
    ElMessage.error('加载配置失败: ' + e.message)
  } finally {
    loading.value = false
  }
}

async function saveConfig() {
  saving.value = true
  try {
    const res = await updateCallbackConfig(form.value)
    form.value = { ...res }
    ElMessage.success('配置已保存')
  } catch (e) {
    ElMessage.error('保存失败: ' + e.message)
  } finally {
    saving.value = false
  }
}

onMounted(loadConfig)
</script>

<style scoped>
.callback-settings { max-width: 900px; margin: 0 auto; }
.config-form { max-width: 700px; padding: 16px 0; }
.section-title { font-weight: 600; }
.tip-text { margin-left: 8px; color: #909399; font-size: 13px; }
.doc-section h4 { color: #303133; font-size: 14px; margin-bottom: 8px; }
.code-block {
  background: #1e1e1e; color: #d4d4d4; padding: 12px 16px;
  border-radius: 6px; font-family: 'Consolas', monospace;
  font-size: 12px; white-space: pre; overflow-x: auto;
  max-height: 300px;
}
</style>
