<template>
  <p v-if="field.type === 'evidence'" class="text-xs text-slate-500">{{ field.label }}：{{ model[field.key!] }}</p>
  <label v-else class="block text-sm">{{ field.label }}
    <textarea v-if="field.type === 'textarea'" v-model="model[field.key!]" :rows="field.rows || 3" :maxlength="field.max"
      class="mt-1 w-full rounded border p-2" />
    <select v-else-if="field.type === 'select'" v-model="model[field.key!]" class="mt-1 w-full rounded border p-2">
      <option v-if="field.placeholder" :value="null" disabled>{{ field.placeholder }}</option>
      <option v-for="o in options" :key="o.value" :value="o.value">{{ o.label }}</option>
    </select>
    <input v-else-if="field.type === 'integer'" v-model.number="model[field.key!]" type="number" step="1"
      :min="field.min" :max="field.max" class="mt-1 w-full rounded border p-2" />
    <input v-else-if="field.type === 'money'" v-model="model[field.key!]" type="number" min="0.01" step="0.01"
      class="mt-1 w-full rounded border p-2" />
    <input v-else-if="field.type === 'date'" v-model="model[field.key!]" type="date" class="mt-1 w-full rounded border p-2" />
    <input v-else v-model="model[field.key!]" :maxlength="field.max" :placeholder="field.placeholder"
      class="mt-1 w-full rounded border p-2" />
  </label>
</template>

<script setup lang="ts">
import { computed, inject } from 'vue'
import type { FormField } from '../api/automationWork'

const props = defineProps<{ field: FormField; model: Record<string, any> }>()
// options_from 的下拉选项由外层（如 AutomationWorkPanel）按当前部门动态提供
const dynamic = inject<Record<string, { value: string | number; label: string }[]>>('workflowOptions', {})
const options = computed(() => (props.field.options_from ? dynamic[props.field.options_from] || [] : props.field.options || []))
</script>
