<template>
  <div class="space-y-2 rounded border border-slate-200 bg-white p-3 text-xs" data-testid="invoice-recognizer">
    <div class="flex flex-wrap items-center justify-between gap-2">
      <p class="font-medium text-slate-800">从发票识别</p>
      <label class="cursor-pointer rounded border border-slate-300 px-2.5 py-1 text-slate-700 hover:bg-slate-50">
        {{ busy ? '识别中…' : invoice ? '换一张' : '选择发票（PDF / 图片）' }}
        <input type="file" accept=".pdf,.png,.jpg,.jpeg" class="sr-only" :disabled="busy" data-testid="invoice-file" @change="onFile" />
      </label>
    </div>
    <template v-if="invoice">
      <p class="text-slate-500">
        {{ invoice.method === 'ocr' ? '图片 / 扫描件经文字识别，所有字段都请逐项核对。' : '文字版发票。' }}
        黄色字段识别把握不大，确认前请逐项核对（勾选“已核对”或直接改正）。
      </p>
      <ul class="space-y-1">
        <li v-for="c in invoice.checks" :key="c.code + c.text" :class="checkClass[c.level]" class="rounded px-2 py-1">{{ c.text }}</li>
      </ul>
      <div class="grid gap-1.5 sm:grid-cols-2">
        <div v-for="key in FIELD_ORDER" :key="key" class="rounded border px-2 py-1.5"
          :class="needsReview(key) ? 'border-amber-300 bg-amber-50' : 'border-slate-200'">
          <div class="flex items-center justify-between gap-2">
            <label :for="`inv-${key}`" class="text-slate-500">{{ invoice.labels[key] }}</label>
            <span class="tabular-nums text-slate-400">{{ confidenceText(key) }}</span>
          </div>
          <input :id="`inv-${key}`" v-model="values[key]" :disabled="locked" class="mt-1 h-7 w-full rounded border border-slate-300 px-1.5" />
          <label v-if="needsReview(key)" class="mt-1 flex items-center gap-1 text-amber-800">
            <input v-model="checked[key]" type="checkbox" :disabled="locked" />已核对
          </label>
        </div>
      </div>
      <div class="flex flex-wrap items-center gap-2">
        <button v-if="!locked" :disabled="busy" @click="confirm" data-testid="invoice-confirm"
          class="rounded bg-indigo-600 px-2.5 py-1 text-white hover:bg-indigo-700 disabled:opacity-50">确认并填入报销单</button>
        <button v-else :disabled="busy" @click="emit('use', invoice!)" data-testid="invoice-use"
          class="rounded bg-indigo-600 px-2.5 py-1 text-white hover:bg-indigo-700 disabled:opacity-50">填入报销单</button>
        <button :disabled="busy" @click="discard" class="rounded border border-slate-300 px-2.5 py-1 text-slate-600 hover:bg-slate-50">作废</button>
        <span v-if="locked" class="text-emerald-700">已确认</span>
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import * as api from '../../api/financeExtras'
import type { InvoiceExtraction } from '../../api/financeExtras'
import { getErrorMessage } from '../../utils/request'
import { toastError, toastSuccess } from '../../utils/toast'

const props = defineProps<{ teamId: number }>()
const emit = defineEmits<{ (e: 'use', invoice: InvoiceExtraction): void }>()

const FIELD_ORDER = ['invoice_number', 'invoice_code', 'issued_at', 'total_amount', 'amount_without_tax', 'tax_amount',
  'seller_name', 'seller_tax_id', 'buyer_name', 'buyer_tax_id', 'invoice_type']
const checkClass = { error: 'bg-red-50 text-red-700', warning: 'bg-amber-50 text-amber-800', info: 'bg-slate-50 text-slate-600' }

const invoice = ref<InvoiceExtraction | null>(null)
const values = reactive<Record<string, string>>({})
const checked = reactive<Record<string, boolean>>({})
const busy = ref(false)
const locked = computed(() => invoice.value?.status === 'confirmed')

const needsReview = (key: string) => !!invoice.value?.needs_review.includes(key)
const confidenceText = (key: string) => {
  const c = invoice.value?.confidence[key]
  return c === undefined || !invoice.value?.fields[key] ? '未识别' : `把握 ${Math.round(c * 100)}%`
}

function load(next: InvoiceExtraction) {
  invoice.value = next
  for (const key of FIELD_ORDER) {
    values[key] = next.fields[key] ?? ''
    checked[key] = false
  }
}

async function onFile(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file) return
  busy.value = true
  try {
    load(await api.extractInvoice(props.teamId, file))
  } catch (e) {
    toastError(getErrorMessage(e, '识别失败'))
  } finally {
    busy.value = false
  }
}

async function confirm() {
  if (!invoice.value) return
  const current = invoice.value
  const changed: Record<string, string | null> = {}
  for (const key of FIELD_ORDER) if ((current.fields[key] ?? '') !== values[key]) changed[key] = values[key] || null
  const confirmedFields = FIELD_ORDER.filter((k) => checked[k] || k in changed)
  busy.value = true
  try {
    load(await api.confirmInvoice(current.id, changed, confirmedFields))
    toastSuccess('发票已确认')
    emit('use', invoice.value!)
  } catch (e) {
    toastError(getErrorMessage(e, '确认失败'))
  } finally {
    busy.value = false
  }
}

async function discard() {
  if (!invoice.value) return
  try {
    await api.discardInvoice(invoice.value.id)
    invoice.value = null
  } catch (e) {
    toastError(getErrorMessage(e, '作废失败'))
  }
}
</script>
