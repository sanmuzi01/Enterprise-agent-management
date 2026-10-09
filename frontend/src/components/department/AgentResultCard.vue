<template>
  <article class="w-full rounded-lg border bg-white px-3.5 py-3 text-sm"
    :class="card.kind === 'budget' ? 'border-slate-200' : 'border-indigo-100'" data-testid="agent-card">
    <header class="flex items-start justify-between gap-2">
      <h4 class="font-semibold text-slate-900">{{ card.title }}</h4>
      <span v-if="statusText" class="shrink-0 rounded px-2 py-0.5 text-xs" :class="badgeClass">{{ statusText }}</span>
    </header>
    <dl class="mt-2 grid grid-cols-[4.5rem_1fr] gap-x-3 gap-y-1">
      <template v-for="f in card.fields" :key="f.label">
        <dt class="text-xs leading-5 text-slate-400">{{ f.label }}</dt>
        <dd class="text-xs leading-5 tabular-nums" :class="f.warn ? 'text-amber-700' : 'text-slate-800'">{{ f.value }}</dd>
      </template>
    </dl>
    <p v-if="error" class="mt-2 text-xs text-red-600">{{ error }}</p>
    <footer v-if="showSubmit || card.section" class="mt-3 flex flex-wrap gap-2">
      <button v-if="showSubmit" @click="submit" :disabled="busy" data-testid="agent-card-submit"
        class="rounded bg-indigo-600 px-3 py-1 text-xs font-medium text-white hover:bg-indigo-700 disabled:opacity-50">
        {{ busy ? '提交中…' : submitLabel }}
      </button>
      <button v-if="card.section" @click="emit('open', card.section)" data-testid="agent-card-open"
        class="rounded border border-slate-200 px-3 py-1 text-xs text-slate-700 hover:bg-slate-50">
        {{ showSubmit ? '去修改' : '在工作台中查看' }}
      </button>
    </footer>
  </article>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import type { BusinessCard } from '../../utils/agentCards'
import { statusBadgeClass, statusLabel } from '../../utils/requestStatus'
import { submitMyExpenseClaim } from '../../api/departmentFinance'
import { submitMyLeaveRequest } from '../../api/departmentLeave'
import { submitMyPurchaseRequest } from '../../api/departmentProcurement'
import { confirmFollowup } from '../../api/departmentCrm'
import { getErrorMessage } from '../../utils/request'

const props = defineProps<{ card: BusinessCard }>()
const emit = defineEmits<{
  (e: 'open', section: 'office' | 'business'): void
  /** 业务数据变了（提交了草稿），工作台据此刷新业务模块和概览数字 */
  (e: 'changed'): void
}>()

const busy = ref(false)
const error = ref('')
// 提交成功后卡片自己更新状态，不用等助手再回一句
const submittedStatus = ref<string | null>(null)

const status = computed(() => submittedStatus.value ?? props.card.status)
const statusText = computed(() => {
  if (submittedStatus.value) return props.card.kind === 'followup' ? '已记录' : statusLabel(submittedStatus.value)
  return props.card.statusText
})
const badgeClass = computed(() => (status.value === 'CONFIRMED' ? 'bg-emerald-50 text-emerald-700' : statusBadgeClass(status.value || '')))
const showSubmit = computed(() => props.card.canSubmit && status.value === 'DRAFT' && props.card.id != null)
const submitLabel = computed(() => (props.card.kind === 'followup' ? '确认记录' : '确认提交'))

async function submit() {
  const id = props.card.id
  if (id == null || busy.value) return
  busy.value = true
  error.value = ''
  try {
    const result: { status: string } =
      props.card.kind === 'expense' ? await submitMyExpenseClaim(id)
      : props.card.kind === 'leave' ? await submitMyLeaveRequest(id)
      : props.card.kind === 'purchase' ? await submitMyPurchaseRequest(id)
      : await confirmFollowup(id)
    submittedStatus.value = result.status
    emit('changed')
  } catch (e) {
    error.value = getErrorMessage(e, '提交失败，请稍后重试')
  } finally {
    busy.value = false
  }
}
</script>
