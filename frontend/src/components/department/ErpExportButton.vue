<template>
  <div class="flex flex-wrap items-center gap-2 text-xs" data-testid="erp-export">
    <template v-if="state">
      <span v-if="state.export?.status === 'sent'" class="text-emerald-700">已推送到 ERP<span v-if="state.export.erp_document_id">（单号 {{ state.export.erp_document_id }}）</span></span>
      <template v-else-if="state.configured">
        <button :disabled="busy" @click="push" data-testid="erp-push"
          class="rounded border border-indigo-200 px-2.5 py-1 text-indigo-700 hover:bg-indigo-50 disabled:opacity-50">
          {{ state.export?.status === 'failed' ? '重新推送到 ERP' : '推送到 ERP' }}
        </button>
        <span v-if="state.export?.status === 'failed'" class="text-red-600">上次失败：{{ state.export.last_error }}</span>
        <span v-else class="text-slate-400">同一张凭证重复点击只会推送一次</span>
      </template>
      <span v-else class="text-slate-400">没有配置 ERP 连接</span>
    </template>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { erpExport, erpExportStatus } from '../../api/financeExtras'
import { getErrorMessage } from '../../utils/request'
import { toastError, toastSuccess } from '../../utils/toast'

const props = defineProps<{ teamId: number; voucherId: number }>()
const state = ref<Awaited<ReturnType<typeof erpExportStatus>> | null>(null)
const busy = ref(false)

async function load() {
  try {
    state.value = await erpExportStatus(props.teamId, props.voucherId)
  } catch {
    state.value = null
  }
}

async function push() {
  busy.value = true
  try {
    const result = await erpExport(props.teamId, props.voucherId)
    toastSuccess(result.duplicate ? '这张凭证之前已经推送过' : '已推送到 ERP')
  } catch (e) {
    toastError(getErrorMessage(e, '推送失败'))
  } finally {
    busy.value = false
    await load()
  }
}

watch(() => props.voucherId, load)
onMounted(load)
</script>
