import { createRouter, createWebHistory } from 'vue-router'
import { hasSession } from '../utils/session'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/',
      redirect: '/login',
    },
    {
      path: '/login',
      name: 'Login',
      component: () => import('../views/Login.vue'),
    },
    {
      // 登录后的落脚点：属于某个部门的员工进部门工作台，否则进个人工作台（见下方守卫）
      path: '/home',
      component: () => import('../views/AgentList.vue'),
    },
    {
      path: '/agents',
      name: 'Agents',
      component: () => import('../views/AgentList.vue'),
    },
    {
      path: '/llm-configs',
      name: 'LlmConfigs',
      component: () => import('../views/LlmConfig.vue'),
    },
    {
      path: '/settings',
      name: 'Settings',
      component: () => import('../views/Settings.vue'),
    },
    {
      path: '/admin',
      component: () => import('../views/admin/AdminLayout.vue'),
      children: [
        { path: '', redirect: '/admin/overview' },
        { path: 'overview', name: 'AdminOverview', component: () => import('../views/admin/AdminOverview.vue') },
        { path: 'users', name: 'AdminUsers', component: () => import('../views/admin/AdminUsers.vue') },
        { path: 'tasks', name: 'AdminTasks', component: () => import('../views/admin/AdminTasks.vue') },
        { path: 'usage', name: 'AdminUsage', component: () => import('../views/admin/AdminUsage.vue') },
        { path: 'logs', name: 'AdminLogs', component: () => import('../views/admin/AdminLogs.vue') },
        { path: 'knowledge-spaces', name: 'AdminKnowledgeSpaces', component: () => import('../views/admin/AdminKnowledgeSpaces.vue') },
        { path: 'organization', name: 'AdminOrganization', component: () => import('../views/admin/AdminOrganization.vue') },
        { path: 'plans', name: 'AdminPlans', component: () => import('../views/admin/AdminPlans.vue') },
        { path: 'skills', name: 'AdminSkills', component: () => import('../views/SkillList.vue') },
        { path: 'issues', name: 'AdminIssues', component: () => import('../views/admin/AdminIssues.vue') },
        { path: 'diagnose', name: 'AdminDiagnose', component: () => import('../views/admin/AdminDiagnose.vue') },
      ],
    },
    {
      path: '/department',
      name: 'DepartmentWorkstation',
      component: () => import('../views/DepartmentWorkstation.vue'),
    },
    {
      path: '/todos',
      name: 'TodoCenter',
      component: () => import('../views/TodoCenter.vue'),
    },
    {
      path: '/skills',
      name: 'Skills',
      component: () => import('../views/SkillList.vue'),
    },
    {
      path: '/tasks',
      name: 'Tasks',
      component: () => import('../views/TaskCenter.vue'),
    },
    {
      path: '/web-monitor',
      name: 'WebMonitor',
      component: () => import('../views/WebMonitor.vue'),
    },
    {
      path: '/widgets',
      name: 'WidgetStudio',
      component: () => import('../views/WidgetStudio.vue'),
    },
    {
      path: '/pipelines',
      name: 'AgentPipelines',
      component: () => import('../views/AgentPipelines.vue'),
    },
    {
      path: '/knowledge-spaces',
      name: 'KnowledgeSpaceCenter',
      component: () => import('../views/knowledge/SpaceCenter.vue'),
    },
    {
      path: '/knowledge-spaces/:id',
      name: 'KnowledgeSpaceDetail',
      component: () => import('../views/knowledge/SpaceDetail.vue'),
      props: true,
    },
    {
      path: '/knowledge-spaces/:id/debug',
      name: 'KnowledgeSpaceDebug',
      component: () => import('../views/knowledge/RagDebugConsole.vue'),
      props: true,
    },
    {
      path: '/knowledge-spaces/:id/health',
      name: 'KnowledgeSpaceHealth',
      component: () => import('../views/knowledge/SpaceHealth.vue'),
      props: true,
    },
    // 助手空间：聊天 / 知识库 / 记忆 / 运行检查 统一收在 /agents/:agentId/* 下
    { path: '/agents/:agentId', redirect: (to) => `/agents/${to.params.agentId}/chat` },
    {
      path: '/agents/:agentId/chat',
      name: 'Chat',
      component: () => import('../views/Chat.vue'),
      props: true,
    },
    {
      path: '/agents/:agentId/knowledge',
      name: 'AgentKnowledge',
      component: () => import('../views/AgentKnowledgePanel.vue'),
      props: true,
    },
    {
      path: '/agents/:agentId/memory',
      name: 'Memory',
      component: () => import('../views/Memory.vue'),
      props: true,
    },
    {
      path: '/agents/:agentId/tools',
      name: 'AgentApiConnectors',
      component: () => import('../views/AgentApiConnectors.vue'),
      props: true,
    },
    {
      path: '/agents/:agentId/debug',
      name: 'AgentDebug',
      component: () => import('../views/AgentDebug.vue'),
      props: true,
    },
    // 旧链接兼容重定向（不删功能）
    { path: '/chat/:agentId', redirect: (to) => `/agents/${to.params.agentId}/chat` },
    { path: '/memory/:agentId', redirect: (to) => `/agents/${to.params.agentId}/memory` },
    { path: '/knowledge/:agentId', redirect: (to) => `/agents/${to.params.agentId}/knowledge` },
  ],
})

/** 当前用户是否属于至少一个部门（读不到时当作没有，退回个人工作台，不挡住登录）。 */
async function hasDepartment(): Promise<boolean> {
  try {
    const { useCurrentDepartmentStore } = await import('../stores/currentDepartment')
    const store = useCurrentDepartmentStore()
    await store.load(true)
    return store.departments.length > 0
  } catch {
    return false
  }
}

// 路由守卫：未登录跳转登录页
router.beforeEach(async (to, _from) => {
  const token = hasSession()   // 令牌在 HttpOnly Cookie 里，这里只判断会话是否还在；真正的鉴权在后端
  if (to.path !== '/login' && !token) {
    return '/login'
  }
  const user = JSON.parse(localStorage.getItem('user') || 'null')
  if (token && to.path === '/login') {
    return user?.is_admin ? '/admin' : '/home'
  }
  if (to.path === '/home') {
    if (user?.is_admin) return '/admin'
    return (await hasDepartment()) ? '/department' : '/agents'
  }
  if (to.path.startsWith('/admin')) {
    if (!user?.is_admin) return '/agents'
  }
  // 管理员发布完部门 Agent 之后自己也要能进部门工作台看看效果，不能被强制弹回 /admin。
  if (user?.is_admin && !to.path.startsWith('/admin') && !['/department', '/todos'].includes(to.path)) {
    return '/admin'
  }
  return true
})

export default router
