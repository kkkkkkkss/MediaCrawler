<template>
  <el-container class="app-layout">
    <!-- 侧边栏 -->
    <el-aside :width="isCollapse ? '64px' : '220px'" class="app-aside">
      <div class="logo-area" @click="isCollapse = !isCollapse">
        <el-icon :size="24"><Monitor /></el-icon>
        <span v-show="!isCollapse" class="logo-text">数媒鉴</span>
      </div>
      <el-menu
        :default-active="$route.path"
        router
        :collapse="isCollapse"
        class="aside-menu"
      >
        <el-menu-item index="/">
          <el-icon><DataAnalysis /></el-icon>
          <template #title>概览</template>
        </el-menu-item>
        <el-menu-item index="/url-check">
          <el-icon><Link /></el-icon>
          <template #title>链接检测</template>
        </el-menu-item>
        <el-menu-item index="/report">
          <el-icon><Warning /></el-icon>
          <template #title>举报投诉</template>
        </el-menu-item>
        <el-menu-item index="/tasks">
          <el-icon><List /></el-icon>
          <template #title>任务管理</template>
        </el-menu-item>
        <el-menu-item index="/cookies">
          <el-icon><Key /></el-icon>
          <template #title>Cookie 管理</template>
        </el-menu-item>
        <el-menu-item index="/scan">
          <el-icon><Iphone /></el-icon>
          <template #title>扫码登录</template>
        </el-menu-item>
        <el-menu-item index="/callback">
          <el-icon><Connection /></el-icon>
          <template #title>回调设置</template>
        </el-menu-item>
      </el-menu>
    </el-aside>

    <!-- 主内容区 -->
    <el-container>
      <el-header class="app-header">
        <h2 class="page-title">{{ $route.meta.title }}</h2>
        <div class="header-right">
          <el-tag
            :type="apiOnline ? 'success' : 'danger'"
            effect="dark"
            size="small"
            style="cursor:pointer"
            @click="manualHealthCheck"
            :title="'点击手动刷新状态\n下次自动检查: ' + nextCheckLabel"
          >
            {{ apiOnline ? '后端已连接' : '后端离线' }}
          </el-tag>
        </div>
      </el-header>
      <el-main class="app-main">
        <router-view />
      </el-main>
    </el-container>
  </el-container>
</template>

<script setup>
import { ref, onMounted, onUnmounted, computed } from 'vue'
import { healthCheck } from './api'

const isCollapse = ref(false)
const apiOnline = ref(false)

/*
 * 递增式健康检查：
 * 启动 → 1min → 1min → 连续两次 200 OK 后 → 5min → 30min → 60min → 6h → 12h → 24h → 24h...
 * 任何一次失败则重置为 1min 间隔重新开始递增。
 * 点击右上角状态标签可手动触发检查。
 */
const INTERVALS_MS = [
  60_000,       // 1min (初始阶段，连续2次OK后进入下一级)
  60_000,       // 1min (第二次)
  300_000,      // 5min
  1_800_000,    // 30min
  3_600_000,    // 1h
  21_600_000,   // 6h
  43_200_000,   // 12h
  86_400_000,   // 24h
]

let healthTimer = null
let consecutiveOkCount = 0  // 连续成功次数
let intervalIndex = 0       // 当前使用的间隔级别
const nextCheckDelay = ref(0)

const nextCheckLabel = computed(() => {
  if (nextCheckDelay.value <= 0) return '检查中...'
  const sec = Math.round(nextCheckDelay.value / 1000)
  if (sec < 60) return `${sec}秒后`
  const min = Math.round(sec / 60)
  if (min < 60) return `${min}分钟后`
  const hr = Math.round(min / 60)
  return `${hr}小时后`
})

function getNextInterval() {
  // 前两次检查必须连续 OK 才能升级到更长间隔
  if (consecutiveOkCount < 2) return INTERVALS_MS[0]
  // 已连续2次OK，从第3个间隔(5min)开始递增
  const idx = Math.min(intervalIndex, INTERVALS_MS.length - 1)
  return INTERVALS_MS[idx]
}

async function doHealthCheck() {
  try {
    await healthCheck()
    apiOnline.value = true
    consecutiveOkCount++
    // 前两次OK时 intervalIndex 不变(停留在1min)，第二次OK后开始递增
    if (consecutiveOkCount >= 2) {
      intervalIndex = Math.min(intervalIndex + 1, INTERVALS_MS.length - 1)
    }
  } catch {
    apiOnline.value = false
    // 失败时重置递增
    consecutiveOkCount = 0
    intervalIndex = 0
  }
  scheduleNextCheck()
}

function scheduleNextCheck() {
  if (healthTimer) clearTimeout(healthTimer)
  const delay = getNextInterval()
  nextCheckDelay.value = delay
  healthTimer = setTimeout(doHealthCheck, delay)
}

function manualHealthCheck() {
  if (healthTimer) clearTimeout(healthTimer)
  doHealthCheck()
}

onMounted(() => {
  doHealthCheck()
})

onUnmounted(() => {
  if (healthTimer) clearTimeout(healthTimer)
})
</script>

<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
html, body, #app { height: 100%; }

.app-layout { height: 100vh; }

.app-aside {
  background: #1d1e1f;
  transition: width 0.3s;
  overflow: hidden;
}
.logo-area {
  height: 56px;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  color: #409eff;
  cursor: pointer;
  font-weight: bold;
  border-bottom: 1px solid #333;
}
.logo-text { font-size: 16px; white-space: nowrap; }

.aside-menu {
  border-right: none;
  background: #1d1e1f;
}
.aside-menu .el-menu-item {
  color: #bbb;
}
.aside-menu .el-menu-item:hover,
.aside-menu .el-menu-item.is-active {
  background: #263445 !important;
  color: #409eff;
}

.app-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-bottom: 1px solid #e4e7ed;
  background: #fff;
}
.page-title { font-size: 18px; font-weight: 600; color: #303133; }

.app-main {
  background: #f5f7fa;
  overflow-y: auto;
}
</style>
