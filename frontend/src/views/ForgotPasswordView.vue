<script setup lang="ts">
import { onBeforeUnmount, reactive, ref } from 'vue'

import { resetPassword, sendPasswordResetCode } from '../api/auth'
import { getApiErrorMessage } from '../api/client'

type ResetStep = 'request' | 'verify' | 'done'

const step = ref<ResetStep>('request')
const sendingCode = ref(false)
const resettingPassword = ref(false)
const errorMessage = ref('')
const infoMessage = ref('')
const resendRemaining = ref(0)

const formState = reactive({
  email: '',
  code: '',
  newPassword: '',
  confirmPassword: '',
})

let countdownTimer: ReturnType<typeof setInterval> | undefined

function startResendCountdown(seconds: number) {
  resendRemaining.value = Math.max(0, seconds)
  if (countdownTimer) {
    clearInterval(countdownTimer)
  }
  if (resendRemaining.value === 0) {
    countdownTimer = undefined
    return
  }
  countdownTimer = setInterval(() => {
    resendRemaining.value -= 1
    if (resendRemaining.value <= 0 && countdownTimer) {
      clearInterval(countdownTimer)
      countdownTimer = undefined
    }
  }, 1000)
}

async function handleSendCode() {
  const isResend = step.value === 'verify'
  errorMessage.value = ''
  infoMessage.value = ''
  sendingCode.value = true
  try {
    const response = await sendPasswordResetCode(formState.email)
    if (isResend) {
      formState.code = ''
    }
    infoMessage.value = response.message
    step.value = 'verify'
    startResendCountdown(response.cooldown_seconds)
  } catch (error) {
    errorMessage.value = getApiErrorMessage(error, '验证码发送失败，请稍后再试。')
    console.error(error)
  } finally {
    sendingCode.value = false
  }
}

async function handleResetPassword() {
  errorMessage.value = ''
  infoMessage.value = ''
  if (formState.newPassword !== formState.confirmPassword) {
    errorMessage.value = '两次输入的密码不一致。'
    return
  }

  resettingPassword.value = true
  try {
    const response = await resetPassword({
      email: formState.email,
      code: formState.code,
      new_password: formState.newPassword,
    })
    infoMessage.value = response.message
    step.value = 'done'
  } catch (error) {
    errorMessage.value = getApiErrorMessage(error, '密码重置失败，请检查验证码。')
    console.error(error)
  } finally {
    resettingPassword.value = false
  }
}

function changeEmail() {
  step.value = 'request'
  formState.code = ''
  formState.newPassword = ''
  formState.confirmPassword = ''
  errorMessage.value = ''
  infoMessage.value = ''
}

onBeforeUnmount(() => {
  if (countdownTimer) {
    clearInterval(countdownTimer)
  }
})
</script>

<template>
  <div class="auth-page">
    <section class="auth-card">
      <template v-if="step === 'request'">
        <div class="auth-card__heading">
          <span class="auth-card__step">找回密码</span>
          <h1>验证你的邮箱</h1>
          <p>输入注册邮箱，我们会发送一封包含 6 位验证码的邮件。</p>
        </div>

        <form class="auth-card__form" @submit.prevent="handleSendCode">
          <label>
            注册邮箱
            <input
              v-model.trim="formState.email"
              type="email"
              autocomplete="email"
              placeholder="name@example.com"
              required
            />
          </label>

          <p v-if="errorMessage" class="auth-card__message auth-card__message--error" aria-live="polite">
            {{ errorMessage }}
          </p>

          <button type="submit" :disabled="sendingCode">
            {{ sendingCode ? '发送中...' : '发送验证码' }}
          </button>
        </form>

        <RouterLink to="/login" class="auth-card__link">返回登录</RouterLink>
      </template>

      <template v-else-if="step === 'verify'">
        <div class="auth-card__heading">
          <span class="auth-card__step">邮箱验证</span>
          <h1>设置新密码</h1>
          <p>
            验证码已发送至 <strong>{{ formState.email }}</strong>
            <button type="button" class="auth-card__text-button" @click="changeEmail">修改邮箱</button>
          </p>
        </div>

        <form class="auth-card__form" @submit.prevent="handleResetPassword">
          <label>
            邮箱验证码
            <input
              v-model.trim="formState.code"
              type="text"
              inputmode="numeric"
              autocomplete="one-time-code"
              maxlength="6"
              pattern="[0-9]{6}"
              placeholder="请输入 6 位验证码"
              required
            />
          </label>

          <label>
            新密码
            <input
              v-model="formState.newPassword"
              type="password"
              autocomplete="new-password"
              minlength="6"
              maxlength="128"
              placeholder="至少 6 位"
              required
            />
          </label>

          <label>
            确认新密码
            <input
              v-model="formState.confirmPassword"
              type="password"
              autocomplete="new-password"
              minlength="6"
              maxlength="128"
              placeholder="再次输入新密码"
              required
            />
          </label>

          <p v-if="infoMessage" class="auth-card__message auth-card__message--info" aria-live="polite">
            {{ infoMessage }}
          </p>
          <p v-if="errorMessage" class="auth-card__message auth-card__message--error" aria-live="polite">
            {{ errorMessage }}
          </p>

          <button type="submit" :disabled="resettingPassword">
            {{ resettingPassword ? '重置中...' : '重置密码' }}
          </button>
        </form>

        <button
          type="button"
          class="auth-card__resend"
          :disabled="sendingCode || resendRemaining > 0"
          @click="handleSendCode"
        >
          {{
            sendingCode
              ? '发送中...'
              : resendRemaining > 0
                ? `${resendRemaining} 秒后可重新发送`
                : '重新发送验证码'
          }}
        </button>
      </template>

      <template v-else>
        <div class="auth-card__success-icon" aria-hidden="true">✓</div>
        <div class="auth-card__heading auth-card__heading--center">
          <span class="auth-card__step">重置成功</span>
          <h1>密码已更新</h1>
          <p>{{ infoMessage }}</p>
        </div>
        <RouterLink to="/login" class="auth-card__primary-link">返回登录</RouterLink>
      </template>
    </section>
  </div>
</template>

<style scoped>
.auth-page {
  min-height: 100vh;
  display: grid;
  place-items: center;
  background:
    radial-gradient(circle at 15% 15%, rgba(90, 139, 237, 0.12), transparent 32%),
    linear-gradient(180deg, #f7fbff 0%, #eef4ff 100%);
  padding: 24px;
}

.auth-card {
  width: 100%;
  max-width: 440px;
  min-height: 460px;
  background: #ffffff;
  border: 1px solid rgba(220, 230, 245, 0.8);
  border-radius: 20px;
  padding: 34px;
  box-shadow: 0 18px 44px rgba(115, 137, 177, 0.15);
  display: flex;
  flex-direction: column;
  justify-content: center;
  gap: 22px;
}

.auth-card__heading {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.auth-card__heading--center {
  text-align: center;
}

.auth-card__step {
  color: #2f6fed;
  font-size: 13px;
  font-weight: 700;
  letter-spacing: 0.08em;
}

.auth-card h1 {
  margin: 0;
  color: #31456f;
}

.auth-card p {
  margin: 0;
  color: #7b8ba9;
}

.auth-card strong {
  color: #4c6087;
  overflow-wrap: anywhere;
}

.auth-card__form {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.auth-card label {
  display: flex;
  flex-direction: column;
  gap: 8px;
  color: #4c6087;
  font-weight: 600;
}

.auth-card input {
  width: 100%;
  border: 1px solid #dce6f5;
  border-radius: 12px;
  padding: 12px 14px;
  color: #31456f;
  font-size: 14px;
  outline: none;
  transition:
    border-color 0.2s ease,
    box-shadow 0.2s ease;
}

.auth-card input:focus {
  border-color: #77a0f5;
  box-shadow: 0 0 0 3px rgba(47, 111, 237, 0.1);
}

.auth-card button,
.auth-card__primary-link {
  border: none;
  border-radius: 12px;
  padding: 12px 16px;
  background: #2f6fed;
  color: #ffffff;
  cursor: pointer;
  text-align: center;
}

.auth-card button:disabled {
  cursor: not-allowed;
  opacity: 0.58;
}

.auth-card__link {
  color: #2f6fed;
  text-align: center;
}

.auth-card__text-button {
  margin-left: 6px;
  padding: 0;
  background: transparent;
  color: #2f6fed;
  font-size: 13px;
}

.auth-card__resend {
  align-self: center;
  padding: 2px 4px !important;
  background: transparent !important;
  color: #2f6fed !important;
  font-size: 14px;
}

.auth-card__message {
  border-radius: 10px;
  padding: 10px 12px;
  font-size: 14px;
}

.auth-card__message--error {
  background: #fff2f2;
  color: #c83e3e !important;
}

.auth-card__message--info {
  background: #f0f5ff;
  color: #4c6087 !important;
}

.auth-card__success-icon {
  align-self: center;
  width: 58px;
  height: 58px;
  display: grid;
  place-items: center;
  border-radius: 50%;
  background: #eaf7ef;
  color: #2f9d62;
  font-size: 30px;
  font-weight: 700;
}

@media (max-width: 520px) {
  .auth-card {
    min-height: 0;
    padding: 26px 22px;
  }
}
</style>
