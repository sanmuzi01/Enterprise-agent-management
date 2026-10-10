<template>
  <form class="flex flex-wrap items-center gap-2 text-xs text-slate-600" data-testid="saved-time-feedback" @submit.prevent="submit">
    <label :for="inputId">这次大概帮你省了多少分钟？</label>
    <input :id="inputId" v-model.number="minutes" type="number" min="0" max="600" step="1"
      class="h-7 w-20 rounded border border-slate-300 px-1.5" :disabled="busy" />
    <button type="submit" :disabled="busy || minutes === null || minutes < 0"
      class="rounded border border-slate-300 bg-white px-2 py-0.5 hover:bg-slate-50 disabled:opacity-50">{{ done ? '已更新' : '提交' }}</button>
    <span class="text-slate-400">只用于统计，和系统估算的时间分开记</span>
  </form>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { reportSaved } from '../../api/productivity'
import { getErrorMessage } from '../../utils/request'
import { toastError, toastSuccess } from '../../utils/toast'

const props = defineProps<{ sourceKey: string }>()
const minutes = ref<number | null>(null)
const busy = ref(false)
const done = ref(false)
const inputId = computed(() => `saved-${props.sourceKey.replace(/[^a-zA-Z0-9]/g, '-')}`)

async function submit() {
  if (minutes.value === null) return
  busy.value = true
  try {
    await reportSaved(props.sourceKey, minutes.value)
    done.value = true
    toastSuccess('谢谢反馈')
  } catch (e) {
    toastError(getErrorMessage(e, '提交失败'))
  } finally {
    busy.value = false
  }
}
</script>
