<script setup lang="ts">
import { useRouter } from 'vue-router'

import AppSidebar from '../components/AppSidebar.vue'
import { useAuthStore } from '../stores/auth'

withDefaults(defineProps<{
  variant?: 'default' | 'workspace'
}>(), {
  variant: 'default',
})

const router = useRouter()
const authStore = useAuthStore()

async function handleLogout() {
  await authStore.logoutAction()
  await router.push('/login')
}
</script>

<template>
  <div class="layout" :class="{ 'layout--workspace': variant === 'workspace' }">
    <AppSidebar />
    <main class="layout__main" :class="{ 'layout__main--workspace': variant === 'workspace' }">
      <header v-if="variant !== 'workspace'" class="layout__header">
        <div>
          <h2>欢迎使用图数据库维护系统</h2>
          <p>当前用户：{{ authStore.user?.email ?? '未登录' }}</p>
        </div>
        <button class="layout__logout" type="button" @click="handleLogout">退出登录</button>
      </header>
      <section class="layout__content">
        <slot />
      </section>
    </main>
  </div>
</template>

<style scoped>
.layout {
  display: flex;
  min-height: 100vh;
  background: linear-gradient(180deg, #f8fbff 0%, #fefeff 100%);
}

.layout__main {
  flex: 1;
  display: flex;
  flex-direction: column;
  padding: 28px 32px;
  box-sizing: border-box;
}

.layout__main--workspace {
  min-width: 0;
  height: 100vh;
  padding: 0;
  overflow: hidden;
}

.layout__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 24px;
}

.layout__header h2 {
  margin: 0;
  font-size: 24px;
  color: #31456f;
}

.layout__header p {
  margin: 8px 0 0;
  color: #7183a8;
}

.layout__content {
  flex: 1;
}

.layout__main--workspace .layout__content {
  min-width: 0;
  min-height: 0;
}

@media (max-width: 1050px) {
  .layout--workspace {
    min-height: 100vh;
  }

  .layout__main--workspace {
    height: auto;
    min-height: 100vh;
    overflow: visible;
  }
}

.layout__logout {
  border: none;
  border-radius: 10px;
  padding: 10px 16px;
  background: #2f6fed;
  color: #fff;
  cursor: pointer;
}
</style>
