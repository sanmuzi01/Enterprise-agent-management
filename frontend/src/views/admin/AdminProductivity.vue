<template>
  <div class="p-6">
    <ProductivityDashboard :load="adminProductivity" />
  </div>
</template>

<script setup lang="ts">
import ProductivityDashboard from '../../components/ProductivityDashboard.vue'
import { adminProductivity } from '../../api/productivity'
</script>
