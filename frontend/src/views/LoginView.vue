<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'

import { useAuthStore } from '../stores/auth'

const router = useRouter()
const authStore = useAuthStore()
const errorMessage = ref('')

const formState = reactive({
  email: '',
  password: '',
})

async function handleSubmit() {
  errorMessage.value = ''
  try {
    await authStore.loginAction(formState)
    await router.push('/chat')
  } catch (error) {
    errorMessage.value = '登录失败，请检查邮箱和密码。'
    console.error(error)
  }
}
</script>

<template>
  <div class="auth-page">
    <form class="auth-card" @submit.prevent="handleSubmit">
      <h1>登录系统</h1>
      <p>使用注册邮箱和密码进入图数据库维护平台。</p>

      <label>
        邮箱
        <input v-model="formState.email" type="email" placeholder="请输入邮箱" required />
      </label>

      <label>
        密码
        <input v-model="formState.password" type="password" placeholder="请输入密码" required />
      </label>

      <p v-if="errorMessage" class="auth-card__error">{{ errorMessage }}</p>

      <button type="submit" :disabled="authStore.loading">
        {{ authStore.loading ? '登录中...' : '登录' }}
      </button>

      <RouterLink to="/register" class="auth-card__link">没有账号？去注册</RouterLink>
    </form>
  </div>
</template>

<style scoped>
.auth-page {
  min-height: 100vh;
  display: grid;
  place-items: center;
  background: linear-gradient(180deg, #f7fbff 0%, #eef4ff 100%);
  padding: 24px;
}

.auth-card {
  width: 100%;
  max-width: 420px;
  background: #ffffff;
  border-radius: 20px;
  padding: 32px;
  box-shadow: 0 18px 44px rgba(115, 137, 177, 0.15);
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.auth-card h1 {
  margin: 0;
  color: #31456f;
}

.auth-card p {
  margin: 0;
  color: #7b8ba9;
}

.auth-card label {
  display: flex;
  flex-direction: column;
  gap: 8px;
  color: #4c6087;
  font-weight: 600;
}

.auth-card input {
  border: 1px solid #dce6f5;
  border-radius: 12px;
  padding: 12px 14px;
  font-size: 14px;
}

.auth-card button {
  border: none;
  border-radius: 12px;
  padding: 12px 16px;
  background: #2f6fed;
  color: #fff;
  cursor: pointer;
}

.auth-card__link {
  color: #2f6fed;
  text-align: center;
}

.auth-card__error {
  color: #d64545;
}
</style>
