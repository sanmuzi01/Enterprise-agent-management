<template>
  <div class="space-y-3">
    <template v-for="(field, idx) in fields" :key="field.key || `${field.type}-${idx}`">
      <p v-if="field.type === 'note'" class="text-xs text-slate-500">{{ field.text }}</p>
      <p v-else-if="field.type === 'sum'" class="text-sm font-medium">{{ field.label }} ¥{{ sum(field) }}</p>
      <template v-else-if="field.type === 'list'">
        <div v-for="(item, i) in model[field.key!] || []" :key="i" class="rounded bg-slate-50 p-3 space-y-2">
          <div v-if="inline(field).length" class="grid gap-2" :class="gridClass(inline(field).length)">
            <WorkflowField v-for="sub in inline(field)" :key="sub.key" :field="sub" :model="item" />
          </div>
          <WorkflowField v-for="sub in stacked(field)" :key="sub.key" :field="sub" :model="item" />
          <button type="button" class="text-xs text-red-600" @click="model[field.key!].splice(i, 1)">删除这条{{ field.item }}</button>
        </div>
      </template>
      <WorkflowField v-else :field="field" :model="model" />
    </template>
  </div>
</template>

<script setup lang="ts">
import type { FormField } from '../api/automationWork'
import WorkflowField from './WorkflowField.vue'

const props = defineProps<{ fields: FormField[]; model: Record<string, any> }>()

// 列表项里的短字段并排显示，长文本和原文依据单独占一行。
const inline = (f: FormField) => (f.fields || []).filter(s => !s.wide && s.type !== 'evidence' && s.type !== 'textarea')
const stacked = (f: FormField) => (f.fields || []).filter(s => s.wide || s.type === 'evidence' || s.type === 'textarea')
const gridClass = (n: number) => (n >= 3 ? 'sm:grid-cols-3' : n === 2 ? 'sm:grid-cols-2' : '')
const sum = (f: FormField) =>
  ((props.model[f.list!] || []) as Record<string, any>[]).reduce((t, it) => t + Number(it[f.field!] || 0), 0).toFixed(2)
</script>
