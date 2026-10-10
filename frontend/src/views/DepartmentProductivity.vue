<template>
  <div class="h-screen overflow-y-auto bg-transparent p-6">
    <div class="mx-auto max-w-6xl space-y-4">
      <header class="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h1 class="text-base font-semibold text-slate-950">部门提效</h1>
          <p class="text-xs text-slate-500">{{ deptStore.currentDepartment?.name || '当前部门' }} · 只有部门负责人和企业管理员能看</p>
        </div>
        <RouterLink to="/department" class="rounded border border-slate-200 bg-white px-3 py-1.5 text-sm text-slate-700 hover:bg-slate-50">返回部门工作台</RouterLink>
      </header>
      <p v-if="!deptStore.currentTeamId" class="rounded border border-dashed border-slate-200 py-10 text-center text-sm text-slate-400">你还不属于任何部门</p>
      <ProductivityDashboard v-else :key="deptStore.currentTeamId" :load="(days: number) => departmentProductivity(deptStore.currentTeamId!, days)" />
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted } from 'vue'
import { RouterLink } from 'vue-router'
import ProductivityDashboard from '../components/ProductivityDashboard.vue'
import { departmentProductivity } from '../api/productivity'
import { useCurrentDepartmentStore } from '../stores/currentDepartment'

const deptStore = useCurrentDepartmentStore()
onMounted(() => { void deptStore.load() })
</script>
