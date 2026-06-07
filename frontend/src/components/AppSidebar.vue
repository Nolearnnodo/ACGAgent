<script setup lang="ts">
import { computed } from 'vue'
import { RouterLink, useRoute } from 'vue-router'

import { useAuthStore } from '../stores/auth'

const route = useRoute()
const authStore = useAuthStore()

const navItems = computed(() => {
  // 科研阶段：古籍模块对所有登录用户开放（后端权限同步放开）
  void authStore.user
  return [
    { label: '对话中心', to: '/chat', active: route.path.startsWith('/chat') },
    { label: '古籍上传', to: '/passages/upload', active: route.path.startsWith('/passages/upload') },
    { label: '古籍输入', to: '/passages/manual', active: route.path.startsWith('/passages/manual') },
    { label: '同名人物审核', to: '/review/identity', active: route.path.startsWith('/review/identity') },
    { label: '个人设置', to: '/profile', active: route.path.startsWith('/profile') },
  ]
})
</script>

<template>
  <aside class="sidebar">
    <div class="sidebar__brand">
      <h1>ACGAgent</h1>
      <p>图数据库维护系统</p>
    </div>

    <nav class="sidebar__nav">
      <RouterLink
        v-for="item in navItems"
        :key="item.label"
        :to="item.to"
        class="sidebar__link"
        :class="{ 'sidebar__link--active': item.active }"
      >
        {{ item.label }}
      </RouterLink>
    </nav>
  </aside>
</template>

<style scoped>
.sidebar {
  width: 240px;
  background: #fdfdff;
  border-right: 1px solid #e6eaf2;
  padding: 24px 16px;
  box-sizing: border-box;
}

.sidebar__brand h1 {
  margin: 0;
  font-size: 22px;
  color: #3d4d75;
}

.sidebar__brand p {
  margin: 8px 0 0;
  color: #7f8cac;
  font-size: 14px;
}

.sidebar__nav {
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin-top: 28px;
}

.sidebar__link {
  padding: 12px 14px;
  border-radius: 12px;
  text-decoration: none;
  color: #506287;
  background: transparent;
  transition: background-color 0.2s ease, color 0.2s ease;
}

.sidebar__link:hover {
  background: #eef4ff;
}

.sidebar__link--active {
  background: #e8f1ff;
  color: #2b5ecf;
  font-weight: 600;
}
</style>
