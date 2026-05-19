// Vue Router 路由配置
import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  { path: '/', name: 'Dashboard', component: () => import('../views/Dashboard.vue'), meta: { title: '概览' } },
  { path: '/url-check', name: 'UrlCheck', component: () => import('../views/UrlCheck.vue'), meta: { title: '链接检测' } },
  { path: '/report', name: 'ReportComplaint', component: () => import('../views/ReportComplaint.vue'), meta: { title: '举报投诉' } },
  { path: '/tasks', name: 'TaskList', component: () => import('../views/TaskList.vue'), meta: { title: '任务管理' } },
  { path: '/cookies', name: 'CookieManage', component: () => import('../views/CookieManage.vue'), meta: { title: 'Cookie 管理' } },
  { path: '/scan', name: 'ScanLogin', component: () => import('../views/ScanLogin.vue'), meta: { title: '扫码登录' } },
  { path: '/callback', name: 'CallbackSettings', component: () => import('../views/CallbackSettings.vue'), meta: { title: '回调设置' } },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

router.beforeEach((to) => {
  document.title = `${to.meta.title || ''} - MediaCrawler`
})

export default router
