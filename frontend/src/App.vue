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
          <el-tag :type="apiOnline ? 'success' : 'danger'" effect="dark" size="small">
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
import { ref, onMounted, onUnmounted } from 'vue'
import { healthCheck } from './api'

const isCollapse = ref(false)
const apiOnline = ref(false)
let timer = null

async function checkApi() {
  try {
    await healthCheck()
    apiOnline.value = true
  } catch {
    apiOnline.value = false
  }
}

onMounted(() => {
  checkApi()
  timer = setInterval(checkApi, 15000)
})
onUnmounted(() => { if (timer) clearInterval(timer) })
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
