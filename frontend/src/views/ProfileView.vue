<script setup lang="ts">
import { reactive, ref, watch } from 'vue'

import AppLayout from '../layouts/AppLayout.vue'
import { getApiErrorMessage } from '../api/client'
import { updateProfile } from '../api/auth'
import { useAuthStore } from '../stores/auth'

const authStore = useAuthStore()
const errorMessage = ref('')
const successMessage = ref('')
const formState = reactive({
  email: '',
  password: '',
})

watch(
  () => authStore.user,
  (user) => {
    formState.email = user?.email ?? ''
    formState.password = ''
  },
  { immediate: true },
)

async function handleSubmit() {
  errorMessage.value = ''
  successMessage.value = ''
  try {
    const updatedUser = await updateProfile({
      email: formState.email,
      password: formState.password || undefined,
    })
    authStore.user = updatedUser
    formState.password = ''
    successMessage.value = '保存成功。'
  } catch (error) {
    errorMessage.value = getApiErrorMessage(error, '保存失败，请检查输入内容。')
    console.error(error)
  }
}
</script>

<template>
  <AppLayout>
    <div class="profile-card">
      <h3>个人信息</h3>
      <p>当前角色：{{ authStore.user?.role ?? 'unknown' }}</p>

      <form class="profile-form" @submit.prevent="handleSubmit">
        <label>
          邮箱
          <input v-model="formState.email" type="email" required />
        </label>

        <label>
          新密码
          <input v-model="formState.password" type="password" placeholder="留空则不修改密码" />
        </label>

        <button type="submit">保存修改</button>
      </form>

      <p v-if="errorMessage" class="profile-card__error">{{ errorMessage }}</p>
      <p v-if="successMessage" class="profile-card__success">{{ successMessage }}</p>
    </div>
  </AppLayout>
</template>

<style scoped>
.profile-card {
  max-width: 720px;
  background: #fff;
  border-radius: 20px;
  padding: 28px;
  box-shadow: 0 14px 36px rgba(115, 137, 177, 0.12);
}

.profile-card h3 {
  margin-top: 0;
  color: #31456f;
}

.profile-card p {
  color: #7183a8;
}

.profile-form {
  display: flex;
  flex-direction: column;
  gap: 16px;
  margin-top: 24px;
}

.profile-form label {
  display: flex;
  flex-direction: column;
  gap: 8px;
  color: #4c6087;
  font-weight: 600;
}

.profile-form input {
  border: 1px solid #dce6f5;
  border-radius: 12px;
  padding: 12px 14px;
}

.profile-form button {
  width: fit-content;
  border: none;
  border-radius: 12px;
  padding: 12px 18px;
  background: #2f6fed;
  color: #fff;
  cursor: pointer;
}

.profile-card__error {
  margin-top: 16px;
  color: #d64545;
}

.profile-card__success {
  margin-top: 16px;
  color: #17803d;
}
</style>
