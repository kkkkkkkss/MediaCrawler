<template>
  <div class="dashboard">
    <!-- 统计卡片 -->
    <el-row :gutter="20" class="stat-row">
      <el-col :span="6">
        <el-card shadow="hover" class="stat-card">
          <div class="stat-icon" style="background:#409eff"><el-icon :size="28"><Connection /></el-icon></div>
          <div class="stat-info">
            <div class="stat-value">{{ stats.apiStatus }}</div>
            <div class="stat-label">API 状态</div>
          </div>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="hover" class="stat-card">
          <div class="stat-icon" style="background:#67c23a"><el-icon :size="28"><Key /></el-icon></div>
          <div class="stat-info">
            <div class="stat-value">{{ stats.validCookies }}</div>
            <div class="stat-label">有效 Cookie</div>
          </div>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="hover" class="stat-card">
          <div class="stat-icon" style="background:#e6a23c"><el-icon :size="28"><Warning /></el-icon></div>
          <div class="stat-info">
            <div class="stat-value">{{ stats.invalidCookies }}</div>
            <div class="stat-label">失效 Cookie</div>
          </div>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="hover" class="stat-card">
          <div class="stat-icon" style="background:#909399"><el-icon :size="28"><PieChart /></el-icon></div>
          <div class="stat-info">
            <div class="stat-value">{{ stats.platformCount }}</div>
            <div class="stat-label">覆盖平台数</div>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <!-- 支持平台 -->
    <el-card shadow="never" class="section-card">
      <template #header><span class="section-title">支持采集平台</span></template>
      <div class="platform-tags">
        <el-tag v-for="p in supportedPlatforms" :key="p.code" :type="p.type" size="large" effect="plain" class="platform-tag">
          {{ p.name }}
        </el-tag>
      </div>
    </el-card>

    <!-- 各平台 Cookie 明细 -->
    <el-card shadow="never" class="section-card" v-if="platformStats.length">
      <template #header><span class="section-title">各平台 Cookie 状态</span></template>
      <el-table :data="platformStats" stripe>
        <el-table-column prop="platform" label="平台" width="120" />
        <el-table-column prop="total" label="总数" width="100" />
        <el-table-column prop="valid" label="有效" width="100">
          <template #default="{ row }"><el-tag type="success">{{ row.valid }}</el-tag></template>
        </el-table-column>
        <el-table-column prop="invalid" label="失效" width="100">
          <template #default="{ row }"><el-tag type="danger">{{ row.invalid }}</el-tag></template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 快捷入口 -->
    <el-card shadow="never" class="section-card">
      <template #header><span class="section-title">快捷操作</span></template>
      <el-row :gutter="16">
        <el-col :span="6">
          <el-button type="primary" size="large" class="quick-btn" @click="$router.push('/url-check')">
            <el-icon><Link /></el-icon> 链接检测
          </el-button>
        </el-col>
        <el-col :span="6">
          <el-button type="success" size="large" class="quick-btn" @click="$router.push('/tasks')">
            <el-icon><List /></el-icon> 任务管理
          </el-button>
        </el-col>
        <el-col :span="6">
          <el-button type="warning" size="large" class="quick-btn" @click="$router.push('/cookies')">
            <el-icon><Key /></el-icon> Cookie 管理
          </el-button>
        </el-col>
        <el-col :span="6">
          <el-button type="info" size="large" class="quick-btn" @click="$router.push('/scan')">
            <el-icon><Iphone /></el-icon> 扫码登录
          </el-button>
        </el-col>
      </el-row>
    </el-card>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { healthCheck, listCookies } from '../api'
import { ElMessage } from 'element-plus'

const PLATFORM_NAMES = { dy: '抖音', ks: '快手', bili: 'B站', toutiao: '今日头条', xhs: '小红书', wb: '微博' }

const supportedPlatforms = [
  { code: 'dy', name: '抖音', type: '' },
  { code: 'ks', name: '快手', type: 'success' },
  { code: 'bili', name: 'B站', type: 'primary' },
  { code: 'toutiao', name: '今日头条', type: 'danger' },
  { code: 'wb', name: '微博', type: 'warning' },
  // { code: 'xhs', name: '小红书', type: 'info' },
]

const stats = reactive({
  apiStatus: '检测中...',
  validCookies: 0,
  invalidCookies: 0,
  platformCount: 0,
})
const platformStats = ref([])

onMounted(async () => {
  try {
    await healthCheck()
    stats.apiStatus = '正常'
  } catch {
    stats.apiStatus = '离线'
  }

  try {
    const res = await listCookies()
    const s = res.stats || {}
    let valid = 0, invalid = 0
    const rows = []
    for (const [key, val] of Object.entries(s)) {
      valid += val.valid || 0
      invalid += val.invalid || 0
      rows.push({ platform: PLATFORM_NAMES[key] || key, ...val })
    }
    stats.validCookies = valid
    stats.invalidCookies = invalid
    stats.platformCount = rows.length
    platformStats.value = rows
  } catch (e) {
    ElMessage.warning('Cookie 池数据加载失败: ' + e.message)
  }
})
</script>

<style scoped>
.dashboard { max-width: 1200px; margin: 0 auto; }
.stat-row { margin-bottom: 20px; }
.stat-card {
  display: flex;
  align-items: center;
  padding: 12px 0;
}
.stat-card :deep(.el-card__body) {
  display: flex;
  align-items: center;
  gap: 16px;
  width: 100%;
}
.stat-icon {
  width: 56px; height: 56px;
  border-radius: 12px;
  display: flex; align-items: center; justify-content: center;
  color: #fff;
  flex-shrink: 0;
}
.stat-value { font-size: 24px; font-weight: 700; color: #303133; }
.stat-label { font-size: 13px; color: #909399; margin-top: 2px; }
.section-card { margin-bottom: 20px; }
.section-title { font-weight: 600; font-size: 15px; }
.platform-tags { display: flex; flex-wrap: wrap; gap: 12px; }
.platform-tag { font-size: 15px; padding: 8px 20px; }
.quick-btn { width: 100%; height: 60px; font-size: 15px; }
</style>
